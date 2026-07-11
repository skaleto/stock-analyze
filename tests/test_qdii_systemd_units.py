from __future__ import annotations

import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class QDIISystemdUnitTests(unittest.TestCase):
    def test_services_run_only_the_codex_qdii_workflows(self) -> None:
        daily = (UNIT_DIR / "stock-analyze-codex-cn-qdii-etf-daily.service").read_text(
            encoding="utf-8"
        )
        weekly = (UNIT_DIR / "stock-analyze-codex-cn-qdii-etf-weekly.service").read_text(
            encoding="utf-8"
        )

        self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", daily)
        self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", weekly)
        self.assertIn("--market cn_qdii_etf --agent codex run-daily", daily)
        self.assertIn("--market cn_qdii_etf --agent codex run-weekly", weekly)
        self.assertNotIn("--agent claude", daily + weekly)

    def test_timers_use_cst_wall_clock_and_persist_missed_runs(self) -> None:
        daily = (UNIT_DIR / "stock-analyze-codex-cn-qdii-etf-daily.timer").read_text(
            encoding="utf-8"
        )
        weekly = (UNIT_DIR / "stock-analyze-codex-cn-qdii-etf-weekly.timer").read_text(
            encoding="utf-8"
        )

        self.assertIn("OnCalendar=Mon..Fri *-*-* 18:50:00 Asia/Shanghai", daily)
        self.assertIn("OnCalendar=Sat *-*-* 10:15:00 Asia/Shanghai", weekly)
        self.assertIn("Persistent=true", daily)
        self.assertIn("Persistent=true", weekly)


if __name__ == "__main__":
    unittest.main()
