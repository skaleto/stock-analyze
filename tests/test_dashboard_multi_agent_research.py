"""Read-only dashboard resource tests for multi-agent research artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stock_analyze.dashboard_multi_agent_research import (
    build_dashboard_multi_agent_research_data,
    build_dashboard_research_universe_data,
)
from stock_analyze.dashboard_http import InvalidDashboardQuery


class MultiAgentResearchDashboardTests(unittest.TestCase):
    def _write_universe_catalog(self, root: Path) -> None:
        catalog = root / "data/research/universe_catalogs/latest.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            json.dumps({
                "as_of": "20260822",
                "a_share": {
                    "records": [
                        {
                            "ts_code": "000002.SZ",
                            "record_kind": "a_share_equity",
                            "research_only": True,
                            "research_scopes": ["hs300"],
                            "membership_date": "20260731",
                        },
                        {
                            "ts_code": "000001.SZ",
                            "record_kind": "a_share_equity",
                            "research_only": True,
                            "research_scopes": ["csi1000", "hs300"],
                            "membership_date": "20260731",
                        },
                    ],
                },
                "funds": {
                    "records": [
                        {
                            "ts_code": "513100.SH",
                            "record_kind": "fund",
                            "name": "纳斯达克100ETF",
                            "market_source": "exchange",
                            "tradability": "exchange_research_only",
                            "research_only": True,
                            "fund_type": "ETF",
                            "benchmark": "纳斯达克100指数",
                            "overseas_scope": "nasdaq_100",
                            "classification_status": "name_benchmark_inferred",
                        },
                        {
                            "ts_code": "513500.SH",
                            "record_kind": "fund",
                            "name": "标普500ETF",
                            "market_source": "exchange",
                            "tradability": "exchange_research_only",
                            "research_only": True,
                            "fund_type": "ETF",
                            "benchmark": "标普500指数",
                            "overseas_scope": "sp500",
                            "classification_status": "name_benchmark_inferred",
                        },
                        {
                            "ts_code": "000834.OF",
                            "record_kind": "fund",
                            "name": "纳斯达克100指数基金",
                            "market_source": "otc",
                            "tradability": "otc_non_tradable_research_only",
                            "research_only": True,
                            "fund_type": "指数型",
                            "benchmark": "纳斯达克100指数",
                            "overseas_scope": "nasdaq_100",
                            "classification_status": "name_benchmark_inferred",
                        },
                        {
                            "ts_code": "007990.OF",
                            "record_kind": "fund",
                            "name": "标普500指数基金",
                            "market_source": "otc",
                            "tradability": "otc_non_tradable_research_only",
                            "research_only": True,
                            "fund_type": "指数型",
                            "benchmark": "标普500指数",
                            "overseas_scope": "sp500",
                            "classification_status": "name_benchmark_inferred",
                        },
                    ],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )

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

    def test_projects_sorted_a_share_page_and_scope_options(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)

            payload = build_dashboard_research_universe_data(
                repo_root=root,
                kind="a_share",
                query="",
                scope=None,
                page=1,
                page_size=20,
            )

        self.assertEqual(payload["status"], "available")
        self.assertEqual([row["code"] for row in payload["records"]], ["000001.SZ", "000002.SZ"])
        self.assertEqual(payload["scopeOptions"], ["csi1000", "hs300"])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["records"][0]["membershipDate"], "20260731")
        self.assertEqual(payload["executionEffect"], "none_research_only")

    def test_filters_fund_code_or_name_scope_and_paginates(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)

            payload = build_dashboard_research_universe_data(
                repo_root=root,
                kind="exchange_fund",
                query="纳斯",
                scope="nasdaq_100",
                page=1,
                page_size=20,
            )

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["records"][0]["code"], "513100.SH")
        self.assertEqual(payload["records"][0]["tradability"], "exchange_research_only")
        self.assertEqual(payload["scopeOptions"], ["nasdaq_100", "sp500"])

    def test_unavailable_catalog_and_beyond_last_page_are_controlled(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = build_dashboard_research_universe_data(
                repo_root=Path(tmp),
                kind="otc_fund",
                query="",
                scope=None,
                page=1,
                page_size=20,
            )
            root = Path(tmp)
            self._write_universe_catalog(root)
            beyond_last_page = build_dashboard_research_universe_data(
                repo_root=root,
                kind="otc_fund",
                query="",
                scope=None,
                page=2,
                page_size=20,
            )

        self.assertEqual(missing["status"], "unavailable")
        self.assertEqual(missing["records"], [])
        self.assertEqual(missing["total"], 0)
        self.assertEqual(beyond_last_page["status"], "available")
        self.assertEqual(beyond_last_page["records"], [])
        self.assertEqual(beyond_last_page["total"], 2)

    def test_rejects_invalid_kind_page_size_or_query_length(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(InvalidDashboardQuery, "kind"):
                build_dashboard_research_universe_data(
                    repo_root=root,
                    kind="all",
                    query="",
                    scope=None,
                    page=1,
                    page_size=20,
                )
            with self.assertRaisesRegex(InvalidDashboardQuery, "page"):
                build_dashboard_research_universe_data(
                    repo_root=root,
                    kind="a_share",
                    query="",
                    scope=None,
                    page=0,
                    page_size=20,
                )
            with self.assertRaisesRegex(InvalidDashboardQuery, "page_size"):
                build_dashboard_research_universe_data(
                    repo_root=root,
                    kind="a_share",
                    query="",
                    scope=None,
                    page=1,
                    page_size=10,
                )
            with self.assertRaisesRegex(InvalidDashboardQuery, "query"):
                build_dashboard_research_universe_data(
                    repo_root=root,
                    kind="a_share",
                    query="x" * 81,
                    scope=None,
                    page=1,
                    page_size=20,
                )
            with self.assertRaisesRegex(InvalidDashboardQuery, "scope"):
                build_dashboard_research_universe_data(
                    repo_root=root,
                    kind="a_share",
                    query="",
                    scope="x" * 129,
                    page=1,
                    page_size=20,
                )
