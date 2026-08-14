"""Purged walk-forward models, probability calibration, and artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from ..utils import write_text_atomic
from .account_features import date_balanced_sample_weights
from .classical_specs import ClassicalModelSpec
from .edge_calibration import EdgeCalibrator, fit_edge_calibrator
from .feature_registry import DEFAULT_REGISTRY, DEFAULT_REGISTRY_HASH
from .labels import LABEL_CONTRACT_VERSION
from .portfolio_replay import (
    SIMULATOR_VERSION,
    replay_fixed_top_n_diagnostic_portfolio,
    replay_model_portfolio,
    replay_rule_portfolio,
)
from .trial_ledger import DEFAULT_CLASSICAL_TRIAL_SPECS


CLASS_ORDER = ("down", "flat", "up")
TRAINING_PROTOCOL_VERSION = "purged_walk_forward_v8_baseline_first"


@dataclass(frozen=True)
class WalkForwardSplit:
    train_indices: np.ndarray
    validation_indices: np.ndarray


class MultiClassCalibrator:
    def __init__(self, method: str, classes: tuple[str, ...]) -> None:
        self.method = method
        self.classes = classes
        self.models: list[Any | None] = []
        self.temperature = 1.0

    def fit(self, probabilities: np.ndarray, labels: np.ndarray) -> "MultiClassCalibrator":
        self.models = []
        if self.method == "identity":
            return self
        if self.method == "temperature":
            clipped = np.clip(probabilities, 1e-12, 1.0)
            label_index = {class_name: index for index, class_name in enumerate(self.classes)}
            targets = np.asarray([label_index[str(label)] for label in labels], dtype=int)
            candidates = np.geomspace(0.25, 4.0, 161)
            losses: list[tuple[float, float, float]] = []
            for temperature in candidates:
                calibrated = self._temperature_scale(clipped, float(temperature))
                loss = -float(np.mean(np.log(np.clip(calibrated[np.arange(len(targets)), targets], 1e-12, 1.0))))
                losses.append((loss, abs(float(temperature) - 1.0), float(temperature)))
            self.temperature = min(losses)[2]
            return self
        for index, class_name in enumerate(self.classes):
            target = (labels == class_name).astype(int)
            if target.min() == target.max():
                self.models.append(None)
                continue
            if self.method == "isotonic":
                model = IsotonicRegression(out_of_bounds="clip")
                model.fit(probabilities[:, index], target)
            else:
                clipped = np.clip(probabilities[:, index], 1e-6, 1.0 - 1e-6)
                logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
                model = LogisticRegression(C=1.0, max_iter=300, random_state=0)
                model.fit(logits, target)
            self.models.append(model)
        return self

    @staticmethod
    def _normalize(probabilities: np.ndarray) -> np.ndarray:
        normalized = np.clip(np.asarray(probabilities, dtype=float), 0.0, None)
        totals = normalized.sum(axis=1, keepdims=True)
        invalid = totals[:, 0] <= 0
        if invalid.any():
            normalized[invalid] = 1.0 / normalized.shape[1]
            totals = normalized.sum(axis=1, keepdims=True)
        return normalized / totals

    @classmethod
    def _temperature_scale(cls, probabilities: np.ndarray, temperature: float) -> np.ndarray:
        logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / max(float(temperature), 1e-6)
        logits -= logits.max(axis=1, keepdims=True)
        return cls._normalize(np.exp(logits))

    def predict(self, probabilities: np.ndarray) -> np.ndarray:
        if self.method == "identity":
            return self._normalize(probabilities)
        if self.method == "temperature":
            return self._temperature_scale(probabilities, self.temperature)
        calibrated = np.zeros_like(probabilities, dtype=float)
        for index, model in enumerate(self.models):
            if model is None:
                calibrated[:, index] = probabilities[:, index]
            elif self.method == "isotonic":
                calibrated[:, index] = model.predict(probabilities[:, index])
            else:
                clipped = np.clip(probabilities[:, index], 1e-6, 1.0 - 1e-6)
                logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
                calibrated[:, index] = model.predict_proba(logits)[:, 1]
        totals = calibrated.sum(axis=1, keepdims=True)
        invalid = totals[:, 0] <= 0
        calibrated[invalid] = probabilities[invalid]
        return self._normalize(calibrated)


@dataclass
class ModelBundle:
    horizon: int
    feature_columns: tuple[str, ...]
    class_order: tuple[str, ...]
    imputation_values: dict[str, float]
    scaler: StandardScaler
    logistic_model: LogisticRegression
    logistic_calibrator: MultiClassCalibrator
    boosting_model: HistGradientBoostingClassifier
    boosting_calibrator: MultiClassCalibrator
    use_boosting: bool
    metrics: dict[str, Any]
    split_dates: dict[str, str]
    sample_support: int
    calibration_method: str
    model_version: str
    return_stats: dict[str, dict[str, float]]
    ensemble_logistic_weight: float = 0.5
    clip_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    feature_reference: dict[str, dict[str, Any]] = field(default_factory=dict)
    linear_ranking_model: Any | None = None
    boosting_ranking_models: tuple[HistGradientBoostingRegressor, ...] = ()
    ranking_ensemble_linear_weight: float = 0.5
    ranking_prediction_bounds: tuple[float, float] = (-1.0, 1.0)
    account_scope: str = ""
    edge_calibrator: EdgeCalibrator | None = None
    ranking_target: str = "raw_excess_return"
    ranking_residual_weight: float = 1.0

    def _matrix(self, frame: pd.DataFrame) -> np.ndarray:
        numeric = _clip_numeric(
            frame.loc[:, self.feature_columns].apply(pd.to_numeric, errors="coerce"),
            getattr(self, "clip_bounds", {}),
        )
        return numeric.fillna(self.imputation_values).to_numpy(dtype=float)

    def component_probabilities(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        matrix = self._matrix(frame)
        logistic_raw = self.logistic_model.predict_proba(self.scaler.transform(matrix))
        boosting_raw = self.boosting_model.predict_proba(matrix)
        logistic = self.logistic_calibrator.predict(logistic_raw)
        boosting = self.boosting_calibrator.predict(boosting_raw)
        return logistic, boosting

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        logistic, boosting = self.component_probabilities(frame)
        weight = float(getattr(self, "ensemble_logistic_weight", 0.5))
        if not self.use_boosting:
            weight = 1.0
        return weight * logistic + (1.0 - weight) * boosting

    def component_excess_predictions(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        matrix = self._matrix(frame)
        linear_model = getattr(self, "linear_ranking_model", None)
        boosting_models = tuple(getattr(self, "boosting_ranking_models", ()) or ())
        if linear_model is None or not boosting_models:
            probabilities = self.predict_proba(frame)
            fallback = np.asarray([
                sum(
                    float(probabilities[row_index, class_index])
                    * float(self.return_stats[class_name]["mean"])
                    for class_index, class_name in enumerate(self.class_order)
                )
                for row_index in range(len(frame))
            ])
            return fallback, fallback
        linear = np.asarray(linear_model.predict(self.scaler.transform(matrix)), dtype=float)
        boosting = np.mean(
            np.column_stack([model.predict(matrix) for model in boosting_models]),
            axis=1,
        )
        ranking_target = str(
            getattr(self, "ranking_target", "raw_excess_return")
        )
        residual_weight = float(
            getattr(self, "ranking_residual_weight", 1.0)
        )
        linear = _apply_ranking_anchor(
            linear,
            frame,
            ranking_target,
            residual_weight=residual_weight,
        )
        boosting = _apply_ranking_anchor(
            boosting,
            frame,
            ranking_target,
            residual_weight=residual_weight,
        )
        return linear, np.asarray(boosting, dtype=float)

    def _raw_excess_return(self, frame: pd.DataFrame) -> np.ndarray:
        linear, boosting = self.component_excess_predictions(frame)
        weight = float(getattr(self, "ranking_ensemble_linear_weight", 0.5))
        predicted = weight * linear + (1.0 - weight) * boosting
        lower, upper = getattr(self, "ranking_prediction_bounds", (-1.0, 1.0))
        return np.clip(np.asarray(predicted, dtype=float), float(lower), float(upper))

    def predict_excess_return(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self._raw_excess_return(frame)
        calibrator = getattr(self, "edge_calibrator", None)
        if calibrator is None:
            return np.zeros(len(raw), dtype=float)
        expected, _ = calibrator.predict_distribution(raw)
        return expected

    def predict_ranking_score(self, frame: pd.DataFrame) -> np.ndarray:
        """Return the raw cross-sectional score, independent of trade calibration."""

        return self._raw_excess_return(frame)

    def predict_excess_uncertainty(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self._raw_excess_return(frame)
        calibrator = getattr(self, "edge_calibrator", None)
        if calibrator is not None:
            _, uncertainty = calibrator.predict_distribution(raw)
            return uncertainty
        return np.ones(len(raw), dtype=float)

    def out_of_distribution_ratios(self, frame: pd.DataFrame) -> np.ndarray:
        bounds = getattr(self, "clip_bounds", {})
        if not bounds:
            return np.zeros(len(frame), dtype=float)
        numeric = frame.loc[:, self.feature_columns].apply(pd.to_numeric, errors="coerce")
        outside = pd.DataFrame(False, index=numeric.index, columns=numeric.columns)
        for column in self.feature_columns:
            if column not in bounds:
                continue
            lower, upper = bounds[column]
            values = numeric[column]
            outside[column] = values.notna() & ((values < lower) | (values > upper))
        support = numeric.notna().sum(axis=1).replace(0, np.nan)
        return (outside.sum(axis=1) / support).fillna(1.0).to_numpy(dtype=float)

    def feature_drift(self, frame: pd.DataFrame) -> dict[str, Any]:
        reference = getattr(self, "feature_reference", {})
        values: dict[str, float] = {}
        for column in self.feature_columns:
            spec = reference.get(column) or {}
            expected = np.asarray(spec.get("probabilities") or [], dtype=float)
            interior = np.asarray(spec.get("interior_edges") or [], dtype=float)
            latest = pd.to_numeric(frame.get(column), errors="coerce").dropna().to_numpy(dtype=float)
            if not len(expected) or not len(latest):
                continue
            observed, _ = np.histogram(latest, bins=np.concatenate(([-np.inf], interior, [np.inf])))
            observed = observed.astype(float) / max(float(observed.sum()), 1.0)
            if len(observed) != len(expected):
                continue
            expected_safe = np.clip(expected, 1e-6, 1.0)
            observed_safe = np.clip(observed, 1e-6, 1.0)
            values[column] = float(np.sum((observed_safe - expected_safe) * np.log(observed_safe / expected_safe)))
        scores = list(values.values())
        return {
            "mean_psi": float(np.mean(scores)) if scores else 0.0,
            "max_psi": float(np.max(scores)) if scores else 0.0,
            "feature_count": len(scores),
            "features": values,
        }

    def logistic_contributions(self, row: pd.Series, class_name: str = "up") -> list[tuple[str, float]]:
        matrix = self._matrix(pd.DataFrame([row]))
        standardized = self.scaler.transform(matrix)[0]
        class_index = list(self.logistic_model.classes_).index(class_name)
        contributions = self.logistic_model.coef_[class_index] * standardized
        return sorted(zip(self.feature_columns, contributions.astype(float)), key=lambda item: abs(item[1]), reverse=True)


def make_purged_walk_forward_splits(
    data: pd.DataFrame,
    *,
    n_splits: int = 3,
    embargo: int = 20,
) -> list[WalkForwardSplit]:
    required = {"trade_date", "label_end_date"}
    if required.difference(data.columns):
        raise ValueError("walk_forward_missing_dates")
    normalized_dates = data["trade_date"].astype(str)
    unique_dates = np.asarray(sorted(normalized_dates.unique()))
    validation_size = max(1, len(unique_dates) // (n_splits + 2))
    first_validation = len(unique_dates) - validation_size * n_splits
    splits: list[WalkForwardSplit] = []
    for split_number in range(n_splits):
        start = first_validation + split_number * validation_size
        stop = len(unique_dates) if split_number == n_splits - 1 else start + validation_size
        validation_dates = unique_dates[start:stop]
        train_dates = unique_dates[: max(0, start - max(0, embargo))]
        validation = data.loc[normalized_dates.isin(validation_dates)]
        train = data.loc[normalized_dates.isin(train_dates)]
        if not validation.empty:
            train = train.loc[
                train["label_end_date"].astype(str) < str(validation["trade_date"].astype(str).min())
            ]
        if train.empty or validation.empty:
            continue
        splits.append(WalkForwardSplit(train.index.to_numpy(), validation.index.to_numpy()))
    return splits


def _bounded_cross_section_sample(
    frame: pd.DataFrame,
    *,
    max_rows: int,
    random_state: int,
) -> pd.DataFrame:
    """Deterministically cap fit rows while retaining every trade date.

    Selection rotates across codes by hashing ``date + code + seed``. This
    avoids the old failure mode where a fixed code-prefix sample looked large
    in row count but represented only a narrow slice of the investable set.
    Validation rows are never sampled; this helper is only used for estimator
    fitting and calibration.
    """

    sort_columns = ["trade_date"] + (["code"] if "code" in frame.columns else [])
    if len(frame) <= max_rows:
        return frame.sort_values(sort_columns, kind="stable").reset_index(drop=True)
    dates = sorted(frame["trade_date"].astype(str).unique())
    if max_rows < len(dates):
        raise ValueError("model_training_budget_below_date_count")
    base_quota, remainder = divmod(max_rows, len(dates))
    parts: list[pd.DataFrame] = []
    normalized_dates = frame["trade_date"].astype(str)
    for index, trade_date in enumerate(dates):
        group = frame.loc[normalized_dates.eq(trade_date)].copy()
        quota = base_quota + (1 if index < remainder else 0)
        if len(group) > quota:
            codes = group.get("code", pd.Series(group.index.astype(str), index=group.index)).astype(str)
            keys = trade_date + "|" + codes + f"|{random_state}"
            group["_sample_hash"] = pd.util.hash_pandas_object(keys, index=False).to_numpy()
            group = group.nsmallest(quota, "_sample_hash").drop(columns="_sample_hash")
        parts.append(group)
    return pd.concat(parts, ignore_index=True).sort_values(
        sort_columns, kind="stable"
    ).reset_index(drop=True)


def _select_features(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    max_features: int = 20,
    min_coverage: float = 0.55,
    correlation_limit: float = 0.92,
    min_abs_ic: float = 0.02,
    min_stability: float = 0.67,
    min_ic_t_stat: float = 2.0,
    max_per_family: int = 6,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Select non-redundant predictors using training rows only.

    Scores are cross-sectional rank correlations with forward excess return.
    For sparse fixtures without a real cross-section, the calculation falls
    back to a global Spearman correlation. No validation rows enter this step.
    """

    if "excess_return" not in frame.columns:
        raise ValueError("feature_selection_missing_target")
    candidates = [
        column
        for column in columns
        if column in frame.columns
        and pd.to_numeric(frame[column], errors="coerce").notna().mean() >= min_coverage
        and pd.to_numeric(frame[column], errors="coerce").nunique(dropna=True) > 1
    ]
    if not candidates:
        raise ValueError("feature_selection_empty")
    numeric = frame.loc[:, candidates].apply(pd.to_numeric, errors="coerce")
    target = pd.to_numeric(frame["excess_return"], errors="coerce")
    dates = frame["trade_date"].astype(str)
    cross_section_columns = _cross_section_columns(frame)
    cross_section_groupers = [
        frame[column].astype(str) for column in cross_section_columns
    ]
    feature_ranks = numeric.groupby(
        cross_section_groupers,
        sort=False,
    ).rank(pct=True, method="average")
    target_ranks = target.groupby(
        cross_section_groupers,
        sort=False,
    ).rank(pct=True, method="average")
    feature_centered = feature_ranks - feature_ranks.groupby(
        cross_section_groupers,
        sort=False,
    ).transform("mean")
    target_centered = target_ranks - target_ranks.groupby(
        cross_section_groupers,
        sort=False,
    ).transform("mean")
    numerator = feature_centered.mul(target_centered, axis=0).sum(skipna=True)
    denominator = np.sqrt(
        feature_centered.pow(2).sum(skipna=True)
        * float(target_centered.pow(2).sum(skipna=True))
    )
    correlations = numerator.div(denominator.replace(0.0, np.nan))
    global_correlations = numeric.corrwith(target, method="spearman")
    correlations = correlations.fillna(global_correlations).fillna(0.0)

    unique_dates = np.asarray(sorted(dates.unique()))
    windows = [part for part in np.array_split(unique_dates, min(3, len(unique_dates))) if len(part)]
    cross_section_indices = frame.groupby(
        cross_section_columns,
        sort=False,
    ).indices
    stability: dict[str, float] = {}
    ic_t_stats: dict[str, float] = {}
    for column in candidates:
        overall_sign = np.sign(float(correlations[column]))
        daily_observations: list[tuple[str, float]] = []
        for key, indices in cross_section_indices.items():
            value = _spearman(
                numeric.iloc[np.asarray(indices, dtype=int)][column],
                target.iloc[np.asarray(indices, dtype=int)],
            )
            if value is None:
                continue
            trade_date = str(key[0] if isinstance(key, tuple) else key)
            daily_observations.append((trade_date, value))
        window_signs: list[float] = []
        for window in windows:
            values = [
                value
                for trade_date, value in daily_observations
                if trade_date in set(window)
            ]
            value = float(np.mean(values)) if values else None
            if value is None and not daily_observations:
                mask = dates.isin(window)
                value = _spearman(numeric.loc[mask, column], target.loc[mask])
            if value is not None and abs(value) > 1e-12:
                window_signs.append(float(np.sign(value) == overall_sign))
        stability[column] = float(np.mean(window_signs)) if window_signs else 0.0
        daily_values = [value for _, value in daily_observations]
        daily_std = float(np.std(daily_values, ddof=1)) if len(daily_values) > 1 else 0.0
        if len(daily_values) >= 3 and daily_std > 1e-12:
            ic_t_stats[column] = float(
                np.mean(daily_values) / daily_std * np.sqrt(len(daily_values))
            )
        elif len(daily_values) >= 1 and abs(float(np.mean(daily_values))) > 1e-12:
            ic_t_stats[column] = float("inf")
        else:
            aligned = pd.concat([numeric[column], target], axis=1).dropna()
            correlation = float(correlations[column])
            denominator = max(1.0 - correlation * correlation, 1e-12)
            ic_t_stats[column] = (
                float(correlation * np.sqrt(max(len(aligned) - 2, 0) / denominator))
                if len(aligned) >= 3
                else 0.0
            )
    scores = {
        column: abs(float(correlations[column])) * (0.5 + 0.5 * stability[column])
        for column in candidates
    }
    eligible = [
        column for column in candidates
        if abs(float(correlations[column])) >= min_abs_ic
        and stability[column] >= min_stability
        and abs(ic_t_stats[column]) >= min_ic_t_stat
    ]
    rejected_weak = sorted(set(candidates).difference(eligible))
    if not eligible:
        raise ValueError("feature_selection_no_stable_signal")
    ordered = sorted(
        eligible,
        key=lambda column: (
            -scores[column],
            -float(numeric[column].notna().mean()),
            column,
        ),
    )
    feature_correlations = feature_ranks.corr(method="pearson")
    families = {item.name: item.family for item in DEFAULT_REGISTRY}
    family_counts: dict[str, int] = {}
    selected: list[str] = []
    for column in ordered:
        if any(
            abs(float(feature_correlations.loc[column, existing])) >= correlation_limit
            for existing in selected
            if pd.notna(feature_correlations.loc[column, existing])
        ):
            continue
        family = families.get(column, "other")
        if family_counts.get(family, 0) >= max(1, int(max_per_family)):
            continue
        selected.append(column)
        family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= max(1, int(max_features)):
            break
    if not selected:
        selected = [ordered[0]]
    diagnostics: dict[str, Any] = {
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "selected_features": list(selected),
        "scores": {column: float(scores[column]) for column in selected},
        "correlations": {column: float(correlations[column]) for column in candidates},
        "stability": {column: float(stability[column]) for column in candidates},
        "ic_t_stats": {column: float(ic_t_stats[column]) for column in candidates},
        "rejected_weak_features": rejected_weak,
        "family_counts": family_counts,
    }
    return tuple(selected), diagnostics


