"""Shared deterministic fill and transaction-cost calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionFill:
    reference_price: float
    execution_price: float
    shares: int
    side: str
    gross_amount: float
    commission: float
    stamp_tax: float
    slippage: float
    cash_delta: float
    impact_bps: float

    @property
    def total_cost(self) -> float:
        return self.commission + self.stamp_tax + self.slippage


def _baseline_slippage_bps(trading: Mapping[str, Any]) -> float:
    if trading.get("slippage_bps") is not None:
        return max(float(trading.get("slippage_bps") or 0.0), 0.0)
    return max(float(trading.get("slippage_rate") or 0.0) * 10_000.0, 0.0)


def calculate_execution_fill(
    *,
    reference_price: float,
    shares: int,
    side: str,
    trading: Mapping[str, Any],
    impact_bps: float = 0.0,
) -> ExecutionFill:
    """Calculate the exact cash effect used by research and paper execution."""

    normalized_side = str(side).strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise ValueError("execution_side")
    quantity = int(shares)
    price = float(reference_price)
    if quantity <= 0 or price <= 0:
        raise ValueError("execution_quantity_or_price")
    effective_bps = max(_baseline_slippage_bps(trading), float(impact_bps or 0.0))
    multiplier = 1.0 + effective_bps / 10_000.0 if normalized_side == "buy" else 1.0 - effective_bps / 10_000.0
    execution_price = round(price * multiplier, 4)
    gross = quantity * execution_price
    commission_rate = max(float(trading.get("commission_rate") or 0.0), 0.0)
    minimum = max(float(trading.get("min_commission") or 0.0), 0.0)
    commission = max(gross * commission_rate, minimum)
    stamp_rate = max(float(trading.get("stamp_tax_rate") or 0.0), 0.0)
    stamp = gross * stamp_rate if normalized_side == "sell" else 0.0
    slippage = abs(execution_price - price) * quantity
    cash_delta = -(gross + commission) if normalized_side == "buy" else gross - commission - stamp
    return ExecutionFill(
        reference_price=price,
        execution_price=execution_price,
        shares=quantity,
        side=normalized_side,
        gross_amount=gross,
        commission=commission,
        stamp_tax=stamp,
        slippage=slippage,
        cash_delta=cash_delta,
        impact_bps=effective_bps,
    )


__all__ = ["ExecutionFill", "calculate_execution_fill"]
