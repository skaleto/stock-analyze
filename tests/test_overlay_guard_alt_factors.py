"""Tests for overlay_guard's alt-factor support and cross-agent isolation (Task 6 of B)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import overlay_guard


def _valid_overlay(factors: dict) -> dict:
    return {
        "agent_id": "claude",
        "strategy_id": "test",
        "name": "Test",
        "factors": factors,
        "factor_processing": {
            "winsorize_lower": 0.01, "winsorize_upper": 0.99,
            "neutralize_industry": True, "min_factor_coverage": 0.6,
        },
        "portfolio_controls": {
            "max_industry_weight": 0.3, "hold_buffer_pct": 0.5,
            "max_holding_days": 365, "industry_unclassified_label": "未分类",
        },
        "filters": {
            "exclude_st": True, "max_fetch_candidates": 250,
            "min_listing_days": 365, "min_pe": 0,
            "min_avg_amount_20": 0, "min_market_cap_yi": 0,
            "max_market_cap_yi": 100000,
            "require_fields": [], "fallback_require_fields": [],
        },
    }


class ValidateFactorNameTests(unittest.TestCase):
    def test_classic_factor_validates(self):
        overlay_guard.validate_factor_name("pe", agent_id="claude")
        overlay_guard.validate_factor_name("roe", agent_id="codex")

    def test_own_alt_factor_validates(self):
        overlay_guard.validate_factor_name(
            "claude_market_sentiment_1w", agent_id="claude")
        overlay_guard.validate_factor_name(
            "codex_market_sentiment_1w", agent_id="codex")

    def test_other_agent_alt_factor_rejected(self):
        with self.assertRaises(overlay_guard.OverlayCrossAgentFactor) as ctx:
            overlay_guard.validate_factor_name(
                "codex_market_sentiment_1w", agent_id="claude")
        self.assertIn("codex_market_sentiment_1w", str(ctx.exception))
        self.assertIn("claude", str(ctx.exception))

        with self.assertRaises(overlay_guard.OverlayCrossAgentFactor):
            overlay_guard.validate_factor_name(
                "claude_market_sentiment_1w", agent_id="codex")

    def test_unknown_factor_rejected(self):
        with self.assertRaises(overlay_guard.OverlayUnknownFactor):
            overlay_guard.validate_factor_name(
                "made_up_factor", agent_id="claude")

    def test_unknown_factor_distinct_from_cross_agent(self):
        try:
            overlay_guard.validate_factor_name("totally_random", agent_id="claude")
            self.fail("expected exception")
        except overlay_guard.OverlayCrossAgentFactor:
            self.fail("should be OverlayUnknownFactor, not OverlayCrossAgentFactor")
        except overlay_guard.OverlayUnknownFactor:
            pass


class FullOverlayValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "configs/agents").mkdir(parents=True)
        (self.root / "configs/competition.yaml").write_text(json.dumps({
            "competition_id": "x",
            "start_date": "2026-01-01",
            "initial_cash": 100000,
            "accounts": [{"id": "main", "scope": "hs300", "benchmark": "000300",
                          "cash": 100000, "top_n": 50}],
            "schedule": {"execution": "n", "signal_day": "n", "rebalance": "n"},
            "trading": {"lot_size": 100, "commission_rate": 0.0003,
                         "min_commission": 5, "stamp_tax_rate": 0.0005,
                         "slippage_rate": 0.0, "max_single_weight": 0.05},
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_overlay_with_own_alt_factor_validates(self):
        overlay = _valid_overlay({
            "pe": {"weight": 0.5, "direction": "low"},
            "claude_market_sentiment_1w": {"weight": 0.5, "direction": "high"},
        })
        overlay_guard.validate("claude", overlay, repo_root=self.root)

    def test_overlay_with_cross_agent_alt_factor_rejected(self):
        overlay = _valid_overlay({
            "pe": {"weight": 0.5, "direction": "low"},
            "codex_market_sentiment_1w": {"weight": 0.5, "direction": "high"},
        })
        with self.assertRaises(overlay_guard.OverlayCrossAgentFactor):
            overlay_guard.validate("claude", overlay, repo_root=self.root)

    def test_overlay_with_classic_factor_still_works(self):
        overlay = _valid_overlay({
            "pe": {"weight": 0.5, "direction": "low"},
            "roe": {"weight": 0.5, "direction": "high"},
        })
        overlay_guard.validate("claude", overlay, repo_root=self.root)


if __name__ == "__main__":
    unittest.main()
