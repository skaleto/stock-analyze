"""Multi-market tests for build_daily_summary.

Covers the Phase 2/3 extension where the notifier renders per-market
NAV / 持仓 / Sanity blocks for ``markets=['a_share', 'hk', 'us']``.
Single-market path (default) is still tested in test_notifier.py.
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze.notifier import (
    MARKET_LABELS,
    MARKET_INITIAL_CASH,
    build_daily_summary,
)


def _seed_dir(repo: Path, market: str, agent: str) -> Path:
    d = repo / "data" / market / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_nav(data_dir: Path, total_value: float):
    """Minimal daily_nav.csv: single date, single account."""
    today = date.today().isoformat()
    lines = [
        "date,account_id,cash,positions_value,total_value,benchmark_code,benchmark_value,benchmark_date,source",
        f"{today},account1,0,0,{total_value:.2f},X,0,{today},test",
    ]
    (data_dir / "daily_nav.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


class MultiMarketBuildSummaryTests(unittest.TestCase):
    def test_emits_three_market_blocks_when_three_markets_passed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for m in ("a_share", "hk", "us"):
                d = _seed_dir(root, m, "claude")
                _write_nav(d, total_value=1_000_000.0 if m != "us" else 150_000.0)

            text = build_daily_summary(
                ["claude"], repo_root=root,
                today_d=date(2026, 6, 16),
                markets=["a_share", "hk", "us"],
            )
            # Each market produces its own labelled NAV block
            self.assertIn("A股 NAV", text)
            self.assertIn("港股 NAV", text)
            self.assertIn("美股 NAV", text)
            # Each market produces its own labelled 持仓 block
            self.assertIn("A股 持仓", text)
            self.assertIn("港股 持仓", text)
            self.assertIn("美股 持仓", text)

    def test_single_market_default_preserves_legacy_headers(self):
        """Back-compat: single market omits the market-label prefix."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _seed_dir(root, "a_share", "claude")
            _write_nav(d, total_value=1_000_000.0)
            text = build_daily_summary(
                ["claude"], repo_root=root,
                today_d=date(2026, 6, 16),
            )  # markets defaults to ["a_share"]
            # Legacy headers — no "A股" prefix
            self.assertIn("💰 NAV", text)
            self.assertNotIn("💰 A股 NAV", text)
            self.assertIn("📈 持仓", text)
            self.assertNotIn("📈 A股 持仓", text)
            self.assertIn("✅ Sanity-check", text)
            self.assertNotIn("✅ A股 Sanity-check", text)

    def test_us_currency_label_uses_dollar(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _seed_dir(root, "us", "claude")
            _write_nav(d, total_value=160_000.0)
            text = build_daily_summary(
                ["claude"], repo_root=root,
                today_d=date(2026, 6, 16),
                markets=["us"],
            )
            self.assertIn("$", text)
            # US baseline is $150K so this is +6.67% (note: when only 1 market
            # is passed, the legacy header path strips the label — but the
            # currency prefix on the NAV value itself uses MARKET_CURRENCY).
            self.assertIn("150K", text)

    def test_hk_currency_label_uses_hkdollar(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = _seed_dir(root, "hk", "claude")
            _write_nav(d, total_value=1_000_000.0)
            text = build_daily_summary(
                ["claude"], repo_root=root,
                today_d=date(2026, 6, 16),
                markets=["hk"],
            )
            self.assertIn("HK$", text)

    def test_missing_market_data_shows_uninitialized_not_crashes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Only a_share seeded; hk + us absent
            d = _seed_dir(root, "a_share", "claude")
            _write_nav(d, total_value=1_000_000.0)
            text = build_daily_summary(
                ["claude"], repo_root=root,
                today_d=date(2026, 6, 16),
                markets=["a_share", "hk", "us"],
            )
            self.assertIn("尚未初始化", text)
            # All three NAV blocks present anyway
            self.assertIn("A股", text)
            self.assertIn("港股", text)
            self.assertIn("美股", text)


class MarketConstantsTests(unittest.TestCase):
    def test_market_labels_cover_three_markets(self):
        for m in ("a_share", "hk", "us"):
            self.assertIn(m, MARKET_LABELS)

    def test_initial_cash_values(self):
        self.assertEqual(MARKET_INITIAL_CASH["a_share"], 1_000_000.0)
        self.assertEqual(MARKET_INITIAL_CASH["hk"], 1_000_000.0)
        self.assertEqual(MARKET_INITIAL_CASH["us"], 150_000.0)


if __name__ == "__main__":
    unittest.main()
