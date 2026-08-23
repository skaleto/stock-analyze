from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = ROOT / "scripts" / "system-audit.sh"
CHECK_NAMES = (
    "a_share_all_cap_source_manifest",
    "a_share_all_cap_universe_manifest",
    "backtest_statement_code_coverage",
    "backtest_status_code_coverage",
    "a_share_stock_master_counts",
    "filesystem_free_fraction",
)
START = "2018-01-02"
END = "2026-08-21"


class SystemAuditScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary_directory.name)
        (self.repo / "scripts").mkdir()
        shutil.copy2(AUDIT_SCRIPT, self.repo / "scripts" / AUDIT_SCRIPT.name)
        self._write_contract()
        self._write_fake_loaders()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_contract(self) -> None:
        path = self.repo / "configs" / "research" / "a_share_all_cap_v2.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(
            textwrap.dedent(
                f"""\
                windows:
                  development_start: "{START}"
                  holdout_end: "{END}"
                storage:
                  minimum_filesystem_free_fraction_after_publish: 0.15
                """
            ),
            encoding="utf-8",
        )

    def _write_fake_loaders(self) -> None:
        package = self.repo / "stock_analyze" / "research"
        package.mkdir(parents=True)
        (self.repo / "stock_analyze" / "__init__.py").write_text("", encoding="utf-8")
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "a_share_all_cap_sources.py").write_text(
            textwrap.dedent(
                """\
                from pathlib import Path
                from types import SimpleNamespace

                def load_verified_all_cap_sources(repo_root):
                    marker = (
                        Path(repo_root)
                        / "data/research/a_share_all_cap/v1/sources/latest.json"
                    )
                    if not marker.exists():
                        raise ValueError("all_cap_source_manifest_missing")
                    if marker.read_text(encoding="utf-8").strip() == "corrupt":
                        raise ValueError("all_cap_source_checksum:manifest")
                    return SimpleNamespace(
                        metadata={
                            "manifest_sha256": "a" * 64,
                            "row_counts": {
                                "index_daily": 10,
                                "index_weights": 20,
                                "industry_membership": 30,
                                "stk_limit": 40,
                            },
                        },
                        stk_limit={"2018": object(), "2019": object()},
                    )
                """
            ),
            encoding="utf-8",
        )
        (package / "a_share_all_cap_universe.py").write_text(
            textwrap.dedent(
                """\
                from pathlib import Path
                from types import SimpleNamespace

                def load_verified_all_cap_universe(repo_root):
                    marker = (
                        Path(repo_root)
                        / "data/research/a_share_all_cap/v1/universe/latest.json"
                    )
                    if not marker.exists():
                        raise ValueError("all_cap_universe_manifest_missing")
                    if marker.read_text(encoding="utf-8").strip() == "corrupt":
                        raise ValueError("all_cap_universe_checksum:manifest")
                    return SimpleNamespace(
                        metadata={
                            "manifest_sha256": "b" * 64,
                            "row_counts": {
                                "membership": 50,
                                "daily_hard_status": 60,
                            },
                        },
                        membership={"2018": object(), "2019": object()},
                        daily_hard_status={"2018": object(), "2019": object()},
                    )
                """
            ),
            encoding="utf-8",
        )
        (self.repo / "sitecustomize.py").write_text(
            textwrap.dedent(
                """\
                import os
                import shutil

                if os.environ.get("SA_TEST_DISK_FREE_FRACTION"):
                    fraction = float(os.environ["SA_TEST_DISK_FREE_FRACTION"])
                    total = 1000
                    shutil.disk_usage = lambda _path: shutil._ntuple_diskusage(
                        total, total - int(total * fraction), int(total * fraction)
                    )
                """
            ),
            encoding="utf-8",
        )

    def _write_complete_data(self) -> None:
        for relative in (
            "data/research/a_share_all_cap/v1/sources/latest.json",
            "data/research/a_share_all_cap/v1/universe/latest.json",
        ):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("valid\n", encoding="utf-8")

        cache = self.repo / "data" / "shared" / "backtest_cache"
        cache.mkdir(parents=True)
        (cache / "stock_basic.csv").write_text(
            "ts_code,list_status\n"
            "000001.SZ,L\n"
            "000002.SZ,D\n"
            "000003.SZ,P\n",
            encoding="utf-8-sig",
        )
        ranges = [
            f"{code}:{START}:{END}"
            for code in ("000001.SZ", "000002.SZ", "000003.SZ")
        ]
        meta = {
            "stock_basic_done": True,
            "stock_basic_statuses_done": ["D", "L", "P"],
            "income_code_ranges_done": ranges,
            "balancesheet_code_ranges_done": ranges,
            "cashflow_code_ranges_done": ranges,
            "baostock_status_code_ranges_done": ranges,
        }
        (cache / "_meta.json").write_text(
            json.dumps(meta),
            encoding="utf-8",
        )
        for endpoint in ("income", "balancesheet", "cashflow"):
            endpoint_dir = cache / endpoint
            endpoint_dir.mkdir()
            for code in ("000001.SZ", "000002.SZ", "000003.SZ"):
                (endpoint_dir / f"{code}.csv").write_text(
                    "ts_code,ann_date,end_date\n",
                    encoding="utf-8-sig",
                )
        status_dir = cache / "baostock_status"
        status_dir.mkdir()
        for code in ("000001.SZ", "000002.SZ", "000003.SZ"):
            (status_dir / f"{code}.csv").write_text(
                "ts_code,trade_date,tradestatus,is_st,st_source\n",
                encoding="utf-8",
            )

    def _write_python_proxy(self, path: Path, marker: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/bin/sh\n"
            f"touch {shlex.quote(str(marker))}\n"
            f"exec {shlex.quote(sys.executable)} \"$@\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _run(
        self,
        *,
        production: bool = False,
        free_fraction: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.repo),
                "SA_PYTHON_BIN": sys.executable,
                "SA_SYSTEM_AUDIT_DATA_ONLY": "1",
            }
        )
        if production:
            environment["SA_SYSTEM_AUDIT_PRODUCTION"] = "1"
        if free_fraction is not None:
            environment["SA_TEST_DISK_FREE_FRACTION"] = str(free_fraction)
        return subprocess.run(
            ["/bin/bash", str(self.repo / "scripts" / AUDIT_SCRIPT.name)],
            cwd=self.repo,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_repo_venv_is_preferred_without_explicit_python(self) -> None:
        marker = self.repo / "repo-venv-used"
        self._write_python_proxy(self.repo / ".venv" / "bin" / "python", marker)
        environment = os.environ.copy()
        environment.pop("SA_PYTHON_BIN", None)
        environment.update(
            {
                "PYTHONPATH": str(self.repo),
                "SA_SYSTEM_AUDIT_DATA_ONLY": "1",
            }
        )

        result = subprocess.run(
            ["/bin/bash", str(self.repo / "scripts" / AUDIT_SCRIPT.name)],
            cwd=self.repo,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertTrue(marker.exists(), result.stdout + result.stderr)

    def test_explicit_python_overrides_repo_venv(self) -> None:
        repo_marker = self.repo / "repo-venv-used"
        explicit_marker = self.repo / "explicit-python-used"
        self._write_python_proxy(
            self.repo / ".venv" / "bin" / "python",
            repo_marker,
        )
        explicit_python = self.repo / "explicit" / "python"
        self._write_python_proxy(explicit_python, explicit_marker)
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.repo),
                "SA_PYTHON_BIN": str(explicit_python),
                "SA_SYSTEM_AUDIT_DATA_ONLY": "1",
            }
        )

        result = subprocess.run(
            ["/bin/bash", str(self.repo / "scripts" / AUDIT_SCRIPT.name)],
            cwd=self.repo,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(explicit_marker.exists())
        self.assertFalse(repo_marker.exists())

    def test_python_selection_keeps_production_venv_fallback(self) -> None:
        script = (self.repo / "scripts" / AUDIT_SCRIPT.name).read_text(
            encoding="utf-8"
        )

        repo_venv = script.index('if [[ -x "$ROOT/.venv/bin/python" ]]')
        production_venv = script.index(
            "elif [[ -x /opt/stock-analyze/venv/bin/python ]]"
        )
        system_python = script.index("PYTHON_BIN=python3")
        self.assertLess(repo_venv, production_venv)
        self.assertLess(production_venv, system_python)

    def test_local_missing_data_warns_and_continues(self) -> None:
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in CHECK_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, result.stdout)
        self.assertIn("WARN a_share_all_cap_source_manifest", result.stdout)
        self.assertIn("WARN backtest_status_code_coverage", result.stdout)
        self.assertIn("RESULT all_cap_data_foundation WARN", result.stdout)

    def test_complete_production_data_passes_with_counts(self) -> None:
        self._write_complete_data()

        result = self._run(production=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "PASS a_share_all_cap_source_manifest checksum=verified "
            "datasets=4 rows=100 partition_years=2",
            result.stdout,
        )
        self.assertIn(
            "PASS a_share_all_cap_universe_manifest checksum=verified "
            "membership_years=2 membership_rows=50 "
            "status_years=2 status_rows=60",
            result.stdout,
        )
        self.assertIn(
            "PASS a_share_stock_master_counts "
            "active=1 delisted=1 paused=1 total=3",
            result.stdout,
        )
        self.assertIn(
            "PASS backtest_statement_code_coverage "
            "completed_codes=3 total_codes=3 completed_ranges=9",
            result.stdout,
        )
        self.assertIn(
            "PASS backtest_status_code_coverage provider=baostock "
            "completed_codes=3 total_codes=3 completed_ranges=3",
            result.stdout,
        )
        self.assertIn("PASS filesystem_free_fraction", result.stdout)
        self.assertIn("RESULT all_cap_data_foundation PASS", result.stdout)
        for forbidden in ("stock_basic.csv", "_meta.json", "/etc/"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_checksum_loader_failure_is_fail(self) -> None:
        self._write_complete_data()
        marker = (
            self.repo
            / "data/research/a_share_all_cap/v1/sources/latest.json"
        )
        marker.write_text("corrupt\n", encoding="utf-8")

        result = self._run()

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "FAIL a_share_all_cap_source_manifest "
            "checksum=invalid reason=all_cap_source_checksum",
            result.stdout,
        )
        self.assertIn("RESULT all_cap_data_foundation FAIL", result.stdout)

    def test_production_missing_pit_status_fails(self) -> None:
        self._write_complete_data()
        meta_path = self.repo / "data/shared/backtest_cache/_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["baostock_status_code_ranges_done"] = []
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        result = self._run(production=True)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "FAIL backtest_status_code_coverage provider=baostock "
            "completed_codes=0 total_codes=3 completed_ranges=0",
            result.stdout,
        )

    def test_free_fraction_below_fifteen_percent_fails(self) -> None:
        result = self._run(free_fraction=0.149)

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "FAIL filesystem_free_fraction free_bytes=149 total_bytes=1000 "
            "free_fraction=0.149000 minimum=0.150000",
            result.stdout,
        )
        self.assertIn("RESULT all_cap_data_foundation FAIL", result.stdout)

    def test_harness_documents_resumable_production_backfills(self) -> None:
        harness = (ROOT / "docs" / "system-harness.md").read_text(encoding="utf-8")

        self.assertIn(
            'ENV_FILE="${SA_ECS_ENV_FILE:-/etc/stock-analyze/secrets.env}"',
            harness,
        )
        self.assertIn('test -r "$ENV_FILE"', harness)
        self.assertIn(
            "grep -Eq '^[[:space:]]*TUSHARE_TOKEN=' \"$ENV_FILE\"",
            harness,
        )
        self.assertNotIn(
            "systemctl show stock-analyze-model-iteration.service",
            harness,
        )
        self.assertNotIn("--property=EnvironmentFiles", harness)
        for forbidden in (
            'echo "$ENV_FILE"',
            'printf "%s\\n" "$ENV_FILE"',
            'cat "$ENV_FILE"',
            'grep "TUSHARE_TOKEN" "$ENV_FILE"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, harness)

        for command in (
            "--phases statements --code-scope all",
            "--phases status --code-scope all --status-provider baostock",
            "refresh-a-share-all-cap-sources",
            "--start 2018-01-02 --end 2024-12-31",
        ):
            with self.subTest(command=command):
                self.assertIn(command, harness)
        for requirement in (
            "systemd-run",
            "journalctl",
            "df -B1",
            "SA_SYSTEM_AUDIT_PRODUCTION=1",
            "load_verified_all_cap_sources",
            "load_verified_all_cap_universe",
            "15%",
            "禁止使用 `--force`",
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, harness)


if __name__ == "__main__":
    unittest.main()
