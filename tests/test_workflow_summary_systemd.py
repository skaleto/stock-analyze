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

    def test_daily_services_trigger_one_completion_gated_finalizer(self) -> None:
        finalizer = (
            UNIT_DIR / "stock-analyze-daily-finalize.service"
        ).read_text(encoding="utf-8")
        self.assertIn("notify-workflow-summary --cadence daily", finalizer)
        self.assertIn("--require-complete", finalizer)
        self.assertIn("--wait-seconds 1200", finalizer)
        self.assertIn("SuccessExitStatus=75", finalizer)
        self.assertIn("competition-dashboard --market all", finalizer)
        self.assertIn(
            "systemctl start --no-block stock-analyze-model-iteration.service",
            finalizer,
        )
        self.assertIn('if [ "$result" -eq 0 ]', finalizer)

        for name in (
            "stock-analyze-claude-daily.service",
            "stock-analyze-codex-daily.service",
            "stock-analyze-claude-cn-qdii-etf-daily.service",
            "stock-analyze-codex-cn-qdii-etf-daily.service",
        ):
            with self.subTest(unit=name):
                service = (UNIT_DIR / name).read_text(encoding="utf-8")
                self.assertIn(
                    "OnSuccess=stock-analyze-daily-finalize.service",
                    service,
                )
                self.assertNotIn(
                    "OnSuccess=stock-analyze-aggregate-dashboard.service",
                    service,
                )

    def test_summary_timers_have_one_deliberate_delivery_window(self) -> None:
        expected = {
            "daily": "OnCalendar=Mon..Fri *-*-* 21:30:00 Asia/Shanghai",
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
                self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
