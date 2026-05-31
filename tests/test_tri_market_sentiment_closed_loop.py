from __future__ import annotations

import io
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pandas as pd

from stock_analyze import cli
from stock_analyze.dashboard_aggregator import render_sentiment_comparison_panel
from stock_analyze.factor_pipeline import load_broadcast_factor
from stock_analyze.markets.a_share.alt_factors import sentiment
from stock_analyze.markets.hk.strategy import build_signals as build_hk_signals
from stock_analyze.markets.us.strategy import build_signals as build_us_signals
from stock_analyze.overlay_guard import AVAILABLE_FACTORS_BY_MARKET, validate_factor_name


class TriMarketSentimentStoreTests(unittest.TestCase):
    def test_market_sentiment_is_namespaced_by_market(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentiment.record_market_sentiment(
                agent_id="claude",
                market="hk",
                week_end=date(2026, 5, 29),
                score=0.4,
                confidence=0.5,
                drivers=["港股政策预期修复"],
                sources=["https://example.com/hk"],
                llm_model="gpt-5.5",
                prompt_version="market_v1",
                repo_root=root,
            )

            hk_path = root / "data" / "hk" / "claude" / "alt_factors" / "market_sentiment.csv"
            a_path = root / "data" / "a_share" / "claude" / "alt_factors" / "market_sentiment.csv"
            self.assertTrue(hk_path.exists())
            self.assertFalse(a_path.exists())
            self.assertEqual(sentiment.load_sentiment_history("claude", root, market="a_share"), [])

            value = load_broadcast_factor(
                "claude",
                "claude_market_sentiment_1w",
                date(2026, 5, 31),
                root,
                market="hk",
            )
            self.assertAlmostEqual(value, 0.2)

    def test_sector_sentiment_is_namespaced_by_market(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentiment.record_sector_sentiment(
                "codex",
                date(2026, 5, 29),
                [
                    {"industry": "Technology", "score": 0.6, "confidence": 0.5},
                    {"industry": "Utilities", "score": -0.2, "confidence": 1.0},
                ],
                "gpt-5.5",
                "sector_v1",
                root,
                market="us",
            )

            latest = sentiment.load_latest_sector_sentiment(
                "codex", date(2026, 5, 31), root, market="us",
            )
            self.assertAlmostEqual(latest["Technology"], 0.3)
            self.assertAlmostEqual(latest["Utilities"], -0.2)
            self.assertEqual(
                sentiment.load_latest_sector_sentiment("codex", date(2026, 5, 31), root, market="hk"),
                {},
            )


class TriMarketSentimentCLITests(unittest.TestCase):
    def test_record_sentiment_accepts_market_flag(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_market_sentiment"
        ) as mocked:
            rc = cli.main([
                "record-sentiment",
                "--market", "us",
                "--agent", "claude",
                "--week-end", "2026-05-29",
                "--score", "0.25",
                "--confidence", "0.8",
                "--drivers", "Fed降息预期,科技股财报上修",
                "--sources", "https://example.com/us",
                "--llm-model", "gpt-5.5",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(mocked.call_args.kwargs["market"], "us")

    def test_record_sector_sentiment_accepts_market_flag(self):
        with patch(
            "stock_analyze.markets.a_share.alt_factors.sentiment.record_sector_sentiment"
        ) as mocked:
            rc = cli.main([
                "record-sector-sentiment",
                "--market", "hk",
                "--agent", "codex",
                "--week-end", "2026-05-29",
                "--json", '{"llm_model":"gpt-5.5","sectors":[{"industry":"Technology","score":0.5,"confidence":0.8}]}',
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(mocked.call_args.kwargs["market"], "hk")

    def test_sentiment_log_accepts_market_flag(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentiment.record_market_sentiment(
                "claude",
                date(2026, 5, 29),
                0.2,
                0.5,
                ["x"],
                [],
                "gpt-5.5",
                "v1",
                root,
                market="us",
            )
            with patch("sys.stdout", new=io.StringIO()) as captured:
                rc = cli.main([
                    "sentiment-log",
                    "--market", "us",
                    "--agent", "claude",
                    "--repo-root", str(root),
                ])
            self.assertEqual(rc, 0)
            self.assertIn("2026-05-29", captured.getvalue())


class TriMarketSentimentStrategyTests(unittest.TestCase):
    def test_hk_sector_sentiment_changes_ranking(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentiment.record_sector_sentiment(
                "claude",
                date(2026, 5, 29),
                [
                    {"industry": "Tech", "score": 0.8, "confidence": 1.0},
                    {"industry": "Banking", "score": -0.8, "confidence": 1.0},
                ],
                "gpt-5.5",
                "sector_v1",
                root,
                market="hk",
            )
            provider = MagicMock()
            provider.spot.return_value = pd.DataFrame({
                "code": ["0700.HK", "0005.HK"],
                "industry": ["Tech", "Banking"],
                "pe": [10.0, 10.0],
            })
            config = {
                "agent_id": "claude",
                "accounts": [{"id": "hsi", "scope": "hsi", "top_n": 50}],
                "factors": {
                    "pe": {"weight": 0.1, "direction": "low"},
                    "claude_sector_sentiment": {"weight": 1.0, "direction": "high"},
                },
                "factor_processing": {"neutralize_industry": False, "min_factor_coverage": 0.0},
            }
            rows = build_hk_signals(config, provider, as_of=date(2026, 5, 31), repo_root=root)
            by_code = {row["code"]: row["score"] for row in rows}
            self.assertGreater(by_code["0700.HK"], by_code["0005.HK"])

    def test_us_market_sentiment_broadcast_is_loaded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentiment.record_market_sentiment(
                "codex",
                date(2026, 5, 29),
                1.0,
                1.0,
                ["risk on"],
                [],
                "gpt-5.5",
                "v1",
                root,
                market="us",
            )
            provider = MagicMock()
            provider.spot.return_value = pd.DataFrame({
                "code": ["AAPL", "MSFT"],
                "pe": [10.0, 10.0],
            })
            config = {
                "agent_id": "codex",
                "accounts": [{"id": "sp500", "scope": "sp500", "top_n": 50}],
                "factors": {
                    "pe": {"weight": 1.0, "direction": "low"},
                    "codex_market_sentiment_1w": {"weight": 0.25, "direction": "high"},
                },
                "factor_processing": {"neutralize_industry": False, "min_factor_coverage": 0.0},
            }
            rows = build_us_signals(config, provider, as_of=date(2026, 5, 31), repo_root=root)
            self.assertTrue(rows)
            self.assertTrue(all(row["score"] >= 0.25 for row in rows))


class TriMarketSentimentGuardTests(unittest.TestCase):
    def test_sentiment_factors_are_whitelisted_for_every_market(self):
        for market in ("a_share", "hk", "us"):
            whitelist = AVAILABLE_FACTORS_BY_MARKET[market]
            self.assertIn("claude_market_sentiment_1w", whitelist)
            self.assertIn("codex_market_sentiment_1w", whitelist)
            self.assertIn("claude_sector_sentiment", whitelist)
            self.assertIn("codex_sector_sentiment", whitelist)
            validate_factor_name(
                "claude_sector_sentiment",
                "claude",
                factors_whitelist=whitelist,
            )


class TriMarketSentimentDashboardTests(unittest.TestCase):
    def test_sentiment_panel_lists_all_markets(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for market, score in (("a_share", 0.1), ("hk", 0.2), ("us", -0.3)):
                for agent in ("claude", "codex"):
                    sentiment.record_market_sentiment(
                        agent,
                        date(2026, 5, 29),
                        score,
                        0.5,
                        [f"{market} driver"],
                        [f"https://example.com/{market}"],
                        "gpt-5.5",
                        "v1",
                        root,
                        market=market,
                    )
            html = render_sentiment_comparison_panel(root)
            self.assertIn("A股", html)
            self.assertIn("港股", html)
            self.assertIn("美股", html)
            self.assertIn("claude", html)
            self.assertIn("codex", html)


if __name__ == "__main__":
    unittest.main()
