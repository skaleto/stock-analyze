import unittest

import numpy as np
import pandas as pd
import talib

from stock_analyze.research.feature_registry import (
    DEFAULT_REGISTRY,
    FeatureDefinition,
    registry_hash,
)
from stock_analyze.research.technical_features import compute_technical_features


def _ohlcv_frame() -> pd.DataFrame:
    decline = np.linspace(30.0, 20.0, 49)
    close = np.concatenate([decline, np.array([36.0])])
    return pd.DataFrame(
        {
            "code": ["000001"] * len(close),
            "trade_date": pd.date_range("2026-04-01", periods=len(close), freq="B").strftime("%Y%m%d"),
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.6,
            "close": close,
            "volume": np.linspace(1_000_000, 2_000_000, len(close)),
            "amount": np.linspace(20_000_000, 60_000_000, len(close)),
            "turnover_rate": np.linspace(0.8, 2.1, len(close)),
        }
    )


class FeatureRegistryTest(unittest.TestCase):
    def test_registry_hash_is_order_independent(self):
        first = FeatureDefinition("momentum_20", "technical", 20, 0, "v1")
        second = FeatureDefinition("roe_ttm", "fundamental", 0, 1, "v1")

        self.assertEqual(registry_hash([second, first]), registry_hash([first, second]))

    def test_registry_includes_industry_chain_and_value_creation_features(self):
        definitions = {item.name: item for item in DEFAULT_REGISTRY}

        self.assertTrue({
            "profit_pool_concentration",
            "high_value_add_proxy",
            "declining_marginal_cost_proxy",
            "pricing_power_persistence",
            "industry_cycle_score",
        }.issubset(definitions))
        self.assertEqual(definitions["high_value_add_proxy"].family, "industry_chain")
        self.assertEqual(definitions["high_value_add_proxy"].direction, "high")
        self.assertIn("a_share", definitions["high_value_add_proxy"].markets)


class TechnicalFeaturesTest(unittest.TestCase):
    def test_macd_cross_and_volume_features(self):
        features = compute_technical_features(_ohlcv_frame())

        self.assertIn("macd_hist_slope", features.columns)
        self.assertIn("volume_ratio_5_20", features.columns)
        self.assertIn("turnover_percentile_60", features.columns)
        self.assertEqual(features.iloc[-1]["macd_cross"], 1.0)

    def test_macd_matches_talib_canonical_values(self):
        frame = _ohlcv_frame()
        features = compute_technical_features(frame)
        expected_dif, expected_dea, expected_hist = talib.MACD(
            frame["close"].to_numpy(dtype=float),
            fastperiod=12,
            slowperiod=26,
            signalperiod=9,
        )

        self.assertAlmostEqual(features.iloc[-1]["macd_dif"], expected_dif[-1], places=8)
        self.assertAlmostEqual(features.iloc[-1]["macd_dea"], expected_dea[-1], places=8)
        self.assertAlmostEqual(features.iloc[-1]["macd_hist"], expected_hist[-1], places=8)

    def test_missing_volume_keeps_price_features_and_nulls_volume_features(self):
        frame = _ohlcv_frame().drop(columns=["volume", "amount", "turnover_rate"])

        features = compute_technical_features(frame)

        self.assertTrue(np.isfinite(features.iloc[-1]["sma_20"]))
        self.assertTrue(np.isnan(features.iloc[-1]["volume_ratio_5_20"]))
        self.assertTrue(np.isnan(features.iloc[-1]["mfi_14"]))


if __name__ == "__main__":
    unittest.main()
