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


if __name__ == "__main__":
    unittest.main()
