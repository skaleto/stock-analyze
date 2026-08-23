from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from stock_analyze.research.a_share_all_cap_sources import (
    collect_all_cap_sources,
    load_verified_all_cap_sources,
    publish_all_cap_sources,
)


SW2021_L1_CODES = (
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI",
    "801110.SI", "801120.SI", "801130.SI", "801140.SI", "801150.SI",
    "801160.SI", "801170.SI", "801180.SI", "801200.SI", "801210.SI",
    "801230.SI", "801710.SI", "801720.SI", "801730.SI", "801740.SI",
    "801750.SI", "801760.SI", "801770.SI", "801780.SI", "801790.SI",
    "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
)

SLEEVE_INDEX_CODES = (
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "932000.CSI",
)
ALL_INDEX_CODES = (*SLEEVE_INDEX_CODES, "000985.CSI")


class FakePro:
    OPEN_DATES = ("20230831", "20230901", "20240102")

    def __init__(
        self,
        *,
        malformed_index_daily: bool = False,
        overlapping_membership: bool = False,
        missing_index_daily_date: str | None = None,
        extra_index_daily_date: str | None = None,
    ) -> None:
        self.malformed_index_daily = malformed_index_daily
        self.overlapping_membership = overlapping_membership
        self.missing_index_daily_date = missing_index_daily_date
        self.extra_index_daily_date = extra_index_daily_date
        self.index_weight_calls: list[dict[str, object]] = []
        self.index_daily_calls: list[dict[str, object]] = []
        self.index_member_calls: list[dict[str, object]] = []
        self.trade_cal_calls: list[dict[str, object]] = []
        self.stk_limit_calls: list[dict[str, object]] = []

    def index_classify(self, **kwargs: object) -> pd.DataFrame:
        self.index_classify_call = kwargs
        return pd.DataFrame(
            {
                "index_code": list(reversed(SW2021_L1_CODES)),
                "level": "L1",
                "src": "SW2021",
            }
        )

    def index_weight(self, **kwargs: object) -> pd.DataFrame:
        self.index_weight_calls.append(dict(kwargs))
        index_code = str(kwargs["index_code"])
        start_date = str(kwargs["start_date"])
        if index_code == "932000.CSI" and start_date < "20230901":
            raise AssertionError("CSI2000 pre-inception weights were requested")
        member_number = SLEEVE_INDEX_CODES.index(index_code) + 1
        return pd.DataFrame(
            [
                {
                    "index_code": index_code,
                    "con_code": f"{member_number:06d}.SZ",
                    "trade_date": start_date,
                    "weight": 100.0,
                }
            ]
        )

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        self.index_daily_calls.append(dict(kwargs))
        code = str(kwargs["ts_code"])
        dates = [
            value
            for value in self.OPEN_DATES
            if value != self.missing_index_daily_date
        ]
        if self.extra_index_daily_date:
            dates.append(self.extra_index_daily_date)
        rows = [
            {
                "ts_code": code,
                "trade_date": trade_date,
                "open": 100.0 + number,
                "high": 101.0 + number,
                "low": 99.0 + number,
                "close": 100.5 + number,
                "vol": 1000.0 + number,
            }
            for number, trade_date in enumerate(dates)
        ]
        if self.malformed_index_daily:
            for row in rows:
                row.pop("close")
        return pd.DataFrame(rows)

    def index_member_all(self, **kwargs: object) -> pd.DataFrame:
        self.index_member_calls.append(dict(kwargs))
        l1_code = str(kwargs["l1_code"])
        is_new = str(kwargs["is_new"])
        industry_number = SW2021_L1_CODES.index(l1_code) + 1
        stock_number = industry_number if is_new == "Y" else industry_number + 100
        in_date = "20230101" if is_new == "Y" else "20200101"
        out_date: object = pd.NA if is_new == "Y" else "20210101"
        if self.overlapping_membership and l1_code == SW2021_L1_CODES[0]:
            stock_number = 999999
            in_date = "20230101" if is_new == "Y" else "20220101"
            out_date = pd.NA if is_new == "Y" else "20240101"
        return pd.DataFrame(
            [
                {
                    "l1_code": l1_code,
                    "l2_code": f"{industry_number:06d}.SI",
                    "l3_code": f"{industry_number:06d}.SI",
                    "ts_code": f"{stock_number:06d}.SZ",
                    "in_date": in_date,
                    "out_date": out_date,
                    "is_new": is_new,
                },
                {
                    "l1_code": l1_code,
                    "l2_code": f"{industry_number:06d}.SI",
                    "l3_code": f"{industry_number:06d}.SI",
                    "ts_code": f"{stock_number:06d}.SZ",
                    "in_date": in_date,
                    "out_date": out_date,
                    "is_new": is_new,
                },
            ]
        )

    def trade_cal(self, **kwargs: object) -> pd.DataFrame:
        self.trade_cal_calls.append(dict(kwargs))
        start = str(kwargs["start_date"])
        end = str(kwargs["end_date"])
        return pd.DataFrame(
            [
                {"cal_date": value, "is_open": "1"}
                for value in self.OPEN_DATES
                if start <= value <= end
            ]
        )

    def stk_limit(self, **kwargs: object) -> pd.DataFrame:
        self.stk_limit_calls.append(dict(kwargs))
        trade_date = str(kwargs["trade_date"])
        return pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_date": trade_date,
                    "pre_close": 10.0,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
            ]
        )


