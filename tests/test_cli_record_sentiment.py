"""Tests for the ``record-sentiment`` CLI subcommand."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import cli


class RecordSentimentCLITests(unittest.TestCase):
    def test_happy_path_invokes_recording(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment"
        ) as mocked:
            cli.main([
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "0.32",
                "--confidence", "0.78",
                "--drivers", "AI 算力链回暖,央行 MLF 偏鸽,地产新政预期反复",
                "--llm-model", "claude-sonnet-4.5",
                "--sources", "https://www.cls.cn/x|https://finance.sina.com.cn/y",
            ])
            mocked.assert_called_once()
            kw = mocked.call_args.kwargs
            self.assertEqual(kw["agent_id"], "claude")
            self.assertAlmostEqual(kw["score"], 0.32)
            self.assertAlmostEqual(kw["confidence"], 0.78)
            self.assertEqual(kw["llm_model"], "claude-sonnet-4.5")
            self.assertEqual(len(kw["drivers"]), 3)
            self.assertIn("AI 算力链回暖", kw["drivers"])
            self.assertEqual(len(kw["sources"]), 2)

    def test_duplicate_returns_exit_1(self):
        from stock_analyze.markets.a_share.alt_factors.sentiment import DuplicateSentimentEntry
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment",
            side_effect=DuplicateSentimentEntry("claude / 2026-05-22 already exists"),
        ):
            rc = cli.main([
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "0.32", "--confidence", "0.78",
                "--drivers", "x",
                "--llm-model", "m",
            ])
            self.assertEqual(rc, 1)

    def test_validation_error_returns_exit_1(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment",
            side_effect=ValueError("score must be in [-1, 1]"),
        ):
            rc = cli.main([
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "5.0", "--confidence", "0.78",
                "--drivers", "x",
                "--llm-model", "m",
            ])
            self.assertEqual(rc, 1)

    def test_force_flag_propagates(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment"
        ) as mocked:
            cli.main([
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "0.32", "--confidence", "0.78",
                "--drivers", "x",
                "--llm-model", "m",
                "--force",
            ])
            self.assertTrue(mocked.call_args.kwargs["force"])

    def test_default_prompt_version_v1(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment"
        ) as mocked:
            cli.main([
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "0.32", "--confidence", "0.78",
                "--drivers", "x",
                "--llm-model", "m",
            ])
            self.assertEqual(mocked.call_args.kwargs["prompt_version"], "v1")


if __name__ == "__main__":
    unittest.main()
