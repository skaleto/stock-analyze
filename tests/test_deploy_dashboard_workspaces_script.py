from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-dashboard-workspaces-to-ecs.sh"
LEGACY_SCRIPT = ROOT / "scripts" / "deploy-app-to-ecs.sh"

EXPECTED_FILES = [
    "configs/research/paper_candidate_gate_v1.yaml",
    "configs/research/production_paper_challengers_v1.yaml",
    "configs/research/qdii_global_context_v1.yaml",
    "configs/research/scenario_model_v1.yaml",
    "stock_analyze/cli.py",
    "stock_analyze/intelligence/factors.py",
    "stock_analyze/dashboard_aggregator.py",
    "stock_analyze/dashboard_api.py",
    "stock_analyze/dashboard_finance.py",
    "stock_analyze/dashboard_http.py",
    "stock_analyze/dashboard_workspace_api.py",
    "stock_analyze/dashboard_runtime.py",
    "stock_analyze/model_iteration.py",
    "stock_analyze/model_shadow.py",
    "stock_analyze/markets/_settlement_simulator.py",
    "stock_analyze/markets/cn_qdii_etf/mechanics.py",
    "stock_analyze/markets/cn_qdii_etf/data_provider.py",
    "stock_analyze/research/activation.py",
    "stock_analyze/research/classical_specs.py",
    "stock_analyze/research/local_training.py",
    "stock_analyze/research/models.py",
    "stock_analyze/research/pipeline.py",
    "stock_analyze/research/portfolio_replay.py",
    "stock_analyze/research/paper_candidate_gate.py",
    "stock_analyze/research/paper_candidate_runtime.py",
    "stock_analyze/research/qdii_global_context.py",
    "stock_analyze/research/scenario_model.py",
    "stock_analyze/research/shadow_admission.py",
    "stock_analyze/research/storage.py",
    "stock_analyze/research/strategy_campaign.py",
    "stock_analyze/research/account_features.py",
    "stock_analyze/research/technical_features.py",
    "tests/test_cli_dashboard_routes.py",
    "tests/test_cli_research.py",
    "tests/test_dashboard_model_shadow.py",
    "tests/test_dashboard_finance.py",
    "tests/test_dashboard_http.py",
    "tests/test_dashboard_resource_api.py",
    "tests/test_dashboard_runtime.py",
    "tests/test_dashboard_workspace_api.py",
    "tests/test_model_iteration.py",
    "tests/test_model_shadow.py",
    "tests/test_markets_cn_qdii_etf_simulator.py",
    "tests/test_research_activation.py",
    "tests/test_research_classical_specs.py",
    "tests/test_research_local_training.py",
    "tests/test_research_models.py",
    "tests/test_research_pipeline.py",
    "tests/test_research_portfolio_replay.py",
    "tests/test_research_paper_candidate_gate.py",
    "tests/test_research_paper_candidate_runtime.py",
    "tests/test_research_qdii_global_context.py",
    "tests/test_research_collectors.py",
    "tests/test_research_source_features.py",
    "tests/test_research_scenario_model.py",
    "tests/test_research_shadow_admission.py",
    "tests/test_research_storage.py",
    "tests/test_research_strategy_campaign.py",
    "tests/test_intelligence_factors.py",
    "tests/test_research_account_features.py",
    "tests/test_research_tabular_forward.py",
    "scripts/system-audit.sh",
    "scripts/check-ecs-timers.sh",
    "docs/system-harness.md",
    "docs/system-overview.md",
]
DEPLOY_VERSION_FILE = "DEPLOY_VERSION"
EXPECTED_PREIMAGE_FILES = [DEPLOY_VERSION_FILE, *EXPECTED_FILES]