class AllCapSourceCollectorTests(unittest.TestCase):
    START = date(2023, 8, 31)
    END = date(2024, 1, 3)

    def collect(self, root: Path, provider: FakePro | None = None):
        client = provider or FakePro()
        result = collect_all_cap_sources(
            repo_root=root,
            pro_client=client,
            start=self.START,
            end=self.END,
        )
        return result, client

    @staticmethod
    def _canonical_hash(payload: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def rehashed_staging(
        self,
        publication_dir: Path,
        name: str,
        mutate,
    ) -> Path:
        staging = publication_dir.parent / f".all-cap-sources-{name}"
        shutil.copytree(publication_dir, staging)
        manifest_path = staging / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(payload)
        payload.pop("manifest_sha256", None)
        payload["manifest_sha256"] = self._canonical_hash(payload)
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return staging

    def test_collects_reference_indexes_and_both_industry_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, client = self.collect(root)
            manifest = load_verified_all_cap_sources(root)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(manifest.index_daily),
            {
                "000300.SH",
                "000905.SH",
                "000852.SH",
                "932000.CSI",
                "000985.CSI",
            },
        )
        self.assertEqual(
            set(manifest.industry_membership["is_new"].dropna()),
            {"Y", "N"},
        )
        self.assertEqual(
            {(call["l1_code"], call["is_new"]) for call in client.index_member_calls},
            {
                (industry_code, is_new)
                for industry_code in SW2021_L1_CODES
                for is_new in ("Y", "N")
            },
        )
        self.assertEqual(len(client.index_member_calls), 62)

    def test_partitions_daily_limits_and_records_csi2000_pre_inception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, client = self.collect(root)
            manifest = load_verified_all_cap_sources(root)

        self.assertEqual(
            [call["trade_date"] for call in client.stk_limit_calls],
            list(FakePro.OPEN_DATES),
        )
        self.assertEqual(
            {item["partition"] for item in manifest.metadata["partitions"]["stk_limit"]},
            {"2023", "2024"},
        )
        self.assertEqual(
            manifest.metadata["pre_inception"],
            [
                {
                    "dataset": "index_weights",
                    "ts_code": "932000.CSI",
                    "period_start": "20230831",
                    "period_end": "20230831",
                    "status": "pre_inception",
                }
            ],
        )
        micro = manifest.index_weights["932000.CSI"]
        self.assertTrue((micro["trade_date"] >= "20230901").all())
        self.assertTrue(
            all(
                str(call["start_date"]) >= "20230901"
                for call in client.index_weight_calls
                if call["index_code"] == "932000.CSI"
            )
        )
        expected_periods = (
            ("20230831", "20230831"),
            ("20230901", "20230930"),
            ("20231001", "20231031"),
            ("20231101", "20231130"),
            ("20231201", "20231231"),
            ("20240101", "20240103"),
        )
        self.assertEqual(
            client.index_weight_calls,
            [
                {
                    "index_code": index_code,
                    "start_date": period_start,
                    "end_date": period_end,
                }
                for period_start, period_end in expected_periods
                for index_code in SLEEVE_INDEX_CODES
                if not (
                    index_code == "932000.CSI" and period_end < "20230901"
                )
            ],
        )
        self.assertEqual(len(client.index_weight_calls), 23)
        self.assertEqual(
            client.index_daily_calls,
            [
                {
                    "ts_code": index_code,
                    "start_date": "20230831",
                    "end_date": "20240103",
                }
                for index_code in ALL_INDEX_CODES
            ],
        )
        self.assertEqual(len(client.index_daily_calls), 5)
        self.assertEqual(
            client.trade_cal_calls,
            [
                {
                    "exchange": "",
                    "start_date": "20230831",
                    "end_date": "20240103",
                    "is_open": "1",
                }
            ],
        )

    def test_manifest_declares_schemas_bounds_paths_and_real_compression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            manifest = load_verified_all_cap_sources(root)
            metadata = manifest.metadata
            actual_codecs = {}
            for record in metadata["files"]:
                parquet = pq.ParquetFile(manifest.publication_dir / record["path"])
                actual_codecs[record["path"]] = {
                    parquet.metadata.row_group(row_group)
                    .column(column)
                    .compression.upper()
                    for row_group in range(parquet.metadata.num_row_groups)
                    for column in range(
                        parquet.metadata.row_group(row_group).num_columns
                    )
                }

        self.assertEqual(
            set(metadata["dataset_schemas"]),
            {"index_weights", "index_daily", "industry_membership", "stk_limit"},
        )
        for dataset, schema in metadata["dataset_schemas"].items():
            with self.subTest(dataset=dataset):
                self.assertEqual(
                    schema["columns"],
                    [field["name"] for field in schema["fields"]],
                )
                self.assertTrue(all(field["dtype"] for field in schema["fields"]))
        self.assertEqual(
            metadata["dataset_schemas"]["index_daily"]["fields"][0],
            {"name": "ts_code", "dtype": "large_string"},
        )

        expected = {
            "index_weights": [
                ("all", "index_weights.parquet", "20230831", "20240101")
            ],
            "index_daily": [
                ("all", "index_daily.parquet", "20230831", "20240102")
            ],
            "industry_membership": [
                (
                    "all",
                    "industry_membership.parquet",
                    "20200101",
                    "20230101",
                )
            ],
            "stk_limit": [
                ("2023", "stk_limit/year=2023.parquet", "20230831", "20230901"),
                ("2024", "stk_limit/year=2024.parquet", "20240102", "20240102"),
            ],
        }
        for dataset, expected_records in expected.items():
            observed = metadata["partitions"][dataset]
            self.assertEqual(
                [
                    (
                        item["partition"],
                        item["path"],
                        item["min_date"],
                        item["max_date"],
                    )
                    for item in observed
                ],
                expected_records,
            )
            self.assertTrue(
                all(item["compression"] != "UNCOMPRESSED" for item in observed)
            )
            for item in observed:
                self.assertEqual(actual_codecs[item["path"]], {item["compression"]})
                self.assertNotIn("UNCOMPRESSED", actual_codecs[item["path"]])
        self.assertEqual(
            {item["path"]: item for item in metadata["files"]},
            {
                item["path"]: item
                for records in metadata["partitions"].values()
                for item in records
            },
        )

    def test_rejects_rehashed_manifest_with_false_partition_or_date_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            verified = load_verified_all_cap_sources(root)

            def false_partition(payload):
                collections = (
                    payload["files"],
                    payload["partitions"]["index_daily"],
                )
                for collection in collections:
                    record = next(
                        item
                        for item in collection
                        if item["dataset"] == "index_daily"
                    )
                    record["partition"] = "2023"

            def false_bounds(payload):
                collections = (
                    payload["files"],
                    payload["partitions"]["stk_limit"],
                )
                for collection in collections:
                    record = next(
                        item
                        for item in collection
                        if item["path"] == "stk_limit/year=2023.parquet"
                    )
                    record["max_date"] = "20231231"

            for name, mutate, error in (
                ("partition", false_partition, "all_cap_source_manifest_partition"),
                ("bounds", false_bounds, "all_cap_source_manifest_dates"),
            ):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, error):
                    publish_all_cap_sources(
                        self.rehashed_staging(verified.publication_dir, name, mutate),
                        root,
                    )

    def test_rejects_rehashed_manifest_with_false_schema_or_compression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            verified = load_verified_all_cap_sources(root)

            def false_schema(payload):
                payload["dataset_schemas"]["index_daily"]["fields"][0]["dtype"] = "int64"

            def false_compression(payload):
                collections = (
                    payload["files"],
                    payload["partitions"]["index_daily"],
                )
                for collection in collections:
                    record = next(
                        item
                        for item in collection
                        if item["dataset"] == "index_daily"
                    )
                    record["compression"] = "GZIP"

            for name, mutate, error in (
                ("schema", false_schema, "all_cap_source_manifest_schema"),
                ("compression", false_compression, "all_cap_source_manifest_compression"),
            ):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, error):
                    publish_all_cap_sources(
                        self.rehashed_staging(verified.publication_dir, name, mutate),
                        root,
                    )

    def test_rejects_missing_or_extra_index_daily_trade_date(self) -> None:
        for name, provider in (
            ("missing", FakePro(missing_index_daily_date="20230901")),
            ("extra", FakePro(extra_index_daily_date="20230904")),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_source_index_daily_calendar",
                ):
                    self.collect(Path(tmp), provider)
                self.assertEqual(len(provider.trade_cal_calls), 1)

    def test_identifier_and_date_columns_reload_as_explicit_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            manifest = load_verified_all_cap_sources(root)

            frames_and_columns = (
                (next(iter(manifest.index_daily.values())), ("ts_code", "trade_date")),
                (next(iter(manifest.index_weights.values())), ("index_code", "con_code", "trade_date")),
                (
                    manifest.industry_membership,
                    ("l1_code", "l2_code", "l3_code", "ts_code", "in_date", "out_date", "is_new"),
                ),
                (manifest.stk_limit, ("ts_code", "trade_date")),
            )

            for frame, columns in frames_and_columns:
                for column in columns:
                    with self.subTest(column=column):
                        self.assertTrue(pd.api.types.is_string_dtype(frame[column].dtype))

    def test_does_not_advance_latest_after_checksum_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            verified = load_verified_all_cap_sources(root)
            latest = root / "data/research/a_share_all_cap/v1/sources/latest.json"
            old_latest = latest.read_bytes()
            old_manifest = (verified.publication_dir / "manifest.json").read_bytes()
            staging = verified.publication_dir.parent / ".all-cap-sources-corrupt"
            shutil.copytree(verified.publication_dir, staging)
            index_daily = staging / "index_daily.parquet"
            index_daily.write_bytes(index_daily.read_bytes() + b"corrupt")

            with self.assertRaisesRegex(ValueError, "all_cap_source_checksum"):
                publish_all_cap_sources(staging, root)

            reloaded = load_verified_all_cap_sources(root)
            self.assertEqual(latest.read_bytes(), old_latest)
            self.assertEqual(
                (reloaded.publication_dir / "manifest.json").read_bytes(),
                old_manifest,
            )

    def test_rejects_invalid_interval_before_provider_calls(self) -> None:
        provider = FakePro()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError,
            "all_cap_source_interval",
        ):
            collect_all_cap_sources(
                repo_root=Path(tmp),
                pro_client=provider,
                start=date(2024, 1, 2),
                end=date(2024, 1, 1),
            )

        self.assertEqual(provider.index_daily_calls, [])
        self.assertEqual(provider.index_member_calls, [])

    def test_rejects_malformed_source_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "all_cap_source_index_daily_schema"):
                self.collect(root, FakePro(malformed_index_daily=True))

            self.assertFalse(
                (root / "data/research/a_share_all_cap/v1/sources/latest.json").exists()
            )

    def test_rejects_overlapping_sw_membership_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "all_cap_source_industry_overlap"):
                self.collect(root, FakePro(overlapping_membership=True))

            self.assertFalse(
                (root / "data/research/a_share_all_cap/v1/sources/latest.json").exists()
            )

    def test_loader_rejects_missing_and_malformed_latest_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "all_cap_source_manifest_missing"):
                load_verified_all_cap_sources(root)

            latest = root / "data/research/a_share_all_cap/v1/sources/latest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text(json.dumps({"status": "complete"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "all_cap_source_manifest"):
                load_verified_all_cap_sources(root)


if __name__ == "__main__":
    unittest.main()
