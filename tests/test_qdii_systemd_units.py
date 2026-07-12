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
                self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", daily)
                self.assertIn("OnSuccess=stock-analyze-aggregate-dashboard.service", weekly)

    def test_timers_use_cst_wall_clock_and_persist_missed_runs(self) -> None:
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                daily = (
                    UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-daily.timer"
                ).read_text(encoding="utf-8")
                weekly = (
                    UNIT_DIR / f"stock-analyze-{agent}-cn-qdii-etf-weekly.timer"
                ).read_text(encoding="utf-8")

                self.assertIn("OnCalendar=Mon..Fri *-*-* 18:50:00 Asia/Shanghai", daily)
                self.assertIn("OnCalendar=Sat *-*-* 10:15:00 Asia/Shanghai", weekly)
                self.assertIn("Persistent=true", daily)
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
