from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.research_panel import build_research_panel


class QDIIPointInTimeResearchPanelTests(unittest.TestCase):
    def _write_daily(self, cache: Path, code: str) -> None:
        safe_code = code.replace(".", "_")
        pd.DataFrame(
            {
                "ts_code": [code] * 4,
                "trade_date": ["20240108", "20240109", "20240110", "20240111"],
                "open": [1.0, 1.1, 1.2, 1.3],
                "high": [1.1, 1.2, 1.3, 1.4],
                "low": [0.9, 1.0, 1.1, 1.2],
                "close": [1.05, 1.15, 1.25, 1.35],
                "vol": [100, 110, 120, 130],
                "amount": [10, 20, 30, 40],
            }
        ).to_csv(cache / f"fund_daily_{safe_code}_20240111.csv", index=False)

    def _write_universe(self, root: Path, payload: dict) -> Path:
        path = root / "universe.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_panel_filters_each_trade_date_with_catalog_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            self._write_daily(cache, "159823.SZ")
            universe = self._write_universe(
                root,
                {
                    "schema_version": 3,
                    "as_of": "2024-01-11",
                    "universe_hash": "pit-hash",
                    "source_status": "catalog_observations",
                    "catalog_rows": [
                        {
                            "code": "159823.SZ",
                            "name": "Example QDII ETF",
                            "scope": "us_exposure",
                            "list_date": "2020-10-23",
                            "status": "L",
                            "observation_date": "2024-01-01",
                        },
                        {
                            "code": "159823.SZ",
                            "name": "Example QDII ETF",
                            "scope": "us_exposure",
                            "list_date": "2020-10-23",
                            "delist_date": "2024-01-10",
                            "status": "D",
                            "observation_date": "2024-01-10",
                        },
                    ],
                },
            )

            result = build_research_panel(
                cache,
                universe,
                start="2024-01-08",
                end="2024-01-11",
            )

            self.assertEqual(
                result.frame["trade_date"].tolist(),
                ["2024-01-08", "2024-01-09"],
            )
            self.assertEqual(result.metadata["quality"], "available")
            self.assertEqual(result.metadata["universe_as_of"], "2024-01-11")
            self.assertEqual(result.metadata["provenance"]["mode"], "catalog_observations")
            self.assertFalse(result.metadata["survivorship_bias"])
            self.assertTrue(result.metadata["unbiased_universe"])
            first = result.frame.iloc[0]
            self.assertEqual(first["amount_unit"], "yuan")
            self.assertEqual(float(first["amount_thousand_yuan"]), 10.0)
            self.assertEqual(float(first["amount"]), 10_000.0)
            self.assertEqual(float(first["amount_yuan"]), 10_000.0)

    def test_legacy_current_catalog_is_marked_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            self._write_daily(cache, "513100.SH")
            universe = self._write_universe(
                root,
                {
                    "schema_version": 2,
                    "as_of": "2026-07-24",
                    "universe_hash": "current-only",
                    "source_status": "dynamic_fund_basic",
                    "scopes": {
                        "us_exposure": [
                            {
                                "code": "513100.SH",
                                "scope": "us_exposure",
                                "list_date": "2013-04-25",
                                "status": "L",
                            }
                        ],
                        "hk_exposure": [],
                    },
                },
            )

            result = build_research_panel(
                cache,
                universe,
                start="2024-01-08",
                end="2024-01-11",
            )

            self.assertEqual(result.metadata["quality"], "unavailable")
            self.assertFalse(result.metadata["unbiased_universe"])
            self.assertTrue(result.metadata["survivorship_bias"])
            self.assertEqual(
                result.metadata["provenance"]["fallback"],
                "post_hoc_interval_diagnostic",
            )
            self.assertNotEqual(
                result.metadata["source_contract"],
                "current-catalog historical replay",
            )


if __name__ == "__main__":
    unittest.main()
