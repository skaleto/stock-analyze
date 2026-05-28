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


if __name__ == "__main__":
    unittest.main()
