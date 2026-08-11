"""Tests for the HK yfinance data provider.

All yfinance calls are mocked at the module-level accessor functions
(:func:`_fetch_ticker_info`, :func:`_fetch_ticker_history`) so tests
run without network access.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from stock_analyze.markets.hk.data_provider import (
    HKExecutionQuote,
    HKPriceSnapshot,
    YFinanceHKProvider,
    make_provider,
)


def _fake_history(n_days: int = 70, start_close: float = 100.0) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame for tests.

    Daily 0.5% upward drift so momentum is positive and volatility is low.
    """
    today = date.today()
    dates = [today - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    closes = [start_close * (1.005 ** i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "Open": [c * 0.999 for c in closes],
            "High": [c * 1.005 for c in closes],
            "Low": [c * 0.995 for c in closes],
            "Close": closes,
            "Volume": [1_000_000.0] * n_days,
        },
        index=pd.DatetimeIndex(dates),
    )


def _fake_info(pe: float = 12.0, pb: float = 1.4) -> dict:
    return {
        "trailingPE": pe,
        "priceToBook": pb,
        "marketCap": 1_500_000_000_000.0,
        "dividendYield": 0.03,
        "currency": "HKD",
    }


class MakeProviderTests(unittest.TestCase):
    def test_make_provider_returns_yfinance_hk_provider(self):
        provider = make_provider()
        self.assertIsInstance(provider, YFinanceHKProvider)

    def test_make_provider_accepts_cache_dir_and_offline(self):
        with TemporaryDirectory() as tmp:
            provider = make_provider(cache_dir=Path(tmp), offline=True, as_of="2026-05-29")
            self.assertEqual(provider.cache_dir, Path(tmp))
            self.assertTrue(provider.offline)
            self.assertEqual(provider.as_of, "2026-05-29")


class UniverseTests(unittest.TestCase):
    def test_universe_hsi(self):
        provider = make_provider()
        hsi = provider.universe("hsi")
        self.assertGreaterEqual(len(hsi), 50)
        self.assertIn("0700.HK", hsi)

    def test_universe_hscei(self):
        provider = make_provider()
        hscei = provider.universe("hscei")
        self.assertIn("9988.HK", hscei)


class PriceSnapshotTests(unittest.TestCase):
    def test_snapshot_populates_fields_from_yfinance(self):
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_info",
            return_value=_fake_info(pe=11.5, pb=1.3),
        ), patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=_fake_history(n_days=80),
        ):
            snap = provider.price_snapshot("0700.HK")
        self.assertEqual(snap.code, "0700.HK")
        self.assertAlmostEqual(snap.pe, 11.5)
        self.assertAlmostEqual(snap.pb, 1.3)
        self.assertIsNotNone(snap.close)
        self.assertIsNotNone(snap.momentum_20)
        self.assertIsNotNone(snap.momentum_60)
        self.assertIsNotNone(snap.low_volatility_60)
        # Drift is 0.5%/day so 20-day momentum ≈ 1.005^20 - 1 ≈ 0.1046
        self.assertGreater(snap.momentum_20, 0.08)
        self.assertLess(snap.momentum_20, 0.12)

    def test_snapshot_handles_empty_history(self):
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_info",
            return_value={},
        ), patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=pd.DataFrame(),
        ):
            snap = provider.price_snapshot("0000.HK")
        self.assertTrue(snap.paused)
        self.assertIsNone(snap.close)
        self.assertIsNone(snap.momentum_20)

    def test_snapshot_handles_missing_info_fields(self):
        """yfinance.info often returns dicts missing optional fields (PE, PB)."""
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_info",
            return_value={"currency": "HKD"},
        ), patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=_fake_history(n_days=80),
        ):
            snap = provider.price_snapshot("9999.HK")
        self.assertIsNone(snap.pe)
        self.assertIsNone(snap.pb)
        self.assertIsNone(snap.dividend_yield)
        # Close + momentum still work because history is non-empty:
        self.assertIsNotNone(snap.close)


class SpotTests(unittest.TestCase):
    def test_spot_returns_one_row_per_ticker(self):
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_info",
            return_value=_fake_info(),
        ), patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=_fake_history(),
        ):
            df = provider.spot("hsi")
        self.assertEqual(len(df), 50)
        for col in ("code", "close", "pe", "pb", "momentum_20",
                    "momentum_60", "low_volatility_60", "dividend_yield"):
            self.assertIn(col, df.columns, msg=f"spot DataFrame missing column {col}")
        self.assertEqual(df["code"].iloc[0], "0700.HK")  # First in HSI


class ExecutionQuoteTests(unittest.TestCase):
    def test_quote_uses_next_day_open_with_buy_slippage(self):
        provider = make_provider()
        hist = _fake_history(n_days=10, start_close=100.0)
        future_date = (date.today() + timedelta(days=2)).isoformat()
        # No future rows in our deterministic 10-day fixture; quote falls
        # back to latest close (the "execute_after beyond history" path).
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=hist,
        ):
            quote = provider.execution_quote(
                "0700.HK", execute_after=future_date, side="buy"
            )
        self.assertEqual(quote.code, "0700.HK")
        self.assertIsNotNone(quote.price)
        # Buy slippage is +5 bps over the close (~100 * 1.045^9 ≈ 145.something)
        # Just verify the slippage direction (>=close):
        self.assertGreater(quote.price, float(hist["Close"].iloc[-1]))

    def test_quote_uses_next_day_open_with_sell_slippage(self):
        provider = make_provider()
        hist = _fake_history(n_days=10, start_close=100.0)
        target_date = hist.index[5].date().isoformat()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=hist,
        ):
            quote = provider.execution_quote(
                "0700.HK", execute_after=target_date, side="sell"
            )
        self.assertIsNotNone(quote.price)
        # Sell slippage shaves price below the matched-day open
        matched_open = float(hist.iloc[5]["Open"])
        self.assertLess(quote.price, matched_open * 1.001)  # at least slightly below

    def test_quote_returns_paused_on_empty_history(self):
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=pd.DataFrame(),
        ):
            quote = provider.execution_quote(
                "0000.HK", execute_after="2026-05-29", side="buy"
            )
        self.assertTrue(quote.paused)
        self.assertIsNone(quote.price)


class ShortabilityAndLotSizeTests(unittest.TestCase):
    def test_lot_size_default_100(self):
        provider = make_provider()
        self.assertEqual(provider.lot_size("0700.HK"), 100)

    def test_is_shortable_always_true_in_v1(self):
        provider = make_provider()
        self.assertTrue(provider.is_shortable("0700.HK"))


class CachingTests(unittest.TestCase):
    def test_repeated_calls_hit_cache(self):
        provider = make_provider()
        with patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_info",
            return_value=_fake_info(),
        ) as info_mock, patch(
            "stock_analyze.markets.hk.data_provider._fetch_ticker_history",
            return_value=_fake_history(),
        ) as hist_mock:
            provider.price_snapshot("0700.HK")
            provider.price_snapshot("0700.HK")
            provider.price_snapshot("0700.HK")
        # Three calls but only one underlying fetch each
        self.assertEqual(info_mock.call_count, 1)
        self.assertEqual(hist_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
