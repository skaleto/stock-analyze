"""Tests for backtest's point-in-time data view.

This is the chokepoint for all backtest data reads. The invariant: given
``as_of=t``, return only data knowable at time t. No future leakage.
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.backtest.data_view import PointInTimeView


class _CacheBuilder:
    """Helper to construct a minimal backtest_cache layout for tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "daily").mkdir(parents=True)
        (root / "daily_basic").mkdir(parents=True)
        (root / "fina_indicator").mkdir(parents=True)
        (root / "index_weight").mkdir(parents=True)

    def write_daily(self, iso_date: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "daily" / f"{iso_date}.csv", index=False)

    def write_daily_basic(self, iso_date: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "daily_basic" / f"{iso_date}.csv",
                                    index=False)

    def write_fina(self, ts_code: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "fina_indicator" / f"{ts_code}.csv",
                                    index=False)

    def write_index_weight(self, idx_short: str, ym: str, codes: list[str]) -> None:
        df = pd.DataFrame({
            "index_code": [f"{idx_short}.SH" for _ in codes],
            "con_code": codes,
            "weight": [1.0 / len(codes)] * len(codes),
            "trade_date": [f"{ym.replace('-', '')}01"] * len(codes),
        })
        df.to_csv(self.root / "index_weight" / f"{idx_short}_{ym}.csv", index=False)

    def write_stock_basic(self, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "stock_basic.csv", index=False)


class DailyAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.builder = _CacheBuilder(self.cache)
        self.builder.write_daily("2023-06-29", [
            {"ts_code": "000001.SZ", "close": 12.5, "open": 12.3,
             "high": 12.8, "low": 12.2, "vol": 1e6, "amount": 1.25e10},
        ])
        self.builder.write_daily("2023-06-30", [
            {"ts_code": "000001.SZ", "close": 12.7, "open": 12.5,
             "high": 12.9, "low": 12.4, "vol": 1.1e6, "amount": 1.40e10},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_data_for_exact_date(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.daily(as_of=date(2023, 6, 29))
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]["close"], 12.5)

    def test_returns_empty_when_no_data(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.daily(as_of=date(2023, 1, 1))
        self.assertTrue(df.empty)


class FinaAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.builder = _CacheBuilder(self.cache)
        # fina with one ann_date BEFORE as_of and one AFTER
        self.builder.write_fina("000001.SZ", [
            {"ts_code": "000001.SZ", "ann_date": "20230420",
             "end_date": "20230331", "roe": 3.5},
            {"ts_code": "000001.SZ", "ann_date": "20230820",
             "end_date": "20230630", "roe": 7.0},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_filters_by_ann_date(self):
        """Looking up fina at 2023-06-30 only sees ann_date <= 2023-06-30."""
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.fina_for_code("000001.SZ", as_of=date(2023, 6, 30))
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]["roe"], 3.5)

    def test_no_future_leakage(self):
        """At 2023-04-19, no fina row visible (first ann_date = 2023-04-20)."""
        view = PointInTimeView(as_of=date(2024, 1, 1), cache_root=self.cache)
        df = view.fina_for_code("000001.SZ", as_of=date(2023, 4, 19))
        self.assertEqual(len(df), 0)

    def test_returns_empty_if_code_unknown(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.fina_for_code("999999.SZ", as_of=date(2023, 6, 30))
        self.assertTrue(df.empty)


class UniverseAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.builder = _CacheBuilder(self.cache)
        # Index weight for 2023-06
        self.builder.write_index_weight("000300", "2023-06",
                                          ["000001.SZ", "000002.SZ"])
        self.builder.write_index_weight("000905", "2023-06",
                                          ["600000.SH"])
        # Stock basic
        self.builder.write_stock_basic([
            {"ts_code": "000001.SZ", "name": "平安银行",
             "list_date": "19910403", "delist_date": "", "industry": "银行"},
            {"ts_code": "000002.SZ", "name": "万科A",
             "list_date": "19910129", "delist_date": "", "industry": "房地产"},
            {"ts_code": "600000.SH", "name": "浦发银行",
             "list_date": "19991110", "delist_date": "", "industry": "银行"},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_hs300_zz500_union(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 6, 30))
        self.assertIn("000001.SZ", codes)
        self.assertIn("000002.SZ", codes)
        self.assertIn("600000.SH", codes)

    def test_returns_only_hs300_when_filtered(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 6, 30), indices=["hs300"])
        self.assertIn("000001.SZ", codes)
        self.assertIn("000002.SZ", codes)
        self.assertNotIn("600000.SH", codes)

    def test_uses_most_recent_monthly_snapshot(self):
        """Looking up universe(2023-07-15) should use 2023-06 snapshot (no 2023-07 file)."""
        view = PointInTimeView(as_of=date(2023, 7, 15), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 7, 15), indices=["hs300"])
        self.assertIn("000001.SZ", codes)

    def test_excludes_not_yet_listed(self):
        """Stock with list_date > as_of is excluded."""
        self.builder.write_stock_basic([
            {"ts_code": "000001.SZ", "name": "平安银行",
             "list_date": "19910403", "delist_date": "", "industry": "银行"},
            {"ts_code": "000002.SZ", "name": "万科A",
             "list_date": "20240101", "delist_date": "", "industry": "房地产"},
        ])
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 6, 30), indices=["hs300"])
        self.assertIn("000001.SZ", codes)
        self.assertNotIn("000002.SZ", codes)

    def test_excludes_already_delisted(self):
        """Stock with delist_date <= as_of is excluded."""
        self.builder.write_stock_basic([
            {"ts_code": "000001.SZ", "name": "平安银行",
             "list_date": "19910403", "delist_date": "20230101",
             "industry": "银行"},
            {"ts_code": "000002.SZ", "name": "万科A",
             "list_date": "19910129", "delist_date": "", "industry": "房地产"},
        ])
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 6, 30), indices=["hs300"])
        self.assertNotIn("000001.SZ", codes)
        self.assertIn("000002.SZ", codes)


if __name__ == "__main__":
    unittest.main()
