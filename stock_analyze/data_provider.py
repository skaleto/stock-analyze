"""Market-data provider layer.

The previous AKShare-based implementation depended on East Money's
``push2`` endpoint which is now blocked for both cloud and home-broadband
IP ranges. This module replaces it with two providers that share an
abstract base:

* ``TushareProvider`` — primary source backed by the Tushare Pro Python
  SDK (``import tushare as ts``). Requires the ``TUSHARE_TOKEN`` env var.
  One HTTP call per day pulls full-market spot, daily, and basic
  fundamentals; per-stock fundamentals come from ``pro.fina_indicator``.
* ``BaostockProvider`` — fallback for when Tushare is unavailable or no
  token is present. Wraps the ``baostock`` SDK directly.

Callers receive a provider via the :func:`make_provider` factory. For
backwards compatibility ``AkshareProvider`` is kept as an alias of
``TushareProvider`` — every existing import site (``cli.py``,
``market_data.py``, ``simulator.py``, ``diagnostics.py``, ``strategy.py``,
tests) continues to work without change.

Token handling rules (see ``docs/tushare-token-setup.md``):

* Token is read once from ``os.environ["TUSHARE_TOKEN"]`` at provider
  init.
* Token MUST NOT appear in any log, cache file, or error message —
  exception text only references the env-var name, never its value.
* On token absence ``make_provider`` falls back to ``BaostockProvider``
  rather than raising, so the local dev experience is friction-free.
"""

from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .utils import ak_date, next_business_day, parse_date, pct_change, previous_calendar_date, safe_float, write_json


INDEX_CODES = {
    "hs300": "000300",
    "zz500": "000905",
    "zz1000": "000852",
    "cyb": "399006",
    "kcb": "000688",
}

# Tushare requires the exchange suffix on every ts_code; this map covers the
# four common index codes we care about. ``ts_code_for_index`` extends this to
# raw stock codes by deriving the suffix from the first digit.
INDEX_TUSHARE_SUFFIX = {
    "000300": "SH",
    "000905": "SH",
    "000852": "SH",
    "000688": "SH",
    "399006": "SZ",
}

RETRY_DELAYS = [2.0, 5.0, 10.0]
TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"

# Sleep between consecutive Tushare HTTP calls to keep us well below the
# 200-calls-per-minute ceiling at the 2000-credit tier. The largest burst
# we issue is per-stock ``fina_indicator`` during prepare-market-data
# (~800 codes), where 0.35s × 800 ≈ 4.7 min — still under the budget.
TUSHARE_RATE_SLEEP_S = 0.35


@dataclass
class PriceSnapshot:
    code: str
    trade_date: str | None
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    amount: float | None
    momentum_20: float | None
    momentum_60: float | None
    avg_amount_20: float | None
    low_volatility_60: float | None = None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = ""
    warning: str = ""


@dataclass
class ExecutionQuote:
    code: str
    trade_date: str | None
    price: float | None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = ""
    reason: str = ""


class CacheMiss(RuntimeError):
    """Raised when an offline provider call finds no cached entry.

    Carries enough metadata for callers / RunLedger to identify which method
    and which cache key failed without scraping the message text.
    """

    def __init__(self, method: str, cache_name: str) -> None:
        super().__init__(f"cache_miss:{method}:{cache_name}")
        self.method = method
        self.cache_name = cache_name


