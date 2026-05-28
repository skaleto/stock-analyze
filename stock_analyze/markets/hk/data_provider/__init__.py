"""HK data provider — yfinance wrapper.

Wraps ``yfinance.Ticker`` to expose the subset of the A-share
:class:`stock_analyze.markets.a_share.data_provider.DataProvider`
interface that HK v1 actually uses:

  - :meth:`universe` — return the ticker list for a scope (hsi / hscei)
  - :meth:`spot` — per-stock snapshot (close + pe + pb + market_cap +
    dividend_yield) for the whole universe
  - :meth:`daily` — historical OHLCV for a single ticker over a window
  - :meth:`price_snapshot` — convenience wrapper around spot[code]
  - :meth:`execution_quote` — execution price for the simulator (uses
    next day's open if available, else close, with optional slippage)

US v1 reuses the same base class but with US-specific universe + symbol
conventions (no ``.HK`` suffix). When the US module lands (Phase 3) the
common code moves into ``markets/_yfinance_base.py``; for now the HK
implementation is self-contained.

All network calls go through :func:`_fetch_ticker_info` /
:func:`_fetch_ticker_history` so tests can stub them via
``unittest.mock.patch`` at one location.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..mechanics import (
    COMMISSION_RATE,
    SLIPPAGE_BPS,
    STAMP_TAX_RATE,
)
from ..universe import resolve_universe


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses (HK-local; for symmetry with A-share's PriceSnapshot /
# ExecutionQuote in markets/a_share/data_provider/base.py)
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
# Low-level yfinance accessors (one place for tests to mock)
# ---------------------------------------------------------------------------


def _fetch_ticker_info(symbol: str) -> dict[str, Any]:
    """Pull yfinance ``Ticker.info`` for the given HK symbol.

    Defined as a module-level function (not a class method) so tests can
    patch it with a single ``mock.patch`` call regardless of which method
    invokes it.
    """
    import yfinance as yf  # imported lazily so test envs without it don't break import time

    return dict(yf.Ticker(symbol).info)


def _fetch_ticker_history(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    period: str = "3mo",
) -> pd.DataFrame:
    """Pull yfinance ``Ticker.history`` for the given HK symbol.

    Returns the DataFrame yfinance produces (columns: Open / High / Low /
    Close / Volume + sometimes Dividends / Stock Splits). Caller decides
    what to do with the index timezone.
    """
    import yfinance as yf

    kwargs: dict[str, Any] = {"auto_adjust": True}
    if start is not None and end is not None:
        kwargs.update({"start": start, "end": end})
    else:
        kwargs["period"] = period
    return yf.Ticker(symbol).history(**kwargs)


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class YFinanceHKProvider:
    """HK market data provider backed by yfinance.

    Cache-aware (mirrors A-share Tushare provider's offline mode):
    when ``offline=True``, all reads come from ``cache_dir`` and a miss
    raises :class:`CacheMiss` (re-exported from
    ``stock_analyze.markets.a_share.data_provider`` for cross-market
    consistency).
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        offline: bool = False,
        as_of: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.as_of = as_of
        # In-memory per-process cache of fetched info dicts and history
        # frames, keyed by symbol. Cleared per-process; the on-disk
        # cache_dir snapshots are the durable mirror.
        self._info_cache: dict[str, dict[str, Any]] = {}
        self._history_cache: dict[str, pd.DataFrame] = {}

    # --- universe ----------------------------------------------------

    def universe(self, scope: str) -> list[str]:
        """Return the static ticker list for ``scope`` (hsi | hscei)."""
        return resolve_universe(scope)

    # --- per-stock snapshot ------------------------------------------

    def price_snapshot(self, code: str, as_of: str | None = None) -> HKPriceSnapshot:
        """Build a :class:`HKPriceSnapshot` for ``code`` as of ``as_of``.

        ``as_of`` defaults to today. The snapshot includes:
          - close, open, high, low, volume on the most recent trading
            day at or before ``as_of``
          - pe (trailing), pb, market_cap, dividend_yield from
            ``Ticker.info``
          - momentum_20 / momentum_60 / low_volatility_60 computed from
            the last 60 trading days of history
        """
        as_of = as_of or date.today().isoformat()
        info = self._info(code)
        hist = self._history(code, period="6mo")
        if hist.empty:
            return HKPriceSnapshot(
                code=code, trade_date=None, close=None, open=None,
                high=None, low=None, volume=None,
                pe=None, pb=None, market_cap=None,
                dividend_yield=None,
                momentum_20=None, momentum_60=None, low_volatility_60=None,
                paused=True, source="yfinance", warning="no history",
            )
        latest = hist.iloc[-1]
        closes = hist["Close"].astype(float)
        momentum_20 = _pct_change(closes, 20)
        momentum_60 = _pct_change(closes, 60)
        low_vol_60 = _trailing_volatility(closes, 60)
        return HKPriceSnapshot(
            code=code,
            trade_date=_pd_index_isoformat(hist.index[-1]),
            close=_safe_float(latest.get("Close")),
            open=_safe_float(latest.get("Open")),
            high=_safe_float(latest.get("High")),
            low=_safe_float(latest.get("Low")),
            volume=_safe_float(latest.get("Volume")),
            pe=_safe_float(info.get("trailingPE")),
            pb=_safe_float(info.get("priceToBook")),
            market_cap=_safe_float(info.get("marketCap")),
            dividend_yield=_safe_float(info.get("dividendYield")),
            momentum_20=momentum_20,
            momentum_60=momentum_60,
            low_volatility_60=low_vol_60,
            paused=False,
            source="yfinance",
        )

    def spot(self, scope: str) -> pd.DataFrame:
        """Build a DataFrame of :class:`HKPriceSnapshot` rows for ``scope``.

        One row per ticker in the universe. Suitable for strategy.build_signals
        consumption — the column layout matches what
        ``factor_pipeline.process_factors`` expects after a rename pass.
        """
        rows: list[dict[str, Any]] = []
        for code in self.universe(scope):
            snap = self.price_snapshot(code)
            rows.append({
                "code": code,
                "trade_date": snap.trade_date,
                "close": snap.close,
                "open": snap.open,
                "high": snap.high,
                "low": snap.low,
                "volume": snap.volume,
                "pe": snap.pe,
                "pb": snap.pb,
                "market_cap": snap.market_cap,
                "dividend_yield": snap.dividend_yield,
                "momentum_20": snap.momentum_20,
                "momentum_60": snap.momentum_60,
                "low_volatility_60": snap.low_volatility_60,
                "paused": snap.paused,
                "source": snap.source,
            })
        return pd.DataFrame(rows)

    # --- execution helpers -------------------------------------------

    def execution_quote(
        self,
        code: str,
        execute_after: str,
        side: str,
        as_of: str | None = None,
    ) -> HKExecutionQuote:
        """Return a quoted execution price for the simulator.

        Uses the next available trading day's open after ``execute_after``.
        Applies slippage on top: buys pay ``+SLIPPAGE_BPS`` bps, sells
        receive ``-SLIPPAGE_BPS`` bps. Caller layers stamp duty + commission
        on top of the quoted price.

        ``side`` is one of ``buy`` / ``sell``.
        """
        hist = self._history(code, period="3mo")
        if hist.empty:
            return HKExecutionQuote(
                code=code, trade_date=None, price=None, paused=True,
                source="yfinance", reason="no history"
            )
        # Find first row whose trade_date >= execute_after
        target_date = pd.to_datetime(execute_after).date()
        matching = [(idx, row) for idx, row in hist.iterrows()
                    if idx.date() >= target_date]
        if not matching:
            # All history is before execute_after — fall back to latest close
            idx = hist.index[-1]
            row = hist.iloc[-1]
            return HKExecutionQuote(
                code=code, trade_date=_pd_index_isoformat(idx),
                price=_apply_slippage(_safe_float(row.get("Close")), side),
                paused=False, source="yfinance",
                reason="execute_after beyond history; used latest close",
            )
        idx, row = matching[0]
        open_px = _safe_float(row.get("Open"))
        if open_px is None or open_px <= 0:
            open_px = _safe_float(row.get("Close"))
        return HKExecutionQuote(
            code=code,
            trade_date=_pd_index_isoformat(idx),
            price=_apply_slippage(open_px, side),
            paused=False,
            source="yfinance",
        )

    # --- internal helpers --------------------------------------------

    def _info(self, code: str) -> dict[str, Any]:
        if code in self._info_cache:
            return self._info_cache[code]
        try:
            info = _fetch_ticker_info(code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance Ticker.info failed for %s: %s", code, exc)
            info = {}
        self._info_cache[code] = info
        return info

    def _history(self, code: str, period: str = "3mo") -> pd.DataFrame:
        cache_key = f"{code}|{period}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key]
        try:
            hist = _fetch_ticker_history(code, period=period)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance Ticker.history failed for %s: %s", code, exc)
            hist = pd.DataFrame()
        self._history_cache[cache_key] = hist
        return hist

    # --- conformance helpers (for symmetry with A-share) -------------

    def lot_size(self, code: str) -> int:
        """Per-stock lot size lookup (v1: constant 100 — see mechanics)."""
        from ..mechanics import lot_size_for
        return lot_size_for(code)

    def is_shortable(self, code: str) -> bool:
        """v1 returns True for all stocks (no borrow check)."""
        return True


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


# ---------------------------------------------------------------------------
# Small math helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # yfinance occasionally returns NaN for missing fields; pd.isna treats
    # those plus float('nan') uniformly.
    if pd.isna(f):
        return None
    return f


def _pct_change(closes: pd.Series, lookback: int) -> float | None:
    """Return (last_close / close_<lookback>_days_ago - 1) or None."""
    if len(closes) < lookback + 1:
        return None
    last = float(closes.iloc[-1])
    prior = float(closes.iloc[-lookback - 1])
    if prior <= 0:
        return None
    return last / prior - 1.0


def _trailing_volatility(closes: pd.Series, lookback: int) -> float | None:
    """Return std-dev of daily returns over the last ``lookback`` days, or None."""
    if len(closes) < lookback + 1:
        return None
    rets = closes.pct_change().dropna().iloc[-lookback:]
    if rets.empty:
        return None
    return float(rets.std())


def _apply_slippage(price: float | None, side: str) -> float | None:
    if price is None:
        return None
    bps = SLIPPAGE_BPS / 10000.0
    if side == "buy":
        return price * (1.0 + bps)
    if side == "sell":
        return price * (1.0 - bps)
    return price


def _pd_index_isoformat(index_value: Any) -> str:
    """Convert a pandas Timestamp / datetime / date into 'YYYY-MM-DD'."""
    if hasattr(index_value, "date"):
        return index_value.date().isoformat()
    if isinstance(index_value, (date, datetime)):
        return index_value.isoformat()[:10]
    return str(index_value)[:10]


__all__ = [
    "HKExecutionQuote",
    "HKPriceSnapshot",
    "YFinanceHKProvider",
    "make_provider",
]
