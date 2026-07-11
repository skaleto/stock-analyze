from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.cli import _is_dashboard_api_path
from stock_analyze.competition import UnknownAgent
from stock_analyze.dashboard_aggregator import DashboardDataError, build_dashboard_detail_data


def _seed_detail_repo(root: Path) -> None:
    (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "agents" / "codex_cn_qdii_etf.yaml").write_text(
        json.dumps({"agent_id": "codex", "strategy_id": "codex_qdii", "factors": {}}),
        encoding="utf-8",
    )
    data_dir = root / "data" / "cn_qdii_etf" / "codex"
    reports_dir = root / "reports" / "cn_qdii_etf" / "codex"
    shared_cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    shared_cache.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"ts_code": "513100.SH", "name": "纳指ETF"},
            {"ts_code": "159941.SZ", "name": "纳指ETF联接"},
        ]
    ).to_csv(shared_cache / "fund_basic_E.csv", index=False)

    pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "account_id": "us_exposure",
                "cash": 1_000_000,
                "market_value": 0,
                "total_value": 1_000_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.1,
                "benchmark_date": "2026-07-09",
                "notes": "",
            },
            {
                "date": "2026-07-10",
                "account_id": "us_exposure",
                "cash": 998_000,
                "market_value": 2_500,
                "total_value": 1_000_500,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.2,
                "benchmark_date": "2026-07-10",
                "notes": "nav refresh",
            },
        ]
    ).to_csv(data_dir / "daily_nav.csv", index=False)

    pd.DataFrame(
        [
            {
                "run_id": "run-weekly-20260710T005635-8fmi",
                "command": "run-weekly",
                "as_of": "2026-07-10",
                "started_at": "2026-07-10T00:56:35",
                "finished_at": "2026-07-10T00:56:36",
                "duration_ms": 878,
                "status": "success",
                "error_summary": "",
                "config_hash": "abc123",
                "code_version": "db83413",
            }
        ]
    ).to_csv(data_dir / "runs.csv", index=False)

    (data_dir / "pending_orders.json").write_text(
        json.dumps(
            [
                {
                    "account_id": "us_exposure",
                    "code": "513100.SH",
                    "side": "buy",
                    "shares": 1000,
                    "target_value": 10000,
                    "score": 0.92,
                    "trade_date": "2026-07-13",
                    "reason": "momentum",
                },
                {
                    "account_id": "gold",
                    "code": "159941.SZ",
                    "side": "sell",
                    "shares": 500,
                    "target_value": 0,
                    "score": 0.14,
                    "trade_date": "2026-07-13",
                    "reason": "rebalance",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pd.DataFrame(
        [
            {
                "account_id": "us_exposure",
                "code": "513100.SH",
                "name": "纳指ETF",
                "industry": "us_exposure",
                "shares": 1000,
                "available_shares": 1000,
                "avg_cost": 1.2,
                "last_price": 1.25,
                "market_value": 1250,
                "unrealized_pnl": 50,
                "score": 0.92,
                "updated_at": "2026-07-10T01:00:00",
            }
        ]
    ).to_csv(data_dir / "positions.csv", index=False)

    pd.DataFrame(
        [
            {
                "trade_date": "2026-07-10",
                "account_id": "us_exposure",
                "code": "513100.SH",
                "name": "纳指ETF",
                "side": "buy",
                "shares": 1000,
                "price": 1.2,
                "gross_amount": 1200,
                "commission": 0.36,
                "stamp_tax": 0,
                "slippage": 0.6,
                "net_amount": 1200.96,
                "cash_after": 998_799.04,
                "reason": "test",
            }
        ]
    ).to_csv(data_dir / "trades.csv", index=False)

    (reports_dir / "weekly_report.md").write_text(
        "# 跨境 ETF 周报\n\n本周生成 2 笔目标订单。",
        encoding="utf-8",
    )


class DashboardAppApiTests(unittest.TestCase):
    def test_detail_payload_reads_runtime_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

            self.assertEqual(payload["market"], "cn_qdii_etf")
            self.assertEqual(payload["agent"], "codex")
            self.assertEqual(payload["nav"]["latest"]["date"], "2026-07-10")
            self.assertEqual(payload["nav"]["latest"]["benchmark_code"], "513100.SH")
            self.assertEqual(len(payload["nav"]["series"]), 2)
            self.assertEqual(payload["orders"]["summary"]["total"], 2)
            self.assertEqual(payload["orders"]["summary"]["buy"], 1)
            self.assertEqual(payload["orders"]["summary"]["sell"], 1)
            self.assertEqual(payload["orders"]["rows"][0]["code"], "513100.SH")
            self.assertEqual(payload["orders"]["rows"][0]["name"], "纳指ETF")
            self.assertEqual(payload["orders"]["rows"][0]["execute_after"], "2026-07-13")
            self.assertEqual(payload["orders"]["rows"][0]["target_value"], 10000)
            self.assertEqual(payload["positions"]["rows"][0]["code"], "513100.SH")
            self.assertEqual(payload["trades"]["rows"][0]["code"], "513100.SH")
            self.assertEqual(payload["runs"]["rows"][0]["run_id"], "run-weekly-20260710T005635-8fmi")
            self.assertIn("目标订单", payload["weekly_report"]["markdown"])
            json.dumps(payload, allow_nan=False, ensure_ascii=False)

    def test_detail_api_route_is_recognised_with_query_string(self) -> None:
        self.assertTrue(_is_dashboard_api_path("/api/dashboard/detail.json"))
        self.assertFalse(_is_dashboard_api_path("/api/dashboard/unknown.json"))

    def test_existing_malformed_positions_csv_is_an_explicit_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            positions = root / "data" / "cn_qdii_etf" / "codex" / "positions.csv"
            positions.write_text('account_id,code\n"unterminated', encoding="utf-8")

            with self.assertRaises(DashboardDataError) as caught:
                build_dashboard_detail_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="codex",
                )

        self.assertEqual(caught.exception.source, "positions")
        self.assertNotIn(str(root), str(caught.exception))

    def test_existing_malformed_pending_json_is_an_explicit_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            pending = root / "data" / "cn_qdii_etf" / "codex" / "pending_orders.json"
            pending.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(DashboardDataError) as caught:
                build_dashboard_detail_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="codex",
                )

        self.assertEqual(caught.exception.source, "pending_orders")

    def test_unknown_agent_is_rejected_before_filesystem_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)

            with self.assertRaises(UnknownAgent):
                build_dashboard_detail_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="missing",
                )

    def test_combined_nav_exposes_multiple_benchmarks_without_picking_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            nav_path = root / "data" / "cn_qdii_etf" / "codex" / "daily_nav.csv"
            nav = pd.read_csv(nav_path, dtype={"benchmark_code": str})
            nav = pd.concat(
                [
                    nav,
                    pd.DataFrame(
                        [
                            {
                                "date": "2026-07-10",
                                "account_id": "hk_exposure",
                                "cash": 500_000,
                                "market_value": 1_000,
                                "total_value": 501_000,
                                "benchmark_code": "159920.SZ",
                                "benchmark_close": 1.5,
                                "benchmark_date": "2026-07-10",
                                "notes": "nav refresh",
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
            nav.to_csv(nav_path, index=False)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["nav"]["benchmark_codes"], ["159920.SZ", "513100.SH"])
        self.assertEqual(
            payload["nav"]["latest"]["benchmark_codes"],
            ["159920.SZ", "513100.SH"],
        )
        self.assertIsNone(payload["nav"]["latest"]["benchmark_code"])


if __name__ == "__main__":
    unittest.main()
