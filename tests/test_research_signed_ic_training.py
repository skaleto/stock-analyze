import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.signed_ic import SignedICConfig
from stock_analyze.research.signed_ic_training import fit_signed_candidate


class SignedICTrainingTest(unittest.TestCase):
    def _frames(self):
        rng = np.random.default_rng(19)
        rows = []
        dates = pd.bdate_range("2018-01-01", periods=90)
        for day in dates:
            residual = rng.normal(size=25)
            quality = rng.normal(size=25)
            target = residual + 0.5 * quality + rng.normal(scale=0.1, size=25)
            for index in range(25):
                rows.append({
                    "trade_date": day.strftime("%Y%m%d"),
                    "code": f"{index:06d}",
                    "excess_return": target[index],
                    "exante_residual_momentum_20_5": residual[index],
                    "quality": quality[index],
                })
        frame = pd.DataFrame(rows)
        return frame.loc[frame["trade_date"].lt("20180415")], frame.loc[
            frame["trade_date"].ge("20180415")
        ]

    def _selector(self):
        return SignedICConfig(
            minimum_abs_mean_ic=0.02,
            bootstrap_samples=200,
            bootstrap_block_length=10,
            fdr_q=0.10,
            max_features=4,
        )

    def test_composite_is_ordered_and_train_only(self):
        train, validation = self._frames()
        result = fit_signed_candidate(
            train,
            validation,
            candidate_features=(
                "exante_residual_momentum_20_5",
                "quality",
            ),
            feature_families={
                "exante_residual_momentum_20_5": "residual_momentum",
                "quality": "quality",
            },
            selector_config=self._selector(),
            estimator="signed_ic_composite",
            parameters={},
            seed=23,
        )
        self.assertEqual(len(result.predictions), len(validation))
        self.assertGreater(
            pd.Series(result.predictions).corr(
                validation["excess_return"].reset_index(drop=True),
                method="spearman",
            ),
            0.8,
        )
        changed = validation.copy()
        changed["excess_return"] *= -1
        rerun = fit_signed_candidate(
            train,
            changed,
            candidate_features=(
                "exante_residual_momentum_20_5",
                "quality",
            ),
            feature_families={
                "exante_residual_momentum_20_5": "residual_momentum",
                "quality": "quality",
            },
            selector_config=self._selector(),
            estimator="signed_ic_composite",
            parameters={},
            seed=23,
        )
        np.testing.assert_allclose(result.predictions, rerun.predictions)

    def test_rejects_missing_residual_momentum(self):
        train, validation = self._frames()
        with self.assertRaisesRegex(ValueError, "signed_ic_residual_required"):
            fit_signed_candidate(
                train,
                validation,
                candidate_features=("quality",),
                feature_families={"quality": "quality"},
                selector_config=self._selector(),
                estimator="positive_elastic_net",
                parameters={"alpha": 0.001, "l1_ratio": 0.25},
                seed=29,
            )
