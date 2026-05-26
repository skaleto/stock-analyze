"""Tests for the ``sentiment-log`` CLI subcommand (Task 4 of B)."""
from __future__ import annotations

import io
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import cli
from stock_analyze.alt_factors import sentiment


class SentimentLogCLITests(unittest.TestCase):
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

    def test_list_shows_all_rows(self):
        with patch("sys.stdout", new=io.StringIO()) as captured:
            rc = cli.main([
                "sentiment-log",
                "--agent", "claude",
                "--repo-root", str(self.repo),
            ])
        self.assertEqual(rc, 0)
        output = captured.getvalue()
        self.assertIn("2026-05-08", output)
        self.assertIn("2026-05-15", output)
        self.assertIn("2026-05-22", output)

    def test_last_n_limits_output(self):
        with patch("sys.stdout", new=io.StringIO()) as captured:
            cli.main([
                "sentiment-log",
                "--agent", "claude",
                "--repo-root", str(self.repo),
                "--last", "2",
            ])
        output = captured.getvalue()
        self.assertNotIn("2026-05-08", output)
        self.assertIn("2026-05-15", output)
        self.assertIn("2026-05-22", output)

    def test_empty_log_prints_friendly_message(self):
        with TemporaryDirectory() as empty_tmp:
            with patch("sys.stdout", new=io.StringIO()) as captured:
                rc = cli.main([
                    "sentiment-log",
                    "--agent", "claude",
                    "--repo-root", empty_tmp,
                ])
            self.assertEqual(rc, 0)
            self.assertIn("no sentiment rows", captured.getvalue())

    def test_remove_drops_one_row(self):
        rc = cli.main([
            "sentiment-log",
            "--agent", "claude",
            "--repo-root", str(self.repo),
            "--remove",
            "--week-end", "2026-05-15",
        ])
        self.assertEqual(rc, 0)
        rows = sentiment.load_sentiment_history("claude", repo_root=self.repo)
        self.assertEqual([r.week_end for r in rows], [
            date(2026, 5, 8), date(2026, 5, 22),
        ])

    def test_remove_without_week_end_errors(self):
        rc = cli.main([
            "sentiment-log",
            "--agent", "claude",
            "--repo-root", str(self.repo),
            "--remove",
        ])
        self.assertEqual(rc, 1)

    def test_remove_nonexistent_errors(self):
        rc = cli.main([
            "sentiment-log",
            "--agent", "claude",
            "--repo-root", str(self.repo),
            "--remove",
            "--week-end", "2099-01-01",
        ])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
