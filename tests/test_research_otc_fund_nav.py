"""Research-only OTC fund NAV artifact tests."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.research.otc_fund_nav import (
    read_otc_fund_nav_detail,
    refresh_otc_fund_nav,
)


class _NavClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def fund_nav(self, **kwargs):
        self.calls.append(kwargs)
        return pd.DataFrame([
            {"ts_code": kwargs["ts_code"], "ann_date": "20260822", "nav_date": "20260821", "unit_nav": 1.10, "accum_nav": 1.21, "adj_nav": 1.21},
            {"ts_code": kwargs["ts_code"], "ann_date": "20260821", "nav_date": "20260820", "unit_nav": 1.00, "accum_nav": 1.10, "adj_nav": 1.10},
            {"ts_code": kwargs["ts_code"], "ann_date": "20260820", "nav_date": "20260819", "unit_nav": 0.95, "accum_nav": 1.00, "adj_nav": 1.00},
        ])


class OtcFundNavTests(unittest.TestCase):
    def test_persists_adjusted_nav_and_builds_return_drawdown_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"as_of": "20260822", "funds": {"records": [
                {"ts_code": "008401.OF", "market_source": "otc", "research_only": True, "overseas_scope": "sp_500"},
                {"ts_code": "000001.OF", "market_source": "otc", "research_only": True, "overseas_scope": "global_exposure"},
            ]}}), encoding="utf-8")
            client = _NavClient()

            result = refresh_otc_fund_nav(
                repo_root=root,
                pro_client=client,
                as_of="20260822",
                scopes=("sp_500",),
            )
            series, latest, metrics = read_otc_fund_nav_detail(root, "008401.OF")

            self.assertEqual(result["completed"], 1)
            self.assertEqual([call["ts_code"] for call in client.calls], ["008401.OF"])
            self.assertEqual(series[-1]["adjustedNav"], 1.21)
            self.assertEqual(latest["date"], "2026-08-21")
            self.assertIn("max_drawdown", {item["key"] for item in metrics})
