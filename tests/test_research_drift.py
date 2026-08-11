import tempfile
import unittest
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from stock_analyze.research.drift import (
    DriftLifecycle,
    DriftObservation,
    DriftThresholds,
)


def test_thresholds(**overrides) -> DriftThresholds:
    values = {
        "signal_window_days": 1,
        "minimum_calibration_samples_30d": 4,
        "minimum_calibration_samples_90d": 8,
        "minimum_performance_days_30d": 2,
        "minimum_performance_days_90d": 4,
        "consecutive_quarantine_windows": 3,
        "recovery_hysteresis_windows": 2,
    }
    values.update(overrides)
    return DriftThresholds(**values)


def observation(
    day: date,
    *,
    model_version: str = "model-v2",
    feature_psi: float | None = 0.02,
    ood_ratio: float | None = 0.01,
    prediction_distribution: tuple[float, ...] = (0.3, 0.4, 0.3),
    reference_prediction_distribution: tuple[float, ...] = (0.3, 0.4, 0.3),
    predicted_probabilities: tuple[float, ...] = (
        0.2,
        0.2,
        0.2,
        0.2,
        0.2,
        0.8,
        0.8,
        0.8,
        0.8,
        0.8,
    ),
    realized_outcomes: tuple[int, ...] = (1, 0, 0, 0, 0, 1, 1, 1, 1, 0),
    portfolio_return: float | None = 0.001,
    benchmark_return: float | None = 0.0005,
) -> DriftObservation:
    return DriftObservation(
        model_version=model_version,
        as_of=day.isoformat(),
        feature_psi=feature_psi,
        ood_ratio=ood_ratio,
        prediction_distribution=prediction_distribution,
        reference_prediction_distribution=reference_prediction_distribution,
        predicted_probabilities=predicted_probabilities,
        realized_outcomes=realized_outcomes,
        portfolio_return=portfolio_return,
        benchmark_return=benchmark_return,
    )


def severe_observation(day: date) -> DriftObservation:
    return observation(
        day,
        feature_psi=0.50,
        ood_ratio=0.35,
        prediction_distribution=(0.90, 0.05, 0.05),
        portfolio_return=0.001,
        benchmark_return=0.0005,
    )


