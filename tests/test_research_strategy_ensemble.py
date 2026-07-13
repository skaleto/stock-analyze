import unittest

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
            self.candidates.assign(expected_excess_return=[None, None], prediction_confidence=[None, None]),
            top_n=2,
            max_single_weight=0.6,
        )
        self.assertEqual(weights, {"000001": 0.5, "000002": 0.5})


if __name__ == "__main__":
    unittest.main()
