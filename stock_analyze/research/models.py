"""Purged walk-forward models, probability calibration, and artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from ..utils import write_text_atomic


CLASS_ORDER = ("down", "flat", "up")


@dataclass(frozen=True)
class WalkForwardSplit:
    train_indices: np.ndarray
    validation_indices: np.ndarray


class MultiClassCalibrator:
    def __init__(self, method: str, classes: tuple[str, ...]) -> None:
        self.method = method
        self.classes = classes
        self.models: list[Any | None] = []

    def fit(self, probabilities: np.ndarray, labels: np.ndarray) -> "MultiClassCalibrator":
        self.models = []
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

    def predict(self, probabilities: np.ndarray) -> np.ndarray:
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
        totals = calibrated.sum(axis=1, keepdims=True)
        return calibrated / totals


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
    metrics: dict[str, float | int | bool]
    split_dates: dict[str, str]
    sample_support: int
    calibration_method: str
    model_version: str
    return_stats: dict[str, dict[str, float]]

    def _matrix(self, frame: pd.DataFrame) -> np.ndarray:
        numeric = frame.loc[:, self.feature_columns].apply(pd.to_numeric, errors="coerce")
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
        return (logistic + boosting) / 2.0 if self.use_boosting else logistic

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
    ordered = data.sort_values("trade_date")
    count = len(ordered)
    validation_size = max(1, count // (n_splits + 2))
    first_validation = count - validation_size * n_splits
    splits: list[WalkForwardSplit] = []
    for split_number in range(n_splits):
        start = first_validation + split_number * validation_size
        stop = count if split_number == n_splits - 1 else start + validation_size
        validation = ordered.iloc[start:stop]
        train_stop = max(0, start - max(0, embargo))
        train = ordered.iloc[:train_stop]
        if not validation.empty:
            train = train.loc[train["label_end_date"].astype(str) < str(validation.iloc[0]["trade_date"])]
        if train.empty or validation.empty:
            continue
        splits.append(WalkForwardSplit(train.index.to_numpy(), validation.index.to_numpy()))
    return splits


def _impute(frame: pd.DataFrame, columns: tuple[str, ...], values: dict[str, float] | None = None) -> tuple[np.ndarray, dict[str, float]]:
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
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


def _activation_metrics(
    *,
    train_y: np.ndarray,
    validation: pd.DataFrame,
    validation_y: np.ndarray,
    ensemble: np.ndarray,
    logistic_probabilities: np.ndarray,
    boosting_probabilities: np.ndarray,
    feature_columns: tuple[str, ...],
    point_in_time_audit: bool,
) -> dict[str, float | int | bool]:
    class_index = {name: index for index, name in enumerate(CLASS_ORDER)}
    score = ensemble[:, class_index["up"]] - ensemble[:, class_index["down"]]
    logistic_score = logistic_probabilities[:, class_index["up"]] - logistic_probabilities[:, class_index["down"]]
    boosting_score = boosting_probabilities[:, class_index["up"]] - boosting_probabilities[:, class_index["down"]]
    evaluation = pd.DataFrame(
        {
            "trade_date": validation["trade_date"].astype(str).to_numpy(),
            "code": validation.get("code", pd.Series(validation.index.astype(str), index=validation.index)).astype(str).to_numpy(),
            "score": score,
            "logistic_score": logistic_score,
            "boosting_score": boosting_score,
            "excess_return": pd.to_numeric(validation["excess_return"], errors="coerce").to_numpy(),
        }
    ).dropna(subset=["excess_return"])
    daily_ics = [
        value
        for _, group in evaluation.groupby("trade_date", sort=True)
        if (value := _spearman(group["score"], group["excess_return"])) is not None
    ]
    if not daily_ics:
        fallback_ic = _spearman(evaluation["score"], evaluation["excess_return"])
        daily_ics = [fallback_ic] if fallback_ic is not None else []
    rank_ic = float(np.mean(daily_ics)) if daily_ics else 0.0
    ic_std = float(np.std(daily_ics, ddof=1)) if len(daily_ics) > 1 else 0.0
    icir = rank_ic / ic_std if ic_std > 1e-12 else 0.0

    train_frequencies = np.array([(train_y == name).mean() for name in CLASS_ORDER], dtype=float)
    baseline = np.tile(train_frequencies, (len(validation_y), 1))
    brier = _multiclass_brier(ensemble, validation_y, CLASS_ORDER)
    baseline_brier = _multiclass_brier(baseline, validation_y, CLASS_ORDER)
    brier_improvement = (baseline_brier - brier) / baseline_brier if baseline_brier > 0 else 0.0
    predicted_index = np.argmax(ensemble, axis=1)
    predicted_labels = np.array(CLASS_ORDER, dtype=object)[predicted_index]
    high_confidence = np.max(ensemble, axis=1) >= 0.55
    high_hit = float(np.mean(predicted_labels[high_confidence] == validation_y[high_confidence])) if high_confidence.any() else 0.0
    unconditional_hit = max(float(np.mean(validation_y == name)) for name in CLASS_ORDER)
    hit_rate_uplift = high_hit - unconditional_hit
    try:
        auc = float(roc_auc_score(validation_y, ensemble, labels=list(CLASS_ORDER), multi_class="ovr", average="macro"))
    except ValueError:
        auc = 0.0
    if not np.isfinite(auc):
        auc = 0.0

    gross_returns: list[float] = []
    turnovers: list[float] = []
    previous: set[str] | None = None
    for _, group in evaluation.groupby("trade_date", sort=True):
        count = max(1, int(np.ceil(len(group) * 0.20)))
        selected = group.nlargest(count, "score")
        gross_returns.append(float(selected["excess_return"].mean() - group["excess_return"].mean()))
        current = set(selected["code"])
        turnovers.append(0.0 if previous is None else 1.0 - len(current & previous) / max(1, len(current | previous)))
        previous = current
    net_daily = np.asarray(gross_returns, dtype=float) - np.asarray(turnovers, dtype=float) * 0.0015
    net_excess_return = float(np.mean(net_daily) * 252.0) if len(net_daily) else 0.0
    if len(net_daily):
        curve = np.cumprod(1.0 + np.clip(net_daily, -0.99, None))
        drawdowns = curve / np.maximum.accumulate(curve) - 1.0
        max_drawdown = abs(float(np.min(drawdowns)))
    else:
        max_drawdown = 1.0
    annual_turnover = float(np.mean(turnovers) * 252.0) if turnovers else 1_000_000_000.0
    stability_values = [
        value
        for _, group in evaluation.groupby("trade_date", sort=True)
        if (value := _spearman(group["logistic_score"], group["boosting_score"])) is not None
    ]
    if not stability_values:
        fallback_stability = _spearman(evaluation["logistic_score"], evaluation["boosting_score"])
        stability_values = [fallback_stability] if fallback_stability is not None else []
    ablation_stability = float(np.clip((np.mean(stability_values) + 1.0) / 2.0, 0.0, 1.0)) if stability_values else 0.0
    feature_coverage = float(validation.loc[:, feature_columns].notna().mean().mean()) if feature_columns else 0.0
    return {
        "feature_coverage": feature_coverage,
        "point_in_time_audit": bool(point_in_time_audit),
        "oos_predictions": int(len(validation_y)),
        "rank_ic": rank_ic,
        "icir": float(icir),
        "brier_improvement": float(brier_improvement),
        "hit_rate_uplift": float(hit_rate_uplift),
        "auc": auc,
        "net_excess_return": net_excess_return,
        "max_drawdown": max_drawdown,
        "annual_turnover": annual_turnover,
        "ablation_stability": ablation_stability,
    }


def train_model_bundle(
    dataset: pd.DataFrame,
    *,
    feature_columns: list[str] | tuple[str, ...],
    horizon: int,
    random_state: int = 20260713,
) -> ModelBundle:
    required = {"trade_date", "label_end_date", "horizon", "label", "excess_return"}
    if required.difference(dataset.columns):
        raise ValueError("model_missing_columns")
    columns = tuple(feature_columns)
    data = dataset.loc[dataset["horizon"] == horizon].sort_values("trade_date").reset_index(drop=True)
    if len(data) < 120:
        raise ValueError("model_insufficient_samples")
    split_one = int(len(data) * 0.60)
    split_two = int(len(data) * 0.80)
    calibration_start = str(data.iloc[split_one]["trade_date"])
    validation_start = str(data.iloc[split_two]["trade_date"])
    train = data.iloc[: max(0, split_one - horizon)]
    train = train.loc[train["label_end_date"].astype(str) < calibration_start]
    calibration = data.iloc[split_one : max(split_one, split_two - horizon)]
    calibration = calibration.loc[calibration["label_end_date"].astype(str) < validation_start]
    validation = data.iloc[split_two:]
    if train["label"].nunique() < 3 or calibration["label"].nunique() < 2:
        raise ValueError("model_class_coverage")

    train_x, imputation_values = _impute(train, columns)
    calibration_x, _ = _impute(calibration, columns, imputation_values)
    validation_x, _ = _impute(validation, columns, imputation_values)
    train_y = train["label"].astype(str).to_numpy()
    calibration_y = calibration["label"].astype(str).to_numpy()
    validation_y = validation["label"].astype(str).to_numpy()

    scaler = StandardScaler().fit(train_x)
    logistic = LogisticRegression(C=0.5, max_iter=500, random_state=random_state)
    logistic.fit(scaler.transform(train_x), train_y)
    boosting = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_iter=120,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=random_state,
    )
    boosting.fit(train_x, train_y)
    classes = tuple(str(value) for value in logistic.classes_)
    if classes != CLASS_ORDER or tuple(str(value) for value in boosting.classes_) != CLASS_ORDER:
        raise ValueError("model_class_order")

    class_counts = pd.Series(calibration_y).value_counts()
    calibration_method = "isotonic" if all(class_counts.get(name, 0) >= 1000 for name in CLASS_ORDER) else "sigmoid"
    logistic_calibrator = MultiClassCalibrator(calibration_method, CLASS_ORDER).fit(
        logistic.predict_proba(scaler.transform(calibration_x)), calibration_y
    )
    boosting_calibrator = MultiClassCalibrator(calibration_method, CLASS_ORDER).fit(
        boosting.predict_proba(calibration_x), calibration_y
    )
    logistic_probabilities = logistic_calibrator.predict(logistic.predict_proba(scaler.transform(validation_x)))
    boosting_probabilities = boosting_calibrator.predict(boosting.predict_proba(validation_x))
    logistic_loss = float(log_loss(validation_y, logistic_probabilities, labels=list(CLASS_ORDER)))
    boosting_loss = float(log_loss(validation_y, boosting_probabilities, labels=list(CLASS_ORDER)))
    use_boosting = boosting_loss + 0.002 < logistic_loss
    ensemble = (logistic_probabilities + boosting_probabilities) / 2.0 if use_boosting else logistic_probabilities
    ensemble_loss = float(log_loss(validation_y, ensemble, labels=list(CLASS_ORDER)))
    agreement = float(1.0 - np.mean(np.abs(logistic_probabilities - boosting_probabilities)) / 2.0)
    point_in_time_audit = bool(
        str(train["label_end_date"].max()) < calibration_start
        and str(calibration["label_end_date"].max()) < validation_start
        and (validation["label_end_date"].astype(str) >= validation["trade_date"].astype(str)).all()
    )
    activation_metrics = _activation_metrics(
        train_y=train_y,
        validation=validation,
        validation_y=validation_y,
        ensemble=ensemble,
        logistic_probabilities=logistic_probabilities,
        boosting_probabilities=boosting_probabilities,
        feature_columns=columns,
        point_in_time_audit=point_in_time_audit,
    )

    returns = pd.to_numeric(train["excess_return"], errors="coerce")
    return_stats: dict[str, dict[str, float]] = {}
    for class_name in CLASS_ORDER:
        values = returns.loc[train["label"].astype(str) == class_name].dropna().to_numpy(dtype=float)
        return_stats[class_name] = {
            "mean": float(np.mean(values)) if len(values) else 0.0,
            "q10": float(np.quantile(values, 0.10)) if len(values) else 0.0,
            "q50": float(np.quantile(values, 0.50)) if len(values) else 0.0,
            "q90": float(np.quantile(values, 0.90)) if len(values) else 0.0,
        }
    version_payload = {
        "horizon": horizon,
        "features": columns,
        "train_end": str(train.iloc[-1]["trade_date"]),
        "random_state": random_state,
    }
    version = hashlib.sha256(json.dumps(version_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return ModelBundle(
        horizon=horizon,
        feature_columns=columns,
        class_order=CLASS_ORDER,
        imputation_values=imputation_values,
        scaler=scaler,
        logistic_model=logistic,
        logistic_calibrator=logistic_calibrator,
        boosting_model=boosting,
        boosting_calibrator=boosting_calibrator,
        use_boosting=use_boosting,
        metrics={
            "log_loss": ensemble_loss,
            "brier_score": _multiclass_brier(ensemble, validation_y, CLASS_ORDER),
            "logistic_log_loss": logistic_loss,
            "boosting_log_loss": boosting_loss,
            "model_agreement": agreement,
            "calibration_quality": float(np.clip(1.0 - ensemble_loss / np.log(3.0), 0.0, 1.0)),
            **activation_metrics,
        },
        split_dates={
            "train_start": str(train.iloc[0]["trade_date"]),
            "train_end": str(train.iloc[-1]["trade_date"]),
            "calibration_start": str(calibration.iloc[0]["trade_date"]),
            "calibration_end": str(calibration.iloc[-1]["trade_date"]),
            "validation_start": str(validation.iloc[0]["trade_date"]),
            "validation_end": str(validation.iloc[-1]["trade_date"]),
        },
        sample_support=len(train),
        calibration_method=calibration_method,
        model_version=version,
        return_stats=return_stats,
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
        "feature_columns": list(bundle.feature_columns),
        "class_order": list(bundle.class_order),
        "calibration_method": bundle.calibration_method,
        "use_boosting": bundle.use_boosting,
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
