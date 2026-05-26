"""Tests that simulator functions accept as_of (date) and data_root / market_data_root kwargs for backtest mode.

These kwargs let the future backtest engine (Task 7) drive the simulator
day-by-day over historical data without changing the forward-mode contract:
when all three kwargs are None, behavior is identical to before this change.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

from stock_analyze import simulator
from stock_analyze.data_provider import ExecutionQuote
from stock_analyze.store import PortfolioStore


def _base_config(cash: float = 100_000) -> dict:
    return {
        "strategy_id": "clock_injection",
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


class _RecordingProvider:
    """Provider stub that records all calls and never returns a fill."""

    def __init__(self) -> None:
        self.cache_dir: Path | None = None
        self.execution_quote_calls: list[dict] = []
        self.price_snapshot_calls: list[dict] = []
        self.benchmark_close_calls: list[dict] = []

    def execution_quote(self, code: str, execute_after: str, side: str, as_of: str | None = None) -> ExecutionQuote:
        self.execution_quote_calls.append({"code": code, "execute_after": execute_after, "side": side, "as_of": as_of})
        return ExecutionQuote(code=code, trade_date=None, price=None, reason="not_visible")

    def price_snapshot(self, code: str, as_of: str | None = None):
        self.price_snapshot_calls.append({"code": code, "as_of": as_of})

        class _Snap:
            close = 10.0

        return _Snap()

    def benchmark_close(self, code: str, as_of: str | None = None):
        self.benchmark_close_calls.append({"code": code, "as_of": as_of})
        return 100.0, as_of or "2026-01-01"

    def next_trading_day(self, day: str) -> str:
        return day


class ExecuteDueOrdersClockInjectionTests(unittest.TestCase):
    def test_accepts_as_of_as_date_object(self) -> None:
        """execute_due_orders should accept a datetime.date for as_of (not only str)."""
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            target = date(2026, 5, 18)

            # Should not raise TypeError for date object
            simulator.execute_due_orders(config, store, provider, as_of=target)

    def test_uses_today_when_as_of_none(self) -> None:
        """When as_of is None, execute_due_orders should use date.today().

        Picks an execute_after that is in the past relative to the mocked today
        (so the batch is consumed) but in the future relative to a clearly
        earlier date — letting us prove the mocked today was actually used.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            store.save_pending(
                [
                    {
                        "run_id": "test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-18",
                        "orders": [],
                    }
                ]
            )
            provider = _RecordingProvider()

            mocked_today = date(2026, 5, 20)  # > 2026-05-18 → batch is due
            with mock.patch("stock_analyze.simulator.date") as mocked_date:
                mocked_date.today.return_value = mocked_today
                mocked_date.side_effect = lambda *a, **kw: date(*a, **kw)
                simulator.execute_due_orders(config, store, provider)

            # If today (mocked) >= execute_after, the batch is consumed.
            self.assertEqual(len(store.load_pending()), 0)

    def test_as_of_date_takes_precedence_over_today(self) -> None:
        """When a date is passed, it is the cutoff regardless of real today."""
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            store.save_pending(
                [
                    {
                        "run_id": "test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-20",
                        "orders": [],
                    }
                ]
            )
            provider = _RecordingProvider()

            # as_of < execute_after → batch retained
            simulator.execute_due_orders(config, store, provider, as_of=date(2026, 5, 19))
            self.assertEqual(len(store.load_pending()), 1)

            # as_of >= execute_after → batch consumed (orders empty so nothing to fill)
            simulator.execute_due_orders(config, store, provider, as_of=date(2026, 5, 20))
            self.assertEqual(len(store.load_pending()), 0)

    def test_data_root_overrides_store(self) -> None:
        """When data_root is provided, simulator writes to that root, not store.data_dir."""
        with tempfile.TemporaryDirectory() as outer, tempfile.TemporaryDirectory() as inner:
            config = _base_config()
            outer_store = PortfolioStore(outer)
            outer_store.initialize(config)
            # Seed inner with its own state
            inner_store = PortfolioStore(inner)
            inner_store.initialize(config)
            inner_store.save_pending(
                [
                    {
                        "run_id": "inner-test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-18",
                        "orders": [],
                    }
                ]
            )

            provider = _RecordingProvider()
            simulator.execute_due_orders(
                config,
                outer_store,
                provider,
                as_of=date(2026, 5, 18),
                data_root=Path(inner),
            )

            # The inner pending batch (empty orders) should be consumed since
            # data_root overrode outer_store. outer's pending should be untouched.
            self.assertEqual(len(PortfolioStore(inner).load_pending()), 0)
            # outer was initialized with empty pending and we did NOT pass outer
            # to inner-driven call, so outer pending stays as it was.
            self.assertEqual(len(outer_store.load_pending()), 0)

    def test_market_data_root_overrides_provider_cache(self) -> None:
        """When market_data_root is provided, the provider's cache_dir attribute is updated."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            provider.cache_dir = Path("/old/cache")

            simulator.execute_due_orders(
                config,
                store,
                provider,
                as_of=date(2026, 5, 18),
                market_data_root=Path(cache),
            )

            self.assertEqual(provider.cache_dir, Path(cache))

    def test_default_behavior_unchanged_when_kwargs_omitted(self) -> None:
        """Existing call signature (str as_of, no new kwargs) must still work."""
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            store.save_pending(
                [
                    {
                        "run_id": "test",
                        "account_id": "acc",
                        "signal_date": "2026-05-18",
                        "execute_after": "2026-05-18",
                        "orders": [],
                    }
                ]
            )
            provider = _RecordingProvider()

            # Same call as the existing tests in test_simulation_correctness.py
            trades = simulator.execute_due_orders(config, store, provider, as_of="2026-05-18")
            self.assertEqual(trades, [])
            self.assertEqual(len(store.load_pending()), 0)


class UpdateNavClockInjectionTests(unittest.TestCase):
    def test_accepts_as_of_as_date_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            target = date(2026, 5, 18)

            rows = simulator.update_nav(config, store, provider, as_of=target)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2026-05-18")

    def test_uses_today_when_as_of_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()

            mocked_today = date(2026, 5, 18)
            with mock.patch("stock_analyze.simulator.date") as mocked_date:
                mocked_date.today.return_value = mocked_today
                mocked_date.side_effect = lambda *a, **kw: date(*a, **kw)
                rows = simulator.update_nav(config, store, provider)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2026-05-18")

    def test_data_root_overrides_store(self) -> None:
        with tempfile.TemporaryDirectory() as outer, tempfile.TemporaryDirectory() as inner:
            config = _base_config()
            outer_store = PortfolioStore(outer)
            outer_store.initialize(config)
            inner_store = PortfolioStore(inner)
            inner_store.initialize(config)
            provider = _RecordingProvider()

            simulator.update_nav(
                config,
                outer_store,
                provider,
                as_of=date(2026, 5, 18),
                data_root=Path(inner),
            )

            # nav written to inner, not outer
            inner_nav_path = Path(inner) / "daily_nav.csv"
            self.assertTrue(inner_nav_path.exists())
            outer_nav_path = Path(outer) / "daily_nav.csv"
            self.assertFalse(outer_nav_path.exists())

    def test_market_data_root_overrides_provider_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            provider.cache_dir = Path("/old/cache")

            simulator.update_nav(
                config,
                store,
                provider,
                as_of=date(2026, 5, 18),
                market_data_root=Path(cache),
            )

            self.assertEqual(provider.cache_dir, Path(cache))


class GenerateRebalanceOrdersClockInjectionTests(unittest.TestCase):
    def _stub_signal(self):
        # Patch target: build_signals returns an object with .candidates / .factor_table / .warnings
        from types import SimpleNamespace

        return SimpleNamespace(
            candidates=pd.DataFrame(),
            factor_table=pd.DataFrame(),
            warnings=[],
        )

    def test_accepts_as_of_as_date_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            target = date(2026, 5, 18)

            with mock.patch("stock_analyze.simulator.build_signals", return_value=self._stub_signal()):
                batches = simulator.generate_rebalance_orders(config, store, provider, as_of=target)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["signal_date"], "2026-05-18")

    def test_uses_today_when_as_of_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()

            mocked_today = date(2026, 5, 18)
            with mock.patch("stock_analyze.simulator.date") as mocked_date, \
                 mock.patch("stock_analyze.simulator.build_signals", return_value=self._stub_signal()):
                mocked_date.today.return_value = mocked_today
                mocked_date.side_effect = lambda *a, **kw: date(*a, **kw)
                batches = simulator.generate_rebalance_orders(config, store, provider)

            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["signal_date"], "2026-05-18")

    def test_data_root_overrides_store(self) -> None:
        with tempfile.TemporaryDirectory() as outer, tempfile.TemporaryDirectory() as inner:
            config = _base_config()
            outer_store = PortfolioStore(outer)
            outer_store.initialize(config)
            inner_store = PortfolioStore(inner)
            inner_store.initialize(config)
            provider = _RecordingProvider()

            with mock.patch("stock_analyze.simulator.build_signals", return_value=self._stub_signal()):
                simulator.generate_rebalance_orders(
                    config,
                    outer_store,
                    provider,
                    as_of=date(2026, 5, 18),
                    data_root=Path(inner),
                )

            # Pending written to inner, not outer
            inner_pending = PortfolioStore(inner).load_pending()
            outer_pending = outer_store.load_pending()
            self.assertEqual(len(inner_pending), 1)
            self.assertEqual(len(outer_pending), 0)

    def test_market_data_root_overrides_provider_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cache:
            config = _base_config()
            store = PortfolioStore(tmp)
            store.initialize(config)
            provider = _RecordingProvider()
            provider.cache_dir = Path("/old/cache")

            with mock.patch("stock_analyze.simulator.build_signals", return_value=self._stub_signal()):
                simulator.generate_rebalance_orders(
                    config,
                    store,
                    provider,
                    as_of=date(2026, 5, 18),
                    market_data_root=Path(cache),
                )

            self.assertEqual(provider.cache_dir, Path(cache))


if __name__ == "__main__":
    unittest.main()
