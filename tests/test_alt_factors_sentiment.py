"""Tests for ``stock_analyze.alt_factors.sentiment``.

Module-under-test persists one durable row per (agent_id, week_end) of
operator-supplied market-sentiment data, with strict validation and a
duplicate-rejection rule (force-overwrite available).
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze.markets.a_share.alt_factors import sentiment


class RecordMarketSentimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_happy_path_appends_one_row(self):
        sentiment.record_market_sentiment(
            agent_id="claude", week_end=date(2026, 5, 22),
            score=0.32, confidence=0.78,
            drivers=["AI 算力链回暖", "央行 MLF 偏鸽", "地产新政预期反复"],
            sources=["https://www.cls.cn/x"],
            llm_model="claude-sonnet-4.5",
            prompt_version="v1",
            repo_root=self.repo,
        )
        csv = self.repo / "data" / "claude" / "alt_factors" / "market_sentiment.csv"
        self.assertTrue(csv.exists())
        lines = csv.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("2026-05-22", lines[1])
        self.assertIn("0.3200", lines[1])
        self.assertIn("claude-sonnet-4.5", lines[1])

    def test_score_out_of_range_raises(self):
        with self.assertRaises(ValueError) as ctx:
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=1.5, confidence=0.5, drivers=["x"],
                sources=[], llm_model="x", prompt_version="v1",
                repo_root=self.repo,
            )
        self.assertIn("score", str(ctx.exception).lower())

    def test_score_negative_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=-2.0, confidence=0.5, drivers=["x"],
                sources=[], llm_model="x", prompt_version="v1",
                repo_root=self.repo,
            )

    def test_confidence_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.0, confidence=1.5, drivers=["x"],
                sources=[], llm_model="x", prompt_version="v1",
                repo_root=self.repo,
            )

    def test_drivers_empty_raises(self):
        with self.assertRaises(ValueError):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.0, confidence=0.5, drivers=[],
                sources=[], llm_model="x", prompt_version="v1",
                repo_root=self.repo,
            )

    def test_drivers_too_many_raises(self):
        with self.assertRaises(ValueError):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.0, confidence=0.5,
                drivers=["a", "b", "c", "d", "e", "f"],
                sources=[], llm_model="x", prompt_version="v1",
                repo_root=self.repo,
            )

    def test_duplicate_week_end_rejected_without_force(self):
        sentiment.record_market_sentiment(
            agent_id="claude", week_end=date(2026, 5, 22),
            score=0.32, confidence=0.78, drivers=["x"],
            sources=[], llm_model="m", prompt_version="v1",
            repo_root=self.repo,
        )
        with self.assertRaises(sentiment.DuplicateSentimentEntry):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.40, confidence=0.7, drivers=["x"],
                sources=[], llm_model="m", prompt_version="v1",
                repo_root=self.repo,
            )

    def test_force_overwrites_existing(self):
        for s in (0.32, 0.40):
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=s, confidence=0.78, drivers=["x"],
                sources=[], llm_model="m", prompt_version="v1",
                repo_root=self.repo, force=True,
            )
        rows = sentiment.load_sentiment_history("claude", repo_root=self.repo)
        match = [r for r in rows if r.week_end == date(2026, 5, 22)]
        self.assertEqual(len(match), 1)
        self.assertAlmostEqual(match[0].score, 0.40)


class LoadLatestMarketSentimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_if_no_data(self):
        v = sentiment.load_latest_market_sentiment(
            "claude", date(2026, 5, 22), repo_root=self.repo,
        )
        self.assertIsNone(v)

    def test_returns_most_recent_week_le_as_of(self):
        for week, score in [
            (date(2026, 5, 8), 0.10),
            (date(2026, 5, 15), 0.20),
            (date(2026, 5, 22), 0.30),
        ]:
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=week,
                score=score, confidence=0.7, drivers=["x"],
                sources=[], llm_model="m", prompt_version="v1",
                repo_root=self.repo,
            )
        v = sentiment.load_latest_market_sentiment(
            "claude", date(2026, 5, 20), repo_root=self.repo,
        )
        self.assertAlmostEqual(v, 0.20)

    def test_as_of_exactly_on_a_week_end_picks_that_row(self):
        sentiment.record_market_sentiment(
            agent_id="claude", week_end=date(2026, 5, 15),
            score=0.25, confidence=0.7, drivers=["x"],
            sources=[], llm_model="m", prompt_version="v1",
            repo_root=self.repo,
        )
        v = sentiment.load_latest_market_sentiment(
            "claude", date(2026, 5, 15), repo_root=self.repo,
        )
        self.assertAlmostEqual(v, 0.25)


class SentimentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for week, score in [
            (date(2026, 5, 8), 0.10),
            (date(2026, 5, 15), 0.20),
            (date(2026, 5, 22), 0.30),
        ]:
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=week,
                score=score, confidence=0.7, drivers=["x"],
                sources=[], llm_model="m", prompt_version="v1",
                repo_root=self.repo,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_returns_chronological_order(self):
        rows = sentiment.load_sentiment_history("claude", repo_root=self.repo)
        self.assertEqual([r.week_end for r in rows], [
            date(2026, 5, 8), date(2026, 5, 15), date(2026, 5, 22),
        ])

    def test_history_last_n_filter(self):
        rows = sentiment.load_sentiment_history(
            "claude", repo_root=self.repo, last_n=2,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].week_end, date(2026, 5, 15))
        self.assertEqual(rows[1].week_end, date(2026, 5, 22))

    def test_remove_sentiment_drops_one_row(self):
        sentiment.remove_sentiment(
            "claude", date(2026, 5, 15), repo_root=self.repo,
        )
        rows = sentiment.load_sentiment_history("claude", repo_root=self.repo)
        self.assertEqual([r.week_end for r in rows],
                          [date(2026, 5, 8), date(2026, 5, 22)])

    def test_remove_nonexistent_raises(self):
        with self.assertRaises(ValueError):
            sentiment.remove_sentiment(
                "claude", date(2099, 1, 1), repo_root=self.repo,
            )


if __name__ == "__main__":
    unittest.main()
