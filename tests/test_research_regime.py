import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.regime import classify_regimes


class ResearchRegimeTest(unittest.TestCase):
    def test_classifies_all_regime_dimensions(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["20260707", "20260708", "20260709", "20260710"],
                "trend_score": [0.1, 0.7, 0.8, 0.9],
                "volatility_score": [0.0, 0.8, 0.9, 1.0],
                "liquidity_score": [0.0, -0.7, -0.8, -0.9],
                "macro_score": [-0.5, -0.2, 0.2, 0.6],
                "global_risk_score": [0.0, -0.7, -0.8, -0.9],
            }
        )

        result = classify_regimes(frame)

        latest = result.iloc[-1]
        self.assertEqual(latest["trend_regime"], "up")
        self.assertEqual(latest["volatility_regime"], "high")
        self.assertEqual(latest["liquidity_regime"], "contracting")
        self.assertIn(latest["macro_regime"], {"recovery", "expansion"})
        self.assertEqual(latest["global_risk_regime"], "risk_off")

    def test_transition_requires_two_consecutive_candidates(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["1", "2", "3", "4", "5"],
                "trend_score": [-0.8, -0.9, 0.9, -0.8, 0.9],
                "volatility_score": [0.0] * 5,
                "liquidity_score": [0.0] * 5,
                "macro_score": [0.0] * 5,
                "global_risk_score": [0.0] * 5,
            }
        )

        result = classify_regimes(frame)

        self.assertEqual(result.iloc[1]["trend_regime"], "down")
        self.assertEqual(result.iloc[2]["trend_regime"], "down")
        self.assertEqual(result.iloc[-1]["trend_regime"], "down")

    def test_low_coverage_is_unknown_and_probabilities_are_bounded(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["1", "2"],
                "trend_score": [np.nan, 0.8],
                "volatility_score": [np.nan, np.nan],
                "liquidity_score": [np.nan, np.nan],
                "macro_score": [np.nan, np.nan],
                "global_risk_score": [np.nan, np.nan],
            }
        )

        result = classify_regimes(frame)

        self.assertEqual(result.iloc[-1]["composite_regime"], "unknown")
        probability_columns = [column for column in result if column.endswith("_transition_probability")]
        for column in probability_columns:
            finite = result[column].dropna()
            self.assertTrue(finite.between(0.0, 1.0).all())


if __name__ == "__main__":
    unittest.main()
