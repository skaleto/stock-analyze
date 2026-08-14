"""Frozen economic and execution gates for transparent strategy trials."""

from __future__ import annotations

from typing import Any, Mapping


def _number(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def evaluate_execution_viability(
    metrics: Mapping[str, Any],
    cost_stress_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate executable quality without treating intentional cash as failure."""

    checks = {
        "attribution_status": str(metrics.get("attribution_status") or "") == "reconciled",
        "target_fill_ratio": _number(metrics.get("target_fill_ratio"), -1.0) >= 0.95,
        "missing_liquidity_notional_ratio": (
            _number(metrics.get("missing_liquidity_notional_ratio"), 1.0) <= 0.10
        ),
        "impact_capped_notional_ratio": (
            _number(metrics.get("impact_capped_notional_ratio"), 1.0) <= 0.10
        ),
        "cost_stress_net_excess_return": (
            _number(cost_stress_metrics.get("net_excess_return"), -1.0) >= 0.0
        ),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not reasons,
        "checks": checks,
        "reasons": reasons,
        "evidence": {
            "strategic_risky_exposure": _number(
                metrics.get("strategic_risky_exposure"), 0.0
            ),
            "target_fill_ratio": _number(metrics.get("target_fill_ratio"), -1.0),
            "cash_drag": _number(metrics.get("cash_drag"), 1.0),
            "base_net_excess_return": _number(metrics.get("net_excess_return"), -1.0),
            "cost_stress_net_excess_return": _number(
                cost_stress_metrics.get("net_excess_return"), -1.0
            ),
        },
    }


__all__ = ["evaluate_execution_viability"]
