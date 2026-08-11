import unittest

import numpy as np
import pandas as pd

from stock_analyze.execution_costs import calculate_execution_fill
from stock_analyze.markets.a_share.data_provider import ExecutionQuote
from stock_analyze.markets.a_share.simulator import execute_order
from stock_analyze.research.portfolio_replay import replay_executable_portfolio


class FixedQuoteProvider:
    def __init__(self, price: float) -> None:
        self.price = price

    def execution_quote(self, code, execute_after, side, as_of=None):
        return ExecutionQuote(
            code=code,
            trade_date=as_of or execute_after,
            price=self.price,
        )


def replay_rows(*, account_id: str = "hs300", winners: bool = True) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=8, freq="B")
    rows = []
    for day_index, signal_date in enumerate(dates[:-1]):
        entry_date = dates[day_index + 1]
        benchmark_open = 100.0 + day_index
        for code_index in range(4):
            entry = 10.0 + code_index
            next_entry = entry * (1.02 if code_index == 0 and winners else 0.99)
            rows.append({
                "account_id": account_id,
                "trade_date": signal_date.strftime("%Y%m%d"),
                "entry_date": entry_date.strftime("%Y%m%d"),
                "code": f"{code_index + 1:06d}",
                "score": 4.0 - code_index,
                "entry_price": entry if day_index == 0 else (
                    (10.0 + code_index) * ((1.02 if code_index == 0 and winners else 0.99) ** day_index)
                ),
                "benchmark_entry_price": benchmark_open,
                "avg_amount_20": 50_000_000.0,
                "realized_volatility_20": 0.20,
                "fold": 0,
            })
    return pd.DataFrame(rows)


class ResearchPortfolioReplayTest(unittest.TestCase):
    def test_shared_fill_math_matches_a_share_paper_execution(self):
        trading = {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.0005,
        }
        expected = calculate_execution_fill(
            reference_price=10.0,
            shares=1_000,
            side="buy",
            trading=trading,
            impact_bps=5.0,
        )
        account = {"cash": 100_000.0, "positions": {}}
        order = {
            "code": "000001",
            "name": "测试",
            "side": "buy",
            "target_shares": 1_000,
            "estimated_impact_bps": 5.0,
        }

        trade = execute_order(
            {"trading": trading},
            account,
            order,
            FixedQuoteProvider(10.0),
            "2026-01-05",
            "hs300",
            "2026-01-05",
        )

        self.assertIsNotNone(trade)
        self.assertEqual(trade["price"], expected.execution_price)
        self.assertEqual(trade["commission"], round(expected.commission, 2))
        self.assertEqual(trade["slippage"], round(expected.slippage, 2))
        self.assertAlmostEqual(account["cash"], 100_000.0 + expected.cash_delta)

    def test_replay_uses_account_top_n_lots_and_full_cost_breakdown(self):
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.40,
            },
        }

        result = replay_executable_portfolio(replay_rows(), contract=contract)

        self.assertEqual(result.metrics["simulator_version"], "paper-parity-daily-v1")
        self.assertGreater(result.metrics["trade_count"], 0)
        self.assertGreater(result.metrics["total_commission"], 0.0)
        self.assertGreater(result.metrics["total_slippage"], 0.0)
        self.assertIn("impact_bps_p50", result.metrics)
        self.assertIn("impact_bps_p90", result.metrics)
        self.assertEqual(result.metrics["missing_liquidity_notional_ratio"], 0.0)
        self.assertEqual(result.metrics["impact_capped_notional_ratio"], 0.0)
        self.assertEqual(result.metrics["execution_evidence_status"], "available")
        self.assertTrue({
            "avg_daily_amount",
            "participation_rate",
            "liquidity_status",
            "impact_capped",
        }.issubset(result.trades.columns))
        self.assertIn("hs300", result.metrics["account_metrics"])
        self.assertTrue((result.trades["shares"] % 100).eq(0).all())

    def test_account_failure_cannot_be_hidden_by_pooled_result(self):
        profitable = replay_rows(account_id="hs300", winners=True)
        losing = replay_rows(account_id="zz500", winners=False)
        contract = {
            "accounts": [
                {"id": "hs300", "cash": 100_000.0, "top_n": 1},
                {"id": "zz500", "cash": 100_000.0, "top_n": 1},
            ],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 1.0,
            },
        }

        result = replay_executable_portfolio(
            pd.concat([profitable, losing], ignore_index=True),
            contract=contract,
        )

        self.assertGreater(result.metrics["account_metrics"]["hs300"]["net_return"], 0.0)
        self.assertLess(result.metrics["account_metrics"]["zz500"]["net_return"], 0.0)
        self.assertFalse(result.metrics["all_accounts_profitable"])
        self.assertFalse(result.metrics["all_accounts_positive_active"])

    def test_cost_aware_replay_records_no_trade_and_reduces_churn(self):
        evaluation = replay_rows().assign(
            expected_excess_return=0.001,
            prediction_uncertainty_bps=20.0,
        )
        base_contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.40,
            },
        }
        legacy = replay_executable_portfolio(evaluation, contract=base_contract)
        cost_aware = replay_executable_portfolio(
            evaluation,
            contract={
                **base_contract,
                "execution_policy": {
                    "version": "cost-aware-aim-v1",
                    "rank_buffer_pct": 0.50,
                    "minimum_target_change": 0.01,
                    "partial_adjustment_rate": 0.35,
                    "max_daily_turnover": 0.10,
                    "cost_safety_multiple": 1.50,
                    "alpha_persistence": 1.0,
                },
            },
        )

        self.assertLess(cost_aware.metrics["trade_count"], legacy.metrics["trade_count"])
        self.assertLess(cost_aware.metrics["annual_turnover"], legacy.metrics["annual_turnover"])
        self.assertGreater(cost_aware.metrics["no_trade_count"], 0)
        self.assertEqual(
            cost_aware.metrics["execution_policy_version"],
            "cost-aware-aim-v1",
        )
        self.assertIn("insufficient_net_edge", cost_aware.metrics["no_trade_reason_counts"])
        self.assertTrue({
            "gross_expected_edge_bps",
            "round_trip_cost_bps",
            "uncertainty_bps",
            "net_expected_edge_bps",
            "trade_allowed",
            "no_trade_reason",
            "partial_adjustment_rate",
        }.issubset(cost_aware.decisions.columns))


if __name__ == "__main__":
    unittest.main()
