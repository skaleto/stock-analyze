import json
import tempfile
import unittest
from pathlib import Path


class ModelIterationLifecycleTest(unittest.TestCase):
    def _write_registry(self, root: Path, market: str, horizon: int, registry: dict) -> Path:
        path = (
            root
            / "data"
            / "research"
            / "models"
            / market
            / str(horizon)
            / "registry.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def test_selects_latest_shadow_before_latest_research(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "cn_qdii_etf", 5, {
                "champion_model_version": "champion",
                "models": {
                    "champion": {"status": "active", "registered_at": "2026-07-01T12:00:00+08:00"},
                    "shadow-old": {"status": "shadow", "registered_at": "2026-07-02T12:00:00+08:00"},
                    "shadow-new": {"status": "shadow", "registered_at": "2026-07-03T12:00:00+08:00"},
                    "research-newest": {"status": "research", "registered_at": "2026-07-04T12:00:00+08:00"},
                },
            })

            candidate = ensure_iteration_candidate(root, "cn_qdii_etf", 5, as_of="2026-07-18")

        self.assertEqual(candidate["model_version"], "shadow-new")
        self.assertEqual(candidate["display_version"], "Q5-V003")
        self.assertEqual(candidate["status"], "shadow")
        self.assertEqual(candidate["champion_model_version"], "champion")
        self.assertEqual(candidate["shadow_cycles"], 0)
        self.assertEqual(candidate["shadow_cycles_remaining"], 12)

    def test_summary_uses_persisted_realized_forward_cycles(self):
        from stock_analyze.model_iteration import model_version_summary

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "a_share", 20, {
                "models": {"candidate": {
                    "status": "shadow",
                    "metrics": {
                        "historical_net_return": 0.103,
                        "historical_net_excess_return": -0.042,
                        "historical_cost_stress_net_excess_return": -0.058,
                        "historical_max_drawdown": 0.233,
                        "historical_target_fill_ratio": 0.983,
                        "historical_bootstrap_probability": 0.57,
                    },
                }},
            })
            cycles_path = (
                root / "data/research/models/a_share/20/shadow_cycles.json"
            )
            cycles_path.write_text(json.dumps({
                "models": {
                    "candidate": {
                        "cycles": [
                            {"week": f"2026-W{week:02d}"}
                            for week in range(20, 32)
                        ],
                        "usable_cycle_count": 5,
                    }
                }
            }), encoding="utf-8")

            summary = model_version_summary(
                root, "a_share", 20, "candidate"
            )

        self.assertEqual(summary["shadow_cycles"], 5)
        self.assertEqual(summary["shadow_cycles_remaining"], 7)
        self.assertEqual(summary["historical_net_return"], 0.103)
        self.assertEqual(summary["historical_net_excess_return"], -0.042)
        self.assertEqual(
            summary["historical_cost_stress_net_excess_return"],
            -0.058,
        )
        self.assertEqual(summary["historical_max_drawdown"], 0.233)
        self.assertEqual(summary["historical_target_fill_ratio"], 0.983)
        self.assertEqual(summary["historical_bootstrap_probability"], 0.57)

    def test_research_candidate_rotates_when_newer_research_model_arrives(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate, read_iteration_state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "candidate-v1": {"status": "research", "registered_at": "2026-07-01T12:00:00+08:00"},
                },
            })
            first = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["models"]["candidate-v2"] = {
                "status": "research",
                "registered_at": "2026-07-18T12:00:00+08:00",
            }
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            second = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")
            state = read_iteration_state(root, "a_share", 20)

        self.assertEqual(first["model_version"], "candidate-v1")
        self.assertEqual(second["model_version"], "candidate-v2")
        self.assertEqual(second["display_version"], "A20-V002")
        self.assertEqual(state["history"][-1]["model_version"], "candidate-v1")
        self.assertEqual(state["history"][-1]["outcome"], "superseded")

    def test_shadow_candidate_stays_pinned_when_newer_research_model_arrives(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "shadow-v1": {
                        "status": "shadow",
                        "registered_at": "2026-07-01T12:00:00+08:00",
                    },
                },
            })
            first = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["models"]["research-v2"] = {
                "status": "research",
                "registered_at": "2026-07-18T12:00:00+08:00",
            }
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            second = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")

        self.assertEqual(first["model_version"], "shadow-v1")
        self.assertEqual(second["model_version"], "shadow-v1")

    def test_mainline_spec_replaces_shadow_from_retired_spec(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate
        from stock_analyze.research.classical_specs import mainline_specs

        mainline_spec_id = mainline_specs("a_share", "")[0].spec_id

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "retired-shadow": {
                        "status": "shadow",
                        "registered_at": "2026-08-13T12:00:00+08:00",
                        "spec_id": "h20_elasticnet_rank_v1",
                    },
                    "mainline-research": {
                        "status": "research",
                        "registered_at": "2026-08-12T12:00:00+08:00",
                        "spec_id": mainline_spec_id,
                    },
                },
            })

            candidate = ensure_iteration_candidate(
                root,
                "a_share",
                20,
                as_of="2026-08-14",
            )

        self.assertEqual(candidate["model_version"], "mainline-research")

    def test_account_scoped_mainline_rejects_stale_protocol(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate
        from stock_analyze.research.classical_specs import mainline_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = mainline_specs("a_share", "hs300")[0]
            registry_path = (
                root / "data" / "research" / "models" / "a_share"
                / "hs300" / "20" / "registry.json"
            )
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "stale-v1": {
                        "status": "shadow",
                        "spec_id": spec.spec_id,
                        "spec_hash": spec.spec_hash,
                        "metrics": {
                            "training_protocol_version": "retired-protocol"
                        },
                    }
                },
            }), encoding="utf-8")

            candidate = ensure_iteration_candidate(
                root,
                "a_share",
                20,
                account_scope="hs300",
                as_of="2026-08-14",
            )

        self.assertIsNone(candidate)

    def test_account_scoped_admitted_transparent_rule_is_selectable_and_auditable(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = (
                root / "data/research/models/a_share/zz500/20/registry.json"
            )
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": None,
                "formal_strategy_activated": False,
                "models": {
                    "old-mainline": {
                        "status": "rejected",
                        "spec_id": "retired-mainline",
                    },
                    "rule-a-mom": {
                        "status": "shadow",
                        "registered_at": "2026-08-14T20:00:00+08:00",
                        "candidate_kind": "transparent_rule",
                        "runtime_contract": "transparent-rule-shadow-v1",
                        "spec_id": "A_MOM_02",
                        "spec_hash": "rule-hash",
                        "artifact": "data/rule-a-mom.json",
                        "admission_grade": "promising",
                        "source_campaign": "campaign-v1",
                        "promotion_policy": "strict-forward-review-v1",
                        "development_admission": {
                            "contract": "evidence-first-shadow-v2",
                            "active_evidence_passed": True,
                        },
                    },
                    "unqualified-rule": {
                        "status": "shadow",
                        "registered_at": "2026-08-15T20:00:00+08:00",
                        "candidate_kind": "transparent_rule",
                        "runtime_contract": "transparent-rule-shadow-v1",
                        "spec_id": "A_MOM_02",
                        "spec_hash": "rule-hash",
                        "artifact": "data/unqualified-rule.json",
                        "admission_grade": "promising",
                        "development_admission": {
                            "contract": "evidence-first-shadow-v2",
                            "active_evidence_passed": False,
                        },
                    },
                },
            }), encoding="utf-8")

            candidate = ensure_iteration_candidate(
                root,
                "a_share",
                20,
                account_scope="zz500",
                as_of="2026-08-14",
            )

        self.assertEqual(candidate["model_version"], "rule-a-mom")
        self.assertEqual(candidate["candidate_kind"], "transparent_rule")
        self.assertEqual(candidate["admission_grade"], "promising")
        self.assertEqual(candidate["source_campaign"], "campaign-v1")
        self.assertEqual(candidate["promotion_policy"], "strict-forward-review-v1")

    def test_terminal_models_are_never_selected_as_iteration_candidates(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "rejected": {
                        "status": "rejected",
                        "registered_at": "2026-07-18T12:00:00+08:00",
                    },
                    "quarantined": {
                        "status": "quarantined",
                        "registered_at": "2026-07-19T12:00:00+08:00",
                    },
                },
            })

            candidate = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-20")

        self.assertIsNone(candidate)

    def test_promotion_closes_candidate_and_selects_next_version(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate, read_iteration_state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": "champion-v1",
                "models": {
                    "champion-v1": {"status": "active", "registered_at": "2026-07-01T12:00:00+08:00"},
                    "candidate-v2": {"status": "shadow", "registered_at": "2026-07-02T12:00:00+08:00"},
                    "candidate-v3": {"status": "research", "registered_at": "2026-07-03T12:00:00+08:00"},
                },
            })
            selected = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            self.assertEqual(selected["model_version"], "candidate-v2")

            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["champion_model_version"] = "candidate-v2"
            registry["models"]["candidate-v2"]["status"] = "active"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            replacement = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")
            state = read_iteration_state(root, "a_share", 20)

        self.assertEqual(replacement["model_version"], "candidate-v3")
        self.assertEqual(replacement["champion_model_version"], "candidate-v2")
        self.assertEqual(state["history"][-1]["model_version"], "candidate-v2")
        self.assertEqual(state["history"][-1]["outcome"], "promoted")
        self.assertEqual(state["history"][-1]["ended_at"], "2026-07-18")

    def test_versioned_paths_keep_predictions_and_portfolios_isolated(self):
        from stock_analyze.model_iteration import (
            iteration_portfolio_dir,
            iteration_prediction_path,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction = iteration_prediction_path(
                root, "cn_qdii_etf", 5, "model-v4", "2026-07-18"
            )
            portfolio = iteration_portfolio_dir(root, "cn_qdii_etf", 5, "model-v4")

        self.assertEqual(
            prediction.relative_to(root).as_posix(),
            "data/research/iteration_predictions/cn_qdii_etf/5/model-v4/20260718.parquet",
        )
        self.assertEqual(
            portfolio.relative_to(root).as_posix(),
            "data/model_iterations/cn_qdii_etf/5/model-v4",
        )

    def test_scoped_forward_evidence_resolves_matching_composite_portfolio(self):
        from stock_analyze.model_iteration import (
            iteration_evidence_portfolio_dir,
            iteration_portfolio_dir,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            composite = (
                root / "data/model_iterations/a_share/20/scoped-abc123"
            )
            composite.mkdir(parents=True)
            (composite / "daily_nav.csv").write_text(
                "date,account_id,total_value,benchmark_close\n",
                encoding="utf-8",
            )
            status_path = composite.parent / "current_status.json"
            status_path.write_text(json.dumps({
                "model_versions": {
                    "hs300": "hs-model-v1",
                    "zz500": "zz-model-v1",
                },
                "portfolio_path": str(composite),
            }), encoding="utf-8")

            resolved = iteration_evidence_portfolio_dir(
                root,
                "a_share",
                20,
                "hs-model-v1",
                account_scope="hs300",
            )
            mismatch = iteration_evidence_portfolio_dir(
                root,
                "a_share",
                20,
                "different-model",
                account_scope="hs300",
            )

        self.assertEqual(resolved, composite)
        self.assertEqual(
            mismatch,
            iteration_portfolio_dir(
                root,
                "a_share",
                20,
                "different-model",
                account_scope="hs300",
            ),
        )

    def test_account_scoped_paths_and_candidate_state_are_isolated(self):
        from stock_analyze.model_iteration import (
            ensure_iteration_candidate,
            iteration_portfolio_dir,
            iteration_prediction_path,
            model_registry_path,
            read_iteration_state,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = model_registry_path(
                root,
                "a_share",
                3,
                account_scope="hs300",
            )
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "hs300-v1": {
                        "status": "shadow",
                        "registered_at": "2026-08-09T12:00:00+08:00",
                    }
                },
            }), encoding="utf-8")

            candidate = ensure_iteration_candidate(
                root,
                "a_share",
                3,
                account_scope="hs300",
                as_of="2026-08-09",
            )
            state = read_iteration_state(
                root,
                "a_share",
                3,
                account_scope="hs300",
            )
            prediction = iteration_prediction_path(
                root,
                "a_share",
                3,
                "hs300-v1",
                "2026-08-09",
                account_scope="hs300",
            )
            portfolio = iteration_portfolio_dir(
                root,
                "a_share",
                3,
                "hs300-v1",
                account_scope="hs300",
            )

        self.assertEqual(candidate["account_scope"], "hs300")
        self.assertEqual(state["account_scope"], "hs300")
        self.assertEqual(
            prediction.relative_to(root).as_posix(),
            "data/research/iteration_predictions/a_share/hs300/3/hs300-v1/20260809.parquet",
        )
        self.assertEqual(
            portfolio.relative_to(root).as_posix(),
            "data/model_iterations/a_share/hs300/3/hs300-v1",
        )


if __name__ == "__main__":
    unittest.main()
