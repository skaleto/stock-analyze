from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_analyze import cli
from stock_analyze.markets.cn_qdii_etf.capacity_study import (
    CapacityStudyError,
    CapacityStudyResult,
)
from stock_analyze.markets.cn_qdii_etf.research_panel import ResearchPanelResult


def _fake_result() -> CapacityStudyResult:
    return CapacityStudyResult(
        run_id="2026-07-10-test",
        metrics=pd.DataFrame([{"strategy": "codex", "scope": "us_exposure", "top_n": 5}]),
        selections=pd.DataFrame(),
        trades=pd.DataFrame(),
        nav=pd.DataFrame(),
        summary={"run_id": "2026-07-10-test", "recommendations": []},
    )


class QDIICapacityStudyCLITests(unittest.TestCase):
    def test_command_passes_explicit_window_and_top_ns(self) -> None:
        fake_panel = ResearchPanelResult(pd.DataFrame(), {"universe_hash": "x"})
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.markets.cn_qdii_etf.research_panel.build_research_panel",
            return_value=fake_panel,
        ) as build, patch(
            "stock_analyze.markets.cn_qdii_etf.capacity_study.run_capacity_study",
            return_value=_fake_result(),
        ) as run, patch(
            "stock_analyze.markets.cn_qdii_etf.capacity_study.write_capacity_artifacts",
            return_value={"report": Path(tmp) / "report.md"},
        ) as write:
            code = cli.main(
                [
                    "qdii-capacity-study",
                    "--start",
                    "2023-07-12",
                    "--end",
                    "2026-07-10",
                    "--top-n",
                    "4",
                    "6",
                    "8",
                    "--cache-dir",
                    str(Path(tmp) / "cache"),
                    "--universe",
                    str(Path(tmp) / "universe.json"),
                    "--output-root",
                    tmp,
                    "--min-signal-weeks",
                    "12",
                ]
            )

            self.assertEqual(code, 0)
            self.assertEqual(build.call_args.kwargs["start"], "2023-07-12")
            self.assertEqual(build.call_args.kwargs["end"], "2026-07-10")
            self.assertEqual(run.call_args.kwargs["top_ns"], [4, 6, 8])
            self.assertEqual(run.call_args.kwargs["min_signal_weeks"], 12)
            self.assertEqual(write.call_args.kwargs["end_date"], "2026-07-10")

    def test_command_defaults_to_three_year_window_from_catalog_as_of(self) -> None:
        fake_panel = ResearchPanelResult(pd.DataFrame(), {"universe_hash": "x"})
        with tempfile.TemporaryDirectory() as tmp:
            universe = Path(tmp) / "universe.json"
            universe.write_text(json.dumps({"as_of": "2026-07-12"}), encoding="utf-8")
            with patch(
                "stock_analyze.markets.cn_qdii_etf.research_panel.build_research_panel",
                return_value=fake_panel,
            ) as build, patch(
                "stock_analyze.markets.cn_qdii_etf.capacity_study.run_capacity_study",
                return_value=_fake_result(),
            ), patch(
                "stock_analyze.markets.cn_qdii_etf.capacity_study.write_capacity_artifacts",
                return_value={"report": Path(tmp) / "report.md"},
            ):
                code = cli.main(
                    [
                        "qdii-capacity-study",
                        "--universe",
                        str(universe),
                        "--cache-dir",
                        str(Path(tmp) / "cache"),
                        "--output-root",
                        tmp,
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(build.call_args.kwargs["start"], "2023-07-12")
            self.assertEqual(build.call_args.kwargs["end"], "2026-07-12")

    def test_command_returns_nonzero_for_research_error(self) -> None:
        with patch(
            "stock_analyze.markets.cn_qdii_etf.research_panel.build_research_panel",
            side_effect=CapacityStudyError("missing cache"),
        ):
            code = cli.main(
                [
                    "qdii-capacity-study",
                    "--start",
                    "2023-07-12",
                    "--end",
                    "2026-07-10",
                ]
            )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
