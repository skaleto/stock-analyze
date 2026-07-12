from __future__ import annotations

import unittest

from stock_analyze.markets.cn_qdii_etf.lookthrough import (
    build_portfolio_lookthrough,
    load_index_profiles,
)


class QdiiLookthroughTests(unittest.TestCase):
    def test_weighted_portfolio_lookthrough_aggregates_real_company_exposure(self) -> None:
        profiles = {
            "nasdaq_100": {
                "index_key": "nasdaq_100",
                "name": "纳斯达克100",
                "country": "美国",
                "as_of": "2026-06-30",
                "source_url": "https://indexes.nasdaq.com/docs/FS_NDX.pdf",
                "constituents": [
                    {"symbol": "NVDA", "name": "NVIDIA", "sector": "信息技术", "weight": 0.6},
                    {"symbol": "AAPL", "name": "Apple", "sector": "信息技术", "weight": 0.4},
                ],
                "sector_weights": [{"label": "信息技术", "weight": 1.0}],
            },
            "hang_seng_tech": {
                "index_key": "hang_seng_tech",
                "name": "恒生科技",
                "country": "香港",
                "as_of": "2026-06-30",
                "source_url": "https://www.hsi.com.hk/static/uploads/contents/en/dl_centre/factsheets/hsteche.pdf",
                "constituents": [
                    {"symbol": "0700.HK", "name": "腾讯控股", "sector": "信息技术", "weight": 1.0},
                ],
                "sector_weights": [{"label": "信息技术", "weight": 1.0}],
            },
        }
        rows = [
            {"code": "513100.SH", "index_key": "nasdaq_100", "theme": "纳斯达克100", "country": "美国", "market_value": 60.0},
            {"code": "513180.SH", "index_key": "hang_seng_tech", "theme": "恒生科技", "country": "香港", "market_value": 40.0},
        ]

        result = build_portfolio_lookthrough(rows, profiles=profiles, source="positions")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["profile_coverage"], 1.0)
        self.assertEqual(result["company_weight_coverage"], 1.0)
        companies = {row["symbol"]: row["weight"] for row in result["companies"]}
        self.assertAlmostEqual(companies["NVDA"], 0.36)
        self.assertAlmostEqual(companies["AAPL"], 0.24)
        self.assertAlmostEqual(companies["0700.HK"], 0.40)
        countries = {row["label"]: row["weight"] for row in result["countries"]}
        self.assertEqual(countries, {"美国": 0.6, "香港": 0.4})

    def test_unsupported_index_is_reported_as_partial_not_invented(self) -> None:
        profiles = {
            "nasdaq_100": {
                "index_key": "nasdaq_100",
                "name": "纳斯达克100",
                "country": "美国",
                "as_of": "2026-06-30",
                "source_url": "https://indexes.nasdaq.com/docs/FS_NDX.pdf",
                "constituents": [{"symbol": "NVDA", "name": "NVIDIA", "weight": 0.5}],
                "sector_weights": [],
            }
        }
        rows = [
            {"code": "A", "index_key": "nasdaq_100", "theme": "纳斯达克100", "target_value": 50.0},
            {"code": "B", "index_key": "unknown_index", "theme": "未知指数", "target_value": 50.0},
        ]

        result = build_portfolio_lookthrough(rows, profiles=profiles, source="planned_orders")

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["profile_coverage"], 0.5)
        self.assertEqual(result["company_weight_coverage"], 0.25)
        self.assertEqual(result["unsupported_indexes"], ["unknown_index"])
        self.assertEqual(result["companies"], [{"symbol": "NVDA", "name": "NVIDIA", "sector": "未分类", "weight": 0.25}])

    def test_bundled_profiles_are_source_dated_and_use_official_urls(self) -> None:
        profiles = load_index_profiles()

        self.assertIn("nasdaq_100", profiles)
        self.assertIn("hang_seng_tech", profiles)
        for profile in profiles.values():
            self.assertRegex(profile["as_of"], r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
            self.assertTrue(profile["source_url"].startswith("https://"))
            self.assertTrue(profile["constituents"])


if __name__ == "__main__":
    unittest.main()
