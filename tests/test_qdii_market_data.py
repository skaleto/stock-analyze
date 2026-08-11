from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.market_data import prepare_market_data


class QDIIMarketDataTests(unittest.TestCase):
    def test_prepare_market_data_warms_every_catalog_code(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_dir = root / "data/research/features/cn_qdii_etf"
            feature_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"code": "513100", "trade_date": "20260721"},
                    {"code": "159920", "trade_date": "20260721"},
                    {"code": "520870", "trade_date": "20260721"},
                ]
            ).to_parquet(feature_dir / "20260721.parquet", index=False)
            state_dir = root / "data/cn_qdii_etf/codex"
            state_dir.mkdir(parents=True)
            (state_dir / "state.json").write_text(
                '{"accounts":{"us_exposure":{"positions":{"513400":{}}}}}',
                encoding="utf-8",
            )
            calls: list[tuple[str, str]] = []
            provider = SimpleNamespace(
                _fund_basic=lambda **_kwargs: pd.DataFrame(
                    [
                        {
                            "ts_code": "513100.SH",
                            "name": "纳指ETF",
                            "benchmark": "纳斯达克100指数",
                            "list_date": "20130515",
                            "status": "L",
                            "m_fee": 0.6,
                        },
                        {
                            "ts_code": "159920.SZ",
                            "name": "恒生ETF",
                            "benchmark": "恒生指数",
                            "list_date": "20121022",
                            "status": "L",
                            "m_fee": 0.6,
                        },
                    ]
                ),
                _fund_daily=lambda code, as_of: (
                    calls.append((code, as_of))
                    or pd.DataFrame(
                        [
                            {
                                "ts_code": code,
                                "trade_date": "20260723",
                                "close": 1.0,
                            }
                        ]
                    )
                ),
                universe_snapshot=lambda _as_of: {
                    "scopes": {
                        "us_exposure": [{"code": "159612"}],
                        "hk_exposure": [{"code": "159920"}],
                    }
                },
                persist_health=lambda: None,
            )
            catalog = pd.DataFrame(
                [
                    {"code": "513100.SH"},
                    {"code": "159920.SZ"},
                ]
            )
            with (
                patch(
                    "stock_analyze.markets.cn_qdii_etf.market_data.make_provider",
                    return_value=provider,
                ),
                patch(
                    "stock_analyze.markets.cn_qdii_etf.market_data.build_research_catalog",
                    return_value=catalog,
                ),
            ):
                result = prepare_market_data(
                    repo_root=root,
                    as_of="2026-07-23",
                )

            snapshot = root / "data/cn_qdii_etf/shared/market_snapshot_2026-07-23.json"
            snapshot_exists = snapshot.is_file()

        self.assertEqual(
            calls,
            [
                ("159612.SZ", "20260723"),
                ("159920.SZ", "20260723"),
                ("513100.SH", "20260723"),
                ("513400.SH", "20260723"),
                ("520870.SH", "20260723"),
            ],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["catalog_codes"], 2)
        self.assertEqual(result["universe_codes"], 2)
        self.assertEqual(result["target_codes"], 5)
        self.assertEqual(result["fresh_codes"], 5)
        self.assertTrue(snapshot_exists)


if __name__ == "__main__":
    unittest.main()
