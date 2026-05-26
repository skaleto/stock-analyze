"""Tests for dashboard backtest-vs-live panel."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze import reporting


class BacktestVsLivePanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        # Training run (synthetic NAV path: +5% over 5 days)
        train_dir = self.repo / "data" / "claude" / "backtest" / "training" / "2026-06"
        train_dir.mkdir(parents=True)
        pd.DataFrame({
            "date": ["2021-01-04", "2021-01-05", "2024-12-29",
                      "2024-12-30", "2024-12-31"],
            "account_id": ["main"] * 5,
            "cash": [500_000] * 5,
            "positions_value": [0, 0, 50_000, 75_000, 100_000],
            "total_value": [500_000, 500_000, 550_000, 575_000, 600_000],
        }).to_csv(train_dir / "daily_nav.csv", index=False)

        # Live run (+2%)
        live_dir = self.repo / "data" / "claude"
        live_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "date": ["2026-05-18", "2026-05-19", "2026-05-25"],
            "account_id": ["main"] * 3,
            "cash": [500_000] * 3,
            "positions_value": [0, 5_000, 10_000],
            "total_value": [500_000, 505_000, 510_000],
        }).to_csv(live_dir / "daily_nav.csv", index=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_panel_renders_both_series(self):
        html = reporting.render_backtest_vs_live_panel(
            agent_id="claude", repo_root=self.repo,
        )
        self.assertIn("历史回测", html)
        self.assertIn("真实运行", html)

    def test_panel_includes_diff_indicator(self):
        html = reporting.render_backtest_vs_live_panel(
            agent_id="claude", repo_root=self.repo,
        )
        # Training cum = +20% (500k -> 600k), Live cum = +2% (500k -> 510k)
        # Diff = +18pp; should be marked as warn (|diff| > 5pp)
        self.assertIn("差异", html)
        self.assertIn("warn", html.lower())

    def test_panel_empty_state_when_no_training_data(self):
        # Wipe training dir
        import shutil
        shutil.rmtree(self.repo / "data" / "claude" / "backtest")
        html = reporting.render_backtest_vs_live_panel(
            agent_id="claude", repo_root=self.repo,
        )
        self.assertIn("尚无", html)


if __name__ == "__main__":
    unittest.main()
