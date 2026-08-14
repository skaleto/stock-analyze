from __future__ import annotations

import unittest

from stock_analyze.research.strategy_viability import evaluate_execution_viability


class ResearchStrategyViabilityTest(unittest.TestCase):
    def test_execution_gate_uses_target_fill_and_cost_stress_not_absolute_turnover(self) -> None:
        report = evaluate_execution_viability(
            {
                "attribution_status": "reconciled",
                "target_fill_ratio": 0.97,
                "missing_liquidity_notional_ratio": 0.03,
                "impact_capped_notional_ratio": 0.04,
                "annual_turnover": 30.0,
                "capital_utilization": 0.50,
            },
            {"net_excess_return": 0.001},
        )

        self.assertTrue(report["passed"])
        self.assertNotIn("annual_turnover", report["checks"])
        self.assertNotIn("capital_utilization", report["checks"])

    def test_execution_gate_reports_each_failed_reason(self) -> None:
        report = evaluate_execution_viability(
            {
                "attribution_status": "mismatch",
                "target_fill_ratio": 0.94,
                "missing_liquidity_notional_ratio": 0.11,
                "impact_capped_notional_ratio": 0.12,
            },
            {"net_excess_return": -0.0001},
        )

        self.assertFalse(report["passed"])
        self.assertEqual(
            report["reasons"],
            [
                "attribution_status",
                "target_fill_ratio",
                "missing_liquidity_notional_ratio",
                "impact_capped_notional_ratio",
                "cost_stress_net_excess_return",
            ],
        )


if __name__ == "__main__":
    unittest.main()
