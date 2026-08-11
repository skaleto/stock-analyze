import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import competition


class MarketDispatchTests(unittest.TestCase):
    def test_markets_constant_lists_supported_markets(self):
        self.assertEqual(competition.MARKETS, ["a_share", "cn_qdii_etf"])
        self.assertEqual(competition.ARCHIVED_MARKETS, ["hk", "us"])

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

    Exercised against committed active configs. The default remains identical
    to market="a_share" while the mainland ETF account has its own baseline.
    """

    def test_load_default_is_byte_identical_to_a_share(self):
        self.assertEqual(competition.load("claude"),
                         competition.load("claude", market="a_share"))

    def test_load_dispatches_by_market(self):
        a = competition.load("claude", market="a_share")
        qdii = competition.load("claude", market="cn_qdii_etf")
        self.assertEqual([x["scope"] for x in a["accounts"]], ["hs300", "zz500"])
        self.assertEqual(
            [x["scope"] for x in qdii["accounts"]],
            ["us_exposure", "hk_exposure"],
        )

    def test_load_baseline_market_aware(self):
        self.assertEqual(
            competition.load_baseline(market="cn_qdii_etf")["competition_id"],
            "claude-vs-codex-cn-qdii-etf",
        )

    def test_archived_market_raises(self):
        with self.assertRaises(competition.UnknownMarket):
            competition.load_baseline(market="hk")

    def test_load_baseline_unknown_market_raises(self):
        with self.assertRaises(competition.UnknownMarket):
            competition.load_baseline(market="moon")


if __name__ == "__main__":
    unittest.main()
