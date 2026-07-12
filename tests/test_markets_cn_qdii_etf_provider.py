from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.markets.a_share.data_provider import CacheMiss
from stock_analyze.markets.cn_qdii_etf.data_provider import (
    CNQDIETFProvider,
    ETFExecutionQuote,
    ETFPriceSnapshot,
    UNIVERSE_RULES_VERSION,
    UNIVERSE_SCHEMA_VERSION,
    make_provider,
    normalize_ts_code,
)


class FakeTushareClient:
    def __init__(self) -> None:
        self.daily_calls: list[dict] = []
        self.nav_calls: list[dict] = []
        self.basic_calls: list[dict] = []
        self.adj_calls: list[dict] = []
        self.share_calls: list[dict] = []

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
                {
                    "ts_code": "513100.SH",
                    "name": "国泰纳斯达克100ETF(QDII)",
                    "fund_type": "QDII",
                    "list_date": "20130515",
                },
                {
                    "ts_code": "159941.SZ",
                    "name": "广发纳指100ETF(QDII)",
                    "fund_type": "QDII",
                    "list_date": "20150610",
                },
            ]
        )

    def fund_adj(self, **kwargs):
        self.adj_calls.append(kwargs)
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

    def fund_share(self, **kwargs):
        self.share_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "trade_date": "20260709",
                    "fd_share": 10_000.0,
                }
            ]
        )


class BoundedTushareClient(FakeTushareClient):
    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        frame = _daily_frame()
        return frame.loc[frame["trade_date"] <= "20260710"].copy()


class StaleDailyTushareClient(FakeTushareClient):
    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        frame = _daily_frame()
        return frame.loc[frame["trade_date"] <= "20260630"].copy()


class AdjustedTushareClient(FakeTushareClient):
    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        frame = _daily_frame().sort_values("trade_date").reset_index(drop=True)
        frame["close"] = 1.0
        frame["open"] = 1.0
        return frame

    def fund_adj(self, **kwargs):
        self.adj_calls.append(kwargs)
        daily = _daily_frame().sort_values("trade_date").reset_index(drop=True)
        return pd.DataFrame(
            {
                "ts_code": daily["ts_code"],
                "trade_date": daily["trade_date"],
                "adj_factor": 1.0 + daily.index * 0.01,
            }
        )


class StaleNavTushareClient(FakeTushareClient):
    def fund_nav(self, **kwargs):
        self.nav_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "513100.SH",
                    "ann_date": "20260602",
                    "nav_date": "20260601",
                    "unit_nav": 2.0,
                    "accum_nav": 2.0,
                    "adj_nav": 2.0,
                }
            ]
        )


class FailedAdjTushareClient(FakeTushareClient):
    def fund_adj(self, **kwargs):
        self.adj_calls.append(kwargs)
        raise RuntimeError("fund_adj unavailable")


class DynamicCatalogClient(FakeTushareClient):
    def fund_basic(self, **kwargs):
        self.basic_calls.append(kwargs)
        return pd.DataFrame(
            [
                {"ts_code": "513100.SH", "name": "国泰纳斯达克100ETF(QDII)", "benchmark": "纳斯达克100指数×100%", "list_date": "20130515", "status": "L", "m_fee": 0.6},
                {"ts_code": "159941.SZ", "name": "广发纳斯达克100ETF(QDII)", "benchmark": "纳斯达克100指数×100%", "list_date": "20150713", "status": "L", "m_fee": 0.8},
                {"ts_code": "513300.SH", "name": "华夏纳斯达克100ETF(QDII)", "benchmark": "纳斯达克100指数×100%", "list_date": "20201105", "status": "L", "m_fee": 0.6},
                {"ts_code": "513500.SH", "name": "博时标普500ETF(QDII)", "benchmark": "标普500指数×100%", "list_date": "20140115", "status": "L", "m_fee": 0.6},
                {"ts_code": "513180.SH", "name": "华夏恒生科技ETF(QDII)", "benchmark": "恒生科技指数收益率×100%", "list_date": "20210525", "status": "L", "m_fee": 0.5},
                {"ts_code": "513130.SH", "name": "华泰柏瑞恒生科技ETF(QDII)", "benchmark": "恒生科技指数收益率×100%", "list_date": "20210601", "status": "L", "m_fee": 0.2},
                {"ts_code": "513580.SH", "name": "华安恒生科技ETF(QDII)", "benchmark": "恒生科技指数收益率×100%", "list_date": "20210527", "status": "L", "m_fee": 0.5},
                {"ts_code": "159920.SZ", "name": "华夏恒生ETF(QDII)", "benchmark": "香港恒生指数×100%", "list_date": "20121022", "status": "L", "m_fee": 0.6},
            ]
        )

    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        code = kwargs["ts_code"]
        frame = _daily_frame().copy()
        frame["ts_code"] = code
        liquidity_rank = int(code[:6]) % 17 + 1
        frame["amount"] = frame["amount"] * liquidity_rank
        return frame

    def fund_nav(self, **kwargs):
        self.nav_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "ann_date": "20260709",
                    "nav_date": "20260708",
                    "unit_nav": 2.0,
                    "accum_nav": 2.0,
                    "adj_nav": 2.0,
                }
            ]
        )


