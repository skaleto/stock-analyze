from __future__ import annotations

import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class QDIISystemdUnitTests(unittest.TestCase):
    def test_services_run_both_qdii_strategy_slots(self) -> None:
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                daily = (
                    UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-daily.service"
                ).read_text(encoding="utf-8")
                weekly = (
                    UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-weekly.service"
                ).read_text(encoding="utf-8")

                self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", daily)
                self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", weekly)
                self.assertIn(
                    f"--market cn_qdii_etf --agent {agent} run-daily", daily
                )
                self.assertIn(
                    f"--market cn_qdii_etf --agent {agent} run-weekly", weekly
                )
                self.assertIn("run-daily --offline", daily)
                self.assertIn("run-weekly --offline", weekly)
                self.assertIn("weekly review", weekly.lower())
                self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", daily)
                self.assertIn("OnSuccess=stock-analyze-aggregate-dashboard.service", weekly)

    def test_daily_is_pipeline_triggered_and_weekly_timer_is_persistent(self) -> None:
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                weekly = (
                    UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-weekly.timer"
                ).read_text(encoding="utf-8")

                self.assertFalse(
                    (UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-daily.timer").exists()
                )
                self.assertIn("OnCalendar=Sat *-*-* 10:15:00 Asia/Shanghai", weekly)
                self.assertIn("Persistent=true", weekly)

    def test_weekly_research_timer_runs_after_strategy_slots_and_before_summary(self) -> None:
        service = (UNIT_DIR / "stock-analyze-qdii-research.service").read_text(encoding="utf-8")
        timer = (UNIT_DIR / "stock-analyze-qdii-research.timer").read_text(encoding="utf-8")

        self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", service)
        self.assertIn("scripts/run-qdii-research.sh", service)
        self.assertIn("OnSuccess=stock-analyze-aggregate-dashboard.service", service)
        self.assertIn("OnCalendar=Sat *-*-* 10:30:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
