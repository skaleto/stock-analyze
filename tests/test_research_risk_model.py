import unittest
from unittest import mock

import numpy as np
import pandas as pd

from stock_analyze.research import risk_model as risk_model_module
from stock_analyze.research.risk_model import (
    neutralize_cross_sectional_scores,
    PortfolioLimits,
    PortfolioProblem,
    optimize_portfolio,
)


def _frame(rows):
    return pd.DataFrame(rows).set_index("code", drop=False)


class ResearchRiskModelTest(unittest.TestCase):
    def test_cross_sectional_score_neutralization_removes_size_volatility_and_industry_means(self):
        frame = pd.DataFrame({
            "trade_date": ["20260102"] * 8,
            "code": [f"{index:06d}" for index in range(8)],
            "industry": ["科技"] * 4 + ["银行"] * 4,
            "log_total_mv": np.linspace(8.0, 12.0, 8),
            "realized_volatility_20": np.linspace(0.1, 0.5, 8),
            "score": np.linspace(-1.0, 1.0, 8)
            + np.array([0.4] * 4 + [-0.4] * 4)
            + 0.05 * np.array([1.0, -1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0]),
        })

        result = neutralize_cross_sectional_scores(frame)

        self.assertIn("raw_score", result.columns)
        self.assertIn("neutralized_score", result.columns)
        self.assertLess(
            abs(result["neutralized_score"].corr(result["log_total_mv"])),
            1e-8,
        )
        self.assertLess(
            abs(
                result["neutralized_score"].corr(
                    result["realized_volatility_20"]
                )
            ),
            1e-8,
        )
        industry_means = result.groupby("industry")["neutralized_score"].mean()
        self.assertTrue((industry_means.abs() < 1e-8).all())

    def _problem(
        self,
        candidates,
        covariance,
        exposures,
        *,
        limits=None,
        current=None,
        benchmark=None,
        cost_bps=0.0,
        risk_aversion=1.0,
        active_risk_aversion=0.0,
    ):
        codes = candidates["code"].astype(str).tolist()
        return PortfolioProblem(
            candidates=candidates,
            current_weights=pd.Series(current or {}, dtype=float),
            benchmark_weights=pd.Series(benchmark or {}, dtype=float),
            covariance=pd.DataFrame(covariance, index=codes, columns=codes),
            exposure_matrix=exposures.reindex(codes),
            limits=limits or PortfolioLimits(max_positions=len(codes)),
            cost_bps=cost_bps,
            risk_aversion=risk_aversion,
            active_risk_aversion=active_risk_aversion,
        )

    def test_joint_optimizer_selects_diversifier_outside_raw_alpha_top_n(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.140, "liquidity_cap": 0.70, "industry": "tech"},
                {"code": "B", "alpha": 0.135, "liquidity_cap": 0.70, "industry": "tech"},
                {"code": "C", "alpha": 0.105, "liquidity_cap": 0.70, "industry": "health"},
            ]
        )
        covariance = [
            [0.040, 0.039, 0.000],
            [0.039, 0.040, 0.000],
            [0.000, 0.000, 0.015],
        ]
        exposures = pd.DataFrame(
            {"market_beta": [1.0, 1.0, 0.8]},
            index=["A", "B", "C"],
        )
        limits = PortfolioLimits(
            max_positions=2,
            max_name_weight=0.70,
            required_exposures=("market_beta",),
        )

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=limits,
                risk_aversion=4.0,
            )
        )

        selected = set(solution.weights[solution.weights > 1e-8].index)
        self.assertEqual(selected, {"A", "C"})
        self.assertIsNone(solution.fallback_reason)

    def test_long_only_cash_name_group_turnover_and_liquidity_constraints(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.20, "liquidity_cap": 0.30, "industry": "tech"},
                {"code": "B", "alpha": 0.18, "liquidity_cap": 0.40, "industry": "tech"},
                {"code": "C", "alpha": 0.12, "liquidity_cap": 0.50, "industry": "health"},
            ]
        )
        covariance = np.diag([0.03, 0.04, 0.02])
        exposures = pd.DataFrame(
            {"market_beta": [1.1, 1.0, 0.7]},
            index=["A", "B", "C"],
        )
        limits = PortfolioLimits(
            max_positions=3,
            max_name_weight=0.50,
            max_gross_exposure=1.00,
            min_cash_weight=0.25,
            max_turnover=0.20,
            group_caps={"industry": 0.45},
            required_exposures=("market_beta",),
        )
        current = {"A": 0.20, "C": 0.30}

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=limits,
                current=current,
                cost_bps=pd.Series({"A": 12.0, "B": 20.0, "C": 10.0}),
                risk_aversion=0.5,
            )
        )

        self.assertIsNone(solution.fallback_reason)
        self.assertGreater(float(solution.weights.sum()), 0.0)
        self.assertTrue((solution.weights >= -1e-12).all())
        self.assertLessEqual(int((solution.weights > 1e-8).sum()), 3)
        self.assertLessEqual(float(solution.weights.sum()), 0.75 + 1e-8)
        self.assertGreaterEqual(solution.cash_weight, 0.25 - 1e-8)
        self.assertLessEqual(solution.weights["A"], 0.30 + 1e-8)
        self.assertLessEqual(solution.weights["A"] + solution.weights["B"], 0.45 + 1e-8)
        self.assertLessEqual(solution.turnover, 0.20 + 1e-8)
        self.assertGreaterEqual(solution.expected_cost, 0.0)

    def test_target_gross_exposure_prevents_structural_cash_drag(self):
        candidates = _frame([
            {"code": "A", "alpha": 0.20, "liquidity_cap": 0.50},
            {"code": "B", "alpha": 0.18, "liquidity_cap": 0.50},
            {"code": "C", "alpha": 0.16, "liquidity_cap": 0.50},
        ])
        exposures = pd.DataFrame(
            {"market_beta": [1.0, 1.0, 1.0]},
            index=["A", "B", "C"],
        )

        solution = optimize_portfolio(
            self._problem(
                candidates,
                np.diag([0.09, 0.08, 0.07]),
                exposures,
                limits=PortfolioLimits(
                    max_positions=3,
                    max_name_weight=0.50,
                    target_gross_exposure=0.85,
                    required_exposures=("market_beta",),
                ),
                risk_aversion=4.0,
            )
        )

        self.assertIsNone(solution.fallback_reason)
        self.assertGreaterEqual(float(solution.weights.sum()), 0.85 - 1e-8)
        self.assertLessEqual(float(solution.weights.sum()), 1.0 + 1e-8)
        self.assertAlmostEqual(
            solution.exposures["gross_exposure_shortfall"],
            0.0,
            places=8,
        )

    def test_infeasible_target_gross_exposure_reports_shortfall(self):
        candidates = _frame([
            {"code": "A", "alpha": 0.20, "liquidity_cap": 0.20},
            {"code": "B", "alpha": 0.18, "liquidity_cap": 0.20},
        ])
        exposures = pd.DataFrame(
            {"market_beta": [1.0, 1.0]},
            index=["A", "B"],
        )

        solution = optimize_portfolio(
            self._problem(
                candidates,
                np.diag([0.02, 0.02]),
                exposures,
                limits=PortfolioLimits(
                    max_positions=2,
                    max_name_weight=0.20,
                    target_gross_exposure=0.80,
                    required_exposures=("market_beta",),
                ),
                risk_aversion=0.0,
            )
        )

        self.assertIsNone(solution.fallback_reason)
        self.assertAlmostEqual(float(solution.weights.sum()), 0.40, places=8)
        self.assertAlmostEqual(
            solution.exposures["gross_exposure_shortfall"],
            0.40,
            places=8,
        )

    def test_overlapping_linear_exposure_cap_limits_shared_underlying_company(self):
        candidates = _frame(
            [
                {"code": "ETF-A", "alpha": 0.20, "liquidity_cap": 0.80},
                {"code": "ETF-B", "alpha": 0.18, "liquidity_cap": 0.80},
            ]
        )
        covariance = np.diag([0.01, 0.01])
        exposures = pd.DataFrame(
            {
                "market_beta": [1.0, 1.0],
                "underlying_company:NVDA": [0.25, 0.20],
            },
            index=["ETF-A", "ETF-B"],
        )
        limits = PortfolioLimits(
            max_positions=2,
            max_name_weight=0.80,
            exposure_caps={"underlying_company:NVDA": 0.10},
            required_exposures=("market_beta",),
        )

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=limits,
                risk_aversion=0.0,
            )
        )

        self.assertIsNone(solution.fallback_reason)
        self.assertLessEqual(
            solution.exposures["underlying_company:NVDA"],
            0.10 + 1e-8,
        )
        self.assertIn(
            "exposure:underlying_company:NVDA",
            solution.binding_constraints,
        )

    def test_component_risk_contributions_reconcile_to_volatility(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.12, "liquidity_cap": 0.80},
                {"code": "B", "alpha": 0.10, "liquidity_cap": 0.80},
            ]
        )
        covariance = [[0.04, 0.01], [0.01, 0.09]]
        exposures = pd.DataFrame({"market_beta": [1.0, 0.8]}, index=["A", "B"])

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=PortfolioLimits(
                    max_positions=2,
                    max_name_weight=0.80,
                    required_exposures=("market_beta",),
                ),
            )
        )

        self.assertGreater(solution.volatility, 0.0)
        self.assertAlmostEqual(
            sum(solution.risk_contributions.values()),
            solution.volatility,
            places=9,
        )

    def test_tracking_error_is_benchmark_relative_and_respects_limit(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.30, "liquidity_cap": 1.00},
                {"code": "B", "alpha": 0.01, "liquidity_cap": 1.00},
            ]
        )
        covariance = np.diag([0.04, 0.09])
        exposures = pd.DataFrame({"market_beta": [1.0, 1.0]}, index=["A", "B"])
        limits = PortfolioLimits(
            max_positions=2,
            max_name_weight=1.0,
            max_tracking_error=0.03,
            required_exposures=("market_beta",),
        )

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=limits,
                benchmark={"A": 0.50, "B": 0.50},
                risk_aversion=0.1,
            )
        )

        weights = solution.weights.reindex(["A", "B"]).to_numpy(dtype=float)
        active = weights - np.array([0.50, 0.50])
        expected = float(np.sqrt(active @ covariance @ active))
        self.assertAlmostEqual(solution.tracking_error, expected, places=10)
        self.assertLessEqual(solution.tracking_error, 0.03 + 1e-8)

    def test_market_industry_volatility_fx_and_premium_stresses_are_reproducible(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.12, "liquidity_cap": 0.80, "industry": "tech"},
                {"code": "B", "alpha": 0.10, "liquidity_cap": 0.80, "industry": "health"},
            ]
        )
        covariance = np.diag([0.04, 0.03])
        exposures = pd.DataFrame(
            {
                "market_beta": [1.0, 0.8],
                "volatility_beta": [0.5, 0.2],
                "fx_beta": [1.0, 0.0],
                "premium_beta": [0.2, 0.6],
                "industry:tech": [1.0, 0.0],
                "industry:health": [0.0, 1.0],
            },
            index=["A", "B"],
        )
        limits = PortfolioLimits(
            max_positions=2,
            max_name_weight=0.80,
            required_exposures=(
                "market_beta",
                "volatility_beta",
                "fx_beta",
                "premium_beta",
            ),
        )

        solution = optimize_portfolio(
            self._problem(candidates, covariance, exposures, limits=limits)
        )

        self.assertEqual(
            set(solution.stress_losses),
            {"market", "industry", "volatility", "fx", "premium"},
        )
        self.assertAlmostEqual(
            solution.stress_losses["market"],
            0.20 * solution.exposures["market_beta"],
        )
        self.assertAlmostEqual(
            solution.stress_losses["volatility"],
            0.10 * solution.exposures["volatility_beta"],
        )
        self.assertAlmostEqual(
            solution.stress_losses["fx"],
            0.08 * solution.exposures["fx_beta"],
        )
        self.assertAlmostEqual(
            solution.stress_losses["premium"],
            0.10 * solution.exposures["premium_beta"],
        )
        self.assertAlmostEqual(
            solution.stress_losses["industry"],
            0.15
            * max(
                solution.exposures["industry:tech"],
                solution.exposures["industry:health"],
            ),
        )

    def test_missing_required_exposure_fails_closed_to_cash(self):
        candidates = _frame(
            [{"code": "A", "alpha": 0.20, "liquidity_cap": 1.0}]
        )
        covariance = [[0.04]]
        exposures = pd.DataFrame({"market_beta": [1.0]}, index=["A"])
        limits = PortfolioLimits(
            max_positions=1,
            required_exposures=("market_beta", "fx_beta"),
        )

        solution = optimize_portfolio(
            self._problem(candidates, covariance, exposures, limits=limits)
        )

        self.assertAlmostEqual(float(solution.weights.sum()), 0.0)
        self.assertEqual(solution.cash_weight, 1.0)
        self.assertEqual(solution.fallback_reason, "missing_required_exposures:fx_beta")
        self.assertIn("fail_closed", solution.binding_constraints)

    def test_negative_net_utility_uses_explicit_cash_fallback(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": -0.05, "liquidity_cap": 1.0},
                {"code": "B", "alpha": -0.02, "liquidity_cap": 1.0},
            ]
        )
        covariance = np.diag([0.04, 0.04])
        exposures = pd.DataFrame({"market_beta": [1.0, 1.0]}, index=["A", "B"])

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=PortfolioLimits(
                    max_positions=2,
                    required_exposures=("market_beta",),
                ),
            )
        )

        self.assertAlmostEqual(float(solution.weights.sum()), 0.0)
        self.assertEqual(solution.cash_weight, 1.0)
        self.assertEqual(solution.fallback_reason, "non_positive_net_utility")

    def test_existing_holding_is_not_forced_into_the_new_support(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": -0.10, "liquidity_cap": 1.0},
                {"code": "B", "alpha": 0.20, "liquidity_cap": 1.0},
            ]
        )
        covariance = np.diag([0.04, 0.04])
        exposures = pd.DataFrame({"market_beta": [1.0, 1.0]}, index=["A", "B"])

        solution = optimize_portfolio(
            self._problem(
                candidates,
                covariance,
                exposures,
                limits=PortfolioLimits(
                    max_positions=1,
                    max_turnover=1.0,
                    required_exposures=("market_beta",),
                ),
                current={"A": 0.50},
            )
        )

        self.assertEqual(
            set(solution.weights[solution.weights > 1e-8].index),
            {"B"},
        )
        self.assertLessEqual(solution.turnover, 1.0 + 1e-8)

    def test_optimization_is_deterministic(self):
        candidates = _frame(
            [
                {"code": "C", "alpha": 0.09, "liquidity_cap": 0.50, "industry": "health"},
                {"code": "A", "alpha": 0.12, "liquidity_cap": 0.50, "industry": "tech"},
                {"code": "B", "alpha": 0.11, "liquidity_cap": 0.50, "industry": "tech"},
            ]
        )
        covariance = [
            [0.03, 0.00, 0.00],
            [0.00, 0.04, 0.02],
            [0.00, 0.02, 0.04],
        ]
        exposures = pd.DataFrame(
            {"market_beta": [0.8, 1.0, 1.0]},
            index=["C", "A", "B"],
        )
        problem = self._problem(
            candidates,
            covariance,
            exposures,
            limits=PortfolioLimits(
                max_positions=2,
                max_name_weight=0.60,
                group_caps={"industry": 0.65},
                required_exposures=("market_beta",),
            ),
            cost_bps=15.0,
            risk_aversion=1.5,
        )

        first = optimize_portfolio(problem)
        second = optimize_portfolio(problem)

        pd.testing.assert_series_equal(first.weights, second.weights)
        self.assertEqual(first.cash_weight, second.cash_weight)
        self.assertEqual(first.exposures, second.exposures)
        self.assertEqual(first.risk_contributions, second.risk_contributions)
        self.assertEqual(first.stress_losses, second.stress_losses)
        self.assertEqual(first.binding_constraints, second.binding_constraints)
        self.assertEqual(first.fallback_reason, second.fallback_reason)

    def test_group_constraints_do_not_rescan_pandas_strings_inside_solver(self):
        candidates = _frame(
            [
                {"code": "A", "alpha": 0.12, "liquidity_cap": 0.50, "industry": "tech"},
                {"code": "B", "alpha": 0.11, "liquidity_cap": 0.50, "industry": "tech"},
                {"code": "C", "alpha": 0.10, "liquidity_cap": 0.50, "industry": "health"},
            ]
        )
        exposures = pd.DataFrame(
            {"market_beta": [1.0, 1.0, 0.8]},
            index=["A", "B", "C"],
        )
        problem = self._problem(
            candidates,
            np.diag([0.04, 0.04, 0.03]),
            exposures,
            limits=PortfolioLimits(
                max_positions=2,
                max_name_weight=0.50,
                group_caps={"industry": 0.60},
                required_exposures=("market_beta",),
            ),
        )

        with mock.patch.object(
            pd.Series,
            "eq",
            side_effect=AssertionError("group membership must be precomputed"),
        ):
            solution = optimize_portfolio(problem)

        self.assertIsNone(solution.fallback_reason)
        self.assertLessEqual(
            solution.weights["A"] + solution.weights["B"],
            0.60 + 1e-8,
        )

    def test_large_universe_uses_a_bounded_number_of_support_solves(self):
        codes = [f"S{index:03d}" for index in range(30)]
        candidates = _frame(
            [
                {
                    "code": code,
                    "alpha": 0.20 - index * 0.003,
                    "liquidity_cap": 0.20,
                    "industry": f"group-{index % 5}",
                }
                for index, code in enumerate(codes)
            ]
        )
        exposures = pd.DataFrame({"market_beta": 1.0}, index=codes)
        problem = self._problem(
            candidates,
            np.diag(np.linspace(0.02, 0.08, len(codes))),
            exposures,
            limits=PortfolioLimits(
                max_positions=10,
                max_name_weight=0.20,
                group_caps={"industry": 0.40},
                required_exposures=("market_beta",),
            ),
        )

        with mock.patch.object(
            risk_model_module,
            "_solve_support",
            wraps=risk_model_module._solve_support,
        ) as solve_support:
            solution = optimize_portfolio(problem)

        self.assertIsNone(solution.fallback_reason)
        self.assertLessEqual(int((solution.weights > 1e-8).sum()), 10)
        self.assertLessEqual(solve_support.call_count, 3)


if __name__ == "__main__":
    unittest.main()
