import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.event_study import build_event_study


class ResearchEventStudyTest(unittest.TestCase):
    def setUp(self):
        count = 40
        self.events = pd.DataFrame(
            {
                "event_id": [f"e-{index}" for index in range(count)],
                "event": ["macd_golden_cross"] * count,
                "market": ["a_share"] * count,
                "code": [f"{index:06d}" for index in range(count)],
                "trade_date": ["20260710"] * count,
                "regime": ["up"] * count,
                "industry": ["科技"] * count,
            }
        )
        returns = np.linspace(-0.03, 0.08, count)
        self.labels = pd.DataFrame(
            {
                "code": self.events["code"],
                "trade_date": ["20260710"] * count,
                "horizon": [5] * count,
                "label": ["up" if value > 0.005 else "down" if value < -0.005 else "flat" for value in returns],
                "excess_return": returns,
                "max_favorable_excursion": returns + 0.02,
                "max_adverse_excursion": returns - 0.02,
            }
        )

    def test_computes_conditional_statistics_and_seeded_intervals(self):
        first = build_event_study(self.events, self.labels, min_support=30, bootstrap_samples=200, seed=7)
        second = build_event_study(self.events, self.labels, min_support=30, bootstrap_samples=200, seed=7)

        row = first.iloc[0]
        self.assertEqual(row["observations"], 40)
        self.assertFalse(bool(row["research_only"]))
        self.assertGreater(row["up_rate"], row["down_rate"])
        self.assertLessEqual(row["bootstrap_ci_low"], row["mean_excess"])
        self.assertGreaterEqual(row["bootstrap_ci_high"], row["mean_excess"])
        pd.testing.assert_frame_equal(first, second)

    def test_marks_low_support_groups_research_only(self):
        result = build_event_study(self.events.iloc[:5], self.labels.iloc[:5], min_support=30)
        self.assertTrue(bool(result.iloc[0]["research_only"]))


if __name__ == "__main__":
    unittest.main()
