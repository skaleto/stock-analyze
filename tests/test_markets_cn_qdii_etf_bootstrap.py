from __future__ import annotations

import unittest

from stock_analyze import competition
from stock_analyze.overlay_guard import AVAILABLE_FACTORS_BY_MARKET


class CNQDIETFBootstrapTests(unittest.TestCase):
    def test_market_is_registered_and_importable(self):
        self.assertIn("cn_qdii_etf", competition.MARKETS)
        mod = competition.get_market_module("cn_qdii_etf")
        for name in (
            "make_provider",
            "build_signals",
            "initialize",
            "generate_rebalance_orders",
            "execute_due_orders",
            "update_nav",
        ):
            self.assertTrue(callable(getattr(mod, name)), msg=f"missing {name}")

    def test_competition_configs_load_for_both_agents(self):
        codex = competition.load("codex", market="cn_qdii_etf")
        claude = competition.load("claude", market="cn_qdii_etf")

        self.assertEqual(codex["competition_id"], "claude-vs-codex-cn-qdii-etf")
        self.assertEqual(claude["competition_id"], "claude-vs-codex-cn-qdii-etf")
        self.assertEqual([a["scope"] for a in codex["accounts"]], ["us_exposure", "hk_exposure"])
        self.assertEqual([a["scope"] for a in claude["accounts"]], ["us_exposure", "hk_exposure"])
        self.assertEqual(codex["trading"]["stamp_tax_rate"], 0.0)
        self.assertEqual(codex["trading"]["lot_size_default"], 100)

    def test_overlay_guard_has_etf_native_factor_whitelist(self):
        whitelist = AVAILABLE_FACTORS_BY_MARKET["cn_qdii_etf"]
        for name in (
            "momentum_20",
            "momentum_60",
            "low_volatility_60",
            "avg_amount_20",
            "discount_premium",
        ):
            self.assertIn(name, whitelist)
        self.assertNotIn("pe", whitelist)
        self.assertNotIn("roe", whitelist)


if __name__ == "__main__":
    unittest.main()
