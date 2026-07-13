from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from stock_analyze.dashboard_aggregator import build_dashboard_summary_data
from stock_analyze.dashboard_api import (
    build_dashboard_operations_data,
    build_dashboard_overview_data,
    build_dashboard_performance_data,
    build_dashboard_portfolio_data,
    build_dashboard_predictions_data,
    build_dashboard_research_data,
)


def _seed_repo(root: Path) -> None:
    config_dir = root / "configs" / "agents"
    config_dir.mkdir(parents=True)
    (config_dir / "codex_cn_qdii_etf.yaml").write_text(
        json.dumps(
            {
                "agent_id": "codex",
                "strategy_id": "codex_qdii_v1",
                "name": "趋势进攻",
                "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
            }
        ),
        encoding="utf-8",
    )
    data_dir = root / "data" / "cn_qdii_etf" / "codex"
    reports_dir = root / "reports" / "cn_qdii_etf" / "codex"
    cache_dir = root / "data" / "cn_qdii_etf" / "shared" / "cache"
    data_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    pd.DataFrame([{"ts_code": "513100.SH", "name": "纳指ETF"}]).to_csv(
        cache_dir / "fund_basic_E.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "account_id": "us_exposure",
                "cash": 1_000_000,
                "market_value": 0,
                "total_value": 1_000_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.0,
            },
            {
                "date": "2026-07-10",
                "account_id": "us_exposure",
                "cash": 900_000,
                "market_value": 110_000,
                "total_value": 1_010_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.1,
            },
        ]
    ).to_csv(data_dir / "daily_nav.csv", index=False)
    pd.DataFrame(
        [
            {
                "run_id": "daily-1",
                "command": "run-daily",
                "started_at": "2026-07-10T18:30:00",
                "finished_at": "2026-07-10T18:30:02",
                "status": "success",
                "duration_ms": 2000,
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
                    "delta_shares": 100,
                    "target_value": 110,
                    "trade_date": "2026-07-13",
                    "run_id": "run-internal",
                    "strategy_id": "strategy-internal",
                    "warnings": [f"internal-warning-{index}" for index in range(100)],
                }
            ]
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "account_id": "us_exposure",
                "code": "513100.SH",
                "shares": 100,
                "market_value": 110,
            }
        ]
    ).to_csv(data_dir / "positions.csv", index=False)
    pd.DataFrame(
        [
            {
                "trade_date": "2026-07-10",
                "account_id": "us_exposure",
                "code": "513100.SH",
                "side": "buy",
                "shares": 100,
                "price": 1.1,
                "gross_amount": 110,
            }
        ]
    ).to_csv(data_dir / "trades.csv", index=False)
    (reports_dir / "weekly_report.md").write_text("# 周报\n\n测试内容", encoding="utf-8")

    prediction_dir = data_dir / "predictions"
    prediction_dir.mkdir()
    rows = []
    for horizon in (3, 5):
        for index in range(7):
            rows.append(
                {
                    "as_of": "2026-07-10",
                    "code": str(513100 + index),
                    "horizon": horizon,
                    "p_up": 0.50 + index / 100,
                    "p_flat": 0.30,
                    "p_down": 0.20 - index / 100,
                    "confidence": 0.60 + index / 100,
                }
            )
    pd.DataFrame(rows).to_parquet(prediction_dir / "20260710.parquet", index=False)


class DashboardResourceApiTests(unittest.TestCase):
    def test_summary_does_not_build_legacy_full_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            with mock.patch(
                "stock_analyze.dashboard_aggregator.build_dashboard_detail_data",
                side_effect=AssertionError("legacy detail must not be used"),
            ):
                payload = build_dashboard_summary_data(
                    repo_root=root,
                    markets=["cn_qdii_etf"],
                    agents=["codex"],
                )

        self.assertEqual(payload["markets"][0]["agents"][0]["agent"], "codex")

    def test_resources_have_single_domain_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            kwargs = {"repo_root": root, "market": "cn_qdii_etf", "agent": "codex"}
            overview = build_dashboard_overview_data(**kwargs)
            performance = build_dashboard_performance_data(**kwargs)
            portfolio = build_dashboard_portfolio_data(**kwargs)
            predictions = build_dashboard_predictions_data(**kwargs, limit_per_horizon=3)
            research = build_dashboard_research_data(**kwargs)
            operations = build_dashboard_operations_data(**kwargs)

        self.assertEqual(
            set(overview),
            {"generated_at", "market", "market_label", "currency", "agent", "strategy", "latest_nav"},
        )
        self.assertEqual(set(performance), {"generated_at", "market", "agent", "nav"})
        self.assertEqual(
            set(portfolio),
            {"generated_at", "market", "agent", "activity", "orders", "positions", "trades"},
        )
        self.assertEqual(
            set(predictions),
            {
                "generated_at",
                "market",
                "agent",
                "prediction_summary",
                "alerts",
                "regimes",
                "model_health",
                "source_health",
            },
        )
        self.assertEqual(
            set(research),
            {"generated_at", "market", "agent", "selection", "lookthrough", "research"},
        )
        self.assertEqual(
            set(operations),
            {"generated_at", "market", "agent", "runs", "weekly_report"},
        )

    def test_predictions_are_bounded_per_horizon_and_report_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_predictions_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                limit_per_horizon=3,
            )

        summary = payload["prediction_summary"]
        self.assertEqual(summary["total"], 14)
        self.assertEqual(len(summary["rows"]), 6)
        self.assertEqual(
            {horizon: sum(row["horizon"] == horizon for row in summary["rows"]) for horizon in (3, 5)},
            {3: 3, 5: 3},
        )
        self.assertAlmostEqual(summary["rows"][0]["confidence"], 0.66)

    def test_prediction_limit_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_predictions_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
                limit_per_horizon=0,
            )

        counts = {
            horizon: sum(row["horizon"] == horizon for row in payload["prediction_summary"]["rows"])
            for horizon in (3, 5)
        }
        self.assertEqual(counts, {3: 1, 5: 1})

    def test_portfolio_projects_runtime_rows_to_public_dto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            payload = build_dashboard_portfolio_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        order = payload["orders"]["rows"][0]
        activity = payload["activity"]["rows"][0]
        self.assertEqual(order["shares"], 100)
        self.assertNotIn("warnings", order)
        self.assertNotIn("run_id", order)
        self.assertNotIn("strategy_id", order)
        self.assertNotIn("warnings", activity)

    def test_research_does_not_depend_on_trade_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            trades_path = root / "data" / "cn_qdii_etf" / "codex" / "trades.csv"
            trades_path.write_text("broken\nvalue\n", encoding="utf-8")

            payload = build_dashboard_research_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent="codex",
            )

        self.assertEqual(payload["lookthrough"]["source"], "positions")


if __name__ == "__main__":
    unittest.main()
