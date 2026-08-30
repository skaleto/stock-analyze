from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.signals import (
    dynamic_target_weights,
    fixed_target_weights,
)


def _momentum_observations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "role": ["equity", "bond", "cash", "gold"] * 4,
            "months_ago": [12] * 4 + [6] * 4 + [1] * 4 + [0] * 4,
            "adjusted_close": [
                100,
                100,
                100,
                100,
                110,
                103,
                101,
                120,
                120,
                105,
                102,
                130,
                121,
                106,
                103,
                131,
            ],
        }
    )


class PermanentPortfolioSignalTests(unittest.TestCase):
    def test_fixed_rule_does_not_trade_inside_band(self) -> None:
        result = fixed_target_weights(
            {"equity": 0.30, "bond": 0.20, "cash": 0.25, "gold": 0.25},
            lower=0.15,
            upper=0.35,
        )

        self.assertIsNone(result)

    def test_fixed_rule_includes_exact_boundaries(self) -> None:
        result = fixed_target_weights(
            {"equity": 0.35, "bond": 0.15, "cash": 0.25, "gold": 0.25},
            lower=0.15,
            upper=0.35,
        )

        self.assertIsNone(result)

    def test_fixed_rule_restores_all_assets_after_breach(self) -> None:
        result = fixed_target_weights(
            {"equity": 0.36, "bond": 0.14, "cash": 0.25, "gold": 0.25},
            lower=0.15,
            upper=0.35,
        )

        self.assertEqual(
            result,
            {"equity": 0.25, "bond": 0.25, "cash": 0.25, "gold": 0.25},
        )

    def test_dynamic_rule_maps_rank_to_frozen_weights(self) -> None:
        weights = dynamic_target_weights(_momentum_observations())

        self.assertEqual(weights["gold"], 0.40)
        self.assertEqual(weights["equity"], 0.30)
        self.assertEqual(weights["bond"], 0.20)
        self.assertEqual(weights["cash"], 0.10)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreaterEqual(min(weights.values()), 0.10)
        self.assertLessEqual(max(weights.values()), 0.40)

    def test_dynamic_rule_is_input_order_independent(self) -> None:
        observations = _momentum_observations()

        expected = dynamic_target_weights(observations)
        actual = dynamic_target_weights(
            observations.sample(frac=1.0, random_state=7)
        )

        self.assertEqual(actual, expected)

    def test_dynamic_ties_use_frozen_order(self) -> None:
        observations = _momentum_observations()
        observations["adjusted_close"] = 100.0

        weights = dynamic_target_weights(observations)

        self.assertEqual(
            weights,
            {"cash": 0.40, "bond": 0.30, "gold": 0.20, "equity": 0.10},
        )

    def test_dynamic_rule_fails_when_window_is_incomplete(self) -> None:
        with self.assertRaisesRegex(ValueError, "momentum_window"):
            dynamic_target_weights(pd.DataFrame())


if __name__ == "__main__":
    unittest.main()
