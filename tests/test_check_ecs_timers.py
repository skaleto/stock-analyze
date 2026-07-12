from __future__ import annotations

import unittest
from pathlib import Path


class CheckEcsTimersScriptTests(unittest.TestCase):
    def test_ledger_check_uses_market_namespaced_a_share_paths(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn('runs_csv="${app_dir}/data/a_share/${agent}/runs.csv"', script)
        self.assertNotIn('runs_csv="${app_dir}/data/${agent}/runs.csv"', script)

    def test_qdii_timers_are_expected_and_recent_failures_are_checked(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn("stock-analyze-codex-cn-qdii-etf-daily.timer", script)
        self.assertIn("stock-analyze-codex-cn-qdii-etf-weekly.timer", script)
        self.assertIn("stock-analyze-claude-cn-qdii-etf-daily.timer", script)
        self.assertIn("stock-analyze-claude-cn-qdii-etf-weekly.timer", script)
        self.assertIn("stock-analyze-qdii-research.timer", script)
        self.assertIn('data/cn_qdii_etf/${agent}/runs.csv', script)
        self.assertIn("latest_failed_epoch", script)
        self.assertIn("latest_finished_epoch", script)
        self.assertIn("latest_failed_epoch > latest_finished_epoch", script)

    def test_consolidated_notification_timers_are_expected(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn("stock-analyze-daily-summary.timer", script)
        self.assertIn("stock-analyze-weekly-summary.timer", script)
        self.assertIn("stock-analyze-monthly-summary.timer", script)


if __name__ == "__main__":
    unittest.main()
