from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.research_panel import (
    ResearchPanelError,
    build_research_panel,
)


class QDIIResearchPanelTests(unittest.TestCase):
    def _write_universe(self, root: Path, rows: list[dict]) -> Path:
        path = root / "universe_latest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "as_of": "2026-07-12",
                    "universe_hash": "panel-hash",
                    "source_status": "dynamic_fund_basic",
                    "scopes": {"us_exposure": rows, "hk_exposure": []},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def _write_daily(self, cache: Path, suffix: str, closes: list[float]) -> None:
        pd.DataFrame(
            {
                "ts_code": ["513100.SH"] * 4,
                "trade_date": ["20240102", "20240103", "20240104", "20240105"],
                "open": [0.9, 1.0, 1.1, 1.2],
                "high": [1.0, 1.1, 1.2, 1.3],
                "low": [0.8, 0.9, 1.0, 1.1],
                "close": closes,
                "vol": [100, 110, 120, 130],
                "amount": [10, 20, 30, 40],
            }
        ).to_csv(cache / f"fund_daily_513100_SH_{suffix}.csv", index=False)

    def test_builds_point_in_time_panel_from_latest_full_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            universe = self._write_universe(
                root,
                [
                    {
                        "code": "513100.SH",
                        "name": "纳指ETF",
                        "scope": "us_exposure",
                        "index_key": "nasdaq_100",
                        "theme": "纳斯达克100",
                        "list_date": "2024-01-03",
                        "management_fee": 0.5,
                    }
                ],
            )
            self._write_daily(cache, "20260101", [9.0, 9.0, 9.0, 9.0])
            self._write_daily(cache, "20260712", [1.0, 1.1, 1.2, 1.3])
            pd.DataFrame(
                {
                    "ts_code": ["513100.SH"] * 4,
                    "trade_date": ["20240102", "20240103", "20240104", "20240105"],
                    "adj_factor": [1.0, 1.0, 1.0, 2.0],
                }
            ).to_csv(cache / "fund_adj_513100_SH_20260712.csv", index=False)
            pd.DataFrame(
                {
                    "ts_code": ["513100.SH", "513100.SH"],
                    "ann_date": ["20240104", "20240106"],
                    "nav_date": ["20240103", "20240105"],
                    "unit_nav": [1.0, 1.25],
                    "adj_nav": [1.0, 1.25],
                }
            ).to_csv(cache / "fund_nav_513100_SH_20260712.csv", index=False)
            pd.DataFrame(
                {
                    "ts_code": ["513100.SH"],
                    "trade_date": ["20240103"],
                    "fd_share": [50],
                }
            ).to_csv(cache / "fund_share_513100_SH_20260712.csv", index=False)

            result = build_research_panel(
                cache,
                universe,
                start="2024-01-02",
                end="2024-01-05",
            )

            frame = result.frame.reset_index(drop=True)
            self.assertEqual(frame["code"].tolist(), ["513100.SH"] * 3)
            self.assertEqual(frame["trade_date"].tolist()[0], "2024-01-03")
            self.assertEqual(frame["close"].tolist(), [1.1, 1.2, 1.3])
            self.assertEqual(frame["amount_yuan"].tolist(), [20_000, 30_000, 40_000])
            self.assertEqual(frame["adj_close"].tolist(), [1.1, 1.2, 2.6])
            self.assertTrue(pd.isna(frame.iloc[0]["nav"]))
            self.assertEqual(frame.iloc[1]["nav"], 1.0)
            self.assertAlmostEqual(frame.iloc[1]["discount_premium"], 0.2)
            self.assertTrue(pd.isna(frame.iloc[0]["fund_size_yuan"]))
            self.assertEqual(frame.iloc[1]["fund_size_yuan"], 500_000.0)
            self.assertEqual(result.metadata["universe_hash"], "panel-hash")
            self.assertTrue(result.metadata["survivorship_bias"])
            self.assertEqual(result.metadata["daily_files"]["513100.SH"], "fund_daily_513100_SH_20260712.csv")

    def test_missing_full_daily_history_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            universe = self._write_universe(
                root,
                [
                    {
                        "code": "513100.SH",
                        "scope": "us_exposure",
                        "index_key": "nasdaq_100",
                        "list_date": "2020-01-01",
                    }
                ],
            )

            with self.assertRaisesRegex(ResearchPanelError, "missing_daily_history:513100.SH"):
                build_research_panel(cache, universe, start="2024-01-01", end="2024-12-31")

    def test_empty_universe_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            cache.mkdir()
            universe = self._write_universe(root, [])

            with self.assertRaisesRegex(ResearchPanelError, "empty_universe"):
                build_research_panel(cache, universe, start="2024-01-01", end="2024-12-31")


if __name__ == "__main__":
    unittest.main()
