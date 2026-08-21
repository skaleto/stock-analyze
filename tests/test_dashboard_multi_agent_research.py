"""Read-only dashboard resource tests for multi-agent research artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stock_analyze.dashboard_multi_agent_research import (
    build_dashboard_multi_agent_research_data,
)


class MultiAgentResearchDashboardTests(unittest.TestCase):
    def test_returns_empty_state_without_research_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_dashboard_multi_agent_research_data(repo_root=Path(tmp))

        self.assertEqual(payload["status"], "empty")
        self.assertIsNone(payload["latestRun"])
        self.assertEqual(payload["universe"]["status"], "unavailable")

    def test_reads_only_latest_completed_artifact_and_bounded_universe_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "reports/research/multi_agent/a_share/000001.SZ/run-1"
            output.mkdir(parents=True)
            (output / "manifest.json").write_text(
                json.dumps({
                    "run_id": "run-1", "created_at": "2026-08-22T01:02:03+00:00",
                    "status": "completed_with_degradation", "market": "a_share",
                    "instrument": {"code": "000001.SZ", "name": "平安银行"},
                    "model": "test-model", "degraded_roles": ["news"],
                    "execution_effect": "none_research_only",
                }),
                encoding="utf-8",
            )
            (output / "digest.md").write_text("# 简报\n\n仅研究\n", encoding="utf-8")
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(
                json.dumps({
                    "as_of": "20260822",
                    "a_share": {"summary": {"scope_counts": {"csi1000": 1000}}},
                    "funds": {"summary": {"source_counts": {"exchange": 2188, "otc": 15000}}},
                }),
                encoding="utf-8",
            )

            payload = build_dashboard_multi_agent_research_data(repo_root=root)

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["latestRun"]["runId"], "run-1")
        self.assertEqual(payload["latestRun"]["degradedRoles"], ["news"])
        self.assertEqual(payload["latestRun"]["digest"], "# 简报\n\n仅研究")
        self.assertNotIn("output_dir", payload["latestRun"])
        self.assertEqual(payload["universe"]["aShare"]["scopeCounts"]["csi1000"], 1000)
        self.assertEqual(payload["universe"]["funds"]["sourceCounts"]["otc"], 15000)
