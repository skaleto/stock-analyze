"""Tests for the prepare-backtest-data CLI subcommand."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_analyze import cli


class PrepareBacktestDataCLITests(unittest.TestCase):
    def test_subcommand_invokes_prepare_backtest_data(self):
        with patch(
            "stock_analyze.backtest.data_prep.prepare_backtest_data"
        ) as mocked:
            cli.main([
                "prepare-backtest-data",
                "--start", "2021-01-01",
                "--end", "2026-04-30",
            ])
            mocked.assert_called_once()
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs["start"], date(2021, 1, 1))
            self.assertEqual(kwargs["end"], date(2026, 4, 30))
            self.assertFalse(kwargs["force"])

    def test_subcommand_passes_force_flag(self):
        with patch(
            "stock_analyze.backtest.data_prep.prepare_backtest_data"
        ) as mocked:
            cli.main([
                "prepare-backtest-data",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
                "--force",
            ])
            self.assertTrue(mocked.call_args.kwargs["force"])

    def test_subcommand_passes_custom_cache_root(self):
        with patch(
            "stock_analyze.backtest.data_prep.prepare_backtest_data"
        ) as mocked:
            cli.main([
                "prepare-backtest-data",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
                "--cache-root", "/tmp/custom_cache",
            ])
            self.assertEqual(
                mocked.call_args.kwargs["cache_root"],
                Path("/tmp/custom_cache"),
            )

    def test_subcommand_defaults_cache_root_to_shared(self):
        """When --cache-root omitted, default is data/shared/backtest_cache."""
        with patch(
            "stock_analyze.backtest.data_prep.prepare_backtest_data"
        ) as mocked:
            cli.main([
                "prepare-backtest-data",
                "--start", "2021-01-01",
                "--end", "2021-01-31",
            ])
            self.assertEqual(
                mocked.call_args.kwargs["cache_root"],
                Path("data/shared/backtest_cache"),
            )


if __name__ == "__main__":
    unittest.main()
