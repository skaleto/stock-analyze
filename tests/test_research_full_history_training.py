import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.full_history_training import fit_predict_candidate


class FullHistoryTrainingTest(unittest.TestCase):
    def _frames(self):
        train = pd.DataFrame({
            "trade_date": ["20200102", "20200102", "20200103", "20200103", "20200106", "20200106"],
            "code": ["000001", "000002"] * 3,
            "f_good": [1.0, -1.0, 2.0, -2.0, 3.0, -3.0],
            "f_validation_only": [np.nan] * 6,
            "excess_return": [0.01, -0.01, 0.02, -0.02, 0.03, -0.03],
        })
        validation = pd.DataFrame({
            "trade_date": ["20210104", "20210104"],
            "code": ["000001", "000002"],
            "f_good": [4.0, -4.0],
            "f_validation_only": [100.0, -100.0],
            "excess_return": [0.04, -0.04],
        })
        return train, validation

    def test_elastic_net_selects_features_from_training_only(self):
        train, validation = self._frames()
        result = fit_predict_candidate(
            train,
            validation,
            candidate_features=("f_good", "f_validation_only"),
            estimator="elastic_net",
            parameters={"alpha": 0.001, "l1_ratio": 0.1},
            max_features=1,
            minimum_coverage=0.70,
            random_state=7,
        )
        self.assertEqual(result.selected_features, ("f_good",))
        self.assertEqual(len(result.predictions), 2)
        self.assertGreater(result.predictions[0], result.predictions[1])

    def test_additive_candidate_is_deterministic_and_bounded(self):
        train, validation = self._frames()
        first = fit_predict_candidate(
            train,
            validation,
            candidate_features=("f_good",),
            estimator="additive",
            parameters={"decay": 0.99, "coefficient_bound": 0.35},
            max_features=1,
            minimum_coverage=0.70,
            random_state=7,
        )
        second = fit_predict_candidate(
            train,
            validation,
            candidate_features=("f_good",),
            estimator="additive",
            parameters={"decay": 0.99, "coefficient_bound": 0.35},
            max_features=1,
            minimum_coverage=0.70,
            random_state=999,
        )
        np.testing.assert_allclose(first.predictions, second.predictions)
        self.assertLessEqual(max(abs(value) for value in first.coefficients.values()), 0.35)


if __name__ == "__main__":
    unittest.main()


class FullHistoryRankingTrainingTest(FullHistoryTrainingTest):
    def test_lambdarank_and_catboost_return_ordered_predictions(self):
        train, validation = self._frames()
        for estimator, parameters in (
            ("lightgbm_lambdarank", {"n_estimators": 20, "num_leaves": 7, "min_child_samples": 2}),
            ("catboost_ranker", {"iterations": 20, "depth": 2, "learning_rate": 0.05, "l2_leaf_reg": 10.0}),
        ):
            with self.subTest(estimator=estimator):
                result = fit_predict_candidate(
                    train,
                    validation,
                    candidate_features=("f_good",),
                    estimator=estimator,
                    parameters=parameters,
                    max_features=1,
                    minimum_coverage=0.70,
                    random_state=7,
                )
                self.assertEqual(len(result.predictions), 2)
                self.assertTrue(np.isfinite(result.predictions).all())
                if estimator == "catboost_ranker":
                    self.assertFalse(
                        result.metadata["validation_labels_visible"]
                    )


