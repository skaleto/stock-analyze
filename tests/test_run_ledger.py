from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.config import config_hash
from stock_analyze.run_ledger import RunLedger, code_version, read_runs


SAMPLE_CONFIG = {
    "strategy_id": "test",
    "factors": {"pe": {"weight": 1.0, "direction": "low"}},
}


class RunLedgerTests(unittest.TestCase):
    def test_success_run_writes_start_and_success_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunLedger(tmp)
            with ledger.run("rebalance", as_of="2026-05-19", config=SAMPLE_CONFIG):
                pass
            runs = read_runs(tmp)
            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual(run["command"], "rebalance")
            self.assertEqual(run["status"], "success")
            self.assertTrue(run["run_id"].startswith("rebalance-"))
            self.assertEqual(run["config_hash"], config_hash(SAMPLE_CONFIG))

    def test_failure_records_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunLedger(tmp)
            with self.assertRaises(RuntimeError):
                with ledger.run("execute", as_of="2026-05-19", config=SAMPLE_CONFIG):
                    raise RuntimeError("simulated failure")
            runs = read_runs(tmp)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "failed")
            self.assertIn("simulated failure", runs[0]["error_summary"])

    def test_config_snapshot_written_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunLedger(tmp)
            digest_a = ledger.snapshot_config(SAMPLE_CONFIG)
            mtime_a = (Path(tmp) / "configs" / f"{digest_a}.json").stat().st_mtime
            digest_b = ledger.snapshot_config(SAMPLE_CONFIG)
            mtime_b = (Path(tmp) / "configs" / f"{digest_b}.json").stat().st_mtime
            self.assertEqual(digest_a, digest_b)
            self.assertEqual(mtime_a, mtime_b)

    def test_config_change_creates_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = RunLedger(tmp)
            digest_a = ledger.snapshot_config(SAMPLE_CONFIG)
            modified = {**SAMPLE_CONFIG, "factors": {"pe": {"weight": 0.6, "direction": "low"}}}
            digest_b = ledger.snapshot_config(modified)
            self.assertNotEqual(digest_a, digest_b)
            snapshot = json.loads((Path(tmp) / "configs" / f"{digest_b}.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["factors"]["pe"]["weight"], 0.6)

    def test_code_version_returns_no_git_in_non_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(code_version(tmp), "no_git")

    def test_code_version_prefers_deploy_marker_over_git_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text("0123456789abcdef\n", encoding="utf-8")
            (root / "DEPLOY_VERSION").write_text("deployed-commit-123\n", encoding="utf-8")

            self.assertEqual(code_version(root), "deployed-commit-123")


if __name__ == "__main__":
    unittest.main()
