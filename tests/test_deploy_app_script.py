from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DeployAppScriptTests(unittest.TestCase):
    def test_dashboard_service_runs_bounded_cache_warmer(self) -> None:
        service = Path(
            "deploy/systemd/stock-analyze-dashboard.service"
        ).read_text(encoding="utf-8")

        self.assertIn("ExecStartPost=", service)
        self.assertIn(
            "scripts/warm-dashboard-cache.py",
            service,
        )
        self.assertIn("--base-url http://127.0.0.1:8765", service)
        self.assertNotIn("/api/dashboard/intelligence.json", service)

    def test_build_script_creates_the_react_artifact(self) -> None:
        script = Path("scripts/build-dashboard-app.sh").read_text(encoding="utf-8")

        self.assertIn("npm ci", script)
        self.assertIn("npm run build", script)
        self.assertIn("npm audit --omit=dev", script)
        self.assertIn("reports/app/index.html", script)
        self.assertIn(
            "write_dashboard_permanent_portfolio_public_snapshot",
            script,
        )

    def test_legacy_deploy_entrypoint_delegates_to_dashboard_only_release(
        self,
    ) -> None:
        script = Path("scripts/deploy-app-to-ecs.sh").read_text(encoding="utf-8")

        self.assertIn("deploy-dashboard-workspaces-to-ecs.sh", script)
        self.assertIn('exec "$SCRIPT_DIR/', script)
        self.assertNotIn("rsync", script)
        self.assertNotIn("systemctl", script)
        self.assertNotIn("configs/", script)
        self.assertNotIn("deploy/systemd", script)

    def test_legacy_deploy_entrypoint_has_no_scheduler_side_effects(self) -> None:
        script = Path("scripts/deploy-app-to-ecs.sh").read_text(encoding="utf-8")

        forbidden = (
            "daemon-reload",
            "enable --now",
            "stock-analyze-intelligence",
            "stock-analyze-market-data",
            "stock-analyze-model-training",
            "cleanup-retired-runtime",
            "SA_SKIP_AGENT_CONFIG_SYNC",
        )
        for token in forbidden:
            self.assertNotIn(token, script)


class IntelligenceRuntimeInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = Path("scripts/install-intelligence-runtime.sh")

    def _run(
        self,
        bin_dir: Path,
        *,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": str(bin_dir),
            "SECRET_SENTINEL": "must-not-be-printed",
        }
        if extra_environment:
            environment.update(extra_environment)
        return subprocess.run(
            ["/bin/bash", str(self.script)],
            cwd=Path.cwd(),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _tesseract_script(
        *,
        include_chinese: bool = True,
    ) -> str:
        languages = "eng\\nchi_sim" if include_chinese else "eng"
        return f"""#!/bin/sh
if [ "${{1:-}}" = "--list-langs" ]; then
  printf 'List of available languages (2):\\n{languages}\\n'
  exit 0
fi
printf 'tesseract 5.3.0\\n'
"""

    @staticmethod
    def _package_manager_script(manager: str) -> str:
        return f"""#!/bin/sh
printf '{manager} %s\\n' "$*" >> "$PACKAGE_LOG"
case " $* " in
  *" install "*)
    /bin/cat > "$BIN_DIR/tesseract" <<'TESSERACT'
#!/bin/sh
if [ "${{1:-}}" = "--list-langs" ]; then
  printf 'List of available languages (2):\\neng\\nchi_sim\\n'
  exit 0
fi
printf 'tesseract 5.3.0\\n'
TESSERACT
    /bin/chmod +x "$BIN_DIR/tesseract"
    ;;
esac
"""

    def test_returns_immediately_when_required_languages_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bin_dir = Path(temporary_directory)
            _write_executable(
                bin_dir / "tesseract",
                self._tesseract_script(),
            )

            completed = self._run(bin_dir)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("tesseract_version=5.3.0", completed.stdout)
        self.assertIn(
            "tesseract_languages=chi_sim+eng",
            completed.stdout,
        )
        self.assertNotIn("must-not-be-printed", completed.stdout)
        self.assertNotIn("must-not-be-printed", completed.stderr)

    def test_installs_required_apt_packages_and_verifies_languages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            package_log = root / "packages.log"
            _write_executable(
                bin_dir / "apt-get",
                self._package_manager_script("apt-get"),
            )

            completed = self._run(
                bin_dir,
                extra_environment={
                    "BIN_DIR": str(bin_dir),
                    "PACKAGE_LOG": str(package_log),
                },
            )
            commands = package_log.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("apt-get update", commands)
        self.assertIn(
            "apt-get install -y tesseract-ocr tesseract-ocr-chi-sim",
            commands,
        )

    def test_installs_required_dnf_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            package_log = root / "packages.log"
            _write_executable(
                bin_dir / "dnf",
                self._package_manager_script("dnf"),
            )

            completed = self._run(
                bin_dir,
                extra_environment={
                    "BIN_DIR": str(bin_dir),
                    "PACKAGE_LOG": str(package_log),
                },
            )
            commands = package_log.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "dnf install -y tesseract tesseract-langpack-chi_sim",
            commands,
        )

    def test_installs_required_yum_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            package_log = root / "packages.log"
            _write_executable(
                bin_dir / "yum",
                self._package_manager_script("yum"),
            )

            completed = self._run(
                bin_dir,
                extra_environment={
                    "BIN_DIR": str(bin_dir),
                    "PACKAGE_LOG": str(package_log),
                },
            )
            commands = package_log.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "yum install -y tesseract tesseract-langpack-chi_sim",
            commands,
        )

    def test_unsupported_package_manager_exits_two_without_secret_leak(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            completed = self._run(Path(temporary_directory))

        self.assertEqual(completed.returncode, 2)
        self.assertIn("unsupported_package_manager", completed.stderr)
        self.assertNotIn("must-not-be-printed", completed.stdout)
        self.assertNotIn("must-not-be-printed", completed.stderr)

    def test_installer_is_executable(self) -> None:
        mode = self.script.stat().st_mode

        self.assertTrue(mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
