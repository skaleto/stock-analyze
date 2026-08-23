from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_analyze.cli import main


class CLIResearchAllCapTests(unittest.TestCase):
    def test_dispatches_dates_cwd_and_provider_without_network(self) -> None:
        expected = {"status": "complete", "message": "参考数据已验证"}
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.cli.Path.cwd",
            return_value=Path(tmp),
        ), patch(
            "stock_analyze.markets.a_share.backtest.data_prep._make_pro_client",
            return_value="fake-pro-client",
        ) as make_client, patch(
            "stock_analyze.research.a_share_all_cap_sources.collect_all_cap_sources",
            return_value=expected,
        ) as collect:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "refresh-a-share-all-cap-sources",
                        "--start",
                        "2018-01-02",
                        "--end",
                        "2024-12-31",
                    ]
                )

        self.assertEqual(code, 0)
        make_client.assert_called_once_with()
        collect.assert_called_once_with(
            repo_root=Path(tmp),
            pro_client="fake-pro-client",
            start=date(2018, 1, 2),
            end=date(2024, 12, 31),
        )
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertIn("参考数据已验证", output.getvalue())
        self.assertNotIn("\\u53c2", output.getvalue())

    def test_invalid_iso_date_fails_before_provider_construction(self) -> None:
        with patch(
            "stock_analyze.markets.a_share.backtest.data_prep._make_pro_client",
        ) as make_client, redirect_stderr(io.StringIO()), self.assertRaises(
            SystemExit,
        ) as raised:
            main(
                [
                    "refresh-a-share-all-cap-sources",
                    "--start",
                    "not-a-date",
                    "--end",
                    "2024-12-31",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        make_client.assert_not_called()

    def test_collection_failure_returns_nonzero_without_printing_secrets(self) -> None:
        with patch(
            "stock_analyze.markets.a_share.backtest.data_prep._make_pro_client",
            return_value="secret-client-value",
        ), patch(
            "stock_analyze.research.a_share_all_cap_sources.collect_all_cap_sources",
            side_effect=ValueError("all_cap_source_checksum:index_daily.parquet"),
        ):
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(
                    [
                        "refresh-a-share-all-cap-sources",
                        "--start",
                        "2018-01-02",
                        "--end",
                        "2024-12-31",
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("all_cap_source_checksum", error.getvalue())
        self.assertNotIn("secret-client-value", error.getvalue())


if __name__ == "__main__":
    unittest.main()
