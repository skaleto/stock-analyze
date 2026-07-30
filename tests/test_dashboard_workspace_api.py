"""Contract tests for the bounded model-research dashboard resource."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd

from stock_analyze import competition
from stock_analyze import dashboard_aggregator as agg
from stock_analyze.dashboard_workspace_api import (
    FORMAL_FACTOR_SOURCES,
    _bounded_intelligence_lane,
    _operations_timestamp,
    _sanitize_run_error,
    _structured_snapshot_coverage,
    build_dashboard_data_intelligence_data,
    build_dashboard_model_research_data,
    build_dashboard_operations_center_data,
)
from stock_analyze.overlay_guard import (
    AVAILABLE_FACTORS_BY_MARKET,
    SENTIMENT_FACTORS,
)
from stock_analyze.research.feature_registry import DEFAULT_REGISTRY


def _model(
    version: str = "A20-V005",
    *,
    features: list[str] | None = None,
    champion: bool = False,
) -> dict:
    return {
        "model_version": version,
        "horizon": 20,
        "sample_support": 4200,
        "feature_columns": features
        if features is not None
        else ["momentum_20", "event_net_strength_5d"],
        "trained_at": "2026-07-29T23:00:00",
        "metrics": {
            "candidate_feature_count": 72,
            "point_in_time_audit": True,
            "rank_ic": 0.021,
            "brier_score": 0.61,
        },
        "gate_passed": False,
        "gate_reasons": ["rank_ic_below_floor"],
        "shadow_cycles": 0,
        "shadow_cycles_remaining": 12,
        "is_champion": champion,
    }


def _iteration(**overrides: object) -> dict:
    payload = {
        "status": "available",
        "candidate": {
            "model_version": "A20-V005",
            "display_version": "A20-V005",
            "shadow_cycles": 0,
            "shadow_cycles_remaining": 12,
        },
        "champion": None,
        "candidate_rows": 31,
        "eligible_rows": 0,
        "selected_count": 0,
        "cash_only": True,
        "cash_reason": "probability_gate_not_met",
    }
    payload.update(overrides)
    return payload


def _intelligence(**overrides: object) -> dict:
    payload = {
        "pipeline": {
            "status": "available",
            "documents": 584598,
            "stages": {
                "catalogued": 584598,
                "pdfReady": 23243,
                "parsed": 6888,
                "semanticCompleted": 35,
                "canonicalEvents": 12,
            },
            "backlog": {
                "download": 561355,
                "parse": 16355,
                "semantic": 6853,
                "total": 584563,
            },
            "sources": [],
            "artifactWorkers": {"status": "available"},
        },
        "extraction": {
            "status": "available",
            "semanticRuns": {"succeeded": 35},
            "decisions": {
                "canonical": 12,
                "no_event": 20,
                "quarantined": 2,
                "failed": 1,
            },
            "latestBatch": None,
            "contract": {"profileId": "a-share-announcement-v1"},
        },
        "factorSupply": {
            "status": "available",
            "suppliedFactors": 23,
            "modelEligible": False,
            "modelEligibleFactors": [],
            "factors": [],
        },
        "modelImpact": {
            "status": "available",
            "adopted": False,
            "activeFactors": [],
            "iterationFactors": [],
            "reason": "no_factor_passed_gate",
        },
        "decisions": {
            "canonical": 12,
            "no_event": 20,
            "quarantined": 2,
            "failed": 1,
        },
        "rowsByDecision": {"canonical": [{"raw": "never expose"}]},
    }
    payload.update(overrides)
    return payload


class DashboardWorkspaceApiTests(unittest.TestCase):
    def _build(
        self,
        root: Path,
        *,
        models: object,
        iteration: object | None = None,
        sources: object | None = None,
        usage: object | None = None,
    ) -> dict:
        with mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value=models,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration() if iteration is None else iteration,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            return_value=(
                [{"source": "market", "status": "available", "rows": 1000}]
                if sources is None
                else sources
            ),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[] if usage is None else usage,
        ):
            return build_dashboard_model_research_data(
                repo_root=root,
                market="a_share",
            )

    def test_model_resource_keeps_simulation_when_model_health_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            side_effect=agg.DashboardDataError("model_health"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            return_value=[],
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_model_research_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertEqual(payload["training"]["models"], [])
        self.assertEqual(
            payload["simulation"]["candidate"]["model_version"],
            "A20-V005",
        )
        self.assertEqual(
            payload["errors"],
            [{"resource": "model_health", "reason": "unavailable"}],
        )
        stage_statuses = {
            row["key"]: row["status"] for row in payload["stages"]
        }
        self.assertEqual(stage_statuses["training"], "unavailable")
        self.assertEqual(stage_statuses["validation"], "unavailable")
        self.assertEqual(stage_statuses["adoption"], "unavailable")

    def test_explicit_unavailable_model_health_stays_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "unavailable", "models": []},
            )

        stage_statuses = {
            row["key"]: row["status"] for row in payload["stages"]
        }
        self.assertEqual(stage_statuses["training"], "unavailable")
        self.assertEqual(stage_statuses["validation"], "unavailable")
        self.assertEqual(stage_statuses["adoption"], "unavailable")

    def test_explicit_unavailable_iteration_marks_simulation_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                iteration={
                    "status": "unavailable",
                    "candidate": None,
                    "champion": None,
                },
            )

        simulation_stage = next(
            row for row in payload["stages"] if row["key"] == "simulation"
        )
        self.assertEqual(simulation_stage["status"], "unavailable")
        self.assertEqual(payload["simulation"]["status"], "unavailable")
        self.assertEqual(payload["errors"], [])

    def test_source_health_errors_are_replaced_with_stable_codes(self) -> None:
        sensitive_path = "/opt/stock-analyze/secrets/provider.env"
        sensitive_key = "DEEPSEEK_API_KEY=plainsecretvalue123456"
        sensitive_endpoint = "https://user:password@api.internal.example/v1"
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                sources=[
                    {
                        "source": "tushare",
                        "status": "failed",
                        "failed": True,
                        "error": (
                            f"{sensitive_path}: {sensitive_key}; "
                            f"endpoint={sensitive_endpoint}"
                        ),
                    },
                    {
                        "source": "ifind",
                        "status": "source_unavailable",
                        "error_summary": "endpoint=10.0.0.8:9443 timeout",
                    },
                ],
            )

        self.assertEqual(
            {
                row["error"] for row in payload["dataPreparation"]["sources"]
            },
            {"数据源状态读取失败"},
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            sensitive_path,
            sensitive_key,
            sensitive_endpoint,
            "10.0.0.8:9443",
        ):
            self.assertNotIn(secret, serialized)

    def test_model_resource_keeps_training_when_other_sections_fail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            side_effect=OSError("iteration path must not leak"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            side_effect=ValueError("source payload must not leak"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            side_effect=OSError("lineage path must not leak"),
        ):
            payload = build_dashboard_model_research_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertEqual(
            payload["training"]["models"][0]["modelVersion"],
            "A20-V005",
        )
        self.assertEqual(payload["dataPreparation"]["sources"], [])
        self.assertEqual(payload["simulation"]["status"], "unavailable")
        simulation_stage = next(
            row for row in payload["stages"] if row["key"] == "simulation"
        )
        self.assertEqual(simulation_stage["status"], "unavailable")
        self.assertEqual(payload["adoption"]["strategyUsage"], [])
        adoption_stage = next(
            row for row in payload["stages"] if row["key"] == "adoption"
        )
        self.assertEqual(adoption_stage["status"], "unavailable")
        self.assertEqual(
            payload["errors"],
            [
                {"resource": "source_health", "reason": "unavailable"},
                {"resource": "model_iteration", "reason": "unavailable"},
                {"resource": "strategy_model_usage", "reason": "unavailable"},
            ],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("must not leak", serialized)

    def test_explicit_unavailable_strategy_usage_stays_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                usage={"status": "unavailable", "rows": []},
            )

        adoption_stage = next(
            row for row in payload["stages"] if row["key"] == "adoption"
        )
        self.assertEqual(adoption_stage["status"], "unavailable")
        self.assertEqual(payload["adoption"]["strategyUsage"], [])

    def test_data_resource_keeps_structured_lane_when_intelligence_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            side_effect=agg.DashboardDataError("intelligence"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertTrue(payload["structured"]["sources"])
        self.assertEqual(
            payload["intelligence"]["pipeline"]["status"],
            "unavailable",
        )
        self.assertEqual(
            {row["status"] for row in payload["intelligence"]["stages"]},
            {"unavailable"},
        )
        self.assertEqual(
            payload["errors"],
            [{"resource": "intelligence", "reason": "unavailable"}],
        )

    def test_data_resource_keeps_intelligence_when_model_sections_fail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            side_effect=OSError("health path must not leak"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            side_effect=TypeError("iteration detail must not leak"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            side_effect=ValueError("usage detail must not leak"),
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        self.assertEqual(
            payload["intelligence"]["pipeline"]["documents"],
            584598,
        )
        self.assertTrue(payload["structured"]["sources"])
        formal_consumers = [
            row
            for row in payload["usageMatrix"]
            if row["consumerKey"] in {"defensive", "trend"}
        ]
        self.assertEqual(
            {row["modelAdoption"]["status"] for row in formal_consumers},
            {"unavailable"},
        )
        research_consumers = [
            row
            for row in payload["usageMatrix"]
            if row["consumerKey"] in {"research_model", "candidate_simulation"}
        ]
        self.assertEqual(
            {
                cell["status"]
                for row in research_consumers
                for cell in (
                    row["structuredData"],
                    row["traditionalFactors"],
                    row["intelligenceFactors"],
                )
            },
            {"unavailable"},
        )
        self.assertEqual(
            payload["errors"],
            [
                {"resource": "model_health", "reason": "unavailable"},
                {"resource": "model_iteration", "reason": "unavailable"},
                {"resource": "strategy_model_usage", "reason": "unavailable"},
            ],
        )
        self.assertNotIn(
            "must not leak",
            json.dumps(payload, ensure_ascii=False),
        )

    def test_data_resource_preserves_explicit_unavailable_strategy_usage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={
                "defensive": {
                    "label": "稳健防守",
                    "factors": ["momentum_20"],
                },
                "trend": {
                    "label": "趋势进攻",
                    "factors": ["momentum_20"],
                },
            },
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value={"status": "unavailable", "rows": []},
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        formal_consumers = [
            row
            for row in payload["usageMatrix"]
            if row["consumerKey"] in {"defensive", "trend"}
        ]
        self.assertEqual(
            {row["modelAdoption"]["status"] for row in formal_consumers},
            {"unavailable"},
        )
        self.assertEqual(
            {
                cell["researchStatus"]
                for row in formal_consumers
                for cell in (
                    row["structuredData"],
                    row["traditionalFactors"],
                    row["intelligenceFactors"],
                )
            },
            {"unavailable"},
        )

    def test_data_resource_redacts_intelligence_source_errors(self) -> None:
        intelligence = _intelligence()
        intelligence["pipeline"]["sources"] = [
            {
                "source": "tushare_anns_d",
                "status": "failed",
                "error": (
                    "/opt/stock-analyze/secrets/intelligence.env: "
                    "DEEPSEEK_API_KEY=plainsecretvalue123456; "
                    "endpoint=https://user:password@api.internal.example/v1"
                ),
            }
        ]
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=intelligence,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        source = payload["intelligence"]["pipeline"]["sources"][0]
        self.assertEqual(source["error"], "情报采集状态读取失败")
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            "/opt/stock-analyze/secrets/intelligence.env",
            "DEEPSEEK_API_KEY=plainsecretvalue123456",
            "https://user:password@api.internal.example/v1",
        ):
            self.assertNotIn(secret, serialized)

    def test_data_resource_marks_unavailable_candidate_simulation_usage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
            return_value={},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value={
                "status": "unavailable",
                "candidate": None,
                "champion": None,
            },
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ):
            payload = build_dashboard_data_intelligence_data(
                repo_root=Path(tmp),
                market="a_share",
            )

        candidate = next(
            row
            for row in payload["usageMatrix"]
            if row["consumerKey"] == "candidate_simulation"
        )
        for cell in (
            candidate["structuredData"],
            candidate["traditionalFactors"],
            candidate["intelligenceFactors"],
        ):
            self.assertEqual(cell["status"], "unavailable")
            self.assertEqual(cell["researchStatus"], "unavailable")
            self.assertEqual(
                cell["missingManifestEvidence"],
                ["model_iteration:unavailable"],
            )
        self.assertEqual(candidate["impact"], "候选模拟状态不可用")

    def test_operations_resource_keeps_runtime_when_intelligence_is_unreadable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=self._operations_scope_runtime(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            side_effect=OSError("database path must not leak"),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(2026, 7, 30, 13, 30),
            )

        self.assertEqual(payload["runtime"]["status"], "available")
        self.assertTrue(payload["mainChain"])
        self.assertEqual(payload["background"]["status"], "unavailable")
        self.assertEqual(
            payload["errors"],
            [{"resource": "intelligence", "reason": "unavailable"}],
        )
        self.assertNotIn(
            "database path",
            json.dumps(payload, ensure_ascii=False),
        )

    def test_workspace_read_does_not_hide_unexpected_programming_errors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            side_effect=RuntimeError("unexpected programming error"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "unexpected programming error",
            ):
                build_dashboard_model_research_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

    def test_workspace_payloads_remain_bounded(self) -> None:
        runtime = self._operations_scope_runtime()
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_health",
            return_value={"status": "available", "models": [_model()]},
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
            return_value=_iteration(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.agg._read_research_source_health",
            return_value=[],
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
            return_value=[],
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ):
            root = Path(tmp)
            payloads = (
                build_dashboard_model_research_data(
                    repo_root=root,
                    market="a_share",
                ),
                build_dashboard_data_intelligence_data(
                    repo_root=root,
                    market="a_share",
                ),
                build_dashboard_operations_center_data(
                    repo_root=root,
                    scope="all",
                    now=datetime(2026, 7, 30, 13, 30),
                ),
            )

        def assert_bounded_lists(value: object) -> None:
            if isinstance(value, list):
                self.assertLessEqual(len(value), 20)
                for item in value:
                    assert_bounded_lists(item)
            elif isinstance(value, dict):
                for item in value.values():
                    assert_bounded_lists(item)

        for payload in payloads:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.assertLess(len(encoded), 250_000)
            assert_bounded_lists(payload)

    def test_release_gates_cover_workspace_runtime_and_live_canaries(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        deploy = (root / "scripts" / "deploy-app-to-ecs.sh").read_text(
            encoding="utf-8"
        )
        audit = (root / "scripts" / "system-audit.sh").read_text(
            encoding="utf-8"
        )

        for module in (
            "tests.test_dashboard_workspace_api",
            "tests.test_dashboard_runtime",
        ):
            self.assertIn(module, deploy)
            self.assertIn(module, audit)
        for endpoint in (
            "/api/dashboard/model-research.json?market=a_share",
            "/api/dashboard/data-intelligence.json?market=a_share",
            "/api/dashboard/operations-center.json?scope=all",
        ):
            self.assertIn(endpoint, audit)

    def test_operator_docs_cover_five_workspace_runtime_contract(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        overview = (root / "docs" / "system-overview.md").read_text(
            encoding="utf-8"
        )
        harness = (root / "docs" / "system-harness.md").read_text(
            encoding="utf-8"
        )

        for workspace in (
            "决策总览",
            "策略工作台",
            "模型研究",
            "数据与情报",
            "运行中心",
        ):
            self.assertIn(workspace, overview)
        self.assertIn("Dashboard Workspace Runtime Contract", harness)
        self.assertIn("250 KB", harness)
        self.assertIn("20", harness)

    def test_reports_five_evidence_backed_stages_and_explicit_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
            )

        self.assertEqual(
            [item["key"] for item in payload["stages"]],
            ["data", "training", "validation", "simulation", "adoption"],
        )
        self.assertEqual(payload["dataPreparation"]["candidateFeatureCount"], 72)
        self.assertEqual(payload["dataPreparation"]["selectedFeatureCount"], 2)
        self.assertEqual(payload["dataPreparation"]["structuredFeatureCount"], 1)
        self.assertEqual(payload["dataPreparation"]["intelligenceFeatureCount"], 1)
        self.assertEqual(payload["validation"]["passed"], 0)
        self.assertEqual(
            payload["validation"]["models"][0]["gateReasons"],
            ["rank_ic_below_floor"],
        )
        self.assertEqual(payload["simulation"]["decision"]["selectedCount"], 0)
        self.assertTrue(payload["simulation"]["decision"]["cashOnly"])
        self.assertEqual(
            payload["simulation"]["decision"]["cashReason"],
            "probability_gate_not_met",
        )
        self.assertEqual(payload["adoption"]["champions"], [])
        self.assertEqual(payload["adoption"]["rollbackCandidates"], [])
        self.assertLess(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            250_000,
        )

    def test_simulation_account_is_bounded_iteration_status_evidence(self) -> None:
        long_text = "x" * 2_000
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                iteration=_iteration(
                    account_id=f"shadow-{long_text}",
                    portfolio_label=f"独立模拟账户-{long_text}",
                    label="不可覆盖 portfolio_label",
                    isolation=f"完全隔离-{long_text}",
                    nav_rows=17,
                    portfolio_path=f"data/model_shadow/{long_text}",
                ),
            )

        account = payload["simulation"]["account"]
        self.assertEqual(account["accountId"][:7], "shadow-")
        self.assertTrue(account["accountLabel"].startswith("独立模拟账户-"))
        self.assertEqual(account["isolation"][:5], "完全隔离-")
        self.assertEqual(account["navRows"], 17)
        self.assertEqual(account["portfolioRef"][:18], "data/model_shadow/")
        self.assertTrue(
            all(
                len(account[key]) <= 1_000
                for key in (
                    "accountId",
                    "accountLabel",
                    "isolation",
                    "portfolioRef",
                )
            )
        )

    def test_registry_dates_and_active_gate_evidence_remain_distinct(self) -> None:
        model = _model(champion=True)
        model.pop("trained_at")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V005.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": str(artifact),
                                "registered_at": "2026-07-30T08:30:00+08:00",
                                "gate_history": [
                                    {
                                        "passed": True,
                                        "target_status": "active",
                                        "evaluated_at": "2026-07-30T09:00:00+08:00",
                                    },
                                    {
                                        "passed": True,
                                        "target_status": "shadow",
                                        "evaluated_at": "2026-07-30T10:00:00+08:00",
                                    },
                                    {
                                        "passed": True,
                                        "target_status": "active",
                                        "evaluated_at": "2026-07-30T11:00:00+08:00",
                                    },
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = self._build(
                root,
                models={"status": "available", "models": [model]},
            )

        registered = payload["training"]["models"][0]
        champion = payload["adoption"]["champions"][0]
        self.assertIsNone(registered["trainedAt"])
        self.assertEqual(
            registered["registeredAt"],
            "2026-07-30T08:30:00+08:00",
        )
        self.assertEqual(
            champion["activatedAt"],
            "2026-07-30T11:00:00+08:00",
        )

    def test_champion_without_active_gate_has_no_activation_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V005.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": str(artifact),
                                "registered_at": "2026-07-30T08:30:00+08:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = self._build(
                root,
                models={
                    "status": "available",
                    "models": [_model(champion=True)],
                },
            )

        self.assertIsNone(payload["adoption"]["champions"][0]["activatedAt"])

    def test_unknown_market_is_rejected(self) -> None:
        with self.assertRaises(competition.UnknownMarket):
            build_dashboard_model_research_data(
                repo_root=Path("/tmp"),
                market="unknown",
            )

    def test_malformed_or_missing_registry_and_artifact_are_not_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text("{broken", encoding="utf-8")
            payload = self._build(
                root,
                models={
                    "status": "available",
                    "models": [_model(champion=True)],
                },
                usage=[
                    {
                        "market": "a_share",
                        "agent": "codex",
                        "status": "active",
                        "model_versions": {"20": "A20-V005"},
                    }
                ],
            )

        model = payload["training"]["models"][0]
        self.assertEqual(model["registryStatus"], "missing")
        self.assertEqual(model["artifactStatus"], "missing")
        self.assertIsNone(model["artifactRef"])
        self.assertEqual(payload["adoption"]["champions"], [])
        self.assertEqual(payload["adoption"]["strategyUsage"], [])
        self.assertEqual(payload["stages"][-1]["status"], "waiting_upstream")

    def test_registry_artifact_champion_adoption_and_rollback_are_evidenced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V005.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": str(artifact),
                                "registered_at": "2026-07-29T22:00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            usage = [
                {
                    "market": "a_share",
                    "agent": "codex",
                    "status": "active",
                    "as_of": datetime(2026, 7, 30, 8, 30),
                    "model_versions": {"20": "A20-V005"},
                },
                {
                    "market": "a_share",
                    "agent": "claude",
                    "status": "fallback",
                    "model_versions": {},
                },
            ]
            history = [
                {
                    "model_version": f"old-{index}",
                    "display_version": f"A20-V{index:03d}",
                    "outcome": "retired",
                    "ended_at": f"2026-07-{index + 1:02d}",
                }
                for index in range(8)
            ]
            payload = self._build(
                root,
                models={
                    "status": "available",
                    "models": [_model(champion=True)],
                },
                iteration=_iteration(version_history=history),
                usage=usage,
            )

        model = payload["training"]["models"][0]
        self.assertEqual(model["registryStatus"], "available")
        self.assertEqual(model["artifactStatus"], "available")
        self.assertEqual(
            model["artifactRef"],
            "data/research/models/a_share/20/run-A20-V005.joblib",
        )
        self.assertEqual(
            payload["adoption"]["champions"][0]["modelVersion"],
            "A20-V005",
        )
        self.assertEqual(len(payload["adoption"]["strategyUsage"]), 1)
        self.assertEqual(
            payload["adoption"]["strategyUsage"][0]["as_of"],
            "2026-07-30T08:30:00",
        )
        self.assertEqual(len(payload["adoption"]["rollbackCandidates"]), 5)
        self.assertEqual(payload["stages"][-1]["status"], "success")

    def test_external_registry_artifact_cannot_evidence_champion_adoption(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.NamedTemporaryFile() as external,
        ):
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": external.name,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = self._build(
                root,
                models={
                    "status": "available",
                    "models": [_model(champion=True)],
                },
                usage=[
                    {
                        "market": "a_share",
                        "agent": "codex",
                        "status": "active",
                        "model_versions": {"20": "A20-V005"},
                    }
                ],
            )

        model = payload["training"]["models"][0]
        self.assertEqual(model["artifactStatus"], "missing")
        self.assertIsNone(model["artifactRef"])
        self.assertEqual(payload["adoption"]["champions"], [])
        self.assertEqual(payload["adoption"]["strategyUsage"], [])
        self.assertEqual(payload["stages"][-1]["status"], "waiting_upstream")

    def test_adoption_requires_matching_champion_horizon_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V005.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": str(artifact),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            payload = self._build(
                root,
                models={
                    "status": "available",
                    "models": [_model(champion=True)],
                },
                usage=[
                    {
                        "market": "a_share",
                        "agent": "codex",
                        "status": "active",
                        "model_versions": {"5": "A20-V005"},
                    }
                ],
            )

        self.assertEqual(payload["adoption"]["strategyUsage"], [])
        self.assertEqual(payload["stages"][-1]["status"], "waiting_upstream")

    def test_datetime_sources_and_lifecycle_timestamps_are_iso_json_safe(self) -> None:
        class PandasLikeTimestamp:
            def isoformat(self) -> str:
                return "2026-07-30T09:45:00+08:00"

        trained_at = datetime(2026, 7, 29, 23, 0, tzinfo=timezone.utc)
        adopted_at = datetime(2026, 7, 30, 8, 30)
        history_at = date(2026, 7, 28)
        model = _model()
        model["trained_at"] = trained_at
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [model]},
                sources=[
                    {
                        "source": "market",
                        "status": "available",
                        "rows": 1000,
                        "as_of": PandasLikeTimestamp(),
                    }
                ],
                iteration=_iteration(
                    prediction_as_of=date(2026, 7, 30),
                    candidate={
                        "model_version": "A20-V005",
                        "selected_at": adopted_at,
                        "registered_at": PandasLikeTimestamp(),
                    },
                    version_history=[
                        {
                            "model_version": "A20-V004",
                            "ended_at": history_at,
                        }
                    ],
                ),
                usage=[
                    {
                        "market": "a_share",
                        "agent": "codex",
                        "status": "active",
                        "as_of": adopted_at,
                        "model_versions": {},
                    }
                ],
            )

        self.assertEqual(
            payload["training"]["models"][0]["trainedAt"],
            "2026-07-29T23:00:00+00:00",
        )
        self.assertEqual(
            payload["dataPreparation"]["sources"][0]["as_of"],
            "2026-07-30T09:45:00+08:00",
        )
        self.assertEqual(
            payload["simulation"]["candidate"]["selected_at"],
            "2026-07-30T08:30:00",
        )
        self.assertEqual(
            payload["simulation"]["candidate"]["registered_at"],
            "2026-07-30T09:45:00+08:00",
        )
        self.assertEqual(payload["simulation"]["predictionAsOf"], "2026-07-30")
        self.assertEqual(
            payload["adoption"]["rollbackCandidates"][0]["endedAt"],
            "2026-07-28",
        )
        json.dumps(payload, allow_nan=False)

    def test_selected_features_distinguish_structured_intelligence_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={
                    "status": "available",
                    "models": [
                        _model(
                            features=[
                                "momentum_20",
                                "event_net_strength_5d",
                                "future_feature_not_registered",
                            ]
                        )
                    ],
                },
            )

        preparation = payload["dataPreparation"]
        self.assertEqual(preparation["structuredFeatureCount"], 1)
        self.assertEqual(preparation["intelligenceFeatureCount"], 1)
        self.assertEqual(preparation["unclassifiedFeatureCount"], 1)
        self.assertEqual(
            preparation["unclassifiedFeatures"],
            ["future_feature_not_registered"],
        )

    def test_adversarial_decision_diagnostics_are_recursively_bounded(self) -> None:
        diagnostics = {
            f"branch-{index}": {
                "message": "x" * 10_000,
                "children": [
                    {"detail": "y" * 10_000, "values": list(range(1000))}
                    for _ in range(10)
                ],
            }
            for index in range(30)
        }
        self.assertGreater(
            len(json.dumps(diagnostics).encode("utf-8")),
            300_000,
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                iteration=_iteration(decision_diagnostics=diagnostics),
            )

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertLess(len(encoded), 250_000)
        bounded = payload["simulation"]["decision"]["diagnostics"]
        self.assertLess(len(bounded), len(diagnostics))
        self.assertLessEqual(len(bounded["branch-0"]["message"]), 1_000)
        self.assertLess(len(bounded["branch-0"]["children"]), 10)

    def test_initial_tables_are_bounded_and_payload_is_json_safe(self) -> None:
        models = [
            _model(
                f"A20-V{index:03d}",
                features=[f"feature_{index}_{part}" for part in range(50)],
            )
            for index in range(40)
        ]
        models[0]["metrics"]["rank_ic"] = math.nan
        sources = [
            {
                "source": f"source-{index}",
                "status": "available",
                "rows": index,
                "detail": "x" * 5000,
            }
            for index in range(40)
        ]
        usage = [
            {
                "market": "a_share",
                "agent": f"agent-{index}",
                "status": "active",
                "model_versions": {"20": f"A20-V{index:03d}"},
                "detail": "y" * 5000,
            }
            for index in range(40)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": models},
                sources=sources,
                usage=usage,
            )

        self.assertLessEqual(len(payload["dataPreparation"]["sources"]), 20)
        self.assertLessEqual(
            len(payload["dataPreparation"]["selectedFeatures"]),
            20,
        )
        self.assertLessEqual(len(payload["training"]["models"]), 20)
        self.assertLessEqual(len(payload["validation"]["models"]), 20)
        self.assertLessEqual(len(payload["adoption"]["strategyUsage"]), 20)
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
        self.assertLess(len(encoded), 250_000)

    def test_deduplicates_models_by_horizon_and_version_deterministically(self) -> None:
        first = _model()
        first["trained_at"] = "2026-07-30T01:00:00"
        first["sample_support"] = 4300
        first["algorithm_family"] = "z-latest-evidence"
        second = _model()
        second["trained_at"] = "2026-07-29T23:00:00"
        second["sample_support"] = 9999
        second["algorithm_family"] = "a-lexically-preferred"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forward = self._build(
                root,
                models={"status": "available", "models": [first, second]},
            )
            reverse = self._build(
                root,
                models={"status": "available", "models": [second, first]},
            )

        self.assertEqual(len(forward["training"]["models"]), 1)
        self.assertEqual(
            forward["training"]["models"],
            reverse["training"]["models"],
        )
        self.assertEqual(
            forward["training"]["models"][0]["trainedAt"],
            "2026-07-30T01:00:00",
        )
        self.assertEqual(forward["training"]["models"][0]["sampleSupport"], 4300)
        self.assertEqual(forward["validation"]["total"], 1)

    def test_deduplicates_only_identical_source_evidence_rows(self) -> None:
        evidence = {
            "source": "market",
            "status": "available",
            "rows": 1000,
            "failed": False,
            "as_of": "2026-07-30",
        }
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._build(
                Path(tmp),
                models={"status": "available", "models": [_model()]},
                sources=[
                    evidence,
                    dict(evidence),
                    {**evidence, "as_of": "2026-07-29"},
                ],
            )

        self.assertEqual(
            payload["dataPreparation"]["sources"],
            [
                {**evidence, "error": None},
                {**evidence, "as_of": "2026-07-29", "error": None},
            ],
        )

    def test_deduplicates_strategy_usage_by_public_agent_identity(self) -> None:
        latest_usage = {
            "market": "a_share",
            "agent": "codex",
            "strategy_label": "Codex public account",
            "as_of": "2026-07-30",
            "status": "active",
            "model_versions": {"20": "A20-V005"},
        }
        older_usage = {
            **latest_usage,
            "strategy_label": "Lexically earlier but stale",
            "as_of": "2026-07-29",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V005.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V005",
                        "models": {
                            "A20-V005": {
                                "status": "active",
                                "artifact": str(artifact),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            model = _model(champion=True)
            payload = self._build(
                root,
                models={"status": "available", "models": [model]},
                usage=[latest_usage, older_usage],
            )
            reversed_payload = self._build(
                root,
                models={"status": "available", "models": [model]},
                usage=[older_usage, latest_usage],
            )

        self.assertEqual(len(payload["adoption"]["strategyUsage"]), 1)
        self.assertEqual(
            payload["adoption"]["strategyUsage"][0]["agent"],
            "codex",
        )
        self.assertEqual(
            payload["adoption"]["strategyUsage"][0]["as_of"],
            "2026-07-30",
        )
        self.assertEqual(
            payload["adoption"]["strategyUsage"],
            reversed_payload["adoption"]["strategyUsage"],
        )

    def test_adversarial_scalars_are_sanitized_and_final_payload_is_pruned(self) -> None:
        models = []
        for index in range(20):
            model = _model(
                f"A20-V{index:03d}",
                features=[
                    f"feature-{index}-{part}-" + ("f" * 200)
                    for part in range(20)
                ],
            )
            model["metrics"] = {
                key: "m" * 300_000
                for key in (
                    "rank_ic",
                    "mean_rank_ic",
                    "icir",
                    "brier_score",
                    "auc",
                    "hit_rate_lift",
                    "net_excess_return",
                    "turnover",
                )
            }
            model["metrics"]["candidate_feature_count"] = math.inf
            model["metrics"]["point_in_time_audit"] = math.nan
            model["gate_reasons"] = ["g" * 300_000 for _ in range(20)]
            models.append(model)
        candidate = {
            "model_version": "v" * 300_000,
            "display_version": "d" * 300_000,
            "status": "s" * 300_000,
            "shadow_cycles": math.inf,
            "shadow_cycles_remaining": math.nan,
            "horizon": object(),
        }
        usage = [
            {
                "market": "a_share",
                "agent": "a" * 300_000,
                "strategy_label": "l" * 300_000,
                "status": "active",
                "model_versions": {"20": "A20-V000"},
                "applied_candidates": math.inf,
                "candidate_coverage": math.nan,
                "fallback_reason": "r" * 300_000,
                "accounts": object(),
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "data" / "research" / "models" / "a_share" / "20"
            model_root.mkdir(parents=True)
            artifact = model_root / "run-A20-V000.joblib"
            artifact.write_bytes(b"model")
            (model_root / "registry.json").write_text(
                json.dumps(
                    {
                        "champion_model_version": "A20-V000",
                        "models": {
                            "A20-V000": {
                                "status": "active",
                                "artifact": str(artifact),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            models[0]["is_champion"] = True
            payload = self._build(
                root,
                models={"status": "available", "models": models},
                iteration=_iteration(candidate=candidate),
                usage=usage,
            )

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        self.assertLess(len(encoded), 250_000)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["truncationReason"], "serialized_size_limit")
        self.assertEqual(
            [stage["key"] for stage in payload["stages"]],
            ["data", "training", "validation", "simulation", "adoption"],
        )
        self.assertEqual(payload["validation"]["total"], 20)
        self.assertEqual(payload["dataPreparation"]["selectedFeatureCount"], 400)
        first_model = payload["training"]["models"][0]
        self.assertEqual(first_model["candidateFeatureCount"], 20)
        self.assertIsNone(first_model["pointInTimeAudit"])
        self.assertLessEqual(len(first_model["metrics"]["rank_ic"]), 1_000)
        bounded_candidate = payload["simulation"]["candidate"]
        self.assertLessEqual(len(bounded_candidate["model_version"]), 256)
        self.assertEqual(bounded_candidate["shadow_cycles"], 0)
        self.assertIsNone(bounded_candidate["horizon"])
        bounded_usage = payload["adoption"]["strategyUsage"][0]
        self.assertEqual(bounded_usage["candidate_coverage"], 0.0)
        self.assertLessEqual(len(bounded_usage["fallback_reason"]), 1_000)

    def test_data_intelligence_usage_requires_explicit_consumption_evidence(
        self,
    ) -> None:
        profiles = {
            "defensive": {
                "label": "稳健防守",
                "factors": ["roe", "low_volatility_60"],
            },
            "trend": {
                "label": "趋势进攻",
                "factors": ["momentum_20"],
            },
        }
        model_health = {
            "status": "available",
            "models": [
                {
                    "model_version": "SHARED-V005",
                    "horizon": 5,
                    "feature_columns": ["event_negative_decay_5d"],
                    "metrics": {"point_in_time_audit": False},
                },
                {
                    "model_version": "SHARED-V005",
                    "horizon": 20,
                    "feature_columns": [
                        "momentum_20",
                        "event_net_strength_5d",
                    ],
                    "metrics": {"point_in_time_audit": True},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "data" / "research" / "features" / "a_share"
            snapshot_root.mkdir(parents=True)
            pd.DataFrame({"trade_date": ["20230711"]}).to_parquet(
                snapshot_root / "20230712.parquet",
                index=False,
            )
            pd.DataFrame({"trade_date": ["20260729"]}).to_parquet(
                snapshot_root / "20260729.parquet",
                index=False,
            )
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value=profiles,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value=model_health,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={
                    "candidate": {
                        "model_version": "SHARED-V005",
                        "horizon": 20,
                    },
                    "selected_count": 0,
                    "trades_executed": 0,
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=root,
                    market="a_share",
                )

        usage = {row["consumerKey"]: row for row in payload["usageMatrix"]}
        self.assertEqual(set(usage), {
            "defensive",
            "trend",
            "research_model",
            "candidate_simulation",
        })
        self.assertEqual(usage["defensive"]["traditionalFactors"]["count"], 2)
        self.assertEqual(usage["defensive"]["intelligenceFactors"]["count"], 0)
        self.assertEqual(
            usage["defensive"]["intelligenceFactors"]["status"],
            "not_used",
        )
        self.assertEqual(usage["research_model"]["intelligenceFactors"]["count"], 2)
        self.assertEqual(
            usage["research_model"]["intelligenceFactors"]["evidence"],
            [
                "model_feature_manifest:5:SHARED-V005",
                "model_feature_manifest:20:SHARED-V005",
            ],
        )
        self.assertEqual(
            usage["candidate_simulation"]["intelligenceFactors"]["features"],
            ["event_net_strength_5d"],
        )
        self.assertEqual(
            usage["candidate_simulation"]["intelligenceFactors"]["evidence"],
            ["candidate_registry:20:SHARED-V005"],
        )
        self.assertEqual(payload["structured"]["coverage"]["rangeStart"], "20230711")
        self.assertEqual(payload["structured"]["coverage"]["rangeEnd"], "20260729")
        self.assertEqual(
            payload["structured"]["coverage"]["latestTradeDate"],
            "20260729",
        )
        adjusted = next(
            row
            for row in payload["structured"]["sources"]
            if row["source"] == "adjusted_ohlcv"
        )
        self.assertEqual(adjusted["selectedModelFeatureCount"], 1)
        self.assertEqual(adjusted["activeStrategyFactorCount"], 2)
        self.assertIn("研究模型 SHARED-V005 (20日)", adjusted["useLocations"])
        self.assertEqual(
            payload["structured"]["quality"]["pointInTimeAuditedModels"],
            1,
        )
        self.assertEqual(payload["intelligence"]["pipeline"]["documents"], 584598)
        self.assertNotIn("rowsByDecision", payload["intelligence"])

    def test_active_decision_lineage_is_horizon_aware_for_formal_model_use(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={
                    "models": [
                        {
                            "model_version": "SAME",
                            "horizon": 5,
                            "feature_columns": ["event_negative_decay_5d"],
                        },
                        {
                            "model_version": "SAME",
                            "horizon": 20,
                            "feature_columns": [
                                "momentum_20",
                                "event_net_strength_5d",
                            ],
                        },
                    ]
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[
                    {
                        "market": "a_share",
                        "agent": "claude",
                        "status": "active",
                        "model_versions": {"20": "SAME"},
                    }
                ],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        defensive = payload["usageMatrix"][0]
        self.assertEqual(
            defensive["intelligenceFactors"]["features"],
            ["event_net_strength_5d"],
        )
        self.assertEqual(
            defensive["intelligenceFactors"]["evidence"],
            ["decision_lineage:20:SAME"],
        )
        self.assertEqual(
            defensive["structuredData"]["evidence"],
            ["decision_lineage:20:SAME"],
        )
        self.assertEqual(
            defensive["traditionalFactors"]["formalFactors"],
            [],
        )
        self.assertEqual(
            defensive["traditionalFactors"]["researchFeatures"],
            ["momentum_20"],
        )
        self.assertEqual(
            defensive["traditionalFactors"]["evidenceByNamespace"],
            {
                "formal": [],
                "research": ["decision_lineage:20:SAME"],
            },
        )

    def test_usage_preserves_overlapping_formal_and_research_namespaces(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {
                        "label": "稳健防守",
                        "factors": ["momentum_20"],
                    },
                    "trend": {
                        "label": "趋势进攻",
                        "factors": ["pb"],
                    },
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={
                    "models": [
                        {
                            "model_version": "OVERLAP",
                            "horizon": 20,
                            "feature_columns": [
                                "momentum_20",
                                "pb",
                                "event_net_strength_5d",
                            ],
                        }
                    ]
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[
                    {
                        "market": "a_share",
                        "agent": "claude",
                        "status": "active",
                        "model_versions": {"20": "OVERLAP"},
                    }
                ],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        usage = {row["consumerKey"]: row for row in payload["usageMatrix"]}
        defensive = usage["defensive"]["traditionalFactors"]
        self.assertEqual(defensive["formalFactors"], ["momentum_20"])
        self.assertEqual(
            defensive["researchFeatures"],
            ["momentum_20", "pb"],
        )
        self.assertEqual(defensive["formalCount"], 1)
        self.assertEqual(defensive["researchCount"], 2)
        self.assertEqual(defensive["count"], 3)
        self.assertEqual(
            defensive["countSemantics"],
            "formal_plus_research_namespace_items",
        )
        self.assertEqual(
            defensive["evidenceByNamespace"],
            {
                "formal": ["strategy_overlay"],
                "research": ["decision_lineage:20:OVERLAP"],
            },
        )

        trend = usage["trend"]["traditionalFactors"]
        self.assertEqual(trend["formalFactors"], ["pb"])
        self.assertEqual(trend["researchFeatures"], [])
        self.assertEqual(
            trend["evidenceByNamespace"],
            {"formal": ["strategy_overlay"], "research": []},
        )

        research = payload["structured"]["researchFeatureNamespace"]
        expected_research_names = {
            item.name
            for item in DEFAULT_REGISTRY
            if "a_share" in item.markets
            and item.family != "market_intelligence"
        }
        self.assertEqual(
            research["selectedFeatures"],
            ["momentum_20", "pb"],
        )
        self.assertEqual(
            research["definedFeatureCount"],
            len(expected_research_names),
        )
        self.assertNotIn("event_net_strength_5d", research["selectedFeatures"])
        self.assertEqual(
            payload["intelligence"]["featureNamespace"]["selectedFeatures"],
            ["event_net_strength_5d"],
        )
        self.assertEqual(
            payload["structured"]["formalFactorNamespace"]["activeFactors"],
            ["momentum_20", "pb"],
        )

    def test_latest_rule_only_lineage_supersedes_older_active_model_use(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={
                    "models": [
                        {
                            "model_version": "SAME",
                            "horizon": 20,
                            "feature_columns": ["event_net_strength_5d"],
                        }
                    ]
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[
                    {
                        "market": "a_share",
                        "agent": "claude",
                        "as_of": "2026-07-28",
                        "status": "active",
                        "model_versions": {"20": "SAME"},
                    },
                    {
                        "market": "a_share",
                        "agent": "claude",
                        "as_of": "2026-07-29",
                        "status": "rule_only",
                        "model_versions": {},
                    },
                ],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        defensive = payload["usageMatrix"][0]
        self.assertEqual(
            defensive["intelligenceFactors"]["status"],
            "not_used",
        )
        self.assertEqual(defensive["impact"], "本期规则驱动")

    def test_active_lineage_survives_a_missing_feature_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={"models": []},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[
                    {
                        "market": "a_share",
                        "agent": "claude",
                        "as_of": "2026-07-30",
                        "status": "active",
                        "model_versions": {"20": "RETIRED-MANIFEST"},
                    }
                ],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        defensive = payload["usageMatrix"][0]
        self.assertEqual(defensive["impact"], "正式决策采用 1 个模型版本")
        self.assertEqual(
            defensive["modelAdoption"],
            {
                "status": "active",
                "modelCount": 1,
                "resolvableManifestCount": 0,
                "missingManifestCount": 1,
                "models": [
                    {
                        "horizon": 20,
                        "modelVersion": "RETIRED-MANIFEST",
                        "manifestStatus": "unavailable",
                        "evidence": "decision_lineage:20:RETIRED-MANIFEST",
                        "missingManifestEvidence": (
                            "missing_manifest:20:RETIRED-MANIFEST"
                        ),
                    }
                ],
            },
        )
        traditional = defensive["traditionalFactors"]
        self.assertEqual(traditional["researchStatus"], "unavailable")
        self.assertEqual(
            traditional["missingManifestEvidence"],
            ["missing_manifest:20:RETIRED-MANIFEST"],
        )
        self.assertEqual(traditional["researchCount"], 0)

    def test_formal_factor_sources_cover_every_non_sentiment_overlay_factor(
        self,
    ) -> None:
        for market in ("a_share", "cn_qdii_etf"):
            with self.subTest(market=market):
                expected = (
                    set(AVAILABLE_FACTORS_BY_MARKET[market])
                    - set(SENTIMENT_FACTORS)
                )
                mapped = set().union(*FORMAL_FACTOR_SOURCES[market].values())
                self.assertEqual(mapped, expected)
        self.assertEqual(
            FORMAL_FACTOR_SOURCES["a_share"],
            {
                "tushare_daily_basic": {
                    "pe",
                    "pb",
                    "dividend_yield",
                },
                "tushare_fina_indicator_announced": {
                    "roe",
                    "gross_margin",
                    "debt_ratio",
                    "net_profit_growth",
                },
                "adjusted_ohlcv": {
                    "momentum_20",
                    "momentum_60",
                    "low_volatility_60",
                },
            },
        )

    def test_data_intelligence_empty_partial_and_unknown_market_states(self) -> None:
        with self.assertRaises(competition.UnknownMarket):
            build_dashboard_data_intelligence_data(
                repo_root=Path("/tmp"),
                market="unknown",
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_root = (
                root / "data" / "research" / "features" / "cn_qdii_etf"
            )
            feature_root.mkdir(parents=True)
            (feature_root / "20260729.parquet").write_bytes(b"not parquet")
            with mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={"status": "unavailable", "models": []},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(
                    pipeline={
                        "status": "unavailable",
                        "documents": 0,
                        "stages": {},
                        "backlog": {},
                        "sources": [],
                        "artifactWorkers": {},
                    },
                    factorSupply={
                        "status": "unavailable",
                        "suppliedFactors": 0,
                        "modelEligible": False,
                        "modelEligibleFactors": [],
                        "factors": [],
                    },
                ),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=root,
                    market="cn_qdii_etf",
                )

        coverage = payload["structured"]["coverage"]
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["snapshotAsOf"], "20260729")
        self.assertIsNone(coverage["rangeStart"])
        self.assertIsNone(coverage["rangeEnd"])
        self.assertIsNone(coverage["latestTradeDate"])
        self.assertEqual(len(payload["usageMatrix"]), 4)
        self.assertTrue(
            all(
                cell["status"] == "unavailable"
                for row in payload["usageMatrix"]
                for cell in (
                    row["structuredData"],
                    row["traditionalFactors"],
                    row["intelligenceFactors"],
                )
            )
        )

    def test_structured_coverage_reads_only_boundary_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_root = root / "data" / "research" / "features" / "a_share"
            feature_root.mkdir(parents=True)
            for day in range(1, 26):
                (feature_root / f"202601{day:02d}.parquet").write_bytes(b"x")

            def read_boundary(path: Path, **_kwargs: object) -> pd.DataFrame:
                trade_date = (
                    "20230711"
                    if Path(path).stem == "20260101"
                    else "20260729"
                )
                return pd.DataFrame({"trade_date": [trade_date]})

            with mock.patch(
                "stock_analyze.dashboard_workspace_api.pd.read_parquet",
                side_effect=read_boundary,
            ) as reader, mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={"models": []},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=root,
                    market="a_share",
                )

        self.assertEqual(reader.call_count, 2)
        self.assertEqual(
            payload["structured"]["coverage"]["rangeStart"],
            "20230711",
        )
        self.assertEqual(
            payload["structured"]["coverage"]["rangeEnd"],
            "20260729",
        )
        self.assertEqual(
            payload["structured"]["coverage"]["snapshotAsOf"],
            "20260125",
        )

    def test_structured_coverage_reports_partial_readable_boundary_honestly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_root = root / "data" / "research" / "features" / "a_share"
            feature_root.mkdir(parents=True)
            first = feature_root / "20260101.parquet"
            latest = feature_root / "20260125.parquet"
            first.write_bytes(b"x")
            latest.write_bytes(b"x")

            def read_boundary(path: Path, **_kwargs: object) -> pd.DataFrame:
                if Path(path) == latest:
                    raise OSError("broken latest snapshot")
                return pd.DataFrame({"trade_date": ["20230711"]})

            with mock.patch(
                "stock_analyze.dashboard_workspace_api.pd.read_parquet",
                side_effect=read_boundary,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": "稳健防守", "factors": []},
                    "trend": {"label": "趋势进攻", "factors": []},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={"models": []},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ):
                payload = build_dashboard_data_intelligence_data(
                    repo_root=root,
                    market="a_share",
                )

        coverage = payload["structured"]["coverage"]
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["snapshotAsOf"], "20260125")
        self.assertEqual(coverage["rangeStart"], "20230711")
        self.assertEqual(coverage["rangeEnd"], "20230711")
        self.assertEqual(coverage["latestTradeDate"], "20230711")
        self.assertEqual(coverage["datedSnapshots"], 1)

    def test_empty_snapshot_has_as_of_but_no_trade_date_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = (
                root
                / "data"
                / "research"
                / "features"
                / "a_share"
                / "20260729.parquet"
            )
            snapshot.parent.mkdir(parents=True)
            pd.DataFrame({"trade_date": []}).to_parquet(snapshot, index=False)

            coverage = _structured_snapshot_coverage(root, "a_share")

        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["snapshotAsOf"], "20260729")
        self.assertIsNone(coverage["rangeStart"])
        self.assertIsNone(coverage["rangeEnd"])
        self.assertIsNone(coverage["latestTradeDate"])
        self.assertEqual(coverage["readableSnapshots"], 1)
        self.assertEqual(coverage["datedSnapshots"], 0)

    def test_bounded_intelligence_lane_preserves_required_object_types(
        self,
    ) -> None:
        wide = {
            key: {
                f"column-{index:02d}": {
                    f"nested-{nested:02d}": list(range(40))
                    for nested in range(40)
                }
                for index in range(40)
            }
            for key in (
                "pipeline",
                "extraction",
                "factorSupply",
                "modelImpact",
                "decisions",
            )
        }

        lane = _bounded_intelligence_lane(wide)

        for key in (
            "pipeline",
            "extraction",
            "factorSupply",
            "modelImpact",
            "decisions",
        ):
            self.assertIsInstance(lane[key], dict)
        self.assertTrue(lane["truncated"])
        self.assertIn("node_budget_exhausted", lane["truncationReasons"])

    def test_data_intelligence_payload_is_sanitized_bounded_and_deterministic(
        self,
    ) -> None:
        oversized = "x" * 300_000
        intelligence = _intelligence()
        intelligence["pipeline"]["sources"] = [
            {"source": f"source-{index}", "raw_prose": oversized}
            for index in range(40)
        ]
        intelligence["factorSupply"]["factors"] = [
            {"name": f"factor-{index}", "detail": oversized}
            for index in range(40)
        ]
        intelligence["extraction"]["latestBatch"] = {
            "notes": oversized,
            "items": list(range(40)),
        }
        models = [
            {
                "model_version": f"V{index}",
                "horizon": 20,
                "feature_columns": [f"feature-{part}-{oversized}" for part in range(40)],
                "metrics": {"point_in_time_audit": index % 2 == 0},
            }
            for index in range(25)
        ]
        patches = (
            mock.patch(
                "stock_analyze.dashboard_workspace_api._public_strategy_profiles",
                return_value={
                    "defensive": {"label": oversized, "factors": []},
                    "trend": {"label": oversized, "factors": []},
                },
            ),
            mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_health",
                return_value={"models": models},
            ),
            mock.patch(
                "stock_analyze.dashboard_workspace_api.agg._read_model_iteration_status",
                return_value={},
            ),
            mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=intelligence,
            ),
            mock.patch(
                "stock_analyze.dashboard_workspace_api._latest_strategy_model_usage",
                return_value=[],
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                first = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                second = build_dashboard_data_intelligence_data(
                    repo_root=Path(tmp),
                    market="a_share",
                )

        def assert_bounded(value: object) -> None:
            if isinstance(value, list):
                self.assertLessEqual(len(value), 20)
                for item in value:
                    assert_bounded(item)
            elif isinstance(value, dict):
                self.assertNotIn("rowsByDecision", value)
                self.assertNotIn("raw_prose", value)
                for item in value.values():
                    assert_bounded(item)
            elif isinstance(value, str):
                self.assertLessEqual(len(value), 1_000)

        assert_bounded(first)
        first_generated = first.pop("generated_at")
        second_generated = second.pop("generated_at")
        self.assertIsInstance(first_generated, str)
        self.assertIsInstance(second_generated, str)
        self.assertEqual(first, second)
        self.assertLess(
            len(json.dumps(first, ensure_ascii=False).encode("utf-8")),
            250_000,
        )

    def test_operations_center_distinguishes_today_waiting_skip_and_partial(
        self,
    ) -> None:
        runtime = {
            "status": "available",
            "generated_at": "2026-07-30T13:30:00+08:00",
            "last_known_at": "2026-07-30T13:30:00+08:00",
            "reason": None,
            "services": {
                "stock-analyze-intelligence.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "2026-07-30T04:30:00Z",
                    "finishedAt": "Wed 2026-07-30 12:31:00 CST",
                },
                "stock-analyze-market-data.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "Tue 2026-07-29 18:30:00 CST",
                    "finishedAt": "Tue 2026-07-29 18:31:00 CST",
                },
                "stock-analyze-intelligence-artifact-backfill.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 75,
                    "startedAt": "Wed 2026-07-30 13:20:00 CST",
                    "finishedAt": "Wed 2026-07-30 13:20:01 CST",
                },
                "stock-analyze-claude-daily.service": {
                    "activeState": "inactive",
                    "subState": "dead",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "Wed 2026-07-30 13:00:00 CST",
                    "finishedAt": "Wed 2026-07-30 13:01:00 CST",
                },
            },
            "timers": {
                "stock-analyze-market-data.timer": {
                    "activeState": "active",
                    "lastTriggerAt": "Tue 2026-07-29 18:30:00 CST",
                    "nextTriggerAt": "Wed 2026-07-30 18:30:00 CST",
                },
            },
        }
        intelligence = _intelligence()
        intelligence["pipeline"]["artifactWorkers"] = {
            "status": "available",
            "activeLeases": 0,
            "latestFinishedAt": "2026-07-30T13:20:01+08:00",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=intelligence,
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(
                    2026,
                    7,
                    30,
                    13,
                    30,
                    tzinfo=ZoneInfo("Asia/Shanghai"),
                ),
            )

        statuses = {row["key"]: row["status"] for row in payload["mainChain"]}
        self.assertEqual(statuses["intelligence"], "success")
        self.assertEqual(statuses["market_snapshot"], "waiting_schedule")
        self.assertEqual(statuses["research"], "waiting_upstream")
        self.assertEqual(statuses["simulation"], "waiting_upstream")
        workers = {row["key"]: row for row in payload["backgroundWorkers"]}
        self.assertEqual(workers["artifact_backfill"]["status"], "skipped")
        market_timer = next(
            row
            for row in payload["schedules"]["daily"]
            if row["unit"] == "stock-analyze-market-data.timer"
        )
        self.assertEqual(market_timer["status"], "active")
        self.assertEqual(payload["interventions"], [])

    def test_operations_center_partial_stage_is_not_running_without_active_unit(
        self,
    ) -> None:
        success = {
            "activeState": "inactive",
            "subState": "dead",
            "result": "success",
            "exitStatus": 0,
            "startedAt": "Wed 2026-07-30 13:00:00 CST",
            "finishedAt": "Wed 2026-07-30 13:01:00 CST",
        }
        runtime = {
            "status": "available",
            "services": {
                "stock-analyze-intelligence.service": success,
                "stock-analyze-market-data.service": success,
                "stock-analyze-research.service": success,
                "stock-analyze-model-iteration.service": success,
            },
            "timers": {},
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                now=datetime(2026, 7, 30, 13, 30),
            )

        statuses = {row["key"]: row["status"] for row in payload["mainChain"]}
        self.assertEqual(statuses["simulation"], "waiting_schedule")

        runtime["services"]["stock-analyze-codex-daily.service"] = {
            **success,
            "activeState": "active",
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            active_payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                now=datetime(2026, 7, 30, 13, 30),
            )
        active_statuses = {
            row["key"]: row["status"] for row in active_payload["mainChain"]
        }
        self.assertEqual(active_statuses["simulation"], "running")

    def test_operations_center_runtime_unavailable_is_not_waiting(self) -> None:
        runtime = {
            "status": "unavailable",
            "generated_at": "2026-07-30T13:30:00+08:00",
            "last_known_at": "2026-07-30T13:20:00+08:00",
            "reason": "runtime_status_unavailable",
            "services": {
                "stock-analyze-intelligence.service": {
                    "activeState": "inactive",
                    "result": "success",
                    "exitStatus": 0,
                    "startedAt": "Wed 2026-07-30 13:00:00 CST",
                },
            },
            "timers": {},
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                now=datetime(2026, 7, 30, 13, 30),
            )

        self.assertTrue(
            all(row["status"] == "unavailable" for row in payload["mainChain"])
        )
        self.assertTrue(
            all(
                row["status"] == "unavailable"
                for row in payload["backgroundWorkers"]
            )
        )

    def test_operations_center_explicit_unloaded_units_are_unavailable(
        self,
    ) -> None:
        runtime = self._operations_scope_runtime()
        runtime["services"]["stock-analyze-intelligence.service"].update(
            {
                "loadState": "not-found",
                "reason": "unit_load_state_not-found",
            }
        )
        runtime["services"][
            "stock-analyze-intelligence-artifact-backfill.service"
        ] = {
            **self._operations_failed_service(),
            "loadState": "error",
            "reason": "unit_load_state_error",
        }
        runtime["timers"] = {
            "stock-analyze-market-data.timer": {
                "loadState": "masked",
                "reason": "unit_load_state_masked",
                "activeState": "active",
                "lastTriggerAt": "Wed 2026-07-30 12:30:00 CST",
                "nextTriggerAt": "Wed 2026-07-30 18:30:00 CST",
            }
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(2026, 7, 30, 13, 30),
            )

        intelligence = next(
            row for row in payload["mainChain"] if row["key"] == "intelligence"
        )
        self.assertEqual(intelligence["status"], "unavailable")
        self.assertEqual(intelligence["units"][0]["loadState"], "not-found")
        self.assertEqual(
            intelligence["units"][0]["reason"],
            "unit_load_state_not-found",
        )
        market_timer = next(
            row
            for row in payload["schedules"]["daily"]
            if row["unit"] == "stock-analyze-market-data.timer"
        )
        self.assertEqual(market_timer["status"], "unavailable")
        self.assertEqual(market_timer["loadState"], "masked")
        self.assertEqual(market_timer["reason"], "unit_load_state_masked")
        artifact = next(
            row
            for row in payload["backgroundWorkers"]
            if row["key"] == "artifact_backfill"
        )
        self.assertEqual(artifact["status"], "unavailable")
        self.assertEqual(artifact["loadState"], "error")
        self.assertEqual(artifact["reason"], "unit_load_state_error")

    def test_operations_timestamp_accepts_localized_prefix_and_never_case(
        self,
    ) -> None:
        self.assertIsNone(_operations_timestamp("NeVeR"))
        self.assertIsNone(_operations_timestamp("N/A"))
        runtime = self._operations_scope_runtime()
        runtime["services"]["stock-analyze-intelligence.service"][
            "startedAt"
        ] = "星期三 2026-07-30 12:30:00 CST"
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="a_share",
                now=datetime(2026, 7, 30, 13, 30),
            )

        intelligence = next(
            row for row in payload["mainChain"] if row["key"] == "intelligence"
        )
        self.assertEqual(intelligence["status"], "success")

    def test_operations_center_a_share_scope_excludes_etf_simulation_failure(
        self,
    ) -> None:
        runtime = self._operations_scope_runtime()
        runtime["services"][
            "stock-analyze-codex-cn-qdii-etf-daily.service"
        ] = self._operations_failed_service()
        runtime["services"][
            "stock-analyze-model-iteration.service"
        ] = self._operations_failed_service()
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            a_share = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="a_share",
                now=datetime(2026, 7, 30, 13, 30),
            )
            all_markets = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(2026, 7, 30, 13, 30),
            )

        a_simulation = next(
            row for row in a_share["mainChain"] if row["key"] == "simulation"
        )
        self.assertEqual(a_simulation["status"], "success")
        self.assertEqual(a_simulation["label"], "正式策略模拟")
        self.assertEqual(a_share["dailyFreshness"]["status"], "success")
        self.assertEqual(
            {row["unit"] for row in a_simulation["units"]},
            {
                "stock-analyze-claude-daily.service",
                "stock-analyze-codex-daily.service",
            },
        )
        self.assertEqual(len(a_simulation["crossMarketUnits"]), 1)
        model_evidence = a_simulation["crossMarketUnits"][0]
        self.assertEqual(
            model_evidence["unit"],
            "stock-analyze-model-iteration.service",
        )
        self.assertEqual(model_evidence["status"], "failed")
        self.assertEqual(
            model_evidence["reason"],
            "cross_market_service_result_not_attributable_to_single_market",
        )
        self.assertNotIn("候选模型", a_simulation["label"])
        all_simulation = next(
            row
            for row in all_markets["mainChain"]
            if row["key"] == "simulation"
        )
        self.assertEqual(all_simulation["status"], "failed")
        self.assertEqual(
            all_simulation["label"],
            "正式策略及候选模型模拟",
        )
        self.assertEqual(all_simulation["crossMarketUnits"], [])

    def test_operations_center_etf_scope_excludes_a_share_simulation_failure(
        self,
    ) -> None:
        runtime = self._operations_scope_runtime()
        runtime["services"][
            "stock-analyze-claude-daily.service"
        ] = self._operations_failed_service()
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=_intelligence(),
        ):
            etf = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="cn_qdii_etf",
                now=datetime(2026, 7, 30, 13, 30),
            )
            exceptions = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="exceptions",
                now=datetime(2026, 7, 30, 13, 30),
            )

        etf_simulation = next(
            row for row in etf["mainChain"] if row["key"] == "simulation"
        )
        self.assertEqual(etf_simulation["status"], "success")
        self.assertEqual(etf_simulation["label"], "正式策略模拟")
        self.assertEqual(etf["dailyFreshness"]["status"], "success")
        self.assertEqual(
            {row["unit"] for row in etf_simulation["units"]},
            {
                "stock-analyze-claude-cn-qdii-etf-daily.service",
                "stock-analyze-codex-cn-qdii-etf-daily.service",
            },
        )
        self.assertLessEqual(len(etf_simulation["crossMarketUnits"]), 20)
        self.assertEqual(
            etf_simulation["crossMarketUnits"][0]["reason"],
            "cross_market_service_result_not_attributable_to_single_market",
        )
        exception_simulation = next(
            row
            for row in exceptions["mainChain"]
            if row["key"] == "simulation"
        )
        self.assertEqual(exception_simulation["status"], "failed")
        self.assertEqual(
            exception_simulation["label"],
            "正式策略及候选模型模拟",
        )

    @staticmethod
    def _operations_scope_runtime() -> dict:
        success = {
            "activeState": "inactive",
            "subState": "dead",
            "result": "success",
            "exitStatus": 0,
            "startedAt": "Wed 2026-07-30 13:00:00 CST",
            "finishedAt": "Wed 2026-07-30 13:01:00 CST",
        }
        units = (
            "stock-analyze-intelligence.service",
            "stock-analyze-market-data.service",
            "stock-analyze-research.service",
            "stock-analyze-model-iteration.service",
            "stock-analyze-claude-daily.service",
            "stock-analyze-codex-daily.service",
            "stock-analyze-claude-cn-qdii-etf-daily.service",
            "stock-analyze-codex-cn-qdii-etf-daily.service",
            "stock-analyze-aggregate-dashboard.service",
            "stock-analyze-daily-summary.service",
        )
        return {
            "status": "available",
            "services": {unit: dict(success) for unit in units},
            "timers": {},
        }

    @staticmethod
    def _operations_failed_service() -> dict:
        return {
            "activeState": "failed",
            "subState": "failed",
            "result": "exit-code",
            "exitStatus": 1,
            "startedAt": "Wed 2026-07-30 13:00:00 CST",
            "finishedAt": "Wed 2026-07-30 13:01:00 CST",
        }

    def test_operations_center_recent_runs_are_logical_bounded_and_redacted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for market in competition.MARKETS:
                for agent in ("claude", "codex"):
                    path = root / "data" / market / agent / "runs.csv"
                    path.parent.mkdir(parents=True)
                    rows = [
                        "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version",
                        "000001,run-daily,2026-07-30,2026-07-30T10:00:00,,,running,,000abc,v1",
                        "000001,run-daily,2026-07-30,2026-07-30T10:00:00,2026-07-30T10:01:00,60000,failed,Authorization: Bearer top-secret token=also-secret,000abc,v1",
                    ]
                    for index in range(25):
                        rows.append(
                            f"{agent}-{index:02d},run-weekly,2026-07-29,"
                            f"2026-07-29T{index % 20:02d}:00:00,"
                            f"2026-07-29T{index % 20:02d}:01:00,60000,"
                            "success,,000abc,v1"
                        )
                    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            with mock.patch(
                "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
                return_value={
                    "status": "available",
                    "services": {},
                    "timers": {},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ):
                payload = build_dashboard_operations_center_data(
                    repo_root=root,
                    scope="exceptions",
                    now=datetime(2026, 7, 30, 13, 30),
                )

        self.assertLessEqual(len(payload["recentRuns"]), 20)
        self.assertTrue(payload["recentRuns"])
        self.assertTrue(
            all(row["status"] == "failed" for row in payload["recentRuns"])
        )
        self.assertEqual(payload["recentRuns"][0]["runId"], "000001")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("also-secret", serialized)
        self.assertLessEqual(
            len(payload["recentRuns"][0]["errorSummary"]),
            200,
        )

    def test_operations_center_recent_runs_use_completion_order_deterministically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "data" / "a_share" / "codex" / "runs.csv"
            runs.parent.mkdir(parents=True)
            runs.write_text(
                "\n".join(
                    [
                        "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version",
                        "000301,run-daily,,2026-07-30T10:00:00,2026-07-30T10:00:10,1,failed,first failure,h,v",
                        "000302,run-daily,,2026-07-30T10:00:00,2026-07-30T10:00:20,1,failed,second failure,h,v",
                        "000303,run-daily,,2026-07-30T10:00:00,2026-07-30T10:00:20,1,failed,third failure,h,v",
                        "000304,run-daily,,2026-07-30T10:00:00,,,running,,h,v",
                        "000304,run-daily,,2026-07-30T10:00:00,2026-07-30T10:00:30,1,success,,h,v",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch(
                "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
                return_value={
                    "status": "available",
                    "services": {},
                    "timers": {},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ):
                payload = build_dashboard_operations_center_data(
                    repo_root=root,
                    scope="a_share",
                    now=datetime(2026, 7, 30, 13, 30),
                )

        self.assertEqual(
            [row["runId"] for row in payload["recentRuns"]],
            ["000304", "000303", "000302", "000301"],
        )
        self.assertEqual(payload["recentRuns"][0]["status"], "success")
        self.assertFalse(
            any(
                row["key"].startswith("consecutive_failure:")
                for row in payload["interventions"]
            )
        )

    def test_operations_center_success_run_keeps_empty_error_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "data" / "a_share" / "codex" / "runs.csv"
            runs.parent.mkdir(parents=True)
            runs.write_text(
                "\n".join(
                    [
                        "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version",
                        "000201,run-daily,,2026-07-30T10:00:00,2026-07-30T10:01:00,1,success,,h,v",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch(
                "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
                return_value={
                    "status": "available",
                    "services": {},
                    "timers": {},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=_intelligence(),
            ):
                payload = build_dashboard_operations_center_data(
                    repo_root=root,
                    scope="a_share",
                    now=datetime(2026, 7, 30, 13, 30),
                )

        self.assertEqual(payload["recentRuns"][0]["errorSummary"], "")

    def test_run_error_sanitizer_redacts_prefixed_api_keys(self) -> None:
        secrets = (
            "sk-proj-" + "A" * 32,
            "sk-ant-api03-" + "B7cD" * 10,
            "sk-or-v1-" + "E8fG" * 10,
            "sk-live-" + "H9iJ" * 10,
            "sk-Ab3dEf4gHi5jKl6mNo7pQr8sTu9vWx0y",
            "AIza" + "B" * 35,
            "AKID" + "C" * 20,
            "LTAI" + "D" * 20,
        )
        text = (
            "HTTP 401 provider rejected "
            + " ".join(secrets)
            + "; request denied"
        )

        sanitized = _sanitize_run_error(text)

        for secret in secrets:
            self.assertNotIn(secret, sanitized)
        self.assertIn("HTTP 401 provider rejected", sanitized)
        self.assertIn("request denied", sanitized)
        self.assertLessEqual(len(sanitized), 200)

    def test_run_error_sanitizer_redacts_url_userinfo(self) -> None:
        text = (
            "fetch https://alice:p%40ssword@example.com/private/path?mode=full "
            "failed with timeout"
        )

        sanitized = _sanitize_run_error(text)

        self.assertNotIn("alice", sanitized)
        self.assertNotIn("p%40ssword", sanitized)
        self.assertIn("https://<redacted>@example.com/private/path", sanitized)
        self.assertIn("failed with timeout", sanitized)

    def test_run_error_sanitizer_redacts_credential_and_access_key_fields(
        self,
    ) -> None:
        known_key = "sk-proj-" + "Z9yX" * 10
        text = (
            "credential=abcDEF123-xyz7890 rejected; "
            "credential:abcdef1234567890 rejected again; "
            f"credential {known_key} rejected as known key; "
            "credential topsecret remains diagnostic; "
            "AccessKeySecret=SecretValue987654321; "
            "AccessKeyId=LTAI5tExampleAccessKey12"
        )

        sanitized = _sanitize_run_error(text)

        self.assertNotIn("abcDEF123-xyz7890", sanitized)
        self.assertNotIn("abcdef1234567890", sanitized)
        self.assertNotIn(known_key, sanitized)
        self.assertIn("credential topsecret remains diagnostic", sanitized)
        self.assertNotIn("SecretValue987654321", sanitized)
        self.assertNotIn("LTAI5tExampleAccessKey12", sanitized)
        self.assertIn("credential=<redacted> rejected", sanitized)
        self.assertLessEqual(len(sanitized), 200)

    def test_run_error_sanitizer_redacts_named_env_assignments(self) -> None:
        secrets = (
            "deepSeekSecret123456",
            "tushareSecret123456",
            "passwordSecret123456",
            "accessSecret123456",
            "backupAccessSecret123456",
        )
        text = (
            f'export DEEPSEEK_API_KEY="{secrets[0]}"; '
            f"TUSHARE_TOKEN={secrets[1]}; "
            f"CUSTOM_PASSWORD={secrets[2]}; "
            f"OSS_ACCESS_KEY_SECRET={secrets[3]}; "
            f"VENDOR_ACCESS_KEY_BACKUP={secrets[4]}"
        )

        sanitized = _sanitize_run_error(text)

        for secret in secrets:
            self.assertNotIn(secret, sanitized)
        self.assertIn("DEEPSEEK_API_KEY=<redacted>", sanitized)
        self.assertIn("TUSHARE_TOKEN=<redacted>", sanitized)

    def test_run_error_sanitizer_redacts_named_json_and_colon_fields(
        self,
    ) -> None:
        cases = (
            ('{"DEEPSEEK_API_KEY" : "lowentropy"}', "lowentropy"),
            ('{"tushare_token": "123"}', "123"),
            ('CUSTOM_PASSWORD : "simple"', "simple"),
            ('"vendor_secret" = "x"', '"x"'),
        )

        for text, secret in cases:
            with self.subTest(text=text):
                sanitized = _sanitize_run_error(text)
                self.assertNotIn(secret, sanitized)
                self.assertIn("<redacted>", sanitized)

    def test_run_error_sanitizer_redacts_safe_context_key_and_credential(
        self,
    ) -> None:
        for context in (
            "auth",
            "authentication",
            "authorization",
            "provider",
            "credential",
            "secret",
            "token",
            "api",
        ):
            text = f'{context} key:"plainsecretvalue123456"'
            with self.subTest(context=context):
                self.assertEqual(
                    _sanitize_run_error(text),
                    f"{context} key:<redacted>",
                )

        self.assertEqual(
            _sanitize_run_error(
                "credential plainsecretvalue123456 rejected"
            ),
            "credential <redacted> rejected",
        )

    def test_run_error_sanitizer_preserves_long_business_key_values(
        self,
    ) -> None:
        diagnostics = (
            "factor key=momentumfactor20261234",
            "feature key=technicalfeature20261234",
            "market key=asharemarket20261234",
            "service key=dashboardservice20261234",
            "key=plainsecretvalue123456",
        )

        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(_sanitize_run_error(diagnostic), diagnostic)

    def test_run_error_sanitizer_preserves_non_secret_diagnostics(self) -> None:
        diagnostics = (
            "credential file missing; sk-short is a label",
            "endpoint sk-analysis-worker-2026 unavailable",
            "resolver sk-model-resolver-v2 failed",
            "strategy sk-growth-rotation-2026 skipped",
            "credential endpoint unavailable",
            "credential resolver failed",
            "credential=file missing",
            "key=momentum_20",
            "key:portfolio",
            '"key": "instrument_id"',
            "LTAI docs unavailable; task skipped after 3 retries",
        )

        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.assertEqual(_sanitize_run_error(diagnostic), diagnostic)

    def test_operations_center_prunes_oversized_runtime_without_500(self) -> None:
        runtime = self._operations_scope_runtime()
        huge = "x" * 300_000
        runtime["reason"] = huge
        for service in runtime["services"].values():
            service["externalDetail"] = huge
        runtime["timers"] = {
            "stock-analyze-market-data.timer": {
                "loadState": "loaded",
                "activeState": "active",
                "lastTriggerAt": huge,
                "nextTriggerAt": huge,
                "externalDetail": huge,
            }
        }
        intelligence = _intelligence()
        intelligence["pipeline"]["status"] = huge
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            return_value=runtime,
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            return_value=intelligence,
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                scope="all",
                now=datetime(2026, 7, 30, 13, 30),
            )

        self.assertTrue(payload["truncated"])
        self.assertEqual(
            payload["truncationReason"],
            "serialized_size_limit",
        )
        self.assertLess(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            250_000,
        )
        self.assertIsInstance(payload["mainChain"], list)
        self.assertTrue(
            all("status" in row for row in payload["mainChain"])
        )
        self.assertIn("status", payload["background"])
        self.assertIn("status", payload["dailyFreshness"])
        self.assertIn("status", payload["runtime"])
        self.assertIsInstance(payload["interventions"], list)

        def assert_external_text_bounded(value: object) -> None:
            if isinstance(value, str):
                self.assertLessEqual(len(value), 1_000)
            elif isinstance(value, list):
                for item in value:
                    assert_external_text_bounded(item)
            elif isinstance(value, dict):
                for item in value.values():
                    assert_external_text_bounded(item)

        assert_external_text_bounded(payload)

    def test_operations_center_only_raises_actionable_interventions(self) -> None:
        intelligence = _intelligence()
        intelligence["pipeline"]["backlog"]["total"] = 10
        intelligence["pipeline"]["artifactWorkers"] = {
            "status": "available",
            "activeLeases": 0,
            "latestFinishedAt": "2026-07-28T12:00:00+08:00",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "data" / "a_share" / "codex" / "runs.csv"
            runs.parent.mkdir(parents=True)
            runs.write_text(
                "\n".join(
                    [
                        "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version",
                        "000101,run-daily,,2026-07-30T10:00:00,2026-07-30T10:01:00,1,failed,missing credential API_KEY=secret-value,h,v",
                        "000102,run-daily,,2026-07-29T10:00:00,2026-07-29T10:01:00,1,failed,retryable timeout,h,v",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch(
                "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
                return_value={
                    "status": "available",
                    "services": {},
                    "timers": {},
                },
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
                return_value=intelligence,
            ), mock.patch(
                "stock_analyze.dashboard_workspace_api.shutil.disk_usage",
                return_value=mock.Mock(total=100, used=90, free=10),
            ):
                payload = build_dashboard_operations_center_data(
                    repo_root=root,
                    now=datetime(
                        2026,
                        7,
                        30,
                        13,
                        30,
                        tzinfo=ZoneInfo("Asia/Shanghai"),
                    ),
                )

        keys = {row["key"] for row in payload["interventions"]}
        self.assertIn("disk_capacity", keys)
        self.assertIn("artifact_worker_stale", keys)
        self.assertTrue(any(key.startswith("credential:") for key in keys))
        self.assertTrue(
            any(key.startswith("consecutive_failure:") for key in keys)
        )
        self.assertNotIn("secret-value", json.dumps(payload, ensure_ascii=False))

    def test_operations_center_locally_degrades_failed_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "stock_analyze.dashboard_workspace_api.read_dashboard_runtime",
            side_effect=OSError("systemctl unavailable"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.build_dashboard_intelligence_data",
            side_effect=OSError("sqlite unavailable"),
        ), mock.patch(
            "stock_analyze.dashboard_workspace_api.shutil.disk_usage",
            side_effect=OSError("disk unavailable"),
        ):
            payload = build_dashboard_operations_center_data(
                repo_root=Path(tmp),
                now=datetime(2026, 7, 30, 13, 30),
            )

        self.assertEqual(payload["runtime"]["status"], "unavailable")
        self.assertEqual(
            {row["status"] for row in payload["mainChain"]},
            {"unavailable"},
        )
        self.assertEqual(payload["background"]["status"], "unavailable")
        self.assertEqual(payload["disk"]["status"], "unavailable")
        self.assertLess(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            250_000,
        )


if __name__ == "__main__":
    unittest.main()
