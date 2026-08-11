"""Tests for the HK + US high-level, config-driven run wrappers.

These wrappers (``markets.<m>.run``, re-exported as the package public API)
adapt the generic CLI run contract — ``generate_rebalance_orders(config,
store, provider, ...)`` / ``execute_due_orders(config, ...)`` /
``update_nav(config, ...)`` — onto the low-level settlement engine. They are
what ``competition.get_market_module(market)`` hands the CLI for hk/us.

Two real-data-shaped invariants are pinned here that the ``_FakeStore``-based
settlement unit tests do NOT cover:

  1. ``--as-of`` arrives as a STRING from the CLI; the wrappers must coerce
     it to ``datetime.date`` before the settlement engine calls
     ``as_of.isoformat()`` (a bare string would AttributeError).
  2. The settlement engine reads/writes pending orders via
     ``read_pending``/``write_pending``; the real ``PortfolioStore`` only
     defined ``load_pending``/``save_pending`` until aliases were added — so
     these exercise the *real* store end-to-end, not a fake.
"""

from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pandas as pd

from stock_analyze.markets.hk.data_provider import HKExecutionQuote, HKPriceSnapshot
from stock_analyze.markets.us.data_provider import USExecutionQuote, USPriceSnapshot
from stock_analyze.store import PortfolioStore

import stock_analyze.markets.hk as hk
import stock_analyze.markets.us as us


def _hk_config(top_n: int = 2) -> dict:
    return {
        "strategy_id": "claude",
        "accounts": [
            {"id": "hsi", "scope": "hsi", "top_n": top_n, "cash": 500000.0,
             "benchmark": "^HSI"},
            {"id": "hscei", "scope": "hscei", "top_n": top_n, "cash": 500000.0,
             "benchmark": "^HSCE"},
        ],
        "trading": {"max_single_weight": 0.5},
        "factors": {
            "pe": {"weight": 0.5, "direction": "low"},
            "momentum_20": {"weight": 0.5, "direction": "high"},
        },
        "factor_processing": {"min_factor_coverage": 0.0},
    }


def _us_config(top_n: int = 2) -> dict:
    return {
        "strategy_id": "claude",
        "accounts": [
            {"id": "sp500", "scope": "sp500", "top_n": top_n, "cash": 75000.0,
             "benchmark": "^GSPC"},
            {"id": "ndx100", "scope": "ndx100", "top_n": top_n, "cash": 75000.0,
             "benchmark": "^NDX"},
        ],
        "trading": {"max_single_weight": 0.5},
        "factors": {
            "pe": {"weight": 0.5, "direction": "low"},
            "momentum_20": {"weight": 0.5, "direction": "high"},
        },
        "factor_processing": {"min_factor_coverage": 0.0},
    }


def _spot_df(codes: list[str]) -> pd.DataFrame:
    n = len(codes)
    return pd.DataFrame({
        "code": codes,
        "pe": [10.0 + i for i in range(n)],
        "momentum_20": [0.20 - 0.01 * i for i in range(n)],
    })


def _hk_provider(price: float = 100.0) -> MagicMock:
    provider = MagicMock()
    provider.spot.side_effect = lambda scope: _spot_df(
        ["0700.HK", "9988.HK", "0005.HK"] if scope == "hsi"
        else ["0939.HK", "1398.HK", "3690.HK"]
    )
    provider.price_snapshot.return_value = HKPriceSnapshot(
        code="X", trade_date=date.today().isoformat(),
        close=price, open=price, high=price, low=price, volume=1000.0,
        pe=10.0, pb=1.5, market_cap=1e12, dividend_yield=0.03,
        momentum_20=0.05, momentum_60=0.10, low_volatility_60=0.01,
    )
    provider.execution_quote.return_value = HKExecutionQuote(
        code="X", trade_date=date.today().isoformat(), price=price, paused=False,
    )
    return provider


def _us_provider(price: float = 100.0) -> MagicMock:
    provider = MagicMock()
    provider.spot.side_effect = lambda scope: _spot_df(
        ["AAPL", "MSFT", "NVDA"] if scope == "sp500"
        else ["GOOGL", "AMZN", "META"]
    )
    provider.price_snapshot.return_value = USPriceSnapshot(
        code="X", trade_date=date.today().isoformat(),
        close=price, open=price, high=price, low=price, volume=1000.0,
        pe=20.0, pb=3.0, market_cap=1e12, dividend_yield=0.01,
        momentum_20=0.05, momentum_60=0.10, low_volatility_60=0.012,
    )
    provider.execution_quote.return_value = USExecutionQuote(
        code="X", trade_date=date.today().isoformat(), price=price, paused=False,
    )
    return provider


