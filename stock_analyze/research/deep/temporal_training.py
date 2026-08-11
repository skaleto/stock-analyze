"""Multi-horizon training and artifacts for the DL-D1 temporal challenger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import log_loss

from ...utils import write_text_atomic
from .dataset import CLASS_ORDER, DatasetSplit
from .model import combined_objective
from .temporal_dataset import (
    PreparedTemporalDataset,
    TemporalSplit,
    prepare_temporal_dataset,
)
from .temporal_model import TemporalContextNet
from .training import (
    _class_weights,
    _date_block_batches,
    _file_hash,
    _fit_temperature,
    _json_default,
    _resolve_device,
    _seed_everything,
    _state_dict_hash,
    _validation_metrics,
)


TRAINING_PROTOCOL = "dl-d1-temporal-context-v1"


@dataclass(frozen=True)
class TemporalTrainingConfig:
    seed: int = 20260729
    epochs: int = 16
    patience: int = 4
    batch_size: int = 2048
    hidden_dim: int = 64
    context_dim: int = 32
    gru_layers: int = 2
    dropout: float = 0.15
    learning_rate: float = 0.0007
    weight_decay: float = 0.0001
    return_scale: float = 100.0
    classification_weight: float = 0.45
    regression_weight: float = 0.35
    ranking_weight: float = 0.20
    max_training_rows: int | None = 120000
    device: str = "auto"


@dataclass
class TemporalTrainingResult:
    model: TemporalContextNet
    config: TemporalTrainingConfig
    metrics: dict[int, dict[str, float | None]]
    metric_notes: dict[int, dict[str, str]]
    history: list[dict[str, float]]
    validation_predictions: pd.DataFrame
    temperatures: dict[int, float]
    model_version: str
    architecture: dict[str, Any]
    training_rows: int
    device: str
    training_seconds: float


def _bounded_indices(split: TemporalSplit, limit: int | None, seed: int) -> np.ndarray:
    if limit is None or len(split.metadata) <= int(limit):
        return np.arange(len(split.metadata), dtype=np.int64)
    dates = split.metadata["trade_date"].astype(str).to_numpy()
    unique_dates = np.asarray(sorted(np.unique(dates)))
    if int(limit) < len(unique_dates):
        raise ValueError("temporal_training_budget_below_date_count")
    rng = np.random.default_rng(seed)
    quota, remainder = divmod(int(limit), len(unique_dates))
    selected: list[int] = []
    for date_index, date in enumerate(unique_dates):
        indices = np.flatnonzero(dates == date)
        count = quota + (1 if date_index < remainder else 0)
        selected.extend(rng.permutation(indices)[:count].tolist())
    return np.asarray(sorted(selected), dtype=np.int64)


def _slice_split(split: TemporalSplit, indices: np.ndarray) -> TemporalSplit:
    return TemporalSplit(
        sequence_indices=split.sequence_indices[indices],
        sequence_lengths=split.sequence_lengths[indices],
        static_values=split.static_values[indices],
        industry_context=split.industry_context[indices],
        market_context=split.market_context[indices],
        y_class=split.y_class[indices],
        y_return=split.y_return[indices],
        metadata=split.metadata.iloc[indices].reset_index(drop=True),
    )


def _batch(
    prepared: PreparedTemporalDataset,
    split: TemporalSplit,
    indices: np.ndarray,
    device: torch.device,
) -> tuple[torch.Tensor, ...]:
    sequence_indices = split.sequence_indices[indices]
    padded = sequence_indices < 0
    safe_indices = np.where(padded, 0, sequence_indices)
    values = prepared.history_values[safe_indices].copy()
    validity = prepared.history_validity[safe_indices].astype(np.float32, copy=True)
    values[padded] = 0.0
    validity[padded] = 0.0
    date_groups = pd.factorize(
        split.metadata.iloc[indices]["trade_date"],
        sort=True,
    )[0].astype(np.int64)
    return (
        torch.from_numpy(values).to(device),
        torch.from_numpy(validity).to(device),
        torch.from_numpy(split.sequence_lengths[indices]).to(device),
        torch.from_numpy(split.static_values[indices]).to(device),
        torch.from_numpy(split.industry_context[indices]).to(device),
        torch.from_numpy(split.market_context[indices]).to(device),
        torch.from_numpy(split.y_class[indices]).to(device),
        torch.from_numpy(split.y_return[indices]).to(device),
        torch.from_numpy(date_groups).to(device),
    )


@torch.no_grad()
def _predict_raw(
    model: TemporalContextNet,
    prepared: PreparedTemporalDataset,
    split: TemporalSplit,
    *,
    device: torch.device,
    return_scale: float,
    batch_size: int = 4096,
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    model.eval()
    logits = {horizon: [] for horizon in prepared.horizons}
    returns = {horizon: [] for horizon in prepared.horizons}
    for start in range(0, len(split.metadata), batch_size):
        indices = np.arange(start, min(start + batch_size, len(split.metadata)))
        tensors = _batch(prepared, split, indices, device)
        outputs = model(*tensors[:6])
        for horizon, (batch_logits, batch_returns) in outputs.items():
            logits[horizon].append(batch_logits.detach().cpu().numpy())
            returns[horizon].append(
                batch_returns.detach().cpu().numpy() / float(return_scale)
            )
    return (
        {horizon: np.concatenate(parts) for horizon, parts in logits.items()},
        {horizon: np.concatenate(parts) for horizon, parts in returns.items()},
    )


def _calibration_score(
    model: TemporalContextNet,
    prepared: PreparedTemporalDataset,
    split: TemporalSplit,
    *,
    device: torch.device,
    return_scale: float,
) -> float:
    logits, returns = _predict_raw(
        model,
        prepared,
        split,
        device=device,
        return_scale=return_scale,
    )
    scores = []
    for horizon_index, horizon in enumerate(prepared.horizons):
        values = logits[horizon]
        values -= values.max(axis=1, keepdims=True)
        probabilities = np.exp(values)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        classification = log_loss(
            split.y_class[:, horizon_index],
            probabilities,
            labels=[0, 1, 2],
        )
        regression = (
            np.mean(np.abs(returns[horizon] - split.y_return[:, horizon_index]))
            * return_scale
        )
        scores.append(float(classification + 0.25 * regression))
    return float(np.mean(scores))


def _implementation_hash() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for name in (
        "dataset.py",
        "model.py",
        "training.py",
        "temporal_dataset.py",
        "temporal_inference.py",
        "temporal_model.py",
        "temporal_training.py",
    ):
        digest.update(name.encode("utf-8"))
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _model_version(
    dataset_hash: str,
    config: TemporalTrainingConfig,
    model: TemporalContextNet,
    device: torch.device,
) -> str:
    payload = {
        "config": asdict(config),
        "device": str(device),
        "torch": torch.__version__,
        "protocol": TRAINING_PROTOCOL,
        "implementation": _implementation_hash(),
    }
    runtime_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:8]
    return (
        f"DL-D1-V001-{dataset_hash[:8]}-{runtime_hash}-"
        f"{_state_dict_hash(model)[:8]}"
    )


def train_temporal_model(
    prepared: PreparedTemporalDataset,
    config: TemporalTrainingConfig | None = None,
    *,
    verbose: bool = False,
) -> TemporalTrainingResult:
    resolved = config or TemporalTrainingConfig()
    _seed_everything(resolved.seed)
    device = _resolve_device(resolved.device)
    model = TemporalContextNet(
        sequence_feature_dim=len(prepared.sequence_columns),
        static_dim=len(prepared.static_columns),
        horizons=prepared.horizons,
        hidden_dim=resolved.hidden_dim,
        context_dim=resolved.context_dim,
        gru_layers=resolved.gru_layers,
        dropout=resolved.dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=resolved.learning_rate,
        weight_decay=resolved.weight_decay,
    )
    selected = _bounded_indices(
        prepared.train,
        resolved.max_training_rows,
        resolved.seed,
    )
    training = _slice_split(prepared.train, selected)
    class_weights = {
        horizon: _class_weights(training.y_class[:, index], device)
        for index, horizon in enumerate(prepared.horizons)
    }
    best_score = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    stale_epochs = 0
    history: list[dict[str, float]] = []
    started = time.monotonic()
    for epoch in range(resolved.epochs):
        model.train()
        losses = []
        for indices in _date_block_batches(
            training.metadata,
            batch_size=resolved.batch_size,
            seed=resolved.seed,
            epoch=epoch,
        ):
            tensors = _batch(prepared, training, indices, device)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(*tensors[:6])
            horizon_losses = []
            for horizon_index, horizon in enumerate(prepared.horizons):
                logits, predicted_returns = outputs[horizon]
                loss, _ = combined_objective(
                    logits,
                    predicted_returns,
                    tensors[6][:, horizon_index],
                    tensors[7][:, horizon_index],
                    tensors[8],
                    class_weights=class_weights[horizon],
                    return_scale=resolved.return_scale,
                    classification_weight=resolved.classification_weight,
                    regression_weight=resolved.regression_weight,
                    ranking_weight=resolved.ranking_weight,
                )
                horizon_losses.append(loss)
            total = torch.stack(horizon_losses).mean()
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            losses.append(float(total.detach().cpu()))
        score = _calibration_score(
            model,
            prepared,
            prepared.calibration,
            device=device,
            return_scale=resolved.return_scale,
        )
        history.append(
            {
                "epoch": float(epoch + 1),
                "train_loss": float(np.mean(losses)),
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
                name: value.detach().cpu().clone()
                for name, value in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= resolved.patience:
                break
    if best_state is None:
        raise RuntimeError("temporal_training_no_checkpoint")
    model.load_state_dict(best_state)
    model.to(device)
    calibration_logits, _ = _predict_raw(
        model,
        prepared,
        prepared.calibration,
        device=device,
        return_scale=resolved.return_scale,
    )
    temperatures = {
        horizon: _fit_temperature(
            calibration_logits[horizon],
            prepared.calibration.y_class[:, horizon_index],
        )
        for horizon_index, horizon in enumerate(prepared.horizons)
    }
    validation_logits, validation_returns = _predict_raw(
        model,
        prepared,
        prepared.validation,
        device=device,
        return_scale=resolved.return_scale,
    )
    predictions = prepared.validation.metadata[["code", "trade_date"]].copy()
    metrics: dict[int, dict[str, float | None]] = {}
    metric_notes: dict[int, dict[str, str]] = {}
    for horizon_index, horizon in enumerate(prepared.horizons):
        metadata = prepared.validation.metadata[
            [
                "code",
                "trade_date",
                f"label_end_date_{horizon}",
                f"label_{horizon}",
                f"excess_return_{horizon}",
            ]
        ].rename(
            columns={
                f"label_end_date_{horizon}": "label_end_date",
                f"label_{horizon}": "label",
                f"excess_return_{horizon}": "excess_return",
            }
        )
        metric_split = DatasetSplit(
            x=np.zeros((len(metadata), 0), dtype=np.float32),
            y_class=prepared.validation.y_class[:, horizon_index],
            y_return=prepared.validation.y_return[:, horizon_index],
            metadata=metadata,
        )
        horizon_metrics, horizon_predictions, notes = _validation_metrics(
            metric_split,
            validation_logits[horizon],
            validation_returns[horizon],
            temperature=temperatures[horizon],
            class_prior=np.bincount(
                training.y_class[:, horizon_index],
                minlength=3,
            ).astype(float),
            horizon=horizon,
        )
        metrics[horizon] = horizon_metrics
        metric_notes[horizon] = notes
        for column in ("prob_down", "prob_flat", "prob_up", "predicted_excess_return"):
            predictions[f"{column}_{horizon}"] = horizon_predictions[column].to_numpy()
        predictions[f"predicted_label_{horizon}"] = horizon_predictions[
            "predicted_label"
        ].to_numpy()
    model.to("cpu")
    return TemporalTrainingResult(
        model=model,
        config=resolved,
        metrics=metrics,
        metric_notes=metric_notes,
        history=history,
        validation_predictions=predictions,
        temperatures=temperatures,
        model_version=_model_version(prepared.dataset_hash, resolved, model, device),
        architecture=model.architecture(),
        training_rows=len(training.metadata),
        device=str(device),
        training_seconds=float(time.monotonic() - started),
    )


def save_temporal_artifact(
    result: TemporalTrainingResult,
    prepared: PreparedTemporalDataset,
    output_root: Path,
    *,
    market: str,
    source_snapshot: str,
) -> Path:
    parent = output_root / market / "multi_horizon"
    parent.mkdir(parents=True, exist_ok=True)
    artifact = parent / result.model_version
    temporary: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{result.model_version}-", dir=parent)
    )
    state_hash = _state_dict_hash(result.model)
    implementation_hash = _implementation_hash()
    try:
        torch.save(
            {
                "artifact_schema_version": 1,
                "training_protocol": TRAINING_PROTOCOL,
                "state_dict": result.model.state_dict(),
                "state_dict_hash": state_hash,
                "architecture": result.architecture,
                "sequence_columns": prepared.sequence_columns,
                "static_columns": prepared.static_columns,
                "sequence_transform": asdict(prepared.sequence_transform),
                "static_transform": asdict(prepared.static_transform),
                "temperatures": result.temperatures,
                "return_scale": result.config.return_scale,
            },
            temporary / "model.pt",
        )
        metadata = {
            "schema_version": 1,
            "research_only": True,
            "formal_strategy_eligible": False,
            "training_protocol": TRAINING_PROTOCOL,
            "model_version": result.model_version,
            "market": market,
            "horizons": list(prepared.horizons),
            "source_snapshot": source_snapshot,
            "dataset_hash": prepared.dataset_hash,
            "architecture": result.architecture,
            "training_config": asdict(result.config),
            "training_rows": result.training_rows,
            "device": result.device,
            "torch_version": torch.__version__,
            "implementation_hash": implementation_hash,
            "model_state_hash": state_hash,
            "training_seconds": result.training_seconds,
            "temperatures": result.temperatures,
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
        report_lines = [
            f"# 深度时序研究模型 {result.model_version}",
            "",
            "> 研究用途，不参与正式策略选股或模拟下单。",
            "",
            f"- 数据集：`{prepared.dataset_hash}`",
            f"- 序列：`{prepared.sequence_length}` 日",
            f"- 序列特征：`{len(prepared.sequence_columns)}`",
            f"- 静态特征：`{len(prepared.static_columns)}`",
            f"- 训练样本：`{result.training_rows}`",
            f"- 参数量：`{result.architecture['parameters']}`",
            "",
        ]
        for horizon in prepared.horizons:
            report_lines.extend(
                [
                    f"## {horizon} 日",
                    "",
                    *[
                        f"- `{name}`: {'不可计算' if value is None else f'{value:.6f}'}"
                        for name, value in sorted(result.metrics[horizon].items())
                    ],
                    "",
                ]
            )
        write_text_atomic(temporary / "report.md", "\n".join(report_lines))
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
            existing_path = artifact / "manifest.json"
            if not existing_path.is_file():
                raise ValueError(f"temporal_artifact_version_conflict:{artifact}")
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if not (
                existing.get("complete") is True
                and existing.get("dataset_hash") == prepared.dataset_hash
                and existing.get("implementation_hash") == implementation_hash
                and existing.get("model_state_hash") == state_hash
            ):
                raise ValueError(f"temporal_artifact_version_conflict:{artifact}")
            return artifact
        os.replace(temporary, artifact)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the research-only DL-D1 model")
    parser.add_argument("--features", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--config", default="configs/research/deep_d1.json")
    parser.add_argument("--output-root", default="data/research/deep/models")
    args = parser.parse_args(argv)
    config_payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if config_payload.get("research_only") is not True:
        raise ValueError("temporal_config_research_only")
    features = pd.read_parquet(args.features)
    labels = pd.read_parquet(args.labels)
    prepared = prepare_temporal_dataset(
        features,
        labels,
        horizons=tuple(config_payload["horizons"]),
        intelligence_lifecycle_path=config_payload["intelligence_lifecycle_path"],
        **config_payload["data"],
    )
    result = train_temporal_model(
        prepared,
        TemporalTrainingConfig(**config_payload["training"]),
        verbose=True,
    )
    artifact = save_temporal_artifact(
        result,
        prepared,
        Path(args.output_root),
        market=config_payload["market"],
        source_snapshot=Path(args.features).stem,
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