class FullHistoryWalkForwardEvaluationTest(unittest.TestCase):
    def test_walk_forward_purges_rows_with_late_labels_on_eligible_trade_date(self):
        from stock_analyze.research.full_history_training import evaluate_candidate_walk_forward
        from stock_analyze.research.full_history_windows import load_full_history_config

        rows = []
        for year in range(2018, 2025):
            for trade_date in pd.date_range(f"{year}-02-01", periods=24, freq="B"):
                for code_index in range(6):
                    signal = (code_index - 2.5) / 2.5
                    rows.append({
                        "code": f"{code_index + 1:06d}",
                        "account_id": "hs300",
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                        "label_end_date": (trade_date + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                        "entry_price": 10.0 + code_index,
                        "benchmark_entry_price": 100.0,
                        "horizon": 20,
                        "signal": signal,
                        "excess_return": signal * 0.01,
                    })
        dataset = pd.DataFrame(rows)
        same_day = dataset["trade_date"].eq("20200203")
        late_row = same_day & dataset["code"].eq("000006")
        self.assertTrue(late_row.any())
        dataset.loc[late_row, "label_end_date"] = "20210315"

        result = evaluate_candidate_walk_forward(
            dataset,
            contract=load_full_history_config("configs/research/full_history_rebuild.yaml"),
            scope="hs300",
            candidate_features=("signal",),
            estimator="elastic_net",
            parameters={"alpha": 0.0001, "l1_ratio": 0.1},
            portfolio_contract={
                "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 3}],
                "trading": {
                    "lot_size": 100,
                    "commission_rate": 0.0003,
                    "min_commission": 5.0,
                    "stamp_tax_rate": 0.0005,
                    "slippage_rate": 0.0005,
                    "max_single_weight": 0.34,
                },
            },
            random_state=7,
        )

        self.assertTrue(result["folds"][0]["point_in_time_audit"])

    def test_four_fold_evaluation_uses_exact_cost_replay(self):
        from stock_analyze.research.full_history_training import evaluate_candidate_walk_forward
        from stock_analyze.research.full_history_windows import load_full_history_config

        rows = []
        rng = np.random.default_rng(7)
        for year in range(2018, 2025):
            for trade_date in pd.date_range(f"{year}-02-01", periods=24, freq="B"):
                benchmark = 100.0 + (year - 2018)
                for code_index in range(10):
                    signal = (code_index - 4.5) / 4.5
                    rows.append({
                        "code": f"{code_index + 1:06d}",
                        "account_id": "hs300",
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                        "label_end_date": (trade_date + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                        "entry_price": 10.0 + code_index,
                        "benchmark_entry_price": benchmark,
                        "horizon": 20,
                        "signal": signal,
                        "excess_return": signal * 0.01 + rng.normal(scale=0.0001),
                    })
        contract = load_full_history_config("configs/research/full_history_rebuild.yaml")
        result = evaluate_candidate_walk_forward(
            pd.DataFrame(rows),
            contract=contract,
            scope="hs300",
            candidate_features=("signal",),
            estimator="elastic_net",
            parameters={"alpha": 0.0001, "l1_ratio": 0.1},
            portfolio_contract={
                "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 3}],
                "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5.0, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.34},
            },
            random_state=7,
        )
        self.assertEqual(result["expected_outer_folds"], 4)
        self.assertEqual(len(result["folds"]), 4)
        self.assertGreater(result["metrics"]["trade_count"], 0)
        self.assertEqual(result["metrics"]["simulator_version"], "paper-parity-daily-v1")
        self.assertIn("net_excess_return", result["cost_stress"])


class FullHistoryCampaignGovernanceTest(FullHistoryWalkForwardEvaluationTest):
    def test_campaign_accounts_for_all_preregistered_trials(self):
        from stock_analyze.research.full_history_training import evaluate_scope_campaign
        from stock_analyze.research.full_history_windows import load_full_history_config

        rows = []
        for year in range(2018, 2025):
            for trade_date in pd.date_range(f"{year}-03-01", periods=60, freq="B"):
                for code_index in range(8):
                    signal = (code_index - 3.5) / 3.5
                    rows.append({
                        "code": f"{code_index + 1:06d}", "account_id": "hs300",
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                        "label_end_date": (trade_date + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                        "entry_price": 10.0 + code_index, "benchmark_entry_price": 100.0,
                        "horizon": 20, "signal": signal, "excess_return": signal * 0.01,
                    })
        result = evaluate_scope_campaign(
            pd.DataFrame(rows),
            contract=load_full_history_config("configs/research/full_history_rebuild.yaml"),
            scope="hs300",
            candidate_features=("signal",),
            candidate_declarations={
                "elastic_net": [
                    {"alpha": 0.0001, "l1_ratio": value}
                    for value in (0.1, 0.2, 0.3)
                ],
                "additive": [{"decay": 0.99, "coefficient_bound": 0.35}],
            },
            portfolio_contract={
                "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 3}],
                "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5.0, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.34},
            },
            random_state=7,
        )
        self.assertEqual(result["declared_variant_count"], 4)
        self.assertEqual(result["declared_family_count"], 2)
        self.assertEqual(result["trial_count"], 2)
        self.assertEqual(result["governance"]["valid_trial_count"], 2)
        self.assertIn("deflated_sharpe_probability", result["governance"])
        self.assertIn("probability_of_backtest_overfit", result["governance"])

        for trial in result["trials"]:
            self.assertEqual(len(trial["folds"]), 4)
            for fold in trial["folds"]:
                self.assertEqual(fold["inner_fold_count"], 3)
                self.assertTrue(fold["inner_point_in_time_audit"])
                self.assertEqual(
                    len(fold["inner_selection"]),
                    3 if trial["estimator"] == "elastic_net" else 1,
                )


class FullHistoryFrozenTestEvaluationTest(FullHistoryWalkForwardEvaluationTest):
    def test_historical_test_opens_manifest_once_and_replays_costs(self):
        import tempfile
        from pathlib import Path
        from stock_analyze.research.full_history_training import evaluate_frozen_historical_test
        from stock_analyze.research.full_history_windows import load_full_history_config, seal_full_history_manifest

        rows = []
        for year in range(2018, 2027):
            for trade_date in pd.date_range(f"{year}-04-01", periods=20, freq="B"):
                for code_index in range(8):
                    signal = (code_index - 3.5) / 3.5
                    rows.append({
                        "code": f"{code_index + 1:06d}", "account_id": "hs300",
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                        "label_end_date": (trade_date + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                        "entry_price": 10.0 + code_index, "benchmark_entry_price": 100.0,
                        "horizon": 20, "signal": signal, "excess_return": signal * 0.01,
                    })
        dataset = pd.DataFrame(rows)
        late_row = dataset["trade_date"].eq("20240401") & dataset["code"].eq("000008")
        self.assertTrue(late_row.any())
        dataset.loc[late_row, "label_end_date"] = "20250115"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            manifest = seal_full_history_manifest(path, {"scope": "hs300", "data_fingerprint": "abc"})
            result = evaluate_frozen_historical_test(
                dataset,
                contract=load_full_history_config("configs/research/full_history_rebuild.yaml"),
                scope="hs300",
                candidate_features=("signal",),
                estimator="elastic_net",
                parameters={"alpha": 0.0001, "l1_ratio": 0.1},
                portfolio_contract={
                    "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 3}],
                    "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5.0, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.34},
                },
                manifest_path=path,
                declaration_id=manifest["declaration_id"],
                random_state=7,
            )
            repeated = evaluate_frozen_historical_test(
                dataset, contract=load_full_history_config("configs/research/full_history_rebuild.yaml"), scope="hs300", candidate_features=("signal",), estimator="elastic_net", parameters={"alpha": 0.0001, "l1_ratio": 0.1}, portfolio_contract={"accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 3}], "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5.0, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.34}}, manifest_path=path, declaration_id=manifest["declaration_id"], random_state=7,
            )
        self.assertEqual(result["historical_test_status"], "diagnostic_only_already_observed")
        self.assertTrue(result["development_point_in_time_audit"])
        self.assertGreater(result["metrics"]["trade_count"], 0)
        self.assertIn("net_excess_return", result["cost_stress"])
        self.assertEqual(result["metrics"], repeated["metrics"])
