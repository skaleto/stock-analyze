import unittest

import pandas as pd

from stock_analyze.research.account_features import (
    account_feature_contract,
    alpha158_lite_feature_columns,
    build_alpha158_lite_feature_view,
    build_account_feature_view,
    date_balanced_sample_weights,
)


class ResearchAccountFeaturesTest(unittest.TestCase):
    def test_date_balanced_weights_give_each_date_equal_total_weight(self) -> None:
        frame = pd.DataFrame({
            "trade_date": ["20260102", "20260102", "20260103"],
        })

        weights = date_balanced_sample_weights(frame)

        totals = weights.groupby(frame["trade_date"]).sum()
        self.assertAlmostEqual(float(totals.loc["20260102"]), 1.0)
        self.assertAlmostEqual(float(totals.loc["20260103"]), 1.0)

    @staticmethod
    def _rows() -> pd.DataFrame:
        return pd.DataFrame([
            {
                "trade_date": day,
                "code": code,
                "account_id": scope,
                "research_scope": scope,
                "industry": industry,
                "momentum_20": momentum,
                "momentum_5": momentum * 0.4,
                "momentum_10": momentum * 0.7,
                "momentum_60": momentum * 1.5,
                "momentum_120": momentum * 2.0,
                "return_1": momentum * 0.1,
                "realized_volatility_20": volatility,
                "downside_volatility_20": volatility * 0.8,
                "drawdown_60": -volatility * 0.2,
                "amihud_illiquidity_20": 1.0 / amount,
                "price_volume_confirmation_20": momentum * amount,
                "avg_amount_20": amount,
                "total_mv": amount * 100.0,
                "pe_ttm": 10.0 + amount / 100.0,
                "pb": 1.0 + amount / 1_000.0,
                "roe": roe,
            }
            for day in ("20260709", "20260710")
            for code, scope, industry, momentum, volatility, amount, roe in (
                ("000001", "hs300", "科技", 0.10, 0.20, 100.0, 0.15),
                ("000002", "hs300", "科技", 0.06, 0.30, 80.0, 0.12),
                ("600000", "hs300", "银行", -0.02, 0.10, 120.0, 0.10),
                ("000905", "zz500", "制造", 0.20, 0.40, 50.0, 0.08),
            )
        ])

    def test_view_is_scoped_and_account_relative_by_date(self):
        result = build_account_feature_view(
            self._rows(),
            account_scope="hs300",
        )

        self.assertEqual(set(result["account_id"]), {"hs300"})
        means = result.groupby("trade_date")["account_residual_momentum_20"].mean()
        self.assertTrue((means.abs() < 1e-12).all())
        self.assertTrue(result["account_liquidity_percentile"].between(0.0, 1.0).all())

    def test_contract_excludes_raw_levels_and_caps_feature_count(self):
        contract = account_feature_contract("a_share", "hs300", 3)

        self.assertLessEqual(contract.max_features, 12)
        self.assertLessEqual(contract.max_per_family, 3)
        self.assertTrue(
            {"close", "unit_nav", "obv", "ad", "atr_14"}.isdisjoint(
                contract.allowed_features
            )
        )

    def test_h20_contract_prioritizes_slow_quality_value_features(self):
        contract = account_feature_contract("a_share", "hs300", 20)

        self.assertTrue({
            "pe_ttm", "pb", "roe", "roic", "cash_conversion",
            "accrual_ratio", "free_cashflow_to_assets",
            "gross_profit_to_assets", "account_low_volatility_percentile",
        }.issubset(contract.allowed_features))
        self.assertTrue({
            "momentum_20", "macd_hist_slope_pct", "amount_ratio_5_20",
        }.isdisjoint(contract.allowed_features))

    def test_alpha158_lite_view_exposes_cross_section_industry_and_regime_inputs(self):
        result = build_alpha158_lite_feature_view(
            self._rows(),
            account_scope="hs300",
        )
        columns = alpha158_lite_feature_columns(result)

        self.assertTrue({
            "momentum_20_cs_rank",
            "momentum_20_industry_rank",
            "log_total_mv_cs_rank",
            "market_breadth_1",
            "market_median_momentum_20",
            "market_cross_sectional_volatility_1",
            "roe_missing",
        }.issubset(columns))
        by_date = result.groupby("trade_date")["momentum_20_cs_rank"]
        self.assertTrue((by_date.mean().abs() < 1e-12).all())
        self.assertTrue(result["market_breadth_1"].between(0.0, 1.0).all())

    def test_scope_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "account_feature_scope_mismatch"):
            build_account_feature_view(
                self._rows().loc[lambda frame: frame["account_id"].eq("hs300")],
                account_scope="zz500",
            )


if __name__ == "__main__":
    unittest.main()
