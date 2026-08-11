"""Training, evaluation, and versioned artifacts for the DL-D0 challenger."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from torch import nn

from ...utils import write_text_atomic
from .dataset import CLASS_ORDER, DatasetSplit, PreparedDeepDataset, prepare_tabular_dataset
from .model import DualHeadTabularNet, combined_objective


TRAINING_PROTOCOL = "dl-d0-tabular-dual-head-v1"


@dataclass(frozen=True)
class DeepTrainingConfig:
    seed: int = 20260729
    epochs: int = 20
    patience: int = 4
    batch_size: int = 4096
    hidden_dim: int = 128
    bottleneck_dim: int = 64
    dropout: float = 0.15
    learning_rate: float = 0.0007
    weight_decay: float = 0.0001
    return_scale: float = 100.0
    classification_weight: float = 0.45
    regression_weight: float = 0.35
    ranking_weight: float = 0.20
    max_training_rows: int | None = 180000
    device: str = "auto"


@dataclass
class DeepTrainingResult:
    model: DualHeadTabularNet
    config: DeepTrainingConfig
    metrics: dict[str, float | None]
    metric_notes: dict[str, str]
    history: list[dict[str, float]]
    validation_predictions: pd.DataFrame
    temperature: float
    architecture: dict[str, Any]
    model_version: str
    training_rows: int
    device: str
    training_seconds: float


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def _resolve_device(requested: str) -> torch.device:
    if requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _bounded_training_indices(split: DatasetSplit, limit: int | None, seed: int) -> np.ndarray:
    if limit is None or len(split.x) <= int(limit):
        return np.arange(len(split.x), dtype=np.int64)
    rng = np.random.default_rng(seed)
    date_values = split.metadata["trade_date"].astype(str).to_numpy()
    unique_dates = np.asarray(sorted(np.unique(date_values)))
    if int(limit) < len(unique_dates):
        raise ValueError("deep_training_budget_below_date_count")
    quota = max(1, int(limit) // len(unique_dates))
    selected: list[int] = []
    leftovers: list[int] = []
    for date in unique_dates:
        indices = np.flatnonzero(date_values == date)
        shuffled = rng.permutation(indices)
        selected.extend(shuffled[:quota].tolist())
        leftovers.extend(shuffled[quota:].tolist())
    remaining = int(limit) - len(selected)
    if remaining > 0 and leftovers:
        selected.extend(rng.permutation(np.asarray(leftovers))[:remaining].tolist())
    return np.asarray(sorted(selected[: int(limit)]), dtype=np.int64)


def _date_block_batches(
    metadata: pd.DataFrame,
    *,
    batch_size: int,
    seed: int,
    epoch: int,
) -> Iterator[np.ndarray]:
    date_values = metadata["trade_date"].astype(str).to_numpy()
    dates = np.asarray(sorted(np.unique(date_values)))
    rng = np.random.default_rng(seed + epoch)
    dates = rng.permutation(dates)
    current: list[np.ndarray] = []
    current_size = 0
    for date in dates:
        indices = np.flatnonzero(date_values == date)
        if current and current_size + len(indices) > batch_size:
            yield np.concatenate(current)
            current = []
            current_size = 0
        current.append(indices)
        current_size += len(indices)
    if current:
        yield np.concatenate(current)


def _tensor_batch(
    split: DatasetSplit,
    indices: np.ndarray,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    dates = pd.factorize(split.metadata.iloc[indices]["trade_date"], sort=True)[0]
    return (
        torch.from_numpy(split.x[indices]).to(device),
        torch.from_numpy(split.y_class[indices]).to(device),
        torch.from_numpy(split.y_return[indices]).to(device),
        torch.from_numpy(dates.astype(np.int64)).to(device),
    )


def _class_weights(labels: np.ndarray, device: torch.device) -> torch.Tensor:
    counts = np.bincount(labels, minlength=len(CLASS_ORDER)).astype(float)
    weights = 1.0 / np.sqrt(np.maximum(counts, 1.0))
    weights /= weights.mean()
    return torch.tensor(weights, dtype=torch.float32, device=device)


@torch.no_grad()
def _predict_raw(
    model: nn.Module,
    split: DatasetSplit,
    *,
    device: torch.device,
    return_scale: float,
    batch_size: int = 16384,
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()
    logits: list[np.ndarray] = []
    predicted_returns: list[np.ndarray] = []
    for start in range(0, len(split.x), batch_size):
        features = torch.from_numpy(split.x[start : start + batch_size]).to(device)
        batch_logits, batch_returns = model(features)
        logits.append(batch_logits.detach().cpu().numpy())
        predicted_returns.append(batch_returns.detach().cpu().numpy() / float(return_scale))
    return np.concatenate(logits), np.concatenate(predicted_returns)


def _softmax(logits: np.ndarray, temperature: float) -> np.ndarray:
    scaled = logits / max(float(temperature), 1e-6)
    scaled -= scaled.max(axis=1, keepdims=True)
    values = np.exp(scaled)
    return values / values.sum(axis=1, keepdims=True)


def _fit_temperature(logits: np.ndarray, labels: np.ndarray) -> float:
    candidates = np.geomspace(0.35, 4.0, 121)
    scored = [
        (log_loss(labels, _softmax(logits, float(value)), labels=[0, 1, 2]), abs(value - 1.0), value)
        for value in candidates
    ]
    return float(min(scored)[2])


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    if left.nunique() < 2 or right.nunique() < 2:
        return None
    value = left.rank(method="average").corr(right.rank(method="average"))
    return None if pd.isna(value) else float(value)


def _validation_metrics(
    split: DatasetSplit,
    logits: np.ndarray,
    predicted_returns: np.ndarray,
    *,
    temperature: float,
    class_prior: np.ndarray,
    horizon: int,
) -> tuple[dict[str, float | None], pd.DataFrame, dict[str, str]]:
    probabilities = _softmax(logits, temperature)
    predictions = split.metadata.copy()
    for index, class_name in enumerate(CLASS_ORDER):
        predictions[f"prob_{class_name}"] = probabilities[:, index]
    predictions["predicted_label"] = np.asarray(CLASS_ORDER)[np.argmax(probabilities, axis=1)]
    predictions["predicted_excess_return"] = predicted_returns

    rank_ics: list[float] = []
    spreads: list[float] = []
    for _, group in predictions.groupby("trade_date", sort=True):
        rank_ic = _spearman(group["predicted_excess_return"], group["excess_return"])
        if rank_ic is not None:
            rank_ics.append(rank_ic)
        tail = max(1, int(math.ceil(len(group) * 0.10)))
        ordered = group.sort_values("predicted_excess_return")
        spreads.append(float(ordered.tail(tail)["excess_return"].mean() - ordered.head(tail)["excess_return"].mean()))
    notes: dict[str, str] = {}
    rank_ic = float(np.mean(rank_ics)) if rank_ics else None
    rank_ic_std = float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else None
    prior = np.asarray(class_prior, dtype=float)
    prior = np.clip(prior, 1e-12, None)
    prior /= prior.sum()
    prior_probabilities = np.tile(prior, (len(split.y_class), 1))
    classification_log_loss = float(log_loss(split.y_class, probabilities, labels=[0, 1, 2]))
    auc: float | None = None
    try:
        if len(np.unique(split.y_class)) < len(CLASS_ORDER):
            raise ValueError("not_all_classes_present")
        auc = float(
            roc_auc_score(
                split.y_class,
                probabilities,
                labels=[0, 1, 2],
                multi_class="ovr",
                average="macro",
            )
        )
        if not np.isfinite(auc):
            raise ValueError("non_finite_auc")
    except ValueError as exc:
        notes["macro_auc_ovr"] = f"unavailable:{exc}"
    one_hot = np.eye(3, dtype=float)[split.y_class]
    return_mae = float(np.mean(np.abs(predicted_returns - split.y_return)))
    def block_interval(values: list[float]) -> tuple[float | None, float | None]:
        if len(values) < 2:
            return None, None
        array = np.asarray(values, dtype=float)
        block_size = max(1, min(int(horizon), len(array)))
        rng = np.random.default_rng(20260729)
        means = []
        for _ in range(500):
            sampled: list[float] = []
            while len(sampled) < len(array):
                start = int(rng.integers(0, len(array)))
                sampled.extend(array.take((np.arange(block_size) + start) % len(array)).tolist())
            means.append(float(np.mean(sampled[: len(array)])))
        return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))

    rank_low, rank_high = block_interval(rank_ics)
    spread_low, spread_high = block_interval(spreads)
    if rank_ic is None:
        notes["rank_ic"] = "unavailable:no_valid_cross_section"
    rank_icir = None
    if rank_ic is not None and rank_ic_std is not None and rank_ic_std > 1e-12:
        rank_icir = float(rank_ic / rank_ic_std)
    else:
        notes["rank_icir"] = "unavailable:insufficient_nonconstant_daily_ic"
    top_bottom_spread = float(np.mean(spreads)) if spreads else None
    if top_bottom_spread is None:
        notes["top_bottom_decile_spread"] = "unavailable:no_valid_cross_section"
    metrics: dict[str, float | None] = {
        "accuracy": float(accuracy_score(split.y_class, np.argmax(probabilities, axis=1))),
        "macro_auc_ovr": auc,
        "log_loss": classification_log_loss,
        "log_loss_skill_vs_prior": float(
            log_loss(split.y_class, prior_probabilities, labels=[0, 1, 2]) - classification_log_loss
        ),
        "multiclass_brier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "return_mae": return_mae,
        "return_mae_skill_vs_zero": float(np.mean(np.abs(split.y_return)) - return_mae),
        "rank_ic": rank_ic,
        "rank_ic_std": rank_ic_std,
        "rank_icir": rank_icir,
        "rank_ic_block_ci_low": rank_low,
        "rank_ic_block_ci_high": rank_high,
        "daily_ic_count": float(len(rank_ics)),
        "top_bottom_decile_spread": top_bottom_spread,
        "top_bottom_spread_block_ci_low": spread_low,
        "top_bottom_spread_block_ci_high": spread_high,
        "validation_down_support": float(np.sum(split.y_class == 0)),
        "validation_flat_support": float(np.sum(split.y_class == 1)),
        "validation_up_support": float(np.sum(split.y_class == 2)),
        "training_prior_down": float(prior[0]),
        "training_prior_flat": float(prior[1]),
        "training_prior_up": float(prior[2]),
    }
    return metrics, predictions, notes


def _calibration_score(
    model: nn.Module,
    split: DatasetSplit,
    *,
    device: torch.device,
    return_scale: float,
) -> float:
    logits, predicted_returns = _predict_raw(
        model,
        split,
        device=device,
        return_scale=return_scale,
    )
    classification = log_loss(split.y_class, _softmax(logits, 1.0), labels=[0, 1, 2])
    regression = np.mean(np.abs(predicted_returns - split.y_return)) * return_scale
    return float(classification + 0.25 * regression)


def _implementation_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _state_dict_hash(model: nn.Module) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("utf-8"))
        digest.update(str(tuple(tensor.shape)).encode("utf-8"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _model_version(
    dataset_hash: str,
    config: DeepTrainingConfig,
    model: nn.Module,
    device: torch.device,
) -> str:
    runtime_payload = {
        "config": asdict(config),
        "device": str(device),
        "torch": torch.__version__,
        "protocol": TRAINING_PROTOCOL,
        "implementation_hash": _implementation_hash(),
    }
    runtime_hash = hashlib.sha256(
        json.dumps(runtime_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:8]
    return (
        f"DL-D0-V001-{dataset_hash[:8]}-{runtime_hash}-"
        f"{_state_dict_hash(model)[:8]}"
    )


def train_deep_model(
    prepared: PreparedDeepDataset,
    config: DeepTrainingConfig | None = None,
    *,
    verbose: bool = False,
) -> DeepTrainingResult:
    """Train DL-D0 with date-block batches and untouched final validation."""

    resolved = config or DeepTrainingConfig()
    _seed_everything(resolved.seed)
    device = _resolve_device(resolved.device)
    model = DualHeadTabularNet(
        input_dim=len(prepared.feature_columns),
        hidden_dim=resolved.hidden_dim,
        bottleneck_dim=resolved.bottleneck_dim,
        dropout=resolved.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=resolved.learning_rate,
        weight_decay=resolved.weight_decay,
    )
    selected = _bounded_training_indices(
        prepared.train,
        resolved.max_training_rows,
        resolved.seed,
    )
    training = DatasetSplit(
        x=prepared.train.x[selected],
        y_class=prepared.train.y_class[selected],
        y_return=prepared.train.y_return[selected],
        metadata=prepared.train.metadata.iloc[selected].reset_index(drop=True),
    )
    class_weights = _class_weights(training.y_class, device)
    best_score = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    stale_epochs = 0
    history: list[dict[str, float]] = []
    started = time.monotonic()
    for epoch in range(resolved.epochs):
        model.train()
        totals: list[float] = []
        for indices in _date_block_batches(
            training.metadata,
            batch_size=resolved.batch_size,
            seed=resolved.seed,
            epoch=epoch,
        ):
            features, labels, returns, date_groups = _tensor_batch(training, indices, device)
            optimizer.zero_grad(set_to_none=True)
            logits, predicted_returns = model(features)
            loss, _ = combined_objective(
                logits,
                predicted_returns,
                labels,
                returns,
                date_groups,
                class_weights=class_weights,
                return_scale=resolved.return_scale,
                classification_weight=resolved.classification_weight,
                regression_weight=resolved.regression_weight,
                ranking_weight=resolved.ranking_weight,
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            totals.append(float(loss.detach().cpu()))
        score = _calibration_score(
            model,
            prepared.calibration,
            device=device,
            return_scale=resolved.return_scale,
        )
        history.append(
            {
                "epoch": float(epoch + 1),
                "train_loss": float(np.mean(totals)),
                "calibration_score": score,
            }
        )
        if verbose:
            print(
                f"epoch={epoch + 1} train_loss={history[-1]['train_loss']:.6f} "
                f"calibration_score={score:.6f}",
                flush=True,
            )
        if score < best_score - 1e-5:
            best_score = score
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= resolved.patience:
                break
    if best_state is None:
        raise RuntimeError("deep_training_no_checkpoint")
    model.load_state_dict(best_state)
    model.to(device)
    calibration_logits, _ = _predict_raw(
        model,
        prepared.calibration,
        device=device,
        return_scale=resolved.return_scale,
    )
    temperature = _fit_temperature(calibration_logits, prepared.calibration.y_class)
    validation_logits, validation_returns = _predict_raw(
        model,
        prepared.validation,
        device=device,
        return_scale=resolved.return_scale,
    )
    metrics, validation_predictions, metric_notes = _validation_metrics(
        prepared.validation,
        validation_logits,
        validation_returns,
        temperature=temperature,
        class_prior=np.bincount(training.y_class, minlength=3).astype(float),
        horizon=prepared.horizon,
    )
    model.to("cpu")
    return DeepTrainingResult(
        model=model,
        config=resolved,
        metrics=metrics,
        metric_notes=metric_notes,
        history=history,
        validation_predictions=validation_predictions,
        temperature=temperature,
        architecture=model.architecture(),
        model_version=_model_version(prepared.dataset_hash, resolved, model, device),
        training_rows=len(training.x),
        device=str(device),
        training_seconds=float(time.monotonic() - started),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_training_artifact(
    result: DeepTrainingResult,
    prepared: PreparedDeepDataset,
    output_root: Path,
    *,
    market: str,
    source_snapshot: str,
) -> Path:
    """Persist a reproducible research artifact without registering it for trading."""

    parent = output_root / market / str(prepared.horizon)
    parent.mkdir(parents=True, exist_ok=True)
    artifact = parent / result.model_version
    temporary: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{result.model_version}-", dir=parent)
    )
    state_hash = _state_dict_hash(result.model)
    implementation_hash = _implementation_hash()
    try:
        model_payload = {
            "artifact_schema_version": 2,
            "state_dict": result.model.state_dict(),
            "state_dict_hash": state_hash,
            "architecture": result.architecture,
            "feature_columns": prepared.feature_columns,
            "transform": asdict(prepared.transform),
            "class_order": CLASS_ORDER,
            "temperature": result.temperature,
            "return_scale": result.config.return_scale,
            "training_protocol": TRAINING_PROTOCOL,
        }
        torch.save(model_payload, temporary / "model.pt")
        metadata = {
            "schema_version": 2,
            "research_only": True,
            "formal_strategy_eligible": False,
            "training_protocol": TRAINING_PROTOCOL,
            "model_version": result.model_version,
            "market": market,
            "horizon": prepared.horizon,
            "source_snapshot": str(source_snapshot),
            "dataset_hash": prepared.dataset_hash,
            "feature_columns": list(prepared.feature_columns),
            "architecture": result.architecture,
            "training_config": asdict(result.config),
            "training_rows": result.training_rows,
            "device": result.device,
            "torch_version": torch.__version__,
            "determinism_policy": "seeded_warn_only_content_addressed_weights",
            "implementation_hash": implementation_hash,
            "model_state_hash": state_hash,
            "training_seconds": result.training_seconds,
            "temperature": result.temperature,
            "metrics": result.metrics,
            "metric_notes": result.metric_notes,
            "history": result.history,
        }
        write_text_atomic(
            temporary / "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2, default=_json_default),
        )
        write_text_atomic(
            temporary / "cleaning_audit.json",
            json.dumps(prepared.audit, ensure_ascii=False, indent=2, default=_json_default),
        )
        result.validation_predictions.to_parquet(
            temporary / "validation_predictions.parquet",
            index=False,
        )
        metrics_lines = "\n".join(
            f"- `{name}`: {'不可计算' if value is None else f'{value:.6f}'}"
            for name, value in sorted(result.metrics.items())
        )
        write_text_atomic(
            temporary / "report.md",
            "\n".join(
                (
                    f"# 深度研究模型 {result.model_version}",
                    "",
                    "> 研究用途，不参与正式策略选股或模拟下单。",
                    "",
                    "## 数据",
                    "",
                    f"- 市场：`{market}`",
                    f"- 预测周期：`{prepared.horizon}` 个交易日",
                    f"- 数据快照：`{source_snapshot}`",
                    f"- 训练样本：`{result.training_rows}`",
                    f"- 特征数：`{len(prepared.feature_columns)}`",
                    f"- 数据集哈希：`{prepared.dataset_hash}`",
                    "",
                    "## 模型",
                    "",
                    f"- 协议：`{TRAINING_PROTOCOL}`",
                    "- 架构：共享残差 MLP + 涨跌分类头 + 超额收益回归头",
                    f"- 参数量：`{result.architecture['parameters']}`",
                    f"- 权重哈希：`{state_hash}`",
                    f"- 温度校准：`{result.temperature:.4f}`",
                    "",
                    "## 独立验证",
                    "",
                    metrics_lines,
                    "",
                    "## 边界",
                    "",
                    "- 该版本是 DL-D0 基线，只消费现有点时结构化特征。",
                    "- 事件语义特征必须通过带证据哈希的 lifecycle 门禁。",
                    "- 未写入正式模型注册表，也不会影响两套竞赛策略。",
                    "",
                )
            ),
        )
        artifact_files = (
            "model.pt",
            "metadata.json",
            "cleaning_audit.json",
            "validation_predictions.parquet",
            "report.md",
        )
        manifest = {
            "schema_version": 1,
            "complete": True,
            "model_version": result.model_version,
            "training_protocol": TRAINING_PROTOCOL,
            "dataset_hash": prepared.dataset_hash,
            "implementation_hash": implementation_hash,
            "model_state_hash": state_hash,
            "files": {
                name: {"sha256": _file_hash(temporary / name)}
                for name in artifact_files
            },
        }
        write_text_atomic(
            temporary / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        if artifact.exists():
            existing_manifest_path = artifact / "manifest.json"
            if not existing_manifest_path.is_file():
                raise ValueError(f"deep_artifact_version_conflict:{artifact}")
            existing = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            same_content = (
                existing.get("complete") is True
                and existing.get("dataset_hash") == prepared.dataset_hash
                and existing.get("implementation_hash") == implementation_hash
                and existing.get("model_state_hash") == state_hash
            )
            if not same_content:
                raise ValueError(f"deep_artifact_version_conflict:{artifact}")
            return artifact
        os.replace(temporary, artifact)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    return artifact


def _load_config(path: Path) -> tuple[dict[str, Any], DeepTrainingConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("research_only") is not True:
        raise ValueError("deep_config_research_only")
    return payload, DeepTrainingConfig(**payload.get("training", {}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the research-only DL-D0 challenger")
    parser.add_argument("--features", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--config", default="configs/research/deep_d0.json")
    parser.add_argument("--output-root", default="data/research/deep/models")
    args = parser.parse_args(argv)
    payload, training_config = _load_config(Path(args.config))
    data_config = payload.get("data", {})
    features = pd.read_parquet(args.features)
    labels = pd.read_parquet(args.labels)
    prepared = prepare_tabular_dataset(
        features,
        labels,
        horizon=int(payload["horizon"]),
        intelligence_lifecycle_path=payload["intelligence_lifecycle_path"],
        **data_config,
    )
    result = train_deep_model(prepared, training_config, verbose=True)
    source_snapshot = Path(args.features).stem
    artifact = save_training_artifact(
        result,
        prepared,
        Path(args.output_root),
        market=str(payload["market"]),
        source_snapshot=source_snapshot,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "artifact": str(artifact),
                "model_version": result.model_version,
                "metrics": result.metrics,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
