"""Validated records shared by the research pipeline and dashboard API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_HORIZONS = frozenset({3, 5, 10, 20})


@dataclass(frozen=True)
class PredictionRecord:
    code: str
    as_of: str
    horizon: int
    p_up: float
    p_flat: float
    p_down: float
    confidence: float = 0.0
    expected_absolute_return: float | None = None
    expected_excess_return: float | None = None
    return_q10: float | None = None
    return_q50: float | None = None
    return_q90: float | None = None
    regime: str = "unknown"
    reasons: tuple[str, ...] = field(default_factory=tuple)
    invalidation: tuple[str, ...] = field(default_factory=tuple)
    model_version: str = ""
    feature_snapshot_id: str = ""
    research_status: str = "research"
    active_status: str = "inactive"
    classifier_status: str = "inactive"
    ranker_status: str = "inactive"
    portfolio_status: str = "inactive"
    active_roles: tuple[str, ...] = field(default_factory=tuple)
    invalidated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.horizon not in SUPPORTED_HORIZONS:
            raise ValueError("prediction_horizon")
        probabilities = (self.p_up, self.p_flat, self.p_down)
        if any(value < 0.0 or value > 1.0 for value in probabilities):
            raise ValueError("prediction_probability_range")
        if abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError("prediction_probability_sum")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("prediction_confidence_range")
        if not str(self.code).strip():
            raise ValueError("prediction_code")
        if not str(self.as_of).strip():
            raise ValueError("prediction_as_of")
