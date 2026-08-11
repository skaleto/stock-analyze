import unittest

from stock_analyze.research.execution_policy import (
    estimate_execution_cost,
    estimate_market_impact_bps,
)


class ExecutionPolicyTest(unittest.TestCase):
    def test_structured_cost_reports_participation_and_cap_state(self):
        estimate = estimate_execution_cost(
            order_value=50_000,
            avg_daily_amount=500_000_000,
            volatility=0.30,
            baseline_bps=5.0,
        )

        self.assertAlmostEqual(estimate.participation_rate, 0.0001)
        self.assertLess(estimate.total_bps, 25.0)
        self.assertGreater(estimate.impact_bps, 0.0)
        self.assertEqual(estimate.liquidity_status, "available")
        self.assertFalse(estimate.capped)

    def test_structured_cost_marks_missing_liquidity_as_capped(self):
        estimate = estimate_execution_cost(
            order_value=50_000,
            avg_daily_amount=None,
            volatility=0.30,
            baseline_bps=5.0,
            max_bps=80.0,
        )

        self.assertIsNone(estimate.participation_rate)
        self.assertEqual(estimate.total_bps, 80.0)
        self.assertEqual(estimate.liquidity_status, "missing")
        self.assertTrue(estimate.capped)

    def test_market_impact_increases_with_participation_and_volatility(self):
        small = estimate_market_impact_bps(
            order_value=100_000,
            avg_daily_amount=100_000_000,
            volatility=0.15,
            baseline_bps=5.0,
        )
        large = estimate_market_impact_bps(
            order_value=10_000_000,
            avg_daily_amount=100_000_000,
            volatility=0.30,
            baseline_bps=5.0,
        )

        self.assertGreater(large, small)
        self.assertGreaterEqual(small, 5.0)

    def test_missing_liquidity_fails_closed_at_configured_cap(self):
        impact = estimate_market_impact_bps(
            order_value=100_000,
            avg_daily_amount=None,
            volatility=None,
            baseline_bps=5.0,
            max_bps=80.0,
        )

        self.assertEqual(impact, 80.0)


if __name__ == "__main__":
    unittest.main()
