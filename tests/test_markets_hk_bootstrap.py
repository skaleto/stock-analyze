"""Smoke tests for the HK market subpackage bootstrap."""
import unittest


class HKBootstrapTests(unittest.TestCase):
    def test_hk_subpackage_importable(self):
        from stock_analyze.markets import hk  # noqa: F401

    def test_mechanics_constants_present(self):
        from stock_analyze.markets.hk import mechanics
        self.assertEqual(mechanics.SETTLEMENT_DAYS, 2)
        self.assertIsNone(mechanics.DAILY_LIMIT_PCT)
        self.assertTrue(mechanics.ALLOW_SHORTING)
        self.assertAlmostEqual(mechanics.STAMP_TAX_RATE, 0.0013)
        self.assertEqual(mechanics.lot_size_for("0700.HK"), 100)

    def test_universe_resolves_hsi_and_hscei(self):
        from stock_analyze.markets.hk import universe
        hsi = universe.resolve_universe("hsi")
        hscei = universe.resolve_universe("hscei")
        self.assertIn("0700.HK", hsi)
        self.assertIn("0700.HK", hscei)
        self.assertGreaterEqual(len(hsi), 50)
        self.assertGreaterEqual(len(hscei), 50)

    def test_universe_unknown_scope_raises(self):
        from stock_analyze.markets.hk import universe
        with self.assertRaises(ValueError):
            universe.resolve_universe("zzz")

    def test_hk_in_competition_markets(self):
        from stock_analyze import competition
        self.assertIn("hk", competition.MARKETS)

    def test_hk_factor_whitelist_includes_6_factors(self):
        from stock_analyze.overlay_guard import AVAILABLE_FACTORS_BY_MARKET
        hk = AVAILABLE_FACTORS_BY_MARKET["hk"]
        for name in ("pe", "pb", "momentum_20", "momentum_60",
                     "low_volatility_60", "dividend_yield"):
            self.assertIn(name, hk, msg=f"hk missing factor {name}")


if __name__ == "__main__":
    unittest.main()
