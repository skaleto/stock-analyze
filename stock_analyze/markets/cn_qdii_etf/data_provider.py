"""Tushare-backed provider for mainland-listed cross-border ETFs."""

from __future__ import annotations

import fcntl
import math
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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
from .universe import (
    UNIVERSES,
    build_catalog_candidates,
    catalog_content_hash,
    classify_scope,
    metadata_for_code,
    resolve_universe,
    select_liquid_representatives,
)
from ..a_share.data_provider import CacheMiss, TushareTokenMissing
from ..a_share.data_provider.base import TUSHARE_TOKEN_ENV
from ...utils import read_json, write_dataframe_csv_atomic, write_json


MAX_NAV_AGE_DAYS = 7
MAX_PRICE_AGE_DAYS = 7
INSTRUMENT_HISTORY_YEARS = 3
HISTORY_START_TOLERANCE_DAYS = 7
LIQUIDITY_LOOKBACK_DAYS = 45
TUSHARE_AMOUNT_TO_YUAN = 1_000.0
UNIVERSE_SCHEMA_VERSION = 2
UNIVERSE_RULES_VERSION = "2026-07-12-p0"
MAX_ETFS_PER_INDEX = 2
MAX_ETFS_PER_SCOPE = 24
FUND_BASIC_REQUIRED_COLUMNS = {"ts_code", "name", "benchmark", "list_date", "status"}
TUSHARE_FUND_DAILY_MIN_INTERVAL_S = 0.38
TUSHARE_FUND_DAILY_RATE_RETRIES = 2
TUSHARE_FUND_DAILY_RATE_BACKOFF_S = 61.0


