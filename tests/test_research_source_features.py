import unittest

import pandas as pd

from stock_analyze.research.source_features import build_source_features


class ResearchSourceFeaturesTest(unittest.TestCase):
    def test_builds_flow_valuation_and_cash_quality_features(self):
        frames = {
            "daily_basic": pd.DataFrame([
                {"ts_code": "000001.SZ", "trade_date": "20260709", "pe_ttm": 10.0, "pb": 1.0, "turnover_rate": 1.0, "total_mv": 1000.0},
                {"ts_code": "000001.SZ", "trade_date": "20260710", "pe_ttm": 12.0, "pb": 1.2, "turnover_rate": 2.0, "total_mv": 1100.0},
            ]),
            "moneyflow": pd.DataFrame([
                {"ts_code": "000001.SZ", "trade_date": "20260709", "buy_lg_amount": 10.0, "buy_elg_amount": 8.0, "sell_lg_amount": 4.0, "sell_elg_amount": 3.0},
                {"ts_code": "000001.SZ", "trade_date": "20260710", "buy_lg_amount": 12.0, "buy_elg_amount": 9.0, "sell_lg_amount": 5.0, "sell_elg_amount": 3.0},
            ]),
            "income": pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20260630", "n_income": 20.0, "revenue": 100.0}]),
            "cashflow": pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20260630", "n_cashflow_act": 30.0}]),
        }

        features = build_source_features(frames)

        row = features.iloc[0]
        self.assertEqual(row["code"], "000001")
        self.assertAlmostEqual(row["flow_net_large"], 13.0)
        self.assertAlmostEqual(row["cash_flow_quality"], 1.5)
        self.assertGreater(row["turnover_change"], 0.0)

    def test_builds_qdii_share_and_global_risk_features(self):
        frames = {
            "fund_share": pd.DataFrame([
                {"ts_code": "513100.SH", "trade_date": "20260610", "fd_share": 100.0},
                {"ts_code": "513100.SH", "trade_date": "20260710", "fd_share": 120.0},
            ]),
            "index_global": pd.DataFrame([
                {"ts_code": "SPX", "trade_date": "20260610", "close": 100.0},
                {"ts_code": "SPX", "trade_date": "20260710", "close": 110.0},
            ]),
            "fx_daily": pd.DataFrame([
                {"ts_code": "USDCNH.FXCM", "trade_date": "20260610", "close": 7.0},
                {"ts_code": "USDCNH.FXCM", "trade_date": "20260710", "close": 7.1},
            ]),
        }

        features = build_source_features(frames)

        etf = features.loc[features["code"] == "513100"].iloc[0]
        self.assertAlmostEqual(etf["fund_share_change"], 0.2)
        self.assertAlmostEqual(etf["global_index_momentum"], 0.1)
        self.assertAlmostEqual(etf["rmb_depreciation"], 7.1 / 7.0 - 1.0)


if __name__ == "__main__":
    unittest.main()