class TushareTokenMissing(RuntimeError):
    """Raised by :class:`TushareProvider` when no ``TUSHARE_TOKEN`` is set.

    The message intentionally avoids printing the env-var value so a
    stack-trace dump cannot leak the token. See ``docs/tushare-token-setup.md``
    for setup instructions.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{TUSHARE_TOKEN_ENV} env var not set; see docs/tushare-token-setup.md"
        )


# ---------------------------------------------------------------------------
# Shared base class
# ---------------------------------------------------------------------------


class DataProvider(ABC):
    """Abstract base of the provider hierarchy.

    Concrete subclasses MUST implement the eight spec-level fetch methods
    (``spot``, ``daily``, ``fina_indicator``, ``stock_basic``,
    ``index_weight``, ``index_daily``, ``dividend``, ``trade_cal``). The
    higher-level helpers (``universe``, ``price_snapshot``,
    ``execution_quote``, etc.) are implemented here on top of those eight
    primitives so both providers share identical aggregation logic.

    All providers honour the cache-first / offline-mode contract: when
    ``offline=True`` and the cache is empty, callers see a structured
    :class:`CacheMiss` rather than a network attempt.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        offline: bool = False,
        as_of: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = bool(offline)
        self.as_of = as_of
        self._spot_df: pd.DataFrame | None = None
        self._history_cache: dict[str, pd.DataFrame] = {}
        self.health: list[dict[str, Any]] = []

    # -- helpers shared by every concrete provider --------------------------

    def _date_stamp(self, as_of: str | None = None) -> str:
        """Return YYYYMMDD for cache filenames.

        Resolution order:

        1. Explicit ``as_of`` argument
        2. ``self.as_of`` set at construction time
        3. Auto-detect: newest ``spot_<YYYYMMDD>.csv`` in ``cache_dir``
           whose date is ``<= today``. This lets Saturday agent runs
           naturally read Friday's cache.
        4. Today's date as a last resort.
        """

        explicit = as_of or self.as_of
        if explicit:
            return ak_date(explicit)
        resolved = getattr(self, "_resolved_default_date", None)
        if resolved is None:
            resolved = self._resolve_default_date()
            self._resolved_default_date = resolved
        return resolved

    def _resolve_default_date(self) -> str:
        today_stamp = ak_date()
        if not self.cache_dir or not self.cache_dir.exists():
            return today_stamp
        candidates: list[str] = []
        for path in self.cache_dir.glob("spot_*.csv"):
            stem = path.stem
            parts = stem.split("_")
            if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) != 8:
                continue
            if parts[1] <= today_stamp:
                candidates.append(parts[1])
        return max(candidates) if candidates else today_stamp

    def _raise_cache_miss(self, method: str, cache_name: str) -> None:
        self.record_health(method, "cache_miss", f"offline lookup failed for {cache_name}")
        raise CacheMiss(method=method, cache_name=cache_name)

    def record_health(self, source: str, status: str, message: str = "", rows: int | None = None) -> None:
        self.health.append(
            {
                "time": pd.Timestamp.now().isoformat(timespec="seconds"),
                "source": source,
                "status": status,
                "message": message[:300],
                "rows": rows,
            }
        )

    def persist_health(self) -> None:
        if self.cache_dir and self.health:
            write_json(self.cache_dir.parent / "data_health.json", self.health)

    def retry(self, label: str, func: Callable[[], Any], delays: list[float] | None = None) -> Any:
        last_error: Exception | None = None
        retry_delays = RETRY_DELAYS if delays is None else delays
        for attempt, delay in enumerate([0.0] + retry_delays, start=1):
            if delay:
                time.sleep(delay)
            try:
                result = func()
                rows = len(result) if hasattr(result, "__len__") else None
                self.record_health(label, "ok", f"attempt={attempt}", rows=rows)
                return result
            except Exception as exc:  # noqa: BLE001 — external data APIs fail in many shapes
                last_error = exc
                self.record_health(
                    label,
                    "retry" if attempt <= len(retry_delays) else "failed",
                    f"attempt={attempt}: {exc}",
                )
        raise last_error or RuntimeError(f"{label} failed")

    def fallback_retry(self, label: str, func: Callable[[], Any]) -> Any:
        return self.retry(label, func, delays=[0.5])

    def cache_path(self, name: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        return self.cache_dir / f"{safe_name}.csv"

    def save_cache(self, name: str, df: pd.DataFrame) -> None:
        path = self.cache_path(name)
        if path and df is not None and not df.empty:
            df.to_csv(path, index=False, encoding="utf-8-sig")

    def load_cache(self, name: str) -> pd.DataFrame:
        path = self.cache_path(name)
        if path and path.exists():
            self.record_health(name, "cache", f"using cache {path.name}")
            return pd.read_csv(path, dtype={"code": str, "代码": str, "成分券代码": str, "品种代码": str})
        return pd.DataFrame()

    # -- abstract primitives -------------------------------------------------
    #
    # The eight methods below describe the spec-level contract every
    # concrete provider implements. The "spec.md" capability requirement
    # nails down their signatures.

    @abstractmethod
    def spot(self) -> pd.DataFrame:
        """Return a normalized full-market spot DataFrame.

        Columns: ``code``, ``name``, ``latest_price``, ``pe``, ``pb``,
        ``market_cap_yi`` (in 亿元), ``avg_amount_20`` (in 元) where
        available.
        """

    @abstractmethod
    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """Return a normalized OHLCV+amount frame for ``code`` between
        ``start`` and ``end`` (YYYYMMDD).

        Output columns: ``日期``, ``开盘``, ``收盘``, ``最高``, ``最低``,
        ``成交额`` (元), ``停牌``, ``is_st``, ``pe``, ``pb``, ``source``.
        """

    @abstractmethod
    def fina_indicator(self, code: str) -> pd.DataFrame:
        """Return a per-period financial-indicator frame for ``code``.

        Output columns at minimum: ``roe``, ``grossprofit_margin``,
        ``debt_to_assets``, ``netprofit_yoy``. Rows are sorted by ``end_date``
        descending (latest first).
        """

    @abstractmethod
    def stock_basic(self) -> pd.DataFrame:
        """Return the all-A static reference table.

        Columns: ``code``, ``name``, ``industry``, ``list_date`` (YYYYMMDD).
        """

    @abstractmethod
    def index_weight(self, scope: str, trade_date: str) -> pd.DataFrame:
        """Return index-constituent codes for ``scope`` at ``trade_date``.

        Columns: ``code``, ``name``. ``scope`` accepts both friendly names
        (``hs300``, ``zz500``) and raw 6-digit index codes.
        """

    @abstractmethod
    def index_daily(self, code: str, as_of: str) -> pd.DataFrame:
        """Return index closing series for ``code`` ending at ``as_of``.

        Columns: ``日期``, ``收盘``.
        """

    @abstractmethod
    def dividend(self, code: str) -> pd.DataFrame:
        """Return cash-dividend history for ``code``.

        Columns: ``code``, ``ann_date``, ``end_date``, ``cash_div``,
        ``ex_date`` where available.
        """

    @abstractmethod
    def trade_cal(self) -> list[str]:
        """Return the trading-calendar dates (YYYY-MM-DD strings) sorted asc."""

    # -- aggregation layer (built on top of the primitives) ----------------

    def index_constituents(self, scope: str) -> pd.DataFrame:
        index_code = INDEX_CODES.get(scope, scope)
        cache_name = f"constituents_{index_code}_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return normalize_constituents(cached)

        if self.offline:
            self._raise_cache_miss("index_constituents", cache_name)

        try:
            df = self.index_weight(scope, self._date_stamp())
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"index_constituents_{index_code}", "failed", str(exc))
            return pd.DataFrame(columns=["code", "name"])

        normalized = normalize_constituents(df)
        if not normalized.empty:
            self.save_cache(cache_name, normalized)
        return normalized

    def universe(self, scope: str) -> pd.DataFrame:
        if scope.startswith("custom:"):
            codes = {normalize_code(item) for item in scope.replace("custom:", "").split(",") if normalize_code(item)}
            return pd.DataFrame({"code": sorted(codes)}).assign(name="", latest_price=None, pe=None, pb=None, market_cap_yi=None)

        spot_df = self.spot()
        if scope == "all":
            return spot_df

        constituents = self.index_constituents(scope)
        if constituents.empty:
            self.record_health(f"universe_{scope}", "failed", "no constituents")
            return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
        if spot_df.empty:
            return constituents.assign(latest_price=None, pe=None, pb=None, market_cap_yi=None)

        merged = constituents.merge(spot_df, on="code", how="left", suffixes=("_index", ""))
        merged["name"] = merged["name"].fillna(merged.get("name_index"))
        return merged[["code", "name", "latest_price", "pe", "pb", "market_cap_yi"]].copy()

    def trading_calendar(self) -> list[str]:
        cache_name = "trading_calendar"
        cached = self.load_cache(cache_name)
        dates = normalize_trading_calendar(cached)
        if dates:
            return dates

        if self.offline:
            self._raise_cache_miss("trading_calendar", cache_name)

        try:
            dates = self.trade_cal()
        except Exception as exc:  # noqa: BLE001
            self.record_health("trading_calendar", "failed", str(exc))
            return []

        if dates:
            self.save_cache(cache_name, pd.DataFrame({"trade_date": dates}))
        return dates

    def next_trading_day(self, value: str | date | None) -> str:
        day = parse_date(value)
        dates = self.trading_calendar()
        for item in dates:
            try:
                candidate = parse_date(item)
            except ValueError:
                continue
            if candidate > day:
                return candidate.isoformat()
        fallback = next_business_day(day)
        self.record_health("trading_calendar", "cache", f"fallback next_business_day={fallback}")
        return fallback

    def basic_info(self, code: str) -> dict[str, Any]:
        cache_name = f"basic_{code}_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return cached.iloc[0].to_dict()

        if self.offline:
            self._raise_cache_miss("basic_info", cache_name)

        try:
            basic = self.stock_basic()
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"basic_{code}", "failed", str(exc))
            return {}

        if basic.empty:
            return {}
        row = basic[basic["code"] == normalize_code(code)]
        if row.empty:
            return {}
        record = row.iloc[0].to_dict()
        # Compute listing_date in ISO form for downstream filters.
        list_date = str(record.get("list_date") or "")
        if list_date and re.fullmatch(r"\d{8}", list_date):
            record["listing_date"] = f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:]}"
        result = {
            "code": normalize_code(record.get("code", code)) or code,
            "name": record.get("name"),
            "latest_price": None,
            "market_cap_yi": None,
            "industry": record.get("industry"),
            "listing_date": record.get("listing_date"),
        }
        self.save_cache(cache_name, pd.DataFrame([result]))
        return result

    def valuation_metrics(self, code: str) -> dict[str, float | None]:
        cache_name = f"valuation_{code}_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            row = cached.iloc[0]
            return {"pe": safe_float(row.get("pe")), "pb": safe_float(row.get("pb"))}

        if self.offline:
            self._raise_cache_miss("valuation_metrics", cache_name)

        # Spot already carries PE/PB at the daily-basic level. Pull it from
        # there to avoid an extra HTTP call.
        spot_df = self.spot()
        if spot_df.empty:
            return {"pe": None, "pb": None}
        row = spot_df[spot_df["code"] == normalize_code(code)]
        if row.empty:
            return {"pe": None, "pb": None}
        result = {"pe": safe_float(row.iloc[0].get("pe")), "pb": safe_float(row.iloc[0].get("pb"))}
        if result["pe"] is not None or result["pb"] is not None:
            self.save_cache(cache_name, pd.DataFrame([{"code": normalize_code(code), **result}]))
        return result

    def financial_metrics(self, code: str) -> dict[str, Any]:
        cache_name = f"financial_{code}_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return cached.iloc[0].to_dict()

        if self.offline:
            self._raise_cache_miss("financial_metrics", cache_name)

        try:
            df = self.fina_indicator(code)
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"financial_{code}", "failed", str(exc))
            return {"fetch_error": str(exc)}

        if df is None or df.empty:
            return {}
        row = df.iloc[0]
        result: dict[str, Any] = {}
        roe = safe_float(row.get("roe"))
        gross_margin = safe_float(row.get("grossprofit_margin"))
        debt_ratio = safe_float(row.get("debt_to_assets"))
        net_profit_growth = safe_float(row.get("netprofit_yoy"))
        if roe is not None:
            result["roe"] = roe
        if gross_margin is not None:
            result["gross_margin"] = gross_margin
        if debt_ratio is not None:
            result["debt_ratio"] = debt_ratio
        if net_profit_growth is not None:
            result["net_profit_growth"] = net_profit_growth
        if result:
            self.save_cache(cache_name, pd.DataFrame([{"code": normalize_code(code), **result}]))
        return result

    def price_history(self, code: str, as_of: str | None = None, days: int = 180) -> pd.DataFrame:
        stamp = self._date_stamp(as_of)
        cache_key = f"{code}:{stamp}:{days}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key].copy()

        cache_name = f"history_{code}_{stamp}_{days}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            normalized = normalize_history(cached)
            self._history_cache[cache_key] = normalized
            return normalized.copy()

        if self.offline:
            self._raise_cache_miss("price_history", cache_name)

        end_date = stamp
        start_date = previous_calendar_date(days, as_of or self.as_of)

        try:
            df = self.daily(code, start_date, end_date)
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"history_{code}", "failed", str(exc))
            df = pd.DataFrame()

        normalized = normalize_history(df)
        if not normalized.empty:
            self.save_cache(cache_name, normalized)
        self._history_cache[cache_key] = normalized
        return normalized.copy()

    def price_snapshot(self, code: str, as_of: str | None = None, spot_row: dict[str, Any] | None = None) -> PriceSnapshot:
        df = self.price_history(code, as_of=as_of, days=220)
        if df.empty:
            spot_price = safe_float((spot_row or {}).get("latest_price"))
            if spot_price is not None:
                return PriceSnapshot(code, None, spot_price, None, None, None, None, None, None, None, source="spot", warning="history_missing")
            return PriceSnapshot(code, None, None, None, None, None, None, None, None, None, paused=True, warning="price_missing")

        latest = df.iloc[-1]
        close = safe_float(latest.get("收盘"))
        open_price = safe_float(latest.get("开盘"))
        high = safe_float(latest.get("最高"))
        low = safe_float(latest.get("最低"))
        amount = safe_float(latest.get("成交额"))
        closes = pd.to_numeric(df["收盘"], errors="coerce").dropna()
        amounts = pd.to_numeric(df.get("成交额", pd.Series(dtype=float)), errors="coerce").dropna()
        momentum_20 = pct_change(float(closes.iloc[-21]), float(closes.iloc[-1])) if len(closes) >= 21 else None
        momentum_60 = pct_change(float(closes.iloc[-61]), float(closes.iloc[-1])) if len(closes) >= 61 else None
        avg_amount_20 = float(amounts.tail(20).mean()) if len(amounts) else None
        if len(closes) >= 61:
            returns = closes.pct_change().tail(60).dropna()
            low_volatility_60 = float(returns.std(ddof=0)) if not returns.empty else None
        else:
            low_volatility_60 = None
        return PriceSnapshot(
            code=code,
            trade_date=str(latest.get("日期")),
            close=close,
            open=open_price,
            high=high,
            low=low,
            amount=amount,
            momentum_20=momentum_20,
            momentum_60=momentum_60,
            avg_amount_20=avg_amount_20,
            low_volatility_60=low_volatility_60,
            paused=is_truthy(latest.get("停牌")),
            source=str(latest.get("source", "")),
        )

    def dividend_yield(self, code: str, as_of: str | None = None) -> float | None:
        """Return TTM dividend yield (percent, e.g. 2.5 == 2.5%) for ``code``.

        Tushare provides ``dv_ttm`` directly via ``daily_basic`` which we
        merge into ``spot()``. Baostock does not expose dividend-yield, so
        the fallback returns ``None`` rather than raising.
        """

        cache_name = f"dividend_yield_{code}_{self._date_stamp(as_of)}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return safe_float(cached.iloc[0].get("dividend_yield"))

        if self.offline:
            self._raise_cache_miss("dividend_yield", cache_name)

        spot_df = self.spot()
        if spot_df.empty:
            return None
        row = spot_df[spot_df["code"] == normalize_code(code)]
        if row.empty:
            return None
        value = safe_float(row.iloc[0].get("dividend_yield"))
        if value is None:
            return None
        self.save_cache(cache_name, pd.DataFrame([{"code": normalize_code(code), "dividend_yield": value}]))
        return value

    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None) -> ExecutionQuote:
        visible_as_of = as_of or execute_after
        df = self.price_history(code, as_of=visible_as_of, days=45)
        if df.empty:
            return ExecutionQuote(code=code, trade_date=None, price=None, reason="execution_quote_missing")
        df = df.copy()
        target = pd.to_datetime(execute_after).date()
        visible_until = pd.to_datetime(visible_as_of).date()
        df["_date"] = pd.to_datetime(df["日期"]).dt.date
        visible = df[(df["_date"] >= target) & (df["_date"] <= visible_until)].copy()
        if visible.empty:
            return ExecutionQuote(code=code, trade_date=None, price=None, reason="execution_quote_not_visible")
        row = visible.iloc[0]
        price = safe_float(row.get("开盘")) or safe_float(row.get("收盘"))
        trade_date = str(row.get("日期"))
        if price is None or price <= 0:
            return ExecutionQuote(code=code, trade_date=trade_date, price=None, reason="execution_price_missing")

        previous = df[df["_date"] < row["_date"]]
        previous_close = safe_float(previous.iloc[-1].get("收盘")) if not previous.empty else None
        upper, lower = price_limit_bounds(previous_close, code, is_truthy(row.get("is_st")))
        paused = is_truthy(row.get("停牌"))
        limit_up = upper is not None and price >= upper - 0.005
        limit_down = lower is not None and price <= lower + 0.005
        reason = ""
        if paused:
            reason = "paused"
        elif side == "buy" and limit_up:
            reason = "limit_up_buy_blocked"
        elif side == "sell" and limit_down:
            reason = "limit_down_sell_blocked"
        return ExecutionQuote(
            code=code,
            trade_date=trade_date,
            price=price,
            paused=paused,
            limit_up=limit_up,
            limit_down=limit_down,
            source=str(row.get("source", "")),
            reason=reason,
        )

    def execution_price(self, code: str, execute_after: str, side: str) -> tuple[float | None, str | None]:
        quote = self.execution_quote(code, execute_after, side, as_of=execute_after)
        if quote.reason:
            return None, quote.trade_date
        return quote.price, quote.trade_date

    def benchmark_close(self, benchmark_code: str, as_of: str | None = None) -> tuple[float | None, str | None]:
        cache_name = f"benchmark_{benchmark_code}_{self._date_stamp(as_of)}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            row = cached.iloc[0]
            return safe_float(row.get("close")), str(row.get("trade_date"))

        if self.offline:
            self._raise_cache_miss("benchmark_close", cache_name)

        try:
            df = self.index_daily(benchmark_code, as_of or self._date_stamp())
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"benchmark_{benchmark_code}", "failed", str(exc))
            return None, None

        if df.empty:
            return None, None
        if as_of:
            target = pd.to_datetime(as_of).date()
            df = df[pd.to_datetime(df["日期"]).dt.date <= target]
        if df.empty:
            return None, None
        row = df.sort_values("日期").iloc[-1]
        close = safe_float(row.get("收盘"))
        trade_date = str(row.get("日期"))
        if close is not None and trade_date:
            self.save_cache(cache_name, pd.DataFrame([{"benchmark_code": benchmark_code, "close": close, "trade_date": trade_date}]))
        return close, trade_date


