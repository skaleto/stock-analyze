"""Tests for Phase 3 sector-level sentiment.

Covers: the record/load CSV layer, overlay_guard whitelisting + cross-agent
rule, factor_pipeline skip-neutralization, and the key behavioural proof —
sector sentiment changes cross-sectional ranking (unlike the broadcast
market factor, which doesn't).
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.factor_pipeline import (
    is_broadcast_factor,
    is_sector_sentiment_factor,
    process_factors,
)
from stock_analyze.markets.a_share.alt_factors import sentiment as alt_sent
from stock_analyze.overlay_guard import (
    OverlayCrossAgentFactor,
    validate_factor_name,
    AVAILABLE_FACTORS_BY_MARKET,
)


# ---------------------------------------------------------------------------
# CSV record/load layer
# ---------------------------------------------------------------------------


class SectorSentimentStoreTests(unittest.TestCase):
    def test_record_then_load_round_trip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            n = alt_sent.record_sector_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                sectors=[
                    {"industry": "银行", "score": 0.2, "confidence": 0.6},
                    {"industry": "半导体", "score": 0.5, "confidence": 0.8},
                ],
                llm_model="m", prompt_version="sector_v1", repo_root=root,
            )
            self.assertEqual(n, 2)
            rows = alt_sent.load_sector_sentiment("claude", root)
            self.assertEqual(len(rows), 2)
            self.assertEqual({r.industry for r in rows}, {"银行", "半导体"})

    def test_latest_returns_score_times_confidence_map(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 15),
                [{"industry": "银行", "score": 1.0, "confidence": 0.5}],
                "m", "v", root,
            )
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "银行", "score": 0.4, "confidence": 0.5},
                 {"industry": "半导体", "score": 0.8, "confidence": 1.0}],
                "m", "v", root,
            )
            latest = alt_sent.load_latest_sector_sentiment("claude", date(2026, 5, 25), root)
            # only the 2026-05-22 week; values are score*confidence
            self.assertAlmostEqual(latest["银行"], 0.4 * 0.5)
            self.assertAlmostEqual(latest["半导体"], 0.8 * 1.0)
            self.assertNotIn("2026-05-15", str(latest))  # older week not mixed in

    def test_point_in_time_excludes_future_weeks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "银行", "score": 0.4, "confidence": 1.0}], "m", "v", root,
            )
            # as_of before the only week → empty
            self.assertEqual(
                alt_sent.load_latest_sector_sentiment("claude", date(2026, 5, 1), root), {}
            )

    def test_duplicate_week_rejected_without_force(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "银行", "score": 0.4, "confidence": 1.0}], "m", "v", root,
            )
            with self.assertRaises(alt_sent.DuplicateSentimentEntry):
                alt_sent.record_sector_sentiment(
                    "claude", date(2026, 5, 22),
                    [{"industry": "银行", "score": 0.1, "confidence": 1.0}], "m", "v", root,
                )

    def test_force_replaces_week(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "银行", "score": 0.4, "confidence": 1.0}], "m", "v", root,
            )
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "白酒", "score": -0.2, "confidence": 1.0}],
                "m", "v", root, force=True,
            )
            latest = alt_sent.load_latest_sector_sentiment("claude", date(2026, 5, 25), root)
            self.assertEqual(set(latest), {"白酒"})  # 银行 replaced

    def test_validation_rejects_out_of_range_and_dups(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                alt_sent.record_sector_sentiment(
                    "claude", date(2026, 5, 22),
                    [{"industry": "银行", "score": 2.0, "confidence": 1.0}], "m", "v", root,
                )
            with self.assertRaises(ValueError):
                alt_sent.record_sector_sentiment(
                    "claude", date(2026, 5, 22),
                    [{"industry": "银行", "score": 0.1, "confidence": 0.5},
                     {"industry": "银行", "score": 0.2, "confidence": 0.5}],
                    "m", "v", root,
                )


# ---------------------------------------------------------------------------
# Factor classification + overlay guard
# ---------------------------------------------------------------------------


class FactorClassificationTests(unittest.TestCase):
    def test_sector_factor_is_not_broadcast(self):
        self.assertTrue(is_sector_sentiment_factor("claude_sector_sentiment"))
        self.assertFalse(is_broadcast_factor("claude_sector_sentiment"))
        # broadcast market factor is not a sector factor
        self.assertFalse(is_sector_sentiment_factor("claude_market_sentiment_1w"))
        self.assertTrue(is_broadcast_factor("claude_market_sentiment_1w"))

    def test_whitelisted_for_a_share(self):
        wl = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        self.assertIn("claude_sector_sentiment", wl)
        self.assertIn("codex_sector_sentiment", wl)

    def test_own_sector_factor_allowed(self):
        wl = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        validate_factor_name("claude_sector_sentiment", "claude", factors_whitelist=wl)

    def test_cross_agent_sector_factor_rejected(self):
        wl = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        with self.assertRaises(OverlayCrossAgentFactor):
            validate_factor_name("codex_sector_sentiment", "claude", factors_whitelist=wl)


# ---------------------------------------------------------------------------
# The behavioural proof: sector sentiment changes ranking
# ---------------------------------------------------------------------------


class SectorSentimentAffectsRankingTests(unittest.TestCase):
    def _candidates(self):
        # 4 stocks, 2 industries, identical PE so PE alone gives a flat tie.
        return pd.DataFrame({
            "code": ["000001", "000002", "600000", "600519"],
            "industry": ["银行", "银行", "白酒", "白酒"],
            "pe": [10.0, 10.0, 10.0, 10.0],
        })

    def test_skips_industry_neutralization(self):
        """A sector factor must NOT be industry-neutralized (else it zeroes)."""
        frame = self._candidates()
        # 银行 bullish (+0.5), 白酒 bearish (-0.5)
        frame["claude_sector_sentiment"] = [0.5, 0.5, -0.5, -0.5]
        scored, _ = process_factors(
            frame,
            {"claude_sector_sentiment": {"weight": 1.0, "direction": "high"}},
            {"neutralize_industry": True, "min_factor_coverage": 0.0},
        )
        by_code = dict(zip(scored["code"], scored["score"]))
        # 银行 stocks (bullish) must outrank 白酒 stocks (bearish).
        self.assertGreater(by_code["000001"], by_code["600000"])
        # If neutralization had NOT been skipped, both industries would
        # demean to ~0 and scores would be ~equal — assert they're clearly apart.
        self.assertGreater(by_code["000001"] - by_code["600000"], 0.5)

    def test_broadcast_factor_would_not_change_ranking(self):
        """Contrast: a broadcast factor shifts all scores equally (no reranking)."""
        frame = self._candidates()
        frame["momentum_20"] = [0.1, 0.2, 0.3, 0.4]  # the only differentiator
        scored_base, _ = process_factors(
            frame, {"momentum_20": {"weight": 1.0, "direction": "high"}},
            {"neutralize_industry": False, "min_factor_coverage": 0.0},
        )
        scored_bc, _ = process_factors(
            frame,
            {"momentum_20": {"weight": 1.0, "direction": "high"},
             "claude_market_sentiment_1w": {"weight": 0.5, "direction": "high"}},
            {"neutralize_industry": False, "min_factor_coverage": 0.0},
            broadcast_values={"claude_market_sentiment_1w": 0.8},
        )
        # Broadcast shifts every score by the same constant → identical order.
        self.assertEqual(
            list(scored_base.sort_values("score")["code"]),
            list(scored_bc.sort_values("score")["code"]),
        )


class StrategyColumnGlueTests(unittest.TestCase):
    """strategy._resolve_sector_sentiment_column maps industry → score column."""

    def test_builds_column_from_industry_map(self):
        from stock_analyze.markets.a_share.strategy import _resolve_sector_sentiment_column
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            alt_sent.record_sector_sentiment(
                "claude", date(2026, 5, 22),
                [{"industry": "银行", "score": 0.4, "confidence": 1.0},
                 {"industry": "白酒", "score": -0.2, "confidence": 1.0}],
                "m", "v", root,
            )
            candidates = pd.DataFrame({
                "code": ["1", "2", "3"],
                "industry": ["银行", "白酒", "未知行业"],
            })
            config = {
                "agent_id": "claude",
                "factors": {"claude_sector_sentiment": {"weight": 0.1, "direction": "high"}},
            }
            name, col = _resolve_sector_sentiment_column(
                config, candidates, "2026-05-25", root,
            )
            self.assertEqual(name, "claude_sector_sentiment")
            self.assertAlmostEqual(col.iloc[0], 0.4)   # 银行
            self.assertAlmostEqual(col.iloc[1], -0.2)  # 白酒
            self.assertTrue(pd.isna(col.iloc[2]))      # 未知行业 → NaN

    def test_no_sector_factor_returns_none(self):
        from stock_analyze.markets.a_share.strategy import _resolve_sector_sentiment_column
        with TemporaryDirectory() as tmp:
            name, col = _resolve_sector_sentiment_column(
                {"agent_id": "claude", "factors": {"pe": {"weight": 1.0, "direction": "low"}}},
                pd.DataFrame({"code": ["1"], "industry": ["银行"]}),
                "2026-05-25", Path(tmp),
            )
            self.assertIsNone(name)
            self.assertIsNone(col)


if __name__ == "__main__":
    unittest.main()
