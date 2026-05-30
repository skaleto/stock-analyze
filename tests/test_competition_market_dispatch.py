import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import competition


class MarketDispatchTests(unittest.TestCase):
    def test_markets_constant_lists_supported_markets(self):
        self.assertIn("a_share", competition.MARKETS)
        # Phase 2/3 will add 'hk' and 'us'; v1 only has 'a_share'.

    def test_get_market_module_a_share(self):
        mod = competition.get_market_module("a_share")
        self.assertTrue(callable(mod.execute_due_orders))
        self.assertTrue(callable(mod.make_provider))

    def test_get_market_module_unknown_raises(self):
        with self.assertRaises(competition.UnknownMarket):
            competition.get_market_module("zz_top")

    def test_resolve_market_paths_a_share_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = competition.resolve_market_paths(
                "a_share", "claude", repo_root=root,
            )
            self.assertEqual(paths.data_dir, root / "data" / "a_share" / "claude")
            self.assertEqual(paths.reports_dir, root / "reports" / "a_share" / "claude")
            self.assertEqual(
                paths.config_path,
                root / "configs" / "agents" / "claude_a_share.yaml",
            )


class LoadMarketParamTests(unittest.TestCase):
    """competition.load / load_baseline are market-aware (default a_share).

    Exercised against the committed configs so the wiring that lets the CLI
    run --market hk/us is regression-locked. The key invariant: default
    behaviour is byte-identical to market="a_share".
    """

    def test_load_default_is_byte_identical_to_a_share(self):
        self.assertEqual(competition.load("claude"),
                         competition.load("claude", market="a_share"))

    def test_load_dispatches_by_market(self):
        a = competition.load("claude", market="a_share")
        hk = competition.load("claude", market="hk")
        us = competition.load("claude", market="us")
        self.assertEqual([x["scope"] for x in a["accounts"]], ["hs300", "zz500"])
        self.assertEqual([x["scope"] for x in hk["accounts"]], ["hsi", "hscei"])
        self.assertEqual([x["scope"] for x in us["accounts"]], ["sp500", "ndx100"])

    def test_load_baseline_market_aware(self):
        self.assertEqual(
            competition.load_baseline(market="hk")["competition_id"],
            "claude-vs-codex-hk",
        )

    def test_load_baseline_unknown_market_raises(self):
        with self.assertRaises(competition.UnknownMarket):
            competition.load_baseline(market="moon")


if __name__ == "__main__":
    unittest.main()
