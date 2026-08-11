"""Point-in-time economic calibration for cross-sectional research scores."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from statistics import NormalDist

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True)
class ScoreCalibration:
    expected_excess_return: np.ndarray
    uncertainty_bps: np.ndarray
    confidence: np.ndarray
    calibrator_hash: str
    effective_date_count: float


def _stable_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def fit_predict_score_calibration(
    calibration: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    score_column: str,
    return_column: str,
    horizon: int,
    bins: int,
    minimum_dates: int,
) -> ScoreCalibration:
    """Fit on date-level bucket means and predict later rows without leakage."""

    required = {"trade_date", score_column, return_column}
    missing = required.difference(calibration.columns)
    if missing:
        raise ValueError(
            "score_calibration_missing_columns:" + ",".join(sorted(missing))
        )
    if score_column not in validation.columns:
        raise ValueError(f"score_calibration_validation_missing:{score_column}")
    if int(horizon) <= 0:
        raise ValueError("score_calibration_horizon")
    if int(bins) < 3:
        raise ValueError("score_calibration_bins")

    frame = calibration.loc[:, ["trade_date", score_column, return_column]].copy()
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    frame[return_column] = pd.to_numeric(frame[return_column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    date_count = int(frame["trade_date"].nunique())
    if date_count < int(minimum_dates):
        raise ValueError(
            f"score_calibration_insufficient_dates:{date_count}:{int(minimum_dates)}"
        )
    if frame[score_column].nunique() < 3:
        raise ValueError("score_calibration_score_variation")

    quantiles = np.linspace(0.0, 1.0, int(bins) + 1)
    edges = np.unique(
        np.quantile(frame[score_column].to_numpy(dtype=float), quantiles)
    )
    if len(edges) < 4:
        raise ValueError("score_calibration_score_variation")
    interior = edges[1:-1]
    frame["_bucket"] = np.searchsorted(
        interior,
        frame[score_column].to_numpy(dtype=float),
        side="right",
    )

    daily = (
        frame.groupby(["trade_date", "_bucket"], sort=True, observed=True)
        .agg(
            score=(score_column, "mean"),
            excess_return=(return_column, "mean"),
        )
        .reset_index()
    )
    summary = (
        daily.groupby("_bucket", sort=True, observed=True)
        .agg(
            score=("score", "mean"),
            expected=("excess_return", "mean"),
            daily_std=("excess_return", "std"),
            dates=("trade_date", "nunique"),
        )
        .reset_index(drop=True)
    )
    if len(summary) < 3:
        raise ValueError("score_calibration_bucket_coverage")

    centers = summary["score"].to_numpy(dtype=float)
    observed = summary["expected"].to_numpy(dtype=float)
    date_observations = summary["dates"].to_numpy(dtype=float)
    isotonic = IsotonicRegression(increasing=True, out_of_bounds="clip")
    isotonic.fit(centers, observed, sample_weight=date_observations)

    effective_by_bucket = np.maximum(date_observations / float(horizon), 1.0)
    daily_std = summary["daily_std"].fillna(0.0).to_numpy(dtype=float)
    standard_error = np.maximum(daily_std / np.sqrt(effective_by_bucket), 0.0)

    validation_score = pd.to_numeric(
        validation[score_column], errors="coerce"
    ).replace([np.inf, -np.inf], np.nan)
    fallback_score = float(np.median(centers))
    values = validation_score.fillna(fallback_score).to_numpy(dtype=float)
    expected = np.asarray(isotonic.predict(values), dtype=float)
    uncertainty = np.interp(
        np.clip(values, float(centers.min()), float(centers.max())),
        centers,
        standard_error,
    )
    confidence = np.asarray([
        1.0
        if error <= 1e-12 and estimate > 0.0
        else 0.0
        if error <= 1e-12 and estimate < 0.0
        else 0.5
        if error <= 1e-12
        else NormalDist().cdf(float(estimate) / float(error))
        for estimate, error in zip(expected, uncertainty)
    ], dtype=float)

    hash_payload = {
        "method": "date_bucket_isotonic_v1",
        "horizon": int(horizon),
        "bins": int(bins),
        "minimum_dates": int(minimum_dates),
        "centers": np.round(centers, 12).tolist(),
        "fitted": np.round(isotonic.predict(centers), 12).tolist(),
        "standard_error": np.round(standard_error, 12).tolist(),
    }
    return ScoreCalibration(
        expected_excess_return=expected,
        uncertainty_bps=np.asarray(uncertainty * 10_000.0, dtype=float),
        confidence=np.clip(confidence, 0.0, 1.0),
        calibrator_hash=_stable_hash(hash_payload),
        effective_date_count=float(date_count / float(horizon)),
    )


__all__ = ["ScoreCalibration", "fit_predict_score_calibration"]
