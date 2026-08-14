from __future__ import annotations

import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class IntelligenceSystemdTests(unittest.TestCase):
    def _read(self, name: str) -> str:
        return (UNIT_DIR / name).read_text(encoding="utf-8")

    def test_fast_timer_uses_five_decision_relevant_refreshes(self) -> None:
        timer = self._read("stock-analyze-intelligence.timer")

        self.assertIn(
            "OnCalendar=Mon..Fri *-*-* 09,12,16,23:30:00 "
            "Asia/Shanghai",
            timer,
        )
        self.assertIn(
            "OnCalendar=Mon..Fri *-*-* 21:45:00 Asia/Shanghai",
            timer,
        )
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=stock-analyze-intelligence.service", timer)

    def test_ifind_weekly_audit_is_quota_bounded_and_alerted(self) -> None:
        timer = self._read(
            "stock-analyze-ifind-source-audit.timer"
        )
        service = self._read(
            "stock-analyze-ifind-source-audit.service"
        )

        self.assertIn(
            "OnCalendar=Sat *-*-* 08:30:00 Asia/Shanghai",
            timer,
        )
        self.assertIn("Persistent=true", timer)
        self.assertIn(
            "intelligence-source-audit",
            service,
        )
        self.assertIn(
            "--datasets announcement",
            service,
        )
        self.assertIn(
            "--announcement-scope full-market",
            service,
        )
        self.assertIn("--supplement", service)
        self.assertIn(
            "IFIND_USER_FILE=/etc/stock-analyze/secrets/ifind_username",
            service,
        )
        self.assertIn(
            "OnFailure=stock-analyze-pipeline-failure@%n.service",
            service,
        )
        self.assertIn(
            "ExecStartPost=/opt/stock-analyze/venv/bin/python "
            "-m stock_analyze.cli intelligence-semantic-status",
            service,
        )

    def test_fast_service_only_runs_metadata_rules_and_light_status(self) -> None:
        service = self._read("stock-analyze-intelligence.service")

        self.assertIn("intelligence-ingest", service)
        self.assertIn("intelligence-extract", service)
        self.assertIn("--limit 1000", service)
        self.assertNotIn("intelligence-status", service)
        self.assertIn("intelligence-semantic-status", service)
        self.assertIn("--wait 600", service)
        self.assertIn("/run/stock-analyze-intelligence-reconcile.lock", service)
        self.assertIn("MemoryMax=1250M", service)
        for forbidden in (
            "intelligence-enrich",
            "intelligence-backfill",
            "intelligence-semantic-daily",
            "download-pdf",
            "deepseek",
            "pdf",
        ):
            self.assertNotIn(forbidden, service)
        self.assertNotIn(
            "-m stock_analyze.cli intelligence-reconcile",
            service,
        )

    def test_reconcile_runs_off_peak_and_is_resource_bounded(self) -> None:
        timer = self._read("stock-analyze-intelligence-reconcile.timer")
        service = self._read("stock-analyze-intelligence-reconcile.service")

        self.assertIn("OnCalendar=*-*-* 00:30:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn(
            "Unit=stock-analyze-intelligence-reconcile.service", timer
        )
        self.assertIn("intelligence-reconcile", service)
        self.assertIn("--lookback-days 2", service)
        self.assertIn(
            "--limit 100 --stages metadata enqueue download",
            service,
        )
        self.assertNotIn("for parse_batch", service)
        self.assertNotIn("--stages parse", service)
        self.assertNotIn(" semantic ", service)
        self.assertNotIn(" validate", service)
        self.assertIn("--wait 2700", service)
        self.assertIn("--conflict-exit-code 75", service)
        self.assertIn("SuccessExitStatus=75", service)
        self.assertIn("TimeoutStartSec=3h", service)
        self.assertIn("MemoryMax=1250M", service)

    def test_full_quality_scan_is_a_separate_low_frequency_job(self) -> None:
        service = self._read("stock-analyze-intelligence-quality.service")
        timer = self._read("stock-analyze-intelligence-quality.timer")

        self.assertIn("intelligence-status", service)
        self.assertNotIn("intelligence-ingest", service)
        self.assertIn("/run/stock-analyze-intelligence-reconcile.lock", service)
        self.assertIn("--wait 1500", service)
        self.assertIn("SuccessExitStatus=75", service)
        self.assertIn("MemoryMax=1250M", service)
        self.assertIn(
            "OnCalendar=Sun *-*-* 03:15:00 Asia/Shanghai",
            timer,
        )

    def test_backfill_is_manual_resumable_and_has_no_timer(self) -> None:
        service = self._read("stock-analyze-intelligence-backfill.service")

        self.assertFalse(
            (UNIT_DIR / "stock-analyze-intelligence-backfill.timer").exists()
        )
        self.assertNotIn("[Install]", service)
        self.assertNotIn("WantedBy=timers.target", service)
        self.assertIn(
            "EnvironmentFile=/etc/stock-analyze/intelligence-backfill.env",
            service,
        )
        self.assertIn("intelligence-backfill", service)
        self.assertIn("--source tushare_announcement", service)
        self.assertIn("--resume", service)
        self.assertIn("--start-date", service)
        self.assertIn("--end-date", service)
        self.assertIn("--max-partitions", service)
        self.assertIn("SuccessExitStatus=3", service)

    def test_artifact_backfill_is_low_priority_bounded_and_resumable(
        self,
    ) -> None:
        timer = self._read(
            "stock-analyze-intelligence-artifact-backfill.timer"
        )
        service = self._read(
            "stock-analyze-intelligence-artifact-backfill.service"
        )

        self.assertIn("OnCalendar=*-*-* 00..16,23:00,20,40 Asia/Shanghai", timer)
        self.assertNotIn("00..16,22,23:00,20,40", timer)
        self.assertIn("OnCalendar=*-*-* 17:00,20 Asia/Shanghai", timer)
        self.assertIn("OnCalendar=*-*-* 21:30,50 Asia/Shanghai", timer)
        self.assertIn("OnCalendar=*-*-* 22:40 Asia/Shanghai", timer)
        self.assertIn("RandomizedDelaySec=30s", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn(
            "stock_analyze.intelligence.artifact_backfill",
            service,
        )
        self.assertIn(
            "--runtime-budget-seconds 1080",
            service,
        )
        self.assertIn("TimeoutStartSec=22min", service)
        self.assertIn("Nice=10", service)
        self.assertIn("CPUWeight=25", service)
        self.assertIn("IOWeight=25", service)
        self.assertIn("MemoryHigh=1G", service)
        self.assertIn("MemoryMax=1250M", service)
        self.assertIn("MemorySwapMax=512M", service)
        self.assertIn(
            "/run/stock-analyze-intelligence-reconcile.lock",
            service,
        )
        self.assertIn("--conflict-exit-code 75", service)
        self.assertIn("SuccessExitStatus=75", service)
        self.assertNotIn("intelligence-semantic-status", service)

    def test_semantic_exchange_requires_the_production_executor_config(
        self,
    ) -> None:
        service = self._read(
            "stock-analyze-intelligence-semantic.service"
        )
        timer = self._read(
            "stock-analyze-intelligence-semantic.timer"
        )

        self.assertIn(
            "intelligence-semantic-daily",
            service,
        )
        self.assertIn(
            "ExecStartPre=/usr/bin/test -s /etc/stock-analyze/"
            "intelligence-semantic-executor.yaml",
            service,
        )
        self.assertIn("--limit 3", service)
        self.assertIn("--wait 300", service)
        self.assertIn("/run/stock-analyze-intelligence-reconcile.lock", service)
        self.assertIn("MemoryMax=1250M", service)
        self.assertIn(
            "--profile a-share-announcement-mentions-v27",
            service,
        )
        self.assertIn(
            "--executor-config /etc/stock-analyze/"
            "intelligence-semantic-executor.yaml",
            service,
        )
        self.assertNotIn("executor=()", service)
        self.assertNotIn("systemctl stop", service)
        self.assertIn(
            "OnCalendar=Mon..Fri *-*-* 22:10:00 Asia/Shanghai",
            timer,
        )
        self.assertIn("Persistent=true", timer)

    def test_deepseek_executor_uses_the_bounded_canary_budget(self) -> None:
        config = Path(
            "deploy/intelligence-semantic-executor.deepseek.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("max_documents_per_daily_run: 3", config)
        self.assertIn("daily_input_token_budget: 250000", config)

    def test_services_use_secrets_venv_failure_hook_and_distinct_locks(self) -> None:
        names = (
            "stock-analyze-intelligence.service",
            "stock-analyze-intelligence-reconcile.service",
            "stock-analyze-intelligence-backfill.service",
            "stock-analyze-intelligence-semantic.service",
        )
        contents = {name: self._read(name) for name in names}

        for service in contents.values():
            self.assertIn(
                "EnvironmentFile=-/etc/stock-analyze/secrets.env", service
            )
            self.assertIn("/opt/stock-analyze/venv/bin/python", service)
            self.assertIn(
                "OnFailure=stock-analyze-pipeline-failure@%n.service", service
            )
            self.assertIn(
                "ExecStartPost=/opt/stock-analyze/venv/bin/python "
                "-m stock_analyze.cli intelligence-semantic-status",
                service,
            )

        artifact = self._read(
            "stock-analyze-intelligence-artifact-backfill.service"
        )
        self.assertIn(
            "EnvironmentFile=-/etc/stock-analyze/secrets.env",
            artifact,
        )
        self.assertIn(
            "OnFailure=stock-analyze-pipeline-failure@%n.service",
            artifact,
        )
        self.assertNotIn("intelligence-semantic-status", artifact)

        self.assertIn(
            "/run/stock-analyze-intelligence-reconcile.lock",
            contents["stock-analyze-intelligence-reconcile.service"],
        )
        self.assertIn(
            "/run/stock-analyze-intelligence-backfill.lock",
            contents["stock-analyze-intelligence-backfill.service"],
        )
        self.assertNotEqual(
            "/run/stock-analyze-intelligence-reconcile.lock",
            "/run/stock-analyze-intelligence-backfill.lock",
        )


if __name__ == "__main__":
    unittest.main()
