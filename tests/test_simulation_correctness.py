from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.data_provider import AkshareProvider, ExecutionQuote
from stock_analyze.simulator import build_target_orders, execute_due_orders
from stock_analyze.store import PortfolioStore


def base_config(cash: float = 100_000) -> dict:
    return {
        "strategy_id": "test_strategy",
        "accounts": [
            {
                "id": "acc",
                "name": "Test Account",
                "scope": "custom:000001",
                "benchmark": "000300",
                "cash": cash,
            }
        ],
        "trading": {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0,
            "max_single_weight": 0.05,
        },
    }


class MissingQuoteProvider:
    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None) -> ExecutionQuote:
        return ExecutionQuote(code=code, trade_date=None, price=None, reason="execution_quote_not_visible")


class FixedQuoteProvider:
    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None) -> ExecutionQuote:
        return ExecutionQuote(code=code, trade_date=as_of or execute_after, price=10.0)


class HistoryProvider(AkshareProvider):
    def __init__(self, history: pd.DataFrame) -> None:
        super().__init__(cache_dir=None)
        self.history = history

    def price_history(self, code: str, as_of: str | None = None, days: int = 180) -> pd.DataFrame:
        return self.history.copy()


class SimulationCorrectnessTests(unittest.TestCase):
    def test_nav_is_upserted_by_date_and_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            store.append_nav(
                [
                    {
                        "date": "2026-05-18",
                        "account_id": "acc",
                        "cash": 100,
                        "market_value": 0,
                        "total_value": 100,
                        "benchmark_code": "000300",
                        "benchmark_close": 1,
                        "benchmark_date": "2026-05-18",
                        "notes": "first",
                    }
                ]
            )
            store.append_nav(
                [
                    {
                        "date": "2026-05-18",
                        "account_id": "acc",
                        "cash": 120,
                        "market_value": 0,
                        "total_value": 120,
                        "benchmark_code": "000300",
                        "benchmark_close": 1,
                        "benchmark_date": "2026-05-18",
                        "notes": "second",
                    }
                ]
            )

            nav = pd.read_csv(Path(tmp) / "daily_nav.csv")
            self.assertEqual(len(nav), 1)
            self.assertEqual(float(nav.iloc[0]["total_value"]), 120)
            self.assertEqual(nav.iloc[0]["notes"], "second")

    def test_missing_execution_quote_retains_pending_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            store.save_pending(
                [
                    {
                        "run_id": "test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-18",
                        "orders": [
                            {
                                "code": "000001",
                                "name": "Test",
                                "side": "buy",
                                "current_shares": 0,
                                "target_shares": 100,
                                "delta_shares": 100,
                                "reference_price": 10,
                                "status": "pending",
                            }
                        ],
                    }
                ]
            )

            trades = execute_due_orders(config, store, MissingQuoteProvider(), as_of="2026-05-18")

            pending = store.load_pending()
            self.assertEqual(trades, [])
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["orders"][0]["status"], "pending")
            self.assertEqual(pending[0]["orders"][0]["unfilled_reason"], "execution_quote_not_visible")
            self.assertEqual(pending[0]["orders"][0]["attempts"], 1)

    def test_same_day_buy_is_not_sellable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = base_config()
            store = PortfolioStore(tmp)
            state = store.initialize(config)
            state["accounts"]["acc"]["positions"]["000001"] = {
                "name": "Test",
                "shares": 100,
                "available_shares": 0,
                "avg_cost": 10,
                "last_buy_date": "2026-05-18",
                "last_price": 10,
                "market_value": 1000,
                "unrealized_pnl": 0,
            }
            store.save_state(state)
            store.save_pending(
                [
                    {
                        "run_id": "test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-18",
                        "orders": [
                            {
                                "code": "000001",
                                "name": "Test",
                                "side": "sell",
                                "current_shares": 100,
                                "target_shares": 0,
                                "delta_shares": -100,
                                "reference_price": 10,
                                "status": "pending",
                            }
                        ],
                    }
                ]
            )

            trades = execute_due_orders(config, store, FixedQuoteProvider(), as_of="2026-05-18")

            pending = store.load_pending()
            state = store.load_state()
            self.assertEqual(trades, [])
            self.assertEqual(state["accounts"]["acc"]["positions"]["000001"]["shares"], 100)
            self.assertEqual(pending[0]["orders"][0]["unfilled_reason"], "no_sellable_shares")

    def test_target_orders_respect_max_single_weight(self) -> None:
        selected = pd.DataFrame(
            [
                {"code": "000001", "name": "A", "latest_price": 10, "score": 1, "score_detail": "pe:1"},
                {"code": "000002", "name": "B", "latest_price": 10, "score": 0.9, "score_detail": "pe:0.9"},
            ]
        )
        account = {"cash": 100_000, "positions": {}}

        orders = build_target_orders(base_config(100_000), account, selected)

        self.assertEqual({order["target_shares"] for order in orders}, {500})
        self.assertTrue(all(order["target_weight"] <= 0.05 for order in orders))

    def test_execution_quote_ignores_future_rows_after_run_date(self) -> None:
        provider = HistoryProvider(
            pd.DataFrame(
                {
                    "日期": ["2026-05-18", "2026-05-20"],
                    "开盘": [10.0, 11.0],
                    "收盘": [10.0, 11.0],
                    "最高": [10.0, 11.0],
                    "最低": [10.0, 11.0],
                    "成交额": [1_000_000, 1_100_000],
                    "停牌": [False, False],
                    "is_st": [False, False],
                    "source": ["test", "test"],
                }
            )
        )

        quote = provider.execution_quote("000001", "2026-05-19", "buy", as_of="2026-05-19")

        self.assertIsNone(quote.price)
        self.assertEqual(quote.reason, "execution_quote_not_visible")

    def test_execution_quote_blocks_limit_up_buy(self) -> None:
        provider = HistoryProvider(
            pd.DataFrame(
                {
                    "日期": ["2026-05-18", "2026-05-19"],
                    "开盘": [10.0, 11.0],
                    "收盘": [10.0, 11.0],
                    "最高": [10.0, 11.0],
                    "最低": [10.0, 11.0],
                    "成交额": [1_000_000, 1_100_000],
                    "停牌": [False, False],
                    "is_st": [False, False],
                    "source": ["test", "test"],
                }
            )
        )

        quote = provider.execution_quote("000001", "2026-05-19", "buy", as_of="2026-05-19")

        self.assertEqual(quote.price, 11.0)
        self.assertTrue(quote.limit_up)
        self.assertEqual(quote.reason, "limit_up_buy_blocked")


if __name__ == "__main__":
    unittest.main()
