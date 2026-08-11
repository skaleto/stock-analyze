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

from dataclasses import dataclass, field
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
    _benchmark_cache: dict[str, pd.DataFrame] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _daily_close_panel: pd.DataFrame | None = field(default=None, init=False, repr=False)

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

    def return_history(
        self,
        codes: List[str],
        *,
        as_of: Optional[date] = None,
        days: int = 90,
    ) -> pd.DataFrame:
        """Return point-in-time close returns without re-reading the panel."""

        d = as_of if as_of is not None else self.as_of
        if self._daily_close_panel is None:
            rows: list[pd.DataFrame] = []
            for path in sorted((self.cache_root / "daily").glob("*.csv")):
                try:
                    frame = pd.read_csv(path, usecols=["ts_code", "close"], dtype={"ts_code": str})
                except (OSError, ValueError, pd.errors.EmptyDataError):
                    continue
                frame["trade_date"] = path.stem
                rows.append(frame)
            if rows:
                combined = pd.concat(rows, ignore_index=True)
                combined["close"] = pd.to_numeric(combined["close"], errors="coerce")
                self._daily_close_panel = combined.pivot_table(
                    index="trade_date",
                    columns="ts_code",
                    values="close",
                    aggfunc="last",
                ).sort_index()
            else:
                self._daily_close_panel = pd.DataFrame()
        panel = self._daily_close_panel
        if panel.empty:
            return pd.DataFrame()
        available = [str(code) for code in codes if str(code) in panel.columns]
        if len(available) != len(codes):
            return pd.DataFrame()
        visible = panel.loc[panel.index <= d.isoformat(), available].tail(max(int(days), 1) + 1)
        return visible.pct_change().tail(max(int(days), 1))

    def benchmark_close(
        self,
        code: str,
        as_of: Optional[date] = None,
    ) -> tuple[float | None, str | None]:
        """Return the latest index close visible on or before ``as_of``."""

        d = as_of if as_of is not None else self.as_of
        short_code = str(code).split(".", 1)[0].zfill(6)
        path = self.cache_root / "benchmark_daily" / f"{short_code}.csv"
        if not path.exists():
            return None, None
        if short_code not in self._benchmark_cache:
            try:
                frame = pd.read_csv(
                    path,
                    dtype={"ts_code": str, "trade_date": str},
                )
            except (pd.errors.EmptyDataError, OSError):
                frame = pd.DataFrame()
            self._benchmark_cache[short_code] = frame
        frame = self._benchmark_cache[short_code]
        if frame.empty or not {"trade_date", "close"}.issubset(frame.columns):
            return None, None
        trade_dates = pd.to_datetime(
            frame["trade_date"].astype(str).str.replace("-", "", regex=False),
            format="%Y%m%d",
            errors="coerce",
        )
        closes = pd.to_numeric(frame["close"], errors="coerce")
        visible = frame.assign(_date=trade_dates, _close=closes)
        visible = visible[
            visible["_date"].notna()
            & visible["_close"].notna()
            & visible["_date"].dt.date.le(d)
        ].sort_values("_date")
        if visible.empty:
            return None, None
        latest = visible.iloc[-1]
        return float(latest["_close"]), latest["_date"].date().isoformat()

    def benchmark_return_history(
        self,
        code: str,
        *,
        as_of: Optional[date] = None,
        days: int = 60,
    ) -> pd.Series:
        d = as_of if as_of is not None else self.as_of
        short_code = str(code).split(".", 1)[0].zfill(6)
        path = self.cache_root / "benchmark_daily" / f"{short_code}.csv"
        if not path.exists():
            return pd.Series(dtype=float)
        if short_code not in self._benchmark_cache:
            try:
                self._benchmark_cache[short_code] = pd.read_csv(
                    path,
                    dtype={"ts_code": str, "trade_date": str},
                )
            except (pd.errors.EmptyDataError, OSError):
                self._benchmark_cache[short_code] = pd.DataFrame()
        frame = self._benchmark_cache[short_code]
        if frame.empty or not {"trade_date", "close"}.issubset(frame.columns):
            return pd.Series(dtype=float)
        dates = pd.to_datetime(
            frame["trade_date"].astype(str).str.replace("-", "", regex=False),
            format="%Y%m%d",
            errors="coerce",
        )
        close = pd.to_numeric(frame["close"], errors="coerce")
        visible = pd.DataFrame({"date": dates, "close": close}).dropna()
        visible = visible.loc[visible["date"].dt.date.le(d)].sort_values("date").tail(max(int(days), 1) + 1)
        return visible.set_index("date")["close"].pct_change().dropna()

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
        # An unparseable ann_date coerces to NaT, and `NaT is not None` is
        # True — so the old `x is not None` guard fell through to `NaT <= d`
        # and raised "Cannot compare NaT with datetime.date". pd.notna(x)
        # correctly rejects both NaT and None before the comparison.
        visible = ann_parsed.apply(lambda x: pd.notna(x) and x <= d)
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
        # Empty input → empty output. Without this guard the downstream
        # boolean-index chain on an empty frame can drop the ts_code column
        # and raise KeyError (e.g. when an index has no weight snapshot yet).
        if not codes:
            return set()
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
        # pd.notna rejects NaT (unparseable list_date) and None; a bare
        # `x is not None` would let NaT through and crash on `NaT <= d`.
        sb = sb[list_parsed.apply(lambda x: pd.notna(x) and x <= d)]
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
