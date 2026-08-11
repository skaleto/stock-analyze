"""Tests for build_target_orders lot sizing and allocation-boundary safety.

Bug history (2026-05-26): under baseline (cash=¥500k per account, top_n=50,
max_single_weight=0.05), the original sizing logic produced 0 lots for any
stock priced > ¥100 (since equal-weight target=¥10k, 1 lot of a ¥111 stock
costs ¥11k > ¥10k → integer-divides to 0). This left 7-13 slots per agent
permanently empty whenever the strategy ranked high-priced stocks into top-50.

Tier 1: bump to 1 lot when 1 lot fits under max_single_weight cap.
The order materializer must never add a security outside the approved
allocation, even when a legacy caller still supplies ``fallback_pool``.
"""
from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.markets.a_share.simulator import build_target_orders


def _config(cash_per_account: int = 500_000, lot_size: int = 100,
             max_single_weight: float = 0.05) -> dict:
    return {
        "trading": {
            "lot_size": lot_size,
            "max_single_weight": max_single_weight,
        },
    }


def _account_state(cash: float, positions: dict | None = None) -> dict:
    return {"cash": cash, "positions": positions or {}}


def _selected(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _orders_by_code(orders: list[dict]) -> dict[str, dict]:
    return {o["code"]: o for o in orders}


class Tier1OneLotFallbackTests(unittest.TestCase):
    """1 lot fits under 5% cap → bump from 0 to lot_size (not leave empty)."""

    def test_price_under_target_budget_buys_multiple_lots(self):
        """Sanity baseline: cheap stock buys 2 lots when target=10k and 1 lot=¥4k."""
        selected = _selected([
            {"code": "000001", "name": "A", "industry": "X",
             "latest_price": 40.0, "score": 1.0},
        ])
        # total = 500k, top_n=1 → target_value = min(500k, 25k) = 25k cap
        # but top_n=1 means target_value = 500k/1 = 500k -> capped to 25k
        # actually with len(selected)=1, top_n=1, target = min(500k/1, 25k) = 25k
        # 25k // (40*100) * 100 = 25000//4000 = 6 → 6*100 = 600 shares
        orders = build_target_orders(_config(), _account_state(500_000), selected)
        self.assertEqual(orders[0]["target_shares"], 600)

    def test_price_above_target_budget_under_5pct_cap_buys_one_lot(self):
        """¥111 stock: 1 lot=¥11k > equal-weight target ¥10k, < ¥25k cap → 1 lot."""
        # Construct 50 selected so target = 500k/50 = 10k
        rows = [{"code": f"{i:06d}", "name": f"S{i}", "industry": "X",
                  "latest_price": 50.0, "score": 1.0 - i*0.01} for i in range(49)]
        rows.append({"code": "300661", "name": "圣邦股份", "industry": "半导体",
                      "latest_price": 111.0, "score": 0.5})
        selected = _selected(rows)
        orders = build_target_orders(_config(), _account_state(500_000), selected)
        by_code = _orders_by_code(orders)
        # Without Tier 1 fix: 300661 would get 0 shares
        # With Tier 1 fix: 300661 gets 100 shares
        self.assertEqual(by_code["300661"]["target_shares"], 100,
                          "Tier 1: 1 lot at ¥111 (=¥11k) fits under ¥25k cap")

    def test_price_above_5pct_cap_stays_zero(self):
        """¥300 stock: 1 lot=¥30k > ¥25k cap → still 0 shares (correct exclusion)."""
        rows = [{"code": f"{i:06d}", "name": f"S{i}", "industry": "X",
                  "latest_price": 50.0, "score": 1.0 - i*0.01} for i in range(49)]
        rows.append({"code": "688008", "name": "澜起科技", "industry": "半导体",
                      "latest_price": 300.0, "score": 0.5})
        selected = _selected(rows)
        orders = build_target_orders(_config(), _account_state(500_000), selected)
        by_code = _orders_by_code(orders)
        # 300×100=30k > 25k cap → no order produced for this code
        self.assertNotIn("688008", by_code,
                          "Stocks above 5% cap stay excluded (no order)")


class AllocationBoundaryTests(unittest.TestCase):
    """Order materialization cannot re-run selection after portfolio controls."""

    def test_fallback_pool_cannot_add_unallocated_securities(self):
        """Unbuyable allocations stay empty instead of bypassing controls.

        Note: build_target_orders only emits orders for codes where
        target_shares != current_shares. Codes with target=0 AND no current
        position do NOT produce orders, so they won't appear in `by_code`.
        """
        # top_n = 3, target = min(500k/3, 25k) = 25k
        # ¥300 stock: 25k // (300*100) = 0; 1 lot ¥30k > ¥25k cap → still 0 → no order
        # ¥50 stock:  25k // (50*100)  = 5 → 500 shares
        selected = _selected([
            {"code": "999001", "name": "Too1", "industry": "X",
             "latest_price": 300.0, "score": 0.9},
            {"code": "999002", "name": "Too2", "industry": "Y",
             "latest_price": 300.0, "score": 0.8},
            {"code": "999003", "name": "OK3",  "industry": "Z",
             "latest_price": 50.0, "score": 0.7},
        ])
        fallback_pool = _selected([
            {"code": "999001", "name": "Too1", "industry": "X",
             "latest_price": 300.0, "score": 0.9},
            {"code": "999002", "name": "Too2", "industry": "Y",
             "latest_price": 300.0, "score": 0.8},
            {"code": "999003", "name": "OK3", "industry": "Z",
             "latest_price": 50.0, "score": 0.7},
            {"code": "999004", "name": "OK4", "industry": "W",
             "latest_price": 40.0, "score": 0.6},
            {"code": "999005", "name": "OK5", "industry": "V",
             "latest_price": 60.0, "score": 0.5},
        ])
        orders = build_target_orders(
            _config(), _account_state(500_000), selected,
            fallback_pool=fallback_pool,
        )
        by_code = _orders_by_code(orders)
        # Only the security approved by allocation and affordable is emitted.
        codes_with_shares = {c for c, o in by_code.items() if o["target_shares"] > 0}
        self.assertEqual(codes_with_shares, {"999003"})
        self.assertTrue({"999001", "999002", "999004", "999005"}.isdisjoint(by_code))

    def test_legacy_fallback_argument_is_ignored(self):
        # top_n=2, target = min(500k/2, 25k) = 25k
        # ¥30 stock: 25k // (30*100) = 8 → 800 shares
        selected = _selected([
            {"code": "999001", "name": "Too", "industry": "X",
             "latest_price": 300.0, "score": 0.9},
            {"code": "999002", "name": "OK",  "industry": "Y",
             "latest_price": 50.0, "score": 0.8},
        ])
        fallback_pool = _selected([
            {"code": "999001", "name": "Too", "industry": "X",
             "latest_price": 300.0, "score": 0.9},  # already in selected → skip
            {"code": "999002", "name": "OK", "industry": "Y",
             "latest_price": 50.0, "score": 0.8},   # already in selected → skip
            {"code": "999003", "name": "Fill", "industry": "Z",
             "latest_price": 30.0, "score": 0.5},
        ])
        orders = build_target_orders(
            _config(), _account_state(500_000), selected,
            fallback_pool=fallback_pool,
        )
        by_code = _orders_by_code(orders)
        self.assertNotIn("999003", by_code)
        self.assertNotIn("999001", by_code)
        self.assertEqual(set(by_code), {"999002"})

    def test_unallocated_candidate_never_receives_fallback_reason(self):
        selected = _selected([
            {"code": "999001", "name": "Too", "industry": "X",
             "latest_price": 300.0, "score": 0.9, "score_detail": "pe:1.0:0.5"},
        ])
        fallback_pool = _selected([
            {"code": "999001", "name": "Too", "industry": "X",
             "latest_price": 300.0, "score": 0.9, "score_detail": "pe:1.0:0.5"},
            {"code": "999002", "name": "Fill", "industry": "Y",
             "latest_price": 30.0, "score": 0.5, "score_detail": "roe:0.8:0.3"},
        ])
        orders = build_target_orders(
            _config(), _account_state(500_000), selected,
            fallback_pool=fallback_pool,
        )
        by_code = _orders_by_code(orders)
        self.assertNotIn("999002", by_code)

    def test_no_fallback_when_pool_is_none_backward_compat(self):
        """When fallback_pool=None (old callers), no skip-down occurs."""
        selected = _selected([
            {"code": "999001", "name": "Too", "industry": "X",
             "latest_price": 300.0, "score": 0.9},
            {"code": "999002", "name": "OK", "industry": "Y",
             "latest_price": 50.0, "score": 0.8},
        ])
        orders = build_target_orders(_config(), _account_state(500_000), selected)
        by_code = _orders_by_code(orders)
        # 999001 has 0 shares + no current position → not in orders (backward-compat)
        self.assertNotIn("999001", by_code)
        # 999002 produces a buy order
        self.assertIn("999002", by_code)
        self.assertGreater(by_code["999002"]["target_shares"], 0)
        # Only 999002 — fallback didn't run since pool=None
        self.assertEqual(set(by_code.keys()), {"999002"})


if __name__ == "__main__":
    unittest.main()
