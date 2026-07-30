"""Contract tests for the bounded model-research dashboard resource."""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import date, datetime, timezone
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
        second = _model()
        second["trained_at"] = "2026-07-29T23:00:00"

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
        usage = {
            "market": "a_share",
            "agent": "codex",
            "strategy_label": "Codex public account",
            "as_of": "2026-07-30",
            "status": "active",
            "model_versions": {"20": "A20-V005"},
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
                usage=[usage, dict(usage)],
            )

        self.assertEqual(len(payload["adoption"]["strategyUsage"]), 1)
        self.assertEqual(
            payload["adoption"]["strategyUsage"][0]["agent"],
            "codex",
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


if __name__ == "__main__":
    unittest.main()
