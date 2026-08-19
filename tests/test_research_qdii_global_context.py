from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from stock_analyze.research.qdii_global_context import (
    ASSET_ROOT,
    attach_global_context,
    backfill_global_context,
    load_contract,
    load_verified_global_context,
    mapping_for_index_key,
    repair_feature_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/research/qdii_global_context_v1.yaml"


class FakePro:
    @staticmethod
    def _dates() -> pd.DatetimeIndex:
        return pd.date_range("2018-01-01", "2018-01-10", freq="D")

    def index_global(self, *, ts_code, start_date, end_date):
        dates = self._dates()
        return pd.DataFrame({
            "ts_code": ts_code,
            "trade_date": dates.strftime("%Y%m%d"),
            "close": np.arange(len(dates), dtype=float) + 100.0,
        })

    def fx_daily(self, *, ts_code, start_date, end_date):
        dates = self._dates()
        return pd.DataFrame({
            "ts_code": ts_code,
            "trade_date": dates.strftime("%Y%m%d"),
            "bid_close": np.arange(len(dates), dtype=float) / 100.0 + 7.0,
        })


class QDIIGlobalContextTest(unittest.TestCase):
    def test_contract_distinguishes_exact_and_family_proxy(self):
        contract = load_contract(CONTRACT)

        self.assertEqual(mapping_for_index_key("sp_500", contract), ("SPX", "exact"))
        self.assertEqual(
            mapping_for_index_key("nasdaq_100", contract),
            ("IXIC", "family_proxy"),
        )
        self.assertIsNone(mapping_for_index_key("unknown_index", contract))

    def test_point_in_time_attachment_uses_prior_source_day_and_index_mapping(self):
        contract = load_contract(CONTRACT)
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        index_rows = []
        for code, slope in (("SPX", 1.0), ("IXIC", 2.0), ("DJI", 0.5), ("HSI", -0.5)):
            for number, day in enumerate(dates):
                index_rows.append({
                    "ts_code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "close": 100.0 + slope * number,
                })
        fx = pd.DataFrame({
            "ts_code": "USDCNH.FXCM",
            "trade_date": dates.strftime("%Y%m%d"),
            "bid_close": 7.0 + np.arange(len(dates)) / 100.0,
        })
        features = pd.DataFrame([
            {"code": "A", "trade_date": "20240130", "index_key": "sp_500"},
            {"code": "B", "trade_date": "20240130", "index_key": "nasdaq_100"},
            {"code": "C", "trade_date": "20240130", "index_key": "unknown_index"},
        ])

        attached = attach_global_context(
            features,
            {"index_global": pd.DataFrame(index_rows), "fx_daily": fx},
            contract=contract,
        ).set_index("code")

        self.assertEqual(attached.loc["A", "global_source_index_code"], "SPX")
        self.assertEqual(attached.loc["A", "global_mapping_kind"], "exact")
        self.assertEqual(attached.loc["B", "global_source_index_code"], "IXIC")
        self.assertEqual(attached.loc["B", "global_mapping_kind"], "family_proxy")
        self.assertNotEqual(
            attached.loc["A", "global_index_momentum"],
            attached.loc["B", "global_index_momentum"],
        )
        self.assertEqual(attached.loc["A", "global_source_trade_date"], "20240129")
        self.assertEqual(attached.loc["A", "global_available_date"], "20240130")
        self.assertEqual(attached.loc["A", "fx_source_trade_date"], "20240129")
        self.assertEqual(attached.loc["A", "fx_available_date"], "20240130")
        self.assertTrue(pd.isna(attached.loc["C", "global_index_momentum"]))
        self.assertFalse(pd.isna(attached.loc["C", "rmb_depreciation"]))

    def test_backfill_is_checksummed_idempotent_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "contract.yaml"
            payload = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
            payload["minimum_rows_per_index"] = 5
            payload["minimum_fx_rows"] = 5
            config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            first = backfill_global_context(
                root, FakePro(), start_date="2018-01-01",
                end_date="2018-01-10", contract_path=config,
            )
            second = backfill_global_context(
                root, FakePro(), start_date="2018-01-01",
                end_date="2018-01-10", contract_path=config,
            )
            verified = load_verified_global_context(
                root, contract_path=config, as_of="2018-01-10"
            )
            index_path = root / ASSET_ROOT / "index_global.parquet"
            index_path.write_bytes(index_path.read_bytes() + b"tamper")

            with self.assertRaisesRegex(ValueError, "file_hash"):
                load_verified_global_context(
                    root, contract_path=config, as_of="2018-01-10"
                )

        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(set(verified), {"index_global", "fx_daily"})
        self.assertEqual(first["index_counts"], {"DJI": 10, "HSI": 10, "IXIC": 10, "SPX": 10})

    def test_snapshot_repair_preserves_identity_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "contract.yaml"
            payload = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
            payload["minimum_rows_per_index"] = 5
            payload["minimum_fx_rows"] = 5
            payload["minimum_feature_coverage"] = 0.0
            config.write_text(
                yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
            )
            backfill_global_context(
                root, FakePro(), start_date="2018-01-01",
                end_date="2018-01-10", contract_path=config,
            )
            path = (
                root / "data/research/features/cn_qdii_etf/20180110.parquet"
            )
            path.parent.mkdir(parents=True)
            original = pd.DataFrame([
                {
                    "code": "A", "trade_date": "20180110",
                    "index_key": "sp_500", "close": 1.0,
                },
                {
                    "code": "B", "trade_date": "20180110",
                    "index_key": "nasdaq_100", "close": 2.0,
                },
            ])
            original.to_parquet(path, index=False)

            result = repair_feature_snapshot(
                root, snapshot_date="20180110", contract_path=config
            )
            repaired = pd.read_parquet(path)

        self.assertEqual(result["rows"], 2)
        self.assertNotEqual(result["old_sha256"], result["new_sha256"])
        self.assertEqual(
            repaired[["code", "trade_date"]].to_dict("records"),
            original[["code", "trade_date"]].to_dict("records"),
        )
        self.assertEqual(
            set(repaired["global_mapping_kind"]),
            {"exact", "family_proxy"},
        )

    def test_snapshot_repair_does_not_overwrite_when_coverage_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "contract.yaml"
            payload = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
            payload["minimum_rows_per_index"] = 5
            payload["minimum_fx_rows"] = 5
            config.write_text(
                yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
            )
            backfill_global_context(
                root, FakePro(), start_date="2018-01-01",
                end_date="2018-01-10", contract_path=config,
            )
            path = (
                root / "data/research/features/cn_qdii_etf/20180110.parquet"
            )
            path.parent.mkdir(parents=True)
            pd.DataFrame([{
                "code": "A", "trade_date": "20180110",
                "index_key": "sp_500", "close": 1.0,
            }]).to_parquet(path, index=False)
            before = path.read_bytes()

            with self.assertRaisesRegex(
                ValueError, "feature_coverage"
            ):
                repair_feature_snapshot(
                    root, snapshot_date="20180110", contract_path=config
                )

            after = path.read_bytes()

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
