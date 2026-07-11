from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory

from stock_analyze.markets.cn_qdii_etf.data_provider import ETFExecutionQuote, ETFPriceSnapshot
from stock_analyze.markets.cn_qdii_etf.simulator import (
    ETFOrder,
    execute_due_orders,
    generate_rebalance_orders,
    initialize,
    update_nav,
)
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
        self.assertEqual(float(positions.iloc[-1]["last_price"]), 2.0)
        self.assertEqual(float(positions.iloc[-1]["market_value"]), 200.0)
        self.assertEqual(float(positions.iloc[-1]["unrealized_pnl"]), 20.0)


if __name__ == "__main__":
    unittest.main()
