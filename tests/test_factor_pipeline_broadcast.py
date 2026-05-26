"""Tests for ``factor_pipeline`` broadcast-factor support (Task 5 of B).

A broadcast factor is one whose value is a single scalar that is applied
uniformly to every candidate's composite score, bypassing the
winsorize/z-score/industry-neutralization pipeline (which requires
cross-sectional variance a constant lacks).
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze import factor_pipeline
from stock_analyze.alt_factors import sentiment


def _three_candidates() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "000001", "name": "A", "industry": "银行",
         "pe": 5.0, "roe": 0.08},
        {"code": "000002", "name": "B", "industry": "地产",
         "pe": 10.0, "roe": 0.12},
        {"code": "000003", "name": "C", "industry": "银行",
         "pe": 7.5, "roe": 0.10},
    ])


class IsBroadcastFactorTests(unittest.TestCase):
    def test_classic_factor_not_broadcast(self):
        self.assertFalse(factor_pipeline.is_broadcast_factor("pe"))
        self.assertFalse(factor_pipeline.is_broadcast_factor("roe"))
        self.assertFalse(factor_pipeline.is_broadcast_factor("momentum_60"))

    def test_agent_sentiment_factors_are_broadcast(self):
        self.assertTrue(
            factor_pipeline.is_broadcast_factor("claude_market_sentiment_1w"))
        self.assertTrue(
            factor_pipeline.is_broadcast_factor("codex_market_sentiment_1w"))

    def test_unknown_factor_not_broadcast(self):
        self.assertFalse(factor_pipeline.is_broadcast_factor("made_up"))
        self.assertFalse(factor_pipeline.is_broadcast_factor(""))


class LoadBroadcastFactorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_when_no_data(self):
        v = factor_pipeline.load_broadcast_factor(
            "claude", "claude_market_sentiment_1w",
            date(2026, 5, 25), repo_root=self.repo,
        )
        self.assertIsNone(v)

    def test_returns_latest_sentiment_when_set(self):
        sentiment.record_market_sentiment(
            agent_id="claude", week_end=date(2026, 5, 22),
            score=0.42, confidence=0.7, drivers=["x"],
            sources=[], llm_model="m", prompt_version="v1",
            repo_root=self.repo,
        )
        v = factor_pipeline.load_broadcast_factor(
            "claude", "claude_market_sentiment_1w",
            date(2026, 5, 25), repo_root=self.repo,
        )
        self.assertAlmostEqual(v, 0.42)

    def test_cross_agent_does_not_leak(self):
        """claude's broadcast factor reads claude's csv, codex's reads codex's."""
        sentiment.record_market_sentiment(
            agent_id="claude", week_end=date(2026, 5, 22),
            score=0.42, confidence=0.7, drivers=["x"],
            sources=[], llm_model="m", prompt_version="v1",
            repo_root=self.repo,
        )
        # codex has no rows
        v_codex = factor_pipeline.load_broadcast_factor(
            "codex", "codex_market_sentiment_1w",
            date(2026, 5, 25), repo_root=self.repo,
        )
        self.assertIsNone(v_codex)

    def test_unknown_broadcast_factor_returns_none(self):
        v = factor_pipeline.load_broadcast_factor(
            "claude", "claude_some_made_up_factor",
            date(2026, 5, 25), repo_root=self.repo,
        )
        self.assertIsNone(v)


class BroadcastFactorAppliedUniformlyTests(unittest.TestCase):
    """The core invariant: adding a broadcast factor shifts every candidate's
    score by the same constant."""

    def test_broadcast_shifts_all_scores_by_same_delta(self):
        candidates = _three_candidates()
        overlay_a = {
            "pe": {"weight": 0.5, "direction": "low"},
            "roe": {"weight": 0.5, "direction": "high"},
        }
        overlay_b = {
            "pe": {"weight": 0.45, "direction": "low"},
            "roe": {"weight": 0.45, "direction": "high"},
            "claude_market_sentiment_1w": {"weight": 0.10, "direction": "high"},
        }
        fp_cfg = {
            "winsorize_lower": 0.01, "winsorize_upper": 0.99,
            "neutralize_industry": False, "min_factor_coverage": 0.1,
        }
        scored_a, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay_a, fp_cfg,
        )
        scored_b, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay_b, fp_cfg,
            broadcast_values={"claude_market_sentiment_1w": 0.5},
        )
        # Order is preserved between calls (we index by code)
        scored_a = scored_a.set_index("code")
        scored_b = scored_b.set_index("code")
        # Pairwise differences in B equal pairwise differences in A:
        # broadcast shifts the WHOLE distribution by a constant.
        d_ab_a = scored_a.loc["000001", "score"] - scored_a.loc["000002", "score"]
        d_ab_b = scored_b.loc["000001", "score"] - scored_b.loc["000002", "score"]
        self.assertAlmostEqual(d_ab_a, d_ab_b, places=4)
        d_ac_a = scored_a.loc["000001", "score"] - scored_a.loc["000003", "score"]
        d_ac_b = scored_b.loc["000001", "score"] - scored_b.loc["000003", "score"]
        self.assertAlmostEqual(d_ac_a, d_ac_b, places=4)
        # And every candidate's score under B is A + same constant
        diff = scored_b["score"] - scored_a["score"]
        self.assertAlmostEqual(diff.std(), 0.0, places=4)

    def test_broadcast_value_none_contributes_zero(self):
        """When broadcast_values doesn't include the factor (or value is None),
        it should contribute 0 — not raise."""
        candidates = _three_candidates()
        overlay = {
            "pe": {"weight": 0.5, "direction": "low"},
            "roe": {"weight": 0.5, "direction": "high"},
            "claude_market_sentiment_1w": {"weight": 0.10, "direction": "high"},
        }
        fp_cfg = {
            "winsorize_lower": 0.01, "winsorize_upper": 0.99,
            "neutralize_industry": False, "min_factor_coverage": 0.1,
        }
        # broadcast_values=None
        scored, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay, fp_cfg,
        )
        self.assertIn("score", scored.columns)
        # Same with explicit None for the key
        scored_b, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay, fp_cfg,
            broadcast_values={"claude_market_sentiment_1w": None},
        )
        # Should not raise and produce comparable scores
        self.assertEqual(len(scored_b), len(candidates))

    def test_broadcast_factor_direction_low_subtracts(self):
        """direction=low means we subtract value (so positive sentiment lowers score)."""
        candidates = _three_candidates()
        overlay_neg = {
            "pe": {"weight": 0.9, "direction": "low"},
            "claude_market_sentiment_1w": {"weight": 0.1, "direction": "low"},
        }
        overlay_pos = {
            "pe": {"weight": 0.9, "direction": "low"},
            "claude_market_sentiment_1w": {"weight": 0.1, "direction": "high"},
        }
        fp_cfg = {
            "winsorize_lower": 0.01, "winsorize_upper": 0.99,
            "neutralize_industry": False, "min_factor_coverage": 0.1,
        }
        scored_neg, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay_neg, fp_cfg,
            broadcast_values={"claude_market_sentiment_1w": 1.0},
        )
        scored_pos, _ = factor_pipeline.process_factors(
            candidates.copy(), overlay_pos, fp_cfg,
            broadcast_values={"claude_market_sentiment_1w": 1.0},
        )
        # Direction high adds +0.1, direction low adds -0.1; difference = 0.2
        delta = scored_pos["score"].iloc[0] - scored_neg["score"].iloc[0]
        self.assertAlmostEqual(delta, 0.2, places=4)


if __name__ == "__main__":
    unittest.main()
