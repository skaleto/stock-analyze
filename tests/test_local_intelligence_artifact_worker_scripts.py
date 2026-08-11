from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "scripts" / "run-local-intelligence-artifact-worker.sh"
INSTALLER = (
    REPO_ROOT
    / "scripts"
    / "install-local-intelligence-artifact-worker-launchd.sh"
)
PLIST = (
    REPO_ROOT
    / "deploy"
    / "launchd"
    / "com.stock-analyze.local-intelligence-artifact-worker.plist"
)
DOC = REPO_ROOT / "docs" / "local-intelligence-artifact-worker.md"


class LocalIntelligenceArtifactWorkerScriptTests(unittest.TestCase):
    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _make_fixture(
        self,
        root: Path,
        *,
        local_exit: int = 0,
        local_delay: float = 0,
        import_status: str = "imported",
        power_source: str = "AC Power",
        ocr_languages: str = "eng\nchi_sim",
    ) -> tuple[dict[str, str], Path, Path, Path]:
        bin_dir = root / "fake bin"
        bin_dir.mkdir()
        ssh_log = root / "ssh calls.jsonl"
        rsync_log = root / "rsync calls.jsonl"
        python_log = root / "python calls.jsonl"

        self._write_executable(
            bin_dir / "ssh",
            """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_SSH_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
command = sys.argv[-1]
if not command.startswith("cd "):
    raise SystemExit(92)
if "intelligence-artifact-job-export" in command:
    print("remote diagnostic")
    print(json.dumps({
        "job_id": "job 42",
        "job_dir": "/opt/stock analyze/data/shared/intelligence/artifact_jobs/job 42",
    }))
    sys.stdout.flush()
    print(os.environ["SECRET_SENTINEL"], file=sys.stderr, flush=True)
elif "intelligence-artifact-job-import" in command:
    print(json.dumps({
        "status": os.environ["FAKE_IMPORT_STATUS"],
        "job_id": "job 42",
    }))
else:
    raise SystemExit(91)
""",
        )
        self._write_executable(
            bin_dir / "rsync",
            """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_RSYNC_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
""",
        )
        self._write_executable(
            bin_dir / "local-python",
            f"""#!/usr/bin/env python3
import json
import os
import sys
import time

if len(sys.argv) > 1 and sys.argv[1] == "-c":
    raise SystemExit(0)
if os.getcwd() != os.environ["EXPECTED_LOCAL_ROOT"]:
    raise SystemExit(23)
time.sleep({local_delay})
with open(os.environ["FAKE_PYTHON_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
raise SystemExit({local_exit})
""",
        )
        self._write_executable(
            bin_dir / "tesseract",
            f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "--list-langs" ]]; then
  printf '%s\\n' 'List of available languages:' '{ocr_languages}'
  exit 0
fi
exit 0
""",
        )
        self._write_executable(
            bin_dir / "pmset",
            f"""#!/usr/bin/env bash
