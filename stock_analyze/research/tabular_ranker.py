"""Single-hypothesis regime-aware LightGBM ranker for development evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml
from lightgbm import LGBMRanker, LGBMRegressor, early_stopping, log_evaluation

from ..utils import write_text_atomic
from .governance import (
    deflated_sharpe_probability,
    probability_of_backtest_overfit,
)
from .models import (
    _bounded_cross_section_sample,
    _ranking_target_values,
    make_purged_walk_forward_splits,
)
from .portfolio_replay import replay_model_portfolio, replay_rule_portfolio
from .risk_model import neutralize_cross_sectional_scores
from .score_calibration import fit_predict_score_calibration


TABULAR_PROTOCOL_VERSION = "regime-aware-tabular-alpha-v2"


@dataclass(frozen=True)
class RollingPurgedSplit:
    train_indices: np.ndarray
    calibration_indices: np.ndarray
    validation_indices: np.ndarray


@dataclass
class TabularFitResult:
    evaluation: pd.DataFrame
    folds: tuple[dict[str, Any], ...]
    feature_columns: tuple[str, ...]
    feature_importance: dict[str, float]
    raw_rank_ic: float
    raw_icir: float
    neutralized_rank_ic: float
    neutralized_icir: float
    point_in_time_audit: bool
    estimator: str
    estimator_version: str
    calibrations: tuple[dict[str, Any], ...]
    protocol_version: str = TABULAR_PROTOCOL_VERSION


def _config_hash(config: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in config.items()
        if key != "config_hash"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def load_tabular_ranker_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tabular_config_mapping_required")
    if payload.get("research_only") is not True:
        raise ValueError("tabular_config_research_only")
    if payload.get("formal_order_source") is not False:
        raise ValueError("tabular_config_formal_order_source")
    feature_set = str(payload.get("feature_set") or "alpha158_lite_v1")
    if feature_set not in {"alpha158_lite_v1", "alpha158_lite_moneyflow_v2"}:
        raise ValueError("tabular_config_feature_set")
    for key in ("market", "account_scope", "horizon", "development", "training", "model", "gates"):
        if key not in payload:
            raise ValueError(f"tabular_config_missing:{key}")
    if str((payload.get("model") or {}).get("estimator")) != "lightgbm_regression":
        raise ValueError("tabular_config_estimator")
    if str((payload.get("model") or {}).get("target")) not in {
        "daily_cross_sectional_percentile_v1",
        "residualized_cross_sectional_rank_v1",
    }:
        raise ValueError("tabular_config_target")
    fallback = payload.get("fallback") or {}
    if fallback.get("enabled") is True:
        if str(fallback.get("estimator")) != "lightgbm_lambdarank":
            raise ValueError("tabular_config_fallback_estimator")
        if str(fallback.get("selection_policy")) != "gate-count-then-net-excess-v1":
            raise ValueError("tabular_config_fallback_selection_policy")
        if int(fallback.get("relevance_bins") or 0) < 3:
            raise ValueError("tabular_config_fallback_relevance_bins")
    score_construction = payload.get("score_construction") or {}
    core_weight = float(score_construction.get("core_weight") or 0.0)
    model_weight = float(score_construction.get("model_weight") or 0.0)
    if str(score_construction.get("core")) != "account_low_volatility_percentile":
        raise ValueError("tabular_config_score_core")
    if model_weight < 0.0 or model_weight > 0.20:
        raise ValueError("tabular_config_model_tilt")
    if abs(core_weight + model_weight - 1.0) > 1e-9:
        raise ValueError("tabular_config_score_weights")
    training = payload.get("training") or {}
    for key in (
        "n_splits",
        "training_window_sessions",
        "calibration_fraction",
        "embargo_sessions",
        "recency_half_life_sessions",
        "max_fit_rows",
    ):
        if key not in training:
            raise ValueError(f"tabular_config_training_missing:{key}")
    if feature_set == "alpha158_lite_moneyflow_v2":
        moneyflow_coverage = float(training.get("minimum_moneyflow_coverage", -1.0))
        if not 0.0 <= moneyflow_coverage <= 1.0:
            raise ValueError("tabular_config_moneyflow_coverage")
    calibration = payload.get("calibration") or {}
    if calibration.get("enabled") is True:
        if str(calibration.get("method")) != "date_bucket_isotonic_v1":
            raise ValueError("tabular_config_calibration_method")
        if int(calibration.get("bins") or 0) < 3:
            raise ValueError("tabular_config_calibration_bins")
        if int(calibration.get("minimum_dates") or 0) < 20:
            raise ValueError("tabular_config_calibration_minimum_dates")
    portfolio = payload.get("portfolio") or {}
    replay_contract = str(portfolio.get("replay_contract") or "rule")
    if replay_contract not in {"model", "rule"}:
        raise ValueError("tabular_config_replay_contract")
    if replay_contract == "model" and calibration.get("enabled") is not True:
        raise ValueError("tabular_config_model_replay_requires_calibration")
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    result["horizon"] = int(result["horizon"])
    result["config_hash"] = _config_hash(result)
    return result


def make_rolling_purged_splits(
    data: pd.DataFrame,
    *,
    n_splits: int,
    training_window_sessions: int,
    calibration_fraction: float,
    embargo_sessions: int,
) -> list[RollingPurgedSplit]:
    if not 0.05 <= float(calibration_fraction) <= 0.40:
        raise ValueError("tabular_calibration_fraction")
    outer_splits = make_purged_walk_forward_splits(
        data,
        n_splits=int(n_splits),
        embargo=int(embargo_sessions),
    )
    splits: list[RollingPurgedSplit] = []
    for outer in outer_splits:
        outer_train = data.loc[outer.train_indices].copy()
        validation = data.loc[outer.validation_indices].copy()
        available_dates = np.asarray(sorted(outer_train["trade_date"].astype(str).unique()))
        window_dates = available_dates[-max(1, int(training_window_sessions)):]
        calibration_count = max(
            10,
            int(np.ceil(len(window_dates) * float(calibration_fraction))),
        )
        if calibration_count >= len(window_dates):
            continue
        calibration_dates = window_dates[-calibration_count:]
        calibration_start = str(calibration_dates[0])
        pre_calibration_dates = window_dates[:calibration_count * -1]
        if int(embargo_sessions) > 0:
            pre_calibration_dates = pre_calibration_dates[: -int(embargo_sessions)]
        train = outer_train.loc[
            outer_train["trade_date"].astype(str).isin(pre_calibration_dates)
            & outer_train["label_end_date"].astype(str).lt(calibration_start)
        ]
        calibration = outer_train.loc[
            outer_train["trade_date"].astype(str).isin(calibration_dates)
        ]
        if not validation.empty:
            validation_start = str(validation["trade_date"].astype(str).min())
            calibration = calibration.loc[
                calibration["label_end_date"].astype(str).lt(validation_start)
            ]
        if train.empty or calibration.empty or validation.empty:
            continue
        splits.append(
            RollingPurgedSplit(
                train_indices=train.index.to_numpy(),
                calibration_indices=calibration.index.to_numpy(),
                validation_indices=validation.index.to_numpy(),
            )
        )
    return splits


def recency_date_balanced_weights(
    frame: pd.DataFrame,
    *,
    half_life_sessions: int,
) -> pd.Series:
    if "trade_date" not in frame.columns:
        raise ValueError("tabular_weights_trade_date_missing")
    dates = frame["trade_date"].astype(str)
    ordered = sorted(dates.unique())
    if not ordered:
        raise ValueError("tabular_weights_empty")
    position = {trade_date: index for index, trade_date in enumerate(ordered)}
    ages = dates.map(lambda value: len(ordered) - 1 - position[str(value)]).astype(float)
    decay = np.power(0.5, ages / max(float(half_life_sessions), 1.0))
    counts = dates.groupby(dates).transform("size").astype(float)
    weights = pd.Series(decay.to_numpy() / counts.to_numpy(), index=frame.index, dtype=float)
    mean = float(weights.mean())
    return weights / mean if mean > 0.0 else weights


def _rank_metrics(evaluation: pd.DataFrame, score_column: str) -> tuple[float, float]:
    daily: list[float] = []
    for _, group in evaluation.groupby("trade_date", sort=True):
        aligned = group.loc[:, [score_column, "excess_return"]].apply(
            pd.to_numeric,
            errors="coerce",
        ).dropna()
        if len(aligned) < 3 or aligned[score_column].nunique() < 2:
            continue
        value = aligned[score_column].corr(
            aligned["excess_return"],
            method="spearman",
        )
        if pd.notna(value):
            daily.append(float(value))
    mean = float(np.mean(daily)) if daily else 0.0
    std = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    return mean, mean / std if std > 1e-12 else 0.0


def _finite_feature_columns(
    data: pd.DataFrame,
    feature_columns: Iterable[str],
    *,
    minimum_coverage: float,
) -> tuple[str, ...]:
    selected = tuple(
        column
        for column in feature_columns
        if column in data.columns
        and pd.to_numeric(data[column], errors="coerce").notna().mean()
        >= float(minimum_coverage)
        and pd.to_numeric(data[column], errors="coerce").nunique(dropna=True) > 1
    )
    if not selected:
        raise ValueError("tabular_feature_contract_empty")
    return selected


def _matrix(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    return frame.loc[:, columns].apply(pd.to_numeric, errors="coerce").replace(
        [np.inf, -np.inf],
        np.nan,
    )


def _model_target_values(frame: pd.DataFrame, *, target: str) -> np.ndarray:
    normalized = str(target).strip().lower()
    raw = _ranking_target_values(frame, "daily_cross_sectional_percentile_v1")
    if normalized == "daily_cross_sectional_percentile_v1":
        return raw
    if normalized != "residualized_cross_sectional_rank_v1":
        raise ValueError(f"tabular_target_unknown:{normalized}")
    target_frame = pd.DataFrame({
        "trade_date": frame["trade_date"].astype(str),
        "_model_target": raw,
        "industry": frame.get(
            "industry",
            pd.Series("unclassified", index=frame.index),
        ),
        "log_total_mv": pd.to_numeric(
            frame.get("log_total_mv"),
            errors="coerce",
        ),
        "account_low_volatility_percentile": pd.to_numeric(
            frame.get("account_low_volatility_percentile"),
            errors="coerce",
        ),
    }, index=frame.index)
    residualized = neutralize_cross_sectional_scores(
        target_frame,
        score_column="_model_target",
        size_column="log_total_mv",
        volatility_column="account_low_volatility_percentile",
    )["neutralized_score"]
    scale = residualized.groupby(
        target_frame["trade_date"],
        sort=False,
    ).transform("std").replace(0.0, np.nan)
    return residualized.div(scale).fillna(0.0).to_numpy(dtype=float)


def _ranking_relevance(frame: pd.DataFrame, *, bins: int) -> np.ndarray:
    target = pd.to_numeric(frame["_model_target"], errors="coerce")
    percentile = target.groupby(
        frame["trade_date"].astype(str),
        sort=False,
    ).rank(pct=True, method="average").fillna(0.5)
    return np.floor(percentile * int(bins)).clip(0, int(bins) - 1).astype(int)


def _ranking_groups(frame: pd.DataFrame) -> np.ndarray:
    return frame.groupby("trade_date", sort=False).size().to_numpy(dtype=int)


def _construct_candidate_score(
    frame: pd.DataFrame,
    *,
    config: dict[str, Any],
) -> pd.Series:
    construction = config["score_construction"]
    dates = frame["trade_date"].astype(str)
    model_rank = pd.to_numeric(frame["model_score"], errors="coerce").groupby(
        dates,
        sort=False,
    ).rank(pct=True, method="average").sub(0.5).fillna(0.0)
    core = pd.to_numeric(
        frame[str(construction["core"])],
        errors="coerce",
    ).sub(0.5).fillna(-0.5)
    combined = (
        float(construction["core_weight"]) * core
        + float(construction["model_weight"]) * model_rank
    )
    return combined.groupby(dates, sort=False).rank(
        pct=True,
        method="average",
    ) - 0.5


def fit_walk_forward_tabular_ranker(
    dataset: pd.DataFrame,
    *,
    feature_columns: Iterable[str],
    config: dict[str, Any],
    estimator: str | None = None,
) -> TabularFitResult:
    required = {
        "trade_date",
        "label_end_date",
        "code",
        "horizon",
        "excess_return",
    }
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(f"tabular_dataset_missing:{','.join(sorted(missing))}")
    horizon = int(config["horizon"])
    data = dataset.loc[
        pd.to_numeric(dataset["horizon"], errors="coerce").eq(horizon)
    ].copy()
    data["trade_date"] = data["trade_date"].astype(str)
    data["label_end_date"] = data["label_end_date"].astype(str)
    data = data.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    model_target = str((config.get("model") or {}).get("target"))
    data["_model_target"] = _model_target_values(data, target=model_target)
    training = config["training"]
    columns = _finite_feature_columns(
        data,
        feature_columns,
        minimum_coverage=float(training.get("minimum_feature_coverage", 0.55)),
    )
    splits = make_rolling_purged_splits(
        data,
        n_splits=int(training["n_splits"]),
        training_window_sessions=int(training["training_window_sessions"]),
        calibration_fraction=float(training["calibration_fraction"]),
        embargo_sessions=int(training["embargo_sessions"]),
    )
    if len(splits) != int(training["n_splits"]):
        raise ValueError("tabular_walk_forward_insufficient")

    selected_estimator = str(
        estimator or (config.get("model") or {}).get("estimator")
    )
    if selected_estimator == "lightgbm_regression":
        estimator_config = config.get("model") or {}
    elif selected_estimator == "lightgbm_lambdarank":
        estimator_config = config.get("fallback") or {}
    else:
        raise ValueError(f"tabular_estimator_unknown:{selected_estimator}")
    parameters = dict(estimator_config.get("parameters") or {})
    early_stopping_rounds = int(parameters.pop("early_stopping_rounds", 50))
    num_threads = int(parameters.pop("num_threads", 1))
    random_state = int(training.get("random_state", 20260810))
    validation_parts: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    importance = np.zeros(len(columns), dtype=float)
    audit: list[bool] = []
    for fold_number, split in enumerate(splits):
        train = data.loc[split.train_indices].copy()
        calibration = data.loc[split.calibration_indices].copy()
        validation = data.loc[split.validation_indices].copy()
        fit_train = _bounded_cross_section_sample(
            train,
            max_rows=int(training["max_fit_rows"]),
            random_state=random_state + fold_number,
        )
        weights = recency_date_balanced_weights(
            fit_train,
            half_life_sessions=int(training["recency_half_life_sessions"]),
        )
        calibration_weights = recency_date_balanced_weights(
            calibration,
            half_life_sessions=int(training["recency_half_life_sessions"]),
        )
        common_parameters = {
            "random_state": random_state + fold_number,
            "n_jobs": num_threads,
            "deterministic": True,
            "force_col_wise": True,
            "verbosity": -1,
            "subsample_freq": 1,
            **parameters,
        }
        if selected_estimator == "lightgbm_lambdarank":
            relevance_bins = int(estimator_config["relevance_bins"])
            model = LGBMRanker(
                objective="lambdarank",
                metric="ndcg",
                label_gain=list(range(relevance_bins)),
                **common_parameters,
            )
            model.fit(
                _matrix(fit_train, columns),
                _ranking_relevance(fit_train, bins=relevance_bins),
                sample_weight=weights.to_numpy(dtype=float),
                group=_ranking_groups(fit_train),
                eval_X=_matrix(calibration, columns),
                eval_y=_ranking_relevance(calibration, bins=relevance_bins),
                eval_sample_weight=[calibration_weights.to_numpy(dtype=float)],
                eval_group=[_ranking_groups(calibration)],
                eval_at=(10, 50),
                callbacks=[
                    early_stopping(early_stopping_rounds, verbose=False),
                    log_evaluation(0),
                ],
            )
        else:
            model = LGBMRegressor(
                objective="regression_l2",
                **common_parameters,
            )
            model.fit(
                _matrix(fit_train, columns),
                pd.to_numeric(fit_train["_model_target"], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=float),
                sample_weight=weights.to_numpy(dtype=float),
                eval_X=_matrix(calibration, columns),
                eval_y=pd.to_numeric(
                    calibration["_model_target"], errors="coerce"
                ).fillna(0.0).to_numpy(dtype=float),
                eval_sample_weight=[calibration_weights.to_numpy(dtype=float)],
                callbacks=[
                    early_stopping(early_stopping_rounds, verbose=False),
                    log_evaluation(0),
                ],
            )
        calibration_config = config.get("calibration") or {}
        calibration_enabled = calibration_config.get("enabled") is True
        if calibration_enabled:
            calibration["model_score"] = model.predict(
                _matrix(calibration, columns),
                num_iteration=model.best_iteration_,
            )
            calibration["score"] = _construct_candidate_score(
                calibration,
                config=config,
            )
        validation["model_score"] = model.predict(
            _matrix(validation, columns),
            num_iteration=model.best_iteration_,
        )
        validation["score"] = _construct_candidate_score(validation, config=config)
        if bool((config.get("score_construction") or {}).get("neutralize_score")):
            if calibration_enabled:
                calibration_scored = neutralize_cross_sectional_scores(calibration)
                calibration_scored["score"] = calibration_scored.groupby(
                    calibration_scored["trade_date"].astype(str),
                    sort=False,
                )["neutralized_score"].rank(pct=True, method="average") - 0.5
            neutralized = neutralize_cross_sectional_scores(validation)
            centered_rank = neutralized.groupby(
                neutralized["trade_date"].astype(str),
                sort=False,
            )["neutralized_score"].rank(pct=True, method="average") - 0.5
            neutralized["score"] = centered_rank
        else:
            if calibration_enabled:
                calibration_scored = calibration.copy()
            neutralized = validation.copy()
            neutralized["raw_score"] = neutralized["model_score"]
            neutralized["neutralized_score"] = neutralized["score"]
        if calibration_enabled:
            calibrated = fit_predict_score_calibration(
                calibration_scored,
                neutralized,
                score_column="score",
                return_column="excess_return",
                horizon=horizon,
                bins=int(calibration_config["bins"]),
                minimum_dates=int(calibration_config["minimum_dates"]),
            )
            uncertainty_multiple = max(
                float(calibration_config.get("uncertainty_multiple") or 1.0),
                0.0,
            )
            neutralized["expected_excess_return"] = (
                calibrated.expected_excess_return
            )
            neutralized["prediction_uncertainty_bps"] = (
                calibrated.uncertainty_bps * uncertainty_multiple
            )
            neutralized["prediction_confidence"] = calibrated.confidence
            prediction_applied = (
                pd.to_numeric(
                    neutralized["expected_excess_return"], errors="coerce"
                ).notna()
                & pd.to_numeric(
                    neutralized["prediction_confidence"], errors="coerce"
                ).notna()
            )
            feature_schema_hash = hashlib.sha256(
                json.dumps(columns, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:16]
            neutralized["prediction_applied"] = prediction_applied
            neutralized["prediction_model_versions"] = (
                f"{TABULAR_PROTOCOL_VERSION}:{selected_estimator}:"
                f"{_config_hash(config)}:fold-{fold_number}"
            )
            neutralized["prediction_feature_schema_hash"] = feature_schema_hash
            neutralized["prediction_calibrator_hash"] = calibrated.calibrator_hash
            calibration_rows.append({
                "fold": fold_number,
                "method": str(calibration_config["method"]),
                "calibrator_hash": calibrated.calibrator_hash,
                "effective_date_count": calibrated.effective_date_count,
                "calibration_start": str(calibration["trade_date"].min()),
                "calibration_end": str(calibration["trade_date"].max()),
                "validation_start": str(validation["trade_date"].min()),
            })
        neutralized["fold"] = fold_number
        validation_parts.append(neutralized)
        importance += model.booster_.feature_importance(importance_type="gain")
        point_in_time = bool(
            str(fit_train["label_end_date"].max())
            < str(calibration["trade_date"].min())
            and str(calibration["label_end_date"].max())
            < str(validation["trade_date"].min())
        )
        audit.append(point_in_time)
        fold_rows.append({
            "fold": fold_number,
            "train_start": str(fit_train["trade_date"].min()),
            "train_end": str(fit_train["trade_date"].max()),
            "calibration_start": str(calibration["trade_date"].min()),
            "calibration_end": str(calibration["trade_date"].max()),
            "validation_start": str(validation["trade_date"].min()),
            "validation_end": str(validation["trade_date"].max()),
            "train_rows": int(len(fit_train)),
            "calibration_rows": int(len(calibration)),
            "validation_rows": int(len(validation)),
            "best_iteration": int(model.best_iteration_ or parameters.get("n_estimators", 0)),
            "point_in_time_audit": point_in_time,
        })
    evaluation = pd.concat(validation_parts, ignore_index=True).sort_values(
        ["trade_date", "code"],
        kind="stable",
    )
    raw_rank_ic, raw_icir = _rank_metrics(evaluation, "model_score")
    neutralized_rank_ic, neutralized_icir = _rank_metrics(evaluation, "score")
    total_importance = float(importance.sum())
    import lightgbm

    return TabularFitResult(
        evaluation=evaluation,
        folds=tuple(fold_rows),
        feature_columns=columns,
        feature_importance={
            column: float(importance[index] / total_importance)
            if total_importance > 0.0 else 0.0
            for index, column in enumerate(columns)
        },
        raw_rank_ic=raw_rank_ic,
        raw_icir=raw_icir,
        neutralized_rank_ic=neutralized_rank_ic,
        neutralized_icir=neutralized_icir,
        point_in_time_audit=bool(all(audit)),
        estimator=selected_estimator,
        estimator_version=str(lightgbm.__version__),
        calibrations=tuple(calibration_rows),
    )


def _should_run_lambdarank_fallback(
    fitted: TabularFitResult,
    gate: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    fallback = config.get("fallback") or {}
    trigger = fallback.get("trigger") or {}
    return bool(
        fallback.get("enabled") is True
        and not bool(gate.get("passed"))
        and str(trigger.get("required_failed_gate") or "top_tail")
        in set(gate.get("reasons") or ())
        and float(fitted.raw_rank_ic)
        > float(trigger.get("minimum_raw_rank_ic") or 0.0)
    )


def _candidate_selection_key(evidence: dict[str, Any]) -> tuple[Any, ...]:
    gate = evidence.get("development_gate") or {}
    checks = gate.get("checks") or {}
    metrics = evidence.get("metrics") or {}
    return (
        bool(gate.get("passed")),
        sum(bool(value) for value in checks.values()),
        float(metrics.get("net_excess_return") or 0.0),
        -float(metrics.get("active_max_drawdown") or 1.0),
        -float(metrics.get("max_drawdown") or 1.0),
        float(metrics.get("information_ratio") or 0.0),
        float(metrics.get("rank_ic") or 0.0),
    )


def _select_candidate_evidence(
    primary: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Keep the primary on ties; a fallback must prove a bounded improvement."""

    if _candidate_selection_key(fallback) > _candidate_selection_key(primary):
        return fallback
    return primary


