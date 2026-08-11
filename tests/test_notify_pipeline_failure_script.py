from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class NotifyPipelineFailureScriptTests(unittest.TestCase):
    def test_falls_back_to_lark_app_dm_when_webhook_is_missing(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "notify-pipeline-failure.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = root / "python.calls"
            fake_python = root / "fake-python"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$SA_FAKE_PYTHON_CALLS\"\n"
                "cat >/dev/null\n",
                encoding="utf-8",
            )
            fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env.pop("SA_LARK_WEBHOOK", None)
            env.update(
                {
                    "SA_LOG_DIR": str(root / "logs"),
                    "SA_FAILURE_ALERT_STATE_DIR": str(root / "state"),
                    "SA_LARK_APP_ID": "cli_test",
                    "SA_LARK_APP_SECRET": "secret",
                    "SA_LARK_USER_OPEN_ID": "ou_test",
                    "SA_REPO_ROOT": str(repo_root),
                    "SA_VENV_PYTHON": str(fake_python),
                    "SA_FAKE_PYTHON_CALLS": str(calls),
                }
            )

            result = subprocess.run(
                [str(script), "stock-analyze-codex-daily.service"],
                cwd=repo_root,
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(calls.exists())
            self.assertIn("stock-analyze-codex-daily.service", calls.read_text(encoding="utf-8"))

    def test_same_unit_is_notified_only_once_within_cooldown(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "notify-pipeline-failure.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = root / "python.calls"
            fake_python = root / "fake-python"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$SA_FAKE_PYTHON_CALLS\"\n"
                "cat >/dev/null\n",
                encoding="utf-8",
            )
            fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env.pop("SA_LARK_WEBHOOK", None)
            env.update(
                {
                    "SA_LOG_DIR": str(root / "logs"),
                    "SA_FAILURE_ALERT_STATE_DIR": str(root / "state"),
                    "SA_FAILURE_ALERT_COOLDOWN_SECONDS": "21600",
                    "SA_LARK_APP_ID": "cli_test",
                    "SA_LARK_APP_SECRET": "secret",
                    "SA_LARK_USER_OPEN_ID": "ou_test",
                    "SA_REPO_ROOT": str(repo_root),
                    "SA_VENV_PYTHON": str(fake_python),
                    "SA_FAKE_PYTHON_CALLS": str(calls),
                }
            )

            for _ in range(2):
                subprocess.run(
                    [str(script), "stock-analyze-intelligence.service"],
                    cwd=repo_root,
                    env=env,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            notifications = calls.read_text(encoding="utf-8").splitlines()
            failure_log = (root / "logs" / "PIPELINE_FAILURES.log").read_text(encoding="utf-8")

        self.assertEqual(len(notifications), 1)
        self.assertIn("SUPPRESSED\tstock-analyze-intelligence.service", failure_log)


if __name__ == "__main__":
    unittest.main()
