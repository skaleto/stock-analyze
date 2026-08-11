import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.event_study import (
    _bootstrap_mean_interval,
    build_event_study,
    build_event_study_from_parquet,
)


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

    def test_parquet_workflow_matches_in_memory_statistics(self):
        expected = build_event_study(
            self.events,
            self.labels,
            min_support=30,
            bootstrap_samples=200,
            seed=7,
        )
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.parquet"
            labels_path = Path(tmp) / "labels.parquet"
            self.events.to_parquet(events_path, index=False)
            self.labels.to_parquet(labels_path, index=False)
            actual = build_event_study_from_parquet(
                events_path,
                labels_path,
                min_support=30,
                bootstrap_samples=200,
                seed=7,
            )

        pd.testing.assert_frame_equal(actual, expected)

    def test_large_groups_use_linear_memory_mean_distribution(self):
        class GuardedRng:
            def integers(self, *_args, **_kwargs):
                raise AssertionError("large_group_must_not_resample_observations")

            def normal(self, *, loc, scale, size):
                self.loc = loc
                self.scale = scale
                return np.linspace(loc - 2 * scale, loc + 2 * scale, size)

        rng = GuardedRng()
        values = np.arange(5_000, dtype=float)

        low, high = _bootstrap_mean_interval(
            values,
            1_000,
            rng,
        )

        self.assertAlmostEqual(rng.loc, float(values.mean()))
        self.assertAlmostEqual(rng.scale, float(values.std(ddof=1) / np.sqrt(len(values))))
        self.assertLess(low, values.mean())
        self.assertGreater(high, values.mean())


if __name__ == "__main__":
    unittest.main()
