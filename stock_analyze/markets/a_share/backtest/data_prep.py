"""One-time batch fetch of historical market data from Tushare Pro.

The output lives at ``data/shared/backtest_cache/`` (kept separate from the
forward-mode cache at ``data/shared/cache/`` so the two systems never collide).

The fetch is idempotent: progress is tracked in ``_meta.json``. A rerun
fetches only what's missing. Use ``--force`` (or ``force=True``) to bypass
the progress check and refetch everything in the requested window.

The default preparation path exercises the market, status, and financial
statement endpoints required by the point-in-time research snapshot:

* ``pro.trade_cal`` — once per call, used to enumerate trading days
* ``pro.stock_basic`` — once per listing status, including listed, delisted,
  and paused A-shares with list/delist dates
* ``pro.daily`` — once per trading day in [start, end]
* ``pro.daily_basic`` — once per trading day in [start, end]
* ``pro.fina_indicator`` — once per stock in stock_basic
* ``pro.income`` / ``pro.balancesheet`` / ``pro.cashflow`` — once per stock
* ``pro.adj_factor`` — once per stock in stock_basic
* ``pro.index_weight`` — once per (index, month) in [start, end] for hs300+zz500
* ``pro.index_daily`` — benchmark closes for hs300+zz500 over [start, end]

Tests in ``tests/test_backtest_data_prep.py`` mock the client; no network is
required for testing.
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from ....utils import write_dataframe_csv_atomic, write_text_atomic


TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
INDEX_CODES = [("000300.SH", "000300"), ("000905.SH", "000905")]
STOCK_BASIC_STATUSES = ("L", "D", "P")
DEFAULT_PHASES = frozenset({
    "calendar",
    "universe",
    "daily",
    "fundamentals",
    "adjustments",
    "benchmark",
})
VALID_PHASES = DEFAULT_PHASES | {"status", "statements"}
VALID_CODE_SCOPES = {"all", "historical-index-union"}
VALID_STATUS_PROVIDERS = {"auto", "tushare", "baostock"}
# Rate limit: tushare 2000-point tier limits are per-endpoint:
#   - daily / daily_basic : 500/min  → 0.12s OK
#   - fina_indicator      : 200/min  → 0.30s required
#   - adj_factor          : 200/min  → 0.30s required
#   - index_weight        : 200/min  → 0.30s required
# Pick 0.35s globally (171/min) to leave buffer for sliding-window throttle
# bursts. Daily endpoints pay a ~20% throughput tax we don't really care
# about (one-time 5y backfill = ~20 min instead of ~15). The audit fix
# 2026-05-27: prior 0.15s tripped fina_indicator 200/min after 200 codes.
_RATE_SLEEP_S = 0.35
_ADJ_FACTOR_MAX_WINDOW_DAYS = 5 * 365
_FINANCIAL_STATEMENT_FIELDS = {
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
        "revenue,operate_profit,n_income,total_cogs,rd_exp"
    ),
    "balancesheet": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,total_assets"
    ),
    "cashflow": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
        "n_cashflow_act,free_cashflow"
    ),
}

_A_SHARE_STOCK_CODE = re.compile(
    r"(?:(?:600|601|603|605|688|689)[0-9]{3}\.SH|"
    r"(?:000|001|002|003|300|301)[0-9]{3}\.SZ|"
    r"(?:43|83|87)[0-9]{4}\.BJ|920[0-9]{3}\.BJ)"
)


# ---------------------------------------------------------------------------
# Tushare client construction (module-level so tests can patch it)
# ---------------------------------------------------------------------------

def _make_pro_client() -> Any:
    """Construct a tushare pro_api client. Tests patch this function."""
    token = os.environ.get(TUSHARE_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"{TUSHARE_TOKEN_ENV} env var not set; see docs/tushare-token-setup.md"
        )
    import tushare as ts  # type: ignore
    return ts.pro_api(token)


# ---------------------------------------------------------------------------
# Meta progress file
# ---------------------------------------------------------------------------

_DEFAULT_META = {
    "daily_dates_done": [],
    "daily_basic_dates_done": [],
    "fina_codes_done": [],
    "fina_code_ranges_done": [],
    "income_codes_done": [],
    "income_code_ranges_done": [],
    "balancesheet_codes_done": [],
    "balancesheet_code_ranges_done": [],
    "cashflow_codes_done": [],
    "cashflow_code_ranges_done": [],
    "adj_factor_codes_done": [],
    "adj_factor_code_ranges_done": [],
    "index_weight_months_done": [],
    "benchmark_ranges_done": [],
    "namechange_codes_done": [],
    "stock_st_dates_done": [],
    "suspend_dates_done": [],
    "baostock_status_code_ranges_done": [],
    "stock_st_available": None,
    "stock_basic_done": False,
    "stock_basic_statuses_done": [],
    "trade_cal_done": False,
}


def _load_meta(cache_root: Path) -> dict:
    meta_path = cache_root / "_meta.json"
    if not meta_path.exists():
        return dict(_DEFAULT_META)
    data = json.loads(meta_path.read_text())
    # Defensive: fill any missing keys
    merged = dict(_DEFAULT_META)
    merged.update(data)
    return merged


def _save_meta(cache_root: Path, meta: dict) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        cache_root / "_meta.json",
        json.dumps(meta, indent=2, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_date(yyyymmdd: str) -> str:
    """Convert '20210104' -> '2021-01-04'."""
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _optional_yyyymmdd_date(value: Any) -> date | None:
    raw = str(value or "").strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _code_range_key(code: str, start: date, end: date) -> str:
    return f"{code}:{start.isoformat()}:{end.isoformat()}"


def _is_a_share_stock_code(value: Any) -> bool:
    return _A_SHARE_STOCK_CODE.fullmatch(str(value)) is not None


def _normalize_phases(phases: Optional[set[str]]) -> set[str]:
    selected = set(DEFAULT_PHASES if phases is None else phases)
    unknown = selected - VALID_PHASES
    if unknown:
        raise ValueError(f"unknown backtest-data phases: {sorted(unknown)}")
    return selected


def _slice_codes(
    codes: List[str],
    *,
    offset: int,
    limit: Optional[int],
) -> List[str]:
    if offset < 0:
        raise ValueError("code_offset must be >= 0")
    if limit is not None and limit <= 0:
        raise ValueError("code_limit must be > 0")
    ordered = sorted(set(codes))
    end = None if limit is None else offset + limit
    return ordered[offset:end]


def _month_starts(start: date, end: date) -> List[date]:
    """Yield first-of-month dates within [start, end]."""
    out: List[date] = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _historical_index_union(cache_root: Path, start: date, end: date) -> List[str]:
    months = {month.strftime("%Y-%m") for month in _month_starts(start, end)}
    codes: set[str] = set()
    for _, fname in INDEX_CODES:
        for month in months:
            path = cache_root / "index_weight" / f"{fname}_{month}.csv"
            if not _valid_csv(path, {"index_code", "con_code", "trade_date"}):
                continue
            frame = pd.read_csv(path, usecols=["con_code"], dtype={"con_code": str})
            codes.update(frame["con_code"].dropna().astype(str))
    return sorted(
        code
        for code in codes
        if _is_a_share_stock_code(code)
    )


def _throttle() -> None:
    time.sleep(_RATE_SLEEP_S)


def _valid_frame(
    frame: pd.DataFrame | None,
    required: set[str],
    *,
    allow_empty: bool = False,
) -> bool:
    return bool(
        frame is not None
        and required.issubset(frame.columns)
        and (allow_empty or not frame.empty)
    )


def _valid_csv(
    path: Path,
    required: set[str],
    *,
    allow_empty: bool = False,
) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        frame = pd.read_csv(path, nrows=1)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return False
    return _valid_frame(frame, required, allow_empty=allow_empty)


def _valid_daily_csv(path: Path) -> bool:
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "amount"}
    if not _valid_csv(path, required):
        return False
    try:
        columns = set(pd.read_csv(path, nrows=0).columns)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return False
    return bool({"vol", "volume"} & columns)


def _csv_has_date_in_range(
    path: Path,
    *,
    date_column: str,
    start: date,
    end: date,
    required: set[str],
) -> bool:
    if not _valid_csv(path, required):
        return False
    try:
        frame = pd.read_csv(path, usecols=[date_column], dtype={date_column: str})
    except (OSError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return False
    values = frame[date_column].astype("string").str.replace("-", "", regex=False).str[:8]
    return bool(values.between(_yyyymmdd(start), _yyyymmdd(end)).any())


def _merge_endpoint_csv(
    out: Path,
    fetched: pd.DataFrame | None,
    *,
    identity: list[str],
    text_columns: list[str],
    sort_columns: list[str],
) -> pd.DataFrame:
    """Merge one range fetch with an existing endpoint cache via atomic replace.

    Historical backfills are commonly requested in adjacent ranges. Replacing
    the endpoint CSV would silently discard the already cached range, so every
    range-shaped endpoint uses the same deduplicate-and-merge contract.
    """
    frames: list[pd.DataFrame] = []
    if out.exists():
        try:
            frames.append(pd.read_csv(out, dtype={column: str for column in text_columns}))
        except pd.errors.EmptyDataError:
            pass
    if fetched is not None and len(fetched.columns):
        frames.append(fetched.copy())
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    for column in text_columns:
        if column in merged.columns:
            merged[column] = (
                merged[column].astype("string").str.replace("-", "", regex=False)
            )
    keys = [column for column in identity if column in merged.columns]
    if keys:
        merged = merged.drop_duplicates(keys, keep="last")
    order = [column for column in sort_columns if column in merged.columns]
    if order:
        merged = merged.sort_values(order, kind="stable")
    write_dataframe_csv_atomic(merged, out, index=False)
    return merged.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Per-endpoint fetchers
# ---------------------------------------------------------------------------

def _fetch_trade_cal(pro: Any, start: date, end: date,
                      cache_root: Path) -> List[str]:
    """Return list of YYYYMMDD trading-day strings, writing trade_cal.csv."""
    fetched = pro.trade_cal(
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
    )
    out = cache_root / "trade_cal.csv"
    df = _merge_endpoint_csv(
        out,
        fetched,
        identity=["exchange", "cal_date"],
        text_columns=["exchange", "cal_date", "pretrade_date"],
        sort_columns=["cal_date", "exchange"],
    )
    if df.empty or "cal_date" not in df.columns:
        return []
    requested = df["cal_date"].between(_yyyymmdd(start), _yyyymmdd(end))
    is_open = pd.to_numeric(df.get("is_open"), errors="coerce").eq(1)
    open_days = df.loc[requested & is_open]
    return open_days["cal_date"].astype(str).tolist()


def _fetch_daily(pro: Any, raw_date: str, cache_root: Path) -> bool:
    df = pro.daily(trade_date=raw_date)
    out = cache_root / "daily" / f"{_iso_date(raw_date)}.csv"
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "amount"}
    if not _valid_frame(df, required) or not ({"vol", "volume"} & set(df.columns)):
        return False
    write_dataframe_csv_atomic(df, out, index=False)
    return _valid_csv(out, required)


def _fetch_daily_basic(pro: Any, raw_date: str, cache_root: Path) -> bool:
    df = pro.daily_basic(trade_date=raw_date)
    out = cache_root / "daily_basic" / f"{_iso_date(raw_date)}.csv"
    required = {"ts_code", "trade_date"}
    if not _valid_frame(df, required):
        return False
    write_dataframe_csv_atomic(df, out, index=False)
    return _valid_csv(out, required)


def _fetch_stock_basic(pro: Any, cache_root: Path) -> tuple[pd.DataFrame, set[str]]:
    out = cache_root / "stock_basic.csv"
    frames: list[pd.DataFrame] = []
    completed_statuses: set[str] = set()
    required = {"ts_code", "name", "list_date", "delist_date"}
    for status in STOCK_BASIC_STATUSES:
        fetched = pro.stock_basic(
            exchange="",
            list_status=status,
            fields="ts_code,symbol,name,area,industry,list_date,delist_date",
        )
        if not _valid_frame(fetched, required, allow_empty=True):
            continue
        completed_statuses.add(status)
        if fetched.empty:
            continue
        fetched = fetched.copy()
        fetched["list_status"] = status
        frames.append(fetched)
    columns = [
        "ts_code", "symbol", "name", "area", "industry", "list_date",
        "delist_date", "list_status",
    ]
    df = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames else pd.DataFrame(columns=columns)
    )
    if "ts_code" in df.columns:
        df = df.drop_duplicates("ts_code", keep="last").sort_values("ts_code")
    if (
        not set(STOCK_BASIC_STATUSES).issubset(completed_statuses)
        and _valid_csv(out, required | {"list_status"})
    ):
        existing = pd.read_csv(
            out,
            dtype={
                "ts_code": str,
                "symbol": str,
                "list_date": str,
                "delist_date": str,
                "name": str,
                "list_status": str,
            },
        )
        return existing, completed_statuses
    write_dataframe_csv_atomic(df, out, index=False)
    return df, completed_statuses


def _fetch_namechange(pro: Any, ts_code: str, cache_root: Path) -> bool:
    fetched = pro.namechange(ts_code=ts_code)
    required = {
        "ts_code", "name", "start_date", "end_date", "ann_date", "change_reason",
    }
    if not _valid_frame(fetched, required, allow_empty=True):
        return False
    out = cache_root / "namechange" / f"{ts_code}.csv"
    _merge_endpoint_csv(
        out,
        fetched,
        identity=["ts_code", "name", "start_date", "end_date", "ann_date"],
        text_columns=["ts_code", "start_date", "end_date", "ann_date"],
        sort_columns=["start_date", "ann_date", "ts_code"],
    )
    return _valid_csv(out, required, allow_empty=True)


def _fetch_suspend(pro: Any, raw_date: str, cache_root: Path) -> bool:
    fetched = pro.suspend_d(trade_date=raw_date)
    required = {"ts_code", "trade_date", "suspend_timing", "suspend_type"}
    if not _valid_frame(fetched, required, allow_empty=True):
        return False
    out = cache_root / "suspend_d" / f"{_iso_date(raw_date)}.csv"
    write_dataframe_csv_atomic(fetched, out, index=False)
    return _valid_csv(out, required, allow_empty=True)


def _fetch_stock_st(pro: Any, raw_date: str, cache_root: Path) -> bool:
    fetched = pro.stock_st(trade_date=raw_date)
    required = {"ts_code", "name", "trade_date", "type", "type_name"}
    if not _valid_frame(fetched, required, allow_empty=True):
        return False
    out = cache_root / "stock_st" / f"{_iso_date(raw_date)}.csv"
    write_dataframe_csv_atomic(fetched, out, index=False)
    return _valid_csv(out, required, allow_empty=True)


def _baostock_code(ts_code: str) -> str:
    symbol, suffix = ts_code.split(".", maxsplit=1)
    exchange = {"SH": "sh", "SZ": "sz"}.get(suffix.upper())
    if exchange is None:
        raise ValueError(f"unsupported Baostock security code: {ts_code}")
    return f"{exchange}.{symbol}"


def _make_baostock_client() -> Any:
    import baostock as bs  # type: ignore

    result = bs.login()
    if str(getattr(result, "error_code", "")) != "0":
        raise RuntimeError(
            f"baostock login failed: {getattr(result, 'error_msg', 'unknown error')}"
        )
    return bs


def _close_baostock_client(client: Any) -> None:
    client.logout()


def _fetch_baostock_status(
    client: Any,
    ts_code: str,
    start: date,
    end: date,
    cache_root: Path,
) -> bool:
    result = client.query_history_k_data_plus(
        _baostock_code(ts_code),
        "date,code,tradestatus,isST",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="3",
    )
    if str(getattr(result, "error_code", "")) != "0":
        return False
    fields = list(getattr(result, "fields", []))
    required_source = {"date", "code", "tradestatus", "isST"}
    if not required_source.issubset(fields):
        return False
    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    frame = pd.DataFrame(rows, columns=fields)
    frame = frame.rename(columns={"date": "trade_date", "isST": "is_st"})
    frame["ts_code"] = ts_code
    frame["st_source"] = "baostock_history_isST_v1"
    if "trade_date" in frame.columns:
        frame["trade_date"] = (
            frame["trade_date"].astype("string").str.replace("-", "", regex=False)
        )
    columns = [
        "ts_code", "trade_date", "tradestatus", "is_st", "st_source", "code",
    ]
    frame = frame.reindex(columns=columns)
    required = {"ts_code", "trade_date", "tradestatus", "is_st", "st_source"}
    if not _valid_frame(frame, required, allow_empty=True):
        return False
    out = cache_root / "baostock_status" / f"{ts_code}.csv"
    _merge_endpoint_csv(
        out,
        frame,
        identity=["ts_code", "trade_date"],
        text_columns=["ts_code", "trade_date", "tradestatus", "is_st", "code"],
        sort_columns=["trade_date", "ts_code"],
    )
    return _valid_csv(out, required, allow_empty=True)


def _fetch_fina(pro: Any, ts_code: str, start: date, end: date,
                 cache_root: Path) -> bool:
    df = pro.fina_indicator(
        ts_code=ts_code,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        fields=(
            "ts_code,ann_date,end_date,roe,grossprofit_margin,"
            "debt_to_assets,netprofit_yoy,roic,netprofit_margin,"
            "current_ratio,quick_ratio,assets_turn,q_sales_yoy,q_op_qoq,ocf_yoy"
        ),
    )
    out = cache_root / "fina_indicator" / f"{ts_code}.csv"
    required = {"ts_code", "ann_date", "end_date"}
    if not _valid_frame(df, required, allow_empty=True):
        return False
    _merge_endpoint_csv(
        out,
        df,
        identity=["ts_code", "ann_date", "end_date"],
        text_columns=["ts_code", "ann_date", "end_date"],
        sort_columns=["ann_date", "end_date", "ts_code"],
    )
    return _valid_csv(out, required, allow_empty=True)


def _fetch_financial_statement(
    pro: Any,
    endpoint: str,
    ts_code: str,
    start: date,
    end: date,
    cache_root: Path,
) -> bool:
    fields = _FINANCIAL_STATEMENT_FIELDS.get(endpoint)
    if fields is None:
        raise ValueError(f"unsupported financial statement endpoint: {endpoint}")
    frame = getattr(pro, endpoint)(
        ts_code=ts_code,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        fields=fields,
    )
    required = {"ts_code", "ann_date", "end_date"}
    if not _valid_frame(frame, required, allow_empty=True):
        return False
    out = cache_root / endpoint / f"{ts_code}.csv"
    _merge_endpoint_csv(
        out,
        frame,
        identity=[
            "ts_code", "ann_date", "f_ann_date", "end_date",
            "report_type", "update_flag",
        ],
        text_columns=[
            "ts_code", "ann_date", "f_ann_date", "end_date",
            "report_type", "update_flag",
        ],
        sort_columns=[
            "ann_date", "f_ann_date", "end_date", "report_type", "update_flag",
        ],
    )
    return _valid_csv(out, required, allow_empty=True)


def _fetch_adj(pro: Any, ts_code: str, start: date, end: date,
                cache_root: Path, *, active_start: date | None = None,
                active_end: date | None = None, force: bool = False) -> bool:
    required = {"ts_code", "trade_date", "adj_factor"}
    out = cache_root / "adj_factor" / f"{ts_code}.csv"
    progress_path = cache_root / "adj_factor" / f"{ts_code}.windows.json"
    completed_windows: set[str] = set()
    if not force and progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            if progress.get("ts_code") == ts_code:
                completed_windows = set(progress.get("completed_windows") or [])
        except (OSError, ValueError, TypeError):
            completed_windows = set()

    lifecycle_start = active_start or start
    lifecycle_end = active_end or end

    def persist_progress() -> None:
        write_text_atomic(
            progress_path,
            json.dumps(
                {
                    "schema_version": "adj-factor-window-progress-v1",
                    "ts_code": ts_code,
                    "completed_windows": sorted(completed_windows),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    window_start = start
    while window_start <= end:
        window_end = min(
            end,
            window_start + timedelta(days=_ADJ_FACTOR_MAX_WINDOW_DAYS),
        )
        window_key = f"{_yyyymmdd(window_start)}:{_yyyymmdd(window_end)}"
        overlaps_lifecycle = (
            max(window_start, lifecycle_start) <= min(window_end, lifecycle_end)
        )
        cached_window_is_valid = (
            not overlaps_lifecycle
            or _csv_has_date_in_range(
                out,
                date_column="trade_date",
                start=max(window_start, lifecycle_start),
                end=min(window_end, lifecycle_end),
                required=required,
            )
        )
        if window_key in completed_windows and cached_window_is_valid:
            window_start = window_end + timedelta(days=1)
            continue
        completed_windows.discard(window_key)

        frame = pro.adj_factor(
            ts_code=ts_code,
            start_date=_yyyymmdd(window_start),
            end_date=_yyyymmdd(window_end),
        )
        _throttle()
        if not _valid_frame(frame, required, allow_empty=True):
            return False
        if frame.empty:
            if overlaps_lifecycle:
                return False
        else:
            codes = frame["ts_code"].astype("string")
            dates = (
                frame["trade_date"].astype("string")
                .str.replace("-", "", regex=False)
            )
            factors = pd.to_numeric(frame["adj_factor"], errors="coerce")
            if (
                not codes.eq(ts_code).all()
                or not dates.str.fullmatch(r"\d{8}").fillna(False).all()
                or not dates.between(_yyyymmdd(window_start), _yyyymmdd(window_end)).all()
                or not factors.gt(0).all()
            ):
                return False
        _merge_endpoint_csv(
            out,
            frame,
            identity=["ts_code", "trade_date"],
            text_columns=["ts_code", "trade_date"],
            sort_columns=["trade_date", "ts_code"],
        )
        completed_windows.add(window_key)
        persist_progress()
        window_start = window_end + timedelta(days=1)

    requested_overlaps_lifecycle = max(start, lifecycle_start) <= min(end, lifecycle_end)
    return _valid_csv(out, required, allow_empty=not requested_overlaps_lifecycle)


def _fetch_index_weight(pro: Any, idx_code: str, fname: str, month_start: date,
                          cache_root: Path) -> bool:
    df = pro.index_weight(
        index_code=idx_code,
        start_date=_yyyymmdd(month_start - timedelta(days=95)),
        end_date=_yyyymmdd(month_start),
    )
    required = {"index_code", "con_code", "trade_date"}
    if not _valid_frame(df, required):
        return False
    if "trade_date" in df.columns:
        latest = df["trade_date"].astype("string").max()
        df = df.loc[df["trade_date"].astype("string").eq(latest)].copy()
    out = cache_root / "index_weight" / f"{fname}_{month_start.strftime('%Y-%m')}.csv"
    write_dataframe_csv_atomic(df, out, index=False)
    return _valid_csv(out, required)


def _fetch_index_daily(
    pro: Any,
    idx_code: str,
    fname: str,
    start: date,
    end: date,
    cache_root: Path,
) -> bool:
    fetched = pro.index_daily(
        ts_code=idx_code,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
    )
    required = {"ts_code", "trade_date", "close"}
    if not _valid_frame(fetched, required):
        return False
    out = cache_root / "benchmark_daily" / f"{fname}.csv"
    frames = [fetched]
    if out.exists():
        try:
            frames.insert(
                0,
                pd.read_csv(out, dtype={"ts_code": str, "trade_date": str}),
            )
        except pd.errors.EmptyDataError:
            pass
    merged = pd.concat(frames, ignore_index=True, sort=False)
    if "trade_date" not in merged.columns:
        return False
    else:
        merged["trade_date"] = merged["trade_date"].astype("string").str.replace(
            "-", "", regex=False
        ).str[:8]
        merged = merged.dropna(subset=["trade_date"])
        merged = merged.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
    write_dataframe_csv_atomic(merged, out, index=False)
    requested = merged["trade_date"].between(_yyyymmdd(start), _yyyymmdd(end))
    return bool(requested.any() and _valid_csv(out, required))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def prepare_backtest_data(
    start: date,
    end: date,
    cache_root: Path,
    force: bool = False,
    *,
    phases: Optional[set[str]] = None,
    code_scope: str = "all",
    code_offset: int = 0,
    code_limit: Optional[int] = None,
    status_provider: str = "auto",
) -> dict[str, object]:
    """Fetch historical market data from Tushare into ``cache_root``.

    Idempotent: dates already in ``_meta.json`` are skipped unless ``force``.
    Resumable: progress is saved after each endpoint completes.
    """
    if end < start:
        raise ValueError("end must be on or after start")
    selected_phases = _normalize_phases(phases)
    if code_scope not in VALID_CODE_SCOPES:
        raise ValueError(f"unknown code_scope: {code_scope}")
    if status_provider not in VALID_STATUS_PROVIDERS:
        raise ValueError(f"unknown status_provider: {status_provider}")

    cache_root.mkdir(parents=True, exist_ok=True)
    pro = _make_pro_client()
    meta = _load_meta(cache_root)

    trade_dates: List[str] = []
    if selected_phases & {"calendar", "daily", "status"}:
        trade_dates = _fetch_trade_cal(pro, start, end, cache_root)
        meta["trade_cal_done"] = _valid_csv(
            cache_root / "trade_cal.csv", {"cal_date", "is_open"}
        )
        _save_meta(cache_root, meta)

    code_phases = {"fundamentals", "statements", "adjustments", "status"}
    needs_stock_basic = bool(selected_phases & ({"universe"} | code_phases))
    stock_basic_path = cache_root / "stock_basic.csv"
    if needs_stock_basic:
        stock_basic_statuses_done = set(meta.get("stock_basic_statuses_done", []))
        stock_basic_complete = set(STOCK_BASIC_STATUSES).issubset(
            stock_basic_statuses_done
        )
        stock_basic_valid = _valid_csv(
            stock_basic_path,
            {"ts_code", "name", "list_date", "delist_date", "list_status"},
        )
        if (
            force
            or not meta.get("stock_basic_done", False)
            or not stock_basic_complete
            or not stock_basic_valid
        ):
            _, completed_statuses = _fetch_stock_basic(pro, cache_root)
            stock_basic_valid = _valid_csv(
                stock_basic_path,
                {"ts_code", "name", "list_date", "delist_date", "list_status"},
            )
            meta["stock_basic_statuses_done"] = sorted(completed_statuses)
            meta["stock_basic_done"] = bool(
                stock_basic_valid
                and set(STOCK_BASIC_STATUSES).issubset(completed_statuses)
            )
            _save_meta(cache_root, meta)

    if "universe" in selected_phases or code_scope == "historical-index-union":
        iw_done = set(meta.get("index_weight_months_done", []))
        for month_start in _month_starts(start, end):
            ym = month_start.strftime("%Y-%m")
            paths_valid = all(
                _valid_csv(
                    cache_root / "index_weight" / f"{fname}_{ym}.csv",
                    {"index_code", "con_code", "trade_date"},
                )
                for _, fname in INDEX_CODES
            )
            if force or ym not in iw_done or not paths_valid:
                month_complete = True
                for idx_code, fname in INDEX_CODES:
                    month_complete = (
                        _fetch_index_weight(pro, idx_code, fname, month_start, cache_root)
                        and month_complete
                    )
                    _throttle()
                if month_complete:
                    iw_done.add(ym)
                else:
                    iw_done.discard(ym)
                meta["index_weight_months_done"] = sorted(iw_done)
                _save_meta(cache_root, meta)
        meta["index_weight_months_done"] = sorted(iw_done)
        _save_meta(cache_root, meta)

    all_codes: List[str] = []
    code_lifecycles: dict[str, tuple[date | None, date | None]] = {}
    if needs_stock_basic and stock_basic_path.exists():
        try:
            sb_df = pd.read_csv(
                stock_basic_path,
                dtype={
                    "ts_code": str,
                    "symbol": str,
                    "list_date": str,
                    "delist_date": str,
                    "name": str,
                },
            )
        except pd.errors.EmptyDataError:
            sb_df = pd.DataFrame(columns=["ts_code"])
        if not sb_df.empty and "ts_code" in sb_df.columns:
            valid_codes = [
                code
                for code in sb_df["ts_code"].dropna().astype(str)
                if _is_a_share_stock_code(code)
            ]
            code_lifecycles = {
                str(row.ts_code): (
                    _optional_yyyymmdd_date(row.list_date),
                    _optional_yyyymmdd_date(row.delist_date),
                )
                for row in sb_df.itertuples(index=False)
                if _is_a_share_stock_code(row.ts_code)
            }
            all_codes = valid_codes
    if code_scope == "historical-index-union":
        all_codes = _historical_index_union(cache_root, start, end)
        if not all_codes:
            raise RuntimeError(
                "historical index union is empty; index-weight backfill is incomplete"
            )
    all_codes = sorted(set(all_codes))
    batch_codes = _slice_codes(all_codes, offset=code_offset, limit=code_limit)

    if "daily" in selected_phases:
        daily_done = set(meta.get("daily_dates_done", []))
        db_done = set(meta.get("daily_basic_dates_done", []))
        for index, raw_d in enumerate(trade_dates, start=1):
            d_iso = _iso_date(raw_d)
            daily_path = cache_root / "daily" / f"{d_iso}.csv"
            if force or d_iso not in daily_done or not _valid_daily_csv(daily_path):
                if _fetch_daily(pro, raw_d, cache_root):
                    daily_done.add(d_iso)
                else:
                    daily_done.discard(d_iso)
                _throttle()
            daily_basic_path = cache_root / "daily_basic" / f"{d_iso}.csv"
            if (
                force
                or d_iso not in db_done
                or not _valid_csv(daily_basic_path, {"ts_code", "trade_date"})
            ):
                if _fetch_daily_basic(pro, raw_d, cache_root):
                    db_done.add(d_iso)
                else:
                    db_done.discard(d_iso)
                _throttle()
            if index % 20 == 0:
                meta["daily_dates_done"] = sorted(daily_done)
                meta["daily_basic_dates_done"] = sorted(db_done)
                _save_meta(cache_root, meta)
        meta["daily_dates_done"] = sorted(daily_done)
        meta["daily_basic_dates_done"] = sorted(db_done)
        _save_meta(cache_root, meta)

    if "fundamentals" in selected_phases:
        fina_done = set(meta.get("fina_codes_done", []))
        fina_ranges_done = set(meta.get("fina_code_ranges_done", []))
        for index, code in enumerate(batch_codes, start=1):
            range_key = _code_range_key(code, start, end)
            fina_path = cache_root / "fina_indicator" / f"{code}.csv"
            if (
                force
                or range_key not in fina_ranges_done
                or not _valid_csv(
                    fina_path,
                    {"ts_code", "ann_date", "end_date"},
                    allow_empty=True,
                )
            ):
                if _fetch_fina(pro, code, start, end, cache_root):
                    fina_done.add(code)
                    fina_ranges_done.add(range_key)
                else:
                    fina_ranges_done.discard(range_key)
                _throttle()
            if index % 50 == 0:
                meta["fina_codes_done"] = sorted(fina_done)
                meta["fina_code_ranges_done"] = sorted(fina_ranges_done)
                _save_meta(cache_root, meta)
        meta["fina_codes_done"] = sorted(fina_done)
        meta["fina_code_ranges_done"] = sorted(fina_ranges_done)
        _save_meta(cache_root, meta)

    if selected_phases & {"fundamentals", "statements"}:
        statement_progress = {
            endpoint: {
                "done": set(meta.get(f"{endpoint}_codes_done", [])),
                "ranges": set(meta.get(f"{endpoint}_code_ranges_done", [])),
            }
            for endpoint in _FINANCIAL_STATEMENT_FIELDS
        }

        def persist_statement_progress() -> None:
            for endpoint, progress in statement_progress.items():
                meta[f"{endpoint}_codes_done"] = sorted(progress["done"])
                meta[f"{endpoint}_code_ranges_done"] = sorted(progress["ranges"])
            _save_meta(cache_root, meta)

        with ThreadPoolExecutor(
            max_workers=len(_FINANCIAL_STATEMENT_FIELDS),
            thread_name_prefix="tushare-statement",
        ) as executor:
            for index, code in enumerate(batch_codes, start=1):
                range_key = _code_range_key(code, start, end)
                pending: dict[str, Any] = {}
                for endpoint, progress in statement_progress.items():
                    statement_path = cache_root / endpoint / f"{code}.csv"
                    if (
                        force
                        or range_key not in progress["ranges"]
                        or not _valid_csv(
                            statement_path,
                            {"ts_code", "ann_date", "end_date"},
                            allow_empty=True,
                        )
                    ):
                        pending[endpoint] = executor.submit(
                            _fetch_financial_statement,
                            pro,
                            endpoint,
                            code,
                            start,
                            end,
                            cache_root,
                        )
                for endpoint, future in pending.items():
                    progress = statement_progress[endpoint]
                    if future.result():
                        progress["done"].add(code)
                        progress["ranges"].add(range_key)
                    else:
                        progress["ranges"].discard(range_key)
                if pending:
                    _throttle()
                if index % 50 == 0:
                    persist_statement_progress()
        persist_statement_progress()

    if "adjustments" in selected_phases:
        adj_done = set(meta.get("adj_factor_codes_done", []))
        adj_ranges_done = set(meta.get("adj_factor_code_ranges_done", []))
        for index, code in enumerate(batch_codes, start=1):
            range_key = _code_range_key(code, start, end)
            lifecycle = code_lifecycles.get(code, (None, None))
            # Legacy range markers cannot prove that every bounded window was
            # fetched. The window-aware fetcher validates progress before skip.
            if _fetch_adj(
                pro,
                code,
                start,
                end,
                cache_root,
                active_start=lifecycle[0],
                active_end=lifecycle[1],
                force=force,
            ):
                adj_done.add(code)
                adj_ranges_done.add(range_key)
            else:
                adj_ranges_done.discard(range_key)
            if index % 50 == 0:
                meta["adj_factor_codes_done"] = sorted(adj_done)
                meta["adj_factor_code_ranges_done"] = sorted(adj_ranges_done)
                _save_meta(cache_root, meta)
        meta["adj_factor_codes_done"] = sorted(adj_done)
        meta["adj_factor_code_ranges_done"] = sorted(adj_ranges_done)
        _save_meta(cache_root, meta)

    if "status" in selected_phases:
        suspend_done = set(meta.get("suspend_dates_done", []))
        for index, raw_d in enumerate(trade_dates, start=1):
            d_iso = _iso_date(raw_d)
            path = cache_root / "suspend_d" / f"{d_iso}.csv"
            if (
                force
                or d_iso not in suspend_done
                or not _valid_csv(
                    path,
                    {"ts_code", "trade_date", "suspend_timing", "suspend_type"},
                    allow_empty=True,
                )
            ):
                if _fetch_suspend(pro, raw_d, cache_root):
                    suspend_done.add(d_iso)
                else:
                    suspend_done.discard(d_iso)
                _throttle()
            if index % 20 == 0:
                meta["suspend_dates_done"] = sorted(suspend_done)
                _save_meta(cache_root, meta)
        meta["suspend_dates_done"] = sorted(suspend_done)

        stock_st_done = set(meta.get("stock_st_dates_done", []))
        stock_st_available = meta.get("stock_st_available")
        should_try_stock_st = status_provider == "tushare" or (
            status_provider == "auto" and stock_st_available is not False
        )
        if should_try_stock_st:
            for raw_d in trade_dates:
                d_iso = _iso_date(raw_d)
                path = cache_root / "stock_st" / f"{d_iso}.csv"
                if (
                    not force
                    and d_iso in stock_st_done
                    and _valid_csv(
                        path,
                        {"ts_code", "name", "trade_date", "type", "type_name"},
                        allow_empty=True,
                    )
                ):
                    continue
                try:
                    completed = _fetch_stock_st(pro, raw_d, cache_root)
                except Exception as exc:  # noqa: BLE001
                    meta["stock_st_available"] = False
                    meta["stock_st_probe_error"] = type(exc).__name__
                    stock_st_available = False
                    break
                if completed:
                    stock_st_done.add(d_iso)
                    meta["stock_st_available"] = True
                    stock_st_available = True
                else:
                    stock_st_done.discard(d_iso)
                _throttle()
        meta["stock_st_dates_done"] = sorted(stock_st_done)

        namechange_done = set(meta.get("namechange_codes_done", []))
        for index, code in enumerate(batch_codes, start=1):
            path = cache_root / "namechange" / f"{code}.csv"
            if (
                force
                or code not in namechange_done
                or not _valid_csv(
                    path,
                    {
                        "ts_code", "name", "start_date", "end_date",
                        "ann_date", "change_reason",
                    },
                    allow_empty=True,
                )
            ):
                if _fetch_namechange(pro, code, cache_root):
                    namechange_done.add(code)
                else:
                    namechange_done.discard(code)
                _throttle()
            if index % 50 == 0:
                meta["namechange_codes_done"] = sorted(namechange_done)
                _save_meta(cache_root, meta)
        meta["namechange_codes_done"] = sorted(namechange_done)

        use_baostock = status_provider == "baostock" or (
            status_provider == "auto" and stock_st_available is False
        )
        if use_baostock and batch_codes:
            baostock_ranges_done = set(
                meta.get("baostock_status_code_ranges_done", [])
            )
            client = _make_baostock_client()
            try:
                for index, code in enumerate(batch_codes, start=1):
                    range_key = _code_range_key(code, start, end)
                    path = cache_root / "baostock_status" / f"{code}.csv"
                    if (
                        force
                        or range_key not in baostock_ranges_done
                        or not _valid_csv(
                            path,
                            {
                                "ts_code", "trade_date", "tradestatus",
                                "is_st", "st_source",
                            },
                            allow_empty=True,
                        )
                    ):
                        if _fetch_baostock_status(
                            client, code, start, end, cache_root
                        ):
                            baostock_ranges_done.add(range_key)
                        else:
                            baostock_ranges_done.discard(range_key)
                    if index % 25 == 0:
                        meta["baostock_status_code_ranges_done"] = sorted(
                            baostock_ranges_done
                        )
                        _save_meta(cache_root, meta)
            finally:
                _close_baostock_client(client)
            meta["baostock_status_code_ranges_done"] = sorted(
                baostock_ranges_done
            )
        _save_meta(cache_root, meta)

    if "benchmark" in selected_phases:
        benchmark_done = set(meta.get("benchmark_ranges_done", []))
        for idx_code, fname in INDEX_CODES:
            key = f"{fname}:{start.isoformat()}:{end.isoformat()}"
            benchmark_path = cache_root / "benchmark_daily" / f"{fname}.csv"
            benchmark_valid = _csv_has_date_in_range(
                benchmark_path,
                date_column="trade_date",
                start=start,
                end=end,
                required={"ts_code", "trade_date", "close"},
            )
            if force or key not in benchmark_done or not benchmark_valid:
                if _fetch_index_daily(pro, idx_code, fname, start, end, cache_root):
                    benchmark_done.add(key)
                else:
                    benchmark_done.discard(key)
                _throttle()
        meta["benchmark_ranges_done"] = sorted(benchmark_done)
        _save_meta(cache_root, meta)

    requested_dates = {_iso_date(raw_date) for raw_date in trade_dates}
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "phases": sorted(selected_phases),
        "code_scope": code_scope,
        "scope_codes": len(all_codes),
        "batch_codes": len(batch_codes),
        "code_offset": code_offset,
        "code_limit": code_limit,
        "trade_dates": len(trade_dates),
        "daily_dates_complete": len(
            requested_dates & set(meta.get("daily_dates_done", []))
        ),
        "daily_basic_dates_complete": len(
            requested_dates & set(meta.get("daily_basic_dates_done", []))
        ),
        "suspend_dates_complete": len(
            requested_dates & set(meta.get("suspend_dates_done", []))
        ),
        "stock_st_dates_complete": len(
            requested_dates & set(meta.get("stock_st_dates_done", []))
        ),
        "stock_st_available": meta.get("stock_st_available"),
    }
