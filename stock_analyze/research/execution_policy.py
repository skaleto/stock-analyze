"""Liquidity-aware execution cost estimates shared by simulators."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ExecutionCostEstimate:
    baseline_bps: float
    impact_bps: float
    total_bps: float
    participation_rate: float | None
    liquidity_status: str
    capped: bool


def estimate_execution_cost(
    *,
    order_value: float,
    avg_daily_amount: float | None,
    volatility: float | None,
    baseline_bps: float,
    max_bps: float = 80.0,
) -> ExecutionCostEstimate:
    """Estimate slippage and expose the evidence used by the cost model.

    Missing liquidity fails closed at the cap. Volatility is annualized and
    clipped to a plausible range so a bad upstream field cannot create either
    free execution or unbounded costs.
    """

    baseline = max(float(baseline_bps), 0.0)
    cap = max(float(max_bps), baseline)
    try:
        daily_amount = float(avg_daily_amount) if avg_daily_amount is not None else 0.0
    except (TypeError, ValueError):
        daily_amount = 0.0
    if not math.isfinite(daily_amount) or daily_amount <= 0.0:
        return ExecutionCostEstimate(
            baseline_bps=baseline,
            impact_bps=max(cap - baseline, 0.0),
            total_bps=cap,
            participation_rate=None,
            liquidity_status="missing",
            capped=True,
        )
    try:
        annual_volatility = float(volatility) if volatility is not None else 0.35
    except (TypeError, ValueError):
        annual_volatility = 0.35
    if not math.isfinite(annual_volatility) or annual_volatility <= 0.0:
        annual_volatility = 0.35
    annual_volatility = min(max(annual_volatility, 0.05), 1.50)
    participation = min(max(abs(float(order_value)) / daily_amount, 0.0), 1.0)
    impact_bps = 10_000.0 * 0.25 * annual_volatility * math.sqrt(participation)
    uncapped = max(baseline + impact_bps, baseline)
    total = float(min(uncapped, cap))
    return ExecutionCostEstimate(
        baseline_bps=baseline,
        impact_bps=max(total - baseline, 0.0),
        total_bps=total,
        participation_rate=float(participation),
        liquidity_status="available",
        capped=bool(uncapped >= cap),
    )


def estimate_market_impact_bps(
    *,
    order_value: float,
    avg_daily_amount: float | None,
    volatility: float | None,
    baseline_bps: float,
    max_bps: float = 80.0,
) -> float:
    """Compatibility wrapper returning the total estimated slippage in bps."""

    return estimate_execution_cost(
        order_value=order_value,
        avg_daily_amount=avg_daily_amount,
        volatility=volatility,
        baseline_bps=baseline_bps,
        max_bps=max_bps,
    ).total_bps


__all__ = [
    "ExecutionCostEstimate",
    "estimate_execution_cost",
    "estimate_market_impact_bps",
]
