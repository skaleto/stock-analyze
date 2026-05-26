"""One-time batch fetch of historical market data from Tushare Pro.

The output lives at ``data/shared/backtest_cache/`` (kept separate from the
forward-mode cache at ``data/shared/cache/`` so the two systems never collide).

The fetch is idempotent: progress is tracked in ``_meta.json``. A rerun
fetches only what's missing. Use ``--force`` (or ``force=True``) to bypass
the progress check and refetch everything in the requested window.

Seven Tushare endpoints are exercised:

* ``pro.trade_cal`` — once per call, used to enumerate trading days
* ``pro.stock_basic`` — once, lists every A-share with list/delist dates
* ``pro.daily`` — once per trading day in [start, end]
* ``pro.daily_basic`` — once per trading day in [start, end]
* ``pro.fina_indicator`` — once per stock in stock_basic
* ``pro.adj_factor`` — once per stock in stock_basic
* ``pro.index_weight`` — once per (index, month) in [start, end] for hs300+zz500

Tests in ``tests/test_backtest_data_prep.py`` mock the client; no network is
required for testing.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd


TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
INDEX_CODES = [("000300.SH", "000300"), ("000905.SH", "000905")]
# Rate limit: tushare 2000-point tier allows 500 calls/min; we leave headroom.
_RATE_SLEEP_S = 0.15


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
    "adj_factor_codes_done": [],
    "index_weight_months_done": [],
    "stock_basic_done": False,
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
    (cache_root / "_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False)
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


def _throttle() -> None:
    time.sleep(_RATE_SLEEP_S)


# ---------------------------------------------------------------------------
# Per-endpoint fetchers
# ---------------------------------------------------------------------------

def _fetch_trade_cal(pro: Any, start: date, end: date,
                      cache_root: Path) -> List[str]:
    """Return list of YYYYMMDD trading-day strings, writing trade_cal.csv."""
    df = pro.trade_cal(
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
    )
    if df is None or df.empty:
        return []
    cache_root.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_root / "trade_cal.csv", index=False)
    open_days = df[df["is_open"] == 1]
    return open_days["cal_date"].astype(str).tolist()


def _fetch_daily(pro: Any, raw_date: str, cache_root: Path) -> None:
    df = pro.daily(trade_date=raw_date)
    out = cache_root / "daily" / f"{_iso_date(raw_date)}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)


def _fetch_daily_basic(pro: Any, raw_date: str, cache_root: Path) -> None:
    df = pro.daily_basic(trade_date=raw_date)
    out = cache_root / "daily_basic" / f"{_iso_date(raw_date)}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)


def _fetch_stock_basic(pro: Any, cache_root: Path) -> pd.DataFrame:
    df = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,list_date,delist_date",
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_root / "stock_basic.csv", index=False)
    return df


def _fetch_fina(pro: Any, ts_code: str, start: date, end: date,
                 cache_root: Path) -> None:
    df = pro.fina_indicator(
        ts_code=ts_code,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
        fields=(
            "ts_code,ann_date,end_date,roe,grossprofit_margin,"
            "debt_to_assets,netprofit_yoy"
        ),
    )
    out = cache_root / "fina_indicator" / f"{ts_code}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)


def _fetch_adj(pro: Any, ts_code: str, start: date, end: date,
                cache_root: Path) -> None:
    df = pro.adj_factor(
        ts_code=ts_code,
        start_date=_yyyymmdd(start),
        end_date=_yyyymmdd(end),
    )
    out = cache_root / "adj_factor" / f"{ts_code}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)


def _fetch_index_weight(pro: Any, idx_code: str, fname: str, month_start: date,
                          cache_root: Path) -> None:
    df = pro.index_weight(
        index_code=idx_code,
        trade_date=_yyyymmdd(month_start),
    )
    out = cache_root / "index_weight" / f"{fname}_{month_start.strftime('%Y-%m')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def prepare_backtest_data(
    start: date,
    end: date,
    cache_root: Path,
    force: bool = False,
) -> None:
    """Fetch historical market data from Tushare into ``cache_root``.

    Idempotent: dates already in ``_meta.json`` are skipped unless ``force``.
    Resumable: progress is saved after each endpoint completes.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    pro = _make_pro_client()
    meta = _load_meta(cache_root)

    # 1. Trade calendar (also gives us the list of trading days in [start, end])
    trade_dates = _fetch_trade_cal(pro, start, end, cache_root)
    meta["trade_cal_done"] = True
    _save_meta(cache_root, meta)

    # 2. Stock basic (once)
    if force or not meta.get("stock_basic_done", False):
        _fetch_stock_basic(pro, cache_root)
        meta["stock_basic_done"] = True
        _save_meta(cache_root, meta)

    stock_basic_path = cache_root / "stock_basic.csv"
    if not stock_basic_path.exists():
        # Force the fetch even if meta says done but file is gone
        _fetch_stock_basic(pro, cache_root)
    try:
        sb_df = pd.read_csv(stock_basic_path)
    except pd.errors.EmptyDataError:
        sb_df = pd.DataFrame(columns=["ts_code"])
    all_codes: List[str] = (
        sb_df["ts_code"].astype(str).tolist()
        if not sb_df.empty and "ts_code" in sb_df.columns
        else []
    )

    # 3. daily + daily_basic per trading day
    daily_done = set(meta.get("daily_dates_done", []))
    db_done = set(meta.get("daily_basic_dates_done", []))
    for raw_d in trade_dates:
        d_iso = _iso_date(raw_d)
        if force or d_iso not in daily_done:
            _fetch_daily(pro, raw_d, cache_root)
            daily_done.add(d_iso)
            _throttle()
        if force or d_iso not in db_done:
            _fetch_daily_basic(pro, raw_d, cache_root)
            db_done.add(d_iso)
            _throttle()
        # Save meta every 20 dates
        if len(daily_done) % 20 == 0:
            meta["daily_dates_done"] = sorted(daily_done)
            meta["daily_basic_dates_done"] = sorted(db_done)
            _save_meta(cache_root, meta)

    meta["daily_dates_done"] = sorted(daily_done)
    meta["daily_basic_dates_done"] = sorted(db_done)
    _save_meta(cache_root, meta)

    # 4. fina_indicator per code
    fina_done = set(meta.get("fina_codes_done", []))
    for code in all_codes:
        if force or code not in fina_done:
            _fetch_fina(pro, code, start, end, cache_root)
            fina_done.add(code)
            _throttle()
            if len(fina_done) % 50 == 0:
                meta["fina_codes_done"] = sorted(fina_done)
                _save_meta(cache_root, meta)
    meta["fina_codes_done"] = sorted(fina_done)
    _save_meta(cache_root, meta)

    # 5. adj_factor per code
    adj_done = set(meta.get("adj_factor_codes_done", []))
    for code in all_codes:
        if force or code not in adj_done:
            _fetch_adj(pro, code, start, end, cache_root)
            adj_done.add(code)
            _throttle()
            if len(adj_done) % 50 == 0:
                meta["adj_factor_codes_done"] = sorted(adj_done)
                _save_meta(cache_root, meta)
    meta["adj_factor_codes_done"] = sorted(adj_done)
    _save_meta(cache_root, meta)

    # 6. index_weight monthly snapshots for hs300 + zz500
    iw_done = set(meta.get("index_weight_months_done", []))
    for month_start in _month_starts(start, end):
        ym = month_start.strftime("%Y-%m")
        if force or ym not in iw_done:
            for idx_code, fname in INDEX_CODES:
                _fetch_index_weight(pro, idx_code, fname, month_start, cache_root)
                _throttle()
            iw_done.add(ym)
    meta["index_weight_months_done"] = sorted(iw_done)
    _save_meta(cache_root, meta)
