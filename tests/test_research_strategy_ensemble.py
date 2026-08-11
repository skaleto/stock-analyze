import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.strategy_ensemble import (
    STRATEGY_PROFILES,
    apply_core_model_tilt,
    apply_cost_aware_transition,
    attach_active_predictions,
    economic_trade_decision,
    risk_adjusted_target_weights,
    validate_strategy_profiles,
)


class ResearchStrategyEnsembleTest(unittest.TestCase):
    def test_core_model_tilt_is_inert_before_active(self):
        result = apply_core_model_tilt(
            {"000001": 0.50, "000002": 0.50},
            {"000001": 0.20, "000002": 0.30, "000003": 0.50},
            model_status="shadow",
            lineage={},
        )

        self.assertEqual(result.weights, {"000001": 0.50, "000002": 0.50})
        self.assertTrue((result.attribution["model_delta"] == 0.0).all())
        self.assertEqual(set(result.attribution["rejection_reason"]), {"model_not_active"})

    def test_active_core_model_tilt_is_zero_sum_and_bounded(self):
        result = apply_core_model_tilt(
            {"000001": 0.50, "000002": 0.50},
            {"000001": 0.20, "000002": 0.30, "000003": 0.50},
            model_status="active",
            lineage={
                "model_version": "model-a20-v1",
                "feature_schema_hash": "feature-hash",
                "calibrator_hash": "calibrator-hash",
            },
            max_tilt=0.20,
        )

        self.assertAlmostEqual(sum(result.weights.values()), 1.0)
        self.assertAlmostEqual(result.attribution["model_delta"].sum(), 0.0)
        self.assertLessEqual(
            result.attribution["model_delta"].clip(lower=0.0).sum(),
            0.20 + 1e-12,
        )
        self.assertEqual(result.weights, {"000001": 0.44, "000002": 0.46, "000003": 0.10})
        self.assertTrue((result.attribution["rejection_reason"] == "").all())

    def test_active_tilt_fails_closed_without_pinned_lineage(self):
        result = apply_core_model_tilt(
            {"000001": 0.50, "000002": 0.50},
            {"000001": 0.20, "000002": 0.80},
            model_status="active",
            lineage={"model_version": "model-a20-v1"},
        )

        self.assertEqual(result.weights, {"000001": 0.50, "000002": 0.50})
        self.assertEqual(
            set(result.attribution["rejection_reason"]),
            {"model_lineage_incomplete"},
        )

    def test_trade_requires_edge_after_cost_and_uncertainty(self):
        decision = economic_trade_decision(
            expected_edge_bps=18.0,
            cost_bps=10.0,
            uncertainty_bps=6.0,
            cost_safety_multiple=1.5,
        )

        self.assertFalse(decision.trade_allowed)
        self.assertEqual(decision.reason, "insufficient_net_edge")

    def setUp(self):
        self.candidates = pd.DataFrame(
            [
                {"code": "000001", "score": 1.0, "low_volatility_60": 0.20},
                {"code": "000002", "score": 0.9, "low_volatility_60": 0.18},
            ]
        )
        self.predictions = pd.DataFrame(
            [
                {"code": "000001", "p_up": 0.30, "p_down": 0.50, "confidence": 0.85, "expected_excess_return": -0.04, "active_status": "active"},
                {"code": "000002", "p_up": 0.78, "p_down": 0.10, "confidence": 0.90, "expected_excess_return": 0.12, "active_status": "active"},
            ]
        )

    def test_defensive_and_trend_profiles_stay_in_design_ranges(self):
        validate_strategy_profiles(STRATEGY_PROFILES)
        self.assertNotEqual(STRATEGY_PROFILES["defensive"].family_weights, STRATEGY_PROFILES["trend"].family_weights)

    def test_cost_aware_transition_retains_existing_holding_inside_rank_buffer(self):
        candidates = pd.DataFrame([
            {
                "code": f"{index:06d}",
                "score": 20 - index,
                "expected_excess_return": 0.03,
                "round_trip_cost_bps": 20.0,
                "prediction_uncertainty_bps": 10.0,
            }
            for index in range(1, 11)
        ])

        result = apply_cost_aware_transition(
            candidates,
            aim_weights={f"{index:06d}": 0.18 for index in range(1, 6)},
            current_weights={"000007": 0.10},
            top_n=5,
            rank_buffer_pct=0.80,
            minimum_target_change=0.02,
            partial_adjustment_rate=0.25,
            max_daily_turnover=0.08,
            cost_safety_multiple=2.0,
        )

        self.assertEqual(result.weights["000007"], 0.10)
        decision = result.decisions.set_index("code").loc["000007"]
        self.assertEqual(decision["no_trade_reason"], "rank_buffer_hold")
        self.assertFalse(bool(decision["trade_allowed"]))

    def test_cost_aware_transition_ignores_qdii_one_percent_target_change(self):
        candidates = pd.DataFrame([{
            "code": "513100",
            "score": 1.0,
            "expected_excess_return": 0.05,
            "round_trip_cost_bps": 20.0,
            "prediction_uncertainty_bps": 10.0,
        }])

        result = apply_cost_aware_transition(
            candidates,
            aim_weights={"513100": 0.21},
            current_weights={"513100": 0.20},
            top_n=1,
            rank_buffer_pct=0.80,
            minimum_target_change=0.02,
            partial_adjustment_rate=0.25,
            max_daily_turnover=0.08,
            cost_safety_multiple=2.0,
        )

        self.assertEqual(result.weights["513100"], 0.20)
        self.assertEqual(
            result.decisions.iloc[0]["no_trade_reason"],
            "target_change_below_band",
        )

    def test_cost_aware_transition_moves_partially_when_edge_covers_cost(self):
        candidates = pd.DataFrame([{
            "code": "513100",
            "score": 1.0,
            "expected_excess_return": 0.03,
            "round_trip_cost_bps": 20.0,
            "prediction_uncertainty_bps": 20.0,
        }])

        result = apply_cost_aware_transition(
            candidates,
            aim_weights={"513100": 0.40},
            current_weights={"513100": 0.20},
            top_n=1,
            rank_buffer_pct=0.80,
            minimum_target_change=0.02,
            partial_adjustment_rate=0.25,
            max_daily_turnover=0.08,
            cost_safety_multiple=2.0,
        )

        self.assertAlmostEqual(result.weights["513100"], 0.25)
        decision = result.decisions.iloc[0]
        self.assertTrue(bool(decision["trade_allowed"]))
        self.assertGreater(decision["net_expected_edge_bps"], 0.0)
        self.assertEqual(decision["partial_adjustment_rate"], 0.25)

    def test_cost_aware_transition_executes_hard_risk_exit_immediately(self):
        candidates = pd.DataFrame([{
            "code": "513100",
            "score": 1.0,
            "expected_excess_return": 0.05,
            "round_trip_cost_bps": 80.0,
            "prediction_uncertainty_bps": 500.0,
            "hard_risk_exit": True,
        }])

        result = apply_cost_aware_transition(
            candidates,
            aim_weights={"513100": 0.40},
            current_weights={"513100": 0.30},
            top_n=1,
            rank_buffer_pct=0.80,
            minimum_target_change=0.02,
            partial_adjustment_rate=0.25,
            max_daily_turnover=0.08,
            cost_safety_multiple=2.0,
        )

        self.assertEqual(result.weights.get("513100", 0.0), 0.0)
        decision = result.decisions.iloc[0]
        self.assertTrue(bool(decision["trade_allowed"]))
        self.assertEqual(decision["no_trade_reason"], "hard_risk_exit")
        self.assertEqual(decision["partial_adjustment_rate"], 1.0)

    def test_only_active_high_confidence_predictions_change_rank(self):
        adjusted = attach_active_predictions(self.candidates, self.predictions, profile="trend")
        self.assertEqual(adjusted.sort_values("score", ascending=False).iloc[0]["code"], "000002")
        self.assertTrue(adjusted["prediction_applied"].all())

        research = self.predictions.assign(active_status="inactive")
        unchanged = attach_active_predictions(self.candidates, research, profile="trend")
        pd.testing.assert_series_equal(unchanged["score"], self.candidates["score"], check_names=False)

    def test_active_prediction_preserves_pinned_lineage(self):
        predictions = self.predictions.assign(
            feature_schema_hash="feature-hash",
            calibrator_hash="calibrator-hash",
            feature_snapshot_id="snapshot-hash",
        )

        result = attach_active_predictions(self.candidates, predictions, profile="trend")

        self.assertEqual(set(result["prediction_feature_schema_hash"]), {"feature-hash"})
        self.assertEqual(set(result["prediction_calibrator_hash"]), {"calibrator-hash"})
        self.assertEqual(set(result["prediction_feature_snapshot_id"]), {"snapshot-hash"})

    def test_low_confidence_and_invalidation_are_inert(self):
        low_confidence = self.predictions.assign(confidence=0.69)
        invalidated = self.predictions.assign(invalidated=True)
        for predictions in (low_confidence, invalidated):
            result = attach_active_predictions(self.candidates, predictions, profile="trend")
            pd.testing.assert_series_equal(result["score"], self.candidates["score"], check_names=False)

    def test_weight_optimizer_falls_back_to_current_top_n(self):
        weights = risk_adjusted_target_weights(
            self.candidates.assign(
                expected_excess_return=[None, None],
                prediction_confidence=[None, None],
                low_volatility_60=[None, None],
            ),
            top_n=2,
            max_single_weight=0.6,
        )
        self.assertEqual(weights, {"000001": 0.5, "000002": 0.5})

    def test_weight_optimizer_uses_bounded_active_model_tilt(self):
        candidates = pd.DataFrame([
            {
                "code": "000001", "base_score": 3.0, "score": 1.0,
                "expected_excess_return": -0.02, "prediction_confidence": 0.90,
                "prediction_applied": True,
            },
            {
                "code": "000002", "base_score": 2.0, "score": 2.0,
                "expected_excess_return": 0.01, "prediction_confidence": 0.90,
                "prediction_applied": True,
            },
            {
                "code": "000003", "base_score": 1.0, "score": 3.0,
                "expected_excess_return": 0.04, "prediction_confidence": 0.90,
                "prediction_applied": True,
            },
        ]).assign(
            prediction_model_versions="a20-v1",
            prediction_feature_schema_hash="feature-hash",
            prediction_calibrator_hash="calibrator-hash",
        )
        core = candidates.assign(
            score=candidates["base_score"],
            prediction_applied=False,
            expected_excess_return=np.nan,
            prediction_confidence=np.nan,
        )
        core_weights = risk_adjusted_target_weights(
            core,
            top_n=3,
            max_single_weight=0.60,
        )
        diagnostics: dict[str, object] = {}

        active_weights = risk_adjusted_target_weights(
            candidates,
            top_n=3,
            max_single_weight=0.60,
            diagnostics=diagnostics,
        )

        attribution = pd.DataFrame(diagnostics["core_model_attribution"])
        self.assertEqual(diagnostics["allocation_contract"], "core-plus-tilt-v1")
        self.assertAlmostEqual(sum(active_weights.values()), sum(core_weights.values()))
        self.assertLessEqual(
            attribution["model_delta"].clip(lower=0.0).sum(),
            0.20 * sum(core_weights.values()) + 1e-12,
        )
        self.assertAlmostEqual(attribution["model_delta"].sum(), 0.0)
        self.assertGreater(attribution["model_delta"].abs().sum(), 0.0)
        self.assertLessEqual(len(active_weights), 3)

    def test_weight_optimizer_uses_volatility_when_predictions_are_absent(self):
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.0, "low_volatility_60": 0.10},
            {"code": "000002", "score": 0.9, "low_volatility_60": 0.30},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
        )

        self.assertGreater(weights["000001"], weights["000002"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertLessEqual(max(weights.values()), 0.80)

    def test_weight_optimizer_blends_toward_current_portfolio(self):
        candidates = pd.DataFrame([
            {
                "code": "000001", "score": 1.0, "low_volatility_60": 0.20,
                "expected_excess_return": 0.12, "prediction_confidence": 0.90,
                "prediction_applied": True,
            },
            {
                "code": "000002", "score": 0.9, "low_volatility_60": 0.20,
                "expected_excess_return": 0.01, "prediction_confidence": 0.75,
                "prediction_applied": True,
            },
        ])
        unconstrained = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
            turnover_penalty=0.0,
        )
        sticky = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
            current_weights={"000001": 0.20, "000002": 0.80},
            turnover_penalty=0.75,
        )

        self.assertLess(abs(sticky["000001"] - 0.20), abs(unconstrained["000001"] - 0.20))
        self.assertAlmostEqual(sum(sticky.values()), 1.0)

    def test_covariance_optimizer_diversifies_correlated_assets(self):
        dates = pd.date_range("2026-01-02", periods=80, freq="B")
        shared = pd.Series(np.sin(np.arange(len(dates)) / 4.0) * 0.02, index=dates)
        returns = pd.DataFrame({
            "000001": shared,
            "000002": shared * 0.98,
            "000003": pd.Series(np.cos(np.arange(len(dates)) / 5.0) * 0.012, index=dates),
        })
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.00, "low_volatility_60": 0.20},
            {"code": "000002", "score": 0.99, "low_volatility_60": 0.20},
            {"code": "000003", "score": 0.95, "low_volatility_60": 0.20},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=3,
            max_single_weight=0.60,
            return_history=returns,
            risk_aversion=1.5,
        )

        self.assertIn("000003", weights)
        self.assertLessEqual(len(weights), 3)
        self.assertGreater(sum(weights.values()), 0.0)
        self.assertLessEqual(sum(weights.values()), 1.0)

    def test_optimizer_estimates_market_beta_from_benchmark_history(self):
        dates = pd.date_range("2026-01-02", periods=80, freq="B")
        benchmark = np.sin(np.arange(len(dates)) / 5.0) * 0.01
        returns = pd.DataFrame({
            "000300": benchmark,
            "000001": benchmark * 2.0,
            "000002": benchmark * 2.5,
        }, index=dates)
        diagnostics: dict[str, object] = {}

        risk_adjusted_target_weights(
            self.candidates,
            top_n=2,
            max_single_weight=0.80,
            return_history=returns,
            benchmark_weights={"000300": 1.0},
            diagnostics=diagnostics,
        )

        self.assertEqual(
            diagnostics["market_beta_source"],
            "estimated_from_benchmark_returns",
        )
        self.assertGreater(
            float(diagnostics["exposures"]["market_beta"]),
            1.5,
        )

    def test_public_allocator_jointly_selects_beyond_raw_score_top_n(self):
        dates = pd.date_range("2026-01-02", periods=80, freq="B")
        shared = np.sin(np.arange(len(dates)) / 4.0) * 0.02
        returns = pd.DataFrame({
            "000001": shared,
            "000002": shared * 0.99,
            "000003": np.cos(np.arange(len(dates)) / 5.0) * 0.008,
        }, index=dates)
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.00, "low_volatility_60": 0.20, "industry": "科技"},
            {"code": "000002", "score": 0.99, "low_volatility_60": 0.20, "industry": "科技"},
            {"code": "000003", "score": 0.92, "low_volatility_60": 0.12, "industry": "医药"},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.70,
            return_history=returns,
            risk_aversion=5.0,
        )

        self.assertEqual(set(weights), {"000001", "000003"})

    def test_optimizer_honors_gross_and_group_caps(self):
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.0, "industry": "科技"},
            {"code": "000002", "score": 0.9, "industry": "科技"},
            {"code": "000003", "score": 0.8, "industry": "消费"},
            {"code": "000004", "score": 0.7, "industry": "医药"},
        ])

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=4,
            max_single_weight=0.35,
            gross_exposure=0.80,
            group_constraints={"industry": 0.35},
        )

        self.assertLessEqual(sum(weights.values()), 0.80)
        self.assertGreater(sum(weights.values()), 0.0)
        self.assertLessEqual(
            weights.get("000001", 0.0) + weights.get("000002", 0.0),
            0.35 + 1e-8,
        )

    def test_optimizer_honors_overlapping_underlying_company_caps(self):
        candidates = pd.DataFrame([
            {
                "code": "ETF-A",
                "score": 1.0,
                "underlying_company:NVDA": 0.25,
            },
            {
                "code": "ETF-B",
                "score": 0.9,
                "underlying_company:NVDA": 0.20,
            },
        ])
        diagnostics: dict[str, object] = {}

        weights = risk_adjusted_target_weights(
            candidates,
            top_n=2,
            max_single_weight=0.80,
            exposure_constraints={"underlying_company:NVDA": 0.10},
            risk_aversion=0.0,
            diagnostics=diagnostics,
        )

        measured = (
            weights.get("ETF-A", 0.0) * 0.25
            + weights.get("ETF-B", 0.0) * 0.20
        )
        self.assertLessEqual(measured, 0.10 + 1e-8)
        self.assertLessEqual(
            diagnostics["exposures"]["underlying_company:NVDA"],
            0.10 + 1e-8,
        )

    def test_explicit_horizon_policy_blends_only_declared_ranker_predictions(self):
        predictions = pd.DataFrame([
            {
                "code": "000001", "as_of": "2026-07-10", "horizon": 5,
                "confidence": 0.90, "expected_excess_return": 0.04,
                "ranker_status": "active", "model_version": "ranker-5",
            },
            {
                "code": "000001", "as_of": "2026-07-10", "horizon": 20,
                "confidence": 0.80, "expected_excess_return": 0.12,
                "ranker_status": "active", "model_version": "ranker-20",
            },
            {
                "code": "000001", "as_of": "2026-07-10", "horizon": 10,
                "confidence": 0.99, "expected_excess_return": -0.50,
                "ranker_status": "active", "model_version": "undeclared-10",
            },
        ])

        adjusted = attach_active_predictions(
            self.candidates.iloc[[0]],
            predictions,
            profile="defensive",
            horizon_weights={5: 0.25, 20: 0.75},
            as_of="2026-07-10",
        )

        self.assertTrue(bool(adjusted.iloc[0]["prediction_applied"]))
        self.assertAlmostEqual(adjusted.iloc[0]["expected_excess_return"], 0.10)
        self.assertEqual(adjusted.iloc[0]["prediction_horizons"], "5,20")
        self.assertEqual(adjusted.iloc[0]["prediction_model_versions"], "ranker-5,ranker-20")

    def test_missing_or_stale_declared_horizon_falls_back_to_rules(self):
        only_one_horizon = pd.DataFrame([
            {
                "code": "000001", "as_of": "2026-07-10", "horizon": 5,
                "confidence": 0.90, "expected_excess_return": 0.10,
                "ranker_status": "active", "model_version": "ranker-5",
            },
        ])
        stale = pd.DataFrame([
            {
                "code": "000001", "as_of": "2026-06-30", "horizon": 5,
                "confidence": 0.90, "expected_excess_return": 0.10,
                "ranker_status": "active", "model_version": "ranker-5",
            },
            {
                "code": "000001", "as_of": "2026-06-30", "horizon": 20,
                "confidence": 0.90, "expected_excess_return": 0.10,
                "ranker_status": "active", "model_version": "ranker-20",
            },
        ])

        for predictions in (only_one_horizon, stale):
            adjusted = attach_active_predictions(
                self.candidates.iloc[[0]],
                predictions,
                profile="defensive",
                horizon_weights={5: 0.4, 20: 0.6},
                as_of="2026-07-10",
                max_prediction_age_days=5,
            )
            self.assertFalse(bool(adjusted.iloc[0]["prediction_applied"]))
            self.assertEqual(adjusted.iloc[0]["score"], self.candidates.iloc[0]["score"])


if __name__ == "__main__":
    unittest.main()
