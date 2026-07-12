from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.research_panel import ResearchPanelResult
from stock_analyze.markets.cn_qdii_etf.shadow_research import (
    run_shadow_research,
    write_shadow_artifacts,
)
from tests.test_qdii_capacity_study import _panel


def _shadow_panel() -> tuple[ResearchPanelResult, pd.DataFrame]:
    equity = _panel().frame.copy()
    equity["scope"] = "japan_exposure"
    equity["asset_class"] = "global_equity"
    commodity = equity.copy()
    commodity["code"] = commodity["code"].str.replace("510", "520", regex=False)
    commodity["scope"] = "commodity_oil"
    commodity["asset_class"] = "commodity"
    commodity["index_key"] = commodity["index_key"].map(lambda value: f"oil_{value}")
    panel = pd.concat([equity, commodity], ignore_index=True)
    catalog = panel.groupby("code", as_index=False).tail(1)[
        ["code", "name", "scope", "asset_class", "index_key", "theme", "list_date"]
    ].copy()
    catalog["research_scope"] = catalog["scope"]
    catalog["product_type"] = "etf"
    catalog["promotion_status"] = "shadow_ready"
    return ResearchPanelResult(panel, {"universe_hash": "shadow", "survivorship_bias": True}), catalog


class QDIIShadowResearchTests(unittest.TestCase):
    def test_runs_asset_specific_shadow_models_without_live_accounts(self) -> None:
        panel, catalog = _shadow_panel()

        result = run_shadow_research(
            panel,
            catalog,
            start="2024-01-02",
            end="2024-07-29",
            min_signal_weeks=4,
        )

        self.assertEqual(set(result.metrics["asset_class"]), {"global_equity", "commodity"})
        self.assertEqual(set(result.metrics["factor_model"]), {"global_equity_v1", "commodity_v1"})
        self.assertTrue((result.metrics["mode"] == "research_only").all())
        self.assertGreater(len(result.nav), 0)
        self.assertGreater(len(result.trades), 0)
        self.assertIn("premium_persistence_20", result.summary["factor_models"]["commodity_v1"])

    def test_writes_only_research_artifacts(self) -> None:
        panel, catalog = _shadow_panel()
        result = run_shadow_research(
            panel,
            catalog,
            start="2024-01-02",
            end="2024-07-29",
            min_signal_weeks=4,
        )

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_shadow_artifacts(result, Path(tmp), end_date="2024-07-29")

            self.assertTrue(paths["summary"].exists())
            self.assertTrue(paths["report"].exists())
            self.assertIn("research/shadow", str(paths["summary"]))
            self.assertFalse((Path(tmp) / "data" / "cn_qdii_etf" / "codex" / "state.json").exists())
            self.assertIn("研究模式", paths["report"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
