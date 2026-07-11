import unittest
from unittest.mock import patch

from stock_analyze.cli import build_parser


class CLIMarketFlagTests(unittest.TestCase):
    def test_parser_accepts_market_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--market", "a_share", "--agent", "claude", "init"]
        )
        self.assertEqual(args.market, "a_share")

    def test_market_defaults_to_a_share_when_absent(self):
        parser = build_parser()
        args = parser.parse_args(["--agent", "claude", "init"])
        self.assertEqual(args.market, "a_share")

    def test_market_rejects_unknown(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--market", "moon", "init"])

    def test_parser_accepts_strategy_pair_validation(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--market", "cn_qdii_etf", "validate-strategy-pair"]
        )
        self.assertEqual(args.market, "cn_qdii_etf")
        self.assertEqual(args.command, "validate-strategy-pair")


if __name__ == "__main__":
    unittest.main()
