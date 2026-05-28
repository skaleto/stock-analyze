"""Tests for backtest engine main loop.

The engine drives ``simulator.execute_due_orders`` and ``simulator.update_nav``
day by day over a historical window, reading market data via
``PointInTimeView``. Signals on Fridays are computed by a simple top-N rule
(low PE first) — MVP simplification; full overlay-driven signals are future
work.
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.markets.a_share.backtest import engine


class _CacheBuilder:
    """Build a minimal backtest_cache for engine tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for sub in ("daily", "daily_basic", "fina_indicator",
                     "index_weight", "adj_factor"):
            (root / sub).mkdir(parents=True)

    def add_trade_cal(self, dates_yyyymmdd: list[str]) -> None:
        pd.DataFrame({
            "cal_date": dates_yyyymmdd,
            "is_open": [1] * len(dates_yyyymmdd),
        }).to_csv(self.root / "trade_cal.csv", index=False)

    def add_daily(self, iso_date: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "daily" / f"{iso_date}.csv",
                                    index=False)

    def add_daily_basic(self, iso_date: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "daily_basic" / f"{iso_date}.csv",
                                    index=False)

    def add_index_weight(self, idx_short: str, ym: str, codes: list[str]) -> None:
        if codes:
            weights = [1.0 / len(codes)] * len(codes)
        else:
            weights = []
        df = pd.DataFrame({
            "index_code": [f"{idx_short}.SH"] * len(codes),
            "con_code": codes,
            "weight": weights,
            "trade_date": [f"{ym.replace('-', '')}01"] * len(codes),
        })
        df.to_csv(self.root / "index_weight" / f"{idx_short}_{ym}.csv",
                  index=False)

    def add_stock_basic(self, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_csv(self.root / "stock_basic.csv", index=False)


def _minimal_overlay() -> dict:
    return {
        "strategy_id": "backtest_test",
        "agent_id": "claude",
        "accounts": [
            {
                "id": "main",
                "name": "Main",
                "scope": "hs300",
                "benchmark": "000300",
                "cash": 1_000_000,
                "top_n": 2,
            },
        ],
        "trading": {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.0,
            "max_single_weight": 0.5,
        },
    }


class RunBacktestSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"
        self.out = Path(self.tmp.name) / "out"
        self.cache.mkdir(parents=True)
        self.out.mkdir(parents=True)

        builder = _CacheBuilder(self.cache)
        # 5 trading days, Mon-Fri (ending Friday 2023-06-30 — a signal day)
        trade_dates = ["20230626", "20230627", "20230628", "20230629", "20230630"]
        builder.add_trade_cal(trade_dates)

        for iso, raw in zip(
            ["2023-06-26", "2023-06-27", "2023-06-28", "2023-06-29", "2023-06-30"],
            trade_dates,
        ):
            builder.add_daily(iso, [
                {"ts_code": "000001.SZ", "trade_date": raw,
                 "open": 12.0, "close": 12.0 + 0.1, "high": 12.5,
                 "low": 11.9, "vol": 1e6, "amount": 1.2e10},
                {"ts_code": "000002.SZ", "trade_date": raw,
                 "open": 20.0, "close": 20.0 + 0.05, "high": 20.5,
                 "low": 19.8, "vol": 8e5, "amount": 1.6e10},
            ])
            builder.add_daily_basic(iso, [
                {"ts_code": "000001.SZ", "trade_date": raw,
                 "pe_ttm": 5.5, "pb": 1.1, "dv_ttm": 4.5,
                 "total_mv": 200_000, "circ_mv": 150_000},
                {"ts_code": "000002.SZ", "trade_date": raw,
                 "pe_ttm": 12.0, "pb": 1.8, "dv_ttm": 2.0,
                 "total_mv": 250_000, "circ_mv": 200_000},
            ])

        builder.add_index_weight("000300", "2023-06", ["000001.SZ", "000002.SZ"])
        builder.add_index_weight("000905", "2023-06", [])
        builder.add_stock_basic([
            {"ts_code": "000001.SZ", "name": "平安银行",
             "list_date": "19910403", "delist_date": "", "industry": "银行"},
            {"ts_code": "000002.SZ", "name": "万科A",
             "list_date": "19910129", "delist_date": "", "industry": "房地产"},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_backtest_produces_required_outputs(self):
        """Engine smoke test: 5 days; outputs all required CSVs."""
        result = engine.run_backtest(
            overlay=_minimal_overlay(),
            start=date(2023, 6, 26),
            end=date(2023, 6, 30),
            universe=["hs300"],
            market_data_root=self.cache,
            out_dir=self.out,
        )

        # daily_nav, trades, signals must exist
        self.assertTrue((self.out / "daily_nav.csv").exists())
        self.assertTrue((self.out / "trades.csv").exists())
        self.assertTrue((self.out / "signals.csv").exists())
        self.assertTrue((self.out / "performance_summary.json").exists())

        # result.metrics has the 5 aggregate fields
        m = result.metrics
        self.assertIsNotNone(m.cum_return)
        self.assertIsNotNone(m.sharpe)
        self.assertIsNotNone(m.max_drawdown)

    def test_run_backtest_writes_one_nav_row_per_day(self):
        engine.run_backtest(
            overlay=_minimal_overlay(),
            start=date(2023, 6, 26),
            end=date(2023, 6, 30),
            universe=["hs300"],
            market_data_root=self.cache,
            out_dir=self.out,
        )
        nav = pd.read_csv(self.out / "daily_nav.csv")
        unique_dates = nav["date"].nunique()
        self.assertEqual(unique_dates, 5)

    def test_run_backtest_signal_generated_on_friday(self):
        engine.run_backtest(
            overlay=_minimal_overlay(),
            start=date(2023, 6, 26),
            end=date(2023, 6, 30),
            universe=["hs300"],
            market_data_root=self.cache,
            out_dir=self.out,
        )
        signals = pd.read_csv(self.out / "signals.csv")
        # 2023-06-30 is a Friday → one signal batch
        friday_signals = signals[signals["signal_date"] == "2023-06-30"]
        self.assertGreater(len(friday_signals), 0)

    def test_run_backtest_respects_in_memory(self):
        result = engine.run_backtest(
            overlay=_minimal_overlay(),
            start=date(2023, 6, 26),
            end=date(2023, 6, 30),
            universe=["hs300"],
            market_data_root=self.cache,
            out_dir=self.out,
            in_memory=True,
        )
        # Final outputs still exist (in_memory just skips per-day writes)
        self.assertTrue((self.out / "daily_nav.csv").exists())
        self.assertIsNotNone(result.metrics.cum_return)


if __name__ == "__main__":
    unittest.main()
