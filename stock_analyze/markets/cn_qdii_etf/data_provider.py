"""Tushare-backed provider for mainland-listed cross-border ETFs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .._yfinance_base import (
    _apply_slippage,
    _pct_change,
    _safe_float,
    _trailing_volatility,
)
from . import mechanics
from .universe import classify_scope, resolve_universe
from ..a_share.data_provider import CacheMiss, TushareTokenMissing
from ..a_share.data_provider.base import TUSHARE_TOKEN_ENV
from ...utils import write_json


MAX_NAV_AGE_DAYS = 7
MAX_PRICE_AGE_DAYS = 7


@dataclass
class ETFPriceSnapshot:
    code: str
    name: str | None
    trade_date: str | None
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    amount: float | None
    avg_amount_20: float | None
    momentum_20: float | None
    momentum_60: float | None
    low_volatility_60: float | None
    nav: float | None
    nav_date: str | None
    discount_premium: float | None
    list_date: str | None = None
    listing_age_days: int | None = None
    industry: str | None = None
    paused: bool = False
    source: str = "tushare-fund"
    warning: str = ""


@dataclass
class ETFExecutionQuote:
    code: str
    trade_date: str | None
    price: float | None
    paused: bool = False
    source: str = "tushare-fund"
    reason: str = ""


def normalize_ts_code(code: str) -> str:
    """Normalize domestic ETF code to Tushare ``<code>.<exchange>`` form."""
    raw = str(code).strip().upper()
    if "." in raw:
        return raw
    if raw.startswith(("51", "58")):
        return f"{raw}.SH"
    if raw.startswith(("15", "16")):
        return f"{raw}.SZ"
    return raw


def _yyyymmdd(value: str | date | None) -> str:
    if value is None:
        return date.today().strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value)
    return text.replace("-", "")[:8]


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10]


class CNQDIETFProvider:
    """Provider backed by Tushare ``fund_*`` endpoints.

    Tests inject ``pro_client`` directly; production construction goes through
    :func:`make_provider`, which lazy-imports tushare and reads TUSHARE_TOKEN.
    """

    def __init__(
        self,
        pro_client: Any | None = None,
        *,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        as_of: str | None = None,
        token: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.as_of = as_of
        self._pro_client = pro_client
        self._token = token
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._daily_refresh_attempted: set[str] = set()
        self._nav_cache: dict[str, pd.DataFrame] = {}
        self._basic_cache: pd.DataFrame | None = None
        self._health: list[dict[str, Any]] = []

    @property
    def pro(self):
        if self._pro_client is None:
            self._pro_client = self._build_pro_client(self._token)
        return self._pro_client

    def _build_pro_client(self, token: str | None):
        resolved = token or os.environ.get(TUSHARE_TOKEN_ENV)
        if not resolved:
            raise TushareTokenMissing()
        import tushare as ts

        return ts.pro_api(resolved)

    def persist_health(self) -> None:
        if self.cache_dir is not None:
            write_json(self.cache_dir.parent / "data_health.json", self._health)

    def record_health(
        self,
        source: str,
        status: str,
        message: str = "",
        rows: int | None = None,
    ) -> None:
        self._health.append(
            {
                "time": pd.Timestamp.now().isoformat(timespec="seconds"),
                "source": source,
                "status": status,
                "message": message[:300],
                "rows": rows,
            }
        )

    def universe(self, scope: str) -> list[str]:
        return resolve_universe(scope)

    def spot(self, scope: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for code in self.universe(scope):
            snap = self.price_snapshot(code)
            rows.append(
                {
                    "code": snap.code,
                    "name": snap.name,
                    "trade_date": snap.trade_date,
                    "close": snap.close,
                    "open": snap.open,
                    "high": snap.high,
                    "low": snap.low,
                    "volume": snap.volume,
                    "amount": snap.amount,
                    "avg_amount_20": snap.avg_amount_20,
                    "momentum_20": snap.momentum_20,
                    "momentum_60": snap.momentum_60,
                    "low_volatility_60": snap.low_volatility_60,
                    "nav": snap.nav,
                    "nav_date": snap.nav_date,
                    "discount_premium": snap.discount_premium,
                    "list_date": snap.list_date,
                    "listing_age_days": snap.listing_age_days,
                    "industry": scope,
                    "paused": snap.paused,
                    "source": snap.source,
                }
            )
        return pd.DataFrame(rows)

    def price_snapshot(self, code: str, as_of: str | None = None) -> ETFPriceSnapshot:
        ts_code = normalize_ts_code(code)
        as_of_key = _yyyymmdd(as_of or self.as_of)
        hist = self._fund_daily(ts_code, as_of_key)
        name, list_date = self._fund_metadata(ts_code)
        if hist.empty:
            return ETFPriceSnapshot(
                code=ts_code,
                name=name,
                trade_date=None,
                close=None,
                open=None,
                high=None,
                low=None,
                volume=None,
                amount=None,
                avg_amount_20=None,
                momentum_20=None,
                momentum_60=None,
                low_volatility_60=None,
                nav=None,
                nav_date=None,
                discount_premium=None,
                list_date=list_date,
                listing_age_days=None,
                industry=classify_scope(ts_code),
                paused=True,
                warning="no fund_daily history",
            )
        hist = hist[hist["trade_date"].astype(str) <= as_of_key].copy()
        if hist.empty:
            return self._paused_snapshot(
                ts_code,
                name,
                "no history on or before as_of",
                list_date=list_date,
            )
        hist = hist.sort_values("trade_date")
        latest = hist.iloc[-1]
        closes = self._adjusted_closes(ts_code, hist, as_of_key)
        amounts = pd.to_numeric(hist.get("amount"), errors="coerce")
        nav, nav_date = self._latest_nav(ts_code, str(latest["trade_date"]))
        close = _safe_float(latest.get("close"))
        price_is_stale = self._price_is_stale(str(latest["trade_date"]), as_of_key)
        warning = ""
        if price_is_stale:
            warning = "stale fund_daily history"
            self.record_health(
                "fund_daily",
                "stale",
                f"{ts_code} trade_date={_iso(latest.get('trade_date'))} as_of={_iso(as_of_key)}",
            )
        nav_is_fresh = self._nav_is_fresh(nav_date, str(latest["trade_date"]))
        discount = (
            close / nav - 1.0
            if nav_is_fresh and close is not None and nav not in (None, 0)
            else None
        )
        if nav is not None and not nav_is_fresh:
            self.record_health(
                "fund_nav",
                "stale",
                f"{ts_code} nav_date={nav_date} trade_date={_iso(latest.get('trade_date'))}",
            )
        return ETFPriceSnapshot(
            code=ts_code,
            name=name,
            trade_date=_iso(latest.get("trade_date")),
            close=close,
            open=_safe_float(latest.get("open")),
            high=_safe_float(latest.get("high")),
            low=_safe_float(latest.get("low")),
            volume=_safe_float(latest.get("vol")),
            amount=_safe_float(latest.get("amount")),
            avg_amount_20=_safe_float(amounts.tail(20).mean()) if not amounts.dropna().empty else None,
            momentum_20=_pct_change(closes, 20),
            momentum_60=_pct_change(closes, 60),
            low_volatility_60=_trailing_volatility(closes, 60),
            nav=nav,
            nav_date=nav_date,
            discount_premium=discount,
            list_date=list_date,
            listing_age_days=self._listing_age_days(list_date, str(latest["trade_date"])),
            industry=classify_scope(ts_code),
            paused=price_is_stale,
            warning=warning,
        )

    def _paused_snapshot(
        self,
        ts_code: str,
        name: str | None,
        warning: str,
        *,
        list_date: str | None = None,
    ) -> ETFPriceSnapshot:
        return ETFPriceSnapshot(
            code=ts_code,
            name=name,
            trade_date=None,
            close=None,
            open=None,
            high=None,
            low=None,
            volume=None,
            amount=None,
            avg_amount_20=None,
            momentum_20=None,
            momentum_60=None,
            low_volatility_60=None,
            nav=None,
            nav_date=None,
            discount_premium=None,
            list_date=list_date,
            listing_age_days=None,
            industry=classify_scope(ts_code),
            paused=True,
            warning=warning,
        )

    def execution_quote(
        self,
        code: str,
        execute_after: str,
        side: str,
        as_of: str | None = None,
    ) -> ETFExecutionQuote:
        ts_code = normalize_ts_code(code)
        target = _yyyymmdd(execute_after)
        as_of_key = _yyyymmdd(as_of or self.as_of or execute_after)
        hist = self._fund_daily(ts_code, max(as_of_key, target))
        if hist.empty:
            return ETFExecutionQuote(
                code=ts_code,
                trade_date=None,
                price=None,
                paused=True,
                reason="no fund_daily history",
            )
        hist = hist.sort_values("trade_date")
        trade_dates = hist["trade_date"].astype(str)
        matching = hist[(trade_dates >= target) & (trade_dates <= as_of_key)]
        if matching.empty:
            return ETFExecutionQuote(
                code=ts_code,
                trade_date=None,
                price=None,
                paused=True,
                reason="no quote in execution window",
            )
        row = matching.iloc[0]
        raw_price = _safe_float(row.get("open")) or _safe_float(row.get("close"))
        return ETFExecutionQuote(
            code=ts_code,
            trade_date=_iso(row.get("trade_date")),
            price=_apply_slippage(raw_price, side, mechanics.SLIPPAGE_BPS),
            paused=False,
            reason="",
        )

    def fund_adj(self, code: str, as_of: str | None = None) -> pd.DataFrame:
        ts_code = normalize_ts_code(code)
        as_of_key = _yyyymmdd(as_of or self.as_of)
        cache_name = self._cache_name("fund_adj", ts_code, as_of_key)
        cached = self._read_cache(cache_name)
        if cached is not None:
            self.record_health("fund_adj", "cache_hit", cache_name, rows=len(cached))
            return cached
        if self.offline:
            self.record_health("fund_adj", "cache_miss", cache_name)
            raise CacheMiss(method="fund_adj", cache_name=cache_name)
        try:
            df = self.pro.fund_adj(ts_code=ts_code, end_date=as_of_key)
        except Exception as exc:
            self.record_health("fund_adj", "failed", str(exc))
            raise
        df = self._normalize_adj(df)
        self.record_health("fund_adj", "ok", rows=len(df))
        return self._write_cache(cache_name, df)

    def _fund_daily(self, ts_code: str, as_of_key: str) -> pd.DataFrame:
        cache_name = self._cache_name("fund_daily", ts_code, as_of_key)
        fallback: pd.DataFrame | None = None
        if cache_name in self._daily_cache:
            fallback = self._daily_cache[cache_name]
            cache_status = "memory_cache"
        else:
            fallback = self._read_cache(cache_name)
            cache_status = "cache_hit"
            if fallback is not None:
                self._daily_cache[cache_name] = fallback
        if fallback is not None:
            complete = self._daily_has_target(fallback, as_of_key)
            if self.offline or complete or cache_name in self._daily_refresh_attempted:
                self.record_health("fund_daily", cache_status, cache_name, rows=len(fallback))
                return fallback
            self.record_health(
                "fund_daily",
                "refresh_incomplete_cache",
                cache_name,
                rows=len(fallback),
            )
        if self.offline:
            self.record_health("fund_daily", "cache_miss", cache_name)
            raise CacheMiss(method="fund_daily", cache_name=cache_name)
        self._daily_refresh_attempted.add(cache_name)
        end = datetime.strptime(as_of_key, "%Y%m%d").date()
        start = (end - timedelta(days=260)).strftime("%Y%m%d")
        try:
            df = self.pro.fund_daily(
                ts_code=ts_code,
                start_date=start,
                end_date=as_of_key,
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
        except Exception as exc:
            self.record_health("fund_daily", "failed", str(exc))
            if fallback is not None:
                return fallback
            raise
        df = self._normalize_daily(df)
        self.record_health("fund_daily", "ok", rows=len(df))
        self._daily_cache[cache_name] = self._write_cache(cache_name, df)
        return self._daily_cache[cache_name]

    @staticmethod
    def _daily_has_target(df: pd.DataFrame, as_of_key: str) -> bool:
        if df.empty or "trade_date" not in df.columns:
            return False
        return bool((df["trade_date"].astype(str) == as_of_key).any())

    def _fund_nav(self, ts_code: str, as_of_key: str) -> pd.DataFrame:
        cache_name = self._cache_name("fund_nav", ts_code, as_of_key)
        if cache_name in self._nav_cache:
            self.record_health(
                "fund_nav",
                "memory_cache",
                cache_name,
                rows=len(self._nav_cache[cache_name]),
            )
            return self._nav_cache[cache_name]
        cached = self._read_cache(cache_name)
        if cached is not None:
            self._nav_cache[cache_name] = cached
            self.record_health("fund_nav", "cache_hit", cache_name, rows=len(cached))
            return cached
        if self.offline:
            self.record_health("fund_nav", "offline_unavailable", cache_name)
            return pd.DataFrame()
        try:
            try:
                df = self.pro.fund_nav(
                    ts_code=ts_code,
                    end_date=as_of_key,
                    fields="ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
                )
            except TypeError:
                df = self.pro.fund_nav(
                    ts_code=ts_code,
                    fields="ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
                )
        except Exception as exc:
            self.record_health("fund_nav", "failed", str(exc))
            raise
        df = self._normalize_nav(df)
        self.record_health("fund_nav", "ok", rows=len(df))
        self._nav_cache[cache_name] = self._write_cache(cache_name, df)
        return self._nav_cache[cache_name]

    def _fund_basic(self) -> pd.DataFrame:
        if self._basic_cache is not None:
            return self._basic_cache
        cache_name = "fund_basic_E.csv"
        cached = self._read_cache(cache_name)
        if cached is not None:
            self._basic_cache = cached
            self.record_health("fund_basic", "cache_hit", cache_name, rows=len(cached))
            return cached
        if self.offline:
            self._basic_cache = pd.DataFrame()
            self.record_health("fund_basic", "offline_unavailable", cache_name)
            return self._basic_cache
        try:
            df = self.pro.fund_basic(
                market="E",
                fields="ts_code,name,management,custodian,fund_type,found_date,list_date,delist_date",
            )
        except Exception as exc:
            self.record_health("fund_basic", "failed", str(exc))
            raise
        self.record_health("fund_basic", "ok", rows=len(df))
        self._basic_cache = self._write_cache(cache_name, df)
        return self._basic_cache

    def _fund_name(self, ts_code: str) -> str | None:
        name, _list_date = self._fund_metadata(ts_code)
        return name

    def _fund_metadata(self, ts_code: str) -> tuple[str | None, str | None]:
        basic = self._fund_basic()
        if basic.empty or "ts_code" not in basic.columns:
            return None, None
        rows = basic[basic["ts_code"].astype(str) == ts_code]
        if rows.empty:
            return None, None
        row = rows.iloc[0]
        name = str(row.get("name") or "") or None
        return name, _iso(row.get("list_date"))

    def _adjusted_closes(
        self,
        ts_code: str,
        hist: pd.DataFrame,
        as_of_key: str,
    ) -> pd.Series:
        raw = pd.to_numeric(hist["close"], errors="coerce").reset_index(drop=True)
        try:
            adj = self.fund_adj(ts_code, as_of=as_of_key)
        except Exception:  # noqa: BLE001 - adjustment is optional; health records the failure
            return raw
        if adj.empty or not {"trade_date", "adj_factor"}.issubset(adj.columns):
            return raw
        base = hist[["trade_date", "close"]].copy().reset_index(drop=True)
        base["trade_date"] = base["trade_date"].astype(str)
        factors = adj[["trade_date", "adj_factor"]].copy()
        factors["trade_date"] = factors["trade_date"].astype(str)
        factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
        merged = base.merge(factors, on="trade_date", how="left")
        close = pd.to_numeric(merged["close"], errors="coerce")
        factor = merged["adj_factor"].fillna(1.0)
        return close * factor

    @staticmethod
    def _listing_age_days(list_date: str | None, trade_date: str) -> int | None:
        if not list_date:
            return None
        try:
            listed = date.fromisoformat(list_date)
            traded = datetime.strptime(_yyyymmdd(trade_date), "%Y%m%d").date()
        except ValueError:
            return None
        return max((traded - listed).days, 0)

    @staticmethod
    def _nav_is_fresh(nav_date: str | None, trade_date: str) -> bool:
        if not nav_date:
            return False
        try:
            nav_day = date.fromisoformat(nav_date)
            trade_day = datetime.strptime(_yyyymmdd(trade_date), "%Y%m%d").date()
        except ValueError:
            return False
        return 0 <= (trade_day - nav_day).days <= MAX_NAV_AGE_DAYS

    @staticmethod
    def _price_is_stale(trade_date: str, as_of_key: str) -> bool:
        try:
            trade_day = datetime.strptime(_yyyymmdd(trade_date), "%Y%m%d").date()
            as_of_day = datetime.strptime(_yyyymmdd(as_of_key), "%Y%m%d").date()
        except ValueError:
            return True
        return (as_of_day - trade_day).days > MAX_PRICE_AGE_DAYS

    def _latest_nav(self, ts_code: str, trade_date: str) -> tuple[float | None, str | None]:
        nav_df = self._fund_nav(ts_code, trade_date)
        if nav_df.empty or "nav_date" not in nav_df.columns:
            return None, None
        eligible = nav_df[nav_df["nav_date"].astype(str) <= trade_date].copy()
        if eligible.empty:
            return None, None
        eligible = eligible.sort_values(["nav_date", "ann_date"])
        row = eligible.iloc[-1]
        nav = _safe_float(row.get("unit_nav")) or _safe_float(row.get("adj_nav"))
        return nav, _iso(row.get("nav_date"))

    def _cache_name(self, kind: str, ts_code: str, as_of_key: str) -> str:
        safe_code = ts_code.replace(".", "_")
        return f"{kind}_{safe_code}_{as_of_key}.csv"

    def _cache_path(self, cache_name: str) -> Path | None:
        return self.cache_dir / cache_name if self.cache_dir is not None else None

    def _read_cache(self, cache_name: str) -> pd.DataFrame | None:
        path = self._cache_path(cache_name)
        if path is None or not path.exists():
            return None
        return pd.read_csv(
            path,
            dtype={
                "ts_code": str,
                "trade_date": str,
                "ann_date": str,
                "nav_date": str,
                "found_date": str,
                "list_date": str,
                "delist_date": str,
            },
        )

    def _write_cache(self, cache_name: str, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy() if df is not None else pd.DataFrame()
        path = self._cache_path(cache_name)
        if path is not None:
            out.to_csv(path, index=False)
        return out

    @staticmethod
    def _normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy() if df is not None else pd.DataFrame()
        for col in ("open", "high", "low", "close", "vol", "amount"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        if "trade_date" in out.columns:
            out["trade_date"] = out["trade_date"].astype(str)
        if "ts_code" in out.columns:
            out["ts_code"] = out["ts_code"].astype(str)
        return out

    @staticmethod
    def _normalize_nav(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy() if df is not None else pd.DataFrame()
        for col in ("unit_nav", "accum_nav", "adj_nav"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        for col in ("ts_code", "ann_date", "nav_date"):
            if col in out.columns:
                out[col] = out[col].astype(str)
        return out

    @staticmethod
    def _normalize_adj(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy() if df is not None else pd.DataFrame()
        if "adj_factor" in out.columns:
            out["adj_factor"] = pd.to_numeric(out["adj_factor"], errors="coerce")
        for col in ("ts_code", "trade_date"):
            if col in out.columns:
                out[col] = out[col].astype(str)
        return out


def make_provider(
    cache_dir: Path | str | None = None,
    offline: bool = False,
    as_of: str | None = None,
) -> CNQDIETFProvider:
    return CNQDIETFProvider(cache_dir=cache_dir, offline=offline, as_of=as_of)


__all__ = [
    "CNQDIETFProvider",
    "ETFExecutionQuote",
    "ETFPriceSnapshot",
    "make_provider",
    "normalize_ts_code",
]
