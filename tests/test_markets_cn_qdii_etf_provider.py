from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.markets.a_share.data_provider import CacheMiss
from stock_analyze.markets.cn_qdii_etf.data_provider import (
    CNQDIETFProvider,
    ETFExecutionQuote,
    ETFPriceSnapshot,
    make_provider,
    normalize_ts_code,
)


class FakeTushareClient:
    def __init__(self) -> None:
        self.daily_calls: list[dict] = []
        self.nav_calls: list[dict] = []
        self.basic_calls: list[dict] = []

    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        return _daily_frame()

    def fund_nav(self, **kwargs):
        self.nav_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "513100.SH",
                    "ann_date": "20260709",
                    "nav_date": "20260708",
                    "unit_nav": 2.0,
                    "accum_nav": 2.0,
                    "adj_nav": 2.0,
                }
            ]
        )

    def fund_basic(self, **kwargs):
        self.basic_calls.append(kwargs)
        return pd.DataFrame(
            [
                {"ts_code": "513100.SH", "name": "国泰纳斯达克100ETF(QDII)", "fund_type": "QDII"},
                {"ts_code": "159941.SZ", "name": "广发纳指100ETF(QDII)", "fund_type": "QDII"},
            ]
        )


class BoundedTushareClient(FakeTushareClient):
    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        frame = _daily_frame()
        return frame.loc[frame["trade_date"] <= "20260710"].copy()


def _daily_frame() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-04-01")
    for i in range(110):
        d = start + pd.Timedelta(days=i)
        if d.weekday() >= 5:
            continue
        close = 1.0 + i * 0.01
        rows.append(
            {
                "ts_code": "513100.SH",
                "trade_date": d.strftime("%Y%m%d"),
                "open": close - 0.005,
                "high": close + 0.01,
                "low": close - 0.01,
                "close": close,
                "vol": 1000 + i,
                "amount": 100_000 + i * 1000,
            }
        )
    return pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)


class CodeNormalizationTests(unittest.TestCase):
    def test_normalize_ts_code_infers_exchange_suffix(self):
        self.assertEqual(normalize_ts_code("513100"), "513100.SH")
        self.assertEqual(normalize_ts_code("159941"), "159941.SZ")
        self.assertEqual(normalize_ts_code("513100.SH"), "513100.SH")


class ProviderSnapshotTests(unittest.TestCase):
    def test_make_provider_is_lazy_without_tushare_token(self):
        provider = make_provider(cache_dir=None, offline=False, as_of="2026-07-09")
        self.assertIsInstance(provider, CNQDIETFProvider)

    def test_price_snapshot_computes_etf_factors_and_nav_discount(self):
        provider = CNQDIETFProvider(pro_client=FakeTushareClient(), cache_dir=None)
        snap = provider.price_snapshot("513100.SH", as_of="2026-07-09")

        self.assertIsInstance(snap, ETFPriceSnapshot)
        self.assertEqual(snap.code, "513100.SH")
        self.assertEqual(snap.name, "国泰纳斯达克100ETF(QDII)")
        self.assertEqual(snap.nav_date, "2026-07-08")
        self.assertIsNotNone(snap.momentum_20)
        self.assertIsNotNone(snap.momentum_60)
        self.assertIsNotNone(snap.low_volatility_60)
        self.assertIsNotNone(snap.avg_amount_20)
        self.assertAlmostEqual(snap.discount_premium, (snap.close / 2.0) - 1.0, places=6)
        self.assertFalse(snap.paused)

    def test_spot_returns_one_row_per_scope_member(self):
        provider = CNQDIETFProvider(pro_client=FakeTushareClient(), cache_dir=None)
        df = provider.spot("us_exposure")

        self.assertIn("513100.SH", set(df["code"]))
        self.assertIn("momentum_20", df.columns)
        self.assertIn("avg_amount_20", df.columns)
        self.assertIn("discount_premium", df.columns)
        self.assertEqual(set(df["industry"].dropna()), {"us_exposure"})

    def test_execution_quote_uses_next_open_with_slippage(self):
        provider = CNQDIETFProvider(pro_client=FakeTushareClient(), cache_dir=None)
        quote = provider.execution_quote("513100.SH", execute_after="2026-05-05", side="buy")

        self.assertIsInstance(quote, ETFExecutionQuote)
        self.assertGreaterEqual(quote.trade_date, "2026-05-05")
        raw_open = _daily_frame().sort_values("trade_date")
        raw_open = raw_open[raw_open["trade_date"] >= "20260505"].iloc[0]["open"]
        self.assertAlmostEqual(quote.price, raw_open * 1.0005, places=6)

    def test_execution_quote_does_not_backfill_a_prior_close(self):
        provider = CNQDIETFProvider(pro_client=BoundedTushareClient(), cache_dir=None)

        quote = provider.execution_quote(
            "513100.SH",
            execute_after="2026-07-13",
            side="buy",
            as_of="2026-07-13",
        )

        self.assertTrue(quote.paused)
        self.assertIsNone(quote.trade_date)
        self.assertIsNone(quote.price)

    def test_offline_cache_miss_raises_structured_cache_miss(self):
        with TemporaryDirectory() as tmp:
            provider = CNQDIETFProvider(
                pro_client=FakeTushareClient(),
                cache_dir=tmp,
                offline=True,
                as_of="2026-07-09",
            )
            with self.assertRaises(CacheMiss):
                provider.price_snapshot("513100.SH", as_of="2026-07-09")

    def test_cache_reuse_avoids_second_fund_daily_call(self):
        with TemporaryDirectory() as tmp:
            client = FakeTushareClient()
            first = CNQDIETFProvider(pro_client=client, cache_dir=tmp, as_of="2026-07-09")
            first.price_snapshot("513100.SH", as_of="2026-07-09")
            self.assertEqual(len(client.daily_calls), 1)

            second = CNQDIETFProvider(
                pro_client=client,
                cache_dir=tmp,
                offline=True,
                as_of="2026-07-09",
            )
            second.price_snapshot("513100.SH", as_of="2026-07-09")
            self.assertEqual(len(client.daily_calls), 1)


if __name__ == "__main__":
    unittest.main()