class CrowdedIndexCatalogClient(DynamicCatalogClient):
    def fund_basic(self, **kwargs):
        frame = super().fund_basic(**kwargs)
        crowded = pd.DataFrame(
            [
                {"ts_code": "159632.SZ", "name": "华安纳斯达克100ETF(QDII)", "benchmark": "纳斯达克100指数×100%", "list_date": "20220721", "status": "L", "m_fee": 0.6},
                {"ts_code": "159509.SZ", "name": "景顺长城纳斯达克科技ETF(QDII)", "benchmark": "纳斯达克100指数×100%", "list_date": "20230719", "status": "L", "m_fee": 0.5},
            ]
        )
        return pd.concat([frame, crowded], ignore_index=True)

    def fund_daily(self, **kwargs):
        frame = super().fund_daily(**kwargs)
        if kwargs["ts_code"] == "159509.SZ":
            frame["amount"] = frame["amount"] * 1_000
        return frame


class RecentListingClient(FakeTushareClient):
    def fund_basic(self, **kwargs):
        self.basic_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "159999.SZ",
                    "name": "测试纳斯达克100ETF(QDII)",
                    "benchmark": "纳斯达克100指数×100%",
                    "list_date": "20250102",
                    "status": "L",
                    "m_fee": 0.5,
                }
            ]
        )

    def fund_daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        frame = _daily_frame().copy()
        frame["ts_code"] = kwargs["ts_code"]
        return frame


