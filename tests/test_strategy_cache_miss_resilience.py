"""Tests that build_signals tolerates per-stock CacheMiss without killing the run.

Bug history: 2026-05-23 weekly run failed for both agents because a single
CacheMiss (missing history_300750_20260522_260) in the per-stock loop bubbled
up and aborted before any candidate was collected. Fix: catch CacheMiss in
the loop, skip the stock, accumulate a warning.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from stock_analyze.markets.a_share import strategy
from stock_analyze.markets.a_share.data_provider import CacheMiss


class CacheMissResilienceTests(unittest.TestCase):
    def setUp(self):
        self.universe = pd.DataFrame({
            "code": ["000001", "000002", "300750"],
            "name": ["平安银行", "万科A", "宁德时代"],
            "latest_price": [12.0, 20.0, 250.0],
            "pe": [5.5, 12.0, 30.0],
            "pb": [1.1, 1.8, 5.0],
            "market_cap_yi": [2500.0, 2800.0, 5500.0],
        })

    def _provider_with_missing_300750(self):
        """Provider that raises CacheMiss for stock 300750 only."""
        provider = MagicMock()
        provider.universe.return_value = self.universe

        def basic_info(code):
            if code == "300750":
                raise CacheMiss(method="basic_info",
                                cache_name=f"basic_{code}_20260522")
            return {"listing_date": "2010-01-01", "industry": "金融",
                    "name": "stub", "market_cap_yi": 1000.0}

        def valuation_metrics(code):
            return {"pe": 10.0, "pb": 1.5}

        def financial_metrics(code, as_of=None):
            return {"roe": 0.1, "gross_margin": 0.3, "debt_ratio": 0.4,
                    "net_profit_growth": 0.05}

        def price_snapshot(code, as_of=None, spot_row=None):
            snap = MagicMock()
            snap.momentum_20 = 0.02
            snap.momentum_60 = 0.05
            snap.low_volatility_60 = 0.01
            snap.avg_amount_20 = 1e9
            snap.paused = False
            snap.warning = ""
            snap.close = 12.0
            return snap

        def dividend_yield(code, as_of=None):
            return 0.04

        provider.basic_info.side_effect = basic_info
        provider.valuation_metrics.side_effect = valuation_metrics
        provider.financial_metrics.side_effect = financial_metrics
        provider.price_snapshot.side_effect = price_snapshot
        provider.dividend_yield.side_effect = dividend_yield
        return provider

    def test_cache_miss_on_one_stock_does_not_kill_run(self):
        """A CacheMiss for 1 stock should leave the other 2 as candidates."""
        config = {
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "factor_processing": {"winsorize_lower": 0.01,
                                    "winsorize_upper": 0.99,
                                    "neutralize_industry": False,
                                    "min_factor_coverage": 0.1},
            "filters": {"exclude_st": True, "max_fetch_candidates": 10,
                         "min_listing_days": 0, "min_pe": 0,
                         "min_avg_amount_20": 0, "min_market_cap_yi": 0,
                         "max_market_cap_yi": 1_000_000,
                         "require_fields": [], "fallback_require_fields": []},
        }
        account = {"id": "test", "scope": "hs300", "top_n": 5}
        provider = self._provider_with_missing_300750()

        result = strategy.build_signals(config, account, provider,
                                          as_of="2026-05-22")

        # 2 of 3 candidates collected (300750 skipped)
        self.assertEqual(len(result.candidates), 2)
        codes_in_candidates = set(result.candidates["code"])
        self.assertIn("000001", codes_in_candidates)
        self.assertIn("000002", codes_in_candidates)
        self.assertNotIn("300750", codes_in_candidates)

        # Warning carries the count + sample codes
        warn_str = ";".join(result.warnings)
        self.assertIn("cache_miss_skipped:1", warn_str)
        self.assertIn("300750", warn_str)

    def test_all_cache_miss_still_raises_clean_error(self):
        """If EVERY stock cache-misses, we still raise the existing error
        (so operator knows nothing was salvageable)."""
        provider = MagicMock()
        provider.universe.return_value = self.universe
        provider.basic_info.side_effect = lambda code: (_ for _ in ()).throw(
            CacheMiss(method="basic_info", cache_name=f"basic_{code}")
        )
        config = {
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "filters": {"max_fetch_candidates": 10,
                         "require_fields": [], "fallback_require_fields": []},
            "factor_processing": {},
        }
        account = {"id": "test", "scope": "hs300", "top_n": 5}

        with self.assertRaises(RuntimeError) as ctx:
            strategy.build_signals(config, account, provider,
                                    as_of="2026-05-22")
        self.assertIn("No candidates", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
