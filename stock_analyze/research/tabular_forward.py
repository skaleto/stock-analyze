"""Frozen future-only observation for the regime-aware tabular candidate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from lightgbm import Booster, LGBMRegressor, early_stopping, log_evaluation

from ..utils import write_dataframe_csv_atomic, write_text_atomic
from .models import _bounded_cross_section_sample
from .portfolio_replay import replay_rule_portfolio
from .storage import ResearchStore
from .tabular_ranker import (
    TABULAR_PROTOCOL_VERSION,
    _config_hash,
    _construct_candidate_score,
    _finite_feature_columns,
    _matrix,
    _model_target_values,
    _rank_metrics,
    recency_date_balanced_weights,
)


FORWARD_PROTOCOL_VERSION = "tabular-forward-observation-v1"
MODEL_FILENAME = "model.txt"
MANIFEST_FILENAME = "manifest.json"
STATUS_FILENAME = "status.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_date(value: Any) -> str:
    normalized = str(value or "").replace("-", "")[:8]
    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"tabular_forward_date:{value}")
    return normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if np.isfinite(numeric) else None
    if value is pd.NA:
        return None
    return value


def tabular_forward_model_root(
    repo_root: str | Path,
    *,
    market: str,
    account_scope: str,
    config_hash: str,
) -> Path:
    return (
        Path(repo_root)
        / "data"
        / "research"
        / "tabular_forward"
        / str(market)
        / str(account_scope)
        / str(config_hash)
    )


def _feature_statistics(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    statistics: dict[str, dict[str, float]] = {}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        finite = values.dropna()
        statistics[column] = {
            "missing_rate": float(values.isna().mean()),
            "q01": float(finite.quantile(0.01)) if not finite.empty else 0.0,
            "q50": float(finite.quantile(0.50)) if not finite.empty else 0.0,
            "q99": float(finite.quantile(0.99)) if not finite.empty else 0.0,
        }
    return statistics


def _source_report_metadata(source_report: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(source_report, Mapping):
        return {
            "config_hash": str(source_report.get("config_hash") or ""),
            "path": None,
            "sha256": None,
        }
    path = Path(source_report)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"tabular_forward_source_report_invalid:{path}") from exc
    return {
        "config_hash": str(payload.get("config_hash") or ""),
        "path": str(path),
        "sha256": _sha256(path),
    }


def _write_current_pointer(model_root: Path, manifest: Mapping[str, Any]) -> None:
    write_text_atomic(
        model_root.parent / "current.json",
        json.dumps(
            {
                "schema_version": 1,
                "config_hash": str(manifest["config_hash"]),
                "manifest": f"{model_root.name}/{MANIFEST_FILENAME}",
                "formal_order_source": False,
                "updated_at": str(manifest["created_at"]),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def freeze_tabular_forward_model(
    repo_root: str | Path,
    *,
    dataset: pd.DataFrame,
    feature_columns: Iterable[str],
    config: dict[str, Any],
    observation_start: str,
    source_report: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    """Fit one final frozen estimator without touching the formal registry."""

    config_hash = _config_hash(config)
    source = _source_report_metadata(source_report)
    if str(source.get("config_hash") or "") != config_hash:
        raise ValueError("tabular_forward_source_config_hash_mismatch")
    model_root = tabular_forward_model_root(
        repo_root,
        market=str(config["market"]),
        account_scope=str(config["account_scope"]),
        config_hash=config_hash,
    )
    model_root.mkdir(parents=True, exist_ok=True)
    manifest_path = model_root / MANIFEST_FILENAME
    model_path = model_root / MODEL_FILENAME
    if manifest_path.exists() and model_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if (
            str(existing.get("config_hash")) == config_hash
            and str(existing.get("artifact_sha256")) == _sha256(model_path)
        ):
            _write_current_pointer(model_root, existing)
            return {
                "status": "cached",
                "model_root": str(model_root),
                "manifest_path": str(manifest_path),
                "model_path": str(model_path),
                "config_hash": config_hash,
            }

    required = {"trade_date", "label_end_date", "code", "horizon", "excess_return"}
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(
            f"tabular_forward_dataset_missing:{','.join(sorted(missing))}"
        )
    horizon = int(config["horizon"])
    development_end = _safe_date(config["development"]["end"])
    data = dataset.loc[
        pd.to_numeric(dataset["horizon"], errors="coerce").eq(horizon)
        & dataset["trade_date"].astype(str).str.replace("-", "", regex=False).le(
            development_end
        )
    ].copy()
    if data.empty:
        raise ValueError("tabular_forward_development_data_empty")
    data["trade_date"] = data["trade_date"].astype(str).str.replace(
        "-", "", regex=False
    ).str[:8]
    data["label_end_date"] = data["label_end_date"].astype(str).str.replace(
        "-", "", regex=False
    ).str[:8]
    data = data.sort_values(["trade_date", "code"], kind="stable").reset_index(
        drop=True
    )
    data["_model_target"] = _model_target_values(
        data,
        target=str(config["model"]["target"]),
    )
    training = config["training"]
    columns = _finite_feature_columns(
        data,
        feature_columns,
        minimum_coverage=float(training.get("minimum_feature_coverage", 0.55)),
    )
    dates = np.asarray(sorted(data["trade_date"].unique()))
    window_dates = dates[-max(1, int(training["training_window_sessions"])) :]
    calibration_count = max(
        10,
        int(np.ceil(len(window_dates) * float(training["calibration_fraction"]))),
    )
    embargo = max(int(training["embargo_sessions"]), 0)
    if calibration_count + embargo >= len(window_dates):
        raise ValueError("tabular_forward_training_window_insufficient")
    calibration_dates = window_dates[-calibration_count:]
    fit_dates = window_dates[: -(calibration_count + embargo)]
    calibration_start = str(calibration_dates[0])
    train = data.loc[
        data["trade_date"].isin(fit_dates)
        & data["label_end_date"].lt(calibration_start)
    ].copy()
    calibration = data.loc[data["trade_date"].isin(calibration_dates)].copy()
    if train.empty or calibration.empty:
        raise ValueError("tabular_forward_training_split_empty")
    random_state = int(training.get("random_state", 20260810))
    fit_train = _bounded_cross_section_sample(
        train,
        max_rows=int(training["max_fit_rows"]),
        random_state=random_state,
    )
    weights = recency_date_balanced_weights(
        fit_train,
        half_life_sessions=int(training["recency_half_life_sessions"]),
    )
    calibration_weights = recency_date_balanced_weights(
        calibration,
        half_life_sessions=int(training["recency_half_life_sessions"]),
    )
    parameters = dict(config["model"].get("parameters") or {})
    early_stopping_rounds = int(parameters.pop("early_stopping_rounds", 50))
    num_threads = int(parameters.pop("num_threads", 1))
    model = LGBMRegressor(
        objective="regression_l2",
        random_state=random_state,
        n_jobs=num_threads,
        deterministic=True,
        force_col_wise=True,
        verbosity=-1,
        subsample_freq=1,
        **parameters,
    )
    model.fit(
        _matrix(fit_train, columns),
        pd.to_numeric(fit_train["_model_target"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float),
        sample_weight=weights.to_numpy(dtype=float),
        eval_X=_matrix(calibration, columns),
        eval_y=pd.to_numeric(calibration["_model_target"], errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float),
        eval_sample_weight=[calibration_weights.to_numpy(dtype=float)],
        callbacks=[
            early_stopping(early_stopping_rounds, verbose=False),
            log_evaluation(0),
        ],
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=model_root,
            prefix=f".{MODEL_FILENAME}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        model.booster_.save_model(temporary_name)
        os.replace(temporary_name, model_path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    feature_schema_hash = hashlib.sha256(
        json.dumps(columns, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    importance = model.booster_.feature_importance(importance_type="gain")
    total_importance = float(importance.sum())
    manifest = _json_safe({
        "schema_version": 1,
        "protocol_version": FORWARD_PROTOCOL_VERSION,
        "training_protocol_version": TABULAR_PROTOCOL_VERSION,
        "lifecycle_status": "forward_observation",
        "model_id": f"TABULAR-{config_hash.upper()}-FWD1",
        "config_hash": config_hash,
        "market": str(config["market"]),
        "account_scope": str(config["account_scope"]),
        "horizon": horizon,
        "estimator": "lightgbm_regression",
        "target": str(config["model"]["target"]),
        "best_iteration": int(
            model.best_iteration_ or parameters.get("n_estimators", 0)
        ),
        "artifact": MODEL_FILENAME,
        "artifact_sha256": _sha256(model_path),
        "feature_schema_hash": feature_schema_hash,
        "feature_columns": list(columns),
        "feature_statistics": _feature_statistics(fit_train, columns),
        "feature_importance": {
            column: (
                float(importance[index] / total_importance)
                if total_importance > 0.0
                else 0.0
            )
            for index, column in enumerate(columns)
        },
        "score_construction": dict(config["score_construction"]),
        "portfolio": dict(config.get("portfolio") or {}),
        "training": {
            "train_start": str(fit_train["trade_date"].min()),
            "train_end": str(fit_train["trade_date"].max()),
            "train_label_end": str(fit_train["label_end_date"].max()),
            "train_rows": int(len(fit_train)),
            "calibration_start": str(calibration["trade_date"].min()),
            "calibration_end": str(calibration["trade_date"].max()),
            "calibration_rows": int(len(calibration)),
            "development_end": development_end,
            "point_in_time_audit": bool(
                str(fit_train["label_end_date"].max()) < calibration_start
            ),
        },
        "observation_start": _safe_date(observation_start),
        "source_report": source,
        "formal_order_source": False,
        "registry_mutated": False,
        "formal_strategy_weight": 0.0,
        "promotion_policy": {
            "minimum_observation_days": 60,
            "minimum_matured_days": 12,
            "automatic_promotion": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_current_pointer(model_root, manifest)
    return {
        "status": "frozen",
        "model_root": str(model_root),
        "manifest_path": str(manifest_path),
        "model_path": str(model_path),
        "config_hash": config_hash,
        "train_rows": int(len(fit_train)),
        "calibration_rows": int(len(calibration)),
        "best_iteration": manifest["best_iteration"],
    }


def _load_manifest(model_root: Path) -> tuple[dict[str, Any], Path]:
    manifest_path = model_root / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"tabular_forward_manifest_invalid:{manifest_path}") from exc
    model_path = model_root / str(manifest.get("artifact") or MODEL_FILENAME)
    if not model_path.is_file():
        raise ValueError("tabular_forward_model_missing")
    if _sha256(model_path) != str(manifest.get("artifact_sha256") or ""):
        raise ValueError("tabular_forward_model_checksum")
    if manifest.get("formal_order_source") is not False:
        raise ValueError("tabular_forward_formal_order_source")
    return manifest, model_path


def _drift_columns(
    frame: pd.DataFrame,
    manifest: Mapping[str, Any],
) -> tuple[pd.Series, pd.Series]:
    columns = tuple(str(value) for value in manifest.get("feature_columns") or ())
    matrix = _matrix(frame, columns)
    coverage = matrix.notna().mean(axis=1)
    outside = pd.DataFrame(False, index=matrix.index, columns=matrix.columns)
    statistics = manifest.get("feature_statistics") or {}
    for column in columns:
        bounds = statistics.get(column) or {}
        lower = float(bounds.get("q01") or 0.0)
        upper = float(bounds.get("q99") or 0.0)
        values = matrix[column]
        outside[column] = values.notna() & ((values < lower) | (values > upper))
    return coverage.astype(float), outside.mean(axis=1).astype(float)


def _score_current(
    featured: pd.DataFrame,
    *,
    manifest: Mapping[str, Any],
    model_path: Path,
    config: dict[str, Any],
) -> pd.DataFrame:
    if featured.empty:
        raise ValueError("tabular_forward_features_empty")
    frame = featured.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace(
        "-", "", regex=False
    ).str[:8]
    latest_date = str(frame["trade_date"].max())
    frame = frame.loc[frame["trade_date"].eq(latest_date)].copy()
    scope_column = (
        "research_scope"
        if "research_scope" in frame.columns
        else "account_id" if "account_id" in frame.columns else ""
    )
    if not scope_column:
        raise ValueError("tabular_forward_scope_missing")
    frame = frame.loc[
        frame[scope_column].astype(str).eq(str(manifest["account_scope"]))
    ].copy()
    if frame.empty:
        raise ValueError("tabular_forward_scope_empty")
    columns = tuple(str(value) for value in manifest.get("feature_columns") or ())
    missing = set(columns).difference(frame.columns)
    if missing:
        raise ValueError(
            f"tabular_forward_features_missing:{','.join(sorted(missing))}"
        )
    booster = Booster(model_file=str(model_path))
    frame["model_score"] = booster.predict(
        _matrix(frame, columns),
        num_iteration=int(manifest.get("best_iteration") or 0) or None,
    )
    frame["score"] = _construct_candidate_score(frame, config=config)
    frame["model_rank"] = frame["model_score"].rank(
        pct=True, method="average"
    )
    frame["score_rank"] = frame["score"].rank(
        ascending=False, method="first"
    ).astype(int)
    coverage, outside = _drift_columns(frame, manifest)
    frame["feature_coverage"] = coverage
    frame["feature_out_of_range_ratio"] = outside
    frame["model_id"] = str(manifest["model_id"])
    frame["config_hash"] = str(manifest["config_hash"])
    frame["feature_schema_hash"] = str(manifest["feature_schema_hash"])
    frame["model_artifact_sha256"] = str(manifest["artifact_sha256"])
    frame["formal_order_source"] = False
    frame["scored_at"] = datetime.now(timezone.utc).isoformat()
    keep = [
        "trade_date", "code", "name", "industry", "account_id",
        "research_scope", "benchmark_code", "benchmark_weight",
        "open", "high", "low", "close", "volume", "return_1",
        "avg_amount_20", "realized_volatility_20",
        "account_low_volatility_percentile", "model_score", "model_rank",
        "score", "score_rank", "feature_coverage",
        "feature_out_of_range_ratio", "model_id", "config_hash",
        "feature_schema_hash", "model_artifact_sha256",
        "formal_order_source", "scored_at",
    ]
    for column in keep:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame.loc[:, keep].sort_values(
        ["score_rank", "code"], kind="stable"
    ).reset_index(drop=True)


def _read_prediction_history(model_root: Path) -> pd.DataFrame:
    paths = sorted((model_root / "predictions").glob("*.parquet"))
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_parquet(path) for path in paths]
    return pd.concat(frames, ignore_index=True, sort=False).sort_values(
        ["trade_date", "code"], kind="stable"
    ).reset_index(drop=True)


def _matured_evidence(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    horizon: int,
    account_scope: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    empty = {
        "status": "waiting_for_horizon",
        "matured_rows": 0,
        "matured_days": 0,
        "rank_ic": None,
        "icir": None,
        "raw_rank_ic": None,
        "raw_icir": None,
        "top_bottom_spread": None,
        "buckets": [],
    }
    if predictions.empty or labels.empty:
        return empty, pd.DataFrame()
    required = {"code", "trade_date", "horizon", "excess_return", "label_end_date"}
    if required.difference(labels.columns):
        return empty, pd.DataFrame()
    realized = labels.loc[
        pd.to_numeric(labels["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    if "research_scope" in realized.columns:
        realized = realized.loc[
            realized["research_scope"].astype(str).eq(str(account_scope))
        ]
    realized["code"] = realized["code"].astype(str).str.split(".").str[0].str.zfill(6)
    realized["trade_date"] = realized["trade_date"].astype(str).str.replace(
        "-", "", regex=False
    ).str[:8]
    prediction_keys = predictions.loc[
        :, ["code", "trade_date", "score", "model_score", "config_hash"]
    ].copy()
    prediction_keys["code"] = prediction_keys["code"].astype(str).str.zfill(6)
    matured = prediction_keys.merge(
        realized.loc[:, [
            "code", "trade_date", "label_end_date", "excess_return"
        ]],
        on=["code", "trade_date"],
        how="inner",
        validate="one_to_one",
    )
    if matured.empty:
        return empty, matured
    rank_ic, icir = _rank_metrics(matured, "score")
    raw_rank_ic, raw_icir = _rank_metrics(matured, "model_score")
    percentile = matured["score"].groupby(matured["trade_date"]).rank(
        pct=True, method="average"
    )
    matured["bucket"] = np.ceil(percentile * 5).clip(1, 5).astype(int)
    buckets = [
        {
            "bucket": int(bucket),
            "mean_excess_return": float(
                pd.to_numeric(group["excess_return"], errors="coerce").mean()
            ),
            "observations": int(len(group)),
        }
        for bucket, group in matured.groupby("bucket", sort=True)
    ]
    bucket_map = {row["bucket"]: row["mean_excess_return"] for row in buckets}
    top_bottom = (
        float(bucket_map[5] - bucket_map[1])
        if 1 in bucket_map and 5 in bucket_map else None
    )
    return _json_safe({
        "status": "available",
        "matured_rows": int(len(matured)),
        "matured_days": int(matured["trade_date"].nunique()),
        "latest_label_end": str(matured["label_end_date"].max()),
        "rank_ic": rank_ic,
        "icir": icir,
        "raw_rank_ic": raw_rank_ic,
        "raw_icir": raw_icir,
        "top_bottom_spread": top_bottom,
        "buckets": buckets,
    }), matured


def _benchmark_open_map(benchmark: pd.DataFrame) -> dict[str, float]:
    if benchmark.empty or {"trade_date", "open"}.difference(benchmark.columns):
        return {}
    dates = benchmark["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    opens = pd.to_numeric(benchmark["open"], errors="coerce")
    return {
        str(day): float(price)
        for day, price in zip(dates, opens)
        if pd.notna(price) and float(price) > 0.0
    }


def _portfolio_panel(
    predictions: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame()
    dates = sorted(predictions["trade_date"].astype(str).unique())
    if len(dates) < 3:
        return pd.DataFrame()
    benchmark_open = _benchmark_open_map(benchmark)
    parts: list[pd.DataFrame] = []
    for index, signal_date in enumerate(dates[:-1]):
        next_date = dates[index + 1]
        current = predictions.loc[
            predictions["trade_date"].astype(str).eq(signal_date)
        ].copy()
        next_day = predictions.loc[
            predictions["trade_date"].astype(str).eq(next_date),
            ["code", "open", "high", "low", "close", "volume"],
        ].copy()
        next_day = next_day.rename(columns={
            "open": "entry_price",
            "high": "entry_high",
            "low": "entry_low",
            "close": "entry_close",
            "volume": "entry_volume",
        })
        current["code"] = current["code"].astype(str).str.zfill(6)
        next_day["code"] = next_day["code"].astype(str).str.zfill(6)
        current = current.merge(next_day, on="code", how="left", validate="one_to_one")
        current["entry_date"] = next_date
        entry = pd.to_numeric(current["entry_price"], errors="coerce")
        prior_close = pd.to_numeric(current["close"], errors="coerce")
        high = pd.to_numeric(current["entry_high"], errors="coerce")
        low = pd.to_numeric(current["entry_low"], errors="coerce")
        volume = pd.to_numeric(current["entry_volume"], errors="coerce")
        entry_return = entry / prior_close - 1.0
        one_price = entry.eq(high) & entry.eq(low)
        current["entry_buy_allowed"] = (
            entry.gt(0.0) & volume.fillna(0.0).gt(0.0)
            & ~(one_price & entry_return.ge(0.095))
        )
        current["entry_sell_allowed"] = (
            entry.gt(0.0) & volume.fillna(0.0).gt(0.0)
            & ~(one_price & entry_return.le(-0.095))
        )
        current["benchmark_entry_price"] = benchmark_open.get(next_date)
        current["fold"] = "forward"
        parts.append(current)
    panel = pd.concat(parts, ignore_index=True, sort=False)
    required = ["entry_price", "benchmark_entry_price"]
    panel = panel.dropna(subset=required)
    return panel.sort_values(["trade_date", "code"], kind="stable").reset_index(
        drop=True
    )


def _portfolio_evidence(
    model_root: Path,
    predictions: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    portfolio_contract: Mapping[str, Any],
) -> dict[str, Any]:
    panel = _portfolio_panel(predictions, benchmark)
    waiting = {
        "status": "waiting_for_next_open",
        "periods": 0,
        "trades": 0,
        "net_return": None,
        "benchmark_return": None,
        "net_excess_return": None,
        "max_drawdown": None,
        "active_max_drawdown": None,
    }
    if panel.empty or panel["trade_date"].nunique() < 2:
        return waiting
    try:
        replay = replay_rule_portfolio(panel, contract=portfolio_contract)
    except ValueError as exc:
        return {**waiting, "reason": str(exc)[:240]}
    portfolio_root = model_root / "portfolio"
    portfolio_root.mkdir(parents=True, exist_ok=True)
    write_dataframe_csv_atomic(replay.periods, portfolio_root / "periods.csv", index=False)
    write_dataframe_csv_atomic(replay.trades, portfolio_root / "trades.csv", index=False)
    write_dataframe_csv_atomic(replay.nav, portfolio_root / "nav.csv", index=False)
    write_dataframe_csv_atomic(replay.decisions, portfolio_root / "decisions.csv", index=False)
    metrics = replay.metrics
    return _json_safe({
        "status": "available",
        "simulator_version": metrics.get("simulator_version"),
        "periods": int(metrics.get("portfolio_rebalance_periods") or 0),
        "rebalance_periods": int(metrics.get("scheduled_rebalance_periods") or 0),
        "trades": int(metrics.get("trade_count") or 0),
        "net_return": metrics.get("net_return"),
        "benchmark_return": metrics.get("benchmark_return"),
        "net_excess_return": metrics.get("net_excess_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "active_max_drawdown": metrics.get("active_max_drawdown"),
        "information_ratio": metrics.get("information_ratio"),
        "annual_turnover": metrics.get("annual_turnover"),
        "capital_utilization": metrics.get("capital_utilization"),
        "execution_cost_bps": metrics.get("execution_cost_bps"),
        "attribution_status": metrics.get("attribution_status"),
    })


def _promotion_summary(
    manifest: Mapping[str, Any],
    *,
    observation_days: int,
    matured: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    drift_alert: bool,
) -> dict[str, Any]:
    policy = manifest.get("promotion_policy") or {}
    checks = {
        "observation_days": observation_days
        >= int(policy.get("minimum_observation_days") or 60),
        "matured_days": int(matured.get("matured_days") or 0)
        >= int(policy.get("minimum_matured_days") or 12),
        "rank_ic": (
            matured.get("rank_ic") is not None
            and float(matured["rank_ic"]) >= 0.03
        ),
        "icir": (
            matured.get("icir") is not None
            and float(matured["icir"]) >= 0.35
        ),
        "portfolio_positive_active": (
            portfolio.get("net_excess_return") is not None
            and float(portfolio["net_excess_return"]) > 0.0
        ),
        "active_drawdown": (
            portfolio.get("active_max_drawdown") is not None
            and float(portfolio["active_max_drawdown"]) <= 0.12
        ),
        "feature_drift": not drift_alert,
    }
    passed = all(checks.values())
    return {
        "status": "eligible_for_manual_review" if passed else "evidence_pending",
        "checks": checks,
        "automatic_promotion": False,
        "formal_strategy_unchanged": True,
    }


def observe_tabular_forward_model(
    repo_root: str | Path,
    *,
    model_root: str | Path,
    featured: pd.DataFrame,
    labels: pd.DataFrame,
    benchmark: pd.DataFrame,
    config: dict[str, Any],
    portfolio_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Score one new market day and rebuild future-only evidence idempotently."""

    root = Path(model_root)
    manifest, model_path = _load_manifest(root)
    if _config_hash(config) != str(manifest["config_hash"]):
        raise ValueError("tabular_forward_config_hash_mismatch")
    scored = _score_current(
        featured,
        manifest=manifest,
        model_path=model_path,
        config=config,
    )
    signal_date = str(scored["trade_date"].max())
    if signal_date < str(manifest["observation_start"]):
        raise ValueError(
            f"tabular_forward_before_observation_start:{signal_date}"
        )
    predictions_root = root / "predictions"
    predictions_root.mkdir(parents=True, exist_ok=True)
    prediction_path = predictions_root / f"{signal_date}.parquet"
    prediction_write = "cached"
    if not prediction_path.exists():
        ResearchStore(root).write_parquet_atomic(prediction_path, scored)
        prediction_write = "written"
    predictions = _read_prediction_history(root)
    matured, matured_rows = _matured_evidence(
        predictions,
        labels,
        horizon=int(manifest["horizon"]),
        account_scope=str(manifest["account_scope"]),
    )
    if not matured_rows.empty:
        ResearchStore(root).write_parquet_atomic(
            root / "matured_predictions.parquet",
            matured_rows,
        )
    effective_contract = dict(portfolio_contract)
    portfolio_config = config.get("portfolio") or {}
    effective_contract["rebalance_frequency"] = str(
        portfolio_config.get(
            "rebalance_frequency",
            effective_contract.get("rebalance_frequency", "monthly"),
        )
    )
    if portfolio_config.get("allocation_policy"):
        effective_contract["allocation_policy"] = dict(
            portfolio_config["allocation_policy"]
        )
    portfolio = _portfolio_evidence(
        root,
        predictions,
        benchmark,
        portfolio_contract=effective_contract,
    )
    observation_days = int(predictions["trade_date"].nunique())
    median_coverage = float(
        pd.to_numeric(predictions["feature_coverage"], errors="coerce").median()
    )
    median_outside = float(
        pd.to_numeric(
            predictions["feature_out_of_range_ratio"], errors="coerce"
        ).median()
    )
    drift_alert = median_coverage < 0.80 or median_outside > 0.20
    promotion = _promotion_summary(
        manifest,
        observation_days=observation_days,
        matured=matured,
        portfolio=portfolio,
        drift_alert=drift_alert,
    )
    status = _json_safe({
        "schema_version": 1,
        "protocol_version": FORWARD_PROTOCOL_VERSION,
        "status": "observing",
        "lifecycle_status": "forward_observation",
        "model_id": manifest["model_id"],
        "config_hash": manifest["config_hash"],
        "model_artifact_sha256": manifest["artifact_sha256"],
        "market": manifest["market"],
        "account_scope": manifest["account_scope"],
        "horizon": manifest["horizon"],
        "observation_start": manifest["observation_start"],
        "latest_prediction_date": signal_date,
        "observation_days": observation_days,
        "prediction_rows": int(len(predictions)),
        "latest_candidates": int(len(scored)),
        "latest_selected": int(
            pd.to_numeric(scored["score_rank"], errors="coerce").le(50).sum()
        ),
        "prediction_write": prediction_write,
        "matured_evidence": matured,
        "portfolio": portfolio,
        "drift": {
            "status": "alert" if drift_alert else "normal",
            "median_feature_coverage": median_coverage,
            "median_out_of_range_ratio": median_outside,
        },
        "promotion": promotion,
        "formal_order_source": False,
        "registry_mutated": False,
        "formal_strategy_weight": 0.0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    status_path = root / STATUS_FILENAME
    write_text_atomic(
        status_path,
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        **status,
        "status_path": str(status_path),
        "prediction_path": str(prediction_path),
    }


__all__ = [
    "FORWARD_PROTOCOL_VERSION",
    "freeze_tabular_forward_model",
    "observe_tabular_forward_model",
    "tabular_forward_model_root",
]
