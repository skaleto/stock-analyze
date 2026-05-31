"""US data provider — thin yfinance subclass.

Companion to the HK provider; the shared logic lives in
``stock_analyze.markets._yfinance_base`` (extracted per OpenSpec change
``extract-yfinance-provider-base``). This module keeps only the
US-specific surface: the snapshot/quote dataclasses, the module-level
yfinance call seam (so tests can ``patch`` it), and a thin
``YFinanceUSProvider`` subclass. US tickers are bare (no ``.HK`` suffix);
lot size is 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..mechanics import SLIPPAGE_BPS
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
# US-specific dataclasses
# ---------------------------------------------------------------------------


@dataclass
class USPriceSnapshot:
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
class USExecutionQuote:
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
    """Pull yfinance ``Ticker.info`` for a US symbol."""
    import yfinance as yf

    return dict(yf.Ticker(symbol).info)


def _fetch_ticker_history(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    period: str = "3mo",
) -> pd.DataFrame:
    """Pull yfinance ``Ticker.history`` for a US symbol."""
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


class YFinanceUSProvider(YFinanceProviderBase):
    """US market data provider backed by yfinance.

    Symmetric with YFinanceHKProvider — only ticker namespace (bare),
    slippage constant, and lot rule (always 1) differ.
    """

    snapshot_cls = USPriceSnapshot
    quote_cls = USExecutionQuote
    slippage_bps = SLIPPAGE_BPS

    def _resolve_universe(self, scope: str) -> list[str]:
        return resolve_universe(scope)

    def lot_size(self, code: str) -> int:
        return 1  # US has no lot constraint in v1

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
) -> YFinanceUSProvider:
    return YFinanceUSProvider(cache_dir=cache_dir, offline=offline, as_of=as_of)


__all__ = [
    "USExecutionQuote",
    "USPriceSnapshot",
    "YFinanceUSProvider",
    "make_provider",
]
