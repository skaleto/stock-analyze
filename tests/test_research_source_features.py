import unittest
import warnings
from unittest.mock import patch

import pandas as pd

from stock_analyze.research.source_features import (
    add_industry_features,
    attach_daily_basic_point_in_time_features,
    attach_industry_membership,
    attach_point_in_time_features,
    build_fundamental_history,
    attach_qdii_point_in_time_features,
    build_regime_components,
    build_source_features,
)


class ResearchSourceFeaturesTest(unittest.TestCase):
    def test_point_in_time_joins_do_not_accumulate_group_concat_parts(self):
        prices = pd.DataFrame([
            {"code": "000002", "trade_date": "20260711", "close": 20.0},
            {"code": "000001", "trade_date": "20260710", "close": 10.0},
            {"code": "000002", "trade_date": "20260710", "close": 19.0},
            {"code": "000001", "trade_date": "20260711", "close": 11.0},
        ])
        fundamentals = pd.DataFrame([
            {"code": "000001", "available_date": "20260710", "end_date": "20260630", "roe": 10.0},
            {"code": "000002", "available_date": "20260711", "end_date": "20260630", "roe": 20.0},
        ])
        daily_basic = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20260710", "pe_ttm": 11.0},
            {"ts_code": "000002.SZ", "trade_date": "20260711", "pe_ttm": 22.0},
        ])

        original_concat = pd.concat

        def reject_row_partition_concat(parts, *args, **kwargs):
            axis = kwargs.get("axis", args[0] if args else 0)
            if axis in (0, "index") and isinstance(parts, list):
                raise AssertionError("group concat is not allowed")
            return original_concat(parts, *args, **kwargs)

        with patch(
            "stock_analyze.research.source_features.pd.concat",
            side_effect=reject_row_partition_concat,
        ):
            attached = attach_point_in_time_features(prices, fundamentals)
            attached = attach_daily_basic_point_in_time_features(attached, daily_basic)

        self.assertEqual(attached[["code", "trade_date"]].to_dict("records"), prices[["code", "trade_date"]].to_dict("records"))
        rows = attached.set_index(["code", "trade_date"])
        self.assertEqual(float(rows.loc[("000001", "20260711"), "roe"]), 10.0)
        self.assertTrue(pd.isna(rows.loc[("000002", "20260710"), "roe"]))
        self.assertEqual(float(rows.loc[("000002", "20260711"), "pe_ttm"]), 22.0)

    def test_daily_basic_valuation_requires_the_exact_market_date(self):
        prices = pd.DataFrame([
            {"code": "000001", "trade_date": "20260424", "close": 10.0},
            {"code": "000001", "trade_date": "20260425", "close": 10.5},
            {"code": "000001", "trade_date": "20260710", "close": 11.0},
        ])
        daily_basic = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260425",
                "pe_ttm": 10.0,
                "pb": 1.2,
                "dv_ttm": 2.5,
                "total_mv": 100_000.0,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260711",
                "pe_ttm": 99.0,
                "pb": 9.9,
                "dv_ttm": 0.0,
                "total_mv": 999_000.0,
            },
        ])

        featured = attach_daily_basic_point_in_time_features(
            prices,
            daily_basic,
        ).set_index("trade_date")

        self.assertTrue(pd.isna(featured.loc["20260424", "pe_ttm"]))
        self.assertAlmostEqual(featured.loc["20260425", "pe_ttm"], 10.0)
        self.assertTrue(pd.isna(featured.loc["20260710", "pe_ttm"]))
        self.assertTrue(pd.isna(featured.loc["20260710", "dividend_yield"]))
        self.assertTrue(pd.isna(featured.loc["20260710", "total_mv"]))
        self.assertTrue(pd.isna(featured.loc["20260710", "daily_basic_trade_date"]))

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

    def test_builds_point_in_time_macro_and_global_regime_components(self):
        frames = {
            "cn_pmi": pd.DataFrame([
                {"MONTH": "202604", "PMI010000": 49.0},
                {"MONTH": "202605", "PMI010000": 50.0},
            ]),
            "cn_m": pd.DataFrame([
                {"month": "202604", "m2_yoy": 7.0},
                {"month": "202605", "m2_yoy": 8.0},
            ]),
            "cn_cpi": pd.DataFrame([
                {"month": "202604", "nt_yoy": 0.0},
                {"month": "202605", "nt_yoy": 1.0},
            ]),
            "cn_ppi": pd.DataFrame([
                {"month": "202604", "ppi_yoy": -2.0},
                {"month": "202605", "ppi_yoy": -1.0},
            ]),
            "shibor": pd.DataFrame([
                {"date": "20260610", "3m": 1.50},
                {"date": "20260710", "3m": 1.40},
            ]),
            "us_tycr": pd.DataFrame([
                {"date": "20260610", "y2": 4.0, "y10": 4.4},
                {"date": "20260710", "y2": 4.1, "y10": 4.6},
            ]),
            "index_global": pd.DataFrame([
                {"ts_code": "SPX", "trade_date": "20260610", "close": 100.0},
                {"ts_code": "SPX", "trade_date": "20260710", "close": 110.0},
            ]),
            "fx_daily": pd.DataFrame([
                {"ts_code": "USDCNH.FXCM", "trade_date": "20260610", "bid_close": 7.0},
                {"ts_code": "USDCNH.FXCM", "trade_date": "20260710", "bid_close": 7.1},
            ]),
        }

        components = build_regime_components(
            frames,
            pd.Series(["20260530", "20260602", "20260611", "20260616", "20260710"]),
        ).set_index("trade_date")

        self.assertTrue(pd.isna(components.loc["20260530", "pmi_change"]))
        self.assertAlmostEqual(components.loc["20260602", "pmi_change"], 1.0)
        self.assertTrue(pd.isna(components.loc["20260611", "m2_change"]))
        self.assertAlmostEqual(components.loc["20260616", "m2_change"], 1.0)
        self.assertAlmostEqual(components.loc["20260710", "yield_curve_slope"], 0.5)
        self.assertAlmostEqual(components.loc["20260710", "global_index_momentum"], 0.1)
        self.assertAlmostEqual(components.loc["20260710", "rmb_depreciation"], 7.1 / 7.0 - 1.0)
        self.assertLess(components.loc["20260710", "shibor_change"], 0.0)
        self.assertGreater(components.loc["20260710", "us_yield_change"], 0.0)

    def test_fundamentals_use_announcement_date_without_future_leak(self):
        frames = {
            "fina_indicator": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331",
                    "roe": 10.0, "grossprofit_margin": 30.0, "roic": 8.0,
                    "netprofit_margin": 12.0, "debt_to_assets": 40.0, "assets_turn": 0.8,
                    "q_sales_yoy": 15.0, "netprofit_yoy": 18.0, "q_op_qoq": 3.0,
                },
                {
                    "ts_code": "000001.SZ", "ann_date": "20260711", "end_date": "20260630",
                    "roe": 99.0, "grossprofit_margin": 60.0, "roic": 20.0,
                    "netprofit_margin": 30.0, "debt_to_assets": 20.0, "assets_turn": 1.2,
                    "q_sales_yoy": 25.0, "netprofit_yoy": 30.0, "q_op_qoq": 9.0,
                },
            ]),
            "income": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331",
                    "revenue": 100.0, "operate_profit": 20.0, "n_income": 15.0,
                    "total_cogs": 70.0, "rd_exp": 5.0,
                }
            ]),
            "cashflow": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331",
                    "n_cashflow_act": 18.0, "free_cashflow": 10.0,
                }
            ]),
            "balancesheet": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331",
                    "total_assets": 200.0,
                }
            ]),
            "fina_mainbz": pd.DataFrame([
                {"ts_code": "000001.SZ", "end_date": "20260331", "bz_item": "软件", "bz_sales": 60.0, "bz_profit": 18.0},
                {"ts_code": "000001.SZ", "end_date": "20260331", "bz_item": "服务", "bz_sales": 40.0, "bz_profit": 8.0},
            ]),
        }
        prices = pd.DataFrame([
            {"code": "000001", "trade_date": "20260424", "momentum_20": 0.1, "realized_volatility_20": 0.2, "return_1": 0.01},
            {"code": "000001", "trade_date": "20260710", "momentum_20": 0.2, "realized_volatility_20": 0.2, "return_1": 0.02},
            {"code": "000001", "trade_date": "20260711", "momentum_20": 0.3, "realized_volatility_20": 0.2, "return_1": 0.03},
        ])

        history = build_fundamental_history(frames)
        featured = attach_point_in_time_features(prices, history).set_index("trade_date")

        self.assertTrue(pd.isna(featured.loc["20260424", "roe"]))
        self.assertAlmostEqual(featured.loc["20260710", "roe"], 10.0)
        self.assertAlmostEqual(featured.loc["20260711", "roe"], 99.0)
        self.assertAlmostEqual(featured.loc["20260710", "cash_conversion"], 0.18)
        self.assertAlmostEqual(featured.loc["20260710", "profit_pool_concentration"], 0.52)
        self.assertAlmostEqual(featured.loc["20260711", "profit_pool_concentration"], 0.52)
        self.assertGreater(featured.loc["20260710", "high_value_add_proxy"], 0.0)
        self.assertEqual(featured.loc["20260710", "fundamental_available_date"], "20260425")
        self.assertEqual(featured.loc["20260710", "fundamental_period_end"], "20260331")
        self.assertEqual(
            featured.loc["20260710", "fundamental_restatement_policy"],
            "latest_revision_visible_on_announcement_date",
        )

    def test_financial_revision_is_visible_only_after_its_announcement(self):
        frames = {
            "fina_indicator": pd.DataFrame([
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "20260425",
                    "end_date": "20260331",
                    "update_flag": "0",
                    "roe": 10.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "20260711",
                    "end_date": "20260331",
                    "update_flag": "1",
                    "roe": 20.0,
                },
            ])
        }
        prices = pd.DataFrame([
            {"code": "000001", "trade_date": "20260710"},
            {"code": "000001", "trade_date": "20260711"},
        ])

        history = build_fundamental_history(frames)
        featured = attach_point_in_time_features(prices, history).set_index("trade_date")

        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(float(featured.loc["20260710", "roe"]), 10.0)
        self.assertAlmostEqual(float(featured.loc["20260711", "roe"]), 20.0)

    def test_indicator_without_observable_announcement_date_is_excluded(self):
        frames = {
            "fina_indicator": pd.DataFrame([
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "20260425",
                    "end_date": "20251231",
                    "roe": 10.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "",
                    "end_date": "20260331",
                    "roe": 99.0,
                },
            ]),
            "income": pd.DataFrame([
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "20260425",
                    "end_date": "20251231",
                    "revenue": 100.0,
                    "operate_profit": 20.0,
                    "n_income": 15.0,
                    "total_cogs": 70.0,
                    "rd_exp": 5.0,
                }
            ]),
        }

        history = build_fundamental_history(frames)

        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["available_date"], "20260425")
        self.assertAlmostEqual(float(history.iloc[0]["roe"]), 10.0)

    def test_arrow_null_statement_values_do_not_trigger_incompatible_assignment(self):
        frames = {
            "fina_indicator": pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": "20260425",
                "end_date": "20260331",
                "roe": 10.0,
            }]).convert_dtypes(dtype_backend="pyarrow"),
            "income": pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": "20260425",
                "end_date": "20260331",
                "revenue": 100.0,
                "operate_profit": 20.0,
                "n_income": 15.0,
                "total_cogs": 70.0,
                "rd_exp": None,
            }]).convert_dtypes(dtype_backend="pyarrow"),
        }

        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            history = build_fundamental_history(frames)

        self.assertAlmostEqual(float(history.iloc[0]["operating_margin"]), 0.2)
        self.assertTrue(pd.isna(history.iloc[0]["rd_intensity"]))

    def test_statement_revisions_use_one_vectorized_asof_join_per_endpoint(self):
        indicators = []
        income = []
        for code in ("000001.SZ", "000002.SZ"):
            for end_date, ann_date in (
                ("20251231", "20260331"),
                ("20260331", "20260425"),
            ):
                indicators.append({
                    "ts_code": code,
                    "ann_date": ann_date,
                    "end_date": end_date,
                    "roe": 10.0,
                })
                income.append({
                    "ts_code": code,
                    "ann_date": ann_date,
                    "end_date": end_date,
                    "revenue": 100.0,
                    "operate_profit": 20.0,
                    "n_income": 15.0,
                    "total_cogs": 70.0,
                    "rd_exp": 5.0,
                })
        original_merge_asof = pd.merge_asof

        with patch(
            "stock_analyze.research.source_features.pd.merge_asof",
            wraps=original_merge_asof,
        ) as merge_asof:
            history = build_fundamental_history({
                "fina_indicator": pd.DataFrame(indicators),
                "income": pd.DataFrame(income),
            })

        self.assertEqual(len(history), 4)
        self.assertEqual(merge_asof.call_count, 1)

    def test_statement_revisions_align_to_the_same_observable_revision(self):
        frames = {
            "fina_indicator": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425",
                    "end_date": "20260331", "roe": 10.0,
                },
                {
                    "ts_code": "000001.SZ", "ann_date": "20260711",
                    "end_date": "20260331", "roe": 20.0,
                },
            ]),
            "income": pd.DataFrame([
                {
                    "ts_code": "000001.SZ", "ann_date": "20260425",
                    "end_date": "20260331", "revenue": 100.0,
                    "operate_profit": 20.0, "n_income": 15.0,
                    "total_cogs": 70.0, "rd_exp": 5.0,
                },
                {
                    "ts_code": "000001.SZ", "ann_date": "20260711",
                    "end_date": "20260331", "revenue": 120.0,
                    "operate_profit": 30.0, "n_income": 22.0,
                    "total_cogs": 78.0, "rd_exp": 7.0,
                },
            ]),
        }
        prices = pd.DataFrame([
            {"code": "000001", "trade_date": "20260710"},
            {"code": "000001", "trade_date": "20260711"},
        ])

        history = build_fundamental_history(frames)
        featured = attach_point_in_time_features(prices, history).set_index("trade_date")

        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(float(featured.loc["20260710", "operating_margin"]), 0.20)
        self.assertAlmostEqual(float(featured.loc["20260711", "operating_margin"]), 0.25)

    def test_attaches_industry_membership_and_derives_industry_cycle_features(self):
        prices = pd.DataFrame([
            {"code": "000001", "trade_date": "20260710", "momentum_20": 0.10, "realized_volatility_20": 0.20, "return_1": 0.01, "roe": 10.0, "profit_growth": 5.0},
            {"code": "000002", "trade_date": "20260710", "momentum_20": 0.20, "realized_volatility_20": 0.30, "return_1": -0.01, "roe": 12.0, "profit_growth": -2.0},
            {"code": "600000", "trade_date": "20260710", "momentum_20": -0.10, "realized_volatility_20": 0.10, "return_1": 0.01, "roe": 8.0, "profit_growth": 1.0},
        ])
        members = pd.DataFrame([
            {"ts_code": "000001.SZ", "l1_name": "科技", "l2_name": "软件", "in_date": "20200101", "out_date": None},
            {"ts_code": "000002.SZ", "l1_name": "科技", "l2_name": "硬件", "in_date": "20200101", "out_date": None},
            {"ts_code": "600000.SH", "l1_name": "银行", "l2_name": "股份行", "in_date": "20200101", "out_date": None},
        ])

        featured = add_industry_features(attach_industry_membership(prices, members))
        tech = featured.loc[featured["industry"] == "科技"]

        self.assertEqual(set(tech["industry_l2"]), {"软件", "硬件"})
        self.assertAlmostEqual(float(tech.iloc[0]["industry_momentum_20"]), 0.15)
        self.assertAlmostEqual(float(tech.iloc[0]["industry_breadth"]), 0.5)
        self.assertIn(tech.iloc[0]["industry_cycle"], {"recovery", "expansion", "slowdown", "contraction"})

    def test_builds_qdii_premium_tracking_and_share_features_point_in_time(self):
        dates = pd.date_range("2026-06-01", periods=25, freq="B")
        prices = pd.DataFrame({
            "code": ["513100"] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "close": [1.01 + index * 0.011 for index in range(len(dates))],
            "return_1": [0.0] + [0.01] * (len(dates) - 1),
            "momentum_20": [float("nan")] * 20 + [0.20] * 5,
        })
        frames = {
            "fund_nav": pd.DataFrame([
                {
                    "ts_code": "513100.SH",
                    "nav_date": date.strftime("%Y%m%d"),
                    "ann_date": (date + pd.Timedelta(days=1)).strftime("%Y%m%d"),
                    "unit_nav": 1.0 + index * 0.01,
                }
                for index, date in enumerate(dates)
            ]),
            "fund_share": pd.DataFrame([
                {"ts_code": "513100.SH", "trade_date": date.strftime("%Y%m%d"), "fd_share": 100.0 + index}
                for index, date in enumerate(dates)
            ]),
        }

        featured = attach_qdii_point_in_time_features(prices, frames).set_index("trade_date")

        self.assertTrue(pd.isna(featured.iloc[0]["unit_nav"]))
        self.assertGreater(featured.iloc[-1]["discount_premium"], 0.0)
        self.assertTrue(pd.notna(featured.iloc[-1]["premium_persistence_20"]))
        self.assertTrue(pd.notna(featured.iloc[-1]["tracking_difference_20"]))
        self.assertGreater(featured.iloc[-1]["fund_share_change_20"], 0.0)


if __name__ == "__main__":
    unittest.main()