printf "Now drawing from '{power_source}'\\n"
""",
        )
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "FAKE_SSH_LOG": str(ssh_log),
                "FAKE_RSYNC_LOG": str(rsync_log),
                "FAKE_PYTHON_LOG": str(python_log),
                "SA_LOCAL_INTELLIGENCE_PYTHON": str(bin_dir / "local-python"),
                "SA_REMOTE_INTELLIGENCE_PYTHON": "/remote venv/bin/python",
                "SA_REMOTE_INTELLIGENCE_ROOT": "/opt/stock analyze",
                "FAKE_IMPORT_STATUS": import_status,
                "SECRET_SENTINEL": "must-not-appear",
            }
        )
        return env, ssh_log, rsync_log, python_log

    def _run(
        self,
        local_root: Path,
        env: dict[str, str],
        *extra_args: str,
    ) -> subprocess.CompletedProcess[str]:
        key_path = local_root.parent / "keys with spaces" / "worker key"
        key_path.parent.mkdir(exist_ok=True)
        key_path.write_text("must-not-appear", encoding="utf-8")
        invocation_env = dict(env)
        invocation_env["EXPECTED_LOCAL_ROOT"] = str(local_root.resolve())
        return subprocess.run(
            [
                str(WORKER),
                "--local-root",
                str(local_root),
                "--remote",
                "worker@example.test",
                "--ssh-key",
                str(key_path),
                "--once",
                *extra_args,
            ],
            cwd=REPO_ROOT,
            env=invocation_env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    @staticmethod
    def _read_calls(path: Path) -> list[list[str]]:
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def test_once_runs_export_pull_local_run_push_and_import_with_space_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "local repo with spaces"
            local_root.mkdir()
            env, ssh_log, rsync_log, python_log = self._make_fixture(root)

            result = self._run(
                local_root,
                env,
                "--allow-battery",
                "--stage",
                "parse",
                "--limit",
                "7",
                "--workers",
                "3",
            )

            ssh_calls = self._read_calls(ssh_log)
            rsync_calls = self._read_calls(rsync_log)
            python_calls = self._read_calls(python_log)
            local_job_dir = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "jobs"
                / "job 42"
            )
            job_dir_cleaned = not local_job_dir.exists()
            receipt = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "history"
                / "job 42.import.json"
            )
            receipt_payload = (
                receipt.read_text(encoding="utf-8")
                if receipt.is_file()
                else ""
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(ssh_calls), 2)
        self.assertIn("intelligence-artifact-job-export", ssh_calls[0][-1])
        self.assertTrue(ssh_calls[0][-1].startswith("cd "))
        self.assertIn(
            ". /etc/stock-analyze/secrets.env",
            ssh_calls[0][-1],
        )
        self.assertIn(
            "/run/stock-analyze-intelligence-reconcile.lock",
            ssh_calls[0][-1],
        )
        self.assertIn("--conflict-exit-code 75", ssh_calls[0][-1])
        self.assertIn("--stage parse", ssh_calls[0][-1])
        self.assertIn("--limit 7", ssh_calls[0][-1])
        self.assertIn("--worker-id", ssh_calls[0][-1])
        self.assertIn("--lease-seconds", ssh_calls[0][-1])
        self.assertIn("intelligence-artifact-job-import", ssh_calls[1][-1])
        self.assertIn("--wait 1800", ssh_calls[1][-1])
        self.assertIn("/opt/stock", ssh_calls[1][-1])
        self.assertEqual(len(rsync_calls), 2)
        self.assertNotIn("--protect-args", rsync_calls[0])
        self.assertEqual(
            rsync_calls[0][-2],
            r"worker@example.test:/opt/stock\ analyze/data/shared/intelligence/artifact_jobs/job\ 42/",
        )
        self.assertEqual(rsync_calls[0][-1], f"{local_job_dir}/")
        self.assertIn("--relative", rsync_calls[1])
        self.assertIn("result.jsonl", rsync_calls[1])
        self.assertIn("run_report.json", rsync_calls[1])
        self.assertIn("outputs/", rsync_calls[1])
        self.assertNotIn(f"{local_job_dir}/", rsync_calls[1])
        self.assertEqual(
            rsync_calls[1][-1],
            r"worker@example.test:/opt/stock\ analyze/data/shared/intelligence/artifact_jobs/job\ 42/",
        )
        self.assertEqual(
            python_calls,
            [
                [
                    "-m",
                    "stock_analyze.cli",
                    "intelligence-artifact-job-run",
                    "--repo-root",
                    str(local_root),
                    "--job-dir",
                    str(local_job_dir),
                    "--workers",
                    "3",
                ]
            ],
        )
        self.assertNotIn("must-not-appear", result.stdout + result.stderr)
        self.assertTrue(job_dir_cleaned)
        self.assertIn('"status": "imported"', receipt_payload)

    def test_local_failure_retains_job_and_does_not_push_or_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            local_root.mkdir()
            env, ssh_log, rsync_log, _ = self._make_fixture(
                root, local_exit=17
            )

            result = self._run(local_root, env, "--allow-battery")
            local_job_dir = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "jobs"
                / "job 42"
            )
            job_was_retained = local_job_dir.is_dir()
            ssh_calls = self._read_calls(ssh_log)
            rsync_calls = self._read_calls(rsync_log)

        self.assertEqual(result.returncode, 17)
        self.assertTrue(job_was_retained)
        self.assertEqual(len(ssh_calls), 1)
        self.assertEqual(len(rsync_calls), 1)
        self.assertIn("retained", result.stderr.lower())

    def test_local_timeout_returns_124_and_retains_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            local_root.mkdir()
            env, ssh_log, rsync_log, _ = self._make_fixture(
                root,
                local_delay=2,
            )

            result = self._run(
                local_root,
                env,
                "--allow-battery",
                "--job-timeout-seconds",
                "1",
            )
            job_retained = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "jobs"
                / "job 42"
            ).is_dir()
            ssh_call_count = len(self._read_calls(ssh_log))
            rsync_call_count = len(self._read_calls(rsync_log))

        self.assertEqual(result.returncode, 124)
        self.assertTrue(job_retained)
        self.assertEqual(ssh_call_count, 1)
        self.assertEqual(rsync_call_count, 1)

    def test_existing_lock_skips_without_contacting_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            lock_dir = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "worker.lock"
            )
            lock_dir.mkdir(parents=True)
            (lock_dir / "pid").write_text(
                str(os.getpid()),
                encoding="utf-8",
            )
            (lock_dir / "host").write_text(
                subprocess.check_output(
                    ["hostname", "-s"],
                    text=True,
                ).strip(),
                encoding="utf-8",
            )
            env, ssh_log, _, _ = self._make_fixture(root)

            result = self._run(local_root, env, "--allow-battery")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ssh_log.exists())
        self.assertIn("already running", result.stdout.lower())

    def test_stale_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            lock_dir = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "worker.lock"
            )
            lock_dir.mkdir(parents=True)
            (lock_dir / "pid").write_text("99999999", encoding="utf-8")
            (lock_dir / "host").write_text(
                subprocess.check_output(
                    ["hostname", "-s"],
                    text=True,
                ).strip(),
                encoding="utf-8",
            )
            env, ssh_log, _, _ = self._make_fixture(root)

            result = self._run(local_root, env, "--allow-battery")
            ssh_call_count = len(self._read_calls(ssh_log))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ssh_call_count, 2)

    def test_partial_import_stops_and_retains_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            local_root.mkdir()
            env, ssh_log, _, _ = self._make_fixture(
                root,
                import_status="partial",
            )

            result = self._run(local_root, env, "--allow-battery")
            job_dir = (
                local_root
                / ".local-intelligence-artifact-worker"
                / "jobs"
                / "job 42"
            )
            job_retained = job_dir.is_dir()
            ssh_call_count = len(self._read_calls(ssh_log))

        self.assertEqual(result.returncode, 3)
        self.assertTrue(job_retained)
        self.assertEqual(ssh_call_count, 2)
        self.assertIn("stopping before retry", result.stderr)

    def test_battery_power_skips_unless_explicitly_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            local_root.mkdir()
            env, ssh_log, _, _ = self._make_fixture(
                root, power_source="Battery Power"
            )

            result = self._run(local_root, env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ssh_log.exists())
        self.assertIn("battery", result.stdout.lower())

    def test_parse_refuses_to_claim_work_without_chinese_ocr_language(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local_root = root / "repo"
            local_root.mkdir()
            env, ssh_log, _, _ = self._make_fixture(
                root,
                ocr_languages="eng",
            )

            result = self._run(
                local_root,
                env,
                "--allow-battery",
                "--stage",
                "parse",
            )

        self.assertEqual(result.returncode, 2)
        self.assertFalse(ssh_log.exists())
        self.assertIn("chi_sim", result.stderr)

    def test_launchd_runs_once_every_thirty_minutes_without_catch_up(self) -> None:
        with PLIST.open("rb") as handle:
            config = plistlib.load(handle)

        self.assertEqual(
            config["Label"],
            "com.stock-analyze.local-intelligence-artifact-worker",
        )
        self.assertEqual(config["StartInterval"], 1800)
        self.assertFalse(config["RunAtLoad"])
        self.assertNotIn("StartCalendarInterval", config)
        self.assertNotIn("KeepAlive", config)
        self.assertIn("--once", config["ProgramArguments"])
        self.assertIn("parse", config["ProgramArguments"])
        self.assertIn("WorkingDirectory", config)
        self.assertIn("EnvironmentVariables", config)

    def test_installer_and_documentation_keep_ecs_as_database_authority(
        self,
    ) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        documentation = DOC.read_text(encoding="utf-8")

        self.assertIn("plistlib", installer)
        self.assertIn("launchctl bootstrap", installer)
        self.assertIn("ECS", documentation)
        self.assertIn("权威数据库", documentation)
        self.assertIn("不直接写", documentation)
        self.assertIn("--allow-battery", documentation)
        self.assertIn("失败", documentation)
        for option in (
            "--stage",
            "--limit",
            "--workers",
            "--max-jobs",
            "--max-runtime-seconds",
            "--job-timeout-seconds",
            "--remote",
            "--ssh-key",
            "--local-root",
            "--once",
        ):
            self.assertIn(option, documentation)

    def test_installer_preserves_space_paths_in_launchd_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home with spaces"
            local_root = root / "repo with spaces"
            key_path = root / "keys with spaces" / "worker key"
            fake_bin = root / "fake bin"
            home.mkdir()
            local_root.mkdir()
            key_path.parent.mkdir()
            key_path.write_text("key material", encoding="utf-8")
            fake_bin.mkdir()
            launchctl_log = root / "launchctl.jsonl"
            self._write_executable(
                fake_bin / "launchctl",
                """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_LAUNCHCTL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
""",
            )
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "FAKE_LAUNCHCTL_LOG": str(launchctl_log),
                }
            )

            result = subprocess.run(
                [
                    str(INSTALLER),
                    "--local-root",
                    str(local_root),
                    "--ssh-key",
                    str(key_path),
                    "--remote",
                    "worker@example.test",
                ],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            target = (
                home
                / "Library"
                / "LaunchAgents"
                / "com.stock-analyze.local-intelligence-artifact-worker.plist"
            )
            with target.open("rb") as handle:
                config = plistlib.load(handle)
            calls = self._read_calls(launchctl_log)

        arguments = config["ProgramArguments"]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments[arguments.index("--local-root") + 1], str(local_root))
        self.assertEqual(arguments[arguments.index("--ssh-key") + 1], str(key_path))
        self.assertEqual(config["WorkingDirectory"], str(local_root))
        self.assertTrue(
            Path(
                config["EnvironmentVariables"][
                    "SA_LOCAL_INTELLIGENCE_PYTHON"
                ]
            ).is_absolute()
        )
        self.assertIn("/usr/bin", config["EnvironmentVariables"]["PATH"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], "bootstrap")


if __name__ == "__main__":
    unittest.main()
