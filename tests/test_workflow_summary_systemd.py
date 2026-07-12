from __future__ import annotations

import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class WorkflowSummarySystemdTests(unittest.TestCase):
    def test_aggregate_dashboard_no_longer_pushes_a_message_per_child(self) -> None:
        service = (UNIT_DIR / "stock-analyze-aggregate-dashboard.service").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("notify-daily-summary.sh", service)
        self.assertNotIn("ExecStartPost=", service)

    def test_summary_services_call_the_idempotent_workflow_command(self) -> None:
        for cadence in ("daily", "weekly", "monthly"):
            with self.subTest(cadence=cadence):
                service = (
                    UNIT_DIR / f"stock-analyze-{cadence}-summary.service"
                ).read_text(encoding="utf-8")
                self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", service)
                self.assertIn(
                    f"notify-workflow-summary --cadence {cadence}", service
                )
                self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", service)

    def test_summary_timers_have_one_deliberate_delivery_window(self) -> None:
        expected = {
            "daily": "OnCalendar=Mon..Fri *-*-* 19:30:00 Asia/Shanghai",
            "weekly": "OnCalendar=Sat *-*-* 10:45:00 Asia/Shanghai",
            "monthly": "OnCalendar=*-*-01 09:30:00 Asia/Shanghai",
        }
        for cadence, calendar in expected.items():
            with self.subTest(cadence=cadence):
                timer = (
                    UNIT_DIR / f"stock-analyze-{cadence}-summary.timer"
                ).read_text(encoding="utf-8")
                self.assertIn(calendar, timer)
                self.assertIn(
                    f"Unit=stock-analyze-{cadence}-summary.service", timer
                )


if __name__ == "__main__":
    unittest.main()
