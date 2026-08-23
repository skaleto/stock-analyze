import json
import unittest
import tempfile
import math
from pathlib import Path

from stock_analyze.research.activation import (
    ActivationEvidence,
    ModelRegistry,
    ShadowCycleTracker,
    activation_evidence_from_metrics,
    evaluate_all_cap_sleeve_gate,
    evaluate_activation,
    evaluate_role_activation,
    transition_status,
)


def passing_evidence(**overrides) -> ActivationEvidence:
    values = {
        "coverage": 0.97,
        "point_in_time_audit": True,
        "oos_predictions": 500,
        "rank_ic": 0.04,
        "icir": 0.55,
        "brier_improvement": 0.06,
        "hit_rate_uplift": 0.06,
        "auc": 0.59,
        "net_excess_return": 0.03,
        "max_drawdown": 0.12,
        "annual_turnover": 4.0,
        "ablation_stability": 0.82,
        "shadow_cycles": 12,
        "deflated_sharpe_probability": 0.99,
        "probability_of_backtest_overfit": 0.20,
        "pbo_trial_count": 6,
        "seed_rank_ic_std": 0.01,
        "subperiod_stability": 0.80,
        "feature_selection_stability": 0.85,
        "unbiased_universe": True,
        "effective_dates": 180,
        "effective_non_overlapping_periods": 40,
        "simulator_version": "paper-parity-daily-v1",
        "all_accounts_positive_active": True,
        "valid_trial_count": 5,
        "trial_evidence_status": "available",
        "execution_evidence_status": "available",
        "missing_liquidity_notional_ratio": 0.0,
        "impact_capped_notional_ratio": 0.0,
        "capital_utilization": 0.90,
        "strategic_risky_exposure": 0.90,
        "target_fill_ratio": 0.99,
        "cash_drag": 0.01,
        "cost_stress_net_excess_return": 0.01,
        "diagnostic_net_excess_return": 0.03,
        "diagnostic_max_drawdown": 0.12,
        "diagnostic_annual_turnover": 4.0,
        "diagnostic_trade_count": 20,
        "diagnostic_capital_utilization": 0.90,
        "diagnostic_target_fill_ratio": 0.99,
        "diagnostic_cost_stress_net_excess_return": 0.01,
        "diagnostic_all_accounts_positive_active": True,
        "diagnostic_simulator_version": "paper-parity-daily-v1",
        "diagnostic_execution_evidence_status": "available",
        "diagnostic_missing_liquidity_notional_ratio": 0.0,
        "diagnostic_impact_capped_notional_ratio": 0.0,
        "diagnostic_attribution_status": "reconciled",
        "forward_evidence_status": "available",
        "forward_cycles": 12,
        "forward_net_excess_return": 0.02,
        "forward_max_drawdown": 0.10,
        "forward_all_accounts_positive_active": True,
    }
    values.update(overrides)
    return ActivationEvidence(**values)


