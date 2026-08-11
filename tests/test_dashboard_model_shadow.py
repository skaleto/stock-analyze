from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.competition import UnknownAgent
from stock_analyze.dashboard_aggregator import build_dashboard_instrument_data
from stock_analyze.dashboard_api import (
    build_dashboard_operations_data,
    build_dashboard_overview_data,
    build_dashboard_portfolio_data,
    build_dashboard_predictions_data,
)
from stock_analyze.model_shadow import MODEL_SHADOW_AGENT


def _seed_shadow_repo(root: Path) -> None:
    (root / "configs").mkdir(parents=True)
    source_config = Path(__file__).resolve().parents[1] / "configs" / "model_shadow.json"
    (root / "configs" / "model_shadow.json").write_text(
        source_config.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    model_root = root / "data" / "research" / "models" / "cn_qdii_etf" / "5"
    model_root.mkdir(parents=True)
    (model_root / "registry.json").write_text(json.dumps({
        "champion_model_version": "model-v2",
        "models": {
            "model-v2": {
                "status": "active",
                "registered_at": "2026-07-10T18:00:00+08:00",
            },
            "model-v3": {
                "status": "shadow",
                "registered_at": "2026-07-17T18:00:00+08:00",
            },
        },
    }), encoding="utf-8")
    (model_root / "shadow_cycles.json").write_text(json.dumps({
        "version": 1,
        "models": {"model-v3": {"cycles": [
            {"week": "2026-W28", "as_of": "2026-07-10", "metrics": {}},
            {"week": "2026-W29", "as_of": "2026-07-17", "metrics": {}},
        ]}},
    }), encoding="utf-8")
    lifecycle_root = root / "data" / "model_iterations" / "cn_qdii_etf" / "5"
    lifecycle_root.mkdir(parents=True)
    (lifecycle_root / "iteration_state.json").write_text(json.dumps({
        "schema_version": 1,
        "market": "cn_qdii_etf",
        "horizon": 5,
        "current_candidate": {
            "model_version": "model-v3",
            "display_version": "Q5-V002",
            "status": "shadow",
            "selected_at": "2026-07-17",
        },
        "history": [{
            "model_version": "model-v1",
            "display_version": "Q5-V000",
            "outcome": "retired",
            "ended_at": "2026-07-10",
        }],
    }), encoding="utf-8")
    data_dir = lifecycle_root / "model-v3"
    data_dir.mkdir(parents=True)
    runtime_status = {
        "schema_version": 2,
        "status": "complete",
        "market": "cn_qdii_etf",
        "label": "模型迭代",
        "portfolio_label": "候选模型模拟组合",
        "isolation": "完全隔离，不计入双策略竞赛",
        "source_agent": "codex",
        "as_of": "2026-07-17",
        "prediction_as_of": "2026-07-17",
        "horizon": 5,
        "model_version": "model-v3",
        "display_version": "Q5-V002",
        "lifecycle_status": "shadow",
        "model_versions": ["model-v3"],
        "eligible_rows": 21,
        "selected_count": 1,
        "cash_only": False,
        "pending_orders": 1,
        "trades_executed": 1,
        "updated_at": "2026-07-17T18:00:00",
    }
    (data_dir / "shadow_status.json").write_text(
        json.dumps(
            runtime_status,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (lifecycle_root / "current_status.json").write_text(
        json.dumps(runtime_status, ensure_ascii=False),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "account_id": "model_shadow",
                "cash": 1_000_000,
                "market_value": 0,
                "total_value": 1_000_000,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.2,
                "benchmark_date": "2026-07-16",
                "notes": "",
            },
            {
                "date": "2026-07-17",
                "account_id": "model_shadow",
                "cash": 799_900,
                "market_value": 201_000,
                "total_value": 1_000_900,
                "benchmark_code": "513100.SH",
                "benchmark_close": 1.22,
                "benchmark_date": "2026-07-17",
                "notes": "model shadow",
            },
        ]
    ).to_csv(data_dir / "daily_nav.csv", index=False)
    (data_dir / "pending_orders.json").write_text(
        json.dumps(
            [
                {
                    "account_id": "model_shadow",
                    "code": "513100",
                    "name": "",
                    "side": "buy",
                    "shares": 100,
                    "target_value": 200,
                    "target_weight": 0.2,
                    "trade_date": "2026-07-20",
                    "score": 0.84,
                    "reason": "模型5日：上涨60%",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "account_id": "model_shadow",
                "code": "513100",
                "name": "",
                "industry": "模型组合",
                "shares": 1000,
                "available_shares": 1000,
                "avg_cost": 2.0,
                "last_price": 2.01,
                "market_value": 2010,
                "unrealized_pnl": 10,
                "score": 0.84,
                "updated_at": "2026-07-17T18:00:00",
            }
        ]
    ).to_csv(data_dir / "positions.csv", index=False)
    pd.DataFrame(
        [
            {
                "trade_date": "2026-07-17",
                "account_id": "model_shadow",
                "code": "513100",
                "name": "",
                "side": "buy",
                "shares": 1000,
                "price": 2.0,
                "gross_amount": 2000,
                "commission": 0.6,
                "stamp_tax": 0,
                "slippage": 1.0,
                "net_amount": -2000.6,
                "cash_after": 997999.4,
                "reason": "模型5日：上涨60%",
            }
        ]
    ).to_csv(data_dir / "trades.csv", index=False)
    pd.DataFrame(
        [
            {
                "run_id": "run-model-iteration-1",
                "command": "run-model-iteration",
                "as_of": "2026-07-17",
                "started_at": "2026-07-17T18:00:00",
                "finished_at": "2026-07-17T18:00:02",
                "duration_ms": 2000,
                "status": "success",
                "error_summary": "",
                "config_hash": "shadow123",
                "code_version": "test",
            }
        ]
    ).to_csv(data_dir / "runs.csv", index=False)

    shared = root / "data" / "cn_qdii_etf" / "shared" / "cache"
    shared.mkdir(parents=True)
    pd.DataFrame([{"ts_code": "513100.SH", "name": "纳指ETF"}]).to_csv(
        shared / "fund_basic_E_v2.csv", index=False
    )
    prediction_dir = (
        root
        / "data"
        / "research"
        / "iteration_predictions"
        / "cn_qdii_etf"
        / "5"
        / "model-v3"
    )
    prediction_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "as_of": "2026-07-17",
                "code": "513100",
                "horizon": 5,
                "p_up": 0.60,
                "p_flat": 0.20,
                "p_down": 0.20,
                "confidence": 0.70,
                "expected_excess_return": 0.04,
                "model_version": "model-v3",
                "invalidated": False,
            }
        ]
    ).to_parquet(prediction_dir / "20260717.parquet", index=False)


