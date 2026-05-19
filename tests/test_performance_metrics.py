from __future__ import annotations

import math
import unittest

import pandas as pd

from stock_analyze.performance import compute_account_performance


def make_nav(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date, "account_id": "acc", "total_value": value, "benchmark_close": bench}
            for date, value, bench in rows
        ]
    )


def make_trades(rows: list[dict]) -> pd.DataFrame:
    base = {"account_id": "acc", "code": "000001", "name": "T", "side": "buy", "shares": 100, "price": 10.0,
            "gross_amount": 1000.0, "commission": 1.0, "stamp_tax": 0.0, "slippage": 0.0, "net_amount": 1001.0,
            "cash_after": 0.0, "reason": ""}
    return pd.DataFrame([{**base, **row} for row in rows])


class PerformanceMetricTests(unittest.TestCase):
    def test_sharpe_uses_configured_risk_free_rate(self) -> None:
        nav = make_nav([("2026-01-01", 100.0, 1.0), ("2026-01-02", 101.0, 1.0), ("2026-01-03", 102.0, 1.0)])
        result = compute_account_performance(nav, pd.DataFrame(), risk_free_rate=0.0)
        ann_ret_zero_rf = result["acc"]["annualized_return"]
        result_with_rf = compute_account_performance(nav, pd.DataFrame(), risk_free_rate=0.5)
        # With higher rf, Sharpe should be lower
        sharpe_zero = result["acc"]["sharpe_ratio"]
        sharpe_high = result_with_rf["acc"]["sharpe_ratio"]
        self.assertGreater(sharpe_zero, sharpe_high)

    def test_information_ratio_definition(self) -> None:
        nav = make_nav(
            [("2026-01-01", 100.0, 100.0), ("2026-01-02", 102.0, 100.5),
             ("2026-01-03", 103.0, 101.0), ("2026-01-04", 104.5, 101.5)]
        )
        result = compute_account_performance(nav, pd.DataFrame(), risk_free_rate=0.0)
        item = result["acc"]
        self.assertIsNotNone(item["information_ratio"])
        # information ratio should be positive when account outpaces benchmark
        self.assertGreater(item["information_ratio"], 0)

    def test_insufficient_history_returns_none(self) -> None:
        nav = make_nav([("2026-01-01", 100.0, 1.0)])
        result = compute_account_performance(nav, pd.DataFrame())
        self.assertIsNone(result["acc"]["annualized_volatility"])
        self.assertIsNone(result["acc"]["sharpe_ratio"])

    def test_cost_bps(self) -> None:
        trades = make_trades(
            [
                {"trade_date": "2026-01-02", "side": "buy", "shares": 100, "price": 10, "gross_amount": 1000, "commission": 1.0},
                {"trade_date": "2026-01-09", "side": "sell", "shares": 100, "price": 11, "gross_amount": 1100, "commission": 1.0, "stamp_tax": 0.55},
            ]
        )
        nav = make_nav(
            [("2026-01-01", 100.0, 1.0), ("2026-01-02", 100.0, 1.0), ("2026-01-09", 110.0, 1.0)]
        )
        result = compute_account_performance(nav, trades)
        self.assertIsNotNone(result["acc"]["cost_bps"])
        self.assertAlmostEqual(
            float(result["acc"]["cost_bps"]),
            (1.0 + 1.0 + 0.55) / (1000 + 1100) * 10000,
            places=2,
        )

    def test_round_trip_win_rate(self) -> None:
        trades = make_trades(
            [
                {"trade_date": "2026-01-02", "side": "buy", "shares": 100, "price": 10, "gross_amount": 1000, "commission": 1.0},
                {"trade_date": "2026-01-09", "side": "sell", "shares": 100, "price": 12, "gross_amount": 1200, "commission": 1.0, "stamp_tax": 0.6},
                {"trade_date": "2026-01-12", "side": "buy", "shares": 100, "price": 12, "gross_amount": 1200, "commission": 1.0},
                {"trade_date": "2026-01-19", "side": "sell", "shares": 100, "price": 11, "gross_amount": 1100, "commission": 1.0, "stamp_tax": 0.55},
            ]
        )
        nav = make_nav(
            [
                ("2026-01-01", 100.0, 1.0),
                ("2026-01-09", 120.0, 1.0),
                ("2026-01-19", 110.0, 1.0),
            ]
        )
        result = compute_account_performance(nav, trades)
        self.assertEqual(result["acc"]["round_trip_count"], 2)
        self.assertAlmostEqual(result["acc"]["round_trip_win_rate"], 0.5, places=4)


if __name__ == "__main__":
    unittest.main()
