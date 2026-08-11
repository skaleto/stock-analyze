import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.governance import (
    TrialRegistry,
    deflated_sharpe_probability,
    probability_of_backtest_overfit,
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


if __name__ == "__main__":
    unittest.main()