class DashboardWorkspaceDeployScriptTests(unittest.TestCase):
    def _run(
        self,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(DEPLOY_SCRIPT), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _write_executable(path: Path, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _create_deploy_fixture(self, temporary_directory: str) -> dict[str, object]:
        root = Path(temporary_directory) / "release-repo"
        remote_app = Path(temporary_directory) / "remote-app"
        release_root = Path(temporary_directory) / "remote-releases"
        fake_bin = Path(temporary_directory) / "fake-bin"
        root.mkdir()
        remote_app.mkdir()
        release_root.mkdir()
        fake_bin.mkdir()

        (remote_app / DEPLOY_VERSION_FILE).write_text(
            "preimage-commit\n",
            encoding="utf-8",
        )

        deploy_script = root / "scripts" / DEPLOY_SCRIPT.name
        deploy_script.parent.mkdir(parents=True)
        shutil.copy2(DEPLOY_SCRIPT, deploy_script)

        for relative in EXPECTED_FILES:
            local_file = root / relative
            local_file.parent.mkdir(parents=True, exist_ok=True)
            local_file.write_text(f"release:{relative}\n", encoding="utf-8")

            remote_file = remote_app / relative
            remote_file.parent.mkdir(parents=True, exist_ok=True)
            remote_file.write_text(f"preimage:{relative}\n", encoding="utf-8")

        local_assets = root / "reports" / "app"
        local_assets.mkdir(parents=True)
        (local_assets / "index.html").write_text("release-app\n", encoding="utf-8")
        (local_assets / "asset.js").write_text("release-asset\n", encoding="utf-8")
        frontend_package = root / "frontend" / "dashboard" / "package.json"
        frontend_package.parent.mkdir(parents=True)
        frontend_package.write_text('{"private": true}\n', encoding="utf-8")
        remote_assets = remote_app / "reports" / "app"
        remote_assets.mkdir(parents=True)
        (remote_assets / "index.html").write_text("preimage-app\n", encoding="utf-8")
        (remote_assets / "asset.js").write_text("preimage-asset\n", encoding="utf-8")

        self._write_executable(
            root / "scripts" / "build-dashboard-app.sh",
            (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "echo 'fixture build log'\n"
                "test -f reports/app/index.html\n"
            ),
        )
        self._write_executable(
            fake_bin / "ssh",
            "#!/usr/bin/env bash\nset -euo pipefail\nshift\nexec \"$@\"\n",
        )
        self._write_executable(
            fake_bin / "systemctl",
            "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
        )
        self._write_executable(
            fake_bin / "curl",
            """#!/usr/bin/env bash
set -euo pipefail
if [[ " $* " == *" --write-out "* ]]; then
  printf '200\\t128\\t0.010'
fi
""",
        )
        remote_python = fake_bin / "remote-python"
        self._write_executable(
            remote_python,
            """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" ]]; then
  exit "${FAKE_REMOTE_TEST_EXIT:-0}"
fi
exec python3 "$@"
""",
        )
        self._write_executable(
            fake_bin / "rsync",
            """#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def local_path(value: str) -> Path:
    if ":" in value and not value.startswith("/"):
        value = value.split(":", 1)[1]
    return Path(value.rstrip("/") or "/")


state_path = Path(os.environ["FAKE_RSYNC_STATE"])
count = int(state_path.read_text(encoding="utf-8") or "0") if state_path.exists() else 0
count += 1
state_path.write_text(str(count), encoding="utf-8")
fail_on = int(os.environ.get("FAKE_RSYNC_FAIL_ON", "0"))
if fail_on and count == fail_on:
    raise SystemExit(23)

arguments = sys.argv[1:]
relative_mode = "--relative" in arguments
delete_mode = "--delete" in arguments
paths = [argument for argument in arguments if not argument.startswith("-")]
sources = paths[:-1]
destination = local_path(paths[-1])

if relative_mode:
    destination.mkdir(parents=True, exist_ok=True)
    for source_value in sources:
        source = Path(source_value)
        target = destination / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
elif len(sources) == 1 and Path(sources[0].rstrip("/")).is_dir():
    source = Path(sources[0].rstrip("/"))
    if delete_mode and destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            shutil.copy2(child, target)
else:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sources[0]), destination)
""",
        )

        subprocess.run(
            ["git", "init", "-q"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "dashboard-tests@example.test"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Dashboard Tests"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "fixture release"],
            cwd=root,
            check=True,
        )

        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        environment["SA_ECS_REMOTE"] = (
            f"operator@example.test:{remote_app.as_posix()}"
        )
        environment["SA_DASHBOARD_RELEASES_DIR"] = release_root.as_posix()
        environment["SA_ECS_PYTHON"] = remote_python.as_posix()
        environment["SA_DASHBOARD_RELEASE_STAMP"] = "reviewed-test"
        environment["SA_DASHBOARD_CANARY_BASE_URL"] = "http://127.0.0.1:8765"
        environment["FAKE_RSYNC_STATE"] = (
            Path(temporary_directory) / "rsync-count"
        ).as_posix()

        return {
            "root": root,
            "script": deploy_script,
            "remote_app": remote_app,
            "release_root": release_root,
            "environment": environment,
        }

    @staticmethod
    def _run_fixture(
        fixture: dict[str, object],
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(fixture["script"]), *arguments],
            cwd=fixture["root"],
            env=environment or fixture["environment"],
            text=True,
            capture_output=True,
            check=False,
        )

    def _capture_review_manifests(
        self,
        fixture: dict[str, object],
        temporary_directory: str,
    ) -> tuple[Path, Path]:
        environment = dict(fixture["environment"])
        release_manifest = Path(temporary_directory) / "reviewed-release.manifest"
        preimage_manifest = Path(temporary_directory) / "reviewed-preimage.manifest"

        release = self._run_fixture(
            fixture,
            "capture-release-input",
            environment=environment,
        )
        self.assertEqual(release.returncode, 0, release.stderr)
        release_manifest.write_text(release.stdout, encoding="utf-8")

        preimage = self._run_fixture(
            fixture,
            "capture-preimage",
            environment=environment,
        )
        self.assertEqual(preimage.returncode, 0, preimage.stderr)
        preimage_manifest.write_text(preimage.stdout, encoding="utf-8")
        return preimage_manifest, release_manifest

    def test_legacy_entrypoint_only_delegates_to_dashboard_deployer(self) -> None:
        source = LEGACY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("deploy-dashboard-workspaces-to-ecs.sh", source)
        self.assertIn("exec", source)
        for forbidden in (
            "rsync",
            "systemctl",
            "cleanup-retired-runtime",
            "configs/",
            "deploy/",
        ):
            self.assertNotIn(forbidden, source)

    def test_sync_allowlist_is_exact_and_reports_app_is_separate(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        match = re.search(
            r"readonly DASHBOARD_FILES=\(\n(?P<body>.*?)\n\)",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        paths = re.findall(r'^\s+"([^"]+)"$', match.group("body"), re.MULTILINE)

        self.assertEqual(paths, EXPECTED_FILES)
        self.assertIn('readonly DASHBOARD_ASSET_TREE="reports/app"', source)

    def test_remote_configuration_rejects_shell_metacharacters_and_overlap(
        self,
    ) -> None:
        safe_environment = os.environ.copy()
        safe_environment.update(
            {
                "SA_ECS_REMOTE": "operator@example.test:/opt/stock-analyze/app",
                "SA_DASHBOARD_RELEASES_DIR": "/opt/stock-analyze/releases",
                "SA_ECS_PYTHON": "/opt/stock-analyze/venv/bin/python",
                "SA_DASHBOARD_CANARY_BASE_URL": "http://127.0.0.1:8765",
            }
        )
        dangerous_cases = (
            ("SA_ECS_REMOTE", "operator@example.test;touch:/opt/app", "host"),
            ("SA_ECS_REMOTE_PATH", "/opt/app;touch", "REMOTE_PATH"),
            (
                "SA_DASHBOARD_RELEASES_DIR",
                "/opt/stock-analyze/app/reports/app/releases",
                "outside",
            ),
            (
                "SA_DASHBOARD_RELEASES_DIR",
                "/opt/stock-analyze",
                "overlap",
            ),
            ("SA_ECS_PYTHON", "/opt/venv/bin/python;touch", "PYTHON"),
            (
                "SA_DASHBOARD_CANARY_BASE_URL",
                "http://127.0.0.1:8765;touch",
                "CANARY",
            ),
        )

        accepted = self._run("validate-config", environment=safe_environment)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        for name, value, expected_error in dangerous_cases:
            with self.subTest(name=name, value=value):
                environment = safe_environment.copy()
                environment[name] = value
                rejected = self._run("validate-config", environment=environment)
                self.assertEqual(rejected.returncode, 2)
                self.assertIn(expected_error.lower(), rejected.stderr.lower())

    def test_deploy_requires_reviewed_release_manifest_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            preimage = Path(temporary_directory) / "preimage.manifest"
            lines = [
                f"FILE MISSING {path}" for path in EXPECTED_PREIMAGE_FILES
            ]
            lines.append("TREE MISSING reports/app")
            preimage.write_text("\n".join(lines) + "\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["SA_ECS_REMOTE"] = "operator@example.test:/opt/app"
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_MAX_BYTES"] = "invalid"
            environment.pop("SA_DASHBOARD_RELEASE_INPUT_MANIFEST", None)

            completed = self._run("deploy", environment=environment)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("SA_DASHBOARD_RELEASE_INPUT_MANIFEST", completed.stderr)

    def test_reviewed_release_manifest_is_bound_to_clean_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)
            overview = Path(fixture["root"]) / "docs" / "system-overview.md"
            overview.write_text("unreviewed change\n", encoding="utf-8")

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertRegex(
            completed.stderr,
            r"(release input mismatch|release inputs must match reviewed commit)",
        )

    def test_generated_asset_tree_does_not_need_to_be_tracked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            root = Path(fixture["root"])
            subprocess.run(
                ["git", "rm", "--cached", "-qr", "reports/app"],
                cwd=root,
                check=True,
            )
            (root / ".gitignore").write_text("/reports/app/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "treat dashboard assets as generated"],
                cwd=root,
                check=True,
            )

            completed = self._run_fixture(
                fixture,
                "capture-release-input",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("TREE ", completed.stdout)
        self.assertIn(" reports/app", completed.stdout)

    def test_capture_release_stdout_contains_only_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)

            completed = self._run_fixture(
                fixture,
                "capture-release-input",
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.startswith("FORMAT "))
        self.assertNotIn("fixture build log", completed.stdout)
        self.assertIn("fixture build log", completed.stderr)

    def test_successful_release_writes_reviewed_commit_marker_and_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            root = Path(fixture["root"])
            expected_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

            backup = Path(fixture["release_root"]) / (
                "reviewed-test-dashboard-workspaces"
            )
            deployed_version = (
                Path(fixture["remote_app"]) / DEPLOY_VERSION_FILE
            ).read_text(encoding="utf-8")
            backed_up_version = (
                backup / "root" / DEPLOY_VERSION_FILE
            ).read_text(encoding="utf-8")
            release_result = (backup / "release-manifest.txt").read_text(
                encoding="utf-8"
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(deployed_version, f"{expected_commit}\n")
        self.assertEqual(backed_up_version, "preimage-commit\n")
        self.assertIn(f"commit={expected_commit}", release_result)
        self.assertIn("deploy_version_file=DEPLOY_VERSION", release_result)
        self.assertRegex(
            release_result,
            r"deploy_version_sha256=[0-9a-f]{64}",
        )

    def test_asset_sync_failure_rolls_back_and_verifies_preimage_and_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)
            environment["FAKE_RSYNC_FAIL_ON"] = "4"

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

            backup = Path(fixture["release_root"]) / (
                "reviewed-test-dashboard-workspaces"
            )
            release_result = (backup / "release-manifest.txt").read_text(
                encoding="utf-8"
            )
            rollback_result = (backup / "rollback-result.txt").read_text(
                encoding="utf-8"
            )
            remote_cli = (
                Path(fixture["remote_app"]) / "stock_analyze" / "cli.py"
            ).read_text(encoding="utf-8")
            remote_app = (
                Path(fixture["remote_app"]) / "reports" / "app" / "index.html"
            ).read_text(encoding="utf-8")
            remote_version = (
                Path(fixture["remote_app"]) / DEPLOY_VERSION_FILE
            ).read_text(encoding="utf-8")
            lock = Path(fixture["release_root"]) / (
                ".dashboard-workspaces-deploy.lock"
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("status=rolled_back", release_result)
        self.assertIn("preimage_status=verified", rollback_result)
        self.assertIn("service_status=active", rollback_result)
        self.assertIn("app_canary_status=passed", rollback_result)
        self.assertEqual(remote_cli, "preimage:stock_analyze/cli.py\n")
        self.assertEqual(remote_app, "preimage-app\n")
        self.assertEqual(remote_version, "preimage-commit\n")
        self.assertFalse(lock.exists())

    def test_remote_test_failure_rolls_back_and_records_verified_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)
            environment["FAKE_REMOTE_TEST_EXIT"] = "9"

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

            backup = Path(fixture["release_root"]) / (
                "reviewed-test-dashboard-workspaces"
            )
            rollback_result = (backup / "rollback-result.txt").read_text(
                encoding="utf-8"
            )
            remote_overview = (
                Path(fixture["remote_app"]) / "docs" / "system-overview.md"
            ).read_text(encoding="utf-8")
            remote_version = (
                Path(fixture["remote_app"]) / DEPLOY_VERSION_FILE
            ).read_text(encoding="utf-8")
            lock = Path(fixture["release_root"]) / (
                ".dashboard-workspaces-deploy.lock"
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("rollback_status=verified", rollback_result)
        self.assertIn("preimage_status=verified", rollback_result)
        self.assertIn("app_canary_status=passed", rollback_result)
        self.assertEqual(remote_overview, "preimage:docs/system-overview.md\n")
        self.assertEqual(remote_version, "preimage-commit\n")
        self.assertFalse(lock.exists())

    def test_existing_remote_lock_blocks_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            lock = Path(fixture["release_root"]) / (
                ".dashboard-workspaces-deploy.lock"
            )
            lock.mkdir()
            (lock / "holder").write_text("another-release\n", encoding="utf-8")
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

            backup = Path(fixture["release_root"]) / (
                "reviewed-test-dashboard-workspaces"
            )
            remote_cli = (
                Path(fixture["remote_app"]) / "stock_analyze" / "cli.py"
            ).read_text(encoding="utf-8")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("lock is already held", completed.stderr)
        self.assertFalse(backup.exists())
        self.assertEqual(remote_cli, "preimage:stock_analyze/cli.py\n")

    def test_remote_resolved_release_path_cannot_reenter_app_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = self._create_deploy_fixture(temporary_directory)
            unsafe_target = (
                Path(fixture["remote_app"]) / "reports" / "app" / "releases"
            )
            unsafe_target.mkdir()
            release_link = Path(temporary_directory) / "release-link"
            release_link.symlink_to(unsafe_target, target_is_directory=True)
            environment = dict(fixture["environment"])
            environment["SA_DASHBOARD_RELEASES_DIR"] = str(release_link)
            fixture["environment"] = environment
            preimage, release = self._capture_review_manifests(
                fixture,
                temporary_directory,
            )
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(preimage)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(release)

            completed = self._run_fixture(
                fixture,
                "deploy",
                environment=environment,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("resolved release path overlaps app", completed.stderr)

    def test_script_has_no_broad_deploy_or_scheduler_side_effects(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        for forbidden in (
            "./configs/",
            "./deploy/",
            "./stock_analyze/",
            "./tests/",
            "cleanup-retired-runtime",
            "systemctl enable",
            "systemctl disable",
            "systemctl start",
            "systemctl stop",
            "systemctl daemon-reload",
            "pip install",
            "--delete data/",
        ):
            self.assertNotIn(forbidden, source)

        units = set(
            re.findall(
                r"stock-analyze-[a-z0-9@.-]+\.(?:service|timer)",
                source,
            )
        )
        self.assertEqual(units, {"stock-analyze-dashboard.service"})

    def test_release_order_and_audit_artifacts_are_explicit(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        main = re.search(
            r"main\(\) \{\n(?P<body>.*?)\n\}\n\nmain ",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(main)
        body = main.group("body")
        ordered_steps = [
            "validate_local_contract",
            "build_dashboard_assets",
            "prepare_remote_backup",
            "sync_dashboard_release",
            "verify_remote_release",
            "write_remote_manifest",
        ]
        positions = [body.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))

        for artifact in (
            "expected-preimage.manifest",
            "actual-preimage.manifest",
            "preexisting-files.txt",
            "missing-files.txt",
            "canary-results.tsv",
            "release-manifest.txt",
        ):
            self.assertIn(artifact, source)
        self.assertIn("systemctl restart", source)
        self.assertIn("systemctl is-active --quiet", source)

    def test_control_manifests_are_uploaded_to_the_release_directory(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            '"$REMOTE_HOST:$BACKUP_DIR/expected-preimage.manifest"',
            source,
        )
        self.assertIn(
            '"$REMOTE_HOST:$BACKUP_DIR/release-input.manifest"',
            source,
        )
        self.assertNotIn('"$REMOTE_TARGET/$BACKUP_DIR/', source)

    def test_preimage_manifest_is_snapshotted_before_the_build(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            '"$LOCAL_RELEASE_DIR/expected-preimage.manifest"',
            source,
        )
        self.assertIn(
            'cp -- "$SA_DASHBOARD_PREIMAGE_MANIFEST" "$PREIMAGE_MANIFEST"',
            source,
        )
        main = re.search(
            r"main\(\) \{\n(?P<body>.*?)\n\}\n\nmain ",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(main)
        body = main.group("body")
        self.assertLess(body.index("trap handle_exit EXIT"), body.index(
            "validate_local_contract"
        ))
        self.assertLess(
            body.index("validate_local_contract"),
            body.index("build_dashboard_assets"),
        )

    def test_canaries_enforce_status_size_and_warm_latency(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        for endpoint in (
            "/api/dashboard/system-overview.json",
            "/api/dashboard/model-research.json?market=a_share",
            "/api/dashboard/data-intelligence.json?market=a_share",
            "/api/dashboard/operations-center.json?scope=all",
            "/app.html?view=system",
        ):
            self.assertIn(endpoint, source)
        self.assertIn("SA_DASHBOARD_MAX_BYTES", source)
        self.assertIn("SA_DASHBOARD_MAX_TTFB_SECONDS", source)
        self.assertIn("%{http_code}", source)
        self.assertIn("%{size_download}", source)
        self.assertIn("%{time_starttransfer}", source)
        self.assertIn("--retry-connrefused", source)
        self.assertIn("--max-time 120", source)

    def test_preimage_capture_is_a_separate_read_only_command(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "deploy-dashboard-workspaces-to-ecs.sh capture-preimage",
            source,
        )
        capture = re.search(
            r"capture_remote_preimage\(\) \{\n(?P<body>.*?)\n\}",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(capture)
        body = capture.group("body")
        self.assertIn("sha256sum", body)
        self.assertIn('printf \'FILE %s %s\\n\'', body)
        self.assertIn('printf \'TREE %s %s\\n\'', body)
        for forbidden in (
            "rsync",
            "mkdir",
            "cp ",
            "rm ",
            "systemctl",
        ):
            self.assertNotIn(forbidden, body)

    def test_capture_preimage_executes_remote_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            bin_dir = Path(temporary_directory)
            ssh = bin_dir / "ssh"
            ssh.write_text("#!/bin/sh\nprintf 'capture-ok\\n'\n", encoding="utf-8")
            ssh.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
            environment["SA_ECS_REMOTE"] = "operator@example:/opt/app"

            completed = self._run(
                "capture-preimage",
                environment=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "capture-ok\n")

    def test_deploy_configuration_reaches_numeric_gate_without_building(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "preimage.manifest"
            release_manifest = Path(temporary_directory) / "release.manifest"
            lines = [
                f"FILE MISSING {path}" for path in EXPECTED_PREIMAGE_FILES
            ]
            lines.append("TREE MISSING reports/app")
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            release_lines = [
                "FORMAT dashboard-workspaces-release-input-v1",
                f"COMMIT {commit}",
                *[f"FILE {'0' * 64} {path}" for path in EXPECTED_FILES],
                f"TREE {'0' * 64} reports/app",
            ]
            release_manifest.write_text(
                "\n".join(release_lines) + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SA_ECS_REMOTE"] = "operator@example:/opt/app"
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(manifest)
            environment["SA_DASHBOARD_RELEASE_INPUT_MANIFEST"] = str(
                release_manifest
            )
            environment["SA_DASHBOARD_MAX_BYTES"] = "invalid"

            completed = self._run("deploy", environment=environment)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("SA_DASHBOARD_MAX_BYTES", completed.stderr)
        self.assertNotIn("bad substitution", completed.stderr)

    def test_remote_and_key_are_environment_driven(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("SA_ECS_REMOTE", source)
        self.assertIn("SA_ECS_SSH_OPTS", source)
        self.assertIn("RSYNC_RSH", source)
        for forbidden in (
            "120.55.",
            "root@",
            "/Users/",
            "/.ssh/",
            "id_rsa",
            "<ssh-key-file>",
        ):
            self.assertNotIn(forbidden, source)

    def test_harness_documents_reviewed_preimage_deploy_flow(self) -> None:
        harness = (ROOT / "docs" / "system-harness.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "./scripts/deploy-dashboard-workspaces-to-ecs.sh capture-preimage",
            harness,
        )
        self.assertIn(
            "./scripts/deploy-dashboard-workspaces-to-ecs.sh validate-manifest",
            harness,
        )
        self.assertIn(
            "./scripts/deploy-dashboard-workspaces-to-ecs.sh capture-release-input",
            harness,
        )
        self.assertIn("SA_DASHBOARD_PREIMAGE_MANIFEST", harness)
        self.assertIn("SA_DASHBOARD_RELEASE_INPUT_MANIFEST", harness)
        self.assertIn("只重启 `stock-analyze-dashboard.service`", harness)

    def test_deploy_fails_closed_before_build_without_remote(self) -> None:
        environment = os.environ.copy()
        for name in (
            "SA_ECS_REMOTE",
            "SA_ECS_SSH_HOST",
            "SA_ECS_REMOTE_PATH",
            "SA_DASHBOARD_PREIMAGE_MANIFEST",
        ):
            environment.pop(name, None)

        completed = self._run("deploy", environment=environment)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("SA_ECS_REMOTE", completed.stderr)

    def test_validate_manifest_accepts_only_the_complete_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "preimage.manifest"
            lines = [
                f"FILE MISSING {path}" for path in EXPECTED_PREIMAGE_FILES
            ]
            lines.append("TREE MISSING reports/app")
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

            accepted = self._run("validate-manifest", str(manifest))
            manifest.write_text("\n".join(lines[:-2]) + "\n", encoding="utf-8")
            rejected = self._run("validate-manifest", str(manifest))

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("manifest_valid", accepted.stdout)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("manifest", rejected.stderr.lower())


if __name__ == "__main__":
    unittest.main()