class StorePendingAliasTests(unittest.TestCase):
    """read_pending/write_pending must round-trip on the real PortfolioStore.

    The settlement engine speaks read_pending/write_pending; A-share speaks
    load_pending/save_pending. Both must hit the same on-disk file.
    """

    def test_aliases_round_trip(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            orders = [{"code": "0700.HK", "side": "buy", "shares": 100,
                        "trade_date": "2026-06-16", "account_id": "hsi"}]
            store.write_pending(orders)
            self.assertEqual(store.read_pending(), orders)
            self.assertEqual(store.load_pending(), orders)


class HighLevelGenerateTests(unittest.TestCase):
    """generate_rebalance_orders(config, ...) scores every account, persists
    pending orders to the real store, and tolerates a STRING as_of."""

    def _run(self, module, config, provider):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            module.initialize(config, store)
            # as_of is a STRING, exactly as the CLI passes --as-of.
            orders = module.generate_rebalance_orders(
                config, store, provider, as_of="2026-06-16", run_id="run-1",
            )
            return orders, store.read_pending()

    def test_hk_generates_and_persists(self):
        orders, pending = self._run(hk, _hk_config(), _hk_provider())
        self.assertGreater(len(orders), 0)
        # orders are tagged per account and persisted to the pending store
        self.assertEqual(len(pending), len(orders))
        self.assertTrue({o["account_id"] for o in orders} <= {"hsi", "hscei"})
        for o in orders:
            self.assertEqual(o["side"], "buy")

    def test_us_generates_and_persists(self):
        orders, pending = self._run(us, _us_config(), _us_provider())
        self.assertGreater(len(orders), 0)
        self.assertEqual(len(pending), len(orders))
        self.assertTrue({o["account_id"] for o in orders} <= {"sp500", "ndx100"})


class HighLevelExecuteTests(unittest.TestCase):
    def test_hk_execute_accepts_config_and_string_as_of(self):
        as_of = date(2026, 6, 16)
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            hk.initialize(_hk_config(), store)
            store.write_pending([{
                "code": "0700.HK", "side": "buy", "shares": 100,
                "trade_date": as_of.isoformat(), "account_id": "hsi",
            }])
            trades = hk.execute_due_orders(
                _hk_config(), store, _hk_provider(100.0), as_of=as_of.isoformat(),
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "buy")

    def test_us_execute_accepts_config_and_string_as_of(self):
        as_of = date(2026, 6, 16)
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            us.initialize(_us_config(), store)
            store.write_pending([{
                "code": "AAPL", "side": "buy", "shares": 10,
                "trade_date": as_of.isoformat(), "account_id": "sp500",
            }])
            trades = us.execute_due_orders(
                _us_config(), store, _us_provider(100.0), as_of=as_of.isoformat(),
            )
        self.assertEqual(len(trades), 1)


class HighLevelUpdateNavTests(unittest.TestCase):
    def test_hk_update_nav_accepts_config_notes_string_as_of(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            hk.initialize(_hk_config(), store)
            state = store.load_state()
            state["accounts"]["hsi"]["positions"] = {
                "0700.HK": {"shares": 100, "avg_cost": 90.0}
            }
            store.save_state(state)
            rows = hk.update_nav(
                _hk_config(), store, _hk_provider(110.0),
                as_of="2026-06-16", notes="weekly signal",
            )
        hsi = next(r for r in rows if r["account_id"] == "hsi")
        # cash 500000 + 100*110 = 511,000
        self.assertAlmostEqual(hsi["total_value"], 511_000.0, places=2)

    def test_us_update_nav_accepts_config_notes_string_as_of(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            us.initialize(_us_config(), store)
            state = store.load_state()
            state["accounts"]["sp500"]["positions"] = {
                "AAPL": {"shares": 10, "avg_cost": 90.0}
            }
            store.save_state(state)
            rows = us.update_nav(
                _us_config(), store, _us_provider(110.0),
                as_of="2026-06-16", notes="weekly signal",
            )
        sp = next(r for r in rows if r["account_id"] == "sp500")
        # cash 75000 + 10*110 = 76,100
        self.assertAlmostEqual(sp["total_value"], 76_100.0, places=2)


class PackageApiIsConfigFirstTests(unittest.TestCase):
    """The package public names dispatch the HIGH-LEVEL (config-first)
    contract, matching a_share, so the generic CLI works uniformly."""

    def test_hk_generate_is_config_first(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            hk.initialize(_hk_config(), store)
            # config dict as first positional must not raise (would TypeError
            # against the low-level store-first signature).
            orders = hk.generate_rebalance_orders(
                _hk_config(), store, _hk_provider(), as_of="2026-06-16",
            )
        self.assertIsInstance(orders, list)

    def test_us_generate_is_config_first(self):
        with TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            us.initialize(_us_config(), store)
            orders = us.generate_rebalance_orders(
                _us_config(), store, _us_provider(), as_of="2026-06-16",
            )
        self.assertIsInstance(orders, list)


if __name__ == "__main__":
    unittest.main()
