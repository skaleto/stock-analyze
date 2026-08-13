import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.execution_costs import calculate_execution_fill
from stock_analyze.markets.a_share.data_provider import ExecutionQuote
from stock_analyze.markets.a_share.simulator import execute_order
from stock_analyze.research import portfolio_replay
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
    def test_trailing_return_history_is_point_in_time_and_bounded(self):
        rows = []
        for day_index, day in enumerate(pd.date_range("2026-01-02", periods=6, freq="B")):
            for code_index in range(3):
                rows.append({
                    "trade_date": day.strftime("%Y%m%d"),
                    "code": f"{code_index + 1:06d}",
                    "return_1": (day_index + 1) * (code_index + 1) / 1000.0,
                })
        frame = pd.DataFrame(rows)

        history = portfolio_replay._trailing_return_history(
            frame,
            signal_date="20260108",
            lookback_sessions=3,
            minimum_sessions=3,
        )

        self.assertEqual(list(history.index), ["20260106", "20260107", "20260108"])
        self.assertEqual(list(history.columns), ["000001", "000002", "000003"])
        self.assertNotIn("20260109", history.index)

    def test_cumulative_excess_is_relative_wealth(self):
        helper = getattr(portfolio_replay, "cumulative_relative_wealth", None)
        self.assertIsNotNone(helper)

        portfolio = pd.Series([100.0, 110.0, 99.0])
        benchmark = pd.Series([100.0, 105.0, 105.0])

        self.assertAlmostEqual(helper(portfolio, benchmark), 99.0 / 105.0 - 1.0)

    def test_replay_persists_canonical_nav_based_metrics(self):
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.40,
            },
            "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
        }

        result = replay_executable_portfolio(replay_rows(), contract=contract)
        metrics = result.metrics

        self.assertTrue({
            "portfolio_nav",
            "benchmark_nav",
            "portfolio_cagr",
            "benchmark_cagr",
            "cumulative_relative_wealth",
            "annualized_excess_wealth",
            "active_max_drawdown",
        }.issubset(metrics))
        self.assertEqual(metrics["portfolio_nav"][0], 1.0)
        self.assertEqual(metrics["benchmark_nav"][0], 1.0)
        self.assertAlmostEqual(
            metrics["cumulative_relative_wealth"],
            metrics["portfolio_nav"][-1] / metrics["benchmark_nav"][-1] - 1.0,
        )
        self.assertAlmostEqual(
            metrics["net_excess_return"],
            metrics["annualized_excess_wealth"],
        )
        relative_nav = np.asarray(metrics["portfolio_nav"]) / np.asarray(
            metrics["benchmark_nav"]
        )
        expected_active_drawdown = float(
            np.max(1.0 - relative_nav / np.maximum.accumulate(relative_nav))
        )
        self.assertAlmostEqual(metrics["active_max_drawdown"], expected_active_drawdown)

    def test_active_return_attribution_reconciles_cash_selection_and_cost(self):
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 1}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.40,
            },
            "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
        }

        result = replay_executable_portfolio(replay_rows(), contract=contract)
        periods = result.periods
        components = (
            periods["cash_position_effect"]
            + periods["security_selection_return"]
            + periods["execution_cost_effect"]
        )

        np.testing.assert_allclose(
            components,
            periods["active_return"],
            atol=1e-10,
        )
        self.assertLess(
            float(periods["attribution_reconciliation_error"].abs().max()),
            1e-10,
        )
        self.assertEqual(result.metrics["attribution_status"], "reconciled")
        self.assertIn("cash_position_effect_total", result.metrics)
        self.assertIn("security_selection_return_total", result.metrics)
        self.assertIn("execution_cost_effect_total", result.metrics)

    def test_cash_position_effect_changes_sign_with_benchmark_direction(self):
        contract = {
            "accounts": [{
                "id": "hs300",
                "cash": 100_000.0,
                "top_n": 1,
                "cash_reserve_pct": 0.60,
            }],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.40,
            },
        }
        rising = replay_rows().copy()
        falling = rising.copy()
        for frame, direction in ((rising, 1.0), (falling, -1.0)):
            dates = sorted(frame["trade_date"].unique())
            benchmark = {
                day: 100.0 + direction * index
                for index, day in enumerate(dates)
            }
            frame["benchmark_entry_price"] = frame["trade_date"].map(benchmark)

        rising_result = replay_executable_portfolio(rising, contract=contract)
        falling_result = replay_executable_portfolio(falling, contract=contract)

        self.assertLess(rising_result.metrics["cash_position_effect_total"], 0.0)
        self.assertGreater(falling_result.metrics["cash_position_effect_total"], 0.0)

    def test_rule_and_model_replay_have_distinct_economic_contracts(self):
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
            "execution_policy": {
                "version": "cost-aware-aim-v1",
                "minimum_target_change": 0.01,
                "max_daily_turnover": 0.10,
                "cost_safety_multiple": 1.50,
            },
        }
        rule_replay = getattr(portfolio_replay, "replay_rule_portfolio", None)
        model_replay = getattr(portfolio_replay, "replay_model_portfolio", None)
        self.assertIsNotNone(rule_replay)
        self.assertIsNotNone(model_replay)

        rule_result = rule_replay(replay_rows(), contract=contract)
        self.assertGreater(rule_result.metrics["trade_count"], 0)
        self.assertEqual(rule_result.metrics["replay_contract"], "rule")
        with self.assertRaisesRegex(
            ValueError,
            "model_replay_missing_economic_prediction",
        ):
            model_replay(replay_rows(), contract=contract)

        model_frame = replay_rows().assign(
            expected_excess_return=0.001,
            prediction_uncertainty_bps=20.0,
        )
        model_result = model_replay(model_frame, contract=contract)
        self.assertEqual(model_result.metrics["replay_contract"], "model")

    def test_fixed_topn_diagnostic_ignores_deployable_portfolio_controls(self):
        evaluation = replay_rows().copy()
        day_rank = {
            day: index
            for index, day in enumerate(sorted(evaluation["trade_date"].unique()))
        }
        evaluation["score"] = [
            float((int(code) + day_rank[day]) % 4)
            for code, day in zip(evaluation["code"], evaluation["trade_date"])
        ]
        contract = {
            "accounts": [{
                "id": "hs300",
                "cash": 100_000.0,
                "top_n": 2,
                "hold_buffer_pct": 2.0,
            }],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.50,
            },
            "execution_policy": {
                "version": "cost-aware-aim-v1",
                "minimum_target_change": 0.20,
                "max_daily_turnover": 0.01,
            },
            "allocation_policy": {
                "version": "benchmark-aware-topn-v1",
                "max_rebalance_turnover": 0.01,
            },
            "rule_execution_policy": {
                "version": "mechanical-rule-v1",
                "rank_buffer_pct": 2.0,
                "max_daily_turnover": 0.01,
            },
        }

        result = portfolio_replay.replay_fixed_top_n_diagnostic_portfolio(
            evaluation,
            contract=contract,
        )

        self.assertEqual(
            result.metrics["replay_contract"],
            "diagnostic_fixed_topn",
        )
        self.assertEqual(
            result.metrics["execution_policy_version"],
            "fixed-topn-diagnostic-v1",
        )
        for signal_date, decisions in result.decisions.groupby("signal_date"):
            expected = set(
                evaluation.loc[evaluation["trade_date"].eq(signal_date)]
                .sort_values(["score", "code"], ascending=[False, True])
                .head(2)["code"]
            )
            actual = set(
                decisions.loc[decisions["aim_weight"].gt(0.0), "code"]
            )
            self.assertEqual(actual, expected)

    def test_rule_replay_applies_mechanical_band_turnover_and_industry_caps(self):
        evaluation = replay_rows().copy()
        evaluation["industry"] = evaluation["code"].map(
            {"000001": "科技", "000002": "科技", "000003": "消费", "000004": "医药"}
        )
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.50,
            },
            "rule_execution_policy": {
                "version": "mechanical-rule-v1",
                "rank_buffer_pct": 0.50,
                "minimum_target_change": 0.01,
                "max_daily_turnover": 0.10,
                "max_industry_weight": 0.50,
                "industry_column": "industry",
            },
        }

        result = portfolio_replay.replay_rule_portfolio(evaluation, contract=contract)

        self.assertEqual(result.metrics["execution_policy_version"], "mechanical-rule-v1")
        self.assertLessEqual(result.periods["turnover"].max(), 0.101)
        self.assertGreater(result.metrics["decision_count"], 0)
        self.assertTrue({
            "trade_allowed", "no_trade_reason", "current_weight", "target_weight",
        }.issubset(result.decisions.columns))
        first_day_buys = result.trades.loc[
            result.trades["signal_date"].eq(result.trades["signal_date"].min())
            & result.trades["side"].eq("buy")
        ]
        self.assertNotEqual(set(first_day_buys["code"]), {"000001", "000002"})

    def test_ineligible_quote_rows_mark_and_liquidate_existing_positions(self):
        evaluation = pd.DataFrame([
            {
                "account_id": "hs300", "trade_date": "20260102",
                "entry_date": "20260105", "code": "000001", "score": 1.0,
                "entry_price": 10.0, "benchmark_entry_price": 100.0,
                "_eligible_for_selection": True,
            },
            {
                "account_id": "hs300", "trade_date": "20260105",
                "entry_date": "20260106", "code": "000001", "score": -np.inf,
                "entry_price": 9.0, "benchmark_entry_price": 100.0,
                "_eligible_for_selection": False,
            },
            {
                "account_id": "hs300", "trade_date": "20260106",
                "entry_date": "20260107", "code": "000001", "score": -np.inf,
                "entry_price": 9.0, "benchmark_entry_price": 100.0,
                "_eligible_for_selection": False,
            },
        ])
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 1}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 1.0,
            },
            "rule_execution_policy": {
                "version": "mechanical-rule-v1",
                "minimum_target_change": 0.0,
                "max_daily_turnover": 1.0,
                "max_industry_weight": 1.0,
            },
        }

        result = portfolio_replay.replay_rule_portfolio(evaluation, contract=contract)

        sells = result.trades.loc[result.trades["side"].eq("sell")]
        self.assertEqual(sells["signal_date"].tolist(), ["20260105"])
        self.assertLess(float(result.nav.iloc[-1]["nav"]), 100_000.0)

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
        self.assertEqual(result.metrics["attribution_status"], "reconciled")
        np.testing.assert_allclose(
            result.periods["gross_return"] - result.periods["cost_return"],
            result.periods["net_return"],
            atol=1e-12,
        )
        np.testing.assert_allclose(
            result.periods["net_return"] - result.periods["benchmark_return"],
            result.periods["active_return"],
            atol=1e-12,
        )
        self.assertTrue({
            "avg_daily_amount",
            "participation_rate",
            "liquidity_status",
            "impact_capped",
        }.issubset(result.trades.columns))
        self.assertIn("hs300", result.metrics["account_metrics"])
        self.assertTrue((result.trades["shares"] % 100).eq(0).all())

    def test_replay_allocates_residual_lots_and_reports_capital_utilization(self):
        rows = []
        dates = ("20260102", "20260105", "20260106")
        for trade_date in dates:
            for code, price, score in (
                ("000001", 320.0, 2.0),
                ("000002", 330.0, 1.0),
            ):
                rows.append({
                    "account_id": "hs300",
                    "trade_date": trade_date,
                    "entry_date": trade_date,
                    "code": code,
                    "score": score,
                    "entry_price": price,
                    "benchmark_entry_price": 100.0,
                    "avg_amount_20": 100_000_000.0,
                    "realized_volatility_20": 0.10,
                })
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.70,
            },
        }

        result = replay_executable_portfolio(pd.DataFrame(rows), contract=contract)

        self.assertGreaterEqual(result.metrics["capital_utilization"], 0.95)
        self.assertLessEqual(result.metrics["cash_ratio"], 0.05)
        self.assertAlmostEqual(result.metrics["target_risky_exposure"], 1.0)
        self.assertLessEqual(result.metrics["passive_cash_ratio"], 0.05)
        self.assertTrue((result.nav["cash"] >= -1e-8).all())
        self.assertEqual(result.trades.loc[result.trades["side"].eq("buy"), "code"].nunique(), 2)

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

    def test_monthly_rebalance_skips_midmonth_rank_churn(self):
        rows = []
        leaders = {
            "20260129": "000001",
            "20260130": "000002",
            "20260202": "000002",
            "20260203": "000001",
        }
        for trade_date, leader in leaders.items():
            for code in ("000001", "000002"):
                rows.append({
                    "account_id": "hs300",
                    "trade_date": trade_date,
                    "entry_date": trade_date,
                    "code": code,
                    "score": 2.0 if code == leader else 1.0,
                    "expected_excess_return": 0.10 if code == leader else 0.0,
                    "prediction_uncertainty_bps": 0.0,
                    "entry_price": 10.0,
                    "benchmark_entry_price": 100.0,
                })
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 1}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 1.0,
            },
            "rebalance_frequency": "monthly",
            "execution_policy": {
                "rank_buffer_pct": 0.0,
                "minimum_target_change": 0.0,
                "partial_adjustment_rate": 1.0,
                "max_daily_turnover": 2.0,
                "cost_safety_multiple": 0.0,
            },
        }

        result = replay_executable_portfolio(pd.DataFrame(rows), contract=contract)

        self.assertNotIn("20260130", set(result.trades["signal_date"]))
        self.assertEqual(
            set(result.trades.loc[result.trades["signal_date"].eq("20260202"), "code"]),
            {"000001", "000002"},
        )
        self.assertEqual(result.metrics["rebalance_frequency"], "monthly")

    def test_monthly_rebalance_allows_midmonth_hard_risk_exit_without_reentry(self):
        rows = []
        for trade_date in ("20260129", "20260130", "20260202"):
            for code in ("000001", "000002"):
                rows.append({
                    "account_id": "hs300",
                    "trade_date": trade_date,
                    "entry_date": trade_date,
                    "code": code,
                    "score": 2.0 if code == ("000001" if trade_date == "20260129" else "000002") else 1.0,
                    "expected_excess_return": 0.10 if code == ("000001" if trade_date == "20260129" else "000002") else 0.0,
                    "prediction_uncertainty_bps": 0.0,
                    "entry_price": 10.0,
                    "benchmark_entry_price": 100.0,
                    "hard_risk_exit": trade_date == "20260130" and code == "000001",
                })
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 1}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 1.0,
            },
            "rebalance_frequency": "monthly",
            "execution_policy": {
                "rank_buffer_pct": 0.0,
                "minimum_target_change": 0.0,
                "partial_adjustment_rate": 1.0,
                "max_daily_turnover": 2.0,
                "cost_safety_multiple": 0.0,
            },
        }

        result = replay_executable_portfolio(pd.DataFrame(rows), contract=contract)

        midmonth = result.trades.loc[result.trades["signal_date"].eq("20260130")]
        self.assertEqual(midmonth[["code", "side"]].to_records(index=False).tolist(), [("000001", "sell")])
        decision = result.decisions.loc[
            result.decisions["signal_date"].eq("20260130")
            & result.decisions["code"].eq("000001")
        ].iloc[0]
        self.assertEqual(decision["no_trade_reason"], "hard_risk_exit")

    def test_benchmark_aware_allocator_sizes_a_constrained_top_n_portfolio(self):
        frame = replay_rows()
        frame["benchmark_weight"] = frame["code"].map({
            "000001": 0.40,
            "000002": 0.30,
            "000003": 0.20,
            "000004": 0.10,
        })
        frame["industry"] = frame["code"].map({
            "000001": "科技",
            "000002": "科技",
            "000003": "消费",
            "000004": "消费",
        })
        frame["realized_volatility_20"] = frame["code"].map({
            "000001": 0.15,
            "000002": 0.18,
            "000003": 0.22,
            "000004": 0.25,
        })
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.80,
            },
            "rebalance_frequency": "monthly",
            "allocation_policy": {
                "version": "benchmark-aware-topn-v1",
                "group_constraints": {"industry": 0.80},
                "risk_aversion": 1.0,
                "active_risk_aversion": 1.75,
                "cost_aversion": 1.0,
                "max_rebalance_turnover": 1.0,
                "use_point_in_time_covariance": True,
                "covariance_lookback_sessions": 5,
                "covariance_min_history_sessions": 2,
            },
        }

        with patch.object(
            portfolio_replay,
            "risk_adjusted_target_weights",
            wraps=portfolio_replay.risk_adjusted_target_weights,
        ) as allocator:
            result = replay_executable_portfolio(frame, contract=contract)

        initial = result.decisions.loc[
            result.decisions["signal_date"].eq(frame["trade_date"].min())
            & result.decisions["target_weight"].gt(0.0)
        ]
        self.assertEqual(len(initial), 2)
        self.assertEqual(initial["target_weight"].round(8).nunique(), 2)
        self.assertEqual(
            set(initial["allocation_policy_version"]),
            {"benchmark-aware-topn-v1"},
        )
        self.assertTrue(allocator.call_args_list)
        self.assertTrue(all(
            call.kwargs["active_risk_aversion"] == 1.75
            for call in allocator.call_args_list
        ))

    def test_benchmark_allocator_and_cost_gate_are_composed(self):
        frame = replay_rows()
        frame["benchmark_weight"] = frame["code"].map({
            "000001": 0.40,
            "000002": 0.30,
            "000003": 0.20,
            "000004": 0.10,
        })
        frame["industry"] = frame["code"].map({
            "000001": "科技",
            "000002": "科技",
            "000003": "消费",
            "000004": "消费",
        })
        frame["expected_excess_return"] = frame["code"].map({
            "000001": 0.050,
            "000002": 0.035,
            "000003": 0.020,
            "000004": 0.010,
        })
        frame["prediction_uncertainty_bps"] = 5.0
        contract = {
            "accounts": [{"id": "hs300", "cash": 100_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0,
                "min_commission": 0.0,
                "stamp_tax_rate": 0.0,
                "slippage_rate": 0.0,
                "max_single_weight": 0.80,
            },
            "rebalance_frequency": "monthly",
            "allocation_policy": {
                "version": "benchmark-relative-risk-v2",
                "group_constraints": {"industry": 0.80},
                "risk_aversion": 1.0,
                "active_risk_aversion": 1.25,
                "cost_aversion": 1.0,
                "max_rebalance_turnover": 1.0,
                "max_tracking_error": 0.20,
            },
            "execution_policy": {
                "version": "cost-aware-aim-v1",
                "rank_buffer_pct": 0.0,
                "minimum_target_change": 0.0,
                "partial_adjustment_rate": 1.0,
                "max_daily_turnover": 1.0,
                "cost_safety_multiple": 1.0,
                "alpha_persistence": 1.0,
            },
        }

        with patch.object(
            portfolio_replay,
            "risk_adjusted_target_weights",
            wraps=portfolio_replay.risk_adjusted_target_weights,
        ) as allocator:
            result = replay_executable_portfolio(frame, contract=contract)

        self.assertTrue(allocator.call_args_list)
        self.assertTrue(all(
            call.kwargs["max_tracking_error"] == 0.20
            for call in allocator.call_args_list
        ))
        initial = result.decisions.loc[
            result.decisions["signal_date"].eq(frame["trade_date"].min())
            & result.decisions["target_weight"].gt(0.0)
        ]
        self.assertFalse(initial.empty)
        self.assertEqual(
            set(initial["allocation_policy_version"]),
            {"benchmark-relative-risk-v2"},
        )
        self.assertTrue(initial["optimizer_tracking_error"].notna().all())
        self.assertTrue(initial["gross_expected_edge_bps"].gt(0.0).all())
        self.assertTrue(initial["uncertainty_bps"].eq(5.0).all())


if __name__ == "__main__":
    unittest.main()
