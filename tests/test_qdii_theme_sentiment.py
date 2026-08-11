from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd
from unittest.mock import patch

from stock_analyze import cli

from stock_analyze.markets.cn_qdii_etf.theme_sentiment import (
    SentimentValidationError,
    attach_point_in_time_sentiment,
    load_theme_sentiment,
    record_theme_sentiment,
    theme_scores_as_of,
)


class QDIIThemeSentimentTests(unittest.TestCase):
    def test_requires_source_and_valid_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SentimentValidationError):
                record_theme_sentiment(
                    Path(tmp) / "theme.csv",
                    agent="codex",
                    week_end="2026-07-10",
                    index_key="nikkei_225",
                    score=0.4,
                    confidence=0.8,
                    drivers="日元走弱",
                    sources="",
                    llm_model="gpt-5.6",
                )

    def test_scores_use_confidence_decay_and_observed_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "theme.csv"
            record_theme_sentiment(
                path,
                agent="codex",
                week_end="2026-07-10",
                index_key="nikkei_225",
                score=0.5,
                confidence=0.8,
                drivers="日元走弱",
                sources="https://example.test/nikkei",
                llm_model="gpt-5.6",
                observed_at=datetime(2026, 7, 11, 8, 0),
            )
            rows = load_theme_sentiment(path)

        hidden = theme_scores_as_of(rows, agent="codex", as_of="2026-07-10T23:59:59")
        fresh = theme_scores_as_of(rows, agent="codex", as_of="2026-07-11T08:00:00")
        decayed = theme_scores_as_of(rows, agent="codex", as_of="2026-07-18T08:00:00")
        stale = theme_scores_as_of(rows, agent="codex", as_of="2026-08-01T08:00:00")

        self.assertEqual(hidden, {})
        self.assertAlmostEqual(fresh["nikkei_225"], 0.4)
        self.assertAlmostEqual(decayed["nikkei_225"], 0.2)
        self.assertEqual(stale, {})

    def test_attaches_cross_sectional_scores_without_filling_missing_themes(self) -> None:
        records = pd.DataFrame(
            [
                {
                    "agent": "codex",
                    "week_end": "2026-07-10",
                    "index_key": "nikkei_225",
                    "score": 0.6,
                    "confidence": 1.0,
                    "observed_at": "2026-07-10T08:00:00",
                    "expires_at": "2026-07-24T08:00:00",
                }
            ]
        )
        panel = pd.DataFrame(
            [
                {"trade_date": "2026-07-10", "code": "A", "index_key": "nikkei_225"},
                {"trade_date": "2026-07-10", "code": "B", "index_key": "germany_dax"},
            ]
        )

        result = attach_point_in_time_sentiment(panel, records, agent="codex")

        self.assertAlmostEqual(result.loc[result["code"] == "A", "theme_sentiment_score"].iloc[0], 0.6)
        self.assertTrue(pd.isna(result.loc[result["code"] == "B", "theme_sentiment_score"].iloc[0]))

    def test_cli_records_one_index_theme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.markets.cn_qdii_etf.theme_sentiment.record_theme_sentiment",
            return_value=pd.DataFrame([{"index_key": "nikkei_225"}]),
        ) as record:
            status = cli.main(
                [
                    "record-theme-sentiment",
                    "--agent", "codex",
                    "--week-end", "2026-07-10",
                    "--index-key", "nikkei_225",
                    "--score", "0.4",
                    "--confidence", "0.8",
                    "--drivers", "日元走弱",
                    "--sources", "https://example.test/nikkei",
                    "--llm-model", "gpt-5.6",
                    "--output", str(Path(tmp) / "theme.csv"),
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(record.call_args.kwargs["index_key"], "nikkei_225")


if __name__ == "__main__":
    unittest.main()