class EmptyFundBasicClient(FakeTushareClient):
    def fund_basic(self, **kwargs):
        self.basic_calls.append(kwargs)
        return pd.DataFrame()


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
        self.assertEqual(snap.list_date, "2013-05-15")
        self.assertGreater(snap.listing_age_days, 4_000)
        self.assertFalse(snap.paused)

    def test_price_snapshot_normalizes_tushare_amount_from_thousand_yuan(self):
        provider = CNQDIETFProvider(pro_client=FakeTushareClient(), cache_dir=None)

        snap = provider.price_snapshot("513100.SH", as_of="2026-07-09")
        raw = _daily_frame().loc[_daily_frame()["trade_date"] <= "20260709"].sort_values("trade_date")

        self.assertAlmostEqual(snap.amount, float(raw.iloc[-1]["amount"]) * 1_000.0)
        self.assertAlmostEqual(
            snap.avg_amount_20,
            float(raw.tail(20)["amount"].mean()) * 1_000.0,
        )

    def test_price_snapshot_uses_adjusted_closes_for_momentum(self):
        provider = CNQDIETFProvider(pro_client=AdjustedTushareClient(), cache_dir=None)

        snap = provider.price_snapshot("513100.SH", as_of="2026-07-09")

        self.assertIsNotNone(snap.momentum_20)
        self.assertGreater(snap.momentum_20, 0.0)

    def test_price_snapshot_falls_back_when_adjustment_is_unavailable(self):
        provider = CNQDIETFProvider(pro_client=FailedAdjTushareClient(), cache_dir=None)

        snap = provider.price_snapshot("513100.SH", as_of="2026-07-09")

        self.assertIsNotNone(snap.momentum_20)
        self.assertFalse(snap.paused)
        self.assertIn(
            "failed",
            {row["status"] for row in provider._health if row["source"] == "fund_adj"},
        )

    def test_stale_nav_is_not_used_for_discount_premium(self):
        provider = CNQDIETFProvider(pro_client=StaleNavTushareClient(), cache_dir=None)

        snap = provider.price_snapshot("513100.SH", as_of="2026-07-09")

        self.assertEqual(snap.nav_date, "2026-06-01")
        self.assertIsNone(snap.discount_premium)

    def test_stale_daily_history_is_marked_paused(self):
        provider = CNQDIETFProvider(pro_client=StaleDailyTushareClient(), cache_dir=None)

        snap = provider.price_snapshot("513100.SH", as_of="2026-07-10")

        self.assertTrue(snap.paused)
        self.assertIn("stale", snap.warning)
        self.assertEqual(snap.trade_date, "2026-06-30")

    def test_persist_health_writes_fetch_outcomes(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            provider = CNQDIETFProvider(
                pro_client=FakeTushareClient(),
                cache_dir=cache_dir,
                as_of="2026-07-09",
            )
            provider.price_snapshot("513100.SH", as_of="2026-07-09")
            provider.persist_health()

            health_path = Path(tmp) / "data_health.json"
            payload = json.loads(health_path.read_text(encoding="utf-8"))

        self.assertTrue(payload)
        self.assertIn("fund_daily", {row["source"] for row in payload})
        self.assertTrue(all("status" in row for row in payload))

    def test_spot_returns_one_row_per_scope_member(self):
        provider = CNQDIETFProvider(pro_client=FakeTushareClient(), cache_dir=None)
        df = provider.spot("us_exposure")

        self.assertIn("513100.SH", set(df["code"]))
        self.assertIn("momentum_20", df.columns)
        self.assertIn("avg_amount_20", df.columns)
        self.assertIn("discount_premium", df.columns)
        self.assertEqual(set(df["industry"].dropna()), {"us_exposure"})

    def test_dynamic_catalog_snapshot_is_shared_and_index_deduplicated(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            client = DynamicCatalogClient()
            online = CNQDIETFProvider(pro_client=client, cache_dir=cache_dir, as_of="2026-07-10")

            first = online.universe_snapshot("2026-07-10")
            online_calls = len(client.daily_calls)
            offline = CNQDIETFProvider(
                pro_client=client,
                cache_dir=cache_dir,
                offline=True,
                as_of="2026-07-10",
            )
            second = offline.universe_snapshot("2026-07-10")

            dated = Path(tmp) / "universe_snapshots" / "2026-07-10.json"
            latest = Path(tmp) / "universe_latest.json"
            dated_exists = dated.exists()
            latest_exists = latest.exists()

        self.assertTrue(dated_exists)
        self.assertTrue(latest_exists)
        self.assertEqual(first["universe_hash"], second["universe_hash"])
        self.assertEqual(len(client.daily_calls), online_calls)
        self.assertEqual(
            sum(row["index_key"] == "nasdaq_100" for row in first["scopes"]["us_exposure"]),
            2,
        )
        self.assertEqual(
            sum(row["index_key"] == "hang_seng_tech" for row in first["scopes"]["hk_exposure"]),
            2,
        )
        self.assertTrue(
            all(
                row["universe_hash"] == first["universe_hash"]
                and row["avg_amount_20"] > 1_000_000
                and row["fund_size_yuan"] == 200_000_000.0
                for rows in first["scopes"].values()
                for row in rows
            )
        )
        nasdaq = [
            row
            for row in first["scopes"]["us_exposure"]
            if row["index_key"] == "nasdaq_100"
        ]
        self.assertEqual(len(nasdaq), 2)
        self.assertTrue(all(row["peer_tracking_error_60"] is not None for row in nasdaq))
        self.assertTrue(all(row["tracking_reference_code"] for row in nasdaq))

    def test_liquidity_selection_evaluates_candidates_beyond_first_four(self):
        provider = CNQDIETFProvider(
            pro_client=CrowdedIndexCatalogClient(),
            cache_dir=None,
            as_of="2026-07-10",
        )

        snapshot = provider.universe_snapshot("2026-07-10")
        nasdaq_codes = {
            row["code"]
            for row in snapshot["scopes"]["us_exposure"]
            if row["index_key"] == "nasdaq_100"
        }

        self.assertIn("159509.SZ", nasdaq_codes)
        self.assertEqual(len(nasdaq_codes), 2)

    def test_dynamic_catalog_refreshes_fund_basic_instead_of_freezing_first_cache(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "ts_code": "513100.SH",
                        "name": "旧目录",
                        "benchmark": "纳斯达克100指数×100%",
                        "list_date": "20130515",
                        "status": "L",
                    }
                ]
            ).to_csv(cache_dir / "fund_basic_E_v2.csv", index=False)
            client = DynamicCatalogClient()
            provider = CNQDIETFProvider(
                pro_client=client,
                cache_dir=cache_dir,
                as_of="2026-07-10",
            )

            snapshot = provider.universe_snapshot("2026-07-10")
            refreshed = pd.read_csv(cache_dir / "fund_basic_E_v2.csv", dtype=str)

        self.assertEqual(len(client.basic_calls), 1)
        self.assertGreater(len(refreshed), 1)
        self.assertEqual(snapshot["source_status"], "dynamic_fund_basic")

    def test_empty_fund_basic_refresh_preserves_healthy_cache(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(parents=True)
            healthy = DynamicCatalogClient().fund_basic()
            healthy.to_csv(cache_dir / "fund_basic_E_v2.csv", index=False)
            provider = CNQDIETFProvider(
                pro_client=EmptyFundBasicClient(),
                cache_dir=cache_dir,
                as_of="2026-07-10",
            )

            snapshot = provider.universe_snapshot("2026-07-10")
            preserved = pd.read_csv(cache_dir / "fund_basic_E_v2.csv", dtype=str)

        self.assertEqual(len(preserved), len(healthy))
        self.assertEqual(snapshot["source_status"], "cached_fund_basic_fallback")

    def test_concurrent_agents_build_one_shared_universe_snapshot(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            providers = [
                CNQDIETFProvider(
                    pro_client=FakeTushareClient(),
                    cache_dir=cache_dir,
                    as_of="2026-07-10",
                )
                for _ in range(2)
            ]
            calls: list[str] = []
            calls_lock = threading.Lock()

            def build(as_of_key: str) -> dict:
                with calls_lock:
                    calls.append(as_of_key)
                time.sleep(0.1)
                return {
                    "schema_version": UNIVERSE_SCHEMA_VERSION,
                    "rules_version": UNIVERSE_RULES_VERSION,
                    "as_of": "2026-07-10",
                    "universe_hash": "one-shared-hash",
                    "scopes": {"us_exposure": [], "hk_exposure": []},
                }

            for provider in providers:
                provider._build_universe_snapshot = build  # type: ignore[method-assign]
            with ThreadPoolExecutor(max_workers=2) as executor:
                snapshots = list(
                    executor.map(
                        lambda provider: provider.universe_snapshot("2026-07-10"),
                        providers,
                    )
                )

        self.assertEqual(calls, ["20260710"])
        self.assertEqual(
            [snapshot["universe_hash"] for snapshot in snapshots],
            ["one-shared-hash", "one-shared-hash"],
        )

    def test_spot_exposes_dynamic_underlying_metadata(self):
        provider = CNQDIETFProvider(
            pro_client=DynamicCatalogClient(),
            cache_dir=None,
            as_of="2026-07-10",
        )

        frame = provider.spot("us_exposure")

        self.assertIn("index_key", frame.columns)
        self.assertIn("theme", frame.columns)
        self.assertIn("universe_hash", frame.columns)
        self.assertEqual(set(frame["industry"]), {"us_exposure"})
        self.assertLessEqual(sum(frame["index_key"] == "nasdaq_100"), 2)

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

    def test_incomplete_online_cache_is_refreshed_for_execution(self):
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)
            stale = _daily_frame().loc[_daily_frame()["trade_date"] <= "20260709"]
            stale.to_csv(cache / "fund_daily_513100_SH_20260710.csv", index=False)
            client = FakeTushareClient()
            provider = CNQDIETFProvider(
                pro_client=client,
                cache_dir=cache,
                offline=False,
            )

            quote = provider.execution_quote(
                "513100.SH",
                execute_after="2026-07-10",
                side="buy",
                as_of="2026-07-10",
            )

        self.assertFalse(quote.paused)
        self.assertEqual(quote.trade_date, "2026-07-10")
        self.assertEqual(len(client.daily_calls), 1)

    def test_short_online_cache_is_refreshed_with_three_year_request(self):
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)
            _daily_frame().to_csv(cache / "fund_daily_513100_SH_20260710.csv", index=False)
            client = FakeTushareClient()
            provider = CNQDIETFProvider(
                pro_client=client,
                cache_dir=cache,
                offline=False,
            )

            provider.price_snapshot("513100.SH", as_of="2026-07-10")

        self.assertEqual(len(client.daily_calls), 1)
        self.assertLessEqual(client.daily_calls[0]["start_date"], "20230710")
        self.assertEqual(client.daily_calls[0]["end_date"], "20260710")

    def test_three_year_request_starts_at_listing_date_for_newer_fund(self):
        client = RecentListingClient()
        provider = CNQDIETFProvider(pro_client=client, cache_dir=None)

        provider.price_snapshot("159999.SZ", as_of="2026-07-12")

        self.assertEqual(client.daily_calls[0]["start_date"], "20250102")
        self.assertEqual(client.daily_calls[0]["end_date"], "20260712")

    def test_history_coverage_accepts_latest_trade_before_weekend(self):
        frame = pd.DataFrame(
            {
                "trade_date": ["20230712", "20260710"],
                "close": [1.0, 2.0],
            }
        )

        self.assertTrue(
            CNQDIETFProvider._daily_covers_window(
                frame,
                "20230712",
                "20260712",
            )
        )

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
