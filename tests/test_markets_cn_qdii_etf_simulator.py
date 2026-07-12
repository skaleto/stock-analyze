from __future__ import annotations

import json
import unittest
from datetime import date
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze.markets.cn_qdii_etf.data_provider import ETFExecutionQuote, ETFPriceSnapshot
from stock_analyze.markets.cn_qdii_etf.simulator import (
    ETFOrder,
    execute_due_orders,
    generate_rebalance_orders,
    initialize,
    update_nav,
)
from stock_analyze.markets.cn_qdii_etf.run import generate_rebalance_orders as generate_config_orders
from stock_analyze.store import PortfolioStore


class FakeProvider:
    def __init__(self, price: float = 2.0) -> None:
        self.price = price

    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None):
        return ETFExecutionQuote(
            code=code,
            trade_date=execute_after,
            price=self.price,
            paused=False,
            source="tushare-fund",
        )

    def price_snapshot(self, code: str, as_of: str | None = None):
        return ETFPriceSnapshot(
            code=code,
            name=code,
            trade_date=as_of,
            close=self.price,
            open=self.price,
            high=self.price,
            low=self.price,
            volume=1000.0,
            amount=100_000.0,
            avg_amount_20=100_000.0,
            momentum_20=0.1,
            momentum_60=0.2,
            low_volatility_60=0.01,
            nav=2.0,
            nav_date=as_of,
            discount_premium=0.0,
            industry="us_exposure",
            paused=False,
            source="tushare-fund",
        )


class PausedProvider(FakeProvider):
    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None):
        return ETFExecutionQuote(
            code=code,
            trade_date=None,
            price=None,
            paused=True,
            source="tushare-fund",
            reason="no quote",
        )


class InterruptingStore(PortfolioStore):
    def __init__(self, data_dir: str) -> None:
        super().__init__(data_dir)
        self.fail_next_pending_write = False

    def write_pending(self, pending):
        if self.fail_next_pending_write:
            self.fail_next_pending_write = False
            raise RuntimeError("simulated interruption")
        return super().write_pending(pending)


def _config():
    return {
        "competition_id": "test-cn-qdii-etf",
        "accounts": [
            {
                "id": "us_exposure",
                "scope": "us_exposure",
                "benchmark": "513100.SH",
                "cash": 100_000.0,
                "top_n": 2,
            }
        ],
    }


