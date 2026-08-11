import unittest

from stock_analyze.research.execution_policy import estimate_market_impact_bps


class ExecutionPolicyTest(unittest.TestCase):
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
