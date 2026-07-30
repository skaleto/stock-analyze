from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-dashboard-workspaces-to-ecs.sh"
LEGACY_SCRIPT = ROOT / "scripts" / "deploy-app-to-ecs.sh"

EXPECTED_FILES = [
    "stock_analyze/cli.py",
    "stock_analyze/dashboard_api.py",
    "stock_analyze/dashboard_finance.py",
    "stock_analyze/dashboard_http.py",
    "stock_analyze/dashboard_workspace_api.py",
    "stock_analyze/dashboard_runtime.py",
    "tests/test_cli_dashboard_routes.py",
    "tests/test_dashboard_finance.py",
    "tests/test_dashboard_http.py",
    "tests/test_dashboard_resource_api.py",
    "tests/test_dashboard_runtime.py",
    "tests/test_dashboard_workspace_api.py",
]


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
            lines = [f"FILE MISSING {path}" for path in EXPECTED_FILES]
            lines.append("TREE MISSING reports/app")
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["SA_ECS_REMOTE"] = "operator@example:/opt/app"
            environment["SA_DASHBOARD_PREIMAGE_MANIFEST"] = str(manifest)
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
            "ai_baby_aliyun",
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
        self.assertIn("SA_DASHBOARD_PREIMAGE_MANIFEST", harness)
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
            lines = [f"FILE MISSING {path}" for path in EXPECTED_FILES]
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
