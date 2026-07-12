from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from stock_analyze import cli
from stock_analyze.markets.cn_qdii_etf.research_panel import ResearchPanelResult
from stock_analyze.markets.cn_qdii_etf.shadow_research import ShadowResearchResult


class QDIIShadowResearchCLITests(unittest.TestCase):
    def test_refreshes_research_inputs_and_runs_shadow_only(self) -> None:
        basic = pd.DataFrame(
            [{"ts_code": "159866.SZ", "name": "日经225ETF(QDII)", "fund_type": "股票型", "list_date": "20210408"}]
        )
        provider = MagicMock()
        provider._fund_basic.return_value = basic
        provider._fund_daily.return_value = pd.DataFrame([{"trade_date": "20260710"}])
        result = ShadowResearchResult(
            "run",
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            {"run_id": "run", "skipped_scopes": []},
        )
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.markets.cn_qdii_etf.data_provider.make_provider",
            return_value=provider,
        ), patch(
            "stock_analyze.markets.cn_qdii_etf.research_panel.build_research_panel",
            return_value=ResearchPanelResult(pd.DataFrame(), {"end": "2026-07-10"}),
        ), patch(
            "stock_analyze.markets.cn_qdii_etf.shadow_research.run_shadow_research",
            return_value=result,
        ) as run, patch(
            "stock_analyze.markets.cn_qdii_etf.shadow_research.write_shadow_artifacts",
            return_value={"report": Path(tmp) / "report.md"},
        ):
            status = cli.main(
                [
                    "qdii-shadow-research",
                    "--start", "2023-07-12",
                    "--end", "2026-07-10",
                    "--cache-dir", str(Path(tmp) / "cache"),
                    "--catalog", str(Path(tmp) / "catalog.json"),
                    "--output-root", tmp,
                    "--refresh-data",
                    "--min-signal-weeks", "4",
                ]
            )

        self.assertEqual(status, 0)
        provider._fund_daily.assert_called_once_with("159866.SZ", "20260710")
        self.assertEqual(run.call_args.kwargs["min_signal_weeks"], 4)


if __name__ == "__main__":
    unittest.main()
