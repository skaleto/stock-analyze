import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.strategy_ensemble import (
    STRATEGY_PROFILES,
    attach_active_predictions,
    risk_adjusted_target_weights,
    validate_strategy_profiles,
)


class ResearchStrategyEnsembleTest(unittest.TestCase):
    def setUp(self):
        self.candidates = pd.DataFrame(
            [
                {"code": "000001", "score": 1.0, "low_volatility_60": 0.20},
                {"code": "000002", "score": 0.9, "low_volatility_60": 0.18},
            ]
        )
        self.predictions = pd.DataFrame(
            [
                {"code": "000001", "p_up": 0.30, "p_down": 0.50, "confidence": 0.85, "expected_excess_return": -0.04, "active_status": "active"},
                {"code": "000002", "p_up": 0.78, "p_down": 0.10, "confidence": 0.90, "expected_excess_return": 0.12, "active_status": "active"},
            ]
        )

    def test_defensive_and_trend_profiles_stay_in_design_ranges(self):
        validate_strategy_profiles(STRATEGY_PROFILES)
        self.assertNotEqual(STRATEGY_PROFILES["defensive"].family_weights, STRATEGY_PROFILES["trend"].family_weights)

    def test_only_active_high_confidence_predictions_change_rank(self):
        adjusted = attach_active_predictions(self.candidates, self.predictions, profile="trend")
        self.assertEqual(adjusted.sort_values("score", ascending=False).iloc[0]["code"], "000002")
        self.assertTrue(adjusted["prediction_applied"].all())

        research = self.predictions.assign(active_status="inactive")
        unchanged = attach_active_predictions(self.candidates, research, profile="trend")
        pd.testing.assert_series_equal(unchanged["score"], self.candidates["score"], check_names=False)

    def test_low_confidence_and_invalidation_are_inert(self):
        low_confidence = self.predictions.assign(confidence=0.69)
        invalidated = self.predictions.assign(invalidated=True)
        for predictions in (low_confidence, invalidated):
            result = attach_active_predictions(self.candidates, predictions, profile="trend")
            pd.testing.assert_series_equal(result["score"], self.candidates["score"], check_names=False)

    def test_weight_optimizer_falls_back_to_current_top_n(self):
        weights = risk_adjusted_target_weights(
            self.candidates.assign(
                expected_excess_return=[None, None],
                prediction_confidence=[None, None],
                low_volatility_60=[None, None],
            ),
            top_n=2,
            max_single_weight=0.6,
        )
        self.assertEqual(weights, {"000001": 0.5, "000002": 0.5})

    def test_weight_optimizer_uses_volatility_when_predictions_are_absent(self):
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.0, "low_volatility_60": 0.10},
            {"code": "000002", "score": 0.9, "low_volatility_60": 0.30},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
        )

        self.assertGreater(weights["000001"], weights["000002"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertLessEqual(max(weights.values()), 0.80)

    def test_weight_optimizer_blends_toward_current_portfolio(self):
        candidates = pd.DataFrame([
            {
                "code": "000001", "score": 1.0, "low_volatility_60": 0.20,
                "expected_excess_return": 0.12, "prediction_confidence": 0.90,
                "prediction_applied": True,
            },
            {
                "code": "000002", "score": 0.9, "low_volatility_60": 0.20,
                "expected_excess_return": 0.01, "prediction_confidence": 0.75,
                "prediction_applied": True,
            },
        ])
        unconstrained = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
            turnover_penalty=0.0,
        )
        sticky = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
            current_weights={"000001": 0.20, "000002": 0.80},
            turnover_penalty=0.75,
        )

        self.assertLess(abs(sticky["000001"] - 0.20), abs(unconstrained["000001"] - 0.20))
        self.assertAlmostEqual(sum(sticky.values()), 1.0)

    def test_covariance_optimizer_diversifies_correlated_assets(self):
        dates = pd.date_range("2026-01-02", periods=80, freq="B")
        shared = pd.Series(np.sin(np.arange(len(dates)) / 4.0) * 0.02, index=dates)
        returns = pd.DataFrame({
            "000001": shared,
            "000002": shared * 0.98,
            "000003": pd.Series(np.cos(np.arange(len(dates)) / 5.0) * 0.012, index=dates),
        })
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.00, "low_volatility_60": 0.20},
            {"code": "000002", "score": 0.99, "low_volatility_60": 0.20},
            {"code": "000003", "score": 0.95, "low_volatility_60": 0.20},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=3,
            max_single_weight=0.60,
            return_history=returns,
            risk_aversion=1.5,
        )

        self.assertGreater(weights["000003"], min(weights["000001"], weights["000002"]))
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_optimizer_honors_gross_and_group_caps(self):
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.0, "industry": "科技"},
            {"code": "000002", "score": 0.9, "industry": "科技"},
            {"code": "000003", "score": 0.8, "industry": "消费"},
            {"code": "000004", "score": 0.7, "industry": "医药"},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=4,
            max_single_weight=0.35,
            gross_exposure=0.80,
            group_constraints={"industry": 0.35},
        )

        self.assertAlmostEqual(sum(weights.values()), 0.80)
        self.assertLessEqual(weights["000001"] + weights["000002"], 0.35 + 1e-8)


if __name__ == "__main__":
    unittest.main()
