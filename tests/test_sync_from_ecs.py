from __future__ import annotations

import unittest
from pathlib import Path


class SyncFromEcsScriptTests(unittest.TestCase):
    def test_exclude_cache_path_is_relative_to_data_source_root(self) -> None:
        script = Path("scripts/sync-from-ecs.sh").read_text(encoding="utf-8")

        self.assertIn("--exclude 'shared/cache/'", script)
        self.assertIn("--exclude 'shared/backtest_cache/'", script)
        self.assertNotIn("--exclude 'data/shared/cache/'", script)


if __name__ == "__main__":
    unittest.main()
