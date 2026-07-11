from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from stock_analyze import competition
from stock_analyze.cli import DASHBOARD_ROUTES, build_parser


REPO_ROOT = Path(__file__).resolve().parents[1]


class ArchivedMarketTests(unittest.TestCase):
    def test_only_mainland_tradeable_accounts_are_active(self) -> None:
        self.assertEqual(competition.MARKETS, ["a_share", "cn_qdii_etf"])
        self.assertEqual(competition.ARCHIVED_MARKETS, ["hk", "us"])

    def test_archived_markets_cannot_be_dispatched(self) -> None:
        for market in competition.ARCHIVED_MARKETS:
            with self.subTest(market=market):
                with self.assertRaises(competition.UnknownMarket):
                    competition.get_market_module(market)

    def test_cli_rejects_archived_market(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                build_parser().parse_args(
                    ["--market", "hk", "--agent", "codex", "run-daily"]
                )
        self.assertEqual(caught.exception.code, 2)

    def test_dashboard_has_no_direct_overseas_routes(self) -> None:
        self.assertFalse(any("/hk/" in path for path in DASHBOARD_ROUTES))
        self.assertFalse(any("/us/" in path for path in DASHBOARD_ROUTES))

    def test_sync_script_only_iterates_active_markets(self) -> None:
        text = (REPO_ROOT / "scripts" / "sync-to-ecs.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("markets=(a_share cn_qdii_etf)", text)
        self.assertNotIn("markets=(a_share hk us cn_qdii_etf)", text)

    def test_overseas_runner_is_an_archive_tombstone(self) -> None:
        text = (REPO_ROOT / "scripts" / "run-overseas.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Direct HK/US simulation is archived", text)
        self.assertNotIn("ipinfo.io", text)
        self.assertTrue(
            (REPO_ROOT / "archive" / "direct-overseas" / "run-overseas.sh").exists()
        )


if __name__ == "__main__":
    unittest.main()
