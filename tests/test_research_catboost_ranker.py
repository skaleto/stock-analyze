import importlib.util
import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.catboost_ranker import fit_catboost_ranker


@unittest.skipUnless(importlib.util.find_spec("catboost"), "catboost training dependency missing")
class CatBoostRankerTest(unittest.TestCase):
    def test_uses_training_imputation_and_deterministic_bounded_parameters(self) -> None:
        train = pd.DataFrame({
            "trade_date": ["20200102", "20200102", "20200103", "20200103", "20200106", "20200106"],
            "code": ["000001", "000002", "000001", "000002", "000001", "000002"],
            "f1": [1.0, np.nan, 3.0, 4.0, 5.0, 6.0],
            "f2": [0.1, 0.2, 0.2, 0.1, 0.3, 0.4],
            "excess_return": [0.01, -0.01, 0.02, -0.02, 0.03, -0.01],
        })
        validation = pd.DataFrame({
            "trade_date": ["20210104", "20210104"],
            "code": ["000001", "000002"],
            "f1": [1000.0, np.nan],
            "f2": [0.2, 0.3],
            "excess_return": [0.01, -0.01],
        })
        first = fit_catboost_ranker(
            train,
            validation,
            feature_columns=("f1", "f2"),
            parameters={"iterations": 20, "depth": 2, "learning_rate": 0.05, "l2_leaf_reg": 10.0},
            random_state=7,
        )
        second = fit_catboost_ranker(
            train,
            validation,
            feature_columns=("f1", "f2"),
            parameters={"iterations": 20, "depth": 2, "learning_rate": 0.05, "l2_leaf_reg": 10.0},
            random_state=7,
        )
        self.assertEqual(first.imputation_values["f1"], 4.0)
        self.assertEqual(first.feature_columns, ("f1", "f2"))
        self.assertEqual(len(first.validation_predictions), 2)
        np.testing.assert_allclose(first.validation_predictions, second.validation_predictions)
        self.assertLessEqual(int(first.parameters["depth"]), 6)
        self.assertLessEqual(int(first.parameters["iterations"]), 1000)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(importlib.util.find_spec("catboost"), "catboost training dependency missing")
class CatBoostFrozenTestTest(CatBoostRankerTest):
    def test_frozen_fit_does_not_depend_on_validation_labels(self) -> None:
        train = pd.DataFrame({
            "trade_date": ["20200102", "20200102", "20200103", "20200103"],
            "code": ["000001", "000002"] * 2,
            "f1": [1.0, -1.0, 2.0, -2.0],
            "excess_return": [0.01, -0.01, 0.02, -0.02],
        })
        first_validation = pd.DataFrame({"trade_date": ["20210104", "20210104"], "code": ["000001", "000002"], "f1": [3.0, -3.0], "excess_return": [0.5, -0.5]})
        second_validation = first_validation.copy()
        second_validation["excess_return"] = [-0.5, 0.5]
        kwargs = dict(feature_columns=("f1",), parameters={"iterations": 20, "depth": 2}, random_state=7, use_validation_for_eval=False)
        first = fit_catboost_ranker(train, first_validation, **kwargs)
        second = fit_catboost_ranker(train, second_validation, **kwargs)
        np.testing.assert_allclose(first.validation_predictions, second.validation_predictions)
