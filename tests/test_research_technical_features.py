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


def _long_ohlcv_frame() -> pd.DataFrame:
    index = np.arange(180, dtype=float)
    close = 20.0 + index * 0.015 + np.sin(index / 4.0) * 0.8
    volume = 1_000_000.0 + index * 2_000.0 + np.cos(index / 5.0) * 100_000.0
    return pd.DataFrame(
        {
            "code": ["000001"] * len(index),
            "trade_date": pd.date_range(
                "2025-08-01", periods=len(index), freq="B"
            ).strftime("%Y%m%d"),
            "open": close * (1.0 - 0.002),
            "high": close * (1.0 + 0.015),
            "low": close * (1.0 - 0.012),
            "close": close,
            "volume": volume,
            "amount": volume * close,
            "turnover_rate": 1.0 + np.sin(index / 7.0) * 0.2,
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

    def test_registry_excludes_raw_price_and_cumulative_volume_levels(self):
        definitions = {item.name: item for item in DEFAULT_REGISTRY}

        self.assertFalse({
            "sma_5", "sma_10", "sma_20", "sma_60", "ema_12", "ema_26",
            "macd_dif", "macd_dea", "macd_hist", "atr_14", "obv", "ad",
        }.intersection(definitions))
        self.assertTrue({
            "sma_distance_5", "sma_distance_20", "ema_distance_12",
            "macd_dif_pct", "macd_hist_pct", "obv_flow_5", "ad_flow_5",
        }.issubset(definitions))

    def test_registry_versions_alpha158_lite_stationary_features(self):
        definitions = {item.name: item for item in DEFAULT_REGISTRY}

        expected = {
            "momentum_10",
            "momentum_120",
            "downside_volatility_20",
            "drawdown_60",
            "amihud_illiquidity_20",
            "volume_price_correlation_20",
            "up_volume_ratio_20",
            "price_volume_confirmation_20",
        }
        self.assertTrue(expected.issubset(definitions))
        self.assertTrue(
            all(definitions[name].version == "alpha158-lite-technical-v1" for name in expected)
        )


class TechnicalFeaturesTest(unittest.TestCase):
    def test_declared_non_yuan_amount_fails_closed(self):
        frame = _ohlcv_frame()
        frame["amount_unit"] = "thousand_yuan"

        with self.assertRaisesRegex(ValueError, "research_amount_unit_mismatch"):
            compute_technical_features(frame)

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

    def test_alpha158_lite_price_volume_features_are_finite_after_warmup(self):
        features = compute_technical_features(_long_ohlcv_frame())

        for column in (
            "momentum_10",
            "momentum_120",
            "reversal_5",
            "realized_volatility_5",
            "realized_volatility_60",
            "downside_volatility_20",
            "intraday_range",
            "close_location",
            "drawdown_60",
            "breakout_20",
            "amount_zscore_20",
            "amihud_illiquidity_20",
            "volume_price_correlation_20",
            "up_volume_ratio_20",
            "turnover_change_20",
            "price_volume_confirmation_20",
        ):
            self.assertIn(column, features.columns)
            self.assertTrue(np.isfinite(features.iloc[-1][column]), column)

    def test_missing_volume_keeps_price_features_and_nulls_volume_features(self):
        frame = _ohlcv_frame().drop(columns=["volume", "amount", "turnover_rate"])

        features = compute_technical_features(frame)

        self.assertTrue(np.isfinite(features.iloc[-1]["sma_20"]))
        self.assertTrue(np.isnan(features.iloc[-1]["volume_ratio_5_20"]))
        self.assertTrue(np.isnan(features.iloc[-1]["mfi_14"]))

    def test_suspended_calendar_row_does_not_poison_post_resume_indicators(self):
        dates = pd.date_range("2026-01-02", periods=90, freq="B")
        close = np.linspace(10.0, 15.0, len(dates))
        frame = pd.DataFrame({
            "code": "000001",
            "trade_date": dates.strftime("%Y%m%d"),
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1_000_000.0,
            "amount": 20_000_000.0,
            "turnover_rate": 1.0,
            "is_suspended": False,
        })
        suspended_index = 40
        frame.loc[suspended_index, [
            "open", "high", "low", "close", "volume", "amount", "turnover_rate"
        ]] = np.nan
        frame.loc[suspended_index, "is_suspended"] = True

        features = compute_technical_features(frame)

        self.assertTrue(pd.isna(features.loc[suspended_index, "sma_20"]))
        self.assertTrue(pd.isna(features.loc[suspended_index, "momentum_20"]))
        self.assertTrue(np.isfinite(features.iloc[-1]["sma_20"]))
        self.assertTrue(np.isfinite(features.iloc[-1]["macd_hist"]))

    def test_predictive_technical_features_are_invariant_to_price_scale(self):
        frame = _ohlcv_frame()
        scaled = frame.copy()
        scaled[["open", "high", "low", "close", "amount"]] *= 10.0

        original_features = compute_technical_features(frame)
        scaled_features = compute_technical_features(scaled)

        for column in (
            "sma_distance_5", "sma_distance_20", "ema_distance_12",
            "macd_dif_pct", "macd_dea_pct", "macd_hist_pct",
            "macd_hist_slope_pct", "natr_14", "bollinger_position",
            "bollinger_width", "obv_flow_5", "ad_flow_5",
            "momentum_10", "momentum_120", "reversal_5",
            "realized_volatility_5", "realized_volatility_60",
            "downside_volatility_20", "intraday_range", "close_location",
            "drawdown_60", "breakout_20", "amount_zscore_20",
            "amihud_illiquidity_20", "volume_price_correlation_20",
            "up_volume_ratio_20", "price_volume_confirmation_20",
        ):
            np.testing.assert_allclose(
                original_features[column],
                scaled_features[column],
                equal_nan=True,
                atol=1e-10,
            )


if __name__ == "__main__":
    unittest.main()
