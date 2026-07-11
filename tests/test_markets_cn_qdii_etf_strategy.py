from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from stock_analyze.markets.cn_qdii_etf import build_signals
from stock_analyze.markets.cn_qdii_etf.strategy import ETF_FACTOR_DIRECTIONS


class ETFStrategyTests(unittest.TestCase):
    def test_factor_directions_are_etf_native(self):
        self.assertEqual(ETF_FACTOR_DIRECTIONS["momentum_20"], "high")
        self.assertEqual(ETF_FACTOR_DIRECTIONS["momentum_60"], "high")
        self.assertEqual(ETF_FACTOR_DIRECTIONS["avg_amount_20"], "high")
        self.assertEqual(ETF_FACTOR_DIRECTIONS["low_volatility_60"], "low")
        self.assertEqual(ETF_FACTOR_DIRECTIONS["discount_premium"], "low")

    def test_build_signals_scores_each_account_scope(self):
        provider = MagicMock()

        def fake_spot(scope: str):
            return pd.DataFrame(
                {
                    "code": [f"{scope}-A", f"{scope}-B", f"{scope}-C"],
                    "momentum_20": [0.10, 0.05, 0.02],
                    "momentum_60": [0.20, 0.10, 0.04],
                    "low_volatility_60": [0.01, 0.03, 0.02],
                    "avg_amount_20": [2_000_000, 1_000_000, 500_000],
                    "discount_premium": [-0.01, 0.02, 0.00],
                    "industry": [scope, scope, scope],
                }
            )

        provider.spot.side_effect = fake_spot
        config = {
            "agent_id": "codex",
            "accounts": [
                {"id": "us_exposure", "scope": "us_exposure", "top_n": 2},
                {"id": "hk_exposure", "scope": "hk_exposure", "top_n": 2},
            ],
            "factors": {
                "momentum_20": {"weight": 0.35, "direction": "high"},
                "low_volatility_60": {"weight": 0.25, "direction": "low"},
                "avg_amount_20": {"weight": 0.25, "direction": "high"},
                "discount_premium": {"weight": 0.15, "direction": "low"},
            },
            "factor_processing": {
                "neutralize_industry": False,
                "min_factor_coverage": 0.0,
            },
        }

        rows = build_signals(config, provider, as_of=date(2026, 7, 9))

        self.assertEqual(len(rows), 6)
        self.assertEqual(
            {row["account_id"] for row in rows},
            {"us_exposure", "hk_exposure"},
        )
        for row in rows:
            self.assertIn("score", row)
            self.assertIsInstance(row["score"], float)
            self.assertIn("reason", row)

    def test_build_signals_applies_liquidity_listing_and_candidate_filters(self):
        provider = MagicMock()
        provider.spot.return_value = pd.DataFrame(
            {
                "code": ["LIQ-A", "LIQ-B", "LOW_LIQ", "RECENT", "PAUSED"],
                "momentum_20": [0.05, 0.04, 0.9, 0.8, 0.7],
                "avg_amount_20": [200_000, 100_000, 1.0, 900_000, 800_000],
                "listing_age_days": [100, 100, 100, 5, 100],
                "paused": [False, False, False, False, True],
                "industry": ["us_exposure"] * 5,
            }
        )
        config = {
            "accounts": [{"id": "us_exposure", "scope": "us_exposure", "top_n": 2}],
            "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
            "factor_processing": {"neutralize_industry": False, "min_factor_coverage": 0.0},
            "filters": {
                "max_fetch_candidates": 2,
                "min_listing_days": 30,
                "min_avg_amount_20": 50_000,
            },
        }

        rows = build_signals(config, provider, as_of=date(2026, 7, 9))
        codes = {row["code"] for row in rows}

        self.assertEqual(codes, {"LIQ-A", "LIQ-B"})
        self.assertLessEqual(len(rows), config["filters"]["max_fetch_candidates"])


if __name__ == "__main__":
    unittest.main()