def _fit_clip_bounds(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    bounds: dict[str, tuple[float, float]] = {}
    for column in columns:
        values = numeric[column].dropna()
        if values.empty:
            continue
        bounds[column] = (float(values.quantile(0.01)), float(values.quantile(0.99)))
    return bounds


def _clip_numeric(
    numeric: pd.DataFrame,
    bounds: dict[str, tuple[float, float]] | None,
) -> pd.DataFrame:
    if not bounds:
        return numeric
    clipped = numeric.copy()
    for column, (lower, upper) in bounds.items():
        if column in clipped.columns:
            clipped[column] = clipped[column].clip(lower=lower, upper=upper)
    return clipped


def _build_feature_reference(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    reference: dict[str, dict[str, Any]] = {}
    for column in columns:
        values = numeric[column].dropna().to_numpy(dtype=float)
        if not len(values):
            continue
        interior = np.unique(np.quantile(values, np.linspace(0.1, 0.9, 9)))
        counts, _ = np.histogram(values, bins=np.concatenate(([-np.inf], interior, [np.inf])))
        probabilities = counts.astype(float) / max(float(counts.sum()), 1.0)
        reference[column] = {
            "interior_edges": interior.astype(float).tolist(),
            "probabilities": probabilities.astype(float).tolist(),
        }
    return reference


def _impute(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    values: dict[str, float] | None = None,
    clip_bounds: dict[str, tuple[float, float]] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    numeric = _clip_numeric(numeric, clip_bounds)
    if values is None:
        medians = numeric.median().fillna(0.0)
        values = {column: float(medians[column]) for column in columns}
    return numeric.fillna(values).to_numpy(dtype=float), values


def _multiclass_brier(probabilities: np.ndarray, labels: np.ndarray, classes: tuple[str, ...]) -> float:
    expected = np.column_stack([(labels == class_name).astype(float) for class_name in classes])
    return float(np.mean(np.sum((probabilities - expected) ** 2, axis=1)))


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    aligned = pd.concat(
        [pd.to_numeric(left, errors="coerce"), pd.to_numeric(right, errors="coerce")],
        axis=1,
    ).dropna()
    if len(aligned) < 3 or aligned.iloc[:, 0].nunique() < 2 or aligned.iloc[:, 1].nunique() < 2:
        return None
    value = aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman")
    return float(value) if pd.notna(value) else None


def _cross_section_columns(frame: pd.DataFrame) -> list[str]:
    columns = ["trade_date"]
    if "account_id" in frame.columns:
        columns.append("account_id")
    elif "research_scope" in frame.columns:
        columns.append("research_scope")
    return columns


def _ranking_target_values(
    frame: pd.DataFrame,
    contract: str,
) -> np.ndarray:
    """Build the estimator target without changing raw economic outcomes."""

    target = pd.to_numeric(frame["excess_return"], errors="coerce")
    normalized = str(contract or "raw_excess_return").strip().lower()
    if normalized == "raw_excess_return":
        return target.fillna(0.0).to_numpy(dtype=float)
    if normalized not in {
        "daily_cross_sectional_percentile_v1",
        "momentum_anchor_residual_v1",
        "momentum_lowvol_anchor_residual_v1",
        "qdii_trend_anchor_residual_v1",
    }:
        raise ValueError(f"ranking_target_unknown:{normalized}")
    groupers = [frame[column].astype(str) for column in _cross_section_columns(frame)]
    ranked = target.groupby(groupers, sort=False).rank(
        pct=True,
        method="average",
    )
    cross_sectional_target = (ranked - 0.5).fillna(0.0).to_numpy(dtype=float)
    if normalized == "momentum_anchor_residual_v1":
        return cross_sectional_target - _momentum_anchor_values(frame)
    if normalized == "momentum_lowvol_anchor_residual_v1":
        return cross_sectional_target - _balanced_anchor_values(frame)
    if normalized == "qdii_trend_anchor_residual_v1":
        return cross_sectional_target - _qdii_trend_anchor_values(frame)
    return cross_sectional_target


def _momentum_anchor_values(frame: pd.DataFrame) -> np.ndarray:
    groupers = [frame[column].astype(str) for column in _cross_section_columns(frame)]
    components: list[pd.Series] = []
    for column in ("momentum_20", "momentum_60"):
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if not values.notna().any():
            continue
        ranked = values.groupby(groupers, sort=False).rank(
            pct=True,
            method="average",
        )
        components.append(ranked - 0.5)
    if not components:
        raise ValueError("ranking_anchor_momentum_missing")
    return (
        pd.concat(components, axis=1)
        .mean(axis=1, skipna=True)
        .fillna(0.0)
        .to_numpy(dtype=float)
    )


def _balanced_anchor_values(frame: pd.DataFrame) -> np.ndarray:
    """Blend momentum and low-volatility ranks without a fitted regime switch."""

    groupers = [frame[column].astype(str) for column in _cross_section_columns(frame)]
    momentum = _momentum_anchor_values(frame)
    if "realized_volatility_20" not in frame.columns:
        raise ValueError("ranking_anchor_low_volatility_missing")
    volatility = pd.to_numeric(
        frame["realized_volatility_20"],
        errors="coerce",
    )
    volatility_rank = volatility.groupby(groupers, sort=False).rank(
        pct=True,
        method="average",
    )
    low_volatility = (0.5 - volatility_rank).fillna(0.0).to_numpy(dtype=float)
    return 0.5 * momentum + 0.5 * low_volatility


def _qdii_trend_anchor_values(frame: pd.DataFrame) -> np.ndarray:
    """Build a bounded absolute trend score with observable ETF frictions."""

    trend_scales = {
        "nav_momentum_20": 0.10,
        "account_residual_momentum_20": 0.10,
        "account_residual_momentum_60": 0.20,
        "sma_distance_20": 0.10,
    }
    trend_components: list[pd.Series] = []
    for column, scale in trend_scales.items():
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if not values.notna().any():
            continue
        trend_components.append(pd.Series(
            np.tanh(values.to_numpy(dtype=float) / scale),
            index=frame.index,
            dtype=float,
        ))
    if not trend_components:
        raise ValueError("ranking_anchor_qdii_trend_missing")
    trend = pd.concat(trend_components, axis=1).mean(axis=1, skipna=True).fillna(0.0)
    penalty = pd.Series(0.0, index=frame.index, dtype=float)
    for column, scale, weight in (
        ("natr_14", 0.10, 0.08),
        ("discount_premium", 0.05, 0.05),
        ("tracking_error_20", 0.10, 0.05),
    ):
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").abs().fillna(0.0)
        penalty += weight * np.tanh(values / scale)
    return (0.5 * trend - penalty).to_numpy(dtype=float)


def _apply_ranking_anchor(
    predictions: np.ndarray,
    frame: pd.DataFrame,
    contract: str,
    *,
    residual_weight: float = 1.0,
) -> np.ndarray:
    values = np.asarray(predictions, dtype=float)
    normalized = str(contract).strip().lower()
    if normalized == "momentum_anchor_residual_v1":
        anchor = _momentum_anchor_values(frame)
    elif normalized == "momentum_lowvol_anchor_residual_v1":
        anchor = _balanced_anchor_values(frame)
    elif normalized == "qdii_trend_anchor_residual_v1":
        anchor = _qdii_trend_anchor_values(frame)
    else:
        return values
    weight = float(residual_weight)
    if not 0.0 <= weight <= 1.0:
        raise ValueError("ranking_residual_weight_out_of_range")
    return anchor + weight * values


def _select_training_features(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    selection_policy: dict[str, Any],
    model_spec: ClassicalModelSpec | None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    mode = str(
        model_spec.feature_selection_mode
        if model_spec is not None
        else "stability_filter_v1"
    ).strip().lower()
    if mode == "stability_filter_v1":
        return _select_features(frame, columns, **selection_policy)
    if mode != "fixed_profile_v1":
        raise ValueError(f"feature_selection_mode_unknown:{mode}")
    minimum_coverage = float(selection_policy.get("min_coverage", 0.55))
    selected = tuple(
        column
        for column in columns
        if column in frame.columns
        and pd.to_numeric(frame[column], errors="coerce").notna().mean()
        >= minimum_coverage
        and pd.to_numeric(frame[column], errors="coerce").nunique(dropna=True) > 1
    )
    if not selected:
        raise ValueError("feature_selection_empty")
    return selected, {
        "mode": mode,
        "candidate_count": len(columns),
        "selected_count": len(selected),
        "selected_features": list(selected),
        "coverage": {
            column: float(
                pd.to_numeric(frame[column], errors="coerce").notna().mean()
            )
            for column in selected
        },
        "rejected_weak_features": [],
    }


def _portfolio_oos_metrics(
    evaluation: pd.DataFrame,
    *,
    horizon: int,
    round_trip_cost: float = 0.0015,
) -> dict[str, float | int]:
    """Evaluate a horizon portfolio on non-overlapping rebalance dates."""

    horizon = max(1, int(horizon))
    dates = sorted(evaluation["trade_date"].astype(str).unique())
    rebalance_dates = set(dates[::horizon])
    period_returns: list[float] = []
    period_return_dates: list[str] = []
    turnovers: list[float] = []
    previous: set[str] | None = None
    for trade_date, group in evaluation.groupby("trade_date", sort=True):
        if str(trade_date) not in rebalance_dates:
            continue
        ranked = group.sort_values(["score", "code"], ascending=[False, True], kind="stable")
        count = max(1, int(np.ceil(len(ranked) * 0.20)))
        buffer_count = max(count, int(np.ceil(len(ranked) * 0.30)))
        if previous:
            buffer_codes = set(ranked.head(buffer_count)["code"].astype(str))
            retained = previous.intersection(buffer_codes)
            additions = [
                code for code in ranked["code"].astype(str)
                if code not in retained
            ][: max(0, count - len(retained))]
            current = set([*retained, *additions])
            selected = ranked.loc[ranked["code"].astype(str).isin(current)]
        else:
            selected = ranked.head(count)
            current = set(selected["code"].astype(str))
        turnover = (
            0.0
            if previous is None
            else 1.0 - len(current.intersection(previous)) / max(len(current), len(previous), 1)
        )
        gross_excess = float(
            selected["excess_return"].mean() - group["excess_return"].mean()
        )
        period_returns.append(gross_excess - turnover * round_trip_cost)
        period_return_dates.append(str(trade_date))
        turnovers.append(turnover)
        previous = current
    if not period_returns:
        return {
            "net_excess_return": 0.0,
            "cumulative_relative_wealth": None,
            "annualized_excess_wealth": None,
            "excess_metric_contract": "arithmetic_active_annualized_v1",
            "max_drawdown": 1.0,
            "annual_turnover": 1_000_000_000.0,
            "portfolio_sharpe": 0.0,
            "portfolio_period_returns": [],
            "portfolio_period_return_dates": [],
            "portfolio_rebalance_periods": 0,
            "portfolio_horizon": horizon,
        }
    clipped = np.clip(np.asarray(period_returns, dtype=float), -0.99, None)
    periods_per_year = 252.0 / horizon
    net_excess_return = float(np.mean(clipped) * periods_per_year)
    active_curve = np.cumsum(clipped)
    drawdowns = active_curve - np.maximum.accumulate(active_curve)
    period_std = float(np.std(clipped, ddof=1)) if len(clipped) > 1 else 0.0
    portfolio_sharpe = (
        float(np.mean(clipped) / period_std * np.sqrt(periods_per_year))
        if period_std > 1e-12
        else 0.0
    )
    return {
        "net_excess_return": net_excess_return,
        "cumulative_relative_wealth": None,
        "annualized_excess_wealth": None,
        "excess_metric_contract": "arithmetic_active_annualized_v1",
        "max_drawdown": abs(float(np.min(drawdowns))),
        "annual_turnover": float(np.mean(turnovers) * periods_per_year),
        "portfolio_sharpe": portfolio_sharpe,
        "portfolio_period_returns": clipped.astype(float).tolist(),
        "portfolio_period_return_dates": period_return_dates,
        "portfolio_rebalance_periods": len(period_returns),
        "portfolio_horizon": horizon,
    }


def _reliability_curve(
    probabilities: np.ndarray,
    labels: np.ndarray,
    *,
    bins: int = 10,
) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    edges = np.linspace(0.0, 1.0, bins + 1)
    for class_index, class_name in enumerate(CLASS_ORDER):
        predicted = probabilities[:, class_index]
        observed = (labels == class_name).astype(float)
        assignments = np.minimum(np.digitize(predicted, edges[1:-1], right=False), bins - 1)
        for bin_index in range(bins):
            mask = assignments == bin_index
            if not mask.any():
                continue
            rows.append({
                "class": class_name,
                "bin": bin_index,
                "count": int(mask.sum()),
                "predicted": float(predicted[mask].mean()),
                "observed": float(observed[mask].mean()),
            })
    return rows


def _activation_metrics(
    *,
    baseline_probabilities: np.ndarray,
    validation: pd.DataFrame,
    validation_y: np.ndarray,
    ensemble: np.ndarray,
    logistic_probabilities: np.ndarray,
    boosting_probabilities: np.ndarray,
    ensemble_weights: np.ndarray,
    ranking_predictions: np.ndarray,
    expected_excess_predictions: np.ndarray,
    linear_ranking_predictions: np.ndarray,
    boosting_ranking_predictions: np.ndarray,
    seed_ranking_predictions: np.ndarray,
    prediction_std: np.ndarray | None,
    feature_coverage: float,
    point_in_time_audit: bool,
    portfolio_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    class_index = {name: index for index, name in enumerate(CLASS_ORDER)}
    score = np.asarray(ranking_predictions, dtype=float)
    fold_values = (
        validation["_walk_forward_fold"]
        if "_walk_forward_fold" in validation.columns
        else pd.Series(-1, index=validation.index, dtype=int)
    )
    logistic_score = logistic_probabilities[:, class_index["up"]] - logistic_probabilities[:, class_index["down"]]
    boosting_score = boosting_probabilities[:, class_index["up"]] - boosting_probabilities[:, class_index["down"]]
    evaluation_payload: dict[str, Any] = {
            "trade_date": validation["trade_date"].astype(str).to_numpy(),
            "code": validation.get("code", pd.Series(validation.index.astype(str), index=validation.index)).astype(str).to_numpy(),
            "score": score,
            "expected_excess_return": np.asarray(
                expected_excess_predictions,
                dtype=float,
            ),
            "prediction_uncertainty_bps": (
                np.asarray(prediction_std, dtype=float)
                if prediction_std is not None
                else np.std(
                    np.asarray(seed_ranking_predictions, dtype=float),
                    axis=1,
                    ddof=0,
                )
            ) * 10_000.0,
            "logistic_score": logistic_score,
            "boosting_score": boosting_score,
            "linear_ranking_score": linear_ranking_predictions,
            "boosting_ranking_score": boosting_ranking_predictions,
            "logistic_weight": ensemble_weights,
            "excess_return": pd.to_numeric(validation["excess_return"], errors="coerce").to_numpy(),
            "fold": pd.to_numeric(fold_values, errors="coerce").fillna(-1).to_numpy(),
        }
    for column in (
        "account_id", "entry_date", "entry_price", "benchmark_entry_price",
        "avg_amount_20", "realized_volatility_20", "momentum_20",
    ):
        if column in validation.columns:
            evaluation_payload[column] = validation[column].to_numpy()
    evaluation = pd.DataFrame(evaluation_payload).dropna(
        subset=["excess_return"]
    ).reset_index(drop=True)
    cross_section_columns = _cross_section_columns(evaluation)
    daily_ics = [
        value
        for _, group in evaluation.groupby(cross_section_columns, sort=True)
        if (value := _spearman(group["score"], group["excess_return"])) is not None
    ]
    if not daily_ics:
        fallback_ic = _spearman(evaluation["score"], evaluation["excess_return"])
        daily_ics = [fallback_ic] if fallback_ic is not None else []
    rank_ic = float(np.mean(daily_ics)) if daily_ics else 0.0
    ic_std = float(np.std(daily_ics, ddof=1)) if len(daily_ics) > 1 else 0.0
    icir = rank_ic / ic_std if ic_std > 1e-12 else 0.0

    brier = _multiclass_brier(ensemble, validation_y, CLASS_ORDER)
    baseline_brier = _multiclass_brier(baseline_probabilities, validation_y, CLASS_ORDER)
    brier_improvement = (baseline_brier - brier) / baseline_brier if baseline_brier > 0 else 0.0
    up_index = class_index["up"]
    down_index = class_index["down"]
    predicted_labels = np.where(ensemble[:, up_index] >= ensemble[:, down_index], "up", "down")
    high_confidence = np.maximum(ensemble[:, up_index], ensemble[:, down_index]) >= 0.55
    high_hit = float(np.mean(predicted_labels[high_confidence] == validation_y[high_confidence])) if high_confidence.any() else 0.0
    unconditional_hit = max(float(np.mean(validation_y == "up")), float(np.mean(validation_y == "down")))
    hit_rate_uplift = high_hit - unconditional_hit
    try:
        auc = float(roc_auc_score(validation_y, ensemble, labels=list(CLASS_ORDER), multi_class="ovr", average="macro"))
    except ValueError:
        auc = 0.0
    if not np.isfinite(auc):
        auc = 0.0

    horizon_values = pd.to_numeric(validation.get("horizon"), errors="coerce").dropna()
    horizon = int(horizon_values.iloc[0]) if not horizon_values.empty else 1
    replay_required = {
        "account_id", "entry_date", "entry_price", "benchmark_entry_price",
    }
    if portfolio_contract is not None and replay_required.issubset(evaluation.columns):
        replay = replay_model_portfolio(
            evaluation,
            contract=portfolio_contract,
        )
        diagnostic_replay = replay_fixed_top_n_diagnostic_portfolio(
            evaluation,
            contract=portfolio_contract,
        )
        portfolio_metrics = dict(replay.metrics)
        deployable_subperiods: list[dict[str, Any]] = []
        for fold, fold_frame in evaluation.groupby("fold", sort=True):
            fold_number = int(fold)
            if fold_number < 0:
                continue
            try:
                fold_replay = replay_model_portfolio(
                    fold_frame,
                    contract=portfolio_contract,
                )
            except ValueError:
                continue
            fold_metrics = fold_replay.metrics
            deployable_subperiods.append({
                "fold": fold_number,
                "start": str(fold_frame["trade_date"].min()),
                "end": str(fold_frame["trade_date"].max()),
                "net_excess_return": float(
                    fold_metrics.get("net_excess_return") or 0.0
                ),
                "max_drawdown": float(
                    fold_metrics.get("max_drawdown") or 0.0
                ),
                "annual_turnover": float(
                    fold_metrics.get("annual_turnover") or 0.0
                ),
                "trade_count": int(fold_metrics.get("trade_count") or 0),
                "capital_utilization": float(
                    fold_metrics.get("capital_utilization") or 0.0
                ),
            })
        portfolio_metrics["deployable_subperiods"] = deployable_subperiods
        diagnostic_metric_keys = (
            "replay_contract",
            "net_return",
            "benchmark_return",
            "net_excess_return",
            "cumulative_relative_wealth",
            "annualized_excess_wealth",
            "max_drawdown",
            "annual_turnover",
            "capital_utilization",
            "portfolio_sharpe",
            "information_ratio",
            "portfolio_rebalance_periods",
            "trade_count",
            "attribution_status",
            "execution_evidence_status",
            "missing_liquidity_notional_ratio",
            "impact_capped_notional_ratio",
            "all_accounts_positive_active",
            "simulator_version",
        )
        portfolio_metrics.update({
            f"diagnostic_{key}": diagnostic_replay.metrics.get(key)
            for key in diagnostic_metric_keys
        })
        trial_results: list[dict[str, Any]] = []

        def add_trial_result(spec_id: str, result: Any) -> None:
            metrics = result.metrics
            trial_results.append({
                "spec_id": spec_id,
                "sharpe": float(metrics.get("information_ratio", 0.0)),
                "net_excess_return": float(metrics.get("net_excess_return", 0.0)),
                "oos_returns": [
                    {"date": str(day), "return": float(value)}
                    for day, value in zip(
                        metrics.get("portfolio_period_return_dates") or [],
                        metrics.get("portfolio_period_returns") or [],
                    )
                ],
            })

        add_trial_result("ridge_hgbr_ensemble", replay)
        component_scores = {
            "ridge_ranker": "linear_ranking_score",
            "hgbr_ranker": "boosting_ranking_score",
        }
        for spec_id, score_column in component_scores.items():
            component_frame = evaluation.copy()
            component_frame["score"] = pd.to_numeric(
                component_frame[score_column], errors="coerce"
            )
            try:
                component_replay = replay_model_portfolio(
                    component_frame,
                    contract=portfolio_contract,
                )
            except ValueError:
                continue
            add_trial_result(spec_id, component_replay)
        baseline_comparison: dict[str, dict[str, float]] = {
            "no_trade": {"net_excess_return": 0.0, "annual_turnover": 0.0},
        }
        baseline_scores = {
            "momentum_20": "momentum_20",
            "low_volatility_20": "realized_volatility_20",
        }
        for baseline_name, column in baseline_scores.items():
            if column not in evaluation.columns:
                continue
            baseline_frame = evaluation.copy()
            values = pd.to_numeric(baseline_frame[column], errors="coerce")
            baseline_frame["score"] = -values if baseline_name == "low_volatility_20" else values
            try:
                baseline_replay = replay_fixed_top_n_diagnostic_portfolio(
                    baseline_frame,
                    contract=portfolio_contract,
                )
            except ValueError:
                continue
            baseline_metrics = baseline_replay.metrics
            baseline_comparison[baseline_name] = {
                "net_excess_return": float(baseline_metrics["net_excess_return"]),
                "annual_turnover": float(baseline_metrics["annual_turnover"]),
                "max_drawdown": float(baseline_metrics["max_drawdown"]),
            }
            add_trial_result(baseline_name, baseline_replay)
        portfolio_metrics["baseline_comparison"] = baseline_comparison
        best_baseline = max(
            (item["net_excess_return"] for item in baseline_comparison.values()),
            default=0.0,
        )
        portfolio_metrics["net_excess_vs_best_simple_baseline"] = (
            float(portfolio_metrics["net_excess_return"]) - float(best_baseline)
        )
        portfolio_metrics["predeclared_trial_results"] = trial_results
        portfolio_metrics["declared_trial_spec_ids"] = [
            str(spec["spec_id"]) for spec in DEFAULT_CLASSICAL_TRIAL_SPECS
        ]
        portfolio_metrics["valid_trial_count"] = len(trial_results)
    else:
        portfolio_metrics = _portfolio_oos_metrics(evaluation, horizon=horizon)
        portfolio_metrics["simulator_version"] = "legacy-percentile-v1"
    stability_values: list[float] = []
    for _, group in evaluation.groupby(cross_section_columns, sort=True):
        for component in ("linear_ranking_score", "boosting_ranking_score"):
            value = _spearman(group["score"], group[component])
            if value is not None:
                stability_values.append(float(np.clip((value + 1.0) / 2.0, 0.0, 1.0)))
    ablation_stability = float(np.mean(stability_values)) if stability_values else 0.0
    fold_ics = [
        value
        for _, group in evaluation.groupby("fold", sort=True)
        if (value := _spearman(group["score"], group["excess_return"])) is not None
    ]
    subperiod_stability = (
        float(np.mean([np.sign(value) == np.sign(rank_ic) for value in fold_ics]))
        if fold_ics and abs(rank_ic) > 1e-12
        else 0.0
    )
    seed_rank_ics: list[float] = []
    for seed_index in range(seed_ranking_predictions.shape[1]):
        seed_values = seed_ranking_predictions[:, seed_index]
        seed_daily_ics = [
            value
            for indices in evaluation.groupby(
                cross_section_columns,
                sort=True,
            ).indices.values()
            if (value := _spearman(
                pd.Series(seed_values[np.asarray(indices, dtype=int)]),
                evaluation.iloc[np.asarray(indices, dtype=int)]["excess_return"].reset_index(drop=True),
            )) is not None
        ]
        seed_rank_ics.append(float(np.mean(seed_daily_ics)) if seed_daily_ics else 0.0)
    return {
        "feature_coverage": feature_coverage,
        "point_in_time_audit": bool(point_in_time_audit),
        "oos_predictions": int(len(validation_y)),
        "rank_ic": rank_ic,
        "icir": float(icir),
        "brier_improvement": float(brier_improvement),
        "hit_rate_uplift": float(hit_rate_uplift),
        "auc": auc,
        **portfolio_metrics,
        "ablation_stability": ablation_stability,
        "subperiod_stability": subperiod_stability,
        "seed_rank_ic": seed_rank_ics,
        "seed_rank_ic_std": float(np.std(seed_rank_ics, ddof=1)) if len(seed_rank_ics) > 1 else 0.0,
        "reliability_curve": _reliability_curve(ensemble, validation_y),
        "class_balance": {
            class_name: float(np.mean(validation_y == class_name)) for class_name in CLASS_ORDER
        },
        "evidence_scope": "development_selection",
        "ranking_score_source": "raw_model_excess_return",
        "economic_score_source": "training_only_edge_calibration",
    }


def _calibration_model_selection_partition(
    calibration: pd.DataFrame,
    *,
    embargo: int,
) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    dates = np.asarray(sorted(calibration["trade_date"].astype(str).unique()))
    if len(dates) < max(15, embargo * 2 + 5):
        return calibration, calibration, False
    selection_start = max(1, int(len(dates) * 0.70))
    fit_dates = dates[: max(0, selection_start - max(0, embargo))]
    selection_dates = dates[selection_start:]
    fit = calibration.loc[calibration["trade_date"].astype(str).isin(fit_dates)].copy()
    selection = calibration.loc[calibration["trade_date"].astype(str).isin(selection_dates)].copy()
    if not selection.empty:
        fit = fit.loc[
            fit["label_end_date"].astype(str) < str(selection["trade_date"].astype(str).min())
        ]
    if fit.empty or selection.empty or fit["label"].nunique() < 2 or selection["label"].nunique() < 2:
        return calibration, calibration, False
    return fit, selection, True


def _select_calibration_method(
    fit_probabilities: np.ndarray,
    fit_labels: np.ndarray,
    selection_probabilities: np.ndarray,
    selection_labels: np.ndarray,
) -> tuple[str, dict[str, float]]:
    counts = pd.Series(fit_labels).value_counts()
    methods = ["identity", "temperature", "sigmoid"]
    if all(counts.get(name, 0) >= 1000 for name in CLASS_ORDER):
        methods.append("isotonic")
    losses: dict[str, float] = {}
    for method in methods:
        calibrator = MultiClassCalibrator(method, CLASS_ORDER).fit(fit_probabilities, fit_labels)
        probabilities = calibrator.predict(selection_probabilities)
        losses[method] = float(log_loss(selection_labels, probabilities, labels=list(CLASS_ORDER)))
    best_loss = min(losses.values())
    complexity = {"identity": 0, "temperature": 1, "sigmoid": 2, "isotonic": 3}
    eligible = [method for method, loss in losses.items() if loss <= best_loss + 0.002]
    selected = min(eligible, key=lambda method: (complexity[method], losses[method], method))
    return selected, losses


def _select_ensemble_weight(
    logistic_probabilities: np.ndarray,
    boosting_probabilities: np.ndarray,
    labels: np.ndarray,
) -> tuple[float, dict[str, float]]:
    losses: dict[str, float] = {}
    candidates = np.linspace(0.0, 1.0, 9)
    for weight in candidates:
        probabilities = weight * logistic_probabilities + (1.0 - weight) * boosting_probabilities
        losses[f"{weight:.3f}"] = float(log_loss(labels, probabilities, labels=list(CLASS_ORDER)))
    best_loss = min(losses.values())
    eligible = [
        float(weight)
        for weight, loss in losses.items()
        if loss <= best_loss + 0.001
    ]
    selected = min(eligible, key=lambda weight: (abs(weight - 0.5), weight))
    return float(selected), losses


def _select_ranking_ensemble_weight(
    linear_predictions: np.ndarray,
    boosting_predictions: np.ndarray,
    selection: pd.DataFrame,
) -> tuple[float, dict[str, float]]:
    target = pd.to_numeric(selection["excess_return"], errors="coerce").reset_index(drop=True)
    dates = selection["trade_date"].astype(str).reset_index(drop=True)
    scores: dict[str, float] = {}
    for weight in np.linspace(0.0, 1.0, 9):
        prediction = pd.Series(
            weight * linear_predictions + (1.0 - weight) * boosting_predictions
        )
        daily = [
            value
            for trade_date in sorted(dates.unique())
            if (value := _spearman(
                prediction.loc[dates.eq(trade_date)],
                target.loc[dates.eq(trade_date)],
            )) is not None
        ]
        fallback = _spearman(prediction, target)
        scores[f"{weight:.3f}"] = float(np.mean(daily)) if daily else float(fallback or 0.0)
    best = max(scores.values())
    eligible = [
        float(weight)
        for weight, score in scores.items()
        if score >= best - 0.002
    ]
    selected = min(eligible, key=lambda weight: (abs(weight - 0.5), weight))
    return float(selected), scores


@dataclass
class _FittedComponents:
    imputation_values: dict[str, float]
    clip_bounds: dict[str, tuple[float, float]]
    feature_reference: dict[str, dict[str, Any]]
    scaler: StandardScaler
    logistic: LogisticRegression
    logistic_calibrator: MultiClassCalibrator
    boosting: HistGradientBoostingClassifier
    boosting_calibrator: MultiClassCalibrator
    linear_ranking: Any
    boosting_ranking: tuple[HistGradientBoostingRegressor, ...]
    calibration_method: str
    ensemble_logistic_weight: float
    ranking_ensemble_linear_weight: float
    calibration_diagnostics: dict[str, Any]
    edge_calibrator: EdgeCalibrator | None
    ranking_target: str
    ranking_residual_weight: float

    def probabilities(self, frame: pd.DataFrame, columns: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        matrix, _ = _impute(frame, columns, self.imputation_values, self.clip_bounds)
        logistic = self.logistic_calibrator.predict(
            self.logistic.predict_proba(self.scaler.transform(matrix))
        )
        boosting = self.boosting_calibrator.predict(self.boosting.predict_proba(matrix))
        ensemble = self.ensemble_logistic_weight * logistic + (1.0 - self.ensemble_logistic_weight) * boosting
        return logistic, boosting, ensemble

    def ranking_predictions(
        self,
        frame: pd.DataFrame,
        columns: tuple[str, ...],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        matrix, _ = _impute(frame, columns, self.imputation_values, self.clip_bounds)
        linear = np.asarray(
            self.linear_ranking.predict(self.scaler.transform(matrix)),
            dtype=float,
        )
        seeds = np.column_stack([model.predict(matrix) for model in self.boosting_ranking])
        linear = _apply_ranking_anchor(
            linear,
            frame,
            self.ranking_target,
            residual_weight=self.ranking_residual_weight,
        )
        seeds = np.column_stack([
            _apply_ranking_anchor(
                seeds[:, index],
                frame,
                self.ranking_target,
                residual_weight=self.ranking_residual_weight,
            )
            for index in range(seeds.shape[1])
        ])
        boosting = np.mean(seeds, axis=1)
        ensemble = (
            self.ranking_ensemble_linear_weight * linear
            + (1.0 - self.ranking_ensemble_linear_weight) * boosting
        )
        return linear, boosting, np.asarray(ensemble, dtype=float), seeds


def _fit_components(
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    random_state: int,
    model_spec: ClassicalModelSpec | None = None,
) -> _FittedComponents:
    if train["label"].nunique() < 3 or calibration["label"].nunique() < 2:
        raise ValueError("model_class_coverage")
    clip_bounds = _fit_clip_bounds(train, columns)
    feature_reference = _build_feature_reference(train, columns)
    train_x, imputation_values = _impute(train, columns, clip_bounds=clip_bounds)
    calibration_x, _ = _impute(calibration, columns, imputation_values, clip_bounds)
    train_y = train["label"].astype(str).to_numpy()
    ranking_target = (
        model_spec.ranking_target
        if model_spec is not None
        else "raw_excess_return"
    )
    train_return_y = _ranking_target_values(train, ranking_target)
    train_weights = date_balanced_sample_weights(train).to_numpy(dtype=float)
    calibration_y = calibration["label"].astype(str).to_numpy()
    scaler = StandardScaler().fit(train_x, sample_weight=train_weights)
    logistic = LogisticRegression(C=0.5, max_iter=500, random_state=random_state)
    logistic.fit(scaler.transform(train_x), train_y, sample_weight=train_weights)
    boosting = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_iter=120,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=random_state,
    )
    boosting.fit(train_x, train_y, sample_weight=train_weights)
    spec_parameters = model_spec.parameter_map if model_spec is not None else {}
    ranking_residual_weight = float(
        spec_parameters.get("residual_tilt_weight", 1.0)
    )
    if not 0.0 <= ranking_residual_weight <= 1.0:
        raise ValueError("ranking_residual_weight_out_of_range")
    if model_spec is not None and model_spec.estimator == "elastic_net":
        linear_ranking = ElasticNet(
            alpha=float(spec_parameters.get("alpha", 0.0005)),
            l1_ratio=float(spec_parameters.get("l1_ratio", 0.25)),
            max_iter=5_000,
            random_state=random_state,
        )
    else:
        linear_ranking = Ridge(
            alpha=float(
                spec_parameters.get(
                    "ridge_alpha",
                    spec_parameters.get("alpha", 10.0),
                )
            )
        )
    linear_ranking.fit(
        scaler.transform(train_x),
        train_return_y,
        sample_weight=train_weights,
    )
    boosting_ranking = tuple(
        HistGradientBoostingRegressor(
            learning_rate=float(spec_parameters.get("learning_rate", 0.05)),
            max_iter=int(spec_parameters.get("max_iter", 100)),
            max_leaf_nodes=int(spec_parameters.get("max_leaf_nodes", 15)),
            min_samples_leaf=int(spec_parameters.get("min_samples_leaf", 20)),
            l2_regularization=float(spec_parameters.get("l2_regularization", 1.0)),
            random_state=random_state + seed_offset,
        ).fit(train_x, train_return_y, sample_weight=train_weights)
        for seed_offset in (0, 997, 1999)
    )
    if tuple(str(value) for value in logistic.classes_) != CLASS_ORDER:
        raise ValueError("model_class_order")
    if tuple(str(value) for value in boosting.classes_) != CLASS_ORDER:
        raise ValueError("model_class_order")
    calibration_fit, calibration_selection, has_holdout = _calibration_model_selection_partition(
        calibration,
        embargo=max(1, int(pd.to_numeric(calibration.get("horizon"), errors="coerce").dropna().iloc[0]))
        if pd.to_numeric(calibration.get("horizon"), errors="coerce").notna().any()
        else 1,
    )
    fit_x, _ = _impute(calibration_fit, columns, imputation_values, clip_bounds)
    selection_x, _ = _impute(calibration_selection, columns, imputation_values, clip_bounds)
    fit_y = calibration_fit["label"].astype(str).to_numpy()
    selection_y = calibration_selection["label"].astype(str).to_numpy()
    linear_selection = np.asarray(
        linear_ranking.predict(scaler.transform(selection_x)),
        dtype=float,
    )
    boosting_seed_selection = np.column_stack([
        model.predict(selection_x) for model in boosting_ranking
    ])
    linear_selection = _apply_ranking_anchor(
        linear_selection,
        calibration_selection,
        ranking_target,
        residual_weight=ranking_residual_weight,
    )
    boosting_seed_selection = np.column_stack([
        _apply_ranking_anchor(
            boosting_seed_selection[:, index],
            calibration_selection,
            ranking_target,
            residual_weight=ranking_residual_weight,
        )
        for index in range(boosting_seed_selection.shape[1])
    ])
    boosting_ranking_selection = np.mean(boosting_seed_selection, axis=1)
    if model_spec is not None:
        ranking_weight = float(spec_parameters.get("ranking_linear_weight", 0.5))
        ranking_scores = {f"{ranking_weight:.3f}": float("nan")}
        ranking_weight_source = "predeclared_spec"
    else:
        ranking_weight, ranking_scores = _select_ranking_ensemble_weight(
            linear_selection,
            boosting_ranking_selection,
            calibration_selection,
        )
        ranking_weight_source = "calibration_selection"
    calibration_linear = np.asarray(
        linear_ranking.predict(scaler.transform(calibration_x)),
        dtype=float,
    )
    calibration_boosting = np.mean(
        np.column_stack([model.predict(calibration_x) for model in boosting_ranking]),
        axis=1,
    )
    calibration_linear = _apply_ranking_anchor(
        calibration_linear,
        calibration,
        ranking_target,
        residual_weight=ranking_residual_weight,
    )
    calibration_boosting = _apply_ranking_anchor(
        calibration_boosting,
        calibration,
        ranking_target,
        residual_weight=ranking_residual_weight,
    )
    calibration_ranking = (
        ranking_weight * calibration_linear
        + (1.0 - ranking_weight) * calibration_boosting
    )
    observed_horizons = pd.to_numeric(
        calibration["horizon"]
        if "horizon" in calibration.columns
        else pd.Series(dtype=float),
        errors="coerce",
    ).dropna()
    calibration_horizon = (
        max(int(observed_horizons.iloc[0]), 1)
        if not observed_horizons.empty else 1
    )
    edge_calibrator = fit_edge_calibrator(
        pd.DataFrame({
            "trade_date": calibration["trade_date"].astype(str).to_numpy(),
            "score": calibration_ranking,
        }),
        pd.to_numeric(calibration["excess_return"], errors="coerce"),
        minimum_dates_per_bucket=8,
        horizon=calibration_horizon,
    )
    logistic_fit_raw = logistic.predict_proba(scaler.transform(fit_x))
    boosting_fit_raw = boosting.predict_proba(fit_x)
    logistic_selection_raw = logistic.predict_proba(scaler.transform(selection_x))
    boosting_selection_raw = boosting.predict_proba(selection_x)
    if has_holdout:
        logistic_method, logistic_losses = _select_calibration_method(
            logistic_fit_raw, fit_y, logistic_selection_raw, selection_y
        )
        boosting_method, boosting_losses = _select_calibration_method(
            boosting_fit_raw, fit_y, boosting_selection_raw, selection_y
        )
        logistic_selection_calibrator = MultiClassCalibrator(logistic_method, CLASS_ORDER).fit(
            logistic_fit_raw, fit_y
        )
        boosting_selection_calibrator = MultiClassCalibrator(boosting_method, CLASS_ORDER).fit(
            boosting_fit_raw, fit_y
        )
        logistic_selection = logistic_selection_calibrator.predict(logistic_selection_raw)
        boosting_selection = boosting_selection_calibrator.predict(boosting_selection_raw)
        ensemble_weight, ensemble_losses = _select_ensemble_weight(
            logistic_selection, boosting_selection, selection_y
        )
    else:
        logistic_method = boosting_method = "sigmoid"
        logistic_losses = boosting_losses = {}
        ensemble_weight = 0.5
        ensemble_losses = {}
    logistic_raw = logistic.predict_proba(scaler.transform(calibration_x))
    boosting_raw = boosting.predict_proba(calibration_x)
    logistic_calibrator = MultiClassCalibrator(logistic_method, CLASS_ORDER).fit(
        logistic_raw, calibration_y
    )
    boosting_calibrator = MultiClassCalibrator(boosting_method, CLASS_ORDER).fit(
        boosting_raw, calibration_y
    )
    method = f"logistic={logistic_method};boosting={boosting_method}"
    return _FittedComponents(
        imputation_values=imputation_values,
        clip_bounds=clip_bounds,
        feature_reference=feature_reference,
        scaler=scaler,
        logistic=logistic,
        logistic_calibrator=logistic_calibrator,
        boosting=boosting,
        boosting_calibrator=boosting_calibrator,
        linear_ranking=linear_ranking,
        boosting_ranking=boosting_ranking,
        calibration_method=method,
        ensemble_logistic_weight=ensemble_weight,
        ranking_ensemble_linear_weight=ranking_weight,
        calibration_diagnostics={
            "selection_holdout": has_holdout,
            "selection_rows": len(calibration_selection) if has_holdout else 0,
            "logistic_losses": logistic_losses,
            "boosting_losses": boosting_losses,
            "ensemble_losses": ensemble_losses,
            "ranking_rank_ic_by_weight": ranking_scores,
            "ranking_weight_source": ranking_weight_source,
            "edge_calibration_available": (
                edge_calibrator.available if edge_calibrator is not None else None
            ),
            "edge_calibration_reason": (
                edge_calibrator.reason if edge_calibrator is not None else "legacy_raw_score"
            ),
            "edge_calibration_version": (
                edge_calibrator.calibration_version
                if edge_calibrator is not None else "legacy_raw_score"
            ),
            "edge_calibrator_hash": (
                edge_calibrator.calibrator_hash if edge_calibrator is not None else ""
            ),
        },
        edge_calibrator=edge_calibrator,
        ranking_target=ranking_target,
        ranking_residual_weight=ranking_residual_weight,
    )


def _training_calibration_partition(data: pd.DataFrame, *, embargo: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = np.asarray(sorted(data["trade_date"].astype(str).unique()))
    if len(dates) < 10:
        raise ValueError("model_insufficient_dates")
    calibration_start_index = max(1, int(len(dates) * 0.80))
    calibration_dates = dates[calibration_start_index:]
    train_dates = dates[: max(0, calibration_start_index - max(0, embargo))]
    calibration = data.loc[data["trade_date"].astype(str).isin(calibration_dates)].copy()
    train = data.loc[data["trade_date"].astype(str).isin(train_dates)].copy()
    if not calibration.empty:
        train = train.loc[
            train["label_end_date"].astype(str) < str(calibration["trade_date"].astype(str).min())
        ]
    if train.empty or calibration.empty:
        raise ValueError("model_partition_empty")
    return train.sort_values("trade_date"), calibration.sort_values("trade_date")


def train_model_bundle(
    dataset: pd.DataFrame,
    *,
    feature_columns: list[str] | tuple[str, ...],
    horizon: int,
    random_state: int = 20260713,
    feature_registry_hash: str = DEFAULT_REGISTRY_HASH,
    portfolio_contract: dict[str, Any] | None = None,
    account_scope: str = "",
    feature_selection_policy: dict[str, Any] | None = None,
    model_spec: ClassicalModelSpec | None = None,
    trial_declaration_id: str = "",
) -> ModelBundle:
    required = {"trade_date", "label_end_date", "horizon", "label", "excess_return"}
    if required.difference(dataset.columns):
        raise ValueError("model_missing_columns")
    candidate_columns = tuple(feature_columns)
    allowed_selection_keys = {
        "max_features",
        "min_coverage",
        "correlation_limit",
        "min_abs_ic",
        "min_stability",
        "min_ic_t_stat",
        "max_per_family",
    }
    selection_policy = {
        key: value
        for key, value in dict(feature_selection_policy or {}).items()
        if key in allowed_selection_keys
    }
    data = dataset.loc[dataset["horizon"] == horizon].sort_values("trade_date").reset_index(drop=True)
    normalized_scope = str(account_scope or "").strip()
    if model_spec is not None:
        if int(model_spec.horizon) != int(horizon):
            raise ValueError("model_spec_horizon_mismatch")
        if str(model_spec.account_scope) != normalized_scope:
            raise ValueError("model_spec_scope_mismatch")
    if normalized_scope:
        scope_column = (
            "research_scope"
            if "research_scope" in data.columns
            else "account_id" if "account_id" in data.columns else ""
        )
        if not scope_column:
            raise ValueError("model_scope_missing")
        observed_scopes = {
            str(value).strip()
            for value in data[scope_column].dropna().astype(str).tolist()
            if str(value).strip()
        }
        if observed_scopes != {normalized_scope}:
            raise ValueError("model_scope_mismatch")
    if len(data) < 120:
        raise ValueError("model_insufficient_samples")
    label_versions = sorted(
        data.get("label_contract_version", pd.Series(dtype=str))
        .dropna().astype(str).unique()
    )
    label_contract_version = label_versions[0] if len(label_versions) == 1 else "unverified"
    unbiased_universe = bool(
        "unbiased_universe" in data.columns
        and data["unbiased_universe"].fillna(False).astype(bool).all()
    )
    universe_versions = sorted(
        data.get("universe_contract_version", pd.Series(dtype=str))
        .dropna().astype(str).unique()
    )
    universe_contract_version = universe_versions[0] if len(universe_versions) == 1 else "unverified"
    membership_sources = sorted(
        data.get("membership_source", pd.Series(dtype=str))
        .dropna().astype(str).unique()
    )
    splits = make_purged_walk_forward_splits(data, n_splits=3, embargo=horizon)
    validation_parts: list[pd.DataFrame] = []
    logistic_parts: list[np.ndarray] = []
    boosting_parts: list[np.ndarray] = []
    ensemble_parts: list[np.ndarray] = []
    baseline_parts: list[np.ndarray] = []
    ensemble_weight_parts: list[np.ndarray] = []
    linear_ranking_parts: list[np.ndarray] = []
    boosting_ranking_parts: list[np.ndarray] = []
    ranking_parts: list[np.ndarray] = []
    expected_excess_parts: list[np.ndarray] = []
    seed_ranking_parts: list[np.ndarray] = []
    prediction_std_parts: list[np.ndarray] = []
    audit_results: list[bool] = []
    evaluation_train = pd.DataFrame()
    evaluation_calibration = pd.DataFrame()
    evaluation_validation = pd.DataFrame()
    fit_train_rows: list[int] = []
    fit_calibration_rows: list[int] = []
    fold_feature_columns: list[tuple[str, ...]] = []
    fold_selection_diagnostics: list[dict[str, Any]] = []
    fold_feature_coverage: list[float] = []
    for split_number, split in enumerate(splits):
        outer_train = data.loc[split.train_indices]
        validation = data.loc[split.validation_indices].sort_values("trade_date").copy()
        validation["_walk_forward_fold"] = split_number
        try:
            train, calibration = _training_calibration_partition(outer_train, embargo=horizon)
            fit_train = _bounded_cross_section_sample(
                train,
                max_rows=100_000,
                random_state=random_state + split_number,
            )
            fit_calibration = _bounded_cross_section_sample(
                calibration,
                max_rows=50_000,
                random_state=random_state + 10_000 + split_number,
            )
            selected_columns, selection_diagnostics = _select_training_features(
                fit_train,
                candidate_columns,
                selection_policy=selection_policy,
                model_spec=model_spec,
            )
            fitted = _fit_components(
                fit_train,
                fit_calibration,
                selected_columns,
                random_state=random_state + split_number,
                model_spec=model_spec,
            )
        except ValueError:
            continue
        logistic_probabilities, boosting_probabilities, ensemble = fitted.probabilities(
            validation, selected_columns
        )
        linear_ranking, boosting_ranking, ranking, seed_ranking = fitted.ranking_predictions(
            validation,
            selected_columns,
        )
        if fitted.edge_calibrator is not None:
            expected_excess, prediction_std = fitted.edge_calibrator.predict_distribution(
                ranking
            )
        else:
            expected_excess = np.zeros(len(ranking), dtype=float)
            prediction_std = np.ones(len(ranking), dtype=float)
        train_y_fold = fit_train["label"].astype(str).to_numpy()
        frequencies = np.asarray(
            [float(np.mean(train_y_fold == class_name)) for class_name in CLASS_ORDER],
            dtype=float,
        )
        validation_parts.append(validation)
        logistic_parts.append(logistic_probabilities)
        boosting_parts.append(boosting_probabilities)
        ensemble_parts.append(ensemble)
        baseline_parts.append(np.tile(frequencies, (len(validation), 1)))
        ensemble_weight_parts.append(
            np.full(len(validation), fitted.ensemble_logistic_weight, dtype=float)
        )
        linear_ranking_parts.append(linear_ranking)
        boosting_ranking_parts.append(boosting_ranking)
        ranking_parts.append(ranking)
        expected_excess_parts.append(expected_excess)
        seed_ranking_parts.append(seed_ranking)
        prediction_std_parts.append(prediction_std)
        fold_feature_columns.append(selected_columns)
        fold_selection_diagnostics.append(selection_diagnostics)
        fold_feature_coverage.append(
            float(validation.loc[:, selected_columns].notna().mean().mean())
        )
        fit_train_rows.append(len(fit_train))
        fit_calibration_rows.append(len(fit_calibration))
        evaluation_train = fit_train
        evaluation_calibration = fit_calibration
        evaluation_validation = validation
        audit_results.append(bool(
            str(train["label_end_date"].max()) < str(calibration["trade_date"].astype(str).min())
            and str(calibration["label_end_date"].max()) < str(validation["trade_date"].astype(str).min())
        ))
    if not validation_parts:
        raise ValueError("model_walk_forward_insufficient")

    validation = pd.concat(validation_parts, ignore_index=True).sort_values("trade_date").reset_index(drop=True)
    logistic_probabilities = np.concatenate(logistic_parts)
    boosting_probabilities = np.concatenate(boosting_parts)
    ensemble = np.concatenate(ensemble_parts)
    baseline_probabilities = np.concatenate(baseline_parts)
    ensemble_weights = np.concatenate(ensemble_weight_parts)
    linear_ranking_predictions = np.concatenate(linear_ranking_parts)
    boosting_ranking_predictions = np.concatenate(boosting_ranking_parts)
    ranking_predictions = np.concatenate(ranking_parts)
    expected_excess_predictions = np.concatenate(expected_excess_parts)
    seed_ranking_predictions = np.concatenate(seed_ranking_parts, axis=0)
    prediction_std = np.concatenate(prediction_std_parts)
    validation_y = validation["label"].astype(str).to_numpy()
    logistic_loss = float(log_loss(validation_y, logistic_probabilities, labels=list(CLASS_ORDER)))
    boosting_loss = float(log_loss(validation_y, boosting_probabilities, labels=list(CLASS_ORDER)))
    ensemble_loss = float(log_loss(validation_y, ensemble, labels=list(CLASS_ORDER)))
    agreement = float(1.0 - np.mean(np.abs(logistic_probabilities - boosting_probabilities)) / 2.0)
    deployment_train_full, deployment_calibration_full = _training_calibration_partition(
        data,
        embargo=horizon,
    )
    train = _bounded_cross_section_sample(
        deployment_train_full,
        max_rows=100_000,
        random_state=random_state + 20_000,
    )
    calibration = _bounded_cross_section_sample(
        deployment_calibration_full,
        max_rows=50_000,
        random_state=random_state + 30_000,
    )
    columns, deployment_selection = _select_training_features(
        train,
        candidate_columns,
        selection_policy=selection_policy,
        model_spec=model_spec,
    )
    deployment = _fit_components(
        train,
        calibration,
        columns,
        random_state=random_state + 40_000,
        model_spec=model_spec,
    )
    fit_train_rows.append(len(train))
    fit_calibration_rows.append(len(calibration))
    deployment_audit = bool(
        str(train["label_end_date"].max())
        < str(calibration["trade_date"].astype(str).min())
    )
    point_in_time_audit = bool(
        all(audit_results)
        and deployment_audit
        and unbiased_universe
        and label_contract_version == LABEL_CONTRACT_VERSION
    )
    activation_metrics = _activation_metrics(
        baseline_probabilities=baseline_probabilities,
        validation=validation,
        validation_y=validation_y,
        ensemble=ensemble,
        logistic_probabilities=logistic_probabilities,
        boosting_probabilities=boosting_probabilities,
        ensemble_weights=ensemble_weights,
        ranking_predictions=ranking_predictions,
        expected_excess_predictions=expected_excess_predictions,
        linear_ranking_predictions=linear_ranking_predictions,
        boosting_ranking_predictions=boosting_ranking_predictions,
        seed_ranking_predictions=seed_ranking_predictions,
        prediction_std=prediction_std,
        feature_coverage=float(np.mean(fold_feature_coverage)) if fold_feature_coverage else 0.0,
        point_in_time_audit=point_in_time_audit,
        portfolio_contract=portfolio_contract,
    )

    return_source = pd.concat([train, calibration], ignore_index=True)
    returns = pd.to_numeric(return_source["excess_return"], errors="coerce")
    return_stats: dict[str, dict[str, float]] = {}
    for class_name in CLASS_ORDER:
        values = returns.loc[return_source["label"].astype(str) == class_name].dropna().to_numpy(dtype=float)
        return_stats[class_name] = {
            "mean": float(np.mean(values)) if len(values) else 0.0,
            "q10": float(np.quantile(values, 0.10)) if len(values) else 0.0,
            "q50": float(np.quantile(values, 0.50)) if len(values) else 0.0,
            "q90": float(np.quantile(values, 0.90)) if len(values) else 0.0,
        }
    ranking_reference = pd.Series(
        _ranking_target_values(return_source, deployment.ranking_target),
        index=return_source.index,
        dtype=float,
    )
    if deployment.ranking_target in {
        "momentum_anchor_residual_v1",
        "momentum_lowvol_anchor_residual_v1",
        "qdii_trend_anchor_residual_v1",
    }:
        ranking_reference = pd.Series(
            _apply_ranking_anchor(
                ranking_reference.to_numpy(dtype=float),
                return_source,
                deployment.ranking_target,
                residual_weight=deployment.ranking_residual_weight,
            ),
            index=return_source.index,
            dtype=float,
        )
    prediction_bounds = (
        float(ranking_reference.quantile(0.01))
        if ranking_reference.notna().any() else -1.0,
        float(ranking_reference.quantile(0.99))
        if ranking_reference.notna().any() else 1.0,
    )
    signature_columns = list(dict.fromkeys([
        *(["code"] if "code" in data.columns else []),
        "trade_date", "label", "excess_return", *columns,
    ]))
    data_hashes = pd.util.hash_pandas_object(
        data.loc[:, signature_columns],
        index=False,
        categorize=True,
    ).to_numpy()
    data_fingerprint = hashlib.sha256(data_hashes.tobytes()).hexdigest()[:16]
    universe_columns = [
        column
        for column in (
            "code",
            "account_id",
            "research_scope",
            "membership_source",
            "universe_contract_version",
        )
        if column in data.columns
    ]
    universe_rows = data.loc[:, universe_columns].drop_duplicates().sort_values(
        universe_columns,
        kind="stable",
    )
    scope_universe_hash = hashlib.sha256(
        pd.util.hash_pandas_object(
            universe_rows,
            index=False,
            categorize=True,
        ).to_numpy().tobytes()
    ).hexdigest()[:16]
    label_columns = [
        column
        for column in (
            "code",
            "trade_date",
            "horizon",
            "label",
            "label_end_date",
            "excess_return",
        )
        if column in data.columns
    ]
    label_hash = hashlib.sha256(
        pd.util.hash_pandas_object(
            data.loc[:, label_columns],
            index=False,
            categorize=True,
        ).to_numpy().tobytes()
    ).hexdigest()[:16]
    feature_schema_hash = hashlib.sha256(
        json.dumps(
            {
                "registry": feature_registry_hash,
                "candidate_features": candidate_columns,
                "selected_features": columns,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    scope_benchmark = ""
    if portfolio_contract is not None:
        scope_accounts = list(portfolio_contract.get("accounts") or [])
        if len(scope_accounts) == 1:
            scope_benchmark = str(scope_accounts[0].get("benchmark") or "")
    simulator_hash = hashlib.sha256(
        json.dumps(
            {
                "simulator_version": SIMULATOR_VERSION,
                "portfolio_contract": portfolio_contract,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    version_payload = {
        "horizon": horizon,
        "account_scope": normalized_scope,
        "features": columns,
        "candidate_features": candidate_columns,
        "train_end": str(train.iloc[-1]["trade_date"]),
        "calibration_end": str(calibration.iloc[-1]["trade_date"]),
        "data_fingerprint": data_fingerprint,
        "validation_mode": "purged_walk_forward",
        "training_protocol_version": TRAINING_PROTOCOL_VERSION,
        "feature_registry_hash": feature_registry_hash,
        "label_contract_version": label_contract_version,
        "universe_contract_version": universe_contract_version,
        "membership_sources": membership_sources,
        "portfolio_contract_hash": (
            hashlib.sha256(
                json.dumps(portfolio_contract, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16]
            if portfolio_contract is not None else None
        ),
        "random_state": random_state,
        "feature_selection_policy": selection_policy,
        "model_spec_id": model_spec.spec_id if model_spec is not None else "legacy_ensemble",
        "model_spec_hash": model_spec.spec_hash if model_spec is not None else "",
        "ranking_target": deployment.ranking_target,
        "ranking_residual_weight": deployment.ranking_residual_weight,
        "scope_universe_hash": scope_universe_hash,
        "scope_benchmark": scope_benchmark,
        "feature_schema_hash": feature_schema_hash,
        "label_hash": label_hash,
        "simulator_hash": simulator_hash,
        "trial_declaration_id": str(trial_declaration_id),
        "edge_calibration_version": (
            deployment.edge_calibrator.calibration_version
            if deployment.edge_calibrator is not None else ""
        ),
        "edge_calibrator_hash": (
            deployment.edge_calibrator.calibrator_hash
            if deployment.edge_calibrator is not None else ""
        ),
    }
    version = hashlib.sha256(json.dumps(version_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    selection_pairs = [
        len(set(left).intersection(right)) / max(len(set(left).union(right)), 1)
        for index, left in enumerate(fold_feature_columns)
        for right in fold_feature_columns[index + 1:]
    ]
    return ModelBundle(
        horizon=horizon,
        feature_columns=columns,
        class_order=CLASS_ORDER,
        imputation_values=deployment.imputation_values,
        scaler=deployment.scaler,
        logistic_model=deployment.logistic,
        logistic_calibrator=deployment.logistic_calibrator,
        boosting_model=deployment.boosting,
        boosting_calibrator=deployment.boosting_calibrator,
        use_boosting=True,
        metrics={
            "log_loss": ensemble_loss,
            "brier_score": _multiclass_brier(ensemble, validation_y, CLASS_ORDER),
            "logistic_log_loss": logistic_loss,
            "boosting_log_loss": boosting_loss,
            "model_agreement": agreement,
            "ensemble_logistic_weight": deployment.ensemble_logistic_weight,
            "ranking_ensemble_linear_weight": deployment.ranking_ensemble_linear_weight,
            "ranking_head": "ridge_hgbr",
            "model_spec_id": model_spec.spec_id if model_spec is not None else "legacy_ensemble",
            "model_spec_hash": model_spec.spec_hash if model_spec is not None else "",
            "model_estimator": model_spec.estimator if model_spec is not None else "legacy_ensemble",
            "ranking_target": (
                model_spec.ranking_target
                if model_spec is not None else "raw_excess_return"
            ),
            "ranking_residual_weight": deployment.ranking_residual_weight,
            "feature_selection_mode": (
                model_spec.feature_selection_mode
                if model_spec is not None else "stability_filter_v1"
            ),
            "edge_calibration_available": (
                deployment.edge_calibrator.available
                if deployment.edge_calibrator is not None else None
            ),
            "edge_calibration_reason": (
                deployment.edge_calibrator.reason
                if deployment.edge_calibrator is not None else "legacy_raw_score"
            ),
            "edge_calibration_version": (
                deployment.edge_calibrator.calibration_version
                if deployment.edge_calibrator is not None else "legacy_raw_score"
            ),
            "edge_calibrator_hash": (
                deployment.edge_calibrator.calibrator_hash
                if deployment.edge_calibrator is not None else ""
            ),
            "edge_calibration_fit_max_date": (
                deployment.edge_calibrator.fit_max_date
                if deployment.edge_calibrator is not None else ""
            ),
            "alpha_half_life_days": (
                deployment.edge_calibrator.alpha_half_life_days
                if deployment.edge_calibrator is not None else None
            ),
            "training_seed_count": len(deployment.boosting_ranking),
            "calibration_quality": float(np.clip(1.0 - ensemble_loss / np.log(3.0), 0.0, 1.0)),
            "walk_forward_splits": len(validation_parts),
            "training_protocol_version": TRAINING_PROTOCOL_VERSION,
            "sample_weighting": "equal_date_mass",
            "feature_registry_hash": feature_registry_hash,
            "fit_train_rows": max(fit_train_rows),
            "fit_calibration_rows": max(fit_calibration_rows),
            "evaluation_rows": len(validation),
            "effective_dates": int(validation["trade_date"].astype(str).nunique()),
            "effective_non_overlapping_periods": int(
                activation_metrics.get("portfolio_rebalance_periods", 0)
            ),
            "data_fingerprint": data_fingerprint,
            "account_scope": normalized_scope,
            "scope_universe_hash": scope_universe_hash,
            "scope_benchmark": scope_benchmark,
            "feature_schema_hash": feature_schema_hash,
            "label_hash": label_hash,
            "simulator_hash": simulator_hash,
            "trial_declaration_id": str(trial_declaration_id),
            "label_contract_version": label_contract_version,
            "universe_contract_version": universe_contract_version,
            "membership_sources": membership_sources,
            "unbiased_universe": unbiased_universe,
            "portfolio_contract_hash": version_payload["portfolio_contract_hash"],
            "candidate_feature_count": len(candidate_columns),
            "selected_feature_count": len(columns),
            "selected_features": list(columns),
            "feature_selection_policy": selection_policy,
            "fold_selected_features": [list(items) for items in fold_feature_columns],
            "feature_selection_stability": float(np.mean(selection_pairs)) if selection_pairs else 1.0,
            "feature_selection": deployment_selection,
            "fold_feature_selection": fold_selection_diagnostics,
            "calibration_diagnostics": deployment.calibration_diagnostics,
            **activation_metrics,
        },
        split_dates={
            "train_start": str(evaluation_train.iloc[0]["trade_date"]),
            "train_end": str(evaluation_train.iloc[-1]["trade_date"]),
            "calibration_start": str(evaluation_calibration.iloc[0]["trade_date"]),
            "calibration_end": str(evaluation_calibration.iloc[-1]["trade_date"]),
            "validation_start": str(evaluation_validation.iloc[0]["trade_date"]),
            "validation_end": str(evaluation_validation.iloc[-1]["trade_date"]),
            "oos_start": str(validation.iloc[0]["trade_date"]),
            "oos_end": str(validation.iloc[-1]["trade_date"]),
            "deployment_train_end": str(train.iloc[-1]["trade_date"]),
            "deployment_calibration_start": str(calibration.iloc[0]["trade_date"]),
            "deployment_calibration_end": str(calibration.iloc[-1]["trade_date"]),
            "validation_mode": "purged_walk_forward",
        },
        sample_support=len(train),
        calibration_method=deployment.calibration_method,
        model_version=version,
        return_stats=return_stats,
        ensemble_logistic_weight=deployment.ensemble_logistic_weight,
        clip_bounds=deployment.clip_bounds,
        feature_reference=deployment.feature_reference,
        linear_ranking_model=deployment.linear_ranking,
        boosting_ranking_models=deployment.boosting_ranking,
        ranking_ensemble_linear_weight=deployment.ranking_ensemble_linear_weight,
        ranking_prediction_bounds=prediction_bounds,
        account_scope=normalized_scope,
        edge_calibrator=deployment.edge_calibrator,
        ranking_target=deployment.ranking_target,
        ranking_residual_weight=deployment.ranking_residual_weight,
    )


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for label, package in (
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("pyarrow", "pyarrow"),
        ("scikit_learn", "scikit-learn"),
        ("ta_lib", "TA-Lib"),
    ):
        try:
            versions[label] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = "unavailable"
    return versions


def save_model_bundle(bundle: ModelBundle, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".joblib",
            delete=False,
        ) as handle:
            tmp_name = handle.name
        joblib.dump(bundle, tmp_name)
        os.replace(tmp_name, destination)
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    metadata = {
        "model_version": bundle.model_version,
        "horizon": bundle.horizon,
        "account_scope": str(getattr(bundle, "account_scope", "") or ""),
        "feature_columns": list(bundle.feature_columns),
        "class_order": list(bundle.class_order),
        "calibration_method": bundle.calibration_method,
        "use_boosting": bundle.use_boosting,
        "ensemble_logistic_weight": float(getattr(bundle, "ensemble_logistic_weight", 0.5)),
        "ranking_head": "ridge_hgbr" if getattr(bundle, "linear_ranking_model", None) is not None else "legacy_probability_buckets",
        "ranking_ensemble_linear_weight": float(
            getattr(bundle, "ranking_ensemble_linear_weight", 0.5)
        ),
        "training_seed_count": len(tuple(getattr(bundle, "boosting_ranking_models", ()) or ())),
        "ranking_target": str(getattr(bundle, "ranking_target", "raw_excess_return")),
        "ranking_residual_weight": float(
            getattr(bundle, "ranking_residual_weight", 1.0)
        ),
        "sample_support": bundle.sample_support,
        "metrics": bundle.metrics,
        "split_dates": bundle.split_dates,
        "dependency_versions": _dependency_versions(),
    }
    metadata_path = destination.with_suffix(".metadata.json")
    write_text_atomic(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def load_model_bundle(path: str | Path) -> ModelBundle:
    bundle = joblib.load(Path(path))
    if not isinstance(bundle, ModelBundle):
        raise ValueError("model_artifact_type")
    return bundle