def evaluate_tabular_development_gate(
    metrics: dict[str, Any],
    *,
    folds: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    buckets: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    bucket_values = [
        float(item.get("mean_excess_return") or 0.0)
        for item in sorted(buckets, key=lambda item: int(item.get("bucket") or 0))
    ]
    bucket_spearman = (
        float(pd.Series(range(1, len(bucket_values) + 1)).corr(
            pd.Series(bucket_values),
            method="spearman",
        ))
        if len(bucket_values) >= 3 else -1.0
    )
    positive_folds = sum(
        float(item.get("rank_ic") or 0.0) > 0.0
        and float(item.get("net_excess_return") or 0.0) > 0.0
        for item in folds
    )
    top_tail = bool(
        len(bucket_values) >= 5
        and bucket_values[-1] > bucket_values[0]
        and bucket_values[-1] >= bucket_values[-2]
        and bucket_spearman >= float(thresholds["minimum_bucket_spearman"])
    )
    checks = {
        "point_in_time_audit": metrics.get("point_in_time_audit") is True,
        "rank_ic": float(metrics.get("rank_ic") or 0.0)
        > float(thresholds["minimum_rank_ic"]),
        "icir": float(metrics.get("icir") or 0.0)
        >= float(thresholds["minimum_icir"]),
        "positive_folds": positive_folds >= int(thresholds["minimum_positive_folds"]),
        "top_tail": top_tail,
        "net_excess_return": float(metrics.get("net_excess_return") or 0.0)
        >= float(thresholds["minimum_net_excess_return"]),
        "active_max_drawdown": float(metrics.get("active_max_drawdown") or 1.0)
        <= float(thresholds["maximum_active_drawdown"]),
        "max_drawdown": float(metrics.get("max_drawdown") or 1.0)
        <= float(thresholds["maximum_total_drawdown"]),
        "annual_turnover": float(metrics.get("annual_turnover") or 1e9)
        <= float(thresholds["maximum_annual_turnover"]),
        "capital_utilization": float(metrics.get("capital_utilization") or 0.0)
        >= float(thresholds["minimum_capital_utilization"]),
        "deflated_sharpe_probability": float(
            metrics.get("deflated_sharpe_probability") or 0.0
        ) >= float(thresholds["minimum_deflated_sharpe_probability"]),
        "probability_of_backtest_overfit": float(
            metrics.get("probability_of_backtest_overfit") or 1.0
        ) <= float(thresholds["maximum_probability_of_backtest_overfit"]),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not reasons,
        "checks": checks,
        "reasons": reasons,
        "positive_folds": int(positive_folds),
        "bucket_spearman": bucket_spearman,
    }


_BOUNDED_REPLAY_KEYS = (
    "simulator_version",
    "gross_return",
    "net_return",
    "benchmark_return",
    "net_excess_return",
    "portfolio_cagr",
    "benchmark_cagr",
    "cumulative_relative_wealth",
    "annualized_excess_wealth",
    "max_drawdown",
    "active_max_drawdown",
    "annual_turnover",
    "capital_utilization",
    "portfolio_sharpe",
    "information_ratio",
    "portfolio_rebalance_periods",
    "trade_count",
    "execution_cost_bps",
    "attribution_status",
    "execution_evidence_status",
    "missing_liquidity_notional_ratio",
    "impact_capped_notional_ratio",
    "scheduled_rebalance_periods",
    "replay_contract",
)


def _bounded_replay(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: metrics.get(key) for key in _BOUNDED_REPLAY_KEYS if key in metrics}


def _score_buckets(evaluation: pd.DataFrame) -> list[dict[str, Any]]:
    ranked = evaluation.loc[:, ["trade_date", "score", "excess_return"]].copy()
    percentile = ranked.groupby("trade_date", sort=False)["score"].rank(
        pct=True,
        method="first",
    )
    ranked["bucket"] = np.ceil(percentile * 5.0).clip(1, 5).astype(int)
    return [
        {
            "bucket": int(bucket),
            "mean_excess_return": float(
                pd.to_numeric(group["excess_return"], errors="coerce").mean()
            ),
            "observations": int(len(group)),
        }
        for bucket, group in ranked.groupby("bucket", sort=True)
    ]


def _economic_calibration_diagnostics(
    fitted: TabularFitResult,
    replay: Any,
) -> dict[str, Any]:
    expected = pd.to_numeric(
        fitted.evaluation.get(
            "expected_excess_return",
            pd.Series(np.nan, index=fitted.evaluation.index),
        ),
        errors="coerce",
    )
    uncertainty = pd.to_numeric(
        fitted.evaluation.get(
            "prediction_uncertainty_bps",
            pd.Series(np.nan, index=fitted.evaluation.index),
        ),
        errors="coerce",
    )
    valid = expected.notna() & uncertainty.notna() & uncertainty.ge(0.0)
    valid_uncertainty = uncertainty.loc[valid]
    lower_bound = expected - uncertainty.div(10_000.0)
    decisions = replay.decisions if isinstance(replay.decisions, pd.DataFrame) else pd.DataFrame()
    tracking_error = pd.to_numeric(
        decisions.get(
            "optimizer_tracking_error",
            pd.Series(np.nan, index=decisions.index),
        ),
        errors="coerce",
    ).dropna()
    reasons = (
        decisions.get("no_trade_reason", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
    )
    reason_counts = {
        str(reason): int(count)
        for reason, count in reasons.loc[reasons.ne("")].value_counts().items()
    }
    effective_dates = [
        float(item.get("effective_date_count") or 0.0)
        for item in fitted.calibrations
    ]
    return {
        "fold_count": int(len(fitted.calibrations)),
        "calibrator_hashes": [
            str(item.get("calibrator_hash") or "")
            for item in fitted.calibrations
        ],
        "minimum_effective_date_count": (
            float(min(effective_dates)) if effective_dates else 0.0
        ),
        "economic_prediction_coverage": (
            float(valid.mean()) if len(valid) else 0.0
        ),
        "positive_lower_bound_coverage": (
            float(lower_bound.loc[valid].gt(0.0).mean())
            if bool(valid.any()) else 0.0
        ),
        "uncertainty_bps_p50": (
            float(valid_uncertainty.quantile(0.50))
            if not valid_uncertainty.empty else 0.0
        ),
        "uncertainty_bps_p90": (
            float(valid_uncertainty.quantile(0.90))
            if not valid_uncertainty.empty else 0.0
        ),
        "optimizer_tracking_error_p50": (
            float(tracking_error.quantile(0.50))
            if not tracking_error.empty else None
        ),
        "optimizer_tracking_error_p90": (
            float(tracking_error.quantile(0.90))
            if not tracking_error.empty else None
        ),
        "no_trade_reasons": reason_counts,
    }


def _active_return_series(replay: Any) -> pd.Series:
    periods = replay.periods.copy()
    return periods.groupby("signal_date", sort=True)["active_return"].mean().astype(float)


def _control_governance(
    evaluation: pd.DataFrame,
    *,
    candidate_replay: Any,
    controls: Iterable[str],
    portfolio_contract: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    trial_series = {"candidate": _active_return_series(candidate_replay)}
    trial_sharpes = [float(candidate_replay.metrics.get("information_ratio") or 0.0)]
    evidence: dict[str, dict[str, Any]] = {}
    for control in controls:
        if control not in evaluation.columns:
            evidence[str(control)] = {"status": "missing"}
            continue
        values = pd.to_numeric(evaluation[control], errors="coerce")
        if values.notna().sum() < 3 or values.nunique(dropna=True) < 2:
            evidence[str(control)] = {"status": "unusable"}
            continue
        control_frame = evaluation.copy()
        control_frame["score"] = values
        replay = replay_rule_portfolio(
            control_frame,
            contract=portfolio_contract,
        )
        trial_series[str(control)] = _active_return_series(replay)
        trial_sharpes.append(float(replay.metrics.get("information_ratio") or 0.0))
        evidence[str(control)] = {
            "status": "available",
            **_bounded_replay(dict(replay.metrics)),
        }
    aligned = pd.concat(trial_series, axis=1, join="inner").dropna()
    valid_trials = int(aligned.shape[1])
    pbo = probability_of_backtest_overfit(aligned) if valid_trials >= 4 else 1.0
    dsr = deflated_sharpe_probability(
        observed_sharpe=float(candidate_replay.metrics.get("information_ratio") or 0.0),
        trial_sharpes=trial_sharpes,
        observations=max(int(len(aligned)), 2),
        periods_per_year=252.0,
    )
    return {
        "deflated_sharpe_probability": float(dsr),
        "probability_of_backtest_overfit": float(pbo),
        "valid_trial_count": valid_trials,
        "trial_evidence_status": "available" if valid_trials >= 4 else "insufficient_evidence",
        "aligned_return_periods": int(len(aligned)),
    }, evidence


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _bounded_research_config(config: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "development",
        "training",
        "model",
        "fallback",
        "score_construction",
        "calibration",
        "portfolio",
        "controls",
        "gates",
    )
    return {
        key: json.loads(json.dumps(config.get(key), ensure_ascii=False))
        for key in keys
        if key in config
    }


def _report_markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    gate = result["development_gate"]

    def percent(value: Any) -> str:
        return f"{float(value or 0.0):+.2%}"

    lines = [
        "# 状态感知表格模型开发实测",
        "",
        f"- 市场/账户：`{result['market']}` / `{result['account_scope']}`",
        f"- 数据快照：`{result['as_of']}`",
        f"- 模型：`{result['estimator']}` `{result['estimator_version']}`",
        f"- 特征：{result['selected_feature_count']} 个",
        "- 正式下单：否",
        "",
        "| RankIC | ICIR | 年化净超额 | 主动回撤 | 总回撤 | 年换手 | 资金利用率 | DSR | PBO |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {float(metrics.get('rank_ic') or 0):+.4f} | "
            f"{float(metrics.get('icir') or 0):+.3f} | "
            f"{percent(metrics.get('net_excess_return'))} | "
            f"{percent(metrics.get('active_max_drawdown'))} | "
            f"{percent(metrics.get('max_drawdown'))} | "
            f"{float(metrics.get('annual_turnover') or 0):.2f}x | "
            f"{percent(metrics.get('capital_utilization'))} | "
            f"{float(metrics.get('deflated_sharpe_probability') or 0):.3f} | "
            f"{float(metrics.get('probability_of_backtest_overfit') or 1):.3f} |"
        ),
        "",
    ]
    if len(result.get("attempts") or ()) > 1:
        lines.extend([
            "## 顺序尝试",
            "",
            "| 次序 | 训练目标 | RankIC | ICIR | 头部排序 | 结论 |",
            "| ---: | --- | ---: | ---: | --- | --- |",
        ])
        for index, attempt in enumerate(result["attempts"], start=1):
            attempt_metrics = attempt["metrics"]
            attempt_gate = attempt["development_gate"]
            lines.append(
                f"| {index} | `{attempt['estimator']}` | "
                f"{float(attempt_metrics.get('rank_ic') or 0):+.4f} | "
                f"{float(attempt_metrics.get('icir') or 0):+.3f} | "
                f"{'通过' if attempt_gate['checks'].get('top_tail') else '未通过'} | "
                f"{'通过' if attempt_gate['passed'] else 'Research'} |"
            )
        lines.append("")
    lines.extend([
        "## 分阶段",
        "",
        "| 折 | 验证区间 | RankIC | ICIR | 年化净超额 | 主动回撤 |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ])
    for fold in result["folds"]:
        lines.append(
            f"| {int(fold['fold']) + 1} | {fold['validation_start']} 至 {fold['validation_end']} | "
            f"{float(fold.get('rank_ic') or 0):+.4f} | "
            f"{float(fold.get('icir') or 0):+.3f} | "
            f"{percent(fold.get('net_excess_return'))} | "
            f"{percent(fold.get('active_max_drawdown'))} |"
        )
    lines.extend([
        "",
        f"## 结论：{result['status']}",
        "",
        "通过开发门槛，可冻结为未来日期 Shadow 候选。"
        if gate["passed"]
        else "开发门槛未通过，保持 Research，不修改正式策略。",
        "",
        "未通过项：" + (", ".join(gate["reasons"]) if gate["reasons"] else "无"),
        "",
    ])
    return "\n".join(lines)


def _evaluate_fitted_candidate(
    fitted: TabularFitResult,
    *,
    config: dict[str, Any],
    effective_contract: dict[str, Any],
) -> dict[str, Any]:
    replay_mode = str(
        (config.get("portfolio") or {}).get("replay_contract") or "rule"
    )
    replay_function = (
        replay_model_portfolio if replay_mode == "model" else replay_rule_portfolio
    )
    replay = replay_function(
        fitted.evaluation,
        contract=effective_contract,
    )
    calibration_diagnostics = _economic_calibration_diagnostics(fitted, replay)
    buckets = _score_buckets(fitted.evaluation)
    fold_evidence: list[dict[str, Any]] = []
    split_by_fold = {int(item["fold"]): item for item in fitted.folds}
    for fold, group in fitted.evaluation.groupby("fold", sort=True):
        rank_ic, icir = _rank_metrics(group, "score")
        fold_replay = replay_function(group, contract=effective_contract)
        fold_evidence.append({
            **split_by_fold[int(fold)],
            "rank_ic": rank_ic,
            "icir": icir,
            **_bounded_replay(dict(fold_replay.metrics)),
        })
    governance, controls = _control_governance(
        fitted.evaluation,
        candidate_replay=replay,
        controls=config.get("controls") or (),
        portfolio_contract=effective_contract,
    )
    metrics = {
        "rank_ic": fitted.neutralized_rank_ic,
        "icir": fitted.neutralized_icir,
        "raw_rank_ic": fitted.raw_rank_ic,
        "raw_icir": fitted.raw_icir,
        "point_in_time_audit": fitted.point_in_time_audit,
        **_bounded_replay(dict(replay.metrics)),
        **governance,
    }
    gate = evaluate_tabular_development_gate(
        metrics,
        folds=fold_evidence,
        buckets=buckets,
        thresholds=config["gates"],
    )
    return {
        "fitted": fitted,
        "metrics": metrics,
        "folds": fold_evidence,
        "calibrations": list(fitted.calibrations),
        "score_buckets": buckets,
        "fixed_controls": controls,
        "calibration_diagnostics": calibration_diagnostics,
        "development_gate": gate,
    }


def _attempt_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    fitted = evidence["fitted"]
    return {
        "estimator": fitted.estimator,
        "estimator_version": fitted.estimator_version,
        "metrics": evidence["metrics"],
        "folds": evidence["folds"],
        "score_buckets": evidence["score_buckets"],
        "calibrations": list(fitted.calibrations),
        "calibration_diagnostics": evidence["calibration_diagnostics"],
        "development_gate": evidence["development_gate"],
    }


def evaluate_regime_tabular_candidate(
    repo_root: str | Path,
    *,
    dataset: pd.DataFrame,
    feature_columns: Iterable[str],
    config: dict[str, Any],
    portfolio_contract: dict[str, Any],
    as_of: str,
) -> dict[str, Any]:
    """Evaluate one frozen hypothesis without mutating a model registry."""

    fitted = fit_walk_forward_tabular_ranker(
        dataset,
        feature_columns=feature_columns,
        config=config,
    )
    portfolio_config = config.get("portfolio") or {}
    effective_contract = {
        **portfolio_contract,
        "rebalance_frequency": str(
            portfolio_config.get("rebalance_frequency") or "monthly"
        ),
        "allocation_policy": dict(portfolio_config.get("allocation_policy") or {}),
    }
    primary = _evaluate_fitted_candidate(
        fitted,
        config=config,
        effective_contract=effective_contract,
    )
    attempt_evidence = [primary]
    selected = primary
    if _should_run_lambdarank_fallback(
        fitted,
        primary["development_gate"],
        config,
    ):
        fallback_fitted = fit_walk_forward_tabular_ranker(
            dataset,
            feature_columns=feature_columns,
            config=config,
            estimator="lightgbm_lambdarank",
        )
        fallback = _evaluate_fitted_candidate(
            fallback_fitted,
            config=config,
            effective_contract=effective_contract,
        )
        attempt_evidence.append(fallback)
        selected = _select_candidate_evidence(primary, fallback)
    fitted = selected["fitted"]
    attempts = [
        {
            **_attempt_payload(evidence),
            "selected": evidence is selected,
        }
        for evidence in attempt_evidence
    ]
    metrics = selected["metrics"]
    fold_evidence = selected["folds"]
    buckets = selected["score_buckets"]
    controls = selected["fixed_controls"]
    gate = selected["development_gate"]
    status = "development_pass" if gate["passed"] else "research"
    safe_as_of = str(as_of).replace("-", "")[:8]
    scope = str(config["account_scope"])
    report_root = Path(repo_root) / "reports" / "research"
    report_root.mkdir(parents=True, exist_ok=True)
    json_path = report_root / f"regime_tabular_alpha_{safe_as_of}_{scope}.json"
    report_path = report_root / f"regime_tabular_alpha_{safe_as_of}_{scope}.md"
    config_hash = _config_hash(config)
    immutable_json_path = report_root / (
        f"regime_tabular_alpha_{safe_as_of}_{scope}_{config_hash}.json"
    )
    immutable_report_path = report_root / (
        f"regime_tabular_alpha_{safe_as_of}_{scope}_{config_hash}.md"
    )
    best_json_path = report_root / f"regime_tabular_alpha_{safe_as_of}_{scope}_best.json"
    best_report_path = report_root / f"regime_tabular_alpha_{safe_as_of}_{scope}_best.md"
    existing_best: dict[str, Any] | None = None
    try:
        loaded_best = json.loads(best_json_path.read_text(encoding="utf-8"))
        if isinstance(loaded_best, dict):
            existing_best = loaded_best
    except (OSError, json.JSONDecodeError):
        pass
    best_candidate_updated = bool(
        existing_best is None
        or _candidate_selection_key(selected) > _candidate_selection_key(existing_best)
    )
    result = _json_safe({
        "schema_version": 1,
        "protocol_version": fitted.protocol_version,
        "status": status,
        "market": str(config["market"]),
        "account_scope": scope,
        "horizon": int(config["horizon"]),
        "as_of": safe_as_of,
        "development": config["development"],
        "config_hash": config_hash,
        "research_config": _bounded_research_config(config),
        "estimator": fitted.estimator,
        "target": str((config.get("model") or {}).get("target")),
        "estimator_version": fitted.estimator_version,
        "selected_feature_count": len(fitted.feature_columns),
        "selected_features": list(fitted.feature_columns),
        "feature_importance_top20": dict(
            sorted(
                fitted.feature_importance.items(),
                key=lambda item: (-item[1], item[0]),
            )[:20]
        ),
        "oos_predictions": int(len(fitted.evaluation)),
        "oos_start": str(fitted.evaluation["trade_date"].min()),
        "oos_end": str(fitted.evaluation["trade_date"].max()),
        "metrics": metrics,
        "folds": fold_evidence,
        "calibrations": list(fitted.calibrations),
        "calibration_diagnostics": selected["calibration_diagnostics"],
        "score_buckets": buckets,
        "fixed_controls": controls,
        "development_gate": gate,
        "attempts": attempts,
        "fallback_executed": len(attempts) > 1,
        "selection_policy": "gate-count-then-net-excess-v1",
        "formal_order_source": False,
        "registry_mutated": False,
        "report_path": str(report_path),
        "json_path": str(json_path),
        "immutable_report_path": str(immutable_report_path),
        "immutable_json_path": str(immutable_json_path),
        "best_report_path": str(best_report_path),
        "best_json_path": str(best_json_path),
        "best_candidate_updated": best_candidate_updated,
    })
    json_payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    report_payload = _report_markdown(result)
    write_text_atomic(
        json_path,
        json_payload,
        encoding="utf-8",
    )
    write_text_atomic(report_path, report_payload, encoding="utf-8")
    write_text_atomic(immutable_json_path, json_payload, encoding="utf-8")
    write_text_atomic(immutable_report_path, report_payload, encoding="utf-8")
    if best_candidate_updated:
        write_text_atomic(best_json_path, json_payload, encoding="utf-8")
        write_text_atomic(best_report_path, report_payload, encoding="utf-8")
    return result


__all__ = [
    "RollingPurgedSplit",
    "TABULAR_PROTOCOL_VERSION",
    "TabularFitResult",
    "evaluate_tabular_development_gate",
    "evaluate_regime_tabular_candidate",
    "_model_target_values",
    "_select_candidate_evidence",
    "fit_walk_forward_tabular_ranker",
    "load_tabular_ranker_config",
    "make_rolling_purged_splits",
    "recency_date_balanced_weights",
]
