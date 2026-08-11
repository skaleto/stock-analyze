"""Liquidity-aware execution cost estimates shared by simulators."""

from __future__ import annotations

import math


def estimate_market_impact_bps(
    *,
    order_value: float,
    avg_daily_amount: float | None,
    volatility: float | None,
    baseline_bps: float,
    max_bps: float = 80.0,
) -> float:
    """Estimate total slippage with a capped square-root impact model.

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
        return cap
    try:
        annual_volatility = float(volatility) if volatility is not None else 0.35
    except (TypeError, ValueError):
        annual_volatility = 0.35
    if not math.isfinite(annual_volatility) or annual_volatility <= 0.0:
        annual_volatility = 0.35
    annual_volatility = min(max(annual_volatility, 0.05), 1.50)
    participation = min(max(abs(float(order_value)) / daily_amount, 0.0), 1.0)
    impact_bps = 10_000.0 * 0.25 * annual_volatility * math.sqrt(participation)
    return float(min(max(baseline + impact_bps, baseline), cap))


__all__ = ["estimate_market_impact_bps"]
