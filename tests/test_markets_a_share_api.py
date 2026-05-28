import unittest


class AShareMarketAPITests(unittest.TestCase):
    def test_make_provider_exposed(self):
        from stock_analyze.markets import a_share
        self.assertTrue(callable(a_share.make_provider))

    def test_simulator_functions_exposed(self):
        from stock_analyze.markets import a_share
        for name in ("execute_due_orders", "update_nav",
                     "generate_rebalance_orders", "initialize"):
            self.assertTrue(callable(getattr(a_share, name)),
                            msg=f"a_share.{name} not exposed")

    def test_build_signals_exposed(self):
        from stock_analyze.markets import a_share
        self.assertTrue(callable(a_share.build_signals))


if __name__ == "__main__":
    unittest.main()
