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
    def test_detail_payload_prefers_current_fund_basic_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            shared_cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            pd.DataFrame(
                [{"ts_code": "513100.SH", "name": "新版纳指ETF"}]
            ).to_csv(shared_cache / "fund_basic_E_v2.csv", index=False)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["orders"]["rows"][0]["name"], "新版纳指ETF")

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
            self.assertEqual(payload["positions"]["rows"][0]["exposure_group"], "美国市场")
            self.assertEqual(payload["positions"]["rows"][0]["theme"], "纳斯达克100")
            self.assertEqual(payload["trades"]["rows"][0]["code"], "513100.SH")
            self.assertEqual(payload["trades"]["rows"][0]["side_label"], "买入")
            self.assertEqual(payload["runs"]["rows"][0]["run_id"], "run-weekly-20260710T005635-8fmi")
            self.assertEqual(payload["strategy"]["agent_label"], "趋势进攻")
            self.assertEqual(payload["activity"]["summary"]["total"], 3)
            self.assertEqual(payload["activity"]["rows"][0]["status"], "planned")
            self.assertIn("目标订单", payload["weekly_report"]["markdown"])
            json.dumps(payload, allow_nan=False, ensure_ascii=False)

    def test_qdii_detail_payload_includes_selection_funnel_and_underlying_lookthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            data_dir = root / "data" / "cn_qdii_etf" / "codex"
            (data_dir / "selection_snapshot.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "as_of": "2026-07-10",
                        "universe_hash": "shared-hash",
                        "scopes": {
                            "us_exposure": {
                                "stages": [
                                    {"key": "catalog", "label": "动态目录", "count": 13},
                                    {"key": "portfolio_target", "label": "目标持仓", "count": 1},
                                ],
                                "rejections": [{"reason": "abnormal_premium", "count": 2}],
                                "selected": [{"code": "513100.SH", "index_key": "nasdaq_100"}],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["selection"]["universe_hash"], "shared-hash")
        self.assertEqual(payload["selection"]["scopes"]["us_exposure"]["stages"][0]["count"], 13)
        self.assertEqual(payload["lookthrough"]["source"], "positions")
        self.assertEqual(payload["lookthrough"]["indexes"][0]["index_key"], "nasdaq_100")
        companies = {row["symbol"]: row for row in payload["lookthrough"]["companies"]}
        self.assertAlmostEqual(companies["NVDA"]["weight"], 0.076)
        self.assertEqual(payload["lookthrough"]["sources"][0]["as_of"], "2026-06-30")

    def test_qdii_detail_payload_includes_dynamic_p2_research_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            research = root / "data" / "cn_qdii_etf" / "research"
            capacity = research / "capacity" / "capacity-run"
            shadow = research / "shadow" / "shadow-run"
            shared = root / "data" / "cn_qdii_etf" / "shared"
            capacity.mkdir(parents=True)
            shadow.mkdir(parents=True)
            shared.mkdir(parents=True, exist_ok=True)
            (capacity / "summary.json").write_text(
                json.dumps({"run_id": "capacity-run", "recommendations": [{"strategy": "codex", "scope": "us_exposure", "recommended_top_n": 5}]}),
                encoding="utf-8",
            )
            pd.DataFrame([{"strategy": "codex", "scope": "us_exposure", "top_n": 5, "sharpe_ratio": 1.2}]).to_csv(capacity / "metrics.csv", index=False)
            (shadow / "summary.json").write_text(
                json.dumps({"run_id": "shadow-run", "mode": "research_only", "skipped_scopes": []}),
                encoding="utf-8",
            )
            pd.DataFrame([{"asset_class": "global_equity", "scope": "japan_exposure", "factor_model": "global_equity_v1", "cumulative_return": 0.12, "sharpe_ratio": 0.8, "max_drawdown": -0.1, "promotion_status": "shadow_ready"}]).to_csv(shadow / "metrics.csv", index=False)
            pd.DataFrame([{"code": "159866.SZ", "name": "日经ETF", "asset_class": "global_equity", "research_scope": "japan_exposure", "promotion_status": "shadow_ready"}]).to_csv(shadow / "catalog.csv", index=False)
            pd.DataFrame([{
                "event_id": "AN1", "report_id": "AN1", "code": "513100.SH", "name": "纳指ETF", "category": "1",
                "title": "暂停申购公告", "published_at": "2026-07-10T00:00:00", "observed_at": "2026-07-10T08:00:00",
                "effective_at": "2026-07-10T00:00:00", "expires_at": "2026-08-09T00:00:00", "event_type": "suspension",
                "severity": "hard", "hard_block": True, "clears_temporary_blocks": False, "source_url": "https://example.test/a",
                "raw_content_hash": "hash", "parser_version": "v1",
            }]).to_csv(shared / "fund_events.csv", index=False)
            pd.DataFrame([{
                "agent": "codex", "week_end": "2026-07-10", "index_key": "nikkei_225", "score": 0.4, "confidence": 0.8,
                "drivers": "日元走弱", "sources": "https://example.test/n", "llm_model": "gpt-5.6", "prompt_version": "theme_v1",
                "observed_at": "2026-07-10T08:00:00", "expires_at": "2026-07-24T08:00:00",
            }]).to_csv(research / "theme_sentiment.csv", index=False)

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        self.assertEqual(payload["research"]["capacity"]["run_id"], "capacity-run")
        self.assertEqual(payload["research"]["shadow"]["metrics"][0]["scope"], "japan_exposure")
        self.assertEqual(payload["research"]["events"]["active_hard_blocks"], 1)
        self.assertEqual(payload["research"]["theme_sentiment"][0]["index_key"], "nikkei_225")

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

    def test_existing_positions_csv_with_missing_required_columns_is_a_data_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            positions = root / "data" / "cn_qdii_etf" / "codex" / "positions.csv"
            pd.DataFrame([{"unexpected": "value"}]).to_csv(positions, index=False)

            with self.assertRaises(DashboardDataError) as caught:
                build_dashboard_detail_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="codex",
                )

        self.assertEqual(caught.exception.source, "positions")

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

    def test_nav_series_contains_weighted_normalized_composite_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            nav_path = root / "data" / "cn_qdii_etf" / "codex" / "daily_nav.csv"
            pd.DataFrame(
                [
                    {
                        "date": "2026-07-09",
                        "account_id": "us_exposure",
                        "cash": 600_000,
                        "market_value": 0,
                        "total_value": 600_000,
                        "benchmark_code": "513100.SH",
                        "benchmark_close": 1.0,
                        "benchmark_date": "2026-07-09",
                    },
                    {
                        "date": "2026-07-09",
                        "account_id": "hk_exposure",
                        "cash": 400_000,
                        "market_value": 0,
                        "total_value": 400_000,
                        "benchmark_code": "159920.SZ",
                        "benchmark_close": 2.0,
                        "benchmark_date": "2026-07-09",
                    },
                    {
                        "date": "2026-07-10",
                        "account_id": "us_exposure",
                        "cash": 600_000,
                        "market_value": 0,
                        "total_value": 600_000,
                        "benchmark_code": "513100.SH",
                        "benchmark_close": 1.1,
                        "benchmark_date": "2026-07-10",
                    },
                    {
                        "date": "2026-07-10",
                        "account_id": "hk_exposure",
                        "cash": 400_000,
                        "market_value": 0,
                        "total_value": 400_000,
                        "benchmark_code": "159920.SZ",
                        "benchmark_close": 2.1,
                        "benchmark_date": "2026-07-10",
                    },
                ]
            ).to_csv(nav_path, index=False)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["nav"]["benchmark_label"], "组合基准")
        self.assertEqual(payload["nav"]["series"][0]["benchmark_return"], 0.0)
        self.assertAlmostEqual(
            payload["nav"]["series"][1]["benchmark_return"],
            0.08,
        )
        self.assertEqual(payload["nav"]["series"][1]["benchmark_coverage"], 1.0)

    def test_instrument_payload_reads_qdii_ohlcv_and_related_trades(self) -> None:
        from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            pd.DataFrame(
                [
                    {
                        "ts_code": "513100.SH",
                        "trade_date": "20260710",
                        "open": 2.18,
                        "high": 2.20,
                        "low": 2.17,
                        "close": 2.19,
                        "vol": 1200,
                        "amount": 2600,
                    },
                    {
                        "ts_code": "513100.SH",
                        "trade_date": "20260709",
                        "open": 2.10,
                        "high": 2.16,
                        "low": 2.09,
                        "close": 2.15,
                        "vol": 1000,
                        "amount": 2200,
                    },
                ]
            ).to_csv(cache / "fund_daily_513100_SH_20260710.csv", index=False)

            payload = build_dashboard_instrument_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                code="513100.SH",
            )

        self.assertEqual(payload["instrument"]["name"], "纳指ETF")
        self.assertEqual(payload["instrument"]["theme"], "纳斯达克100")
        self.assertEqual(
            [item["date"] for item in payload["candles"]],
            ["2026-07-09", "2026-07-10"],
        )
        self.assertAlmostEqual(payload["latest"]["change_pct"], 2.19 / 2.15 - 1.0)
        self.assertEqual(payload["related_trades"][0]["side_label"], "买入")
        self.assertIn("avg_amount_20", {item["key"] for item in payload["metrics"]})
        self.assertEqual(payload["latest"]["amount"], 2_600_000.0)
        average_amount = next(item for item in payload["metrics"] if item["key"] == "avg_amount_20")
        self.assertEqual(average_amount["value"], 2_400_000.0)
        self.assertEqual(payload["underlying"]["index_key"], "nasdaq_100")
        self.assertEqual(payload["underlying"]["constituents"][0]["symbol"], "NVDA")

    def test_instrument_payload_normalizes_a_share_chinese_cache_columns(self) -> None:
        from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "agents").mkdir(parents=True)
            (root / "configs" / "agents" / "codex_a_share.yaml").write_text(
                "{}",
                encoding="utf-8",
            )
            cache = root / "data" / "shared" / "cache"
            cache.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"日期": "2026-07-09", "开盘": 10, "最高": 11, "最低": 9.8, "收盘": 10.5, "成交额": 1000},
                    {"日期": "2026-07-10", "开盘": 10.5, "最高": 10.8, "最低": 10.2, "收盘": 10.6, "成交额": 1200},
                ]
            ).to_csv(cache / "history_000001_20260710_260.csv", index=False)

            payload = build_dashboard_instrument_data(
                repo_root=root,
                market="a_share",
                agent="codex",
                code="000001.SZ",
            )

        self.assertEqual(len(payload["candles"]), 2)
        self.assertEqual(payload["latest"]["close"], 10.6)

    def test_instrument_payload_keeps_three_calendar_years_and_prefers_wide_cache(self) -> None:
        from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs" / "agents").mkdir(parents=True)
            (root / "configs" / "agents" / "codex_a_share.yaml").write_text(
                "{}",
                encoding="utf-8",
            )
            cache = root / "data" / "shared" / "cache"
            cache.mkdir(parents=True)
            pd.DataFrame(
                [{"日期": "2026-07-10", "开盘": 99, "最高": 100, "最低": 98, "收盘": 99}],
            ).to_csv(cache / "history_000001_20260710_260.csv", index=False)
            dates = pd.date_range("2022-01-01", "2026-07-10", freq="D")
            pd.DataFrame(
                {
                    "日期": dates.strftime("%Y-%m-%d"),
                    "开盘": 10.0,
                    "最高": 11.0,
                    "最低": 9.0,
                    "收盘": 10.5,
                    "成交额": 1_000.0,
                }
            ).to_csv(cache / "history_000001_20260710_1098.csv", index=False)

            payload = build_dashboard_instrument_data(
                repo_root=root,
                market="a_share",
                agent="codex",
                code="000001.SZ",
            )

        self.assertGreater(len(payload["candles"]), 260)
        self.assertEqual(payload["candles"][0]["date"], "2023-07-10")
        self.assertEqual(payload["candles"][-1]["date"], "2026-07-10")
        self.assertEqual(payload["latest"]["close"], 10.5)

    def test_missing_instrument_history_returns_readable_empty_state(self) -> None:
        from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            payload = build_dashboard_instrument_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                code="513100.SH",
            )

        self.assertEqual(payload["candles"], [])
        self.assertEqual(payload["warning"], "暂无可用的历史行情缓存")

    def test_invalid_instrument_code_is_rejected(self) -> None:
        from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data
        from stock_analyze.dashboard_finance import InvalidInstrumentCode

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            with self.assertRaises(InvalidInstrumentCode):
                build_dashboard_instrument_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="codex",
                    code="../../etc/passwd",
                )

    def test_summary_total_counts_all_rows_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            trades_path = root / "data" / "cn_qdii_etf" / "codex" / "trades.csv"
            trades = pd.read_csv(trades_path, dtype={"code": str})
            trades = pd.concat(
                [trades, trades.assign(code="159941.SZ"), trades.assign(code="159920.SZ")],
                ignore_index=True,
            )
            trades.to_csv(trades_path, index=False)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                limit=2,
            )

        self.assertEqual(payload["trades"]["summary"]["total"], 3)
        self.assertEqual(len(payload["trades"]["rows"]), 2)

    def test_runs_collapse_running_and_success_rows_by_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            runs_path = root / "data" / "cn_qdii_etf" / "codex" / "runs.csv"
            runs = pd.read_csv(runs_path, dtype=str, keep_default_na=False)
            success = runs.iloc[0].to_dict()
            running = {
                **success,
                "finished_at": "",
                "duration_ms": "",
                "status": "running",
            }
            pd.DataFrame([running, success]).to_csv(runs_path, index=False)

            payload = build_dashboard_detail_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["runs"]["summary"]["total"], 1)
        self.assertEqual(payload["runs"]["rows"][0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
