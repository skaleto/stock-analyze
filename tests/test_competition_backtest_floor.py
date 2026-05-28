"""Tests that competition baseline exposes backtest.floor.* settings."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import competition


class BacktestFloorBaselineTests(unittest.TestCase):
    def test_baseline_includes_backtest_floor(self):
        baseline = competition.load_baseline()
        self.assertIn("backtest", baseline)
        self.assertIn("floor", baseline["backtest"])
        floor = baseline["backtest"]["floor"]
        self.assertAlmostEqual(floor["max_drawdown"], 0.25)
        self.assertAlmostEqual(floor["sharpe_floor"], -0.5)
        self.assertAlmostEqual(floor["cum_return_floor"], -0.15)

    def test_loaded_agent_config_exposes_backtest_floor(self):
        # competition.load(claude) should include backtest.floor from baseline
        cfg = competition.load("claude")
        self.assertIn("backtest", cfg)
        floor = cfg["backtest"]["floor"]
        self.assertAlmostEqual(floor["max_drawdown"], 0.25)

    def test_agent_overlay_cannot_override_backtest(self):
        """Agent overlay containing 'backtest' top-level key is rejected
        because 'backtest' is not in OVERLAY_ALLOWED_TOP_LEVEL."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Build a fake repo layout
            (root / "configs/agents").mkdir(parents=True)
            (root / "configs/competition_a_share.yaml").write_text(
                json.dumps({
                    "competition_id": "x",
                    "start_date": "2026-01-01",
                    "initial_cash": 100,
                    "accounts": [],
                    "schedule": {"execution": "n", "signal_day": "n",
                                  "rebalance": "n"},
                    "trading": {
                        "lot_size": 100, "commission_rate": 0.0003,
                        "min_commission": 5, "stamp_tax_rate": 0.0005,
                        "slippage_rate": 0.0, "max_single_weight": 0.05,
                    },
                    "backtest": {"floor": {"max_drawdown": 0.25}},
                })
            )
            bad_overlay = {
                "agent_id": "claude",
                "strategy_id": "x",
                "name": "x",
                "factors": {},
                "backtest": {"floor": {"max_drawdown": 0.10}},
            }
            (root / "configs/agents/claude_a_share.yaml").write_text(
                json.dumps(bad_overlay)
            )
            with self.assertRaises(Exception) as ctx:
                competition.load("claude", repo_root=root)
            # Either OverlayTopLevelKeyError or generic — the point is it rejects.
            self.assertIn("backtest", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
