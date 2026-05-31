from __future__ import annotations

import unittest
from pathlib import Path


class CheckEcsTimersScriptTests(unittest.TestCase):
    def test_ledger_check_uses_market_namespaced_a_share_paths(self) -> None:
        script = Path("scripts/check-ecs-timers.sh").read_text(encoding="utf-8")

        self.assertIn('runs_csv="${app_dir}/data/a_share/${agent}/runs.csv"', script)
        self.assertNotIn('runs_csv="${app_dir}/data/${agent}/runs.csv"', script)


if __name__ == "__main__":
    unittest.main()
