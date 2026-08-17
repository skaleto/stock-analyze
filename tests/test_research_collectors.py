import unittest

import pandas as pd

from stock_analyze.markets.a_share.market_data import collect_research_sources
from stock_analyze.markets.cn_qdii_etf.data_provider import CNQDIETFProvider


class FakePro:
    def __init__(self):
        self.calls: list[str] = []
        self.kwargs_by_name: dict[str, list[dict]] = {}

    def __getattr__(self, name):
        def endpoint(**kwargs):
            self.calls.append(name)
            self.kwargs_by_name.setdefault(name, []).append(dict(kwargs))
            code = kwargs.get("ts_code", "000001.SZ")
            if name == "fund_nav":
                return pd.DataFrame([{"ts_code": code, "nav_date": "20260710", "ann_date": "20260711", "unit_nav": 1.2}])
            if name == "fund_share":
                return pd.DataFrame([{"ts_code": code, "trade_date": "20260710", "fd_share": 100.0}])
            if name in {"index_global", "fx_daily"}:
                return pd.DataFrame([{"ts_code": kwargs.get("ts_code", "SPX"), "trade_date": "20260710", "close": 100.0}])
            return pd.DataFrame([{"ts_code": code, "trade_date": "20260710", "ann_date": "20260710", "value": 1.0}])

        return endpoint


class ResearchCollectorTest(unittest.TestCase):
    def test_a_share_collector_normalizes_source_metadata(self):
        pro = FakePro()

        result = collect_research_sources(
            pro,
            as_of="2026-07-10",
            codes=["000001.SZ"],
            benchmark_codes=["000300", "000905"],
            observed_at="2026-07-10T18:00:00+08:00",
        )

        expected = {
            "daily_basic", "moneyflow", "margin", "margin_detail", "hsgt_top10",
            "fina_indicator", "income", "balancesheet", "cashflow", "fina_mainbz",
            "index_classify", "index_member_all", "cn_pmi", "cn_m", "cn_cpi",
            "cn_ppi", "shibor", "shibor_lpr", "us_tycr", "benchmark_000300",
            "benchmark_000905",
        }
        self.assertTrue(expected.issubset(result.frames))
        self.assertEqual(set(result.frames["daily_basic"]["source"]), {"tushare:daily_basic"})
        self.assertEqual(result.frames["daily_basic"].iloc[0]["observed_at"], "2026-07-10T18:00:00+08:00")
        self.assertIn("source_date", result.frames["daily_basic"].columns)
        self.assertFalse(result.health["failed"].any())

        benchmark = result.frames["benchmark_000300"]
        self.assertEqual(benchmark.iloc[0]["ts_code"], "000300.SH")
        self.assertEqual(set(benchmark["source"]), {"tushare:benchmark_000300"})
        self.assertLessEqual(pro.kwargs_by_name["cn_pmi"][0]["start_m"], "202307")
        self.assertLessEqual(pro.kwargs_by_name["shibor"][0]["start_date"], "20230710")
        self.assertEqual(pro.kwargs_by_name["index_daily"][0]["start_date"], "20180101")
        self.assertEqual(pro.kwargs_by_name["index_daily"][1]["start_date"], "20180101")

    def test_qdii_collector_includes_fund_global_and_fx_sources(self):
        pro = FakePro()
        provider = CNQDIETFProvider(pro_client=pro, as_of="2026-07-10")

        result = provider.collect_research_sources(
            ["513100.SH"],
            observed_at="2026-07-10T18:00:00+08:00",
        )

        self.assertEqual(set(result.frames), {"fund_nav", "fund_share", "index_global", "fx_daily"})
        self.assertEqual(result.frames["fund_nav"].iloc[0]["ts_code"], "513100.SH")
        self.assertTrue(all("observed_at" in frame.columns for frame in result.frames.values()))


if __name__ == "__main__":
    unittest.main()