class ResearchDriftLifecycleTest(unittest.TestCase):
    def _monitor(self, directory: str, **threshold_overrides) -> DriftLifecycle:
        return DriftLifecycle(
            Path(directory) / "drift-lifecycle.json",
            thresholds=test_thresholds(**threshold_overrides),
        )

    def _seed_healthy_evidence(
        self,
        monitor: DriftLifecycle,
        *,
        start: date = date(2026, 1, 1),
        days: int = 4,
    ) -> date:
        for offset in range(days):
            monitor.record(observation(start + timedelta(days=offset)))
        return start + timedelta(days=days)

    def test_missing_evidence_is_reported_without_becoming_a_breach(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            assessment = monitor.record(
                DriftObservation(
                    model_version="model-v2",
                    as_of="2026-01-01",
                    feature_psi=0.01,
                    ood_ratio=0.01,
                )
            )

        self.assertEqual(assessment.status, "insufficient_evidence")
        self.assertFalse(assessment.evidence_complete)
        self.assertEqual(assessment.breaches, ())
        self.assertIn("prediction_distribution", assessment.evidence_gaps)
        self.assertIn("calibration_30d", assessment.evidence_gaps)
        self.assertIn("live_performance_90d", assessment.evidence_gaps)
        self.assertEqual(
            assessment.metric_states["calibration_30d"],
            "insufficient_evidence",
        )
        self.assertEqual(assessment.consecutive_breach_windows, 0)

    def test_rolling_metrics_cover_feature_prediction_calibration_and_performance(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            next_day = self._seed_healthy_evidence(monitor)
            assessment = monitor.record(observation(next_day))

        self.assertEqual(assessment.status, "healthy")
        self.assertTrue(assessment.evidence_complete)
        self.assertAlmostEqual(assessment.metrics["feature_psi_rolling"], 0.02)
        self.assertAlmostEqual(assessment.metrics["ood_ratio_rolling"], 0.01)
        self.assertAlmostEqual(assessment.metrics["prediction_distribution_psi"], 0.0)
        for window in (30, 90):
            self.assertIn(f"calibration_ece_{window}d", assessment.metrics)
            self.assertIn(f"calibration_brier_{window}d", assessment.metrics)
            self.assertIn(f"calibration_slope_{window}d", assessment.metrics)
            self.assertIn(f"live_excess_return_{window}d", assessment.metrics)
            self.assertIn(f"live_max_drawdown_{window}d", assessment.metrics)

    def test_single_severe_spike_is_warning_not_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            next_day = self._seed_healthy_evidence(monitor)
            assessment = monitor.record(severe_observation(next_day))

        self.assertEqual(assessment.status, "warning")
        self.assertEqual(assessment.consecutive_breach_windows, 1)
        self.assertIn("feature_psi_rolling", assessment.breaches)
        self.assertFalse(assessment.fallback_required)

    def test_signal_window_must_complete_before_breach_counter_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp, signal_window_days=3)
            first = monitor.record(severe_observation(date(2026, 1, 1)))
            second = monitor.record(severe_observation(date(2026, 1, 2)))
            third = monitor.record(severe_observation(date(2026, 1, 3)))
            repeated = monitor.record(severe_observation(date(2026, 1, 3)))

        self.assertEqual(first.status, "insufficient_evidence")
        self.assertEqual(second.status, "insufficient_evidence")
        self.assertEqual(third.status, "warning")
        self.assertEqual(third.consecutive_breach_windows, 1)
        self.assertEqual(repeated.consecutive_breach_windows, 1)

    def test_research_model_reports_drift_without_entering_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            assessments = [
                monitor.record(
                    severe_observation(date(2026, 1, 1) + timedelta(days=offset)),
                    quarantine_eligible=False,
                )
                for offset in range(3)
            ]

        self.assertEqual([item.status for item in assessments], ["warning"] * 3)
        self.assertEqual(assessments[-1].consecutive_breach_windows, 0)
        self.assertFalse(assessments[-1].fallback_required)

    def test_consecutive_breaches_quarantine_active_model_and_suggest_previous_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            next_day = self._seed_healthy_evidence(monitor)
            assessments = [
                monitor.record(
                    severe_observation(next_day + timedelta(days=offset)),
                    active_model_version="model-v2",
                    previous_champion_version="model-v1",
                )
                for offset in range(3)
            ]

        self.assertEqual([item.status for item in assessments], ["warning", "warning", "quarantined"])
        self.assertEqual(assessments[-1].consecutive_breach_windows, 3)
        self.assertTrue(assessments[-1].fallback_required)
        self.assertEqual(assessments[-1].rollback_version, "model-v1")
        self.assertEqual(
            assessments[-1].thresholds["consecutive_quarantine_windows"],
            3,
        )

    def test_quarantine_recovery_requires_hysteresis(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            next_day = self._seed_healthy_evidence(monitor)
            for offset in range(3):
                monitor.record(
                    severe_observation(next_day + timedelta(days=offset)),
                    active_model_version="model-v2",
                    previous_champion_version="model-v1",
                )
            first_clean = monitor.record(
                observation(next_day + timedelta(days=3)),
                active_model_version="model-v2",
                previous_champion_version="model-v1",
            )
            second_clean = monitor.record(
                observation(next_day + timedelta(days=4)),
                active_model_version="model-v2",
                previous_champion_version="model-v1",
            )

        self.assertEqual(first_clean.status, "quarantined")
        self.assertEqual(first_clean.recovery_windows, 1)
        self.assertTrue(first_clean.fallback_required)
        self.assertEqual(second_clean.status, "healthy")
        self.assertEqual(second_clean.recovery_windows, 0)
        self.assertFalse(second_clean.fallback_required)

    def test_inactive_quarantined_model_does_not_request_formal_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            next_day = self._seed_healthy_evidence(monitor)
            assessment = None
            for offset in range(3):
                assessment = monitor.record(
                    severe_observation(next_day + timedelta(days=offset)),
                    active_model_version="model-v3",
                    previous_champion_version="model-v1",
                )

        self.assertIsNotNone(assessment)
        self.assertEqual(assessment.status, "quarantined")
        self.assertFalse(assessment.fallback_required)
        self.assertIsNone(assessment.rollback_version)

    def test_record_is_deterministic_and_idempotent_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            first = self._monitor(left)
            second = self._monitor(right)
            item = observation(date(2026, 1, 1))

            left_first = first.record(item)
            left_repeat = first.record(item)
            right_first = second.record(item)

            self.assertEqual(asdict(left_first), asdict(left_repeat))
            self.assertEqual(asdict(left_first), asdict(right_first))
            self.assertEqual(len(first.snapshot()["models"]["model-v2"]["events"]), 1)

            conflicting = observation(date(2026, 1, 1), feature_psi=0.30)
            with self.assertRaisesRegex(ValueError, "drift_observation_conflict"):
                first.record(conflicting)

    def test_retired_is_terminal_and_retirement_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = self._monitor(tmp)
            monitor.record(observation(date(2026, 1, 1)))
            retired = monitor.retire("model-v2", as_of="2026-01-02", reason="superseded")
            repeated = monitor.retire("model-v2", as_of="2026-01-02", reason="superseded")
            later = monitor.record(observation(date(2026, 1, 3)))

        self.assertEqual(retired.status, "retired")
        self.assertEqual(asdict(retired), asdict(repeated))
        self.assertEqual(later.status, "retired")
        self.assertFalse(later.fallback_required)


if __name__ == "__main__":
    unittest.main()
