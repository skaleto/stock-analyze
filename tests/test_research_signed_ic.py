import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.signed_ic import (
    SignedICConfig,
    select_signed_ic_features,
)


class SignedICSelectionTest(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        rng = np.random.default_rng(7)
        rows = []
        ninety = 90
        dates = pd.bdate_range("2018-01-01", periods=ninety)
        for day_number, day in enumerate(dates):
            base = rng.normal(size=30)
            secondary = rng.normal(size=30)
            noise = rng.normal(scale=0.15, size=30)
            target = base + 0.7 * secondary + noise
            for code_number in range(30):
                rows.append({
                    "trade_date": day.strftime("%Y%m%d"),
                    "code": f"{code_number:06d}",
                    "excess_return": target[code_number],
                    "exante_residual_momentum_20_5": base[code_number],
                    "stable_negative": -secondary[code_number],
                    "redundant": base[code_number] + rng.normal(scale=0.001),
                    "noise": rng.normal(),
                    "unstable": (
                        base[code_number]
                        if day_number < ninety // 2
                        else -base[code_number]
                    ),
                })
        return pd.DataFrame(rows)

    def _config(self) -> SignedICConfig:
        return SignedICConfig(
            minimum_coverage=0.70,
            minimum_abs_mean_ic=0.05,
            minimum_monthly_positive_rate=0.55,
            minimum_subperiod_agreement=2 / 3,
            bootstrap_samples=300,
            bootstrap_block_length=10,
            fdr_q=0.10,
            redundancy_threshold=0.80,
            max_features=4,
            max_per_family=2,
        )

    def test_flips_stable_negative_and_rejects_noise_and_instability(self):
        frame = self._frame()
        selection = select_signed_ic_features(
            frame,
            candidate_features=(
                "exante_residual_momentum_20_5",
                "stable_negative",
                "noise",
                "unstable",
            ),
            feature_families={
                "exante_residual_momentum_20_5": "residual_momentum",
                "stable_negative": "quality",
                "noise": "quality",
                "unstable": "value",
            },
            config=self._config(),
            seed=11,
        )
        self.assertIn("exante_residual_momentum_20_5", selection.selected_features)
        negative = next(item for item in selection.audits if item.feature == "stable_negative")
        self.assertEqual(negative.direction, -1)
        self.assertTrue(negative.eligible)
        rejected = {
            item.feature: item.rejection_reasons for item in selection.audits
        }
        self.assertTrue(rejected["noise"])
        self.assertTrue(rejected["unstable"])

    def test_prunes_redundant_feature(self):
        frame = self._frame()
        selection = select_signed_ic_features(
            frame,
            candidate_features=(
                "exante_residual_momentum_20_5",
                "redundant",
            ),
            feature_families={
                "exante_residual_momentum_20_5": "residual_momentum",
                "redundant": "momentum",
            },
            config=self._config(),
            seed=13,
        )
        self.assertEqual(len(selection.selected_features), 1)
        reasons = {
            item.feature: item.rejection_reasons for item in selection.audits
        }
        self.assertTrue(any("redundant_with:" in reason for values in reasons.values() for reason in values))

    def test_transformed_features_follow_canonical_direction(self):
        frame = self._frame()
        selection = select_signed_ic_features(
            frame,
            candidate_features=("stable_negative",),
            feature_families={"stable_negative": "quality"},
            config=self._config(),
            seed=17,
        )
        transformed = selection.transform(frame)
        correlation = transformed["stable_negative"].corr(frame["excess_return"], method="spearman")
        self.assertGreater(float(correlation), 0.50)


if __name__ == "__main__":
    unittest.main()
