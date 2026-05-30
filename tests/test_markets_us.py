"""Tests for the US market subpackage (Phase 3 bootstrap + provider +
simulator + strategy)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

from stock_analyze.markets.us import (
    build_signals,
    initialize,
    make_provider,
)
# These unit tests exercise the low-level settlement primitives, which live in
# .simulator (the package now re-exports the high-level, config-first run
# wrappers under these names — see markets/us/run.py).
from stock_analyze.markets.us.simulator import (
    execute_due_orders,
    generate_rebalance_orders,
    update_nav,
)
from stock_analyze.markets.us.data_provider import (
    USExecutionQuote,
    USPriceSnapshot,
    YFinanceUSProvider,
)
from stock_analyze.markets.us.simulator import _next_business_day


# ----- helpers -----


class _FakeStore:
    def __init__(self, state=None, pending=None):
        self.state = state or {}
        self.pending = pending or []
        self.nav_rows = []

    def load_state(self):
        return self.state

    def save_state(self, s):
        self.state = s

    def read_pending(self):
        return self.pending

    def write_pending(self, p):
        self.pending = p

    def append_nav(self, rows):
        self.nav_rows.extend(rows)


def _fake_history(n_days: int = 70, start_close: float = 100.0) -> pd.DataFrame:
    today = date.today()
    dates = [today - timedelta(days=n_days - 1 - i) for i in range(n_days)]
    closes = [start_close * (1.005 ** i) for i in range(n_days)]
    return pd.DataFrame(
        {
            "Open": [c * 0.999 for c in closes],
            "High": [c * 1.005 for c in closes],
            "Low": [c * 0.995 for c in closes],
            "Close": closes,
            "Volume": [1_000_000.0] * n_days,
        },
        index=pd.DatetimeIndex(dates),
    )


def _fake_provider(price: float = 100.0):
    provider = MagicMock()
    provider.execution_quote.return_value = USExecutionQuote(
        code="X", trade_date=date.today().isoformat(), price=price, paused=False,
    )
    provider.price_snapshot.return_value = USPriceSnapshot(
        code="X", trade_date=date.today().isoformat(),
        close=price, open=price, high=price, low=price, volume=1000.0,
        pe=20.0, pb=3.0, market_cap=1e12, dividend_yield=0.01,
        momentum_20=0.05, momentum_60=0.10, low_volatility_60=0.012,
    )
    return provider


# ----- bootstrap -----


class USBootstrapTests(unittest.TestCase):
    def test_us_in_markets(self):
        from stock_analyze import competition
        self.assertIn("us", competition.MARKETS)

    def test_us_factor_whitelist(self):
        from stock_analyze.overlay_guard import AVAILABLE_FACTORS_BY_MARKET
        us = AVAILABLE_FACTORS_BY_MARKET["us"]
        for n in ("pe", "pb", "momentum_20", "momentum_60",
                   "low_volatility_60", "dividend_yield"):
            self.assertIn(n, us)

    def test_universe_resolves(self):
        from stock_analyze.markets.us import universe
        sp500 = universe.resolve_universe("sp500")
        ndx100 = universe.resolve_universe("ndx100")
        self.assertIn("AAPL", sp500)
        self.assertIn("NVDA", sp500)
        self.assertIn("MSFT", ndx100)
        self.assertGreaterEqual(len(sp500), 50)
        self.assertGreaterEqual(len(ndx100), 50)

    def test_mechanics_constants(self):
        from stock_analyze.markets.us import mechanics
        self.assertEqual(mechanics.SETTLEMENT_DAYS, 1)
        self.assertIsNone(mechanics.DAILY_LIMIT_PCT)
        self.assertEqual(mechanics.DEFAULT_LOT_SIZE, 1)
        self.assertTrue(mechanics.ALLOW_SHORTING)
        self.assertEqual(mechanics.STAMP_TAX_RATE, 0.0)
        self.assertEqual(mechanics.COMMISSION_RATE, 0.0)


# ----- data_provider -----


class DataProviderTests(unittest.TestCase):
    def test_make_provider_returns_yfinance_us(self):
        self.assertIsInstance(make_provider(), YFinanceUSProvider)

    def test_universe_via_provider(self):
        p = make_provider()
        self.assertIn("AAPL", p.universe("sp500"))

    def test_price_snapshot(self):
        p = make_provider()
        with patch(
            "stock_analyze.markets.us.data_provider._fetch_ticker_info",
            return_value={"trailingPE": 25.0, "priceToBook": 4.0,
                          "marketCap": 3e12, "dividendYield": 0.005},
        ), patch(
            "stock_analyze.markets.us.data_provider._fetch_ticker_history",
            return_value=_fake_history(),
        ):
            snap = p.price_snapshot("AAPL")
        self.assertEqual(snap.code, "AAPL")
        self.assertAlmostEqual(snap.pe, 25.0)
        self.assertIsNotNone(snap.momentum_20)

    def test_lot_size_always_one(self):
        self.assertEqual(make_provider().lot_size("AAPL"), 1)


# ----- simulator -----


class SimulatorBuyTests(unittest.TestCase):
    def test_buy_debits_cash_with_zero_fees(self):
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 100_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": []}}},
            pending=[{"code": "AAPL", "side": "buy", "shares": 10,
                       "trade_date": date.today().isoformat(),
                       "account_id": "sp500"}],
        )
        trades = execute_due_orders(store, _fake_provider(100.0), as_of=date.today())
        self.assertEqual(len(trades), 1)
        # 10 × 100 = 1000 cost, zero fees
        self.assertAlmostEqual(
            store.state["accounts"]["sp500"]["cash"], 99_000.0, places=2,
        )
        self.assertEqual(store.state["accounts"]["sp500"]["positions"]["AAPL"]["shares"], 10)


class SimulatorSellTests(unittest.TestCase):
    def test_sell_uses_t_plus_1_settlement(self):
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 50_000.0, "cash_collateral": 0.0,
                                            "positions": {"AAPL": {"shares": 10, "avg_cost": 90.0,
                                                                      "hold_since": "2026-05-20"}},
                                            "settlement_queue": []}}},
            pending=[{"code": "AAPL", "side": "sell", "shares": 10,
                       "trade_date": date.today().isoformat(),
                       "account_id": "sp500"}],
        )
        trades = execute_due_orders(store, _fake_provider(120.0), as_of=date.today())
        self.assertEqual(len(trades), 1)
        # cash unchanged (T+1 queue)
        self.assertAlmostEqual(store.state["accounts"]["sp500"]["cash"], 50_000.0)
        sq = store.state["accounts"]["sp500"]["settlement_queue"]
        self.assertEqual(len(sq), 1)
        # T+1 from today
        expected = _next_business_day(date.today(), 1).isoformat()
        self.assertEqual(sq[0]["settle_date"], expected)
        # Gross 10*120 = 1200, no fees
        self.assertAlmostEqual(sq[0]["amount"], 1200.0)


class SimulatorShortTests(unittest.TestCase):
    def test_short_freezes_collateral(self):
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 50_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": [],
                                            "benchmark": "^GSPC"}}},
            pending=[{"code": "TSLA", "side": "short", "shares": 100,
                       "trade_date": date.today().isoformat(),
                       "account_id": "sp500"}],
        )
        trades = execute_due_orders(store, _fake_provider(200.0), as_of=date.today())
        self.assertEqual(len(trades), 1)
        # Model A (fix-short-sale-nav-accounting): proceeds (100*200=20000) go
        # into cash_collateral; US is zero-fee so cash is UNCHANGED at open.
        self.assertAlmostEqual(store.state["accounts"]["sp500"]["cash"], 50_000.0)
        self.assertAlmostEqual(store.state["accounts"]["sp500"]["cash_collateral"], 20_000.0)
        self.assertEqual(store.state["accounts"]["sp500"]["positions"]["TSLA"]["shares"], -100)

    def test_short_open_at_fair_value_preserves_nav(self):
        # US zero-fee: opening a short at fair value leaves NAV exactly equal
        # to starting equity (design §Model A).
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 50_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": [],
                                            "benchmark": "^GSPC"}}},
            pending=[{"code": "TSLA", "side": "short", "shares": 100,
                       "trade_date": date.today().isoformat(), "account_id": "sp500"}],
        )
        execute_due_orders(store, _fake_provider(200.0), as_of=date.today())
        rows = update_nav(store, _fake_provider(200.0), as_of=date.today())
        self.assertAlmostEqual(rows[0]["total_value"], 50_000.0, places=2)

    def test_short_round_trip_no_move_returns_full_nav(self):
        # US zero-fee: open then cover at the same price → NAV back to start.
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 50_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": [],
                                            "benchmark": "^GSPC"}}},
            pending=[{"code": "TSLA", "side": "short", "shares": 100,
                       "trade_date": date.today().isoformat(), "account_id": "sp500"}],
        )
        execute_due_orders(store, _fake_provider(200.0), as_of=date.today())
        store.pending = [{"code": "TSLA", "side": "cover", "shares": 100,
                           "trade_date": date.today().isoformat(), "account_id": "sp500"}]
        execute_due_orders(store, _fake_provider(200.0), as_of=date.today())
        rows = update_nav(store, _fake_provider(200.0), as_of=date.today())
        self.assertAlmostEqual(rows[0]["total_value"], 50_000.0, places=2)

    def test_short_mark_to_market_reflects_unrealized_pnl(self):
        # Cover-side P/L is embedded; mark-to-market a price drop = profit.
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 50_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": [],
                                            "benchmark": "^GSPC"}}},
            pending=[{"code": "TSLA", "side": "short", "shares": 100,
                       "trade_date": date.today().isoformat(), "account_id": "sp500"}],
        )
        execute_due_orders(store, _fake_provider(200.0), as_of=date.today())
        rows = update_nav(store, _fake_provider(180.0), as_of=date.today())
        # short @200, mark @180 → +100*(200-180) = +2000 over starting equity.
        self.assertAlmostEqual(rows[0]["total_value"] - 50_000.0, 100 * (200.0 - 180.0), places=2)


class UpdateNAVTests(unittest.TestCase):
    def test_nav_basic(self):
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 80_000.0, "cash_collateral": 0.0,
                                            "positions": {"AAPL": {"shares": 10, "avg_cost": 90.0}},
                                            "settlement_queue": [],
                                            "benchmark": "^GSPC"}}},
        )
        rows = update_nav(store, _fake_provider(110.0), as_of=date.today())
        # 80000 + 10*110 = 81,100
        self.assertAlmostEqual(rows[0]["total_value"], 81_100.0)


class GenerateOrdersTests(unittest.TestCase):
    def test_emits_buy_orders_with_lot_size_1(self):
        store = _FakeStore(
            state={"accounts": {"sp500": {"cash": 100_000.0, "cash_collateral": 0.0,
                                            "positions": {}, "settlement_queue": []}}},
        )
        scored = [
            {"code": "AAPL", "account_id": "sp500", "score": 0.9},
            {"code": "MSFT", "account_id": "sp500", "score": 0.8},
        ]
        orders = generate_rebalance_orders(
            store, _fake_provider(100.0), scored,
            as_of=date.today(), top_n=2, max_single_weight=0.5,
        )
        self.assertEqual(len(orders), 2)
        for order in orders:
            self.assertEqual(order["side"], "buy")
            # lot_size=1 so shares is exact (not rounded down to 100s)
            self.assertGreater(order["shares"], 0)


# ----- strategy -----


class StrategyTests(unittest.TestCase):
    def test_build_signals_basic(self):
        provider = MagicMock()
        provider.spot.return_value = pd.DataFrame({
            "code": ["AAPL", "MSFT", "NVDA"],
            "pe": [25.0, 30.0, 50.0],
            "pb": [4.0, 5.0, 8.0],
            "momentum_20": [0.05, 0.03, 0.10],
            "momentum_60": [0.10, 0.08, 0.20],
            "low_volatility_60": [0.012, 0.015, 0.025],
            "dividend_yield": [0.005, 0.008, 0.0],
        })
        config = {
            "accounts": [{"id": "sp500", "scope": "sp500", "cash": 75000, "top_n": 50,
                           "benchmark": "^GSPC"}],
            "factors": {
                "pe": {"weight": 0.5, "direction": "low"},
                "momentum_20": {"weight": 0.5, "direction": "high"},
            },
            "factor_processing": {"min_factor_coverage": 0.0},
        }
        rows = build_signals(config, provider)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["account_id"], "sp500")


class InitializeTests(unittest.TestCase):
    def test_initialize_seeds_state(self):
        store = _FakeStore()
        config = {
            "competition_id": "test_us",
            "accounts": [
                {"id": "sp500", "scope": "sp500", "benchmark": "^GSPC", "cash": 75000.0},
                {"id": "ndx100", "scope": "ndx100", "benchmark": "^NDX", "cash": 75000.0},
            ],
        }
        state = initialize(config, store)
        self.assertEqual(state["market"], "us")
        self.assertEqual(state["accounts"]["sp500"]["cash"], 75000.0)
        self.assertEqual(state["accounts"]["sp500"]["positions"], {})


class PublicAPITests(unittest.TestCase):
    def test_us_module_exposes_six_callables(self):
        from stock_analyze.markets import us
        for name in ("make_provider", "build_signals", "execute_due_orders",
                     "update_nav", "generate_rebalance_orders", "initialize"):
            self.assertTrue(callable(getattr(us, name)),
                            msg=f"us.{name} not exposed")


if __name__ == "__main__":
    unittest.main()
