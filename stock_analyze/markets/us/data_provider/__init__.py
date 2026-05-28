"""US data provider — yfinance wrapper.

Companion to HK's yfinance provider but for US symbols (no ``.HK``
suffix). Same module-level ``_fetch_ticker_info`` / ``_fetch_ticker_history``
seam so tests mock at a single patch point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..mechanics import SLIPPAGE_BPS
from ..universe import resolve_universe


logger = logging.getLogger(__name__)


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


class YFinanceUSProvider:
    """US market data provider backed by yfinance.

    Symmetric with HK's YFinanceHKProvider — only the ticker namespace
    + slippage default differ.
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
        self._info_cache: dict[str, dict[str, Any]] = {}
        self._history_cache: dict[str, pd.DataFrame] = {}

    def universe(self, scope: str) -> list[str]:
        return resolve_universe(scope)

    def price_snapshot(self, code: str, as_of: str | None = None) -> USPriceSnapshot:
        as_of = as_of or date.today().isoformat()
        info = self._info(code)
        hist = self._history(code, period="6mo")
        if hist.empty:
            return USPriceSnapshot(
                code=code, trade_date=None, close=None, open=None,
                high=None, low=None, volume=None,
                pe=None, pb=None, market_cap=None,
                dividend_yield=None,
                momentum_20=None, momentum_60=None, low_volatility_60=None,
                paused=True, source="yfinance", warning="no history",
            )
        latest = hist.iloc[-1]
        closes = hist["Close"].astype(float)
        return USPriceSnapshot(
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

    def execution_quote(
        self,
        code: str,
        execute_after: str,
        side: str,
        as_of: str | None = None,
    ) -> USExecutionQuote:
        hist = self._history(code, period="3mo")
        if hist.empty:
            return USExecutionQuote(
                code=code, trade_date=None, price=None, paused=True,
                source="yfinance", reason="no history",
            )
        target_date = pd.to_datetime(execute_after).date()
        matching = [(idx, row) for idx, row in hist.iterrows()
                    if idx.date() >= target_date]
        if not matching:
            idx = hist.index[-1]
            row = hist.iloc[-1]
            return USExecutionQuote(
                code=code, trade_date=_pd_index_isoformat(idx),
                price=_apply_slippage(_safe_float(row.get("Close")), side),
                paused=False, source="yfinance",
                reason="execute_after beyond history; used latest close",
            )
        idx, row = matching[0]
        open_px = _safe_float(row.get("Open"))
        if open_px is None or open_px <= 0:
            open_px = _safe_float(row.get("Close"))
        return USExecutionQuote(
            code=code, trade_date=_pd_index_isoformat(idx),
            price=_apply_slippage(open_px, side), paused=False, source="yfinance",
        )

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

    def lot_size(self, code: str) -> int:
        return 1  # US has no lot constraint in v1

    def is_shortable(self, code: str) -> bool:
        return True


def make_provider(
    cache_dir: Path | str | None = None,
    offline: bool = False,
    as_of: str | None = None,
) -> YFinanceUSProvider:
    return YFinanceUSProvider(cache_dir=cache_dir, offline=offline, as_of=as_of)


# Small helpers (same as HK — could be factored to markets/_yfinance_base
# in a future refactor; kept duplicated for v1 isolation).


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _pct_change(closes: pd.Series, lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    last = float(closes.iloc[-1])
    prior = float(closes.iloc[-lookback - 1])
    if prior <= 0:
        return None
    return last / prior - 1.0


def _trailing_volatility(closes: pd.Series, lookback: int) -> float | None:
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
    if hasattr(index_value, "date"):
        return index_value.date().isoformat()
    if isinstance(index_value, (date, datetime)):
        return index_value.isoformat()[:10]
    return str(index_value)[:10]


__all__ = [
    "USExecutionQuote",
    "USPriceSnapshot",
    "YFinanceUSProvider",
    "make_provider",
]
