import unittest
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.models import (
    MultiClassCalibrator,
    _bounded_cross_section_sample,
    _portfolio_oos_metrics,
    _select_features,
    load_model_bundle,
    make_purged_walk_forward_splits,
    save_model_bundle,
    train_model_bundle,
)


def model_dataset(rows: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    dates = pd.date_range("2023-01-02", periods=rows, freq="B")
    factor_a = rng.normal(size=rows)
    factor_b = rng.normal(size=rows)
    score = factor_a - 0.65 * factor_b + rng.normal(scale=0.5, size=rows)
    labels = np.where(score > 0.55, "up", np.where(score < -0.55, "down", "flat"))
    returns = 0.018 * score + rng.normal(scale=0.01, size=rows)
    return pd.DataFrame(
        {
            "code": [f"{index % 80:06d}" for index in range(rows)],
            "trade_date": dates.strftime("%Y%m%d"),
            "entry_date": (dates + pd.offsets.BDay(1)).strftime("%Y%m%d"),
            "label_end_date": (dates + pd.offsets.BDay(5)).strftime("%Y%m%d"),
            "label_contract_version": ["next-open-v1"] * rows,
            "unbiased_universe": [True] * rows,
            "universe_quality": ["available"] * rows,
            "universe_contract_version": ["pit-universe-v1"] * rows,
            "membership_source": ["fixture"] * rows,
            "account_id": ["test_account"] * rows,
            "horizon": [5] * rows,
            "factor_a": factor_a,
            "factor_b": factor_b,
            "label": labels,
            "excess_return": returns,
        }
    )


class ResearchModelsTest(unittest.TestCase):
    def test_feature_selection_uses_training_labels_and_prunes_redundancy(self):
        rng = np.random.default_rng(91)
        dates = pd.date_range("2025-01-02", periods=80, freq="B")
        rows = []
        for trade_date in dates:
            signal = rng.normal(size=12)
            noise = rng.normal(size=12)
            for code_index in range(12):
                rows.append({
                    "code": f"{code_index:06d}",
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "excess_return": 0.02 * signal[code_index] + rng.normal(scale=0.002),
                    "signal": signal[code_index],
                    "signal_copy": signal[code_index],
                    "noise": noise[code_index],
                })
        training = pd.DataFrame(rows)

        selected, diagnostics = _select_features(
            training,
            ("signal", "signal_copy", "noise"),
            max_features=2,
        )

        self.assertIn("signal", selected)
        self.assertNotIn("signal_copy", selected)
        self.assertNotIn("noise", selected)
        self.assertEqual(diagnostics["candidate_count"], 3)
        self.assertEqual(diagnostics["selected_count"], 1)
        self.assertIn("noise", diagnostics["rejected_weak_features"])

    def test_feature_selection_does_not_treat_account_level_gap_as_stock_alpha(self):
        rng = np.random.default_rng(731)
        rows = []
        for trade_date in pd.date_range("2025-01-02", periods=80, freq="B"):
            for account_id, feature_level, return_level in (
                ("hs300", 10.0, 0.02),
                ("zz500", -10.0, -0.02),
            ):
                for code_index in range(10):
                    rows.append({
                        "account_id": account_id,
                        "code": f"{account_id}-{code_index}",
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "excess_return": return_level + rng.normal(scale=0.003),
                        "account_gap_only": feature_level + rng.normal(),
                    })

        with self.assertRaisesRegex(
            ValueError,
            "feature_selection_no_stable_signal",
        ):
            _select_features(
                pd.DataFrame(rows),
                ("account_gap_only",),
            )

    def test_temperature_calibrator_is_multiclass_and_normalized(self):
        probabilities = np.array([
            [0.75, 0.15, 0.10],
            [0.15, 0.70, 0.15],
            [0.10, 0.20, 0.70],
            [0.60, 0.25, 0.15],
            [0.20, 0.60, 0.20],
            [0.15, 0.20, 0.65],
        ])
        labels = np.array(["down", "flat", "up", "down", "flat", "up"])

        calibrator = MultiClassCalibrator("temperature", ("down", "flat", "up")).fit(
            probabilities,
            labels,
        )
        calibrated = calibrator.predict(probabilities)

        np.testing.assert_allclose(calibrated.sum(axis=1), np.ones(len(labels)), atol=1e-12)
        self.assertGreater(calibrator.temperature, 0.0)

    def test_portfolio_metrics_use_non_overlapping_horizon_rebalances(self):
        dates = pd.date_range("2026-01-01", periods=12, freq="B").strftime("%Y%m%d")
        evaluation = pd.DataFrame(
            [
                {
                    "trade_date": trade_date,
                    "code": code,
                    "score": score,
                    "excess_return": excess_return,
                }
                for trade_date in dates
                for code, score, excess_return in (
                    ("000001", 1.0, 0.012),
                    ("000002", 0.5, 0.004),
                    ("000003", 0.0, 0.0),
                    ("000004", -0.5, -0.004),
                )
            ]
        )

        metrics = _portfolio_oos_metrics(evaluation, horizon=3)

        self.assertEqual(metrics["portfolio_rebalance_periods"], 4)
        self.assertEqual(metrics["portfolio_horizon"], 3)
        self.assertEqual(metrics["annual_turnover"], 0.0)
        expected_period_return = 0.012 - np.mean([0.012, 0.004, 0.0, -0.004])
        expected_annualized = (1.0 + expected_period_return) ** (252 / 3) - 1.0
        self.assertAlmostEqual(metrics["net_excess_return"], expected_annualized)

    def test_portfolio_metrics_keep_existing_names_inside_rank_buffer(self):
        dates = pd.date_range("2026-01-01", periods=2, freq="B").strftime("%Y%m%d")
        first_order = list(range(10))
        second_order = [1, 2, 0, 3, 4, 5, 6, 7, 8, 9]
        rows = []
        for trade_date, ordering in zip(dates, (first_order, second_order)):
            rank_by_code = {code: rank for rank, code in enumerate(ordering)}
            rows.extend({
                "trade_date": trade_date,
                "code": f"{code:06d}",
                "score": 10.0 - rank_by_code[code],
                "excess_return": 0.01 - code * 0.0001,
            } for code in range(10))

        metrics = _portfolio_oos_metrics(pd.DataFrame(rows), horizon=1)

        self.assertEqual(metrics["portfolio_rebalance_periods"], 2)
        self.assertEqual(metrics["annual_turnover"], 0.0)

    def test_walk_forward_split_purges_overlap_and_embargo(self):
        data = model_dataset(180)

        splits = make_purged_walk_forward_splits(data, n_splits=3, embargo=5)

        self.assertEqual(len(splits), 3)
        for split in splits:
            train = data.loc[split.train_indices]
            validation = data.loc[split.validation_indices]
            self.assertLess(train["label_end_date"].max(), validation["trade_date"].min())
            self.assertLess(split.train_indices.max(), split.validation_indices.min() - 5)

    def test_walk_forward_never_splits_same_cross_sectional_date(self):
        base = model_dataset(180)
        duplicated = pd.concat(
            [base.assign(code=f"{offset:06d}") for offset in range(10)],
            ignore_index=True,
        ).sort_values(["trade_date", "code"]).iloc[1:].reset_index(drop=True)

        splits = make_purged_walk_forward_splits(duplicated, n_splits=3, embargo=5)

        self.assertEqual(len(splits), 3)
        expected_counts = duplicated.groupby("trade_date").size()
        for split in splits:
            train_dates = set(duplicated.loc[split.train_indices, "trade_date"])
            validation_frame = duplicated.loc[split.validation_indices]
            validation_dates = set(validation_frame["trade_date"])
            self.assertFalse(train_dates.intersection(validation_dates))
            self.assertTrue(
                validation_frame.groupby("trade_date").size().eq(expected_counts.loc[list(validation_dates)]).all()
            )
            self.assertLess(
                duplicated.loc[split.train_indices, "label_end_date"].max(),
                duplicated.loc[split.validation_indices, "trade_date"].min(),
            )

    def test_training_sample_is_bounded_deterministic_and_date_balanced(self):
        base = model_dataset(200)
        panel = pd.concat(
            [base.assign(code=f"{offset:06d}") for offset in range(50)],
            ignore_index=True,
        )

        first = _bounded_cross_section_sample(panel, max_rows=2_000, random_state=7)
        second = _bounded_cross_section_sample(panel, max_rows=2_000, random_state=7)

        self.assertLessEqual(len(first), 2_000)
        self.assertEqual(first["trade_date"].nunique(), panel["trade_date"].nunique())
        self.assertGreater(first["code"].nunique(), 40)
        pd.testing.assert_frame_equal(first, second)

    def test_trains_calibrated_deterministic_ensemble(self):
        data = model_dataset()

        first = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=11)
        second = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=11)
        sample = data.iloc[-12:]
        first_probabilities = first.predict_proba(sample)
        second_probabilities = second.predict_proba(sample)

        np.testing.assert_allclose(first_probabilities.sum(axis=1), np.ones(len(sample)), atol=1e-9)
        np.testing.assert_allclose(first_probabilities, second_probabilities, atol=1e-12)
        self.assertEqual(first.class_order, ("down", "flat", "up"))
        self.assertLess(first.split_dates["train_end"], first.split_dates["calibration_start"])
        self.assertLess(first.split_dates["calibration_end"], first.split_dates["validation_start"])
        self.assertIn("log_loss", first.metrics)
        self.assertTrue({
            "feature_coverage",
            "point_in_time_audit",
            "oos_predictions",
            "rank_ic",
            "icir",
            "brier_improvement",
            "hit_rate_uplift",
            "auc",
            "net_excess_return",
            "max_drawdown",
            "annual_turnover",
            "ablation_stability",
        }.issubset(first.metrics))
        self.assertIn("logistic=", first.calibration_method)
        self.assertIn("boosting=", first.calibration_method)
        self.assertGreaterEqual(first.ensemble_logistic_weight, 0.0)
        self.assertLessEqual(first.ensemble_logistic_weight, 1.0)
        self.assertGreaterEqual(first.metrics["selected_feature_count"], 1)
        self.assertIn("reliability_curve", first.metrics)
        self.assertEqual(first.metrics["walk_forward_splits"], 3)
        self.assertEqual(
            first.metrics["training_protocol_version"],
            "purged_walk_forward_v4_dual_head_multiseed",
        )
        self.assertGreater(first.metrics["oos_predictions"], len(data) * 0.20)
        self.assertEqual(first.split_dates["validation_mode"], "purged_walk_forward")
        horizon_data = data.loc[data["horizon"] == 5]
        self.assertEqual(
            first.split_dates["deployment_calibration_end"],
            str(horizon_data["trade_date"].max()),
        )
        self.assertIn("data_fingerprint", first.metrics)
        self.assertTrue(first.metrics["unbiased_universe"])
        self.assertEqual(first.metrics["label_contract_version"], "next-open-v1")
        self.assertEqual(first.metrics["universe_contract_version"], "pit-universe-v1")
        self.assertEqual(first.metrics["effective_dates"], first.metrics["oos_predictions"])

    def test_training_uses_executable_account_replay_when_contract_is_supplied(self):
        rng = np.random.default_rng(318)
        dates = pd.date_range("2024-01-02", periods=130, freq="B")
        rows = []
        for day_index, trade_date in enumerate(dates):
            signal = rng.normal(size=8)
            for code_index in range(8):
                entry = 10.0 + code_index + day_index * 0.01
                realized = 0.01 * signal[code_index] + rng.normal(scale=0.003)
                rows.append({
                    "code": f"{code_index + 1:06d}",
                    "account_id": "hs300",
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                    "entry_price": entry,
                    "benchmark_entry_price": 100.0 + day_index * 0.02,
                    "label_end_date": (trade_date + pd.offsets.BDay(5)).strftime("%Y%m%d"),
                    "label_contract_version": "next-open-v1",
                    "unbiased_universe": True,
                    "universe_quality": "available",
                    "universe_contract_version": "pit-universe-v1",
                    "membership_source": "fixture",
                    "horizon": 5,
                    "factor_a": signal[code_index],
                    "factor_b": -signal[code_index] + rng.normal(scale=0.2),
                    "avg_amount_20": 50_000_000.0,
                    "realized_volatility_20": 0.20,
                    "label": "up" if realized > 0.002 else "down" if realized < -0.002 else "flat",
                    "excess_return": realized,
                })
        contract = {
            "accounts": [{"id": "hs300", "cash": 200_000.0, "top_n": 3}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.20,
            },
            "execution_policy": {
                "version": "cost-aware-aim-v1",
                "rank_buffer_pct": 0.50,
                "minimum_target_change": 0.01,
                "partial_adjustment_rate": 0.35,
                "max_daily_turnover": 0.10,
                "cost_safety_multiple": 1.50,
                "alpha_persistence": 1.0,
            },
        }

        bundle = train_model_bundle(
            pd.DataFrame(rows),
            feature_columns=["factor_a", "factor_b"],
            horizon=5,
            portfolio_contract=contract,
        )

        self.assertEqual(bundle.metrics["simulator_version"], "paper-parity-daily-v1")
        self.assertGreater(bundle.metrics["trade_count"], 0)
        self.assertGreater(bundle.metrics["decision_count"], 0)
        self.assertEqual(bundle.metrics["execution_policy_version"], "cost-aware-aim-v1")
        self.assertIn("hs300", bundle.metrics["account_metrics"])
        self.assertIsNotNone(bundle.metrics["portfolio_contract_hash"])
        self.assertIn("no_trade", bundle.metrics["baseline_comparison"])
        self.assertIn("low_volatility_20", bundle.metrics["baseline_comparison"])
        self.assertGreaterEqual(bundle.metrics["valid_trial_count"], 4)
        trial_dates = {
            tuple(item["date"] for item in trial["oos_returns"])
            for trial in bundle.metrics["predeclared_trial_results"]
        }
        self.assertEqual(len(trial_dates), 1)

    def test_probability_and_ranking_heads_are_independent(self):
        data = model_dataset()
        bundle = train_model_bundle(
            data,
            feature_columns=["factor_a", "factor_b"],
            horizon=5,
            random_state=31,
        )
        sample = data.iloc[-24:].copy()

        probabilities = bundle.predict_proba(sample)
        expected_excess = bundle.predict_excess_return(sample)

        self.assertEqual(probabilities.shape, (len(sample), 3))
        self.assertEqual(expected_excess.shape, (len(sample),))
        self.assertGreater(
            abs(pd.Series(expected_excess).corr(sample["excess_return"].reset_index(drop=True))),
            0.10,
        )
        self.assertTrue(hasattr(bundle, "linear_ranking_model"))
        self.assertTrue(hasattr(bundle, "boosting_ranking_models"))
        self.assertEqual(bundle.metrics["training_seed_count"], 3)
        self.assertIn("seed_rank_ic_std", bundle.metrics)
        self.assertIn("ranking_ensemble_linear_weight", bundle.metrics)
        self.assertEqual(
            bundle.metrics["training_protocol_version"],
            "purged_walk_forward_v4_dual_head_multiseed",
        )

    def test_training_reference_detects_out_of_distribution_values(self):
        data = model_dataset()
        bundle = train_model_bundle(
            data,
            feature_columns=["factor_a", "factor_b"],
            horizon=5,
            random_state=23,
        )
        sample = data.iloc[-2:].copy()
        sample.loc[sample.index[0], "factor_a"] = 1_000_000.0

        ratios = bundle.out_of_distribution_ratios(sample)
        drift = bundle.feature_drift(sample)

        self.assertGreater(ratios[0], 0.0)
        self.assertEqual(ratios[1], 0.0)
        self.assertGreaterEqual(drift["max_psi"], 0.0)

    def test_model_artifact_has_auditable_metadata(self):
        data = model_dataset()
        bundle = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = save_model_bundle(bundle, Path(tmp) / "model.joblib")
            restored = load_model_bundle(artifact)
            metadata = json.loads((Path(tmp) / "model.metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(restored.model_version, bundle.model_version)
        self.assertEqual(metadata["feature_columns"], ["factor_a", "factor_b"])
        self.assertIn("scikit_learn", metadata["dependency_versions"])


if __name__ == "__main__":
    unittest.main()
