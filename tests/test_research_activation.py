import unittest
import tempfile
from pathlib import Path

from stock_analyze.research.activation import (
    ActivationEvidence,
    ModelRegistry,
    ShadowCycleTracker,
    evaluate_activation,
    transition_status,
)


def passing_evidence(**overrides) -> ActivationEvidence:
    values = {
        "coverage": 0.92,
        "rank_ic": 0.04,
        "icir": 0.55,
        "brier_improvement": 0.03,
        "hit_rate_uplift": 0.06,
        "auc": 0.59,
        "net_excess_return": 0.08,
        "max_drawdown": 0.12,
        "annual_turnover": 4.0,
        "ablation_stability": 0.82,
        "shadow_cycles": 6,
    }
    values.update(overrides)
    return ActivationEvidence(**values)


class ResearchActivationTest(unittest.TestCase):
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
        self.assertEqual(next_week["remaining"], 2)


if __name__ == "__main__":
    unittest.main()
