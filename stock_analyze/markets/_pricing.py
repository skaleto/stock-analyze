"""Market-neutral numeric helpers shared by active providers."""

from __future__ import annotations

from typing import Any

import pandas as pd


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def pct_change(closes: pd.Series, lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    latest = float(closes.iloc[-1])
    prior = float(closes.iloc[-lookback - 1])
    if prior <= 0:
        return None
    return latest / prior - 1.0


def trailing_volatility(closes: pd.Series, lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    returns = closes.pct_change().dropna().iloc[-lookback:]
    return None if returns.empty else float(returns.std())


def apply_slippage(price: float | None, side: str, slippage_bps: float) -> float | None:
    if price is None:
        return None
    slippage = slippage_bps / 10_000.0
    if side == "buy":
        return price * (1.0 + slippage)
    if side == "sell":
        return price * (1.0 - slippage)
    return price


__all__ = ["apply_slippage", "pct_change", "safe_float", "trailing_volatility"]
