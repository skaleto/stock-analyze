"""Tests for the HK simulator.

Covers the HK-specific mechanics that diverge from A-share:
  - T+2 settlement queue
  - No daily-limit block (we explicitly don't model it)
  - Stamp duty 0.13% on both buy and sell
  - Simplified shorting (signed shares + collateral)
  - Variable lot via mechanics.lot_size_for
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from stock_analyze.markets.hk.data_provider import HKExecutionQuote, HKPriceSnapshot
from stock_analyze.markets.hk.simulator import (
    HKOrder,
    _drain_settlement,
    _next_business_day,
    execute_due_orders,
    generate_rebalance_orders,
    initialize,
    update_nav,
)


class _FakeStore:
    """In-memory stand-in for PortfolioStore."""

    def __init__(self, state: dict | None = None, pending: list | None = None):
        self.state: dict = state or {}
        self.pending: list = pending or []
        self.nav_rows: list = []

    def load_state(self) -> dict:
        return self.state

    def save_state(self, state: dict) -> None:
        self.state = state

    def read_pending(self) -> list:
        return self.pending

    def write_pending(self, pending: list) -> None:
        self.pending = pending

    def append_nav(self, rows: list) -> None:
        self.nav_rows.extend(rows)


def _fake_provider(price: float = 100.0):
    """Build a MagicMock provider returning a constant price."""
    provider = MagicMock()
    provider.execution_quote.return_value = HKExecutionQuote(
        code="X", trade_date=date.today().isoformat(), price=price, paused=False,
    )
    provider.price_snapshot.return_value = HKPriceSnapshot(
        code="X", trade_date=date.today().isoformat(),
        close=price, open=price, high=price, low=price, volume=1000.0,
        pe=10.0, pb=1.5, market_cap=1e12, dividend_yield=0.03,
        momentum_20=0.05, momentum_60=0.10, low_volatility_60=0.01,
    )
    return provider


class NextBusinessDayTests(unittest.TestCase):
    def test_monday_plus_one(self):
        # 2026-05-25 is a Monday
        self.assertEqual(
            _next_business_day(date(2026, 5, 25), 1),
            date(2026, 5, 26),
        )

    def test_friday_plus_two_skips_weekend(self):
        # Friday 2026-05-22 + 2 business days = Tuesday 2026-05-26
        self.assertEqual(
            _next_business_day(date(2026, 5, 22), 2),
            date(2026, 5, 26),
        )

    def test_friday_plus_one_lands_monday(self):
        self.assertEqual(
            _next_business_day(date(2026, 5, 22), 1),
            date(2026, 5, 25),
        )


class InitializeTests(unittest.TestCase):
    def test_initialize_sets_per_account_skeleton(self):
        store = _FakeStore()
        config = {
            "competition_id": "test_hk",
            "accounts": [
                {"id": "hsi", "scope": "hsi", "benchmark": "^HSI", "cash": 500000.0},
                {"id": "hscei", "scope": "hscei", "benchmark": "^HSCE", "cash": 500000.0},
            ],
        }
        state = initialize(config, store)
        self.assertEqual(state["market"], "hk")
        self.assertEqual(state["accounts"]["hsi"]["cash"], 500000.0)
        self.assertEqual(state["accounts"]["hsi"]["cash_collateral"], 0.0)
        self.assertEqual(state["accounts"]["hsi"]["positions"], {})
        self.assertEqual(state["accounts"]["hsi"]["settlement_queue"], [])


class SettlementQueueTests(unittest.TestCase):
    def test_drain_settlement_credits_due_items(self):
        as_of = date(2026, 5, 27)
        account = {
            "cash": 100_000.0,
            "settlement_queue": [
                {"settle_date": "2026-05-25", "amount": 5_000.0},  # past — drain
                {"settle_date": "2026-05-27", "amount": 3_000.0},  # today — drain
                {"settle_date": "2026-05-29", "amount": 7_000.0},  # future — keep
            ],
        }
        credited = _drain_settlement(account, as_of)
        self.assertEqual(credited, 8_000.0)
        self.assertEqual(account["cash"], 108_000.0)
        self.assertEqual(len(account["settlement_queue"]), 1)
        self.assertEqual(account["settlement_queue"][0]["amount"], 7_000.0)

    def test_drain_settlement_noop_when_nothing_due(self):
        as_of = date(2026, 5, 27)
        account = {
            "cash": 100_000.0,
            "settlement_queue": [
                {"settle_date": "2026-05-29", "amount": 7_000.0},
            ],
        }
        credited = _drain_settlement(account, as_of)
        self.assertEqual(credited, 0.0)
        self.assertEqual(account["cash"], 100_000.0)


class BuyOrderTests(unittest.TestCase):
    def test_buy_debits_cash_and_creates_position_with_stamp(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {"cash": 100_000.0, "cash_collateral": 0.0,
                             "positions": {}, "settlement_queue": []}
                }
            },
            pending=[
                {"code": "0700.HK", "side": "buy", "shares": 100,
                 "trade_date": date.today().isoformat(),
                 "account_id": "hsi", "target_value": 10000.0}
            ],
        )
        provider = _fake_provider(price=100.0)
        trades = execute_due_orders(store, provider, as_of=date.today())
        self.assertEqual(len(trades), 1)
        # 100 shares × 100 = 10,000 gross + 0.13% stamp (13) + 0.03% commission (3)
        # = 10,016 debited from cash
        self.assertAlmostEqual(store.state["accounts"]["hsi"]["cash"], 100_000 - 10_016, places=2)
        pos = store.state["accounts"]["hsi"]["positions"]["0700.HK"]
        self.assertEqual(pos["shares"], 100)
        self.assertAlmostEqual(pos["avg_cost"], 100.0, places=4)

    def test_buy_blocked_when_insufficient_cash(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {"cash": 1_000.0, "cash_collateral": 0.0,
                             "positions": {}, "settlement_queue": []}
                }
            },
            pending=[
                {"code": "0700.HK", "side": "buy", "shares": 100,
                 "trade_date": date.today().isoformat(), "account_id": "hsi"}
            ],
        )
        provider = _fake_provider(price=100.0)
        trades = execute_due_orders(store, provider, as_of=date.today())
        self.assertEqual(trades, [])
        self.assertEqual(store.state["accounts"]["hsi"]["cash"], 1_000.0)
        self.assertEqual(store.state["accounts"]["hsi"]["positions"], {})


class SellOrderTests(unittest.TestCase):
    def test_sell_credits_settlement_queue_not_cash(self):
        """Key HK behaviour: T+2 means sell proceeds wait 2 business days."""
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {
                        "cash": 50_000.0, "cash_collateral": 0.0,
                        "positions": {"0700.HK": {"shares": 100, "avg_cost": 90.0,
                                                    "hold_since": "2026-05-20"}},
                        "settlement_queue": [],
                    }
                }
            },
            pending=[
                {"code": "0700.HK", "side": "sell", "shares": 100,
                 "trade_date": date.today().isoformat(), "account_id": "hsi"}
            ],
        )
        provider = _fake_provider(price=120.0)
        trades = execute_due_orders(store, provider, as_of=date.today())
        self.assertEqual(len(trades), 1)
        # Cash should NOT increase — gross 12,000 hits settlement_queue
        self.assertEqual(store.state["accounts"]["hsi"]["cash"], 50_000.0)
        sq = store.state["accounts"]["hsi"]["settlement_queue"]
        self.assertEqual(len(sq), 1)
        # 12000 - 0.13% stamp (15.6) - 0.03% commission (3.6) = 11,980.8
        self.assertAlmostEqual(sq[0]["amount"], 11_980.8, places=1)
        # T+2 from today's business calendar
        expected_settle = _next_business_day(date.today(), 2).isoformat()
        self.assertEqual(sq[0]["settle_date"], expected_settle)
        # Position fully closed
        self.assertNotIn("0700.HK", store.state["accounts"]["hsi"]["positions"])


class ShortOrderTests(unittest.TestCase):
    def test_short_freezes_collateral_and_creates_negative_position(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {"cash": 100_000.0, "cash_collateral": 0.0,
                             "positions": {}, "settlement_queue": []}
                }
            },
            pending=[
                {"code": "0700.HK", "side": "short", "shares": 100,
                 "trade_date": date.today().isoformat(), "account_id": "hsi"}
            ],
        )
        provider = _fake_provider(price=100.0)
        trades = execute_due_orders(store, provider, as_of=date.today())
        self.assertEqual(len(trades), 1)
        # Gross = 10,000 → 100% collateral (10,000) + stamp (13) + commission (3) = 10,016 cash debit
        self.assertAlmostEqual(
            store.state["accounts"]["hsi"]["cash"], 100_000 - 10_016, places=2
        )
        self.assertAlmostEqual(
            store.state["accounts"]["hsi"]["cash_collateral"], 10_000.0, places=2
        )
        pos = store.state["accounts"]["hsi"]["positions"]["0700.HK"]
        self.assertEqual(pos["shares"], -100)
        self.assertEqual(pos["short_collateral"], 10_000.0)

    def test_cover_releases_collateral_and_applies_pnl(self):
        """Cover at lower price → positive P/L (we shorted high, bought back low)."""
        # Initial: shorted 100 shares @ $100, collateral $10,000 frozen.
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {
                        "cash": 89_984.0,  # 100k - 10k collateral - 13 stamp - 3 commission
                        "cash_collateral": 10_000.0,
                        "positions": {
                            "0700.HK": {"shares": -100, "avg_cost": 100.0,
                                          "hold_since": "2026-05-20",
                                          "short_collateral": 10_000.0}
                        },
                        "settlement_queue": [],
                    }
                }
            },
            pending=[
                {"code": "0700.HK", "side": "cover", "shares": 100,
                 "trade_date": date.today().isoformat(), "account_id": "hsi"}
            ],
        )
        # Cover at $80 (profit)
        provider = _fake_provider(price=80.0)
        trades = execute_due_orders(store, provider, as_of=date.today())
        self.assertEqual(len(trades), 1)
        # Collateral released: 10000.
        # P/L: (100 - 80) × 100 = +2000 (profit since we shorted at higher price).
        # Stamp/commission at cover price 80: gross=8000, stamp=10.4, comm=2.4 → 12.8 total fees
        # Net cash back: 10000 + 2000 - 12.8 = 11,987.2
        # Final cash: 89,984 + 11,987.2 = 101,971.2
        self.assertAlmostEqual(
            store.state["accounts"]["hsi"]["cash"], 101_971.2, places=1
        )
        self.assertEqual(store.state["accounts"]["hsi"]["cash_collateral"], 0.0)
        self.assertNotIn("0700.HK", store.state["accounts"]["hsi"]["positions"])


class UpdateNAVTests(unittest.TestCase):
    def test_nav_includes_cash_collateral_and_positions(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {
                        "cash": 80_000.0,
                        "cash_collateral": 5_000.0,
                        "positions": {"0700.HK": {"shares": 100, "avg_cost": 90.0}},
                        "settlement_queue": [],
                        "benchmark": "^HSI",
                    }
                }
            }
        )
        provider = _fake_provider(price=110.0)
        rows = update_nav(store, provider, as_of=date.today())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # cash (80k) + collateral (5k) + 100*110 (11k) = 96,000
        self.assertAlmostEqual(row["total_value"], 96_000.0, places=2)
        self.assertEqual(row["account_id"], "hsi")
        self.assertEqual(row["benchmark_code"], "^HSI")

    def test_nav_short_position_reduces_equity(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {
                        "cash": 90_000.0,
                        "cash_collateral": 10_000.0,
                        "positions": {"0700.HK": {"shares": -100, "avg_cost": 100.0,
                                                    "short_collateral": 10_000.0}},
                        "settlement_queue": [],
                        "benchmark": "^HSI",
                    }
                }
            }
        )
        # Price now $120 → adverse move, short is at a loss
        provider = _fake_provider(price=120.0)
        rows = update_nav(store, provider, as_of=date.today())
        # 90000 + 10000 - 100*120 = 88,000  (we've lost $2k on the short)
        self.assertAlmostEqual(rows[0]["total_value"], 88_000.0, places=2)


class GenerateRebalanceOrdersTests(unittest.TestCase):
    def test_emits_buy_orders_for_top_n(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {"cash": 100_000.0, "cash_collateral": 0.0,
                             "positions": {}, "settlement_queue": []}
                }
            },
            pending=[],
        )
        provider = _fake_provider(price=100.0)
        scored = [
            {"code": "0700.HK", "account_id": "hsi", "score": 0.9, "reason": "high_pe"},
            {"code": "9988.HK", "account_id": "hsi", "score": 0.8, "reason": "high_pe"},
        ]
        orders = generate_rebalance_orders(
            store, provider, scored,
            as_of=date.today(), top_n=2, max_single_weight=0.5,
        )
        self.assertEqual(len(orders), 2)
        for order in orders:
            self.assertEqual(order["side"], "buy")
            self.assertGreater(order["shares"], 0)

    def test_sells_holdings_dropped_from_top_n(self):
        store = _FakeStore(
            state={
                "accounts": {
                    "hsi": {
                        "cash": 50_000.0, "cash_collateral": 0.0,
                        "positions": {
                            "OLD.HK": {"shares": 100, "avg_cost": 50.0},
                        },
                        "settlement_queue": [],
                    }
                }
            },
            pending=[],
        )
        provider = _fake_provider(price=100.0)
        scored = [{"code": "0700.HK", "account_id": "hsi", "score": 0.9}]
        orders = generate_rebalance_orders(
            store, provider, scored,
            as_of=date.today(), top_n=1, max_single_weight=0.5,
        )
        # 1 sell for OLD.HK + 1 buy for 0700.HK
        sides = sorted(o["side"] for o in orders)
        self.assertIn("sell", sides)
        self.assertIn("buy", sides)
        sell = next(o for o in orders if o["side"] == "sell")
        self.assertEqual(sell["code"], "OLD.HK")
        self.assertEqual(sell["shares"], 100)


if __name__ == "__main__":
    unittest.main()
