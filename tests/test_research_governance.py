import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.governance import (
    TrialRegistry,
    build_aligned_trial_return_matrix,
    deflated_sharpe_probability,
    probability_of_backtest_overfit,
    evaluate_campaign_governance,
)


class ResearchGovernanceTest(unittest.TestCase):
    def test_trial_registry_is_append_only_and_counts_protocol_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = TrialRegistry(Path(tmp) / "trials.jsonl")
            registry.record({"model_version": "v1", "protocol": "p1", "sharpe": 0.4})
            registry.record({"model_version": "v2", "protocol": "p1", "sharpe": 0.7})

            rows = registry.read()

        self.assertEqual([row["model_version"] for row in rows], ["v1", "v2"])
        self.assertEqual(rows[-1]["trial_number"], 2)
        self.assertEqual(rows[-1]["protocol_trial_number"], 2)

    def test_deflated_sharpe_penalizes_repeated_trials(self):
        few = deflated_sharpe_probability(
            observed_sharpe=1.0,
            trial_sharpes=[0.2, 1.0],
            observations=252,
            skew=0.0,
            kurtosis=3.0,
        )
        many = deflated_sharpe_probability(
            observed_sharpe=1.0,
            trial_sharpes=list(np.linspace(-0.2, 1.0, 80)),
            observations=252,
            skew=0.0,
            kurtosis=3.0,
        )

        self.assertGreater(few, many)
        self.assertGreaterEqual(few, 0.0)
        self.assertLessEqual(few, 1.0)

    def test_pbo_is_fail_closed_without_enough_trials(self):
        insufficient = pd.DataFrame({"only": [0.01, -0.01, 0.02, 0.0]})
        self.assertEqual(probability_of_backtest_overfit(insufficient), 1.0)

    def test_pbo_detects_unstable_trial_selection(self):
        rng = np.random.default_rng(13)
        first = rng.normal(0.03, 0.01, size=(20, 4))
        second = rng.normal(-0.03, 0.01, size=(20, 4))
        second = second[:, ::-1]
        returns = pd.DataFrame(np.vstack([first, second]), columns=["a", "b", "c", "d"])

        pbo = probability_of_backtest_overfit(returns, block_count=4)

        self.assertGreaterEqual(pbo, 0.0)
        self.assertLessEqual(pbo, 1.0)

    def test_trial_return_matrix_requires_identical_oos_dates(self):
        trials = [
            {
                "trial_id": "a",
                "oos_returns": [
                    {"date": "2026-01-02", "return": 0.01},
                    {"date": "2026-01-05", "return": 0.02},
                ],
            },
            {
                "trial_id": "b",
                "oos_returns": [
                    {"date": "2026-01-02", "return": -0.01},
                    {"date": "2026-01-06", "return": 0.03},
                ],
            },
        ]

        with self.assertRaisesRegex(ValueError, "trial_oos_dates_misaligned"):
            build_aligned_trial_return_matrix(trials)

    def test_trial_return_matrix_preserves_dates_and_trial_ids(self):
        trials = [
            {
                "trial_id": trial_id,
                "oos_returns": [
                    {"date": date, "return": value + offset}
                    for date, value in (("2026-01-02", 0.01), ("2026-01-05", -0.02))
                ],
            }
            for trial_id, offset in (("a", 0.0), ("b", 0.01))
        ]

        matrix = build_aligned_trial_return_matrix(trials)

        self.assertEqual(matrix.index.tolist(), ["2026-01-02", "2026-01-05"])
        self.assertEqual(matrix.columns.tolist(), ["a", "b"])

    def test_deflated_sharpe_uses_consistent_annualization_scale(self):
        period_probability = deflated_sharpe_probability(
            observed_sharpe=0.08,
            trial_sharpes=[0.02, 0.04, 0.08, 0.01],
            observations=120,
        )
        annual_probability = deflated_sharpe_probability(
            observed_sharpe=0.08 * np.sqrt(252.0),
            trial_sharpes=[
                value * np.sqrt(252.0) for value in (0.02, 0.04, 0.08, 0.01)
            ],
            observations=120,
            periods_per_year=252.0,
        )

        self.assertAlmostEqual(period_probability, annual_probability)

    def test_campaign_governance_counts_comparable_legacy_trials(self):
        dates = pd.date_range("2025-01-02", periods=40, freq="B").strftime("%Y%m%d")
        trials = [
            {
                "trial_id": trial_id,
                "oos_returns": [
                    {"date": day, "return": float(value)}
                    for day, value in zip(dates, returns)
                ],
            }
            for trial_id, returns in (
                ("selected", np.full(40, 0.002)),
                ("campaign-2", np.linspace(-0.002, 0.002, 40)),
                ("campaign-3", np.linspace(0.002, -0.002, 40)),
            )
        ]
        legacy = [{
            "trial_id": "legacy-1",
            "oos_returns": [
                {"date": day, "return": float(value)}
                for day, value in zip(dates, np.full(40, -0.001))
            ],
        }]

        result = evaluate_campaign_governance(
            trials,
            selected_trial_id="selected",
            legacy_trials=legacy,
        )

        self.assertEqual(result["valid_trial_count"], 4)
        self.assertEqual(result["selected_trial_id"], "selected")
        self.assertGreaterEqual(result["deflated_sharpe_probability"], 0.0)
        self.assertLessEqual(result["probability_of_backtest_overfit"], 1.0)


if __name__ == "__main__":
    unittest.main()
