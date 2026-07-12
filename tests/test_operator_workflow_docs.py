from __future__ import annotations

import unittest
from pathlib import Path


class OperatorWorkflowDocsTests(unittest.TestCase):
    def test_weekly_and_monthly_scripts_have_no_claude_or_sentiment_dependency(self) -> None:
        weekly = Path("scripts/weekly.sh").read_text(encoding="utf-8")
        monthly = Path("scripts/monthly.sh").read_text(encoding="utf-8")
        combined = weekly + monthly

        self.assertNotIn("claude -p", combined)
        self.assertNotIn("record-sentiment", combined)
        self.assertNotIn("configs/agents/claude.yaml", combined)
        self.assertIn("notify-workflow-summary", weekly)
        self.assertIn("--cadence weekly", weekly)
        self.assertIn("运行 ${WEEK_END} 周度复盘", weekly)
        self.assertIn("notify-workflow-summary", monthly)
        self.assertIn("--cadence monthly", monthly)
        self.assertIn("运行 ${TARGET_MONTH} 月度策略演化", monthly)

    def test_repo_workflow_skill_names_only_active_markets(self) -> None:
        skill = Path(".claude/skills/stock-analyze-workflows/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("a_share", skill)
        self.assertIn("cn_qdii_etf", skill)
        self.assertIn("Codex", skill)
        self.assertNotIn("run-overseas.sh", skill)
        self.assertNotIn("hk/claude", skill)
        self.assertNotIn("record-sector-sentiment", skill)

    def test_alerting_docs_describe_fixed_summary_windows(self) -> None:
        docs = Path("docs/operator-alerting-setup.md").read_text(encoding="utf-8")

        self.assertIn("19:30", docs)
        self.assertIn("10:45", docs)
        self.assertIn("09:30", docs)
        self.assertNotIn("ExecStartPost=notify-daily-summary.sh", docs)


if __name__ == "__main__":
    unittest.main()
