"""Notification coverage for the two active mainland-tradeable markets."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import competition
from stock_analyze.notifier import MARKET_INITIAL_CASH, MARKET_LABELS, build_daily_summary


def _seed_nav(repo: Path, market: str, agent: str, total_value: float) -> None:
    data_dir = repo / "data" / market / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    (data_dir / "daily_nav.csv").write_text(
        "date,account_id,cash,positions_value,total_value,benchmark_code,benchmark_value,benchmark_date,source\n"
        f"{today},account1,0,0,{total_value:.2f},X,0,{today},test\n",
        encoding="utf-8",
    )


class MultiMarketBuildSummaryTests(unittest.TestCase):
    def test_emits_both_active_market_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for market in competition.MARKETS:
                _seed_nav(root, market, "claude", 1_000_000.0)

            text = build_daily_summary(
                ["claude"],
                repo_root=root,
                today_d=date(2026, 7, 19),
                markets=list(competition.MARKETS),
            )

            self.assertIn("A股 NAV", text)
            self.assertIn("跨境ETF NAV", text)
            self.assertNotIn("港股 NAV", text)
            self.assertNotIn("美股 NAV", text)

    def test_constants_are_exactly_the_active_markets(self) -> None:
        self.assertEqual(set(MARKET_LABELS), set(competition.MARKETS))
        self.assertEqual(set(MARKET_INITIAL_CASH), set(competition.MARKETS))
        self.assertEqual(MARKET_INITIAL_CASH["a_share"], 1_000_000.0)
        self.assertEqual(MARKET_INITIAL_CASH["cn_qdii_etf"], 1_000_000.0)


if __name__ == "__main__":
    unittest.main()