# ---------------------------------------------------------------------------
# Tushare provider
# ---------------------------------------------------------------------------


class TushareProvider(DataProvider):
    """Primary data provider backed by Tushare Pro.

    Tushare gives us full-market spot + daily_basic in two HTTP calls per
    day (no per-stock loop for the realtime view) and per-stock financial
    indicators via ``pro.fina_indicator``. The 2000-credit tier caps us at
    200 calls/minute, so we sleep briefly between consecutive calls.
    """

    def __init__(
        self,
        token: str | None,
        cache_dir: str | Path | None = None,
        *,
        offline: bool = False,
        as_of: str | None = None,
    ) -> None:
        super().__init__(cache_dir=cache_dir, offline=offline, as_of=as_of)
        token = (token or "").strip()
        if not token:
            raise TushareTokenMissing()
        # Token is kept private and never logged.
        self._token = token
        self._pro: Any | None = None
        self._stock_basic_df: pd.DataFrame | None = None
        self._last_call_at: float = 0.0

    @property
    def pro(self) -> Any:
        if self._pro is None:
            import tushare as ts  # type: ignore

            self._pro = ts.pro_api(self._token)
        return self._pro

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call_at
        if elapsed < TUSHARE_RATE_SLEEP_S:
            time.sleep(TUSHARE_RATE_SLEEP_S - elapsed)
        self._last_call_at = time.time()

    def _safe_pro_call(self, label: str, func: Callable[[], Any]) -> Any:
        """Wrap a ``pro.*`` call with rate limiting and error scrubbing.

        Any exception raised by Tushare may include the request URL; we
        deliberately recreate the exception with a token-stripped message
        so logs / runs.csv never leak credentials.
        """

        self._throttle()
        try:
            result = func()
            rows = len(result) if hasattr(result, "__len__") else None
            self.record_health(label, "ok", rows=rows)
            return result
        except Exception as exc:  # noqa: BLE001
            scrubbed = _scrub_token(str(exc), self._token)
            self.record_health(label, "failed", scrubbed)
            raise RuntimeError(f"{label} failed: {scrubbed}") from None

    # -- spec primitives ---------------------------------------------------

    def spot(self) -> pd.DataFrame:
        if self._spot_df is not None:
            return self._spot_df.copy()

        cache_name = f"spot_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            self._spot_df = normalize_spot(cached)
            return self._spot_df.copy()

        if self.offline:
            self._raise_cache_miss("spot", cache_name)

        trade_date = self._date_stamp()
        try:
            daily_basic = self._safe_pro_call(
                "spot_daily_basic",
                lambda: self.pro.daily_basic(
                    trade_date=trade_date,
                    fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps_ttm,dv_ttm,total_share,float_share,total_mv,circ_mv",
                ),
            )
            daily = self._safe_pro_call(
                "spot_daily",
                lambda: self.pro.daily(
                    trade_date=trade_date,
                    fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
                ),
            )
        except Exception:  # noqa: BLE001
            self._spot_df = pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
            return self._spot_df.copy()

        if daily_basic is None or daily_basic.empty or daily is None or daily.empty:
            self.record_health("spot", "failed", f"empty result for trade_date={trade_date}")
            self._spot_df = pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
            return self._spot_df.copy()

        # Drop daily.close before merging — daily_basic.close is the same EOD
        # value (post-settlement) and avoids the close_daily / close_basic
        # suffix split that breaks downstream `.get("close")` lookups.
        daily_no_close = daily.drop(columns=["close"], errors="ignore")
        merged = daily_no_close.merge(daily_basic, on=["ts_code", "trade_date"], how="outer")
        merged["code"] = merged["ts_code"].map(strip_ts_suffix)
        merged["latest_price"] = pd.to_numeric(merged.get("close"), errors="coerce")
        # pe_ttm is the TTM PE we want; fall back to plain pe when missing.
        merged["pe"] = merged.get("pe_ttm").where(merged.get("pe_ttm").notna(), merged.get("pe"))
        # Tushare total_mv is in 万元; convert to 亿元.
        merged["market_cap_yi"] = pd.to_numeric(merged.get("total_mv"), errors="coerce") / 10_000
        merged["dividend_yield"] = pd.to_numeric(merged.get("dv_ttm"), errors="coerce")
        # Tushare daily.amount is in 千元; convert to 元 for downstream
        # avg_amount_20 calculations that compare against 元-denominated
        # thresholds (e.g. min_avg_amount_20 = 1e8).
        merged["amount_yuan"] = pd.to_numeric(merged.get("amount"), errors="coerce") * 1000

        # We do not have ``name`` in daily/daily_basic — pull from stock_basic.
        try:
            basic = self.stock_basic()
        except Exception as exc:  # noqa: BLE001
            self.record_health("spot_stock_basic", "failed", str(exc))
            basic = pd.DataFrame(columns=["code", "name", "industry"])
        if not basic.empty:
            merged = merged.merge(basic[["code", "name"]], on="code", how="left")
        else:
            merged["name"] = ""

        out = pd.DataFrame()
        out["code"] = merged["code"]
        out["name"] = merged["name"].fillna("")
        out["latest_price"] = pd.to_numeric(merged["latest_price"], errors="coerce")
        out["pe"] = pd.to_numeric(merged["pe"], errors="coerce")
        out["pb"] = pd.to_numeric(merged.get("pb"), errors="coerce")
        out["market_cap_yi"] = merged["market_cap_yi"]
        out["dividend_yield"] = merged["dividend_yield"]
        out["amount"] = merged["amount_yuan"]
        out = out[out["code"].astype(bool)].drop_duplicates("code")

        self.save_cache(cache_name, out)
        self._spot_df = out
        return out.copy()

    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        ts_code = ts_code_for_stock(code)
        df = self._safe_pro_call(
            f"daily_{code}",
            lambda: self.pro.daily(
                ts_code=ts_code,
                start_date=start,
                end_date=end,
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["日期"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
        df["开盘"] = pd.to_numeric(df["open"], errors="coerce")
        df["收盘"] = pd.to_numeric(df["close"], errors="coerce")
        df["最高"] = pd.to_numeric(df["high"], errors="coerce")
        df["最低"] = pd.to_numeric(df["low"], errors="coerce")
        # Tushare amount unit is 千元 → convert to 元.
        df["成交额"] = pd.to_numeric(df["amount"], errors="coerce") * 1000
        df["停牌"] = False
        df["is_st"] = False
        df["source"] = "tushare_daily"
        out = df[["日期", "开盘", "收盘", "最高", "最低", "成交额", "停牌", "is_st", "source"]].copy()
        return out.sort_values("日期").reset_index(drop=True)

    def fina_indicator(self, code: str) -> pd.DataFrame:
        ts_code = ts_code_for_stock(code)
        df = self._safe_pro_call(
            f"fina_indicator_{code}",
            lambda: self.pro.fina_indicator(
                ts_code=ts_code,
                fields="ts_code,end_date,roe,grossprofit_margin,debt_to_assets,netprofit_yoy",
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df = df.sort_values("end_date", ascending=False).reset_index(drop=True)
        return df

    def stock_basic(self) -> pd.DataFrame:
        if self._stock_basic_df is not None:
            return self._stock_basic_df.copy()

        cache_name = f"stock_basic_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            self._stock_basic_df = cached.copy()
            return self._stock_basic_df.copy()

        if self.offline:
            self._raise_cache_miss("stock_basic", cache_name)

        df = self._safe_pro_call(
            "stock_basic",
            lambda: self.pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,area,industry,list_date,delist_date,market",
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "name", "industry", "list_date"])
        df = df.copy()
        df["code"] = df["symbol"].map(normalize_code)
        out = df[["code", "name", "industry", "list_date"]].drop_duplicates("code")
        self.save_cache(cache_name, out)
        self._stock_basic_df = out
        return out.copy()

    def index_weight(self, scope: str, trade_date: str) -> pd.DataFrame:
        index_code = INDEX_CODES.get(scope, scope)
        ts_code = ts_code_for_index(index_code)
        if not ts_code:
            return pd.DataFrame(columns=["code", "name"])
        # index_weight is published monthly — query a 90-day window ending at
        # ``trade_date`` and take the most recent snapshot.
        end_date = trade_date
        start_date = previous_calendar_date(95, _to_iso(trade_date))
        df = self._safe_pro_call(
            f"index_weight_{index_code}",
            lambda: self.pro.index_weight(
                index_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "name"])
        df = df.copy()
        df = df.sort_values("trade_date", ascending=False)
        latest_date = df["trade_date"].iloc[0]
        df = df[df["trade_date"] == latest_date]
        df["code"] = df["con_code"].map(strip_ts_suffix)
        # Optional name lookup via stock_basic when available; otherwise blank.
        name_lookup: dict[str, str] = {}
        try:
            basic = self.stock_basic()
            if not basic.empty:
                name_lookup = dict(zip(basic["code"].astype(str), basic["name"].astype(str)))
        except Exception:  # noqa: BLE001
            pass
        df["name"] = df["code"].map(lambda c: name_lookup.get(c, ""))
        return df[["code", "name"]].drop_duplicates("code")

    def index_daily(self, code: str, as_of: str) -> pd.DataFrame:
        ts_code = ts_code_for_index(code) or code
        end_date = _normalize_yyyymmdd(as_of)
        start_date = previous_calendar_date(45, _to_iso(end_date))
        df = self._safe_pro_call(
            f"index_daily_{code}",
            lambda: self.pro.index_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["日期", "收盘"])
        out = pd.DataFrame()
        out["日期"] = pd.to_datetime(df["trade_date"]).dt.date.astype(str)
        out["收盘"] = pd.to_numeric(df["close"], errors="coerce")
        return out.sort_values("日期").dropna(subset=["收盘"]).reset_index(drop=True)

    def dividend(self, code: str) -> pd.DataFrame:
        ts_code = ts_code_for_stock(code)
        df = self._safe_pro_call(
            f"dividend_{code}",
            lambda: self.pro.dividend(
                ts_code=ts_code,
                fields="ts_code,end_date,ann_date,cash_div_tax,cash_div,ex_date",
            ),
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "ann_date", "end_date", "cash_div", "ex_date"])
        df = df.copy()
        df["code"] = df["ts_code"].map(strip_ts_suffix)
        return df[["code", "ann_date", "end_date", "cash_div", "ex_date"]]

    def trade_cal(self) -> list[str]:
        # Pull the last 2 years and the next year for safety; the result is
        # cached so this only happens once per day.
        today = date.today()
        start = f"{today.year - 2}0101"
        end = f"{today.year + 1}1231"
        df = self._safe_pro_call(
            "trade_cal",
            lambda: self.pro.trade_cal(
                exchange="SSE",
                start_date=start,
                end_date=end,
                is_open="1",
            ),
        )
        if df is None or df.empty:
            return []
        dates = pd.to_datetime(df["cal_date"], format="%Y%m%d", errors="coerce").dropna().dt.date.astype(str)
        return sorted(set(dates.tolist()))


# ---------------------------------------------------------------------------
# Baostock provider
# ---------------------------------------------------------------------------


class BaostockProvider(DataProvider):
    """Fallback data provider backed by Baostock.

    No token required. Notably slower for full-market queries (one HTTP per
    stock for OHLCV), but sufficient when Tushare is unreachable or
    short-staffed (e.g. weekend rollover). Some fields Tushare exposes
    natively are not available here (most notably ``dividend_yield``); we
    return ``None`` rather than fabricate values.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        offline: bool = False,
        as_of: str | None = None,
    ) -> None:
        super().__init__(cache_dir=cache_dir, offline=offline, as_of=as_of)
        self._logged_in = False

    def _login(self) -> Any:
        try:
            import baostock as bs  # type: ignore
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError("baostock is not installed") from exc
        if not self._logged_in:
            result = bs.login()
            if getattr(result, "error_code", "0") != "0":
                raise RuntimeError(getattr(result, "error_msg", "baostock login failed"))
            self._logged_in = True
            self.record_health("baostock_login", "ok")
        return bs

    @staticmethod
    def _to_dataframe(result: Any) -> pd.DataFrame:
        if getattr(result, "error_code", "0") != "0":
            raise RuntimeError(getattr(result, "error_msg", "baostock query failed"))
        rows: list[list[Any]] = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    # -- spec primitives ---------------------------------------------------

    def spot(self) -> pd.DataFrame:
        if self._spot_df is not None:
            return self._spot_df.copy()

        cache_name = f"spot_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            self._spot_df = normalize_spot(cached)
            return self._spot_df.copy()

        if self.offline:
            self._raise_cache_miss("spot", cache_name)

        # Baostock has no "all-market spot" call. The pragmatic fallback is
        # to leave spot empty and let prepare-market-data drive per-stock
        # ``daily`` to populate latest-price/PE/PB via ``stock_basic`` +
        # ``history``. The empty result triggers ``status=failed`` upstream,
        # which signals to the operator that Tushare is unreachable and
        # they need to investigate. We still record a structured warning so
        # ``data_health.json`` shows the failure plainly.
        self.record_health(
            "spot",
            "failed",
            "baostock has no full-market spot endpoint; rerun once tushare recovers",
        )
        self._spot_df = pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
        return self._spot_df.copy()

    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        bs = self._login()
        result = bs.query_history_k_data_plus(
            baostock_code(code),
            "date,code,open,high,low,close,volume,amount,tradestatus,peTTM,pbMRQ,isST",
            start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
            end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
            frequency="d",
            adjustflag="2",
        )
        df = self._to_dataframe(result)
        if df.empty:
            return pd.DataFrame()
        out = pd.DataFrame()
        out["日期"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        out["开盘"] = pd.to_numeric(df["open"], errors="coerce")
        out["收盘"] = pd.to_numeric(df["close"], errors="coerce")
        out["最高"] = pd.to_numeric(df["high"], errors="coerce")
        out["最低"] = pd.to_numeric(df["low"], errors="coerce")
        out["成交额"] = pd.to_numeric(df["amount"], errors="coerce")
        out["停牌"] = df["tradestatus"].astype(str).ne("1")
        out["is_st"] = df["isST"].astype(str).eq("1")
        out["pe"] = pd.to_numeric(df["peTTM"], errors="coerce")
        out["pb"] = pd.to_numeric(df["pbMRQ"], errors="coerce")
        out["source"] = "baostock_history"
        return out.sort_values("日期").reset_index(drop=True)

    def fina_indicator(self, code: str) -> pd.DataFrame:
        bs = self._login()
        # Try the four most recent quarters in descending order.
        today = date.today()
        quarters = [
            (today.year, 1),
            (today.year - 1, 4),
            (today.year - 1, 3),
            (today.year - 1, 2),
            (today.year - 1, 1),
        ]
        rows: list[dict[str, Any]] = []
        for year, quarter in quarters:
            profit_df = self._to_dataframe(bs.query_profit_data(code=baostock_code(code), year=year, quarter=quarter))
            balance_df = self._to_dataframe(bs.query_balance_data(code=baostock_code(code), year=year, quarter=quarter))
            growth_df = self._to_dataframe(bs.query_growth_data(code=baostock_code(code), year=year, quarter=quarter))
            row: dict[str, Any] = {"end_date": f"{year}Q{quarter}"}
            if not profit_df.empty:
                first = profit_df.iloc[0]
                row["roe"] = ratio_to_percent(safe_float(first.get("roeAvg")))
                row["grossprofit_margin"] = ratio_to_percent(safe_float(first.get("gpMargin")))
            if not balance_df.empty:
                asset_to_equity = safe_float(balance_df.iloc[0].get("assetToEquity"))
                if asset_to_equity and asset_to_equity > 1:
                    row["debt_to_assets"] = (1 - 1 / asset_to_equity) * 100
            if not growth_df.empty:
                growth_value = safe_float(growth_df.iloc[0].get("YOYPNI"))
                if growth_value is None:
                    growth_value = safe_float(growth_df.iloc[0].get("YOYNI"))
                row["netprofit_yoy"] = ratio_to_percent(growth_value)
            # Only keep this quarter if at least one field landed.
            if {k for k in row if k != "end_date"}:
                rows.append(row)
            if len(rows) >= 4:
                break
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def stock_basic(self) -> pd.DataFrame:
        cache_name = f"stock_basic_{self._date_stamp()}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return cached.copy()

        if self.offline:
            self._raise_cache_miss("stock_basic", cache_name)

        bs = self._login()
        result = bs.query_stock_basic()
        df = self._to_dataframe(result)
        if df.empty:
            return pd.DataFrame(columns=["code", "name", "industry", "list_date"])
        out = pd.DataFrame()
        out["code"] = df["code"].map(lambda v: normalize_code(str(v).replace(".", "")))
        out["name"] = df.get("code_name", "")
        out["industry"] = ""
        out["list_date"] = df.get("ipoDate", "").astype(str).str.replace("-", "", regex=False)
        out = out[out["code"].astype(bool)].drop_duplicates("code")
        self.save_cache(cache_name, out)
        return out.copy()

    def index_weight(self, scope: str, trade_date: str) -> pd.DataFrame:
        bs = self._login()
        index_code = INDEX_CODES.get(scope, scope)
        query_date = _to_iso(trade_date)
        if index_code == "000300":
            result = bs.query_hs300_stocks(date=query_date)
        elif index_code == "000905":
            result = bs.query_zz500_stocks(date=query_date)
        else:
            raise RuntimeError(f"baostock does not support index constituents for {index_code}")
        df = self._to_dataframe(result)
        if df.empty:
            return pd.DataFrame(columns=["code", "name"])
        out = pd.DataFrame()
        out["code"] = df["code"].map(lambda v: normalize_code(str(v).replace(".", "")))
        out["name"] = df.get("code_name", "")
        return out[out["code"].astype(bool)].drop_duplicates("code")

    def index_daily(self, code: str, as_of: str) -> pd.DataFrame:
        bs = self._login()
        end_iso = _to_iso(_normalize_yyyymmdd(as_of))
        start_iso = _to_iso(previous_calendar_date(45, end_iso))
        result = bs.query_history_k_data_plus(
            baostock_code(code),
            "date,close",
            start_date=start_iso,
            end_date=end_iso,
            frequency="d",
            adjustflag="2",
        )
        df = self._to_dataframe(result)
        if df.empty:
            return pd.DataFrame(columns=["日期", "收盘"])
        out = pd.DataFrame()
        out["日期"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        out["收盘"] = pd.to_numeric(df["close"], errors="coerce")
        return out.sort_values("日期").dropna(subset=["收盘"]).reset_index(drop=True)

    def dividend(self, code: str) -> pd.DataFrame:
        bs = self._login()
        # Baostock's dividend API requires year-by-year iteration.
        today = date.today()
        rows: list[pd.DataFrame] = []
        for year in range(today.year, today.year - 5, -1):
            try:
                result = bs.query_dividend_data(code=baostock_code(code), year=year, yearType="report")
                df = self._to_dataframe(result)
                if not df.empty:
                    rows.append(df)
            except Exception:  # noqa: BLE001
                continue
        if not rows:
            return pd.DataFrame(columns=["code", "ann_date", "end_date", "cash_div", "ex_date"])
        merged = pd.concat(rows, ignore_index=True)
        out = pd.DataFrame()
        out["code"] = merged.get("code", "").map(lambda v: normalize_code(str(v).replace(".", "")))
        out["ann_date"] = merged.get("dividPreNoticeDate", "")
        out["end_date"] = merged.get("dividOperateDate", "")
        out["cash_div"] = pd.to_numeric(merged.get("dividCashPsBeforeTax", 0), errors="coerce")
        out["ex_date"] = merged.get("dividPayDate", "")
        return out

    def trade_cal(self) -> list[str]:
        bs = self._login()
        today = date.today()
        end_iso = f"{today.year + 1}-12-31"
        start_iso = f"{today.year - 2}-01-01"
        result = bs.query_trade_dates(start_date=start_iso, end_date=end_iso)
        df = self._to_dataframe(result)
        if df.empty:
            return []
        df = df[df["is_trading_day"].astype(str) == "1"]
        return sorted(set(df["calendar_date"].astype(str).tolist()))


class AkshareProvider(TushareProvider):
    """Backwards-compat shim for legacy callers and test fixtures.

    The historical ``AkshareProvider(cache_dir=..., offline=True)``
    constructor takes no token. We preserve that signature here so
    subclasses (e.g. ``FakeProvider``, ``HistoryProvider`` in the test
    suite) continue to work without per-test edits. When a token *is*
    provided, behaviour is identical to ``TushareProvider``; without one,
    the provider operates in cache-only mode and refuses to make any
    network call (it raises a structured ``CacheMiss`` instead).
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        offline: bool = False,
        as_of: str | None = None,
        token: str | None = None,
    ) -> None:
        resolved_token = (token if token is not None else os.environ.get(TUSHARE_TOKEN_ENV, "")).strip()
        # When a token is present, defer to the normal TushareProvider
        # init so production callers see identical behaviour.
        if resolved_token:
            super().__init__(resolved_token, cache_dir=cache_dir, offline=offline, as_of=as_of)
            return
        # No token: bypass the parent init's token check by stepping into
        # the abstract base directly. We force ``offline`` semantics for
        # any spec primitive (``spot``, ``daily`` etc.) by stubbing the
        # ``pro`` property to raise — but cache-first reads still work,
        # which is exactly what the legacy tests rely on.
        DataProvider.__init__(self, cache_dir=cache_dir, offline=offline, as_of=as_of)
        self._token = ""
        self._pro = None
        self._stock_basic_df = None
        self._last_call_at = 0.0

    @property
    def pro(self) -> Any:  # type: ignore[override]
        if not self._token:
            raise TushareTokenMissing()
        return super().pro


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_provider(
    token: str | None = None,
    cache_dir: str | Path | None = None,
    *,
    offline: bool = False,
    as_of: str | None = None,
) -> DataProvider:
    """Return a configured provider.

    With a token present we return :class:`TushareProvider`. If no token
    is provided (and the env var is unset), we drop down to
    :class:`BaostockProvider` rather than raising — this keeps the local
    dev experience friction-free for contributors who haven't yet signed
    up for a Tushare account.
    """

    resolved_token = (token if token is not None else os.environ.get(TUSHARE_TOKEN_ENV, "")).strip()
    if resolved_token:
        return TushareProvider(resolved_token, cache_dir=cache_dir, offline=offline, as_of=as_of)
    return BaostockProvider(cache_dir=cache_dir, offline=offline, as_of=as_of)


# ---------------------------------------------------------------------------
# Helpers (kept identical to the pre-migration behaviour so downstream
# callers don't need to update)
# ---------------------------------------------------------------------------


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def strip_ts_suffix(value: Any) -> str:
    """Strip ``.SH`` / ``.SZ`` / ``.BJ`` from a Tushare ``ts_code``."""

    text = str(value or "").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    return normalize_code(text)


def ts_code_for_stock(code: str) -> str:
    """Return a Tushare-style ``<code>.SH/.SZ/.BJ`` from a raw 6-digit code."""

    normalized = normalize_code(code)
    if not normalized:
        return ""
    if normalized.startswith(("5", "6", "9")):
        return f"{normalized}.SH"
    if normalized.startswith(("8", "4")):
        return f"{normalized}.BJ"
    return f"{normalized}.SZ"


def ts_code_for_index(code: str) -> str | None:
    """Return a Tushare-style index ts_code (``000300.SH``) or ``None``."""

    normalized = normalize_code(code) or code
    suffix = INDEX_TUSHARE_SUFFIX.get(normalized)
    if not suffix:
        # Default to SH for legacy index codes we have not seen.
        if normalized.isdigit() and len(normalized) == 6:
            suffix = "SH" if normalized.startswith("0") else "SZ"
        else:
            return None
    return f"{normalized}.{suffix}"


def market_symbol(code: str) -> str:
    """Return a sina-style market symbol (``sh600519``) for legacy callers."""

    normalized = normalize_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh{normalized}"
    if normalized.startswith(("8", "4")):
        return f"bj{normalized}"
    return f"sz{normalized}"


def baostock_code(code: str) -> str:
    normalized = normalize_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh.{normalized}"
    if normalized.startswith(("8", "4")):
        return f"bj.{normalized}"
    return f"sz.{normalized}"


def ratio_to_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100 if abs(value) <= 1 else value


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_trading_calendar(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty:
        return []
    date_col = first_available_column(df, ["trade_date", "日期", "date", "calendarDate"])
    if not date_col:
        return []
    dates = pd.to_datetime(df[date_col], errors="coerce").dropna().dt.date.astype(str)
    return sorted(set(dates.tolist()))


def price_limit_bounds(previous_close: float | None, code: str, is_st: bool = False) -> tuple[float | None, float | None]:
    if previous_close is None or previous_close <= 0:
        return None, None
    rate = price_limit_rate(code, is_st)
    return round(previous_close * (1 + rate), 2), round(previous_close * (1 - rate), 2)


def price_limit_rate(code: str, is_st: bool = False) -> float:
    normalized = normalize_code(code)
    if is_st:
        return 0.05
    if normalized.startswith(("300", "301", "688", "689")):
        return 0.20
    if normalized.startswith(("8", "4")):
        return 0.30
    return 0.10


def normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
    out = df.copy()
    code_col = first_available_column(out, ["代码", "code", "股票代码", "ts_code"])
    name_col = first_available_column(out, ["名称", "name", "股票简称"])
    price_col = first_available_column(out, ["最新价", "最新", "price", "当前价格", "latest_price", "close"])
    pe_col = first_available_column(out, ["市盈率-动态", "市盈率", "pe", "pe_ttm"])
    pb_col = first_available_column(out, ["市净率", "pb"])
    market_cap_col = first_available_column(out, ["市值_yi", "market_cap_yi", "市值", "总市值", "market_cap", "total_mv"])

    normalized = pd.DataFrame()
    normalized["code"] = out[code_col].map(normalize_code) if code_col else ""
    normalized["name"] = out[name_col].astype(str) if name_col else ""
    normalized["latest_price"] = out[price_col].map(safe_float) if price_col else None
    normalized["pe"] = out[pe_col].map(safe_float) if pe_col else None
    normalized["pb"] = out[pb_col].map(safe_float) if pb_col else None
    if market_cap_col == "market_cap_yi":
        normalized["market_cap_yi"] = pd.to_numeric(out[market_cap_col].map(safe_float), errors="coerce")
    elif market_cap_col == "total_mv":
        # Tushare unit is 万元.
        normalized["market_cap_yi"] = pd.to_numeric(out[market_cap_col].map(safe_float), errors="coerce") / 10_000
    elif market_cap_col:
        # Legacy AKShare / EM column in 元.
        normalized["market_cap_yi"] = pd.to_numeric(out[market_cap_col].map(safe_float), errors="coerce") / 100_000_000
    else:
        normalized["market_cap_yi"] = None
    if "dividend_yield" in out.columns:
        normalized["dividend_yield"] = out["dividend_yield"].map(safe_float)
    if "amount" in out.columns:
        normalized["amount"] = out["amount"].map(safe_float)
    normalized = normalized[normalized["code"] != ""]
    return normalized.drop_duplicates("code")


def normalize_constituents(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name"])
    code_col = first_available_column(df, ["成分券代码", "品种代码", "代码", "code", "con_code"])
    name_col = first_available_column(df, ["成分券名称", "品种名称", "名称", "name"])
    if not code_col:
        return pd.DataFrame(columns=["code", "name"])
    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["name"] = df[name_col].astype(str) if name_col else ""
    return out[out["code"] != ""].drop_duplicates("code")


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交额", "source"])
    out = df.copy()
    date_col = first_available_column(out, ["日期", "date", "trade_date"])
    open_col = first_available_column(out, ["开盘", "open"])
    close_col = first_available_column(out, ["收盘", "close"])
    high_col = first_available_column(out, ["最高", "high"])
    low_col = first_available_column(out, ["最低", "low"])
    amount_col = first_available_column(out, ["成交额", "amount"])
    volume_col = first_available_column(out, ["成交量", "volume"])
    if not date_col or not close_col:
        return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交额", "source"])
    normalized = pd.DataFrame()
    normalized["日期"] = pd.to_datetime(out[date_col]).dt.date.astype(str)
    normalized["开盘"] = out[open_col].map(safe_float) if open_col else None
    normalized["收盘"] = out[close_col].map(safe_float)
    normalized["最高"] = out[high_col].map(safe_float) if high_col else None
    normalized["最低"] = out[low_col].map(safe_float) if low_col else None
    if amount_col:
        normalized["成交额"] = normalize_amount_series(out[amount_col].map(safe_float))
    elif volume_col:
        normalized["成交额"] = pd.to_numeric(out[volume_col].map(safe_float), errors="coerce") * pd.to_numeric(normalized["收盘"], errors="coerce")
    else:
        normalized["成交额"] = None
    if "停牌" in out.columns:
        normalized["停牌"] = out["停牌"]
    elif "tradestatus" in out.columns:
        normalized["停牌"] = out["tradestatus"].astype(str).ne("1")
    else:
        normalized["停牌"] = False
    if "is_st" in out.columns:
        normalized["is_st"] = out["is_st"]
    elif "isST" in out.columns:
        normalized["is_st"] = out["isST"].astype(str).eq("1")
    else:
        normalized["is_st"] = False
    if "pe" in out.columns:
        normalized["pe"] = out["pe"].map(safe_float)
    elif "peTTM" in out.columns:
        normalized["pe"] = out["peTTM"].map(safe_float)
    else:
        normalized["pe"] = None
    if "pb" in out.columns:
        normalized["pb"] = out["pb"].map(safe_float)
    elif "pbMRQ" in out.columns:
        normalized["pb"] = out["pbMRQ"].map(safe_float)
    else:
        normalized["pb"] = None
    normalized["source"] = out["source"].astype(str) if "source" in out.columns else "history"
    return normalized.sort_values("日期").dropna(subset=["收盘"])


def normalize_amount_series(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.dropna().empty:
        return numeric
    # Some legacy caches report turnover in 万元 while we keep canonical 元.
    # Heuristic: if the 95th percentile is < 10 million we are looking at
    # 万元; bump to 元.
    high_quantile = numeric[numeric > 0].quantile(0.95)
    if pd.notna(high_quantile) and 0 < high_quantile < 10_000_000:
        return numeric * 10_000
    return numeric


def parse_financial_metrics(df: pd.DataFrame) -> dict[str, Any]:
    """Legacy AKShare-format parser, kept so unit tests / archived cache
    files that still carry an ``指标`` column read out identically.

    The new TushareProvider does not call this — it uses
    ``fina_indicator`` columns directly — but keeping the helper around
    means downstream consumers of older cache CSVs (legacy archives,
    Baostock-derived test fixtures) keep working.
    """

    if df is None or df.empty:
        return {}
    if "指标" in df.columns:
        period_cols = sorted([col for col in df.columns if re.fullmatch(r"\d{8}", str(col))], reverse=True)
        if not period_cols:
            return {}
        return drop_missing_metrics({
            "roe": extract_metric(df, ["净资产收益率"], period_cols),
            "gross_margin": extract_metric(df, ["毛利率"], period_cols),
            "debt_ratio": extract_metric(df, ["资产负债率"], period_cols),
            "net_profit_growth": extract_metric(df, ["归属母公司净利润增长率", "净利润增长率"], period_cols),
        })

    latest = df.iloc[0]
    return drop_missing_metrics({
        "roe": safe_float(first_existing(latest, ["净资产收益率", "加权净资产收益率", "ROE", "roe"])),
        "gross_margin": safe_float(first_existing(latest, ["销售毛利率", "毛利率", "grossprofit_margin"])),
        "debt_ratio": safe_float(first_existing(latest, ["资产负债率", "debt_to_assets"])),
        "net_profit_growth": safe_float(first_existing(latest, ["净利润增长率", "归属母公司净利润增长率", "netprofit_yoy"])),
    })


def drop_missing_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if value is not None}


def extract_metric(df: pd.DataFrame, names: list[str], period_cols: list[str]) -> float | None:
    indicators = df["指标"].astype(str)
    mask = False
    for name in names:
        mask = mask | indicators.str.contains(name, na=False)
    matched = df[mask]
    if matched.empty:
        return None
    row = matched.iloc[0]
    for col in period_cols:
        value = safe_float(row.get(col))
        if value is not None:
            return value
    return None


def first_available_column(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def first_existing(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, "", "--"):
            return row[name]
    return None


def _normalize_yyyymmdd(value: str) -> str:
    """Accept ``YYYY-MM-DD`` or ``YYYYMMDD`` and return ``YYYYMMDD``."""

    text = str(value or "").strip().replace("-", "")
    return text


def _to_iso(value: str) -> str:
    """Accept ``YYYY-MM-DD`` or ``YYYYMMDD`` and return ``YYYY-MM-DD``."""

    text = str(value or "").strip()
    if "-" in text:
        return text
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _scrub_token(message: str, token: str) -> str:
    """Strip the literal token (and any 64-char hex substring) from text.

    Tushare's request errors often inline the request URL which carries
    the token as a query-string param; we make sure that value never
    reaches the cache or logs.
    """

    if not token:
        return message
    cleaned = message.replace(token, "***")
    cleaned = re.sub(r"token=[0-9a-fA-F]{16,}", "token=***", cleaned)
    return cleaned