class ResearchActivationTest(unittest.TestCase):
    def test_all_cap_sleeve_gate_fails_closed_and_keeps_reason_order(self):
        gates = {
            "minimum_oos_folds": 4,
            "minimum_positive_oos_folds": 3,
            "minimum_oos_dates": 252,
            "minimum_completed_trades": 100,
            "minimum_rank_ic": 0.02,
            "minimum_icir": 0.30,
            "minimum_sleeve_net_excess_return": 0.0,
            "minimum_double_cost_net_excess_return": 0.0,
            "maximum_drawdown": 0.20,
            "maximum_benchmark_drawdown_multiple": 1.20,
            "minimum_target_fill_rate": 0.95,
            "minimum_deflated_sharpe_probability": 0.95,
            "maximum_probability_of_backtest_overfit": 0.50,
            "minimum_positive_calendar_years": 4,
            "maximum_single_year_positive_excess_share": 0.50,
            "maximum_liquidation_days": 5,
            "maximum_order_adv_fraction": 0.05,
            "minimum_base_orders_within_adv_fraction": 0.99,
            "require_simulator_parity": True,
        }

        report = evaluate_all_cap_sleeve_gate(
            {
                "oos_folds": 3,
                "oos_dates": math.nan,
                "rank_ic": None,
            },
            gates,
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            report.reasons[:3],
            ("simulator_parity", "oos_folds", "oos_dates"),
        )
        self.assertIn("rank_ic", report.reasons)

    def test_registry_admits_development_winner_as_versioned_shadow_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            metadata = {
                "artifact": "/tmp/model.joblib",
                "spec_id": "baseline-residual-v1",
                "spec_hash": "abc123",
                "metrics": {"training_protocol_version": "fixture-v1"},
            }

            first = registry.admit_development_shadow(
                "model-v1",
                metadata=metadata,
                admission={"contract": "baseline-first-v1", "report": "/tmp/report.json"},
            )
            second = registry.admit_development_shadow(
                "model-v1",
                metadata=metadata,
                admission={"contract": "baseline-first-v1", "report": "/tmp/report.json"},
            )

        model = first["models"]["model-v1"]
        self.assertEqual(model["status"], "shadow")
        self.assertEqual(model["role_status"]["ranker"], "shadow")
        self.assertEqual(model["role_status"]["portfolio"], "shadow")
        self.assertEqual(model["role_status"]["classifier"], "research")
        self.assertIsNone(first["champion_model_version"])
        self.assertFalse(model["formal_strategy_activated"])
        self.assertEqual(len(second["lifecycle_events"]), 1)

    def test_shadow_admission_preserves_existing_formal_activation_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            state = registry.initialize_champion("champion-v1", roles=("ranker",))
            state["formal_strategy_activated"] = True
            registry._write(state)

            admitted = registry.admit_development_shadow(
                "candidate-v2",
                metadata={"artifact": "/tmp/candidate.joblib"},
                admission={"contract": "baseline-first-v1"},
            )

        self.assertTrue(admitted["formal_strategy_activated"])
        self.assertEqual(admitted["champion_model_version"], "champion-v1")
        self.assertFalse(
            admitted["models"]["candidate-v2"]["formal_strategy_activated"]
        )

    def test_registry_rejects_expired_shadow_without_changing_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            registry.initialize_champion("champion-v1", roles=("ranker",))
            registry.admit_development_shadow(
                "candidate-v2",
                metadata={"artifact": "/tmp/candidate.joblib"},
                admission={"contract": "baseline-first-v1"},
            )

            first = registry.reject_shadow(
                "candidate-v2",
                reason="shadow_evidence_cap_reached",
                event_id="shadow-stop:candidate-v2:16",
            )
            second = registry.reject_shadow(
                "candidate-v2",
                reason="shadow_evidence_cap_reached",
                event_id="shadow-stop:candidate-v2:16",
            )

        self.assertEqual(first["models"]["candidate-v2"]["status"], "rejected")
        self.assertEqual(first["champion_model_version"], "champion-v1")
        self.assertEqual(len([
            event for event in second["lifecycle_events"]
            if event["event_id"] == "shadow-stop:candidate-v2:16"
        ]), 1)

    def test_passes_complete_shadow_to_active_evidence(self):
        report = evaluate_activation(passing_evidence(), current_status="shadow", target_status="active")
        self.assertTrue(report.passed)
        self.assertEqual(report.reasons, ())
        self.assertEqual(transition_status("shadow", "active", report), "active")

    def test_failed_gate_keeps_current_champion_status(self):
        evidence = passing_evidence(
            coverage=0.4,
            rank_ic=-0.01,
            brier_improvement=-0.02,
            max_drawdown=0.30,
            shadow_cycles=1,
        )
        report = evaluate_activation(evidence, current_status="shadow", target_status="active")

        self.assertFalse(report.passed)
        self.assertIn("coverage", report.reasons)
        self.assertIn("max_drawdown", report.reasons)
        self.assertEqual(transition_status("shadow", "active", report), "shadow")
        self.assertEqual(report.metrics["coverage"], 0.4)

    def test_research_gate_requires_point_in_time_audit_and_oos_support(self):
        report = evaluate_activation(
            passing_evidence(point_in_time_audit=False, oos_predictions=199),
            current_status="research",
            target_status="shadow",
        )

        self.assertFalse(report.passed)
        self.assertIn("point_in_time_audit", report.reasons)
        self.assertIn("oos_predictions", report.reasons)

    def test_metrics_are_converted_to_activation_evidence(self):
        evidence = activation_evidence_from_metrics(
            {
                "feature_coverage": 0.97,
                "point_in_time_audit": True,
                "oos_predictions": 500,
                "rank_ic": 0.04,
                "icir": 0.55,
                "brier_improvement": 0.06,
                "hit_rate_uplift": 0.06,
                "auc": 0.59,
                "net_excess_return": 0.03,
                "max_drawdown": 0.12,
                "annual_turnover": 4.0,
                "ablation_stability": 0.82,
            },
            shadow_cycles=3,
        )

        self.assertEqual(evidence.shadow_cycles, 3)
        self.assertEqual(evidence.oos_predictions, 500)
        self.assertTrue(evidence.point_in_time_audit)

    def test_non_finite_metrics_fail_closed(self):
        evidence = activation_evidence_from_metrics({"auc": math.nan, "annual_turnover": math.inf})

        self.assertEqual(evidence.auc, 0.0)
        self.assertEqual(evidence.annual_turnover, 1_000_000_000.0)

    def test_rejects_invalid_status_transition(self):
        with self.assertRaisesRegex(ValueError, "activation_transition"):
            evaluate_activation(passing_evidence(), current_status="research", target_status="active")

    def test_failed_gate_is_audited_without_replacing_champion(self):
        report = evaluate_activation(
            passing_evidence(coverage=0.2),
            current_status="shadow",
            target_status="active",
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            registry.initialize_champion("champion-v1")
            state = registry.record_gate("challenger-v2", report)

        self.assertEqual(state["champion_model_version"], "champion-v1")
        self.assertEqual(state["models"]["challenger-v2"]["status"], "shadow")
        self.assertEqual(state["models"]["challenger-v2"]["gate_history"][0]["reasons"], ["coverage"])

    def test_shadow_cycles_are_idempotent_per_calendar_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ShadowCycleTracker(Path(tmp) / "shadow_cycles.json")
            first = tracker.record("model-v2", "2026-07-13", {"brier": 0.2})
            repeated = tracker.record("model-v2", "2026-07-15", {"brier": 0.19})
            next_week = tracker.record("model-v2", "2026-07-20", {"brier": 0.18})

        self.assertEqual(first["count"], 1)
        self.assertEqual(repeated["count"], 1)
        self.assertEqual(next_week["count"], 2)
        self.assertEqual(next_week["remaining"], 10)

    def test_shadow_cycles_do_not_count_unusable_prediction_weeks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ShadowCycleTracker(Path(tmp) / "shadow_cycles.json")
            skipped = tracker.record(
                "model-v2",
                "2026-07-13",
                {"predictions": 0},
                eligible=False,
            )
            usable = tracker.record(
                "model-v2",
                "2026-07-20",
                {"predictions": 10},
                eligible=True,
            )

        self.assertEqual(skipped["count"], 0)
        self.assertFalse(skipped["is_new_cycle"])
        self.assertEqual(usable["count"], 1)

    def test_shadow_tracker_persists_realized_forward_cycle_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow_cycles.json"
            tracker = ShadowCycleTracker(path)
            for week in range(12):
                tracker.record(
                    "model-v2",
                    f"2026-{7 + week // 4:02d}-{6 + (week % 4) * 7:02d}",
                    {"predictions": 10},
                )

            usable = tracker.record_usable_count(
                "model-v2",
                5,
                as_of="2026-09-30",
            )
            state = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(usable, 5)
        self.assertEqual(
            state["models"]["model-v2"]["usable_cycle_count"],
            5,
        )

    def test_role_gates_do_not_conflate_classifier_ranker_and_portfolio(self):
        evidence = passing_evidence(
            brier_improvement=-0.02,
            hit_rate_uplift=-0.01,
            auc=0.51,
            shadow_cycles=12,
        )

        reports = evaluate_role_activation(
            evidence,
            current_status="shadow",
            target_status="active",
        )

        self.assertFalse(reports["classifier"].passed)
        self.assertTrue(reports["ranker"].passed)
        self.assertTrue(reports["portfolio"].passed)
        self.assertIn("brier_improvement", reports["classifier"].reasons)
        self.assertNotIn("brier_improvement", reports["ranker"].reasons)

    def test_ranker_and_portfolio_fail_closed_without_execution_evidence(self):
        reports = evaluate_role_activation(
            passing_evidence(
                execution_evidence_status="unavailable",
                missing_liquidity_notional_ratio=0.11,
                impact_capped_notional_ratio=0.11,
                diagnostic_execution_evidence_status="unavailable",
                diagnostic_missing_liquidity_notional_ratio=0.11,
                diagnostic_impact_capped_notional_ratio=0.11,
            ),
            current_status="research",
            target_status="shadow",
        )

        self.assertTrue(reports["classifier"].passed)
        for role in ("ranker", "portfolio"):
            self.assertFalse(reports[role].passed)
            self.assertIn("execution_evidence_status", reports[role].reasons)
            self.assertIn("missing_liquidity_notional_ratio", reports[role].reasons)
            self.assertIn("impact_capped_notional_ratio", reports[role].reasons)

    def test_ranker_does_not_require_formal_edge_calibration(self):
        reports = evaluate_role_activation(
            passing_evidence(
                edge_calibration_available=False,
                attribution_status="mismatch",
            ),
            current_status="research",
            target_status="shadow",
        )

        self.assertTrue(reports["classifier"].passed)
        self.assertTrue(reports["ranker"].passed)
        self.assertNotIn(
            "edge_calibration_available",
            reports["ranker"].reasons,
        )
        self.assertFalse(reports["portfolio"].passed)
        self.assertIn("edge_calibration_available", reports["portfolio"].reasons)
        self.assertIn("attribution_status", reports["portfolio"].reasons)

    def test_ranker_requires_positive_exact_cost_diagnostic_economics(self):
        reports = evaluate_role_activation(
            passing_evidence(diagnostic_net_excess_return=-0.01),
            current_status="research",
            target_status="shadow",
        )

        self.assertFalse(reports["ranker"].passed)
        self.assertIn("diagnostic_net_excess_return", reports["ranker"].reasons)
        self.assertTrue(reports["portfolio"].passed)

    def test_portfolio_gate_accepts_intentional_cash_when_target_is_filled(self):
        reports = evaluate_role_activation(
            passing_evidence(
                capital_utilization=0.49,
                strategic_risky_exposure=0.50,
                target_fill_ratio=0.98,
                annual_turnover=20.0,
            ),
            current_status="research",
            target_status="shadow",
        )

        self.assertTrue(reports["ranker"].passed)
        self.assertTrue(reports["portfolio"].passed)
        self.assertNotIn("capital_utilization", reports["portfolio"].reasons)
        self.assertNotIn("annual_turnover", reports["portfolio"].reasons)

    def test_portfolio_gate_rejects_failed_target_fill_and_double_cost_stress(self):
        reports = evaluate_role_activation(
            passing_evidence(
                target_fill_ratio=0.94,
                cost_stress_net_excess_return=-0.001,
            ),
            current_status="research",
            target_status="shadow",
        )

        self.assertFalse(reports["portfolio"].passed)
        self.assertIn("target_fill_ratio", reports["portfolio"].reasons)
        self.assertIn("cost_stress_net_excess_return", reports["portfolio"].reasons)

    def test_active_role_promotion_supersedes_previous_role_champion(self):
        report = evaluate_role_activation(
            passing_evidence(shadow_cycles=12),
            current_status="shadow",
            target_status="active",
        )["ranker"]
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            registry.initialize_champion("ranker-v1", roles=("ranker",))
            state = registry.record_role_gate("ranker-v2", "ranker", report)

        self.assertEqual(state["champion_model_versions"]["ranker"], "ranker-v2")
        self.assertEqual(state["models"]["ranker-v2"]["role_status"]["ranker"], "active")
        self.assertEqual(state["models"]["ranker-v1"]["role_status"]["ranker"], "superseded")

    def test_new_active_promotion_requires_twelve_shadow_cycles(self):
        reports = evaluate_role_activation(
            passing_evidence(shadow_cycles=11),
            current_status="shadow",
            target_status="active",
        )

        self.assertFalse(reports["ranker"].passed)
        self.assertIn("shadow_cycles", reports["ranker"].reasons)

    def test_active_promotion_requires_realized_forward_portfolio_evidence(self):
        reports = evaluate_role_activation(
            passing_evidence(
                forward_evidence_status="insufficient_evidence",
                forward_cycles=0,
                forward_net_excess_return=0.0,
                forward_max_drawdown=1.0,
                forward_all_accounts_positive_active=False,
            ),
            current_status="shadow",
            target_status="active",
        )

        for role in ("ranker", "portfolio"):
            self.assertFalse(reports[role].passed)
            self.assertIn("forward_evidence_status", reports[role].reasons)
            self.assertIn("forward_cycles", reports[role].reasons)
            self.assertIn("forward_net_excess_return", reports[role].reasons)
        self.assertIn(
            "forward_all_accounts_positive_active",
            reports["portfolio"].reasons,
        )

    def test_qdii_active_ranker_and_portfolio_require_lookthrough_coverage(self):
        reports = evaluate_role_activation(
            passing_evidence(
                lookthrough_required=True,
                lookthrough_evidence_status="available",
                underlying_profile_coverage=0.79,
                underlying_company_weight_coverage=0.59,
            ),
            current_status="shadow",
            target_status="active",
        )

        self.assertTrue(reports["classifier"].passed)
        for role in ("ranker", "portfolio"):
            self.assertFalse(reports[role].passed)
            self.assertIn("underlying_profile_coverage", reports[role].reasons)
            self.assertIn(
                "underlying_company_weight_coverage",
                reports[role].reasons,
            )

    def test_lookthrough_gate_is_not_required_for_research_to_shadow(self):
        reports = evaluate_role_activation(
            passing_evidence(
                lookthrough_required=True,
                lookthrough_evidence_status="insufficient_evidence",
                underlying_profile_coverage=0.0,
                underlying_company_weight_coverage=0.0,
            ),
            current_status="research",
            target_status="shadow",
        )

        self.assertTrue(reports["ranker"].passed)
        self.assertTrue(reports["portfolio"].passed)

    def test_statistical_governance_fails_closed_for_ranker_and_portfolio(self):
        reports = evaluate_role_activation(
            passing_evidence(
                shadow_cycles=12,
                deflated_sharpe_probability=0.70,
                probability_of_backtest_overfit=1.0,
                pbo_trial_count=1,
                unbiased_universe=False,
            ),
            current_status="shadow",
            target_status="active",
        )

        for role in ("ranker", "portfolio"):
            self.assertFalse(reports[role].passed)
            self.assertIn("deflated_sharpe_probability", reports[role].reasons)
            self.assertIn("probability_of_backtest_overfit", reports[role].reasons)
            self.assertIn("pbo_trial_count", reports[role].reasons)
            self.assertIn("unbiased_universe", reports[role].reasons)

    def test_rows_cannot_replace_independent_dates_or_exact_execution_evidence(self):
        reports = evaluate_role_activation(
            passing_evidence(
                oos_predictions=50_000,
                effective_dates=10,
                effective_non_overlapping_periods=3,
                simulator_version="legacy-percentile-v1",
                diagnostic_simulator_version="legacy-percentile-v1",
                all_accounts_positive_active=False,
                valid_trial_count=1,
                trial_evidence_status="insufficient_evidence",
            ),
            current_status="research",
            target_status="shadow",
        )

        for role in ("ranker", "portfolio"):
            self.assertIn("effective_dates", reports[role].reasons)
            self.assertIn("effective_non_overlapping_periods", reports[role].reasons)
            self.assertIn("simulator_version", reports[role].reasons)
            self.assertIn("valid_trial_count", reports[role].reasons)
            self.assertIn("trial_evidence_status", reports[role].reasons)
        self.assertIn("all_accounts_positive_active", reports["portfolio"].reasons)

    def test_quarantine_rolls_ranker_back_to_previous_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            registry.initialize_champion("v1", roles=("ranker",))
            report = evaluate_role_activation(
                passing_evidence(shadow_cycles=12),
                current_status="shadow",
                target_status="active",
            )["ranker"]
            registry.record_role_gate("v2", "ranker", report)
            state = registry.quarantine_roles(
                "v2",
                roles=("ranker",),
                reason="feature_psi",
                event_id="drift-event-1",
            )

        self.assertEqual(state["models"]["v2"]["role_status"]["ranker"], "quarantined")
        self.assertEqual(state["models"]["v1"]["role_status"]["ranker"], "active")
        self.assertEqual(state["champion_model_versions"]["ranker"], "v1")
        self.assertEqual(state["champion_model_version"], "v1")

    def test_quarantined_pointer_is_not_selected_as_active(self):
        from stock_analyze.research.activation import select_registry_model

        registry = {
            "champion_model_versions": {"ranker": "broken"},
            "models": {
                "broken": {"role_status": {"ranker": "quarantined"}},
                "fallback": {
                    "role_status": {"ranker": "active"},
                    "registered_at": "2026-07-01T00:00:00+00:00",
                },
            },
        }

        selected = select_registry_model(registry, role="ranker")

        self.assertEqual(selected[0], "fallback")

    def test_completed_research_evaluation_rejects_model_without_required_roles(self):
        reports = evaluate_role_activation(
            passing_evidence(
                rank_ic=-0.01,
                net_excess_return=-0.02,
            ),
            current_status="research",
            target_status="shadow",
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            for role, report in reports.items():
                registry.record_role_gate("candidate-v1", role, report)
            state = registry.finalize_research_evaluation("candidate-v1")

        model = state["models"]["candidate-v1"]
        self.assertEqual(model["status"], "rejected")
        self.assertIn("ranker:rank_ic", model["rejection_reasons"])
        self.assertIn("portfolio:net_excess_return", model["rejection_reasons"])

    def test_completed_research_evaluation_enters_shadow_when_ranker_and_portfolio_pass(self):
        reports = evaluate_role_activation(
            passing_evidence(
                brier_improvement=-0.02,
                hit_rate_uplift=-0.01,
                auc=0.51,
            ),
            current_status="research",
            target_status="shadow",
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.json")
            for role, report in reports.items():
                registry.record_role_gate("candidate-v2", role, report)
            state = registry.finalize_research_evaluation("candidate-v2")

        model = state["models"]["candidate-v2"]
        self.assertEqual(model["status"], "shadow")
        self.assertEqual(model["role_status"]["classifier"], "research")
        self.assertEqual(model["rejection_reasons"], [])

    def test_terminal_registry_models_are_not_selected_by_fallback(self):
        from stock_analyze.research.activation import select_registry_model

        registry = {
            "models": {
                "rejected": {"status": "rejected"},
                "quarantined": {"status": "quarantined"},
            }
        }

        self.assertIsNone(select_registry_model(registry))


if __name__ == "__main__":
    unittest.main()
