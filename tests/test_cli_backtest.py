"""Tests for the `backtest` research CLI subcommand."""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pandas as pd

from stock_analyze import cli
from stock_analyze.markets.a_share.backtest.types import BacktestMetrics, BacktestResult


class BacktestCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.out_dir = Path(self.tmp.name) / "bt_run"
        self.overlay_path = Path(self.tmp.name) / "overlay.json"
        self.overlay_path.write_text(json.dumps({
            "agent_id": "claude",
            "strategy_id": "test",
            "name": "Test overlay",
            "factors": {
                "pe": {"weight": 1.0, "direction": "low"},
            },
            "factor_processing": {
                "winsorize_lower": 0.01,
                "winsorize_upper": 0.99,
                "neutralize_industry": True,
                "min_factor_coverage": 0.6,
            },
            "portfolio_controls": {
                "max_industry_weight": 0.3,
                "hold_buffer_pct": 0.5,
                "max_holding_days": 365,
                "industry_unclassified_label": "未分类",
            },
            "filters": {
                "exclude_st": True,
                "max_fetch_candidates": 250,
                "min_listing_days": 365,
                "min_pe": 0,
                "min_avg_amount_20": 0,
                "min_market_cap_yi": 0,
                "max_market_cap_yi": 100000,
                "require_fields": [],
                "fallback_require_fields": [],
            },
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_backtest_invokes_run_backtest(self):
        fake_result = BacktestResult(
            out_dir=self.out_dir,
            start=date(2021, 1, 1),
            end=date(2024, 12, 31),
            metrics=BacktestMetrics(0.18, 0.087, 1.4, -0.087, 0.92),
        )
        with patch(
            "stock_analyze.markets.a_share.backtest.engine.run_backtest",
            return_value=fake_result,
        ) as mocked, patch(
            "stock_analyze.competition.load",
            return_value={"agent_id": "claude", "factors": {}, "accounts": []},
        ):
            cli.main([
                "backtest",
                "--agent", "claude",
                "--start", "2021-01-01",
                "--end", "2024-12-31",
                "--overlay", str(self.overlay_path),
                "--output", str(self.out_dir),
            ])
            mocked.assert_called_once()
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["start"], date(2021, 1, 1))
            self.assertEqual(kwargs["end"], date(2024, 12, 31))
            self.assertEqual(kwargs["out_dir"], self.out_dir)

    def test_backtest_universe_default_is_both(self):
        fake_result = BacktestResult(
            out_dir=self.out_dir,
            start=date(2021, 1, 1),
            end=date(2021, 1, 31),
            metrics=BacktestMetrics(0, 0, 0, 0, 0),
        )
        with patch(
            "stock_analyze.markets.a_share.backtest.engine.run_backtest",
            return_value=fake_result,
        ) as mocked, patch(
            "stock_analyze.competition.load",
            return_value={"agent_id": "claude", "factors": {}, "accounts": []},
        ):
            cli.main([
                "backtest",
                "--agent", "claude",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
                "--overlay", str(self.overlay_path),
                "--output", str(self.out_dir),
            ])
            self.assertEqual(
                mocked.call_args.kwargs["universe"],
                ["hs300", "zz500"],
            )

    def test_backtest_universe_hs300_only(self):
        fake_result = BacktestResult(
            out_dir=self.out_dir,
            start=date(2021, 1, 1),
            end=date(2021, 1, 31),
            metrics=BacktestMetrics(0, 0, 0, 0, 0),
        )
        with patch(
            "stock_analyze.markets.a_share.backtest.engine.run_backtest",
            return_value=fake_result,
        ) as mocked, patch(
            "stock_analyze.competition.load",
            return_value={"agent_id": "claude", "factors": {}, "accounts": []},
        ):
            cli.main([
                "backtest",
                "--agent", "claude",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
                "--overlay", str(self.overlay_path),
                "--output", str(self.out_dir),
                "--universe", "hs300",
            ])
            self.assertEqual(mocked.call_args.kwargs["universe"], ["hs300"])

    def test_backtest_in_memory_flag(self):
        fake_result = BacktestResult(
            out_dir=self.out_dir,
            start=date(2021, 1, 1),
            end=date(2021, 1, 31),
            metrics=BacktestMetrics(0, 0, 0, 0, 0),
        )
        with patch(
            "stock_analyze.markets.a_share.backtest.engine.run_backtest",
            return_value=fake_result,
        ) as mocked, patch(
            "stock_analyze.competition.load",
            return_value={"agent_id": "claude", "factors": {}, "accounts": []},
        ):
            cli.main([
                "backtest",
                "--agent", "claude",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
                "--overlay", str(self.overlay_path),
                "--output", str(self.out_dir),
                "--in-memory",
            ])
            self.assertTrue(mocked.call_args.kwargs["in_memory"])


if __name__ == "__main__":
    unittest.main()
