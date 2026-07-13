"""Prediction records, confidence scoring, and interpretable reasons."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .models import ModelBundle
from .schemas import PredictionRecord


def compute_confidence(
    *,
    calibration_quality: float,
    sample_support: int,
    model_agreement: float,
    data_quality: float,
    regime_stability: float,
) -> float:
    support_score = min(1.0, max(0.0, sample_support / 500.0))
    confidence = (
        0.30 * calibration_quality
        + 0.20 * support_score
        + 0.20 * model_agreement
        + 0.15 * data_quality
        + 0.15 * regime_stability
    )
    if sample_support < 100:
        confidence = min(confidence, 0.49)
    return float(np.clip(confidence, 0.0, 1.0))


def _reason_text(contributions: list[tuple[str, float]]) -> tuple[str, ...]:
    reasons = []
    for name, value in contributions[:3]:
        direction = "正向" if value >= 0 else "负向"
        reasons.append(f"{name} {direction}贡献 {abs(value):.3f}")
    return tuple(reasons)


def generate_predictions(
    bundle: ModelBundle,
    features: pd.DataFrame,
    *,
    as_of: str,
    horizon: int,
    regime: str,
    data_quality: float,
    regime_stability: float,
    feature_snapshot_id: str,
    active_status: str = "inactive",
) -> list[PredictionRecord]:
    if horizon != bundle.horizon:
        raise ValueError("prediction_model_horizon")
    probabilities = bundle.predict_proba(features)
    logistic, boosting = bundle.component_probabilities(features)
    records: list[PredictionRecord] = []
    for index, (_, row) in enumerate(features.iterrows()):
        probability_by_class = dict(zip(bundle.class_order, probabilities[index]))
        agreement = float(1.0 - np.abs(logistic[index] - boosting[index]).sum() / 2.0)
        row_quality = float(data_quality) * float(row.loc[list(bundle.feature_columns)].notna().mean())
        confidence = compute_confidence(
            calibration_quality=bundle.metrics.get("calibration_quality", 0.0),
            sample_support=bundle.sample_support,
            model_agreement=agreement,
            data_quality=row_quality,
            regime_stability=regime_stability,
        )
        expected = sum(probability_by_class[name] * bundle.return_stats[name]["mean"] for name in bundle.class_order)
        quantiles = {
            key: sum(probability_by_class[name] * bundle.return_stats[name][key] for name in bundle.class_order)
            for key in ("q10", "q50", "q90")
        }
        code = str(row.get("code", row.get("ts_code", ""))).split(".")[0]
        reasons = _reason_text(bundle.logistic_contributions(row, "up"))
        invalidation = (
            "数据完整度低于 70%" if row_quality < 0.70 else "模型状态或市场状态发生变化",
            "下行概率超过上行概率",
        )
        records.append(
            PredictionRecord(
                code=code,
                as_of=as_of,
                horizon=horizon,
                p_up=float(probability_by_class["up"]),
                p_flat=float(probability_by_class["flat"]),
                p_down=float(probability_by_class["down"]),
                confidence=confidence,
                expected_absolute_return=float(expected),
                expected_excess_return=float(expected),
                return_q10=float(quantiles["q10"]),
                return_q50=float(quantiles["q50"]),
                return_q90=float(quantiles["q90"]),
                regime=regime,
                reasons=reasons,
                invalidation=invalidation,
                model_version=bundle.model_version,
                feature_snapshot_id=feature_snapshot_id,
                active_status=active_status,
                metadata={"model_agreement": agreement, "data_quality": row_quality},
            )
        )
    return records
