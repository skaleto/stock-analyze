"""Shared yfinance provider base for the HK + US markets.

Extracted per OpenSpec change ``extract-yfinance-provider-base`` (C2). HK
and US data providers were ~80% duplicated; the shared fetch / cache /
snapshot / execution-quote logic + the five math helpers now live here
once. HK / US providers become thin subclasses that supply only:

  - ``snapshot_cls`` / ``quote_cls`` — the per-market dataclass types
    (HKPriceSnapshot / USPriceSnapshot etc. — they have identical fields)
  - ``slippage_bps`` — per-market slippage constant
  - ``_resolve_universe(scope)`` — the market's universe lookup
  - ``lot_size(code)`` — per-market lot rule
  - ``_fetch_info`` / ``_fetch_history`` — the network seam. Subclasses
    route these to their module-level ``_fetch_ticker_info`` /
    ``_fetch_ticker_history`` so existing tests that
    ``patch("stock_analyze.markets.hk.data_provider._fetch_ticker_info")``
    keep working unchanged.

A-share is NOT affected — it has a different (Tushare) provider lineage.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared math helpers (formerly duplicated byte-for-byte in hk + us)
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


def _apply_slippage(price: float | None, side: str, slippage_bps: float) -> float | None:
    if price is None:
        return None
    bps = slippage_bps / 10000.0
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


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------


class YFinanceProviderBase:
    """Base for yfinance-backed market data providers (HK, US).

    Subclass contract (class attrs + methods):
      snapshot_cls : dataclass with the PriceSnapshot field layout
      quote_cls    : dataclass with the ExecutionQuote field layout
      slippage_bps : float
      _resolve_universe(scope) -> list[str]
      lot_size(code) -> int
      is_shortable(code) -> bool   (default True)
      normalize_symbol(code) -> str  (default identity)
      _fetch_info(symbol) -> dict
      _fetch_history(symbol, period) -> pd.DataFrame
    """

    # Subclasses MUST override these three class attrs.
    snapshot_cls: type = None  # type: ignore[assignment]
    quote_cls: type = None  # type: ignore[assignment]
    slippage_bps: float = 5.0

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        offline: bool = False,
        as_of: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.as_of = as_of
        # Per-process in-memory cache, keyed by symbol / (symbol, period).
        self._info_cache: dict[str, dict[str, Any]] = {}
        self._history_cache: dict[str, pd.DataFrame] = {}

    # --- health ledger (no-op) ---------------------------------------
    # The a_share Tushare provider records per-fetch health and flushes it via
    # persist_health(); yfinance providers don't maintain that ledger. These
    # no-ops keep the provider interface uniform so shared callers (the CLI run
    # loop, simulators) can call them unconditionally.

    def record_health(self, *args: Any, **kwargs: Any) -> None:
        """No-op: yfinance providers don't track per-fetch health."""

    def persist_health(self) -> None:
        """No-op: nothing to flush (see record_health)."""

    # --- subclass hooks (defaults) -----------------------------------

    def normalize_symbol(self, code: str) -> str:
        """Map an internal code to the yfinance ticker. Default: identity.

        HK + US universes already store tickers in yfinance form
        (``0700.HK`` / ``AAPL``), so identity is correct for both today.
        Kept as a hook for future code↔ticker translation.
        """
        return code

    def is_shortable(self, code: str) -> bool:
        """v1 returns True for all stocks (no borrow-availability check)."""
        return True

    def _resolve_universe(self, scope: str) -> list[str]:
        raise NotImplementedError

    def lot_size(self, code: str) -> int:
        raise NotImplementedError

    def _fetch_info(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    def _fetch_history(self, symbol: str, period: str = "3mo") -> pd.DataFrame:
        raise NotImplementedError

    # --- universe ----------------------------------------------------

    def universe(self, scope: str) -> list[str]:
        return self._resolve_universe(scope)

    # --- per-stock snapshot ------------------------------------------

    def price_snapshot(self, code: str, as_of: str | None = None):
        """Build a snapshot (``self.snapshot_cls``) for ``code`` as of ``as_of``."""
        as_of = as_of or date.today().isoformat()
        info = self._info(code)
        hist = self._history(code, period="6mo")
        if hist.empty:
            return self.snapshot_cls(
                code=code, trade_date=None, close=None, open=None,
                high=None, low=None, volume=None,
                pe=None, pb=None, market_cap=None,
                dividend_yield=None,
                momentum_20=None, momentum_60=None, low_volatility_60=None,
                paused=True, source="yfinance", warning="no history",
            )
        latest = hist.iloc[-1]
        closes = hist["Close"].astype(float)
        return self.snapshot_cls(
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
            momentum_20=_pct_change(closes, 20),
            momentum_60=_pct_change(closes, 60),
            low_volatility_60=_trailing_volatility(closes, 60),
            paused=False,
            source="yfinance",
        )

    def spot(self, scope: str) -> pd.DataFrame:
        """One snapshot row per ticker in ``scope``'s universe."""
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
    ):
        """Quoted execution price (``self.quote_cls``) for the simulator.

        Uses the next available trading day's open at/after ``execute_after``,
        with +/- ``slippage_bps`` slippage. Falls back to latest close if
        ``execute_after`` is beyond available history.
        """
        hist = self._history(code, period="3mo")
        if hist.empty:
            return self.quote_cls(
                code=code, trade_date=None, price=None, paused=True,
                source="yfinance", reason="no history",
            )
        target_date = pd.to_datetime(execute_after).date()
        matching = [(idx, row) for idx, row in hist.iterrows()
                    if idx.date() >= target_date]
        if not matching:
            idx = hist.index[-1]
            row = hist.iloc[-1]
            return self.quote_cls(
                code=code, trade_date=_pd_index_isoformat(idx),
                price=_apply_slippage(_safe_float(row.get("Close")), side, self.slippage_bps),
                paused=False, source="yfinance",
                reason="execute_after beyond history; used latest close",
            )
        idx, row = matching[0]
        open_px = _safe_float(row.get("Open"))
        if open_px is None or open_px <= 0:
            open_px = _safe_float(row.get("Close"))
        return self.quote_cls(
            code=code,
            trade_date=_pd_index_isoformat(idx),
            price=_apply_slippage(open_px, side, self.slippage_bps),
            paused=False,
            source="yfinance",
        )

    # --- internal cache wrappers -------------------------------------

    def _info(self, code: str) -> dict[str, Any]:
        if code in self._info_cache:
            return self._info_cache[code]
        try:
            info = self._fetch_info(self.normalize_symbol(code))
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance info fetch failed for %s: %s", code, exc)
            info = {}
        self._info_cache[code] = info
        return info

    def _history(self, code: str, period: str = "3mo") -> pd.DataFrame:
        cache_key = f"{code}|{period}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key]
        try:
            hist = self._fetch_history(self.normalize_symbol(code), period=period)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance history fetch failed for %s: %s", code, exc)
            hist = pd.DataFrame()
        self._history_cache[cache_key] = hist
        return hist


__all__ = [
    "YFinanceProviderBase",
    "_apply_slippage",
    "_pct_change",
    "_pd_index_isoformat",
    "_safe_float",
    "_trailing_volatility",
]
