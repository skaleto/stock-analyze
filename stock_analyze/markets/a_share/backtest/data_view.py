"""Point-in-time data access layer for backtest.

All data reads during a backtest must go through ``PointInTimeView``.
Contract: given ``as_of=t``, return only data that was knowable at time t.

Visibility rules:

* ``daily`` / ``daily_basic``: trade_date <= t (we read the CSV for exact date).
* ``fina_indicator``:          ann_date <= t  (announcement-date filter; financial
                               figures with later ann_date are future leakage).
* ``index_weight``:            use the most recent monthly snapshot with
                               YYYY-MM <= t's YYYY-MM.
* ``stock_basic``:             list_date <= t and (delist_date is empty or
                               delist_date > t).

This file deliberately has no other responsibility; it never decides
investment logic or transforms data. It exists so that downstream code
(engine, signals, factor pipeline) cannot accidentally peek at future data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd


_INDEX_FILE_PREFIX = {"hs300": "000300", "zz500": "000905"}


@dataclass
class PointInTimeView:
    """Read-only view of ``backtest_cache/`` constrained to ``as_of``."""

    as_of: date
    cache_root: Path

    # ------------------------------------------------------------------
    # Daily-frequency endpoints
    # ------------------------------------------------------------------

    def daily(self, as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of if as_of is not None else self.as_of
        path = self.cache_root / "daily" / f"{d.isoformat()}.csv"
        if not path.exists():
            return pd.DataFrame()
        # ts_code like '000001.SZ' / trade_date YYYYMMDD must stay str —
        # without these pandas may strip leading zeros or coerce dates to int.
        return pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})

    def daily_basic(self, as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of if as_of is not None else self.as_of
        path = self.cache_root / "daily_basic" / f"{d.isoformat()}.csv"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})

    # ------------------------------------------------------------------
    # Financial indicators (ann_date-filtered)
    # ------------------------------------------------------------------

    def fina_for_code(self, ts_code: str,
                       as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of if as_of is not None else self.as_of
        path = self.cache_root / "fina_indicator" / f"{ts_code}.csv"
        if not path.exists():
            return pd.DataFrame()
        # ann_date / end_date are stored as YYYYMMDD; keep them as str so the
        # explicit pd.to_datetime conversion below has clean input.
        df = pd.read_csv(
            path,
            dtype={"ts_code": str, "ann_date": str, "end_date": str},
        )
        if df.empty or "ann_date" not in df.columns:
            return df
        # ann_date arrives as int (YYYYMMDD) or string; coerce defensively
        ann_parsed = pd.to_datetime(df["ann_date"].astype(str),
                                      format="%Y%m%d", errors="coerce").dt.date
        visible = ann_parsed.apply(lambda x: x is not None and x <= d)
        return df[visible].reset_index(drop=True)

    # ------------------------------------------------------------------
    # Universe at point-in-time
    # ------------------------------------------------------------------

    def universe(self, as_of: Optional[date] = None,
                  indices: Optional[List[str]] = None) -> List[str]:
        d = as_of if as_of is not None else self.as_of
        idx_list = indices if indices is not None else ["hs300", "zz500"]

        all_codes: set[str] = set()
        for idx in idx_list:
            prefix = _INDEX_FILE_PREFIX.get(idx)
            if prefix is None:
                continue
            all_codes |= self._codes_from_index_weight(prefix, d)

        return sorted(self._filter_listed(all_codes, d))

    def _codes_from_index_weight(self, file_prefix: str, d: date) -> set[str]:
        iw_dir = self.cache_root / "index_weight"
        if not iw_dir.exists():
            return set()
        target_ym = d.strftime("%Y-%m")
        # Find most recent snapshot with YM <= target_ym
        candidates = sorted(
            p for p in iw_dir.glob(f"{file_prefix}_*.csv")
            if p.stem.split("_", 1)[1] <= target_ym
        )
        if not candidates:
            return set()
        # con_code / index_code are textual stock tickers; trade_date YYYYMMDD.
        df = pd.read_csv(
            candidates[-1],
            dtype={"con_code": str, "index_code": str, "trade_date": str},
        )
        if df.empty or "con_code" not in df.columns:
            return set()
        return set(df["con_code"].astype(str))

    # ------------------------------------------------------------------
    # Broadcast factors (market-level scalars, e.g. LLM sentiment)
    # ------------------------------------------------------------------

    def broadcast(self, factor_name: str, as_of: Optional[date] = None) -> float:
        """Return a broadcast factor's scalar at ``as_of`` (point-in-time).

        Broadcast factors (``<agent>_market_sentiment_1w``) are LLM-curated
        weekly sentiment that only exists from 2026-05 onward — after the
        training (2021-2024) and validation (2025 → 2026-04) windows. There
        is no historical sentiment to read for backtest dates, so this
        returns ``0.0`` (neutral) and any broadcast factor contributes
        nothing to historical scores. The gate therefore checks the
        overlay's factor *structure*, not sentiment-conditioned alpha (per
        OpenSpec change bridge-factor-pipeline-into-backtest design).
        """
        return 0.0

    def _filter_listed(self, codes: set[str], d: date) -> set[str]:
        sb_path = self.cache_root / "stock_basic.csv"
        if not sb_path.exists():
            return codes
        sb = pd.read_csv(sb_path, dtype={"list_date": str, "delist_date": str})
        if sb.empty:
            return codes
        sb = sb[sb["ts_code"].isin(codes)].copy()
        # list_date <= d
        list_parsed = pd.to_datetime(sb["list_date"], format="%Y%m%d",
                                       errors="coerce").dt.date
        sb = sb[list_parsed.apply(lambda x: x is not None and x <= d)]
        # delist_date empty/NaN OR > d
        if "delist_date" in sb.columns:
            def keep(val) -> bool:
                if pd.isna(val) or val in ("", "nan", "None"):
                    return True
                try:
                    return pd.to_datetime(val, format="%Y%m%d").date() > d
                except (ValueError, TypeError):
                    return True
            sb = sb[sb["delist_date"].apply(keep)]
        return set(sb["ts_code"].astype(str).tolist())
