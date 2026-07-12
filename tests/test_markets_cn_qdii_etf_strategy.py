from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from stock_analyze.markets.cn_qdii_etf import build_signals
from stock_analyze.markets.cn_qdii_etf.strategy import (
    ETF_FACTOR_DIRECTIONS,
    resolve_min_amount_yuan,
)


class FunnelProvider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.funnels: dict[str, dict] = {}

    def spot(self, _scope: str) -> pd.DataFrame:
        return self.frame.copy()

    def record_selection_funnel(self, scope: str, payload: dict) -> None:
        self.funnels[scope] = payload


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
                "min_avg_amount_20_yuan": 50_000,
            },
        }

        rows = build_signals(config, provider, as_of=date(2026, 7, 9))
        codes = {row["code"] for row in rows}

        self.assertEqual(codes, {"LIQ-A", "LIQ-B"})
        self.assertLessEqual(len(rows), config["filters"]["max_fetch_candidates"])

    def test_build_signals_excludes_insufficient_factor_coverage(self):
        provider = MagicMock()
        provider.spot.return_value = pd.DataFrame(
            {
                "code": ["FULL", "PARTIAL"],
                "momentum_20": [0.1, 0.2],
                "momentum_60": [0.2, None],
                "avg_amount_20": [100_000, 100_000],
                "listing_age_days": [100, 100],
                "paused": [False, False],
                "industry": ["us_exposure", "us_exposure"],
            }
        )
        config = {
            "accounts": [{"id": "us_exposure", "scope": "us_exposure", "top_n": 2}],
            "factors": {
                "momentum_20": {"weight": 0.5, "direction": "high"},
                "momentum_60": {"weight": 0.5, "direction": "high"},
            },
            "factor_processing": {
                "neutralize_industry": False,
                "min_factor_coverage": 0.75,
            },
            "filters": {"min_avg_amount_20": 0, "min_listing_days": 0},
        }

        rows = build_signals(config, provider, as_of=date(2026, 7, 9))

        self.assertEqual([row["code"] for row in rows], ["FULL"])

    def test_legacy_liquidity_threshold_is_migrated_from_thousand_yuan(self):
        self.assertEqual(resolve_min_amount_yuan({"min_avg_amount_20": 50_000}), 50_000_000.0)
        self.assertEqual(
            resolve_min_amount_yuan({"min_avg_amount_20": 50_000, "min_avg_amount_20_yuan": 12_000_000}),
            12_000_000.0,
        )

    def test_etf_risk_gates_record_each_rejection_reason(self):
        provider = FunnelProvider(
            pd.DataFrame(
                {
                    "code": ["GOOD", "PAUSED", "ILLIQUID", "PREMIUM", "SMALL", "TRACKING"],
                    "name": ["good", "paused", "illiquid", "premium", "small", "tracking"],
                    "momentum_20": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                    "avg_amount_20": [80e6, 80e6, 5e6, 80e6, 80e6, 80e6],
                    "listing_age_days": [500] * 6,
                    "paused": [False, True, False, False, False, False],
                    "discount_premium": [0.01, 0.01, 0.01, 0.12, 0.01, 0.01],
                    "fund_size_yuan": [500e6, 500e6, 500e6, 500e6, 50e6, 500e6],
                    "peer_tracking_error_60": [0.01, 0.01, 0.01, 0.01, 0.01, 0.30],
                    "management_fee": [0.5] * 6,
                    "industry": ["us_exposure"] * 6,
                    "index_key": ["good", "paused", "illiquid", "premium", "small", "tracking"],
                    "theme": ["GOOD", "PAUSED", "ILLIQUID", "PREMIUM", "SMALL", "TRACKING"],
                    "exposure_group": ["美国市场"] * 6,
                    "universe_hash": ["shared-hash"] * 6,
                }
            )
        )
        config = {
            "accounts": [{"id": "us_exposure", "scope": "us_exposure", "top_n": 2}],
            "factors": {"momentum_20": {"weight": 1.0, "direction": "high"}},
            "factor_processing": {"neutralize_industry": False, "min_factor_coverage": 0.0},
            "filters": {
                "min_avg_amount_20_yuan": 10e6,
                "min_listing_days": 30,
                "min_fund_size_yuan": 100e6,
                "max_abs_premium": 0.08,
                "max_peer_tracking_error_60": 0.20,
            },
        }

        rows = build_signals(config, provider, as_of=date(2026, 7, 10))

        self.assertEqual([row["code"] for row in rows], ["GOOD"])
        self.assertEqual(rows[0]["index_key"], "good")
        funnel = provider.funnels["us_exposure"]
        self.assertEqual(funnel["universe_hash"], "shared-hash")
        self.assertEqual(funnel["stages"][-1]["key"], "factor_ready")
        self.assertEqual(funnel["stages"][-1]["count"], 1)
        reasons = {item["reason"]: item["count"] for item in funnel["rejections"]}
        self.assertEqual(reasons["paused_or_stale"], 1)
        self.assertEqual(reasons["liquidity_below_floor"], 1)
        self.assertEqual(reasons["abnormal_premium"], 1)
        self.assertEqual(reasons["fund_size_below_floor"], 1)
        self.assertEqual(reasons["peer_tracking_error_high"], 1)


if __name__ == "__main__":
    unittest.main()
