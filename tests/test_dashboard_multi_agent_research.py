"""Read-only dashboard resource tests for multi-agent research artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.dashboard_multi_agent_research import (
    build_dashboard_multi_agent_research_data,
    build_dashboard_research_universe_instrument_data,
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
                            "name": "万科A",
                            "record_kind": "a_share_equity",
                            "research_only": True,
                            "research_scopes": ["hs300"],
                            "membership_date": "20260731",
                            "industry": "全国地产",
                            "board": "主板",
                            "size_bucket": "mid_cap",
                            "market_cap_date": "20260821",
                        },
                        {
                            "ts_code": "000001.SZ",
                            "name": "平安银行",
                            "record_kind": "a_share_equity",
                            "research_only": True,
                            "research_scopes": ["csi1000", "hs300"],
                            "membership_date": "20260731",
                            "industry": "银行",
                            "board": "主板",
                            "size_bucket": "large_cap",
                            "market_cap_date": "20260821",
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
        self.assertEqual(payload["scopeOptions"], [
            "board:主板", "csi1000", "hs300",
            "industry:全国地产", "industry:银行", "size:large_cap", "size:mid_cap",
        ])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["records"][0]["membershipDate"], "20260731")
        self.assertEqual(payload["records"][0]["name"], "平安银行")
        self.assertEqual(payload["executionEffect"], "none_research_only")

    def test_filters_a_share_by_persisted_master_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)

            payload = build_dashboard_research_universe_data(
                repo_root=root,
                kind="a_share",
                query="平安",
                scope=None,
                page=1,
                page_size=20,
            )

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["records"][0]["code"], "000001.SZ")
        self.assertEqual(payload["records"][0]["name"], "平安银行")

    def test_projects_and_filters_a_share_board_industry_and_size(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)

            payload = build_dashboard_research_universe_data(
                repo_root=root,
                kind="a_share",
                query="",
                scope="industry:银行",
                page=1,
                page_size=20,
            )

        self.assertEqual(payload["total"], 1)
        self.assertIn("industry:银行", payload["scopeOptions"])
        self.assertEqual(payload["records"][0]["board"], "主板")
        self.assertEqual(payload["records"][0]["sizeBucket"], "large_cap")

    def test_bounds_a_share_scope_options_to_the_frontend_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)
            catalog_path = root / "data/research/universe_catalogs/latest.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["a_share"]["records"].extend(
                {
                    "ts_code": f"{code:06d}.SZ",
                    "name": f"样本{code}",
                    "record_kind": "a_share_equity",
                    "research_only": True,
                    "research_scopes": ["all_a_share"],
                    "industry": f"行业{code}",
                    "board": "主板",
                    "size_bucket": "micro_cap",
                }
                for code in range(100000, 100140)
            )
            catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

            payload = build_dashboard_research_universe_data(
                repo_root=root, kind="a_share", query="", scope=None, page=1, page_size=20
            )

        self.assertEqual(len(payload["scopeOptions"]), 128)

    def test_projects_a_catalog_scoped_a_share_detail_without_account_data(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)
            cache = root / "data/shared/cache"
            cache.mkdir(parents=True)
            history = [
                {
                    "日期": f"2026{month:02d}{day:02d}",
                    "开盘": 10 + index,
                    "最高": 11 + index,
                    "最低": 9 + index,
                    "收盘": 10.5 + index,
                    "成交量": 1000 + index,
                    "成交额": 10000 + index,
                }
                for index, (month, day) in enumerate(
                    [(6, day) for day in range(1, 29)] + [(7, day) for day in range(1, 31)] + [(8, day) for day in range(1, 5)]
                )
            ]
            pd.DataFrame(history).to_csv(
                cache / "history_000001_20260822_90.csv",
                index=False,
            )
            (root / "data/a_share/codex").mkdir(parents=True)
            (root / "data/a_share/codex/trades.csv").write_text(
                "trade_date,account_id,code\n20260822,formal,000001.SZ\n",
                encoding="utf-8",
            )

            payload = build_dashboard_research_universe_instrument_data(
                repo_root=root,
                kind="a_share",
                code="000001.SZ",
            )

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["instrument"]["name"], "平安银行")
        self.assertEqual(payload["instrument"]["researchScopes"], ["csi1000", "hs300"])
        self.assertEqual(len(payload["candles"]), 62)
        self.assertIn("尚未完整", payload["warning"])
        self.assertTrue(payload["metrics"])
        self.assertNotIn("relatedTrades", payload)
        self.assertNotIn("predictions", payload)
        self.assertEqual(payload["executionEffect"], "none_research_only")

    def test_returns_controlled_unavailable_detail_for_unknown_catalog_code(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)

            payload = build_dashboard_research_universe_instrument_data(
                repo_root=root,
                kind="a_share",
                code="999999.SZ",
            )

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["candles"], [])
        self.assertEqual(payload["executionEffect"], "none_research_only")

    def test_reads_research_price_and_otc_nav_artifacts_without_provider_calls(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_universe_catalog(root)
            prices = root / "data/research/a_share_prices/v1"
            prices.mkdir(parents=True)
            (prices / "000001.SZ.csv").write_text(
                "ts_code,trade_date,open,high,low,close,vol,amount\n"
                "000001.SZ,20260820,9,10,8,9.5,900,9000\n"
                "000001.SZ,20260821,10,11,9,10.5,1000,10000\n",
                encoding="utf-8",
            )
            (prices / "latest.json").write_text(json.dumps({
                "schema_version": "a-share-research-prices-v1",
                "status": "complete",
                "as_of": "20260822",
                "completed": {"000001.SZ": {"rows": 2}},
            }), encoding="utf-8")
            navs = root / "data/research/otc_fund_nav/v1"
            navs.mkdir(parents=True)
            (navs / "000834.OF.csv").write_text(
                "ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav\n"
                "000834.OF,20260821,20260820,1,1,1\n"
                "000834.OF,20260822,20260821,1.1,1.1,1.1\n",
                encoding="utf-8",
            )
            (navs / "latest.json").write_text(json.dumps({
                "schema_version": "otc-fund-nav-v1",
                "status": "complete",
                "as_of": "20260822",
                "completed": {"000834.OF": {"rows": 2}},
            }), encoding="utf-8")

            equity = build_dashboard_research_universe_instrument_data(
                repo_root=root, kind="a_share", code="000001.SZ",
            )
            otc = build_dashboard_research_universe_instrument_data(
                repo_root=root, kind="otc_fund", code="000834.OF",
            )

        self.assertEqual(len(equity["candles"]), 2)
        self.assertEqual(equity["instrument"]["industry"], "银行")
        self.assertEqual(otc["candles"], [])
        self.assertEqual(otc["navSeries"][-1]["adjustedNav"], 1.1)
        self.assertTrue(otc["metrics"])

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
