import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yaml

from stock_analyze.research.tabular_ranker import (
    _construct_candidate_score,
    _model_target_values,
    _select_candidate_evidence,
    _should_run_lambdarank_fallback,
    evaluate_regime_tabular_candidate,
    evaluate_tabular_development_gate,
    fit_walk_forward_tabular_ranker,
    load_tabular_ranker_config,
    make_rolling_purged_splits,
    recency_date_balanced_weights,
)


def _panel() -> pd.DataFrame:
    dates = pd.date_range("2022-01-03", periods=300, freq="B")
    rows = []
    rng = np.random.default_rng(20260810)
    for date_index, trade_date in enumerate(dates):
        regime = -1.0 if (date_index // 40) % 2 == 0 else 1.0
        for code_index in range(30):
            signal = (code_index - 14.5) / 14.5
            nonlinear = signal * regime + 0.35 * signal * signal
            rows.append({
                "trade_date": trade_date.strftime("%Y%m%d"),
                "label_end_date": (
                    trade_date + pd.offsets.BDay(20)
                ).strftime("%Y%m%d"),
                "code": f"{code_index + 1:06d}",
                "account_id": "zz500",
                "research_scope": "zz500",
                "horizon": 20,
                "signal": signal,
                "regime": regime,
                "noise": rng.normal(),
                "excess_return": nonlinear * 0.01 + rng.normal(scale=0.001),
                "industry": "科技" if code_index % 2 else "制造",
                "log_total_mv": 8.0 + code_index / 30.0,
                "realized_volatility_20": 0.15 + (code_index % 5) * 0.01,
                "momentum_20_cs_rank": signal / 2.0,
                "account_low_volatility_percentile": 1.0 - (code_index + 1) / 30.0,
                "account_quality_percentile": (code_index + 1) / 30.0,
                "benchmark_weight": 1.0 / 30.0,
                "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                "entry_price": 10.0 + code_index * 0.1 + date_index * 0.001,
                "benchmark_entry_price": 100.0 + date_index * 0.01,
                "avg_amount_20": 100_000_000.0,
            })
    return pd.DataFrame(rows)


class ResearchTabularRankerTest(unittest.TestCase):
    def test_production_config_is_research_only_and_single_hypothesis(self):
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )

        self.assertTrue(config["research_only"])
        self.assertFalse(config["formal_order_source"])
        self.assertEqual(config["market"], "a_share")
        self.assertEqual(config["account_scope"], "zz500")
        self.assertEqual(config["horizon"], 20)
        self.assertEqual(config["model"]["estimator"], "lightgbm_regression")
        self.assertEqual(
            config["model"]["target"],
            "residualized_cross_sectional_rank_v1",
        )
        self.assertEqual(config["fallback"]["estimator"], "lightgbm_lambdarank")
        self.assertTrue(config["fallback"]["enabled"])
        self.assertEqual(config["training"]["n_splits"], 3)
        self.assertNotIn("calibration", config)
        self.assertEqual(
            config["portfolio"].get("replay_contract", "rule"),
            "rule",
        )
        self.assertIn("config_hash", config)

    def test_config_rejects_formal_order_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                "research_only: true\nformal_order_source: true\n"
                "market: a_share\naccount_scope: zz500\nhorizon: 20\n"
                "development: {start: '20200101', end: '20250101'}\n"
                "training: {n_splits: 3, training_window_sessions: 1008, "
                "calibration_fraction: 0.15, embargo_sessions: 20, "
                "recency_half_life_sessions: 504, max_fit_rows: 10000}\n"
                "model: {estimator: lightgbm_regression, parameters: {}}\n"
                "gates: {}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "tabular_config_formal_order_source"):
                load_tabular_ranker_config(path)

    def test_rolling_splits_bound_training_history_and_purge_labels(self):
        frame = _panel()

        splits = make_rolling_purged_splits(
            frame,
            n_splits=3,
            training_window_sessions=120,
            calibration_fraction=0.20,
            embargo_sessions=20,
        )

        self.assertEqual(len(splits), 3)
        for split in splits:
            train = frame.loc[split.train_indices]
            calibration = frame.loc[split.calibration_indices]
            validation = frame.loc[split.validation_indices]
            self.assertLessEqual(
                train["trade_date"].nunique() + calibration["trade_date"].nunique(),
                120,
            )
            self.assertLess(
                str(train["label_end_date"].max()),
                str(calibration["trade_date"].min()),
            )
            self.assertLess(
                str(calibration["label_end_date"].max()),
                str(validation["trade_date"].min()),
            )

    def test_recency_weights_keep_dates_balanced_and_favor_recent_history(self):
        frame = pd.DataFrame({
            "trade_date": ["20260102"] * 2 + ["20260103"] * 4 + ["20260104"] * 3,
        })

        weights = recency_date_balanced_weights(frame, half_life_sessions=1)
        totals = weights.groupby(frame["trade_date"]).sum()

        self.assertAlmostEqual(float(totals.loc["20260103"] / totals.loc["20260102"]), 2.0)
        self.assertAlmostEqual(float(totals.loc["20260104"] / totals.loc["20260103"]), 2.0)
        self.assertAlmostEqual(float(weights.mean()), 1.0)

    def test_residual_target_removes_core_size_and_industry_exposure(self):
        rows = []
        for day in ("20260102", "20260105"):
            for index in range(20):
                core = index / 19.0
                industry = "科技" if index % 2 else "制造"
                rows.append({
                    "trade_date": day,
                    "code": f"{index + 1:06d}",
                    "excess_return": (
                        0.08 * core
                        + (0.02 if industry == "科技" else -0.02)
                        + 0.01 * np.sin(index)
                    ),
                    "account_low_volatility_percentile": core,
                    "log_total_mv": 8.0 + (index % 5) * 0.2,
                    "industry": industry,
                })
        frame = pd.DataFrame(rows)

        target = pd.Series(
            _model_target_values(
                frame,
                target="residualized_cross_sectional_rank_v1",
            ),
            index=frame.index,
        )

        for _, group in frame.assign(target=target).groupby("trade_date"):
            self.assertAlmostEqual(
                float(group["target"].corr(group["account_low_volatility_percentile"])),
                0.0,
                places=10,
            )
            industry_means = group.groupby("industry")["target"].mean()
            self.assertTrue(np.allclose(industry_means.to_numpy(), 0.0, atol=1e-10))

    def test_lightgbm_produces_purged_oos_predictions_and_nonlinear_importance(self):
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )
        config["model"]["target"] = "daily_cross_sectional_percentile_v1"
        config["training"].update({
            "n_splits": 2,
            "training_window_sessions": 180,
            "calibration_fraction": 0.20,
            "embargo_sessions": 20,
            "max_fit_rows": 100_000,
        })
        config["model"]["parameters"].update({
            "n_estimators": 120,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "max_depth": 5,
            "min_child_samples": 20,
            "early_stopping_rounds": 15,
            "num_threads": 2,
        })
        config["calibration"] = {
            "enabled": True,
            "method": "date_bucket_isotonic_v1",
            "minimum_dates": 20,
            "bins": 5,
            "uncertainty_multiple": 1.0,
        }

        fitted = fit_walk_forward_tabular_ranker(
            _panel(),
            feature_columns=("signal", "regime", "noise"),
            config=config,
        )

        self.assertEqual(fitted.evaluation["fold"].nunique(), 2)
        self.assertTrue(fitted.point_in_time_audit)
        self.assertGreater(fitted.raw_rank_ic, 0.20)
        self.assertEqual(fitted.estimator, "lightgbm_regression")
        self.assertGreater(fitted.feature_importance["signal"], 0.0)
        self.assertTrue((fitted.evaluation["score"].between(-0.5, 0.5)).all())
        self.assertEqual(len(fitted.calibrations), 2)
        self.assertTrue(
            np.isfinite(fitted.evaluation["expected_excess_return"]).all()
        )
        self.assertTrue(
            fitted.evaluation["prediction_uncertainty_bps"].ge(0.0).all()
        )
        self.assertTrue(
            fitted.evaluation["prediction_confidence"].between(0.0, 1.0).all()
        )
        self.assertTrue(fitted.evaluation["prediction_applied"].all())
        self.assertTrue(
            fitted.evaluation["prediction_model_versions"].astype(str).str.len().gt(0).all()
        )
        self.assertTrue(
            fitted.evaluation["prediction_feature_schema_hash"].astype(str).str.len().gt(0).all()
        )
        self.assertTrue(
            fitted.evaluation["prediction_calibrator_hash"].astype(str).str.len().gt(0).all()
        )
        self.assertTrue(all(row["calibrator_hash"] for row in fitted.calibrations))
        self.assertTrue(all(
            row["effective_date_count"] > 0.0
            for row in fitted.calibrations
        ))

    def test_lambdarank_fallback_uses_same_purged_evidence_contract(self):
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )
        config["model"]["target"] = "daily_cross_sectional_percentile_v1"
        config["training"].update({
            "n_splits": 2,
            "training_window_sessions": 180,
            "calibration_fraction": 0.20,
            "embargo_sessions": 20,
            "max_fit_rows": 100_000,
        })
        config["fallback"]["parameters"].update({
            "n_estimators": 100,
            "min_child_samples": 20,
            "early_stopping_rounds": 10,
            "num_threads": 2,
        })
        config["calibration"] = {
            "enabled": True,
            "method": "date_bucket_isotonic_v1",
            "minimum_dates": 20,
            "bins": 5,
            "uncertainty_multiple": 1.0,
        }

        fitted = fit_walk_forward_tabular_ranker(
            _panel(),
            feature_columns=("signal", "regime", "noise"),
            config=config,
            estimator="lightgbm_lambdarank",
        )

        self.assertEqual(fitted.estimator, "lightgbm_lambdarank")
        self.assertEqual(fitted.evaluation["fold"].nunique(), 2)
        self.assertTrue(fitted.point_in_time_audit)
        self.assertGreater(fitted.raw_rank_ic, 0.15)

    def test_lambdarank_fallback_only_runs_for_positive_broad_ic_and_failed_top_tail(self):
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )
        positive = SimpleNamespace(raw_rank_ic=0.01)
        negative = SimpleNamespace(raw_rank_ic=-0.01)

        self.assertTrue(_should_run_lambdarank_fallback(
            positive,
            {"passed": False, "reasons": ["top_tail"]},
            config,
        ))
        self.assertFalse(_should_run_lambdarank_fallback(
            negative,
            {"passed": False, "reasons": ["top_tail"]},
            config,
        ))
        self.assertFalse(_should_run_lambdarank_fallback(
            positive,
            {"passed": False, "reasons": ["icir"]},
            config,
        ))

    def test_fallback_cannot_replace_primary_with_equal_gate_count_and_lower_excess(self):
        primary = {
            "metrics": {
                "net_excess_return": 0.06,
                "active_max_drawdown": 0.14,
                "max_drawdown": 0.19,
                "information_ratio": 0.39,
                "rank_ic": 0.09,
            },
            "development_gate": {
                "passed": False,
                "checks": {"rank_ic": True, "top_tail": False},
            },
        }
        fallback = {
            "metrics": {
                "net_excess_return": 0.04,
                "active_max_drawdown": 0.15,
                "max_drawdown": 0.22,
                "information_ratio": 0.25,
                "rank_ic": 0.09,
            },
            "development_gate": {
                "passed": False,
                "checks": {"rank_ic": True, "top_tail": False},
            },
        }

        selected = _select_candidate_evidence(primary, fallback)

        self.assertIs(selected, primary)

    def test_score_construction_keeps_low_volatility_core_and_bounded_model_tilt(self):
        frame = pd.DataFrame({
            "trade_date": ["20260102"] * 5,
            "model_score": [5.0, 4.0, 3.0, 2.0, 1.0],
            "account_low_volatility_percentile": [0.1, 0.3, 0.5, 0.7, 0.9],
        })
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )

        score = _construct_candidate_score(frame, config=config)

        self.assertLess(float(score.iloc[0]), float(score.iloc[-1]))
        self.assertTrue(score.between(-0.5, 0.5).all())
        self.assertAlmostEqual(
            config["score_construction"]["model_weight"],
            0.20,
        )

    def test_score_construction_penalizes_missing_core_risk_history(self):
        frame = pd.DataFrame({
            "trade_date": ["20260102"] * 4,
            "model_score": [4.0, 3.0, 2.0, 1.0],
            "account_low_volatility_percentile": [np.nan, 0.3, 0.6, 0.9],
        })
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )

        score = _construct_candidate_score(frame, config=config)

        self.assertTrue(score.notna().all())
        self.assertEqual(float(score.iloc[0]), float(score.min()))

    def test_gate_requires_stability_top_tail_economics_and_governance(self):
        metrics = {
            "rank_ic": 0.05,
            "icir": 0.42,
            "net_excess_return": 0.04,
            "active_max_drawdown": 0.08,
            "max_drawdown": 0.15,
            "annual_turnover": 5.0,
            "capital_utilization": 0.95,
            "point_in_time_audit": True,
            "deflated_sharpe_probability": 0.97,
            "probability_of_backtest_overfit": 0.30,
        }
        folds = [
            {"rank_ic": 0.04, "net_excess_return": 0.02},
            {"rank_ic": 0.03, "net_excess_return": 0.01},
            {"rank_ic": -0.01, "net_excess_return": -0.01},
        ]
        buckets = [
            {"bucket": 1, "mean_excess_return": -0.01},
            {"bucket": 2, "mean_excess_return": -0.005},
            {"bucket": 3, "mean_excess_return": 0.0},
            {"bucket": 4, "mean_excess_return": 0.005},
            {"bucket": 5, "mean_excess_return": 0.01},
        ]
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )

        passed = evaluate_tabular_development_gate(
            metrics,
            folds=folds,
            buckets=buckets,
            thresholds=config["gates"],
        )
        inverted = evaluate_tabular_development_gate(
            metrics,
            folds=folds,
            buckets=[*buckets[:-1], {"bucket": 5, "mean_excess_return": -0.02}],
            thresholds=config["gates"],
        )

        self.assertTrue(passed["passed"])
        self.assertFalse(inverted["passed"])
        self.assertIn("top_tail", inverted["reasons"])

    def test_candidate_evaluator_writes_bounded_research_artifacts_without_registry_mutation(self):
        config = load_tabular_ranker_config(
            Path("configs/research/classical_model.yaml")
        )
        config["training"].update({
            "n_splits": 2,
            "training_window_sessions": 180,
            "calibration_fraction": 0.20,
            "embargo_sessions": 20,
            "max_fit_rows": 100_000,
        })
        config["model"]["parameters"].update({
            "n_estimators": 80,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "max_depth": 5,
            "min_child_samples": 20,
            "early_stopping_rounds": 10,
            "num_threads": 2,
        })
        config["calibration"] = {
            "enabled": True,
            "method": "date_bucket_isotonic_v1",
            "minimum_dates": 20,
            "bins": 5,
            "uncertainty_multiple": 1.0,
        }
        config["portfolio"]["replay_contract"] = "model"
        contract = {
            "accounts": [{"id": "zz500", "cash": 500_000.0, "top_n": 5}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.20,
            },
            "rebalance_frequency": "monthly",
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_regime_tabular_candidate(
                tmp,
                dataset=_panel(),
                feature_columns=("signal", "regime", "noise"),
                config=config,
                portfolio_contract=contract,
                as_of="20260807",
            )
            report_path = Path(result["report_path"])
            json_path = Path(result["json_path"])
            immutable_report_path = Path(result["immutable_report_path"])
            immutable_json_path = Path(result["immutable_json_path"])
            best_report_path = Path(result["best_report_path"])
            best_json_path = Path(result["best_json_path"])
            self.assertTrue(report_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(immutable_report_path.exists())
            self.assertTrue(immutable_json_path.exists())
            self.assertTrue(best_report_path.exists())
            self.assertTrue(best_json_path.exists())
            self.assertTrue(result["best_candidate_updated"])
            self.assertIn(result["config_hash"], immutable_json_path.name)
            payload = json_path.read_text(encoding="utf-8")

        self.assertFalse(result["formal_order_source"])
        self.assertFalse(result["registry_mutated"])
        self.assertIn(result["status"], {"research", "development_pass"})
        self.assertIn("folds", result)
        self.assertEqual(len(result["calibrations"]), 2)
        self.assertEqual(
            result["calibration_diagnostics"]["fold_count"],
            2,
        )
        self.assertEqual(
            result["calibration_diagnostics"][
                "economic_prediction_coverage"
            ],
            1.0,
        )
        self.assertGreaterEqual(
            result["calibration_diagnostics"]["uncertainty_bps_p90"],
            result["calibration_diagnostics"]["uncertainty_bps_p50"],
        )
        self.assertIn(
            "no_trade_reasons",
            result["calibration_diagnostics"],
        )
        self.assertIn("score_buckets", result)
        self.assertEqual(
            result["research_config"]["score_construction"]["model_weight"],
            0.20,
        )
        self.assertEqual(
            result["research_config"]["portfolio"]["rebalance_frequency"],
            "monthly",
        )
        self.assertNotIn("portfolio_nav", payload)
        self.assertLess(len(payload), 150_000)


if __name__ == "__main__":
    unittest.main()
