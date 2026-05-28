"""End-to-end: ``strategy.build_signals`` resolves broadcast factors from
the per-agent sentiment CSV and applies a uniform score shift.

This is the integration that wires B's pieces together:

    record-sentiment CLI  →  data/<agent>/alt_factors/market_sentiment.csv
                                          ↓
       strategy.build_signals(config)  →  _resolve_broadcast_values
                                          ↓
                          factor_pipeline.load_broadcast_factor
                                          ↓
                          process_factors(broadcast_values=...)
                                          ↓
       signal.candidates with score column reflecting broadcast shift

Without this glue the broadcast factor code is dead — build_signals would
silently pass broadcast_values=None and the broadcast factors contribute 0.
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pandas as pd

from stock_analyze.markets.a_share import strategy
from stock_analyze.markets.a_share.alt_factors import sentiment


def _three_stock_universe() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["000001", "000002", "000003"],
        "name": ["平安银行", "万科A", "招商银行"],
        "latest_price": [12.0, 20.0, 35.0],
        "pe": [5.5, 12.0, 8.0],
        "pb": [1.1, 1.8, 1.5],
        "market_cap_yi": [2500.0, 2800.0, 4000.0],
    })


def _stub_provider(universe: pd.DataFrame) -> MagicMock:
    provider = MagicMock()
    provider.universe.return_value = universe

    def basic_info(code):
        return {"listing_date": "2010-01-01", "industry": "银行",
                "name": "stub", "market_cap_yi": 1000.0}

    def valuation_metrics(code):
        return {"pe": 8.0, "pb": 1.3}

    def financial_metrics(code, as_of=None):
        return {"roe": 0.1, "gross_margin": 0.3, "debt_ratio": 0.4,
                "net_profit_growth": 0.05}

    def price_snapshot(code, as_of=None, spot_row=None):
        snap = MagicMock()
        snap.momentum_20 = 0.02
        snap.momentum_60 = 0.05
        snap.low_volatility_60 = 0.01
        snap.avg_amount_20 = 1e9
        snap.paused = False
        snap.warning = ""
        snap.close = 12.0
        return snap

    def dividend_yield(code, as_of=None):
        return 0.04

    provider.basic_info.side_effect = basic_info
    provider.valuation_metrics.side_effect = valuation_metrics
    provider.financial_metrics.side_effect = financial_metrics
    provider.price_snapshot.side_effect = price_snapshot
    provider.dividend_yield.side_effect = dividend_yield
    return provider


def _base_config(factors: dict) -> dict:
    return {
        "agent_id": "claude",
        "strategy_id": "e2e",
        "factors": factors,
        "factor_processing": {
            "enabled": True,
            "winsorize_lower": 0.01, "winsorize_upper": 0.99,
            "neutralize_industry": False,
            "min_factor_coverage": 0.1,
        },
        "filters": {
            "exclude_st": True, "max_fetch_candidates": 10,
            "min_listing_days": 0, "min_pe": 0,
            "min_avg_amount_20": 0, "min_market_cap_yi": 0,
            "max_market_cap_yi": 1_000_000,
            "require_fields": [], "fallback_require_fields": [],
        },
    }


class BuildSignalsWithSentimentBroadcastTests(unittest.TestCase):
    """Verify the full record → build_signals chain shifts scores."""

    def test_broadcast_shift_applied_after_recording(self):
        """Same overlay (including broadcast factor), same universe; before
        recording, broadcast contributes 0; after recording, broadcast adds
        sign × weight × value uniformly to every score."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            universe = _three_stock_universe()
            account = {"id": "test", "scope": "hs300", "top_n": 3}

            # SAME overlay used in both runs — only the CSV row differs
            cfg = _base_config({
                "pe": {"weight": 0.9, "direction": "low"},
                "claude_market_sentiment_1w": {
                    "weight": 0.1, "direction": "high",
                },
            })

            # Run 1: no sentiment recorded → broadcast contributes 0
            baseline = strategy.build_signals(
                cfg, account, _stub_provider(universe),
                as_of="2026-05-25", repo_root=repo,
            )
            baseline_scores = baseline.candidates.set_index("code")["score"]

            # Record a sentiment
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.50, confidence=0.8,
                drivers=["x"], sources=[],
                llm_model="claude-sonnet-4.5", prompt_version="v1",
                repo_root=repo,
            )

            # Run 2: SAME overlay; only the CSV row was added
            sig_b = strategy.build_signals(
                cfg, account, _stub_provider(universe),
                as_of="2026-05-25", repo_root=repo,
            )
            shifted_scores = sig_b.candidates.set_index("code")["score"]

            # Expected shift = sign(+1) × weight(0.1) × score(0.50) × confidence(0.8) = +0.04
            common_codes = set(baseline_scores.index) & set(shifted_scores.index)
            self.assertGreaterEqual(len(common_codes), 2)
            shifts = (shifted_scores - baseline_scores).dropna()
            self.assertAlmostEqual(
                shifts.std(), 0.0, places=3,
                msg=f"broadcast shift was not uniform: {shifts.to_dict()}",
            )
            self.assertAlmostEqual(
                shifts.mean(), 0.1 * 0.50 * 0.8, places=3,
                msg=f"broadcast shift magnitude wrong: mean={shifts.mean()}",
            )

    def test_no_sentiment_recorded_contributes_zero(self):
        """When the overlay has a broadcast factor but no CSV row exists, the
        factor contributes 0 (does not raise)."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            universe = _three_stock_universe()
            provider = _stub_provider(universe)
            account = {"id": "test", "scope": "hs300", "top_n": 3}

            with_broadcast = _base_config({
                "pe": {"weight": 0.9, "direction": "low"},
                "claude_market_sentiment_1w": {
                    "weight": 0.1, "direction": "high",
                },
            })
            result = strategy.build_signals(
                with_broadcast, account, provider,
                as_of="2026-05-25", repo_root=repo,
            )
            # 3 candidates produced; scores are finite numbers
            self.assertEqual(len(result.candidates), 3)
            self.assertTrue(result.candidates["score"].notna().all())

    def test_sentiment_direction_low_subtracts(self):
        """direction=low on a broadcast factor → subtracts from score."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            universe = _three_stock_universe()
            provider = _stub_provider(universe)
            account = {"id": "test", "scope": "hs300", "top_n": 3}

            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=1.0, confidence=0.8,
                drivers=["x"], sources=[],
                llm_model="m", prompt_version="v1",
                repo_root=repo,
            )

            cfg_pos = _base_config({
                "pe": {"weight": 0.9, "direction": "low"},
                "claude_market_sentiment_1w": {"weight": 0.1, "direction": "high"},
            })
            cfg_neg = _base_config({
                "pe": {"weight": 0.9, "direction": "low"},
                "claude_market_sentiment_1w": {"weight": 0.1, "direction": "low"},
            })
            sig_pos = strategy.build_signals(
                cfg_pos, account, provider,
                as_of="2026-05-25", repo_root=repo,
            )
            sig_neg = strategy.build_signals(
                cfg_neg, account, provider,
                as_of="2026-05-25", repo_root=repo,
            )
            # Confidence-weighted effective value = 1.0 (score) × 0.8 (conf) = 0.8
            # Pos direction adds +0.1×0.8=+0.08, neg adds -0.08; difference = 0.16
            delta = (sig_pos.candidates.set_index("code")["score"]
                      - sig_neg.candidates.set_index("code")["score"])
            self.assertAlmostEqual(delta.mean(), 2 * 0.1 * 1.0 * 0.8, places=2)

    def test_cross_agent_factor_in_overlay_returns_none_for_value(self):
        """If claude's overlay had codex_market_sentiment_1w (caught by
        overlay_guard normally), build_signals would resolve None (since
        agent_id is claude, factor prefix is codex) and contribute 0.

        Tests the defensive behavior even though overlay_guard would catch
        this upstream.
        """
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Pre-populate codex's sentiment csv
            sentiment.record_market_sentiment(
                agent_id="codex", week_end=date(2026, 5, 22),
                score=1.0, confidence=0.8, drivers=["x"], sources=[],
                llm_model="m", prompt_version="v1", repo_root=repo,
            )
            universe = _three_stock_universe()
            provider = _stub_provider(universe)
            account = {"id": "test", "scope": "hs300", "top_n": 3}
            # claude config referencing codex's factor (bypassing guard)
            cfg = _base_config({
                "pe": {"weight": 0.9, "direction": "low"},
                "codex_market_sentiment_1w": {
                    "weight": 0.1, "direction": "high",
                },
            })
            result = strategy.build_signals(
                cfg, account, provider,
                as_of="2026-05-25", repo_root=repo,
            )
            # No score blowup — codex's value is not loaded (different agent_id),
            # broadcast contributes 0
            self.assertTrue(result.candidates["score"].notna().all())


if __name__ == "__main__":
    unittest.main()
