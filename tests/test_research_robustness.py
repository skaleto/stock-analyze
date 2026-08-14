from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.robustness import (
    classify_market_regimes,
    contribution_concentration,
    paired_block_bootstrap_probability,
    stationary_block_bootstrap_probability,
    summarize_regime_performance,
)


class ResearchRobustnessTest(unittest.TestCase):
    def test_stationary_bootstrap_is_seeded_and_directional(self) -> None:
        positive = np.full(80, 0.001)
        negative = np.full(80, -0.001)

        first = stationary_block_bootstrap_probability(
            positive,
            block_length=10,
            samples=10_000,
            seed=20260814,
        )
        repeated = stationary_block_bootstrap_probability(
            positive,
            block_length=10,
            samples=10_000,
            seed=20260814,
        )
        losing = stationary_block_bootstrap_probability(
            negative,
            block_length=10,
            samples=10_000,
            seed=20260814,
        )

        self.assertEqual(first, repeated)
        self.assertEqual(first, 1.0)
        self.assertEqual(losing, 0.0)

    def test_paired_bootstrap_measures_incremental_return_on_identical_dates(self) -> None:
        baseline = pd.Series([0.001, -0.001] * 40)
        challenger = baseline + 0.0005

        probability = paired_block_bootstrap_probability(
            challenger,
            baseline,
            block_length=10,
            samples=10_000,
            seed=20260814,
        )

        self.assertEqual(probability, 1.0)

    def test_market_regimes_use_frozen_sma200_and_sixty_day_thresholds(self) -> None:
        frame = pd.DataFrame({
            "date": ["20260102", "20260105", "20260106"],
            "benchmark_close": [110.0, 90.0, 101.0],
            "benchmark_sma_200": [100.0, 100.0, 100.0],
            "benchmark_momentum_60": [0.06, -0.06, 0.01],
        })

        regimes = classify_market_regimes(frame)

        self.assertEqual(regimes.tolist(), ["bull", "down", "range"])

    def test_regime_report_and_concentration_fail_closed(self) -> None:
        returns = pd.DataFrame({
            "date": ["20260102", "20260105", "20260106", "20260107"],
            "regime": ["bull", "bull", "range", "down"],
            "active_return": [0.01, -0.005, 0.002, -0.003],
        })

        report = summarize_regime_performance(returns)
        concentrated = contribution_concentration(
            {"2025": 0.008, "2026": 0.002},
        )

        self.assertEqual(set(report), {"bull", "range", "down"})
        self.assertEqual(report["bull"]["observations"], 2)
        self.assertAlmostEqual(concentrated["largest_share"], 0.8)
        self.assertFalse(concentrated["passed"])


if __name__ == "__main__":
    unittest.main()
