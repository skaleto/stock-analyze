"""Regression tests for the archived direct-overseas runner."""
from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OLD_CHECKOUT = "/Users/yaoyibin/Documents/stock/stock-analyze"


class LocalScriptPathTests(unittest.TestCase):
    def test_local_scripts_do_not_pin_old_checkout(self):
        scripts = [
            REPO_ROOT / "scripts" / "statusline.sh",
            REPO_ROOT / "scripts" / "install-harness.sh",
        ]
        for script in scripts:
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertNotIn(OLD_CHECKOUT, text)

    def test_direct_overseas_runner_exists_only_in_archive(self):
        self.assertFalse(
            (REPO_ROOT / "scripts" / "run-overseas.sh").exists()
        )
        self.assertTrue(
            (REPO_ROOT / "archive" / "direct-overseas" / "source" / "scripts" / "notify-overseas.sh").exists()
        )

    def test_statusline_uses_portable_stat(self):
        text = (REPO_ROOT / "scripts" / "statusline.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("stat -f '%m'", text)
        self.assertIn("stat -c '%Y'", text)


if __name__ == "__main__":
    unittest.main()
