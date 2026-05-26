"""Tests that monthly briefing isolates training-window (full) from validation-window (aggregate-only) detail."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import agent_briefing


class BriefingBacktestIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.training_dir = self.repo / "data" / "claude" / "backtest" / "training" / "2026-06"
        self.validation_dir = self.repo / "data" / "claude" / "backtest" / "validation" / "2026-06"
        self.training_dir.mkdir(parents=True)
        self.validation_dir.mkdir(parents=True)

        # Both windows have the same shape but differ in detail policy
        summary = {
            "cum_return": 0.183,
            "annual_return": 0.087,
            "sharpe": 1.4,
            "max_drawdown": -0.087,
            "information_ratio": 0.92,
            "month_breakdown": [
                {"month": "2026-01", "ret": 0.02},
                {"month": "2026-02", "ret": -0.01},
            ],
            "factor_breakdown": [
                {"factor": "pe", "contribution": 0.012},
                {"factor": "roe", "contribution": 0.041},
            ],
        }
        (self.training_dir / "performance_summary.json").write_text(json.dumps(summary))
        (self.validation_dir / "performance_summary.json").write_text(json.dumps(summary))

    def tearDown(self):
        self.tmp.cleanup()

    def test_training_section_includes_monthly_breakdown(self):
        text = agent_briefing.render_training_section(
            agent_id="claude", month="2026-06", repo_root=self.repo,
        )
        self.assertIn("训练窗口", text)
        # Full detail: monthly breakdown is visible
        self.assertIn("2026-01", text)
        self.assertIn("2026-02", text)

    def test_validation_section_only_5_aggregate_numbers(self):
        text = agent_briefing.render_validation_section(
            agent_id="claude", month="2026-06", repo_root=self.repo,
        )
        self.assertIn("验证窗口", text)
        # Aggregates present
        for label in ["累计", "年化", "Sharpe", "最大回撤", "IR"]:
            self.assertIn(label, text)
        # Monthly + factor breakdowns NOT present (information isolation)
        self.assertNotIn("2026-01", text)
        self.assertNotIn("2026-02", text)
        self.assertNotIn("factor_breakdown", text)
        self.assertNotIn("month_breakdown", text)

    def test_validation_section_renders_no_data_message_when_missing(self):
        (self.validation_dir / "performance_summary.json").unlink()
        text = agent_briefing.render_validation_section(
            agent_id="claude", month="2026-06", repo_root=self.repo,
        )
        self.assertIn("尚无", text)


if __name__ == "__main__":
    unittest.main()
