"""Tests for dashboard sentiment panels (Task 8 of B)."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import reporting, dashboard_aggregator
from stock_analyze.markets.a_share.alt_factors import sentiment


def _seed_sentiment(repo: Path, agent: str, rows: list[tuple[date, float]]) -> None:
    for week, score in rows:
        sentiment.record_market_sentiment(
            agent_id=agent, week_end=week,
            score=score, confidence=0.7, drivers=["x", "y"],
            sources=["https://example/x"], llm_model="model-v1",
            prompt_version="v1", repo_root=repo,
        )


class SingleAgentMarketSentimentPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        _seed_sentiment(self.repo, "claude", [
            (date(2026, 4, 10), 0.10),
            (date(2026, 4, 17), 0.15),
            (date(2026, 4, 24), 0.20),
            (date(2026, 5, 1), 0.18),
            (date(2026, 5, 8), 0.25),
            (date(2026, 5, 15), 0.30),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_panel_renders_with_history(self):
        html = reporting.render_market_sentiment_panel(
            "claude", repo_root=self.repo,
        )
        self.assertIn("市场情绪", html)
        self.assertIn("2026-05-15", html)
        self.assertIn("+0.30", html)

    def test_panel_shows_aggregates(self):
        html = reporting.render_market_sentiment_panel(
            "claude", repo_root=self.repo,
        )
        # Latest, 4-week and 8-week averages should appear
        self.assertIn("4 周均值", html)
        self.assertIn("8 周均值", html)

    def test_panel_empty_state(self):
        with TemporaryDirectory() as empty_tmp:
            html = reporting.render_market_sentiment_panel(
                "claude", repo_root=empty_tmp,
            )
            self.assertIn("尚无", html)

    def test_panel_stale_warning_when_data_more_than_2_weeks_old(self):
        # Most recent is 2026-05-15; today=2026-06-15 → 31 days old → stale.
        # Patch target is the submodule where the panel actually looks up
        # `_today` after the 2026-05-26 reporting/ split (I1 audit task).
        with patch("stock_analyze.reporting.panels._today",
                    return_value=date(2026, 6, 15)):
            html = reporting.render_market_sentiment_panel(
                "claude", repo_root=self.repo,
            )
        self.assertIn("未更新", html)

    def test_panel_no_stale_warning_when_data_fresh(self):
        # Most recent is 2026-05-15; today=2026-05-22 (one week later) → fresh.
        # Patch target matches the panels submodule after the I1 split.
        with patch("stock_analyze.reporting.panels._today",
                    return_value=date(2026, 5, 22)):
            html = reporting.render_market_sentiment_panel(
                "claude", repo_root=self.repo,
            )
        self.assertNotIn("未更新", html)


class CrossLLMComparisonPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        _seed_sentiment(self.repo, "claude", [
            (date(2026, 5, 8), 0.10),
            (date(2026, 5, 15), 0.20),
            (date(2026, 5, 22), 0.30),
        ])
        _seed_sentiment(self.repo, "codex", [
            (date(2026, 5, 8), 0.05),
            (date(2026, 5, 15), 0.15),
            (date(2026, 5, 22), 0.18),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_comparison_panel_shows_both_strategy_labels(self):
        html = dashboard_aggregator.render_sentiment_comparison_panel(
            repo_root=self.repo,
        )
        self.assertIn("稳健防守", html)
        self.assertIn("趋势进攻", html)
        self.assertNotIn("claude", html.lower())
        self.assertNotIn("codex", html.lower())

    def test_comparison_panel_shows_latest_diff(self):
        html = dashboard_aggregator.render_sentiment_comparison_panel(
            repo_root=self.repo,
        )
        # claude latest 0.30, codex latest 0.18 → diff +0.12
        self.assertIn("+0.30", html)
        self.assertIn("+0.18", html)

    def test_comparison_empty_state_when_one_agent_has_no_data(self):
        with TemporaryDirectory() as partial_tmp:
            partial = Path(partial_tmp)
            _seed_sentiment(partial, "claude", [(date(2026, 5, 22), 0.3)])
            html = dashboard_aggregator.render_sentiment_comparison_panel(
                repo_root=partial,
            )
            self.assertIn("尚无", html)


if __name__ == "__main__":
    unittest.main()
