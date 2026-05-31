"""HK data provider — thin yfinance subclass.

The shared fetch / cache / snapshot / execution-quote logic + math
helpers now live in ``stock_analyze.markets._yfinance_base`` (extracted
per OpenSpec change ``extract-yfinance-provider-base``). This module keeps
only the HK-specific surface: the snapshot/quote dataclasses, the
module-level yfinance call seam (so tests can ``patch`` it), and a thin
``YFinanceHKProvider`` subclass.

All network calls go through :func:`_fetch_ticker_info` /
:func:`_fetch_ticker_history` so tests stub them via
``unittest.mock.patch`` at one location.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..mechanics import SLIPPAGE_BPS, lot_size_for
from ..universe import resolve_universe
from ..._yfinance_base import (
    YFinanceProviderBase,
    # Re-exported for any stray importer that referenced the old local helpers.
    _apply_slippage,
    _pct_change,
    _pd_index_isoformat,
    _safe_float,
    _trailing_volatility,
)


# ---------------------------------------------------------------------------
# HK-specific dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HKPriceSnapshot:
    """Per-stock snapshot row used by strategy + simulator."""

    code: str
    trade_date: str | None
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    pe: float | None
    pb: float | None
    market_cap: float | None
    dividend_yield: float | None
    momentum_20: float | None
    momentum_60: float | None
    low_volatility_60: float | None
    industry: str | None = None
    paused: bool = False
    source: str = "yfinance"
    warning: str = ""


@dataclass
class HKExecutionQuote:
    """Execution-price quote for the simulator's order matching."""

    code: str
    trade_date: str | None
    price: float | None
    paused: bool = False
    source: str = "yfinance"
    reason: str = ""


# ---------------------------------------------------------------------------
# Network seam (one place for tests to mock)
# ---------------------------------------------------------------------------


def _fetch_ticker_info(symbol: str) -> dict[str, Any]:
    """Pull yfinance ``Ticker.info`` for the given HK symbol.

    Module-level (not a method) so tests patch it with a single
    ``mock.patch`` call regardless of which method invokes it. The
    provider subclass routes through this via ``_fetch_info``.
    """
    import yfinance as yf  # lazy import so test envs without it still import

    return dict(yf.Ticker(symbol).info)


def _fetch_ticker_history(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    period: str = "3mo",
) -> pd.DataFrame:
    """Pull yfinance ``Ticker.history`` for the given HK symbol."""
    import yfinance as yf

    kwargs: dict[str, Any] = {"auto_adjust": True}
    if start is not None and end is not None:
        kwargs.update({"start": start, "end": end})
    else:
        kwargs["period"] = period
    return yf.Ticker(symbol).history(**kwargs)


# ---------------------------------------------------------------------------
# Provider (thin subclass of the shared base)
# ---------------------------------------------------------------------------


class YFinanceHKProvider(YFinanceProviderBase):
    """HK market data provider backed by yfinance.

    All heavy lifting is inherited from ``YFinanceProviderBase``; this
    subclass only binds the HK dataclasses, slippage constant, universe
    resolver, lot rule, and the module-level fetch seam.
    """

    snapshot_cls = HKPriceSnapshot
    quote_cls = HKExecutionQuote
    slippage_bps = SLIPPAGE_BPS

    def _resolve_universe(self, scope: str) -> list[str]:
        return resolve_universe(scope)

    def lot_size(self, code: str) -> int:
        return lot_size_for(code)

    def _fetch_info(self, symbol: str) -> dict[str, Any]:
        return _fetch_ticker_info(symbol)

    def _fetch_history(self, symbol: str, period: str = "3mo") -> pd.DataFrame:
        return _fetch_ticker_history(symbol, period=period)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_provider(
    cache_dir: Path | str | None = None,
    offline: bool = False,
    as_of: str | None = None,
) -> YFinanceHKProvider:
    """Construct a YFinanceHKProvider.

    Signature matches A-share's ``make_provider`` so per-market callers
    can dispatch without checking the market id.
    """
    return YFinanceHKProvider(cache_dir=cache_dir, offline=offline, as_of=as_of)


__all__ = [
    "HKExecutionQuote",
    "HKPriceSnapshot",
    "YFinanceHKProvider",
    "make_provider",
]
