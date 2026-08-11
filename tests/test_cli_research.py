import tempfile
import unittest
from unittest.mock import patch

from stock_analyze.cli import build_parser, main


class CLIResearchTest(unittest.TestCase):
    def test_research_cli_bounds_default_a_share_history_sample(self):
        args = build_parser().parse_args(["prepare-research-data"])
        self.assertEqual(args.max_full_history_instruments, 500)

    def test_parser_accepts_all_research_commands(self):
        parser = build_parser()
        for command in (
            "prepare-research-data",
            "run-prediction-research",
            "train-prediction-models",
            "predict",
        ):
            args = parser.parse_args(["--market", "a_share", "--agent", "codex", command, "--offline"])
            self.assertEqual(args.command, command)
            self.assertTrue(args.offline)

    def test_cli_dispatches_prepare_with_explicit_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.prepare_data",
            return_value={"status": "built", "rows": 12},
        ) as prepare:
            code = main(
                [
                    "--market", "a_share", "--agent", "codex", "--as-of", "2026-07-10",
                    "prepare-research-data", "--offline", "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 0)
        prepare.assert_called_once_with(force=False)

    def test_predict_fallback_returns_nonzero_for_systemd(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.pipeline.ResearchPipeline.predict",
            return_value={
                "status": "fallback",
                "predictions": 0,
                "error": "feature snapshot missing",
            },
        ):
            code = main(
                [
                    "--market", "a_share", "--agent", "codex",
                    "--as-of", "2026-08-08", "predict", "--offline",
                    "--repo-root", tmp,
                ]
            )

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
