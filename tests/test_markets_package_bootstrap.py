import unittest


class MarketsPackageBootstrapTests(unittest.TestCase):
    def test_markets_package_importable(self):
        from stock_analyze import markets  # noqa: F401

    def test_a_share_subpackage_importable(self):
        from stock_analyze.markets import a_share  # noqa: F401


if __name__ == "__main__":
    unittest.main()
