"""Training-only score-to-return calibration for economic decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import MISSING, dataclass, fields

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


SUPPORTED_CALIBRATION_VERSIONS = frozenset({
    "clustered-date-mean-se-v2",
    "clustered-date-isotonic-mean-se-v3",
})


@dataclass(frozen=True)
class EdgeCalibrator:
    available: bool
    boundaries: tuple[float, ...]
    expected_returns: tuple[float, ...]
    prediction_std: tuple[float, ...]
    bucket_date_support: tuple[int, ...]
    fit_max_date: str
    alpha_half_life_days: float
    outcome_dispersion: tuple[float, ...] = ()
    mean_standard_error: tuple[float, ...] = ()
    calibration_version: str = "legacy-outcome-std-v1"
    reason: str = ""
    score_centers: tuple[float, ...] = ()
    raw_expected_returns: tuple[float, ...] = ()
    effective_date_support: tuple[float, ...] = ()
    projection_adjustment_mae: float = 0.0

    @property
    def supports_prediction(self) -> bool:
        return bool(
            self.available
            and self.calibration_version in SUPPORTED_CALIBRATION_VERSIONS
            and self.expected_returns
        )

    @property
    def calibrator_hash(self) -> str:
        payload = {}
        for field in fields(self):
            if hasattr(self, field.name):
                payload[field.name] = getattr(self, field.name)
            elif field.default is not MISSING:
                payload[field.name] = field.default
            else:
                payload[field.name] = None
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def predict_distribution(
        self,
        scores: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        values = np.asarray(scores, dtype=float)
        expected = np.zeros(len(values), dtype=float)
        uncertainty = np.ones(len(values), dtype=float)
        if (
            not self.supports_prediction
        ):
            return expected, uncertainty
        valid = np.isfinite(values)
        if not valid.any():
            return expected, uncertainty
        standard_errors = (
            self.mean_standard_error
            if len(self.mean_standard_error) == len(self.expected_returns)
            else self.prediction_std
        )
        if (
            self.calibration_version == "clustered-date-isotonic-mean-se-v3"
            and len(self.score_centers) == len(self.expected_returns)
        ):
            centers = np.asarray(self.score_centers, dtype=float)
            if len(centers) < 2 or not bool((np.diff(centers) > 0.0).all()):
                return expected, uncertainty
            expected[valid] = np.interp(
                values[valid],
                centers,
                np.asarray(self.expected_returns, dtype=float),
            )
            uncertainty[valid] = np.interp(
                values[valid],
                centers,
                np.asarray(standard_errors, dtype=float),
            )
        else:
            buckets = np.digitize(
                values[valid],
                np.asarray(self.boundaries, dtype=float),
            )
            buckets = np.clip(buckets, 0, len(self.expected_returns) - 1)
            expected[valid] = np.asarray(self.expected_returns, dtype=float)[buckets]
            uncertainty[valid] = np.asarray(standard_errors, dtype=float)[buckets]
        return expected, uncertainty


def unavailable_edge_calibrator(reason: str, fit_max_date: str = "") -> EdgeCalibrator:
    return EdgeCalibrator(
        available=False,
        boundaries=(),
        expected_returns=(),
        prediction_std=(),
        bucket_date_support=(),
        fit_max_date=str(fit_max_date),
        alpha_half_life_days=0.0,
        outcome_dispersion=(),
        mean_standard_error=(),
        calibration_version="clustered-date-isotonic-mean-se-v3",
        reason=str(reason),
        score_centers=(),
        raw_expected_returns=(),
        effective_date_support=(),
        projection_adjustment_mae=0.0,
    )


def _alpha_half_life(frame: pd.DataFrame) -> float:
    daily = []
    for _, group in frame.groupby("trade_date", sort=True):
        value = group["score"].corr(group["realized_return"], method="spearman")
        if pd.notna(value):
            daily.append(float(value))
    if len(daily) < 3:
        return 1.0
    if float(np.std(daily, ddof=0)) <= 1e-12:
        return 1.0
    correlation = pd.Series(daily).autocorr(lag=1)
    if pd.isna(correlation) or float(correlation) <= 0.0:
        return 1.0
    bounded = min(max(float(correlation), 1e-6), 0.999999)
    return float(np.clip(np.log(0.5) / np.log(bounded), 1.0, 60.0))


def fit_edge_calibrator(
    predictions: pd.DataFrame,
    realized_returns: pd.Series | np.ndarray,
    *,
    score_column: str = "score",
    buckets: int = 5,
    minimum_dates_per_bucket: int = 20,
    horizon: int = 1,
) -> EdgeCalibrator:
    if "trade_date" not in predictions.columns or score_column not in predictions.columns:
        raise ValueError("edge_calibration_missing_columns")
    frame = pd.DataFrame({
        "trade_date": predictions["trade_date"].astype(str).reset_index(drop=True),
        "score": pd.to_numeric(predictions[score_column], errors="coerce").reset_index(drop=True),
        "realized_return": pd.to_numeric(
            pd.Series(realized_returns), errors="coerce"
        ).reset_index(drop=True),
    }).dropna()
    fit_max_date = str(frame["trade_date"].max()) if not frame.empty else ""
    if int(horizon) <= 0:
        raise ValueError("edge_calibration_horizon")
    if len(frame) < max(int(buckets) * 10, 30) or frame["score"].nunique() < 3:
        return unavailable_edge_calibrator("insufficient_calibration_support", fit_max_date)
    try:
        assignments, edges = pd.qcut(
            frame["score"],
            q=max(3, int(buckets)),
            labels=False,
            retbins=True,
            duplicates="drop",
        )
    except ValueError:
        return unavailable_edge_calibrator("insufficient_calibration_buckets", fit_max_date)
    frame["bucket"] = pd.to_numeric(assignments, errors="coerce")
    frame = frame.dropna(subset=["bucket"])
    date_bucket_means = (
        frame.groupby(["bucket", "trade_date"], sort=True, as_index=False)
        .agg(
            realized_return=("realized_return", "mean"),
            score=("score", "mean"),
        )
    )
    date_grouped = date_bucket_means.groupby("bucket", sort=True)
    raw_means = date_grouped["realized_return"].mean()
    score_centers = date_grouped["score"].mean().reindex(raw_means.index)
    date_support = date_grouped["trade_date"].nunique().reindex(raw_means.index)
    if (
        len(raw_means) < 3
        or bool(date_support.lt(max(1, int(minimum_dates_per_bucket))).any())
    ):
        return unavailable_edge_calibrator("insufficient_bucket_date_support", fit_max_date)
    centers = score_centers.to_numpy(dtype=float)
    raw_expected = raw_means.to_numpy(dtype=float)
    if not bool((np.diff(centers) > 0.0).all()):
        return unavailable_edge_calibrator("insufficient_calibration_buckets", fit_max_date)
    projected = np.asarray(
        IsotonicRegression(increasing=True, out_of_bounds="clip").fit_transform(
            centers,
            raw_expected,
            sample_weight=date_support.to_numpy(dtype=float),
        ),
        dtype=float,
    )
    if float(np.ptp(projected)) <= 1e-12:
        return unavailable_edge_calibrator("calibrated_curve_flat", fit_max_date)
    if float(projected[-1]) <= 0.0:
        return unavailable_edge_calibrator("calibrated_curve_nonpositive", fit_max_date)
    standard_deviations = (
        frame.groupby("bucket", sort=True)["realized_return"]
        .std(ddof=1)
        .reindex(raw_means.index)
        .fillna(0.0)
    )
    effective_support = np.maximum(
        date_support.to_numpy(dtype=float) / float(int(horizon)),
        1.0,
    )
    clustered_standard_error = (
        date_grouped["realized_return"].std(ddof=1).reindex(raw_means.index).fillna(0.0)
        / np.sqrt(effective_support)
    )
    return EdgeCalibrator(
        available=True,
        boundaries=tuple(float(value) for value in np.asarray(edges[1:-1], dtype=float)),
        expected_returns=tuple(float(value) for value in projected),
        prediction_std=tuple(
            float(value) for value in clustered_standard_error.to_numpy(dtype=float)
        ),
        bucket_date_support=tuple(int(value) for value in date_support.to_numpy(dtype=int)),
        fit_max_date=fit_max_date,
        alpha_half_life_days=_alpha_half_life(frame),
        outcome_dispersion=tuple(
            float(value) for value in standard_deviations.to_numpy(dtype=float)
        ),
        mean_standard_error=tuple(
            float(value) for value in clustered_standard_error.to_numpy(dtype=float)
        ),
        calibration_version="clustered-date-isotonic-mean-se-v3",
        reason="",
        score_centers=tuple(float(value) for value in centers),
        raw_expected_returns=tuple(float(value) for value in raw_expected),
        effective_date_support=tuple(float(value) for value in effective_support),
        projection_adjustment_mae=float(np.mean(np.abs(projected - raw_expected))),
    )


__all__ = [
    "EdgeCalibrator",
    "SUPPORTED_CALIBRATION_VERSIONS",
    "fit_edge_calibrator",
    "unavailable_edge_calibrator",
]
