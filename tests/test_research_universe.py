import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.universe import attach_point_in_time_universe


class ResearchUniverseTest(unittest.TestCase):
    def test_a_share_uses_historical_index_snapshots_per_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weight_root = root / "data" / "shared" / "backtest_cache" / "index_weight"
            weight_root.mkdir(parents=True)
            pd.DataFrame({"con_code": ["000001.SZ"]}).to_csv(
                weight_root / "000300_2026-01.csv", index=False
            )
            pd.DataFrame({"con_code": ["000002.SZ"]}).to_csv(
                weight_root / "000300_2026-02.csv", index=False
            )
            pd.DataFrame({"con_code": ["000003.SZ"]}).to_csv(
                weight_root / "000905_2026-01.csv", index=False
            )
            features = pd.DataFrame([
                {"code": "000001", "trade_date": "20260115", "close": 10.0},
                {"code": "000002", "trade_date": "20260115", "close": 11.0},
                {"code": "000003", "trade_date": "20260115", "close": 11.5},
                {"code": "000001", "trade_date": "20260215", "close": 12.0},
                {"code": "000002", "trade_date": "20260215", "close": 13.0},
                {"code": "000003", "trade_date": "20260215", "close": 14.0},
            ])
            accounts = [
                {"id": "hs300", "scope": "hs300", "benchmark": "000300"},
                {"id": "zz500", "scope": "zz500", "benchmark": "000905"},
            ]

            result = attach_point_in_time_universe(
                features,
                repo_root=root,
                market="a_share",
                accounts=accounts,
                as_of="2026-02-28",
            )

        observed = set(zip(result.frame["trade_date"], result.frame["code"], result.frame["account_id"]))
        self.assertEqual(observed, {
            ("20260115", "000001", "hs300"),
            ("20260115", "000003", "zz500"),
            ("20260215", "000002", "hs300"),
            ("20260215", "000003", "zz500"),
        })
        self.assertTrue(result.metadata["unbiased_universe"])
        self.assertEqual(result.metadata["membership_source"], "monthly_index_weight")

    def test_a_share_partial_account_snapshots_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weight_root = root / "data" / "shared" / "backtest_cache" / "index_weight"
            weight_root.mkdir(parents=True)
            pd.DataFrame({"con_code": ["000001.SZ"]}).to_csv(
                weight_root / "000300_2026-01.csv", index=False
            )
            pd.DataFrame(columns=["con_code"]).to_csv(
                weight_root / "000905_2026-01.csv", index=False
            )
            features = pd.DataFrame([
                {"code": "000001", "trade_date": "20260115", "close": 10.0},
                {"code": "000002", "trade_date": "20260115", "close": 11.0},
            ])

            result = attach_point_in_time_universe(
                features,
                repo_root=root,
                market="a_share",
                accounts=[
                    {"id": "hs300", "scope": "hs300", "benchmark": "000300"},
                    {"id": "zz500", "scope": "zz500", "benchmark": "000905"},
                ],
                as_of="2026-01-31",
            )

        self.assertFalse(result.metadata["unbiased_universe"])
        self.assertIn(
            "empty_snapshot:zz500:202601",
            result.metadata["quality_reasons"],
        )

    def test_qdii_reconstructs_listing_interval_and_keeps_delisted_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            cache.mkdir(parents=True)
            pd.DataFrame([
                {
                    "ts_code": "513100.SH",
                    "name": "纳指ETF",
                    "benchmark": "纳斯达克100指数(QDII)",
                    "status": "D",
                    "list_date": "20130515",
                    "delist_date": "20240101",
                    "m_fee": 0.5,
                },
                {
                    "ts_code": "513180.SH",
                    "name": "恒生科技ETF",
                    "benchmark": "恒生科技指数",
                    "status": "L",
                    "list_date": "20210524",
                    "delist_date": "",
                    "m_fee": 0.5,
                },
            ]).to_csv(cache / "fund_basic_E_v2.csv", index=False)
            features = pd.DataFrame([
                {"code": "513100", "trade_date": "20230601", "close": 1.0},
                {"code": "513100", "trade_date": "20240601", "close": 1.1},
                {"code": "513180", "trade_date": "20230601", "close": 1.2},
            ])
            accounts = [
                {"id": "us_exposure", "scope": "us_exposure", "benchmark": "513100.SH"},
                {"id": "hk_exposure", "scope": "hk_exposure", "benchmark": "159920.SZ"},
            ]

            result = attach_point_in_time_universe(
                features,
                repo_root=root,
                market="cn_qdii_etf",
                accounts=accounts,
                as_of="2026-08-08",
            )

        self.assertEqual(
            set(zip(result.frame["trade_date"], result.frame["code"], result.frame["account_id"])),
            {
                ("20230601", "513100", "us_exposure"),
                ("20230601", "513180", "hk_exposure"),
            },
        )
        self.assertTrue(result.metadata["unbiased_universe"])
        self.assertEqual(result.metadata["membership_source"], "tushare_fund_basic_listing_interval")

    def test_missing_membership_source_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            features = pd.DataFrame([
                {"code": "000001", "trade_date": "20260115", "close": 10.0},
            ])
            result = attach_point_in_time_universe(
                features,
                repo_root=Path(tmp),
                market="a_share",
                accounts=[{"id": "hs300", "scope": "hs300", "benchmark": "000300"}],
                as_of="2026-02-28",
            )

        self.assertFalse(result.metadata["unbiased_universe"])
        self.assertEqual(result.metadata["quality"], "unavailable")
        self.assertEqual(result.frame["universe_quality"].unique().tolist(), ["unavailable"])


if __name__ == "__main__":
    unittest.main()
