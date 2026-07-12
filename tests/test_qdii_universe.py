from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.universe import (
    build_catalog_candidates,
    catalog_content_hash,
    select_liquid_representatives,
)


class DynamicQdiiUniverseTests(unittest.TestCase):
    def test_fund_basic_rows_are_classified_by_name_and_benchmark(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "ts_code": "513100.SH",
                    "name": "国泰纳斯达克100ETF(QDII)",
                    "benchmark": "纳斯达克100指数×100%",
                    "list_date": "20130515",
                    "status": "L",
                    "m_fee": 0.60,
                },
                {
                    "ts_code": "159529.SZ",
                    "name": "景顺长城标普消费精选ETF(QDII)",
                    "benchmark": "标普500消费精选指数收益率×100%",
                    "list_date": "20240202",
                    "status": "L",
                    "m_fee": 0.50,
                },
                {
                    "ts_code": "513180.SH",
                    "name": "华夏恒生科技ETF(QDII)",
                    "benchmark": "恒生科技指数收益率×100%",
                    "list_date": "20210525",
                    "status": "L",
                    "m_fee": 0.50,
                },
                {
                    "ts_code": "159568.SZ",
                    "name": "博时中证港股通互联网ETF",
                    "benchmark": "中证港股通互联网指数收益率×100%",
                    "list_date": "20240227",
                    "status": "L",
                    "m_fee": 0.50,
                },
                {
                    "ts_code": "161130.SZ",
                    "name": "易方达纳斯达克100ETF联接(QDII-LOF)-A",
                    "benchmark": "纳斯达克100指数×95%",
                    "list_date": "20170714",
                    "status": "L",
                    "m_fee": 0.50,
                },
                {
                    "ts_code": "516500.SH",
                    "name": "华夏中证生物科技主题ETF",
                    "benchmark": "中证生物科技主题指数收益率×100%",
                    "list_date": "20210317",
                    "status": "L",
                    "m_fee": 0.50,
                },
                {
                    "ts_code": "513960.SH",
                    "name": "博时中证港股通消费主题ETF",
                    "benchmark": "中证港股通消费主题指数收益率×100%",
                    "list_date": "20220318",
                    "delist_date": "20241108",
                    "status": "D",
                    "m_fee": 0.50,
                },
            ]
        )

        rows = build_catalog_candidates(frame, as_of="2026-07-10")
        by_code = {row["code"]: row for row in rows}

        self.assertEqual(set(by_code), {"513100.SH", "159529.SZ", "513180.SH", "159568.SZ"})
        self.assertEqual(by_code["513100.SH"]["scope"], "us_exposure")
        self.assertEqual(by_code["513100.SH"]["index_key"], "nasdaq_100")
        self.assertEqual(by_code["159529.SZ"]["index_key"], "sp_500_consumer")
        self.assertEqual(by_code["513180.SH"]["scope"], "hk_exposure")
        self.assertEqual(by_code["513180.SH"]["index_key"], "hang_seng_tech")
        self.assertEqual(by_code["159568.SZ"]["index_key"], "csi_hk_connect_internet")

    def test_representatives_keep_at_most_two_liquid_funds_per_index(self) -> None:
        rows = [
            {"code": "A", "scope": "us_exposure", "index_key": "nasdaq_100", "avg_amount_20": 20.0},
            {"code": "B", "scope": "us_exposure", "index_key": "nasdaq_100", "avg_amount_20": 50.0},
            {"code": "C", "scope": "us_exposure", "index_key": "nasdaq_100", "avg_amount_20": 30.0},
            {"code": "D", "scope": "us_exposure", "index_key": "sp_500", "avg_amount_20": 10.0},
            {"code": "E", "scope": "hk_exposure", "index_key": "hang_seng_tech", "avg_amount_20": 40.0},
        ]

        selected = select_liquid_representatives(rows, max_per_index=2, max_per_scope=24)

        self.assertEqual([row["code"] for row in selected["us_exposure"]], ["B", "D", "C"])
        self.assertEqual([row["code"] for row in selected["hk_exposure"]], ["E"])
        self.assertNotIn("A", {row["code"] for row in selected["us_exposure"]})

    def test_catalog_hash_is_order_independent_but_content_sensitive(self) -> None:
        left = {
            "us_exposure": [
                {"code": "A", "index_key": "nasdaq_100", "theme": "纳斯达克100"},
                {"code": "B", "index_key": "sp_500", "theme": "标普500"},
            ],
            "hk_exposure": [
                {"code": "C", "index_key": "hang_seng_tech", "theme": "恒生科技"},
            ],
        }
        reordered = {
            "hk_exposure": list(reversed(left["hk_exposure"])),
            "us_exposure": list(reversed(left["us_exposure"])),
        }
        changed = {
            **left,
            "us_exposure": [{**left["us_exposure"][0], "code": "Z"}, left["us_exposure"][1]],
        }

        self.assertEqual(catalog_content_hash(left), catalog_content_hash(reordered))
        self.assertNotEqual(catalog_content_hash(left), catalog_content_hash(changed))


if __name__ == "__main__":
    unittest.main()