class ETFSimulatorTests(unittest.TestCase):
    @staticmethod
    def _pending(trade_date: str) -> dict[str, object]:
        return {
            "code": "513100.SH",
            "side": "buy",
            "shares": 100,
            "trade_date": trade_date,
            "account_id": "us_exposure",
        }

    def test_initialize_sets_market_and_empty_settlement_queue(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            state = initialize(_config(), store)
        self.assertEqual(state["market"], "cn_qdii_etf")
        self.assertEqual(state["accounts"]["us_exposure"]["cash"], 100_000.0)
        self.assertEqual(state["accounts"]["us_exposure"]["settlement_queue"], [])

    def test_buy_uses_100_share_lot_and_zero_stamp_tax(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            store.write_pending(
                [
                    {
                        "code": "513100.SH",
                        "side": "buy",
                        "shares": 100,
                        "trade_date": date.today().isoformat(),
                        "account_id": "us_exposure",
                    }
                ]
            )

            trades = execute_due_orders(store, FakeProvider(price=2.0), as_of=date.today())

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["stamp_tax"], 0.0)
        self.assertEqual(trades[0]["shares"], 100)
        self.assertAlmostEqual(trades[0]["commission"], 2.0 * 100 * 0.0003)

    def test_late_due_order_executes_on_retry_day(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            store.write_pending([self._pending("2026-07-13")])

            trades = execute_due_orders(
                store,
                FakeProvider(price=2.0),
                as_of=date(2026, 7, 14),
            )

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["trade_date"], "2026-07-14")

    def test_missing_quote_retains_pending_order_with_reason(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            store.write_pending([self._pending("2026-07-13")])

            trades = execute_due_orders(
                store,
                PausedProvider(),
                as_of=date(2026, 7, 13),
            )
            pending = store.read_pending()

        self.assertEqual(trades, [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["unfilled_reason"], "no quote")

    def test_successful_fill_persists_trade_and_position_csv(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            store.write_pending([self._pending("2026-07-13")])

            execute_due_orders(
                store,
                FakeProvider(price=2.0),
                as_of=date(2026, 7, 13),
            )
            trades = store.read_trades()
            positions = store.read_positions()

        self.assertEqual(len(trades), 1)
        self.assertEqual(len(positions), 1)
        self.assertEqual(str(trades.iloc[0]["trade_date"]), "2026-07-13")
        self.assertEqual(str(positions.iloc[0]["code"]), "513100.SH")

    def test_interrupted_fill_recovers_without_double_execution(self):
        with TemporaryDirectory() as tmp:
            store = InterruptingStore(tmp)
            initialize(_config(), store)
            store.write_pending([self._pending("2026-07-13")])
            store.fail_next_pending_write = True

            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                execute_due_orders(
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 13),
                )

            journal = store.data_dir / ".settlement_transaction.json"
            self.assertTrue(journal.exists())

            recovered = PortfolioStore(tmp)
            execute_due_orders(
                recovered,
                FakeProvider(price=2.0),
                as_of=date(2026, 7, 13),
            )
            state = recovered.load_state()
            trades = recovered.read_trades()
            pending = recovered.read_pending()
            self.assertFalse(journal.exists())

        self.assertEqual(state["accounts"]["us_exposure"]["positions"]["513100.SH"]["shares"], 100)
        self.assertEqual(len(trades), 1)
        self.assertEqual(pending, [])

    def test_generate_rebalance_orders_rounds_target_to_100_share_lot(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            orders = generate_rebalance_orders(
                store,
                FakeProvider(price=2.0),
                [
                    {"code": "513100.SH", "account_id": "us_exposure", "score": 1.0},
                    {"code": "159941.SZ", "account_id": "us_exposure", "score": 0.9},
                ],
                as_of=date(2026, 7, 9),
                top_n=2,
                max_single_weight=0.50,
            )

        self.assertEqual(len(orders), 2)
        self.assertTrue(all(order["shares"] % 100 == 0 for order in orders))
        self.assertTrue(all(order["side"] == "buy" for order in orders))

    def test_rebalance_preserves_an_existing_retry_order(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            store.write_pending(
                [
                    {
                        **self._pending("2026-07-08"),
                        "unfilled_reason": "no quote",
                    }
                ]
            )

            new_orders = generate_rebalance_orders(
                store,
                FakeProvider(price=2.0),
                [{"code": "159941.SZ", "account_id": "us_exposure", "score": 1.0}],
                as_of=date(2026, 7, 9),
                top_n=1,
                max_single_weight=0.5,
            )
            pending = store.read_pending()

        self.assertEqual({order["code"] for order in pending}, {"513100.SH", "159941.SZ"})
        retry = next(order for order in pending if order["code"] == "513100.SH")
        self.assertEqual(retry["unfilled_reason"], "no quote")
        self.assertEqual([order["code"] for order in new_orders], ["159941.SZ"])

    def test_generate_rebalance_orders_uses_each_account_top_n(self):
        config = {
            "competition_id": "test-cn-qdii-etf",
            "accounts": [
                {
                    "id": "us_exposure",
                    "scope": "us_exposure",
                    "benchmark": "513100.SH",
                    "cash": 100_000.0,
                    "top_n": 1,
                },
                {
                    "id": "hk_exposure",
                    "scope": "hk_exposure",
                    "benchmark": "159920.SZ",
                    "cash": 100_000.0,
                    "top_n": 2,
                },
            ],
            "trading": {"max_single_weight": 0.5},
        }
        scored = [
            {"code": "US-A", "account_id": "us_exposure", "score": 1.0},
            {"code": "US-B", "account_id": "us_exposure", "score": 0.9},
            {"code": "HK-A", "account_id": "hk_exposure", "score": 1.0},
            {"code": "HK-B", "account_id": "hk_exposure", "score": 0.9},
        ]
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(config, store)
            with patch("stock_analyze.markets.cn_qdii_etf.run.build_signals", return_value=scored):
                orders = generate_config_orders(
                    config,
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 9),
                )

        self.assertEqual(
            sum(order["account_id"] == "us_exposure" for order in orders),
            1,
        )
        self.assertEqual(
            sum(order["account_id"] == "hk_exposure" for order in orders),
            2,
        )

    def test_config_rebalance_prioritizes_distinct_underlying_indexes_and_persists_funnel(self):
        config = _config()
        config["accounts"][0]["cash"] = 500_000.0
        config["accounts"][0]["top_n"] = 3
        config["trading"] = {"max_single_weight": 0.34}
        scored = [
            {"code": "A.SH", "account_id": "us_exposure", "score": 1.0, "index_key": "nasdaq_100", "theme": "纳斯达克100", "universe_hash": "shared-hash"},
            {"code": "B.SH", "account_id": "us_exposure", "score": 0.99, "index_key": "nasdaq_100", "theme": "纳斯达克100", "universe_hash": "shared-hash"},
            {"code": "C.SH", "account_id": "us_exposure", "score": 0.90, "index_key": "sp_500", "theme": "标普500", "universe_hash": "shared-hash"},
            {"code": "D.SH", "account_id": "us_exposure", "score": 0.80, "index_key": "dow_jones_industrial", "theme": "道琼斯工业平均", "universe_hash": "shared-hash"},
        ]
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(config, store)
            with patch("stock_analyze.markets.cn_qdii_etf.run.build_signals", return_value=scored):
                orders = generate_config_orders(
                    config,
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 10),
                )
            selection = json.loads((store.data_dir / "selection_snapshot.json").read_text(encoding="utf-8"))

        self.assertEqual([order["code"] for order in orders], ["A.SH", "C.SH", "D.SH"])
        scope = selection["scopes"]["us_exposure"]
        self.assertEqual(scope["stages"][-1], {"key": "portfolio_target", "label": "目标持仓", "count": 3})
        self.assertEqual([row["code"] for row in scope["selected"]], ["A.SH", "C.SH", "D.SH"])
        self.assertEqual(selection["universe_hash"], "shared-hash")

    def test_config_rebalance_reserves_enough_cash_for_all_initial_buys(self):
        config = _config()
        config["accounts"][0]["cash"] = 500_000.0
        config["accounts"][0]["top_n"] = 5
        config["trading"] = {"max_single_weight": 0.20}
        scored = [
            {
                "code": f"51{index:04d}.SH",
                "account_id": "us_exposure",
                "score": 1.0 - index * 0.1,
            }
            for index in range(5)
        ]
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(config, store)
            with patch("stock_analyze.markets.cn_qdii_etf.run.build_signals", return_value=scored):
                orders = generate_config_orders(
                    config,
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 9),
                )
            trades = execute_due_orders(
                store,
                FakeProvider(price=2.0),
                as_of=date(2026, 7, 10),
            )
            pending = store.read_pending()

        self.assertEqual(len(orders), 5)
        self.assertEqual(len(trades), 5)
        self.assertEqual(pending, [])

    def test_recent_holding_inside_rank_buffer_is_not_sold(self):
        config = _config()
        config["accounts"][0]["top_n"] = 1
        config["trading"] = {"max_single_weight": 0.5}
        config["portfolio_controls"] = {
            "hold_buffer_pct": 1.0,
            "max_holding_days": 30,
        }
        scored = [
            {"code": "TOP.SH", "account_id": "us_exposure", "score": 1.0},
            {"code": "BUFFERED.SH", "account_id": "us_exposure", "score": 0.9},
        ]
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(config, store)
            state = store.load_state()
            state["accounts"]["us_exposure"]["positions"] = {
                "BUFFERED.SH": {
                    "shares": 100,
                    "avg_cost": 2.0,
                    "hold_since": "2026-07-01",
                }
            }
            store.save_state(state)
            with patch("stock_analyze.markets.cn_qdii_etf.run.build_signals", return_value=scored):
                orders = generate_config_orders(
                    config,
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 9),
                )

        self.assertFalse(
            any(order["code"] == "BUFFERED.SH" and order["side"] == "sell" for order in orders)
        )

    def test_expired_holding_loses_rank_buffer_protection(self):
        config = _config()
        config["accounts"][0]["top_n"] = 1
        config["trading"] = {"max_single_weight": 0.5}
        config["portfolio_controls"] = {
            "hold_buffer_pct": 1.0,
            "max_holding_days": 30,
        }
        scored = [
            {"code": "TOP.SH", "account_id": "us_exposure", "score": 1.0},
            {"code": "BUFFERED.SH", "account_id": "us_exposure", "score": 0.9},
        ]
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(config, store)
            state = store.load_state()
            state["accounts"]["us_exposure"]["positions"] = {
                "BUFFERED.SH": {
                    "shares": 100,
                    "avg_cost": 2.0,
                    "hold_since": "2026-05-01",
                }
            }
            store.save_state(state)
            with patch("stock_analyze.markets.cn_qdii_etf.run.build_signals", return_value=scored):
                orders = generate_config_orders(
                    config,
                    store,
                    FakeProvider(price=2.0),
                    as_of=date(2026, 7, 9),
                )

        self.assertTrue(
            any(order["code"] == "BUFFERED.SH" and order["side"] == "sell" for order in orders)
        )

    def test_update_nav_persists_market_value_column(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            state = store.load_state()
            state["accounts"]["us_exposure"]["positions"] = {
                "513100.SH": {"shares": 100, "avg_cost": 1.8}
            }
            store.save_state(state)

            rows = update_nav(store, FakeProvider(price=2.0), as_of=date(2026, 7, 9))
            nav = store.read_nav()
            positions = store.read_positions()

        self.assertEqual(rows[0]["market_value"], 200.0)
        self.assertIn("market_value", nav.columns)
        self.assertEqual(float(nav.iloc[-1]["market_value"]), 200.0)
        self.assertEqual(str(nav.iloc[-1]["benchmark_code"]), "513100.SH")
        self.assertEqual(rows[0]["benchmark_close"], 2.0)
        self.assertEqual(rows[0]["benchmark_date"], "2026-07-09")
        self.assertEqual(float(positions.iloc[-1]["last_price"]), 2.0)
        self.assertEqual(float(positions.iloc[-1]["market_value"]), 200.0)
        self.assertEqual(float(positions.iloc[-1]["unrealized_pnl"]), 20.0)

    def test_update_nav_counts_unsettled_sell_proceeds_as_receivable(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            initialize(_config(), store)
            state = store.load_state()
            state["accounts"]["us_exposure"]["settlement_queue"] = [
                {"settle_date": "2026-07-10", "amount": 1_250.0}
            ]
            store.save_state(state)

            rows = update_nav(store, FakeProvider(price=2.0), as_of=date(2026, 7, 9))
            nav = store.read_nav()

        self.assertEqual(rows[0]["settlement_receivable"], 1_250.0)
        self.assertEqual(rows[0]["total_value"], 101_250.0)
        self.assertEqual(float(nav.iloc[-1]["settlement_receivable"]), 1_250.0)


if __name__ == "__main__":
    unittest.main()
