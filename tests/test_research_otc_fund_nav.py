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
    def __init__(
        self,
        *,
        fail_codes: set[str] | None = None,
        include_future_announcement: bool = False,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_codes = fail_codes or set()
        self.include_future_announcement = include_future_announcement

    def fund_nav(self, **kwargs):
        self.calls.append(kwargs)
        code = str(kwargs["ts_code"])
        if code in self.fail_codes:
            raise RuntimeError("source_unavailable")
        rows = [
            {"ts_code": kwargs["ts_code"], "ann_date": "20260822", "nav_date": "20260821", "unit_nav": 1.10, "accum_nav": 1.21, "adj_nav": 1.21},
            {"ts_code": kwargs["ts_code"], "ann_date": "20260821", "nav_date": "20260820", "unit_nav": 1.00, "accum_nav": 1.10, "adj_nav": 1.10},
            {"ts_code": kwargs["ts_code"], "ann_date": "20260820", "nav_date": "20260819", "unit_nav": 0.95, "accum_nav": 1.00, "adj_nav": 1.00},
        ]
        if self.include_future_announcement:
            rows.append({"ts_code": code, "ann_date": "20260823", "nav_date": "20260821", "unit_nav": 1.50, "accum_nav": 1.50, "adj_nav": 1.50})
        return pd.DataFrame(rows)


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

    def test_rejects_nav_announced_after_the_requested_as_of_date(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"as_of": "20260822", "funds": {"records": [
                {"ts_code": "008401.OF", "market_source": "otc", "research_only": True, "overseas_scope": "sp_500"},
            ]}}), encoding="utf-8")

            refresh_otc_fund_nav(
                repo_root=root,
                pro_client=_NavClient(include_future_announcement=True),
                as_of="20260822",
                scopes=("sp_500",),
            )
            series, _, _ = read_otc_fund_nav_detail(root, "008401.OF")

            self.assertEqual(series[-1]["adjustedNav"], 1.21)

    def test_partial_run_preserves_the_last_complete_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"as_of": "20260822", "funds": {"records": [
                {"ts_code": "008401.OF", "market_source": "otc", "research_only": True, "overseas_scope": "sp_500"},
                {"ts_code": "008402.OF", "market_source": "otc", "research_only": True, "overseas_scope": "sp_500"},
            ]}}), encoding="utf-8")

            refresh_otc_fund_nav(
                repo_root=root, pro_client=_NavClient(), as_of="20260822", scopes=("sp_500",)
            )
            partial = refresh_otc_fund_nav(
                repo_root=root,
                pro_client=_NavClient(fail_codes={"008402.OF"}),
                as_of="20260823",
                scopes=("sp_500",),
            )
            latest = json.loads(
                (root / "data/research/otc_fund_nav/v1/latest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(partial["status"], "partial")
            self.assertEqual(latest["status"], "complete")
            self.assertEqual(latest["as_of"], "20260822")
