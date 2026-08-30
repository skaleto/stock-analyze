from __future__ import annotations

import math
import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.metrics import (
    calculate_metrics,
    rolling_series,
)


class PermanentPortfolioMetricsTests(unittest.TestCase):
    def test_metrics_include_return_volatility_drawdown_and_costs(self) -> None:
        nav = pd.DataFrame(
            {
                "date": ["2020-01-02", "2020-01-03", "2020-01-06"],
                "total_value": [200000.0, 202000.0, 199000.0],
                "cash_benchmark_value": [200000.0, 200020.0, 200040.0],
            }
        )

        metrics = calculate_metrics(
            nav,
            total_turnover=40000.0,
            total_cost=45.0,
            trade_count=2,
        )

        self.assertIn("annualized_return", metrics)
        self.assertIn("annualized_volatility", metrics)
        self.assertIn("max_drawdown", metrics)
        self.assertIn("sharpe_vs_cash", metrics)
        self.assertEqual(metrics["total_cost"], 45.0)
        self.assertEqual(metrics["trade_count"], 2)
        self.assertTrue(
            all(
                value is None
                or isinstance(value, int)
                or math.isfinite(float(value))
                for value in metrics.values()
            )
        )

    def test_flat_series_returns_none_for_undefined_ratios(self) -> None:
        nav = pd.DataFrame(
            {
                "date": ["2020-01-02", "2020-01-03"],
                "total_value": [200000.0, 200000.0],
                "cash_benchmark_value": [200000.0, 200000.0],
            }
        )

        metrics = calculate_metrics(
            nav,
            total_turnover=0.0,
            total_cost=0.0,
            trade_count=0,
        )

        self.assertIsNone(metrics["sharpe_vs_cash"])
        self.assertIsNone(metrics["sortino_vs_cash"])
        self.assertIsNone(metrics["calmar"])

    def test_rolling_series_contains_drawdown_and_63_day_volatility(self) -> None:
        values = [100.0 + index for index in range(70)]
        nav = pd.DataFrame(
            {
                "date": pd.date_range("2020-01-02", periods=70, freq="B").strftime("%Y-%m-%d"),
                "total_value": values,
            }
        )

        result = rolling_series(nav)

        self.assertEqual(len(result), 70)
        self.assertIn("drawdown", result.columns)
        self.assertIn("volatility_63d", result.columns)
        self.assertFalse(pd.isna(result.iloc[-1]["volatility_63d"]))


if __name__ == "__main__":
    unittest.main()
