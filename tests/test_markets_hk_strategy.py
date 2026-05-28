"""Smoke tests for HK strategy.build_signals."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from stock_analyze.markets.hk import build_signals, make_provider
from stock_analyze.markets.hk.strategy import _HK_FACTOR_DIRECTIONS


class HKPublicAPITests(unittest.TestCase):
    def test_public_api_exposes_six_callables(self):
        from stock_analyze.markets import hk
        for name in ("make_provider", "build_signals", "execute_due_orders",
                     "update_nav", "generate_rebalance_orders", "initialize"):
            self.assertTrue(callable(getattr(hk, name)),
                            msg=f"hk.{name} missing or not callable")


class FactorDirectionsTests(unittest.TestCase):
    def test_low_factors_have_low_direction(self):
        for name in ("pe", "pb", "low_volatility_60"):
            self.assertEqual(_HK_FACTOR_DIRECTIONS[name], "low")

    def test_high_factors_have_high_direction(self):
        for name in ("momentum_20", "momentum_60", "dividend_yield"):
            self.assertEqual(_HK_FACTOR_DIRECTIONS[name], "high")


class BuildSignalsTests(unittest.TestCase):
    def test_build_signals_produces_per_account_rows(self):
        # Mock provider.spot to return a small DataFrame
        provider = MagicMock()

        def fake_spot(scope):
            return pd.DataFrame({
                "code": ["0700.HK", "9988.HK", "0005.HK"],
                "pe": [12.0, 15.0, 10.0],
                "pb": [1.5, 2.0, 0.9],
                "momentum_20": [0.05, 0.02, 0.07],
                "momentum_60": [0.10, 0.05, 0.12],
                "low_volatility_60": [0.01, 0.02, 0.015],
                "dividend_yield": [0.03, 0.02, 0.04],
            })

        provider.spot.side_effect = fake_spot

        config = {
            "accounts": [
                {"id": "hsi", "scope": "hsi", "cash": 500000, "benchmark": "^HSI", "top_n": 50},
            ],
            "factors": {
                "pe": {"weight": 0.5, "direction": "low"},
                "momentum_20": {"weight": 0.5, "direction": "high"},
            },
            "factor_processing": {
                "neutralize_industry": False,
                "min_factor_coverage": 0.0,
            },
        }
        rows = build_signals(config, provider, as_of=date(2026, 6, 16))
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn("code", row)
            self.assertIn("account_id", row)
            self.assertEqual(row["account_id"], "hsi")
            self.assertIn("score", row)
            self.assertIsInstance(row["score"], float)

    def test_build_signals_handles_empty_spot(self):
        provider = MagicMock()
        provider.spot.return_value = pd.DataFrame()
        config = {
            "accounts": [{"id": "hsi", "scope": "hsi", "cash": 500000,
                           "benchmark": "^HSI", "top_n": 50}],
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "factor_processing": {},
        }
        rows = build_signals(config, provider)
        self.assertEqual(rows, [])

    def test_build_signals_accepts_flat_overlay_weights(self):
        """Backwards-compat: flat {factor: weight} overlay also works."""
        provider = MagicMock()
        provider.spot.return_value = pd.DataFrame({
            "code": ["0700.HK", "9988.HK"],
            "pe": [12.0, 15.0],
            "momentum_20": [0.05, 0.02],
        })
        config = {
            "accounts": [{"id": "hsi", "scope": "hsi", "cash": 500000,
                           "benchmark": "^HSI", "top_n": 50}],
            "factors": {"pe": 0.5, "momentum_20": 0.5},  # flat, not nested
            "factor_processing": {"min_factor_coverage": 0.0},
        }
        rows = build_signals(config, provider)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
