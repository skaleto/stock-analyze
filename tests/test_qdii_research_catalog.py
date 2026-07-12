from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.research_catalog import build_research_catalog


class QDIIResearchCatalogTests(unittest.TestCase):
    def test_classifies_global_equity_commodity_and_bond_products(self) -> None:
        frame = pd.DataFrame(
            [
                {"ts_code": "159866.SZ", "name": "工银瑞信大和日经225ETF(QDII)", "fund_type": "股票型", "list_date": "20210408", "delist_date": ""},
                {"ts_code": "513030.SH", "name": "华安国际龙头(DAX)ETF(QDII)", "fund_type": "股票型", "list_date": "20140905", "delist_date": ""},
                {"ts_code": "159329.SZ", "name": "南方基金南方东英沙特阿拉伯ETF(QDII)", "fund_type": "股票型", "list_date": "20240716", "delist_date": ""},
                {"ts_code": "501018.SH", "name": "南方原油(QDII-LOF)-A", "fund_type": "其他", "list_date": "20160628", "delist_date": ""},
                {"ts_code": "161116.SZ", "name": "易方达黄金主题(QDII-LOF-FOF)-A-CNY", "fund_type": "其他", "list_date": "20111108", "delist_date": ""},
                {"ts_code": "160999.SZ", "name": "示例亚洲美元债(QDII-LOF)-A", "fund_type": "债券型", "list_date": "20200101", "delist_date": ""},
                {"ts_code": "160000.SZ", "name": "普通国内债券(LOF)", "fund_type": "债券型", "list_date": "20200101", "delist_date": ""},
            ]
        )

        result = build_research_catalog(frame, as_of="2026-07-12")
        by_code = result.set_index("code")

        self.assertEqual(by_code.loc["159866.SZ", "research_scope"], "japan_exposure")
        self.assertEqual(by_code.loc["513030.SH", "research_scope"], "europe_exposure")
        self.assertEqual(by_code.loc["159329.SZ", "research_scope"], "saudi_exposure")
        self.assertEqual(by_code.loc["501018.SH", "research_scope"], "commodity_oil")
        self.assertEqual(by_code.loc["161116.SZ", "research_scope"], "commodity_precious_metals")
        self.assertEqual(by_code.loc["160999.SZ", "research_scope"], "bond_overseas")
        self.assertEqual(by_code.loc["501018.SH", "product_type"], "qdii_lof")
        self.assertNotIn("160000.SZ", by_code.index)

    def test_marks_breadth_and_live_promotion_boundaries(self) -> None:
        frame = pd.DataFrame(
            [
                {"ts_code": "159866.SZ", "name": "日经225ETF(QDII)", "fund_type": "股票型", "list_date": "20210408"},
                {"ts_code": "513000.SH", "name": "日经225ETF(QDII)", "fund_type": "股票型", "list_date": "20190625"},
                {"ts_code": "513520.SH", "name": "日经225ETF(QDII)", "fund_type": "股票型", "list_date": "20190625"},
                {"ts_code": "160999.SZ", "name": "亚洲美元债(QDII-LOF)", "fund_type": "债券型", "list_date": "20200101"},
            ]
        )

        result = build_research_catalog(frame, as_of="2026-07-12")

        japan = result[result["research_scope"] == "japan_exposure"]
        bond = result[result["research_scope"] == "bond_overseas"]
        self.assertTrue((japan["promotion_status"] == "shadow_ready").all())
        self.assertTrue((bond["promotion_status"] == "insufficient_breadth").all())
        self.assertTrue((result["mode"] == "research_only").all())


if __name__ == "__main__":
    unittest.main()
