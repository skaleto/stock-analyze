from __future__ import annotations

import unittest

from stock_analyze import cli


class _Store:
    def __init__(self) -> None:
        self.pending = [{"run_id": "stale", "orders": [{"code": "000001"}]}]
        self.events: list[str] = []

    def load_pending(self):
        self.events.append("load_pending")
        return list(self.pending)

    def save_pending(self, rows):
        self.events.append(f"save_pending:{len(rows)}")
        self.pending = list(rows)


class _Provider:
    def persist_health(self) -> None:
        pass


class _Market:
    def __init__(self, *, fail_generation: bool = False) -> None:
        self.events: list[str] = []
        self.fail_generation = fail_generation

    def execute_due_orders(self, config, store, provider, *, as_of):
        self.events.append("execute")
        return [{"code": "000001"}]

    def update_nav(self, config, store, provider, *, as_of, notes):
        self.events.append("nav")
        return [{"account_id": "hs300"}]

    def generate_rebalance_orders(self, config, store, provider, *, as_of, run_id):
        self.events.append("decision")
        if self.fail_generation:
            raise RuntimeError("signal failed")
        rows = [{"run_id": run_id, "orders": [{"code": "000002"}]}]
        store.save_pending(rows)
        return rows

    def forbidden_weekly_generation(self, *args, **kwargs):
        raise AssertionError("weekly review must not generate orders")


class DailyDecisionCycleTests(unittest.TestCase):
    def test_counts_a_share_batches_and_flat_qdii_orders(self) -> None:
        count_orders = getattr(cli, "_count_generated_orders", None)
        self.assertTrue(callable(count_orders))

        self.assertEqual(
            count_orders([
                {"account_id": "hs300", "orders": [{"code": "000001"}]},
                {"account_id": "zz500", "orders": [{"code": "000002"}, {"code": "000003"}]},
            ]),
            3,
        )
        self.assertEqual(
            count_orders([
                {"code": "513100.SH", "side": "buy"},
                {"code": "159941.SZ", "side": "sell"},
            ]),
            2,
        )

    def test_executes_values_and_replaces_targets_in_order(self) -> None:
        cycle = getattr(cli, "_run_daily_decision_cycle", None)
        self.assertTrue(callable(cycle))
        store = _Store()
        market = _Market()

        trades, nav_rows, batches = cycle(
            {}, store, _Provider(), market,
            as_of="2026-07-13", run_id="run-daily-1",
        )

        self.assertEqual(market.events, ["execute", "nav", "decision"])
        self.assertEqual(store.events, ["load_pending", "save_pending:0", "save_pending:1"])
        self.assertEqual(trades[0]["code"], "000001")
        self.assertEqual(nav_rows[0]["account_id"], "hs300")
        self.assertEqual(batches[0]["run_id"], "run-daily-1")
        self.assertEqual(store.pending[0]["orders"][0]["code"], "000002")

    def test_restores_previous_targets_when_signal_generation_fails(self) -> None:
        cycle = getattr(cli, "_run_daily_decision_cycle", None)
        self.assertTrue(callable(cycle))
        store = _Store()

        with self.assertRaisesRegex(RuntimeError, "signal failed"):
            cycle(
                {}, store, _Provider(), _Market(fail_generation=True),
                as_of="2026-07-13", run_id="run-daily-2",
            )

        self.assertEqual(store.pending, [{"run_id": "stale", "orders": [{"code": "000001"}]}])

    def test_weekly_review_updates_nav_without_generating_targets(self) -> None:
        review = getattr(cli, "_run_weekly_review_state", None)
        self.assertTrue(callable(review))
        market = _Market()
        market.generate_rebalance_orders = market.forbidden_weekly_generation

        rows = review(
            {}, _Store(), _Provider(), market,
            market="cn_qdii_etf", as_of="2026-07-17",
        )

        self.assertEqual(market.events, ["nav"])
        self.assertEqual(rows, [{"account_id": "hs300"}])


if __name__ == "__main__":
    unittest.main()
