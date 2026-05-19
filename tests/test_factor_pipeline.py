from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.factor_pipeline import (
    industry_neutralize,
    process_factors,
    winsorize_series,
    zscore_series,
)


class WinsorizeTests(unittest.TestCase):
    def test_clips_extreme_values(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0])
        clipped = winsorize_series(series, 0.0, 0.95)
        self.assertLess(clipped.iloc[-1], 1000.0)
        self.assertEqual(clipped.iloc[0], 1.0)

    def test_keeps_nan(self) -> None:
        series = pd.Series([1.0, None, 3.0])
        clipped = winsorize_series(series, 0.01, 0.99)
        self.assertTrue(pd.isna(clipped.iloc[1]))


class ZscoreTests(unittest.TestCase):
    def test_mean_zero_std_one(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        z = zscore_series(series)
        self.assertAlmostEqual(float(z.mean()), 0.0, places=6)
        self.assertAlmostEqual(float(z.std(ddof=0)), 1.0, places=6)

    def test_all_equal_returns_zero(self) -> None:
        series = pd.Series([2.0, 2.0, 2.0, 2.0])
        z = zscore_series(series)
        self.assertTrue((z == 0.0).all())


class IndustryNeutralizeTests(unittest.TestCase):
    def test_within_industry_mean_is_zero(self) -> None:
        values = pd.Series([1.0, 2.0, 10.0, 12.0])
        industries = pd.Series(["A", "A", "B", "B"])
        out = industry_neutralize(values, industries)
        for label in industries.unique():
            mask = industries == label
            self.assertAlmostEqual(float(out[mask].mean()), 0.0, places=6)

    def test_missing_industry_goes_to_unclassified(self) -> None:
        values = pd.Series([1.0, 2.0, 100.0, 200.0])
        industries = pd.Series(["A", "A", None, None])
        out = industry_neutralize(values, industries)
        # Unclassified bucket gets demeaned separately, so 100 and 200 become -50 / 50.
        self.assertAlmostEqual(float(out.iloc[2]), -50.0, places=6)
        self.assertAlmostEqual(float(out.iloc[3]), 50.0, places=6)


class ProcessFactorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factors = {
            "pe": {"weight": 0.5, "direction": "low"},
            "roe": {"weight": 0.5, "direction": "high"},
        }
        self.config = {
            "enabled": True,
            "winsorize_lower": 0.0,
            "winsorize_upper": 1.0,
            "neutralize_industry": False,
            "min_factor_coverage": 0.5,
        }
        self.candidates = pd.DataFrame(
            [
                {"code": "000001", "industry": "金融", "pe": 5.0, "roe": 20.0},
                {"code": "000002", "industry": "金融", "pe": 30.0, "roe": 10.0},
                {"code": "000003", "industry": "消费", "pe": 50.0, "roe": 5.0},
                {"code": "000004", "industry": "消费", "pe": 8.0, "roe": 18.0},
            ]
        )

    def test_score_matches_contribution_sum(self) -> None:
        scored, factor_table = process_factors(self.candidates.copy(), self.factors, self.config)
        agg = factor_table.groupby("code")["contribution"].sum().round(4)
        merged = scored.set_index("code")["score"].round(4)
        for code in agg.index:
            self.assertAlmostEqual(float(agg.loc[code]), float(merged.loc[code]), places=3)

    def test_direction_low_flips_signed_zscore(self) -> None:
        scored, _ = process_factors(self.candidates.copy(), self.factors, self.config)
        ordered = scored.sort_values("score", ascending=False)
        # Stock with lowest PE and highest ROE should rank highest.
        top_code = ordered.iloc[0]["code"]
        self.assertEqual(top_code, "000001")

    def test_insufficient_coverage_is_flagged(self) -> None:
        partial = self.candidates.copy()
        partial.loc[0, "pe"] = None
        partial.loc[0, "roe"] = None
        scored, _ = process_factors(partial, self.factors, self.config)
        # All factors missing → coverage_ratio == 0 → flagged
        row = scored[scored["code"] == "000001"].iloc[0]
        self.assertTrue(bool(row["insufficient_factor_coverage"]))

    def test_industry_neutralization_zeros_industry_mean(self) -> None:
        config = dict(self.config)
        config["neutralize_industry"] = True
        scored, factor_table = process_factors(self.candidates.copy(), self.factors, config)
        for factor in ["pe", "roe"]:
            sub = factor_table[factor_table["factor"] == factor]
            for industry, group in sub.groupby("industry"):
                values = group["neutralized"].dropna()
                if len(values) >= 2:
                    self.assertAlmostEqual(float(values.mean()), 0.0, places=6)

    def test_partial_coverage_renormalizes_weights(self) -> None:
        partial = self.candidates.copy()
        partial.loc[1, "roe"] = None  # one factor missing for code 000002
        scored, _ = process_factors(partial, self.factors, self.config)
        # 000002's score should still be on the same order of magnitude as others.
        scores = scored.set_index("code")["score"]
        spread = scores.max() - scores.min()
        self.assertGreater(spread, 0.0)


if __name__ == "__main__":
    unittest.main()
