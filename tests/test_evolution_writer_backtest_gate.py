"""Tests that evolution_writer integrates the backtest floor gate."""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import evolution_writer
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics


class EvolutionWriterBacktestGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "configs/agents").mkdir(parents=True)
        # Minimal baseline
        (self.root / "configs/competition.yaml").write_text(json.dumps({
            "competition_id": "x",
            "start_date": "2026-01-01",
            "initial_cash": 100000,
            "accounts": [{"id": "main", "cash": 100000, "scope": "hs300",
                          "benchmark": "000300", "top_n": 50}],
            "schedule": {"execution": "next_trading_day_open",
                          "signal_day": "last_trading_day_of_week",
                          "rebalance": "weekly_after_close"},
            "trading": {"lot_size": 100, "commission_rate": 0.0003,
                         "min_commission": 5, "stamp_tax_rate": 0.0005,
                         "slippage_rate": 0.0, "max_single_weight": 0.05},
            "backtest": {"floor": {"max_drawdown": 0.25,
                                     "sharpe_floor": -0.5,
                                     "cum_return_floor": -0.15}},
        }))
        self.overlay_path = self.root / "configs/agents/claude.yaml"
        self.old_overlay = {
            "agent_id": "claude",
            "strategy_id": "old",
            "name": "Old",
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "factor_processing": {"winsorize_lower": 0.01, "winsorize_upper": 0.99,
                                    "neutralize_industry": True,
                                    "min_factor_coverage": 0.6},
            "portfolio_controls": {"max_industry_weight": 0.3,
                                     "hold_buffer_pct": 0.5,
                                     "max_holding_days": 60,
                                     "industry_unclassified_label": "未分类"},
            "filters": {"exclude_st": True, "max_fetch_candidates": 250,
                         "min_listing_days": 365, "min_pe": 0,
                         "min_avg_amount_20": 0, "min_market_cap_yi": 0,
                         "max_market_cap_yi": 100000, "require_fields": [],
                         "fallback_require_fields": []},
        }
        self.overlay_path.write_text(json.dumps(self.old_overlay))
        self.new_overlay = dict(self.old_overlay)
        self.new_overlay["factors"] = {"pe": {"weight": 0.8, "direction": "low"},
                                         "roe": {"weight": 0.2, "direction": "high"}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_gate_breach_blocks_evolution(self):
        """When gate raises BacktestFloorBreach, evolution_writer must propagate
        and not modify the live overlay."""
        breach = BacktestFloorBreach(
            "max_drawdown_exceeded",
            BacktestMetrics(-0.20, -0.15, -0.8, -0.32, -1.4),
        )
        original_yaml = self.overlay_path.read_text()

        with patch(
            "stock_analyze.backtest.gate.validate_overlay_via_backtest",
            side_effect=breach,
        ):
            with self.assertRaises(BacktestFloorBreach):
                evolution_writer.write_evolution(
                    agent_id="claude",
                    old_overlay=self.old_overlay,
                    new_overlay=self.new_overlay,
                    reasoning_md="# test",
                    repo_root=self.root,
                    month="2026-06",
                )

        # Live yaml unchanged
        self.assertEqual(self.overlay_path.read_text(), original_yaml)
        # Breach log was written
        breach_log = self.root / "data" / "claude" / "evolution_log" / "2026-06-floor-breach.md"
        self.assertTrue(breach_log.exists(),
                         f"expected breach log at {breach_log}")
        text = breach_log.read_text()
        self.assertIn("max_drawdown_exceeded", text)

    def test_gate_pass_lets_evolution_proceed(self):
        """When gate returns metrics, evolution_writer proceeds normally."""
        good_metrics = BacktestMetrics(0.05, 0.04, 0.8, -0.10, 0.6)
        with patch(
            "stock_analyze.backtest.gate.validate_overlay_via_backtest",
            return_value=good_metrics,
        ):
            summary = evolution_writer.write_evolution(
                agent_id="claude",
                old_overlay=self.old_overlay,
                new_overlay=self.new_overlay,
                reasoning_md="# test",
                repo_root=self.root,
                month="2026-06",
            )

        # Live overlay was overwritten
        live = json.loads(self.overlay_path.read_text())
        self.assertEqual(live["factors"]["roe"]["weight"], 0.2)
        # Summary returned successfully
        self.assertIn("to_hash", summary)
        # Backtest metrics were threaded into the diff JSON
        diff_path = self.root / "data" / "claude" / "evolution_diff" / "2026-06.json"
        self.assertTrue(diff_path.exists())
        diff_data = json.loads(diff_path.read_text())
        self.assertIn("backtest_metrics", diff_data)
        self.assertAlmostEqual(diff_data["backtest_metrics"]["sharpe"], 0.8)


if __name__ == "__main__":
    unittest.main()
