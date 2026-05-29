"""Exceptions raised by the backtest layer."""
from __future__ import annotations

from .types import BacktestMetrics


class BacktestFloorBreach(Exception):
    """Raised when an overlay's validation-window backtest fails a floor threshold.

    Carries the ``breach_type`` (string ID for the rule that failed) and the
    full ``BacktestMetrics`` so callers can produce a helpful breach log.
    """

    BREACH_TYPES = (
        "max_drawdown_exceeded",
        "sharpe_below_floor",
        "cum_return_below_floor",
    )

    def __init__(self, breach_type: str, metrics: BacktestMetrics) -> None:
        if breach_type not in self.BREACH_TYPES:
            raise ValueError(
                f"unknown breach_type={breach_type!r}; expected one of {self.BREACH_TYPES}"
            )
        self.breach_type = breach_type
        self.metrics = metrics
        super().__init__(
            f"Backtest floor breach: {breach_type}; metrics={metrics}"
        )


class BacktestStructuralBreach(Exception):
    """Raised when backtest scoring is structurally degenerate.

    Distinct from ``BacktestFloorBreach`` (which is about *returns*): this
    catches the failure mode where the scoring itself stops producing a
    usable ranking — e.g. a refactor or broken overlay makes (nearly) every
    candidate score identically, so "top-N selection" is meaningless and the
    floor gate would pass it by accident (it buys an arbitrary slice of a
    tied universe and earns ordinary returns). Per OpenSpec change
    bridge-factor-pipeline-into-backtest §5.

    Carries ``detail`` (the sampled signal day + measured score uniqueness)
    so the breach log can point at exactly which day went degenerate.
    """

    def __init__(self, detail: dict) -> None:
        self.detail = detail
        super().__init__(f"Backtest structural breach: {detail}")