class DashboardModelShadowTests(unittest.TestCase):
    def test_overview_exposes_virtual_identity_and_model_decision_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_shadow_repo(root)

            payload = build_dashboard_overview_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent=MODEL_SHADOW_AGENT,
            )

        self.assertEqual(payload["agent"], MODEL_SHADOW_AGENT)
        self.assertEqual(payload["strategy"]["agent_label"], "模型迭代")
        self.assertEqual(payload["model_iteration"]["horizon"], 5)
        self.assertEqual(payload["model_iteration"]["prediction_as_of"], "2026-07-17")
        self.assertEqual(payload["model_iteration"]["candidate"]["display_version"], "Q5-V002")
        self.assertEqual(payload["model_iteration"]["candidate"]["shadow_cycles"], 2)
        self.assertEqual(payload["model_iteration"]["champion"]["model_version"], "model-v2")
        self.assertEqual(payload["model_iteration"]["version_history"][0]["model_version"], "model-v1")
        self.assertIn("完全隔离", payload["model_iteration"]["isolation"])
        self.assertEqual(payload["model_shadow"], payload["model_iteration"])
        self.assertEqual(payload["latest_nav"]["date"], "2026-07-17")

    def test_split_resources_use_shadow_runtime_and_codex_prediction_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_shadow_repo(root)

            portfolio = build_dashboard_portfolio_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent=MODEL_SHADOW_AGENT,
            )
            predictions = build_dashboard_predictions_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent=MODEL_SHADOW_AGENT,
            )
            operations = build_dashboard_operations_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent=MODEL_SHADOW_AGENT,
            )

        self.assertEqual(portfolio["orders"]["rows"][0]["name"], "纳指ETF")
        self.assertEqual(portfolio["positions"]["rows"][0]["name"], "纳指ETF")
        self.assertEqual(portfolio["trades"]["rows"][0]["name"], "纳指ETF")
        self.assertEqual(predictions["agent"], MODEL_SHADOW_AGENT)
        self.assertEqual(predictions["prediction_summary"]["rows"][0]["code"], "513100")
        self.assertEqual(operations["runs"]["rows"][0]["command"], "run-model-iteration")

    def test_instrument_uses_shadow_trades_and_source_prediction_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_shadow_repo(root)

            payload = build_dashboard_instrument_data(
                repo_root=root,
                market="cn_qdii_etf",
                agent=MODEL_SHADOW_AGENT,
                code="513100",
            )

        self.assertEqual(payload["agent"], MODEL_SHADOW_AGENT)
        self.assertEqual(payload["related_trades"][0]["account_id"], "model_shadow")
        self.assertEqual(payload["related_trades"][0]["side_label"], "买入")
        self.assertEqual(payload["predictions"][0]["model_version"], "model-v3")

    def test_unknown_non_virtual_agent_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_shadow_repo(root)

            with self.assertRaises(UnknownAgent):
                build_dashboard_overview_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                    agent="not-an-agent",
                )


if __name__ == "__main__":
    unittest.main()
