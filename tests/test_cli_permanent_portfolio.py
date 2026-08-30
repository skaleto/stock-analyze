from __future__ import annotations

import unittest
from unittest import mock

from stock_analyze import cli


class PermanentPortfolioCliTests(unittest.TestCase):
    @mock.patch(
        "stock_analyze.research.permanent_portfolio.data.materialize_market_data"
    )
    @mock.patch(
        "stock_analyze.markets.a_share.backtest.data_prep._make_pro_client"
    )
    def test_prepare_command_uses_frozen_contract(
        self,
        make_client: mock.Mock,
        materialize: mock.Mock,
    ) -> None:
        make_client.return_value = object()
        materialize.return_value = {
            "status": "complete",
            "publication_id": "fixture",
        }

        exit_code = cli.main(
            [
                "prepare-permanent-portfolio-data",
                "--end",
                "2026-08-28",
                "--repo-root",
                ".",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            materialize.call_args.kwargs["codes"],
            ("510300.SH", "511260.SH", "511880.SH", "518880.SH"),
        )

    @mock.patch(
        "stock_analyze.research.permanent_portfolio.workflow.run_development"
    )
    def test_development_command_uses_frozen_contract(
        self,
        run: mock.Mock,
    ) -> None:
        run.return_value = {"status": "development_complete"}

        exit_code = cli.main(
            [
                "run-permanent-portfolio-development",
                "--repo-root",
                ".",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            str(run.call_args.kwargs["contract_path"]),
            "configs/research/permanent_portfolio_v1.yaml",
        )

    @mock.patch(
        "stock_analyze.research.permanent_portfolio.workflow.run_holdout",
        create=True,
    )
    def test_holdout_requires_development_hash(
        self,
        run: mock.Mock,
    ) -> None:
        run.return_value = {"status": "holdout_complete"}

        exit_code = cli.main(
            [
                "open-permanent-portfolio-holdout",
                "--development-artifact",
                "development.json",
                "--development-sha256",
                "a" * 64,
                "--repo-root",
                ".",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run.call_args.kwargs["expected_development_sha256"],
            "a" * 64,
        )

    @mock.patch(
        "stock_analyze.research.permanent_portfolio.paper.run_paper_day"
    )
    def test_paper_command_is_research_scoped(self, run: mock.Mock) -> None:
        run.return_value = {
            "status": "complete",
            "run_id": "permanent-portfolio-20260901",
        }

        exit_code = cli.main(
            [
                "run-permanent-portfolio-paper",
                "--paper-as-of",
                "2026-09-01",
                "--repo-root",
                ".",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_args.kwargs["as_of"], "2026-09-01")
        self.assertNotIn("agent", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
