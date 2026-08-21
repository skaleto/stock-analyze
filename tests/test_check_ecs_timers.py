from __future__ import annotations

import unittest
from pathlib import Path


class CheckEcsTimersScriptTests(unittest.TestCase):
    def test_ledger_check_uses_market_namespaced_a_share_paths(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn('runs_csv="${app_dir}/data/a_share/${agent}/runs.csv"', script)
        self.assertNotIn('runs_csv="${app_dir}/data/${agent}/runs.csv"', script)

    def test_qdii_daily_services_are_pipeline_triggered_not_timer_driven(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")
        expected_block = script.split("expected=(", 1)[1].split(")", 1)[0]
        disabled_block = script.split("disabled=(", 1)[1].split(")", 1)[0]

        self.assertNotIn("stock-analyze-codex-cn-qdii-etf-daily.timer", expected_block)
        self.assertNotIn("stock-analyze-claude-cn-qdii-etf-daily.timer", expected_block)
        self.assertIn("stock-analyze-codex-cn-qdii-etf-daily.timer", disabled_block)
        self.assertIn("stock-analyze-claude-cn-qdii-etf-daily.timer", disabled_block)
        self.assertIn("stock-analyze-codex-cn-qdii-etf-weekly.timer", expected_block)
        self.assertIn("stock-analyze-claude-cn-qdii-etf-weekly.timer", expected_block)
        self.assertIn("stock-analyze-qdii-research.timer", expected_block)
        self.assertIn('data/cn_qdii_etf/${agent}/runs.csv', script)
        self.assertIn("latest_failed_epoch", script)
        self.assertIn("latest_finished_epoch", script)
        self.assertIn("latest_failed_epoch > latest_finished_epoch", script)

    def test_consolidated_notification_timers_are_expected(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn("stock-analyze-daily-summary.timer", script)
        self.assertIn("stock-analyze-weekly-summary.timer", script)
        self.assertIn("stock-analyze-monthly-summary.timer", script)

    def test_research_model_and_intelligence_service_results_are_checked(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn("stock-analyze-research.service", script)
        self.assertIn("stock-analyze-model-iteration.service", script)
        self.assertIn("stock-analyze-model-training.service", script)
        self.assertIn("stock-analyze-intelligence.service", script)
        self.assertIn("stock-analyze-intelligence-reconcile.service", script)
        self.assertIn(
            "stock-analyze-intelligence-artifact-backfill.service",
            script,
        )
        self.assertIn("stock-analyze-intelligence-semantic.service", script)
        self.assertIn("stock-analyze-intelligence-quality.service", script)
        self.assertIn("stock-analyze-tabular-forward.service", script)
        self.assertIn("stock-analyze-daily-finalize.service", script)
        self.assertIn("stock-analyze-ifind-source-audit.service", script)
        self.assertIn("--property=Result", script)
        self.assertIn("--property=ExecMainStatus", script)
        self.assertIn('"$exit_status" == "75"', script)
        self.assertIn("four formal daily ledgers were not complete", script)
        self.assertIn("bounded-work guard", script)
        self.assertIn("paper_portfolios/current_status.json", script)
        self.assertIn("four isolated production paper challengers", script)

    def test_reconcile_timer_is_required_but_backfill_remains_manual(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")
        expected_block = script.split("expected=(", 1)[1].split(")", 1)[0]

        self.assertIn("stock-analyze-intelligence-reconcile.timer", expected_block)
        self.assertIn(
            "stock-analyze-intelligence-artifact-backfill.timer",
            expected_block,
        )
        self.assertIn(
            "stock-analyze-intelligence-semantic.timer",
            expected_block,
        )
        self.assertIn(
            "stock-analyze-intelligence-quality.timer",
            expected_block,
        )
        self.assertIn("stock-analyze-ifind-source-audit.timer", expected_block)
        self.assertNotIn("stock-analyze-intelligence-backfill", expected_block)
        self.assertNotIn("stock-analyze-intelligence-backfill.timer", script)


if __name__ == "__main__":
    unittest.main()