@contextmanager
def _universe_file_lock(path: Path):
    """Serialize same-day catalog construction across the two agent services."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    history_start: str | None = None
    history_end: str | None = None
    history_complete: bool = False


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
        self._fund_daily_throttle_enabled = pro_client is None
        self._fund_daily_last_call_at = 0.0
        self._fund_daily_throttle_lock = threading.Lock()
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._daily_refresh_attempted: set[str] = set()
        self._nav_cache: dict[str, pd.DataFrame] = {}
        self._share_cache: dict[str, pd.DataFrame] = {}
        self._basic_cache: pd.DataFrame | None = None
        self._fund_basic_source = "unavailable"
        self._fund_basic_data_as_of: str | None = None
        self._universe_cache: dict[str, Any] | None = None
        self._selection_funnels: dict[str, dict[str, Any]] = {}
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

    @staticmethod
    def _is_fund_daily_rate_limit(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in ("频率超限", "rate limit", "too many requests", "429")
        )

    def _throttle_fund_daily(self) -> None:
        if not self._fund_daily_throttle_enabled:
            return
        with self._fund_daily_throttle_lock:
            elapsed = time.monotonic() - self._fund_daily_last_call_at
            if elapsed < TUSHARE_FUND_DAILY_MIN_INTERVAL_S:
                time.sleep(TUSHARE_FUND_DAILY_MIN_INTERVAL_S - elapsed)
            self._fund_daily_last_call_at = time.monotonic()

    def _call_fund_daily(self, **kwargs: Any) -> pd.DataFrame:
        for attempt in range(TUSHARE_FUND_DAILY_RATE_RETRIES + 1):
            self._throttle_fund_daily()
            try:
                return self.pro.fund_daily(**kwargs)
            except Exception as exc:
                if (
                    self._is_fund_daily_rate_limit(exc)
                    and attempt < TUSHARE_FUND_DAILY_RATE_RETRIES
                ):
                    self.record_health(
                        "fund_daily",
                        "rate_limited_retry",
                        f"attempt={attempt + 1}",
                    )
                    time.sleep(TUSHARE_FUND_DAILY_RATE_BACKOFF_S * (attempt + 1))
                    continue
                raise
        raise RuntimeError("fund_daily_retry_exhausted")

    def persist_health(self) -> None:
        if self.cache_dir is not None:
            write_json(self.cache_dir.parent / "data_health.json", self._health)

    def record_selection_funnel(self, scope: str, payload: dict[str, Any]) -> None:
        self._selection_funnels[str(scope)] = dict(payload)

    def selection_snapshot(self) -> dict[str, Any]:
        universe = self._universe_cache or {}
        return {
            "schema_version": 1,
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "as_of": universe.get("as_of") or _iso(_yyyymmdd(self.as_of)),
            "universe_hash": universe.get("universe_hash"),
            "universe_source_status": universe.get("source_status"),
            "catalog_stats": universe.get("catalog_stats") or {},
            "scopes": {scope: dict(payload) for scope, payload in self._selection_funnels.items()},
        }

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
        if scope not in UNIVERSES:
            raise ValueError(f"unknown cn_qdii_etf universe scope: {scope}")
        snapshot = self.universe_snapshot(self.as_of)
        return [str(row["code"]) for row in snapshot["scopes"].get(scope, [])]

    def _universe_paths(self, as_of_key: str) -> tuple[Path | None, Path | None]:
        if self.cache_dir is None:
            return None, None
        iso_date = _iso(as_of_key) or as_of_key
        shared_dir = self.cache_dir.parent
        return (
            shared_dir / "universe_snapshots" / f"{iso_date}.json",
            shared_dir / "universe_latest.json",
        )

    @staticmethod
    def _valid_universe_snapshot(payload: Any, as_of_key: str) -> bool:
        return bool(
            isinstance(payload, dict)
            and payload.get("schema_version") == UNIVERSE_SCHEMA_VERSION
            and payload.get("rules_version") == UNIVERSE_RULES_VERSION
            and _yyyymmdd(payload.get("as_of")) == as_of_key
            and isinstance(payload.get("scopes"), dict)
            and payload.get("universe_hash")
        )

    def universe_snapshot(self, as_of: str | None = None) -> dict[str, Any]:
        """Return one shared, versioned universe snapshot for both agents."""

        as_of_key = _yyyymmdd(as_of or self.as_of)
        if self._valid_universe_snapshot(self._universe_cache, as_of_key):
            return dict(self._universe_cache or {})
        dated_path, latest_path = self._universe_paths(as_of_key)
        if dated_path is not None:
            cached = read_json(dated_path, None)
            if self._valid_universe_snapshot(cached, as_of_key):
                self._universe_cache = cached
                self.record_health("etf_universe", "cache_hit", dated_path.name)
                return dict(cached)

        if dated_path is None or latest_path is None:
            return self._build_universe_snapshot(as_of_key)

        lock_path = dated_path.with_suffix(".lock")
        with _universe_file_lock(lock_path):
            # The peer agent may have populated the shared snapshot while this
            # process waited. Re-read before making any upstream API calls.
            cached = read_json(dated_path, None)
            if self._valid_universe_snapshot(cached, as_of_key):
                self._universe_cache = cached
                self.record_health("etf_universe", "cache_hit_after_lock", dated_path.name)
                return dict(cached)
            payload = self._build_universe_snapshot(as_of_key)
            write_json(dated_path, payload)
            write_json(latest_path, payload)
            return payload

    def _build_universe_snapshot(self, as_of_key: str) -> dict[str, Any]:
        source_status = "dynamic_fund_basic"
        try:
            candidates = build_catalog_candidates(
                self._fund_basic(refresh=True, as_of_key=as_of_key),
                as_of=as_of_key,
            )
            if self._fund_basic_source == "stale_cache_fallback":
                source_status = "cached_fund_basic_fallback"
        except Exception as exc:  # noqa: BLE001 - static seed is the explicit degraded path
            self.record_health("etf_universe", "catalog_failed", str(exc))
            candidates = []
            source_status = "static_fallback"
        if self.offline and not candidates:
            source_status = "static_fallback"

        discovery_counts = {
            scope: sum(row.get("scope") == scope for row in candidates)
            for scope in UNIVERSES
        }
        preselected = self._preselect_catalog_candidates(candidates)
        liquid_rows: list[dict[str, Any]] = []
        for candidate in preselected:
            try:
                liquid_rows.append(self._liquidity_candidate(candidate, as_of_key))
            except Exception as exc:  # noqa: BLE001 - one unavailable ETF must not erase a scope
                self.record_health(
                    "etf_universe",
                    "candidate_failed",
                    f"{candidate.get('code')}: {exc}",
                )
                liquid_rows.append(
                    {
                        **candidate,
                        "avg_amount_20": None,
                        "paused": True,
                        "warning": str(exc)[:200],
                    }
                )
        selected = select_liquid_representatives(
            liquid_rows,
            max_per_index=MAX_ETFS_PER_INDEX,
            max_per_scope=MAX_ETFS_PER_SCOPE,
        )
        for scope in UNIVERSES:
            if not selected.get(scope):
                source_status = "static_fallback" if not candidates else "partial_static_fallback"
                selected[scope] = self._fallback_scope_rows(scope, as_of_key)

        enriched: dict[str, list[dict[str, Any]]] = {scope: [] for scope in UNIVERSES}
        for scope, rows in selected.items():
            for candidate in rows:
                try:
                    snapshot = self.price_snapshot(str(candidate["code"]), as_of=as_of_key)
                    row = {
                        **candidate,
                        **asdict(snapshot),
                        "scope": scope,
                        "industry": scope,
                        "catalog_source": candidate.get("source") or "static_seed",
                        "price_source": snapshot.source,
                    }
                    try:
                        fund_size, share_date = self._latest_fund_size(
                            str(candidate["code"]),
                            snapshot.trade_date,
                            snapshot.nav,
                        )
                    except Exception as exc:  # noqa: BLE001 - size is an optional risk field
                        self.record_health(
                            "fund_share",
                            "failed",
                            f"{candidate.get('code')}: {exc}",
                        )
                        fund_size, share_date = None, None
                    row["fund_size_yuan"] = fund_size
                    row["fund_share_date"] = share_date
                except Exception as exc:  # noqa: BLE001
                    self.record_health(
                        "etf_universe",
                        "snapshot_failed",
                        f"{candidate.get('code')}: {exc}",
                    )
                    row = {
                        **candidate,
                        "scope": scope,
                        "industry": scope,
                        "paused": True,
                        "warning": str(exc)[:200],
                    }
                enriched[scope].append(row)

        self._attach_peer_tracking_error(enriched, as_of_key)

        universe_hash = catalog_content_hash(enriched)
        for rows in enriched.values():
            for row in rows:
                row["universe_hash"] = universe_hash
        payload = {
            "schema_version": UNIVERSE_SCHEMA_VERSION,
            "rules_version": UNIVERSE_RULES_VERSION,
            "as_of": _iso(as_of_key),
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "universe_hash": universe_hash,
            "amount_unit": "CNY_yuan",
            "source_status": source_status,
            "source_contract": {
                "catalog": "tushare.fund_basic",
                "catalog_data_as_of": self._fund_basic_data_as_of,
                "classification": "name+benchmark",
                "index_code_available": False,
            },
            "catalog_stats": {
                scope: {
                    "discovered": discovery_counts.get(scope, 0),
                    "prefetched": sum(row.get("scope") == scope for row in preselected),
                    "selected": len(enriched.get(scope, [])),
                    "index_count": len(
                        {row.get("index_key") for row in enriched.get(scope, []) if row.get("index_key")}
                    ),
                }
                for scope in UNIVERSES
            },
            "scopes": enriched,
        }
        self._universe_cache = payload
        self.record_health(
            "etf_universe",
            source_status,
            f"hash={universe_hash}",
            rows=sum(len(rows) for rows in enriched.values()),
        )
        return dict(payload)

    @staticmethod
    def _preselect_catalog_candidates(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        seed_codes = {code for codes in UNIVERSES.values() for code in codes}
        for candidate in candidates:
            grouped[(str(candidate["scope"]), str(candidate["index_key"]))].append(candidate)
        selected: list[dict[str, Any]] = []
        for key in sorted(grouped):
            rows = sorted(
                grouped[key],
                key=lambda row: (
                    str(row.get("code")) not in seed_codes,
                    str(row.get("list_date") or "9999-99-99"),
                    float(row.get("management_fee") or 99.0),
                    str(row.get("code") or ""),
                ),
            )
            selected.extend(rows)
        return selected

    def _liquidity_candidate(
        self,
        candidate: dict[str, Any],
        as_of_key: str,
    ) -> dict[str, Any]:
        code = str(candidate["code"])
        hist = self._fund_daily_liquidity(code, as_of_key)
        eligible = hist[hist["trade_date"].astype(str) <= as_of_key].sort_values("trade_date")
        if eligible.empty:
            return {**candidate, "avg_amount_20": None, "paused": True, "warning": "no fund_daily history"}
        latest = eligible.iloc[-1]
        amounts = pd.to_numeric(eligible.get("amount"), errors="coerce") * TUSHARE_AMOUNT_TO_YUAN
        return {
            **candidate,
            "trade_date": _iso(latest.get("trade_date")),
            "close": _safe_float(latest.get("close")),
            "amount": (
                _safe_float(latest.get("amount")) * TUSHARE_AMOUNT_TO_YUAN
                if _safe_float(latest.get("amount")) is not None
                else None
            ),
            "avg_amount_20": _safe_float(amounts.tail(20).mean()) if not amounts.dropna().empty else None,
            "paused": self._price_is_stale(str(latest.get("trade_date")), as_of_key),
        }

    def _fallback_scope_rows(self, scope: str, as_of_key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code in resolve_universe(scope):
            metadata = metadata_for_code(code)
            try:
                snap = self.price_snapshot(code, as_of=as_of_key)
                rows.append(
                    {
                        **metadata,
                        **asdict(snap),
                        "code": code,
                        "scope": scope,
                        "industry": scope,
                        "catalog_source": "static_seed",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        **metadata,
                        "code": code,
                        "scope": scope,
                        "industry": scope,
                        "paused": True,
                        "warning": str(exc)[:200],
                        "catalog_source": "static_seed",
                    }
                )
        return rows

    def _attach_peer_tracking_error(
        self,
        scopes: dict[str, list[dict[str, Any]]],
        as_of_key: str,
    ) -> None:
        for rows in scopes.values():
            by_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                if row.get("index_key"):
                    by_index[str(row["index_key"])].append(row)
            for peers in by_index.values():
                peers.sort(
                    key=lambda row: -float(row.get("avg_amount_20") or 0.0)
                )
                reference = str(peers[0].get("code") or "")
                for row in peers:
                    row["tracking_reference_code"] = reference
                    row["peer_tracking_error_applicable"] = len(peers) > 1
                    row["peer_tracking_error_60"] = (
                        self._peer_tracking_error_60(
                            str(row.get("code") or ""),
                            reference,
                            as_of_key,
                        )
                        if len(peers) > 1
                        else None
                    )

    def _peer_tracking_error_60(
        self,
        code: str,
        reference_code: str,
        as_of_key: str,
    ) -> float | None:
        if not code or not reference_code:
            return None
        if code == reference_code:
            return 0.0
        left = self._fund_daily(code, as_of_key)
        right = self._fund_daily(reference_code, as_of_key)
        if left.empty or right.empty:
            return None
        left_frame = left[["trade_date", "close"]].copy()
        right_frame = right[["trade_date", "close"]].copy()
        left_frame["trade_date"] = left_frame["trade_date"].astype(str)
        right_frame["trade_date"] = right_frame["trade_date"].astype(str)
        merged = left_frame.merge(
            right_frame,
            on="trade_date",
            suffixes=("_fund", "_reference"),
        ).sort_values("trade_date")
        fund_returns = pd.to_numeric(merged["close_fund"], errors="coerce").pct_change()
        reference_returns = pd.to_numeric(
            merged["close_reference"], errors="coerce"
        ).pct_change()
        differences = (fund_returns - reference_returns).dropna().tail(60)
        if len(differences) < 20:
            return None
        value = float(differences.std(ddof=1) * math.sqrt(252))
        return value if math.isfinite(value) else None

    def spot(self, scope: str) -> pd.DataFrame:
        if scope not in UNIVERSES:
            raise ValueError(f"unknown cn_qdii_etf universe scope: {scope}")
        snapshot = self.universe_snapshot(self.as_of)
        return pd.DataFrame([dict(row) for row in snapshot["scopes"].get(scope, [])])

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
        history_dates = hist["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        expected_history_start = self._history_start_key(as_of_key, list_date)
        history_complete = self._daily_covers_window(
            hist,
            expected_history_start,
            as_of_key,
        )
        closes = self._adjusted_closes(ts_code, hist, as_of_key)
        # Tushare fund_daily publishes amount in thousand yuan. Keep the raw
        # cache unchanged and normalize at the provider boundary so strategy,
        # risk, dashboard, and future data sources all speak RMB yuan.
        amounts = pd.to_numeric(hist.get("amount"), errors="coerce") * TUSHARE_AMOUNT_TO_YUAN
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
            amount=(
                _safe_float(latest.get("amount")) * TUSHARE_AMOUNT_TO_YUAN
                if _safe_float(latest.get("amount")) is not None
                else None
            ),
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
            history_start=_iso(history_dates.min()),
            history_end=_iso(history_dates.max()),
            history_complete=history_complete,
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

    def _fund_daily_liquidity(self, ts_code: str, as_of_key: str) -> pd.DataFrame:
        """Fetch a small window for catalog-wide liquidity ranking."""

        cache_name = self._cache_name("fund_daily_liquidity", ts_code, as_of_key)
        cached = self._read_cache(cache_name)
        if cached is not None:
            self.record_health(
                "fund_daily_liquidity",
                "cache_hit",
                cache_name,
                rows=len(cached),
            )
            return cached
        if self.offline:
            self.record_health("fund_daily_liquidity", "cache_miss", cache_name)
            raise CacheMiss(method="fund_daily_liquidity", cache_name=cache_name)
        end = datetime.strptime(as_of_key, "%Y%m%d").date()
        start = (end - timedelta(days=LIQUIDITY_LOOKBACK_DAYS)).strftime("%Y%m%d")
        try:
            df = self._call_fund_daily(
                ts_code=ts_code,
                start_date=start,
                end_date=as_of_key,
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
        except Exception as exc:
            self.record_health("fund_daily_liquidity", "failed", str(exc))
            raise
        normalized = self._normalize_daily(df)
        self.record_health("fund_daily_liquidity", "ok", rows=len(normalized))
        return self._write_cache(cache_name, normalized)

    def _fund_daily(self, ts_code: str, as_of_key: str) -> pd.DataFrame:
        cache_name = self._cache_name("fund_daily", ts_code, as_of_key)
        _name, list_date = self._fund_metadata(ts_code)
        start = self._history_start_key(as_of_key, list_date)
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
            complete = self._daily_covers_window(fallback, start, as_of_key)
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
        try:
            df = self._call_fund_daily(
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
        complete = self._daily_covers_window(df, start, as_of_key)
        self.record_health(
            "fund_daily",
            "ok_complete" if complete else "incomplete_history",
            f"{ts_code} expected={_iso(start)}..{_iso(as_of_key)}",
            rows=len(df),
        )
        self._daily_cache[cache_name] = self._write_cache(cache_name, df)
        return self._daily_cache[cache_name]

    @staticmethod
    def _history_start_key(as_of_key: str, list_date: str | None) -> str:
        end = datetime.strptime(as_of_key, "%Y%m%d").date()
        try:
            start_day = end.replace(year=end.year - INSTRUMENT_HISTORY_YEARS)
        except ValueError:
            start_day = end.replace(year=end.year - INSTRUMENT_HISTORY_YEARS, day=28)
        start = start_day.strftime("%Y%m%d")
        listed = _yyyymmdd(list_date) if list_date else ""
        return max(start, listed) if len(listed) == 8 and listed.isdigit() else start

    @staticmethod
    def _daily_covers_window(df: pd.DataFrame, start_key: str, as_of_key: str) -> bool:
        if df.empty or "trade_date" not in df.columns:
            return False
        dates = df["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        latest = dates.max()
        try:
            latest_day = datetime.strptime(latest, "%Y%m%d").date()
            as_of_day = datetime.strptime(as_of_key, "%Y%m%d").date()
        except ValueError:
            return False
        if latest_day > as_of_day or (as_of_day - latest_day).days > MAX_PRICE_AGE_DAYS:
            return False
        tolerance = (
            datetime.strptime(start_key, "%Y%m%d").date()
            + timedelta(days=HISTORY_START_TOLERANCE_DAYS)
        ).strftime("%Y%m%d")
        return bool(dates.min() <= tolerance)

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

    def _fund_share(self, ts_code: str, as_of_key: str) -> pd.DataFrame:
        cache_name = self._cache_name("fund_share", ts_code, as_of_key)
        if cache_name in self._share_cache:
            return self._share_cache[cache_name]
        cached = self._read_cache(cache_name)
        if cached is not None:
            self._share_cache[cache_name] = cached
            self.record_health("fund_share", "cache_hit", cache_name, rows=len(cached))
            return cached
        if self.offline:
            self.record_health("fund_share", "offline_unavailable", cache_name)
            return pd.DataFrame()
        end = datetime.strptime(as_of_key, "%Y%m%d").date()
        start = (end - timedelta(days=60)).strftime("%Y%m%d")
        try:
            df = self.pro.fund_share(
                ts_code=ts_code,
                start_date=start,
                end_date=as_of_key,
                fields="ts_code,trade_date,fd_share",
            )
        except TypeError:
            df = self.pro.fund_share(
                ts_code=ts_code,
                start_date=start,
                end_date=as_of_key,
            )
        out = df.copy() if df is not None else pd.DataFrame()
        if "fd_share" in out.columns:
            out["fd_share"] = pd.to_numeric(out["fd_share"], errors="coerce")
        for col in ("ts_code", "trade_date"):
            if col in out.columns:
                out[col] = out[col].astype(str)
        self.record_health("fund_share", "ok", rows=len(out))
        self._share_cache[cache_name] = self._write_cache(cache_name, out)
        return self._share_cache[cache_name]

    def _fund_basic(
        self,
        *,
        refresh: bool = False,
        as_of_key: str | None = None,
    ) -> pd.DataFrame:
        if self._basic_cache is not None and not refresh:
            return self._basic_cache
        # Version the cache because the original artifact omitted benchmark,
        # status, and fee fields required by the dynamic catalog.
        cache_name = "fund_basic_E_v2.csv"
        try:
            cached = (
                self._basic_cache
                if self._basic_cache is not None
                else self._read_cache(cache_name)
            )
        except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            self.record_health("fund_basic", "corrupt_cache", str(exc))
            cached = None
        cache_path = self._cache_path(cache_name)
        if cached is not None and not refresh:
            self._basic_cache = cached
            self._fund_basic_source = "cache"
            if cache_path is not None:
                self._fund_basic_data_as_of = datetime.fromtimestamp(
                    cache_path.stat().st_mtime
                ).date().isoformat()
            self.record_health("fund_basic", "cache_hit", cache_name, rows=len(cached))
            return cached
        if self.offline:
            self._basic_cache = cached if cached is not None else pd.DataFrame()
            self._fund_basic_source = "cache" if cached is not None else "unavailable"
            self.record_health(
                "fund_basic",
                "cache_hit" if cached is not None else "offline_unavailable",
                cache_name,
                rows=len(self._basic_cache),
            )
            return self._basic_cache
        try:
            df = self.pro.fund_basic(
                market="E",
                fields=(
                    "ts_code,name,management,custodian,fund_type,found_date,"
                    "list_date,delist_date,m_fee,c_fee,benchmark,status,invest_type,type"
                ),
            )
        except Exception as exc:
            self.record_health("fund_basic", "failed", str(exc))
            if cached is not None:
                self._basic_cache = cached
                self._fund_basic_source = "stale_cache_fallback"
                if cache_path is not None:
                    self._fund_basic_data_as_of = datetime.fromtimestamp(
                        cache_path.stat().st_mtime
                    ).date().isoformat()
                return self._basic_cache
            raise
        df = df.copy() if df is not None else pd.DataFrame()
        if refresh and (
            df.empty or not FUND_BASIC_REQUIRED_COLUMNS.issubset(df.columns)
        ):
            missing = sorted(FUND_BASIC_REQUIRED_COLUMNS - set(getattr(df, "columns", [])))
            self.record_health(
                "fund_basic",
                "invalid_refresh",
                f"empty={df.empty}; missing={','.join(missing)}",
            )
            if cached is not None:
                self._basic_cache = cached
                self._fund_basic_source = "stale_cache_fallback"
                if cache_path is not None:
                    self._fund_basic_data_as_of = datetime.fromtimestamp(
                        cache_path.stat().st_mtime
                    ).date().isoformat()
                return self._basic_cache
            raise ValueError("fund_basic_refresh_invalid")
        self.record_health("fund_basic", "ok", rows=len(df))
        self._basic_cache = self._write_cache(cache_name, df)
        self._fund_basic_source = "api_refresh"
        self._fund_basic_data_as_of = _iso(as_of_key or self.as_of)
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

    def _latest_fund_size(
        self,
        ts_code: str,
        trade_date: str | None,
        nav: float | None,
    ) -> tuple[float | None, str | None]:
        if not trade_date or nav is None or nav <= 0:
            return None, None
        trade_key = _yyyymmdd(trade_date)
        shares = self._fund_share(ts_code, trade_key)
        if shares.empty or not {"trade_date", "fd_share"}.issubset(shares.columns):
            return None, None
        eligible = shares[shares["trade_date"].astype(str) <= trade_key].copy()
        if eligible.empty:
            return None, None
        eligible = eligible.sort_values("trade_date")
        row = eligible.iloc[-1]
        share_in_ten_thousands = _safe_float(row.get("fd_share"))
        if share_in_ten_thousands is None or share_in_ten_thousands <= 0:
            return None, _iso(row.get("trade_date"))
        return share_in_ten_thousands * 10_000.0 * nav, _iso(row.get("trade_date"))

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
            write_dataframe_csv_atomic(out, path, index=False, encoding="utf-8")
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
