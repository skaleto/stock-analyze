"""Contract tests for the bounded model-research dashboard resource."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_analyze import competition
from stock_analyze.dashboard_workspace_api import (
    build_dashboard_model_research_data,
)


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
        self.assertEqual(len(payload["adoption"]["rollbackCandidates"]), 5)
        self.assertEqual(payload["stages"][-1]["status"], "success")

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


if __name__ == "__main__":
    unittest.main()
