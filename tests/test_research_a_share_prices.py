"""Research-only A-share price artifact tests."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.research.a_share_research_prices import (
    read_a_share_research_history,
    refresh_a_share_research_prices,
)


class _PriceClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def daily(self, **kwargs):
        self.calls.append(kwargs)
        code = str(kwargs["ts_code"])
        return pd.DataFrame([
            {"ts_code": code, "trade_date": "20260821", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "vol": 1000.0, "amount": 10000.0},
            {"ts_code": code, "trade_date": "20260820", "open": 9.5, "high": 10.2, "low": 9.0, "close": 10.0, "vol": 900.0, "amount": 9000.0},
        ])


class AShareResearchPricesTests(unittest.TestCase):
    def test_refreshes_exact_scope_to_durable_history_and_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"as_of": "20260822", "a_share": {"records": [
                {"ts_code": "000012.SZ", "research_only": True, "research_scopes": ["csi1000"]},
                {"ts_code": "000001.SZ", "research_only": True, "research_scopes": ["hs300"]},
            ]}}), encoding="utf-8")
            client = _PriceClient()

            result = refresh_a_share_research_prices(
                repo_root=root,
                pro_client=client,
                as_of="20260822",
                scope="csi1000",
            )
            history = read_a_share_research_history(root, "000012.SZ")

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["completed"], 1)
            self.assertEqual([call["ts_code"] for call in client.calls], ["000012.SZ"])
            self.assertEqual(history[0]["date"], "2026-08-20")
            self.assertEqual(history[-1]["close"], 10.5)
            self.assertTrue((root / "data/research/a_share_prices/v1/latest.json").exists())
