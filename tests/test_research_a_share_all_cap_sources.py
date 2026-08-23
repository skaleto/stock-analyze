from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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
        duplicate_membership_across_states: bool = False,
        missing_index_daily_date: str | None = None,
        extra_index_daily_date: str | None = None,
        future_index_weight: bool = False,
        empty_weight: tuple[str, str] | None = None,
        partial_stk_limit_date: str | None = None,
    ) -> None:
        self.malformed_index_daily = malformed_index_daily
        self.overlapping_membership = overlapping_membership
        self.duplicate_membership_across_states = duplicate_membership_across_states
        self.missing_index_daily_date = missing_index_daily_date
        self.extra_index_daily_date = extra_index_daily_date
        self.future_index_weight = future_index_weight
        self.empty_weight = empty_weight
        self.partial_stk_limit_date = partial_stk_limit_date
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
        snapshot_as_of = str(kwargs["end_date"])
        if index_code == "932000.CSI" and snapshot_as_of < "20230901":
            raise AssertionError("CSI2000 pre-inception weights were requested")
        if self.empty_weight == (index_code, snapshot_as_of):
            return pd.DataFrame(
                columns=["index_code", "con_code", "trade_date", "weight"]
            )
        member_number = SLEEVE_INDEX_CODES.index(index_code) + 1
        reused_dates = {
            "20230831": "20230830",
            "20230901": (
                "20230901" if index_code == "932000.CSI" else "20230830"
            ),
            "20231001": "20230928",
            "20231101": "20231030",
            "20231201": "20231129",
            "20240101": "20231229",
        }
        latest_date = reused_dates.get(snapshot_as_of, snapshot_as_of)
        latest = datetime.strptime(latest_date, "%Y%m%d").date()
        rows = [
            {
                "index_code": index_code,
                "con_code": f"{member_number:06d}.SZ",
                "trade_date": latest_date,
                "weight": 100.0,
            },
            {
                "index_code": index_code,
                "con_code": f"{member_number:06d}.SZ",
                "trade_date": (latest - timedelta(days=7)).strftime("%Y%m%d"),
                "weight": 99.0,
            },
        ]
        if self.future_index_weight:
            rows.append(
                {
                    "index_code": index_code,
                    "con_code": "999999.SZ",
                    "trade_date": (
                        datetime.strptime(snapshot_as_of, "%Y%m%d").date()
                        + timedelta(days=1)
                    ).strftime("%Y%m%d"),
                    "weight": 1.0,
                }
            )
        return pd.DataFrame(rows)

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        self.index_daily_calls.append(dict(kwargs))
        code = str(kwargs["ts_code"])
        start = str(kwargs["start_date"])
        end = str(kwargs["end_date"])
        dates = [
            value
            for value in self.OPEN_DATES
            if start <= value <= end and value != self.missing_index_daily_date
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
        if self.duplicate_membership_across_states and l1_code == SW2021_L1_CODES[0]:
            stock_number = 888888
            in_date = "20220101"
            out_date = pd.NA
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
        codes = ["000001.SZ", "510300.SH"]
        if trade_date >= "20230901":
            codes.append("000002.SZ")
        if trade_date <= "20230901":
            codes.append("000003.SZ")
        if trade_date == self.partial_stk_limit_date:
            codes.remove("000001.SZ")
        return pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "pre_close": 10.0,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
                for code in codes
            ]
        )


class InterruptingPro(FakePro):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt = True

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        if self.interrupt and kwargs["ts_code"] == "000852.SH":
            self.index_daily_calls.append(dict(kwargs))
            raise RuntimeError("transient provider interruption")
        return super().index_daily(**kwargs)


class TransientCalendarPro(FakePro):
    def __init__(self) -> None:
        super().__init__()
        self.calendar_attempts = 0

    def trade_cal(self, **kwargs: object) -> pd.DataFrame:
        self.calendar_attempts += 1
        if self.calendar_attempts < 3:
            self.trade_cal_calls.append(dict(kwargs))
            raise RuntimeError("temporary throttle")
        return super().trade_cal(**kwargs)


class NoCallPro(FakePro):
    def _unexpected(self, **kwargs: object) -> pd.DataFrame:
        raise AssertionError(f"provider must not be called: {kwargs}")

    index_classify = _unexpected
    index_weight = _unexpected
    index_daily = _unexpected
    index_member_all = _unexpected
    trade_cal = _unexpected
    stk_limit = _unexpected


class AllCapSourceCollectorTests(unittest.TestCase):
    START = date(2023, 8, 31)
    END = date(2024, 1, 3)

    @staticmethod
    def write_stock_master(root: Path) -> Path:
        path = root / "data/shared/backtest_cache/stock_basic.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "list_date": "19910403",
                    "delist_date": "",
                },
                {
                    "ts_code": "000002.SZ",
                    "list_date": "20230901",
                    "delist_date": "",
                },
                {
                    "ts_code": "000003.SZ",
                    "list_date": "20200101",
                    "delist_date": "20230901",
                },
            ]
        ).to_csv(path, index=False)
        return path

    def collect(
        self,
        root: Path,
        provider: FakePro | None = None,
        *,
        start: date | None = None,
        end: date | None = None,
        write_stock_master: bool = True,
    ):
        if write_stock_master:
            self.write_stock_master(root)
        client = provider or FakePro()
        result = collect_all_cap_sources(
            repo_root=root,
            pro_client=client,
            start=start or self.START,
            end=end or self.END,
            request_interval_seconds=0,
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

    def resigned_parquet_staging(
        self,
        publication_dir: Path,
        name: str,
        relative_path: str,
        mutate,
    ) -> Path:
        staging = publication_dir.parent / f".all-cap-sources-{name}"
        shutil.copytree(publication_dir, staging)
        parquet_path = staging / relative_path
        frame = mutate(pd.read_parquet(parquet_path, dtype_backend="pyarrow"))
        frame.to_parquet(parquet_path, index=False, compression="snappy")
        parquet = pq.ParquetFile(parquet_path)
        schema = {
            "columns": list(parquet.schema_arrow.names),
            "fields": [
                {"name": field.name, "dtype": str(field.type)}
                for field in parquet.schema_arrow
            ],
        }
        codecs = {
            parquet.metadata.row_group(row_group).column(column).compression.upper()
            for row_group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.row_group(row_group).num_columns)
        }
        payload_path = staging / "manifest.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        file_record = next(
            item for item in payload["files"] if item["path"] == relative_path
        )
        dataset = file_record["dataset"]
        date_column = {
            "index_weights": "snapshot_as_of",
            "index_daily": "trade_date",
            "industry_membership": "in_date",
            "stk_limit": "trade_date",
        }[dataset]
        replacements = {
            "rows": len(frame),
            "bytes": parquet_path.stat().st_size,
            "sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
            "compression": next(iter(codecs)),
            "min_date": str(frame[date_column].min()),
            "max_date": str(frame[date_column].max()),
        }
        for collection in (payload["files"], payload["partitions"][dataset]):
            next(item for item in collection if item["path"] == relative_path).update(
                replacements
            )
        payload["dataset_schemas"][dataset] = schema
        payload["row_counts"][dataset] = sum(
            item["rows"] for item in payload["partitions"][dataset]
        )
        payload.pop("manifest_sha256", None)
        payload["manifest_sha256"] = self._canonical_hash(payload)
        payload_path.write_text(
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
            progress = json.loads(
                (
                    root
                    / "data/research/a_share_all_cap/v1/sources/publications"
                    / ".all-cap-sources-job-20230831-20240103/progress.json"
                ).read_text(encoding="utf-8")
            )

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
            (
                {
                    "dataset": "index_weights",
                    "index_code": "932000.CSI",
                    "snapshot_as_of": "20230831",
                    "status": "pre_inception",
                },
            ),
        )
        micro = manifest.index_weights["932000.CSI"]
        self.assertEqual(
            list(micro["snapshot_as_of"]),
            ["20230901", "20231001", "20231101", "20231201", "20240101"],
        )
        self.assertTrue((micro["trade_date"] >= "20230901").all())
        self.assertTrue(
            all(
                str(call["end_date"]) >= "20230901"
                for call in client.index_weight_calls
                if call["index_code"] == "932000.CSI"
            )
        )
        expected_windows = (
            ("20230528", "20230831"),
            ("20230529", "20230901"),
            ("20230628", "20231001"),
            ("20230729", "20231101"),
            ("20230828", "20231201"),
            ("20230928", "20240101"),
        )
        self.assertEqual(
            client.index_weight_calls,
            [
                {
                    "index_code": index_code,
                    "start_date": query_start,
                    "end_date": snapshot_as_of,
                }
                for query_start, snapshot_as_of in expected_windows
                for index_code in SLEEVE_INDEX_CODES
                if not (index_code == "932000.CSI" and snapshot_as_of < "20230901")
            ],
        )
        self.assertEqual(len(client.index_weight_calls), 23)
        self.assertEqual(
            {
                key
                for key in progress["checkpoints"]
                if key.startswith("index_weight:")
            },
            {
                f"index_weight:{index_code}:{snapshot_as_of}"
                for _, snapshot_as_of in expected_windows
                for index_code in SLEEVE_INDEX_CODES
            },
        )
        large = manifest.index_weights["000300.SH"]
        reused = large.loc[
            large["snapshot_as_of"].isin(["20230831", "20230901"])
        ]
        self.assertEqual(list(reused["trade_date"]), ["20230830", "20230830"])
        self.assertEqual(
            list(reused["snapshot_as_of"]),
            ["20230831", "20230901"],
        )
        self.assertFalse(
            large.duplicated(["index_code", "snapshot_as_of", "con_code"]).any()
        )
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

    def test_index_weight_uses_real_empty_month_lookback_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            manifest = load_verified_all_cap_sources(root)

        september = manifest.index_weights["000300.SH"].loc[
            lambda frame: frame["snapshot_as_of"] == "20230901"
        ]
        self.assertEqual(len(september), 1)
        self.assertEqual(september.iloc[0]["trade_date"], "20230830")

    def test_index_weight_rejects_future_rows_and_post_inception_empty(self) -> None:
        providers_and_errors = (
            (
                FakePro(future_index_weight=True),
                "all_cap_source_index_weight_future",
            ),
            (
                FakePro(empty_weight=("932000.CSI", "20230901")),
                "all_cap_source_index_weight_empty",
            ),
        )
        for provider, error in providers_and_errors:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, error):
                    self.collect(Path(tmp), provider)

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
                    tuple(field["name"] for field in schema["fields"]),
                )
                self.assertTrue(all(field["dtype"] for field in schema["fields"]))
        self.assertEqual(
            metadata["dataset_schemas"]["index_weights"]["columns"],
            ("index_code", "snapshot_as_of", "con_code", "trade_date", "weight"),
        )
        self.assertTrue(
            metadata["dataset_schemas"]["index_daily"]["fields"][0]["dtype"]
            .lower()
            .endswith("string")
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

    def test_rejects_resigned_file_missing_fixed_column_or_invalid_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            verified = load_verified_all_cap_sources(root)

            attacks = (
                (
                    "missing-close",
                    "index_daily.parquet",
                    lambda frame: frame.drop(columns=["close"]),
                    "all_cap_source_schema_contract",
                ),
                (
                    "invalid-interval",
                    "industry_membership.parquet",
                    lambda frame: frame.assign(out_date=frame["in_date"]),
                    "all_cap_source_industry_interval",
                ),
            )
            for name, path, mutate, error in attacks:
                with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError,
                    error,
                ):
                    publish_all_cap_sources(
                        self.resigned_parquet_staging(
                            verified.publication_dir,
                            name,
                            path,
                            mutate,
                        ),
                        root,
                    )

    def test_rejects_declared_parquet_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            verified = load_verified_all_cap_sources(root)
            staging = verified.publication_dir.parent / ".all-cap-sources-symlink"
            shutil.copytree(verified.publication_dir, staging)
            declared = staging / "index_daily.parquet"
            outside = root / "outside-index-daily.parquet"
            shutil.copy2(declared, outside)
            declared.unlink()
            os.symlink(outside, declared)

            with self.assertRaisesRegex(ValueError, "all_cap_source_symlink"):
                publish_all_cap_sources(staging, root)

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

    def test_stk_limit_uses_inclusive_stock_master_and_records_completeness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            manifest = load_verified_all_cap_sources(root)
            limits_2023 = manifest.load_stk_limit_year("2023")
            limits_2024 = manifest.load_stk_limit_year("2024")
            progress = json.loads(
                (
                    root
                    / "data/research/a_share_all_cap/v1/sources/publications"
                    / ".all-cap-sources-job-20230831-20240103/progress.json"
                ).read_text(encoding="utf-8")
            )

        self.assertIsInstance(manifest.stk_limit, Mapping)
        self.assertEqual(
            set(limits_2023.loc[limits_2023["trade_date"] == "20230831", "ts_code"]),
            {"000001.SZ", "000003.SZ"},
        )
        self.assertEqual(
            set(limits_2023.loc[limits_2023["trade_date"] == "20230901", "ts_code"]),
            {"000001.SZ", "000002.SZ", "000003.SZ"},
        )
        self.assertEqual(set(limits_2024["ts_code"]), {"000001.SZ", "000002.SZ"})
        counts = {
            item["partition"]: (
                item["expected_count"],
                item["observed_count"],
                item["missing_count"],
                item["extra_count"],
            )
            for item in manifest.metadata["partitions"]["stk_limit"]
        }
        self.assertEqual(counts, {"2023": (5, 7, 0, 2), "2024": (2, 3, 0, 1)})
        self.assertEqual(
            {
                key: (
                    record["expected_count"],
                    record["observed_count"],
                    record["missing_count"],
                    record["extra_count"],
                )
                for key, record in progress["checkpoints"].items()
                if key.startswith("stk_limit:")
            },
            {
                "stk_limit:20230831": (2, 3, 0, 1),
                "stk_limit:20230901": (3, 4, 0, 1),
                "stk_limit:20240102": (2, 3, 0, 1),
            },
        )
        for key, record in progress["checkpoints"].items():
            with self.subTest(checkpoint=key):
                self.assertGreaterEqual(record["rows"], 0)
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(record["schema"]["columns"])
                self.assertTrue(record["path"].endswith(".parquet"))

    def test_stk_limit_rejects_missing_master_bad_schema_and_partial_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError,
            "all_cap_source_stock_master_missing",
        ):
            self.collect(Path(tmp), write_stock_master=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_stock_master(root)
            pd.DataFrame([{"ts_code": "000001.SZ"}]).to_csv(path, index=False)
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_source_stock_master_schema",
            ):
                self.collect(root, write_stock_master=False)

        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError,
            "all_cap_source_stk_limit_missing",
        ):
            self.collect(
                Path(tmp),
                FakePro(partial_stk_limit_date="20230901"),
            )

    def test_provider_retries_three_times_with_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.a_share_all_cap_sources.time.sleep"
        ) as sleep:
            _, provider = self.collect(Path(tmp), TransientCalendarPro())

        self.assertEqual(provider.calendar_attempts, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.35, 0.7])

    def test_resume_reuses_verified_checkpoints_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider = InterruptingPro()
            with self.assertRaisesRegex(RuntimeError, "provider interruption"):
                self.collect(root, provider)
            completed_weight_calls = list(provider.index_weight_calls)
            completed_daily_calls = [
                call
                for call in provider.index_daily_calls
                if call["ts_code"] in {"000300.SH", "000905.SH"}
            ]
            job = (
                root
                / "data/research/a_share_all_cap/v1/sources/publications"
                / ".all-cap-sources-job-20230831-20240103"
            )
            self.assertTrue((job / "progress.json").is_file())
            progress = json.loads((job / "progress.json").read_text(encoding="utf-8"))
            corrupt = job / progress["checkpoints"]["index_daily:000300.SH"]["path"]
            corrupt.write_bytes(corrupt.read_bytes() + b"corrupt")

            provider.interrupt = False
            result, _ = self.collect(root, provider)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(provider.index_weight_calls, completed_weight_calls)
        self.assertEqual(provider.index_daily_calls.count(completed_daily_calls[0]), 2)
        self.assertEqual(provider.index_daily_calls.count(completed_daily_calls[1]), 1)
        self.assertEqual(len(provider.trade_cal_calls), 1)

    def test_resume_rejects_resigned_progress_with_unsafe_publication_id(self) -> None:
        from stock_analyze.research import a_share_all_cap_source_store as source_store

        unsafe_ids = (
            "..",
            "nested/publication",
            "/absolute/publication",
            "../../../../../../escaped-publication",
        )
        for unsafe_id in unsafe_ids:
            with self.subTest(publication_id=unsafe_id), tempfile.TemporaryDirectory() as tmp:
                sandbox = Path(tmp)
                root = sandbox / "repo"
                job = source_store.JobStore(root, "20230831", "20240103")
                progress = json.loads(job.progress_path.read_text(encoding="utf-8"))
                progress["publication_id"] = unsafe_id
                job.progress_path.write_text(
                    json.dumps(
                        progress,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                sources = source_store.source_root(root)
                outside_before = sorted(
                    path.relative_to(sandbox).as_posix()
                    for path in sandbox.rglob("*")
                    if not path.is_relative_to(sources)
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_source_job_publication_id",
                ):
                    source_store.JobStore(root, "20230831", "20240103")

                outside_after = sorted(
                    path.relative_to(sandbox).as_posix()
                    for path in sandbox.rglob("*")
                    if not path.is_relative_to(sources)
                )
                self.assertEqual(outside_after, outside_before)

    def test_marker_failure_preserves_latest_and_next_run_adopts_orphan(self) -> None:
        from stock_analyze.research import a_share_all_cap_source_store as source_store

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(
                root,
                start=date(2023, 8, 31),
                end=date(2023, 9, 1),
            )
            latest = root / "data/research/a_share_all_cap/v1/sources/latest.json"
            old_latest = latest.read_bytes()
            real_write = source_store.write_text_atomic

            def fail_latest(path, text, encoding="utf-8"):
                if Path(path).name == "latest.json":
                    raise OSError("injected marker failure")
                return real_write(path, text, encoding=encoding)

            with patch.object(
                source_store,
                "write_text_atomic",
                side_effect=fail_latest,
            ), self.assertRaisesRegex(OSError, "marker failure"):
                self.collect(root, FakePro())

            self.assertEqual(latest.read_bytes(), old_latest)
            result, _ = self.collect(root, NoCallPro())
            loaded = load_verified_all_cap_sources(root)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(loaded.metadata["start_date"], "20230831")
        self.assertEqual(loaded.metadata["end_date"], "20240103")

    def test_manifest_metadata_is_deeply_immutable_and_limits_load_by_year(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.collect(root)
            manifest = load_verified_all_cap_sources(root)
            year_path = manifest.stk_limit["2023"].path
            year_frame = manifest.load_stk_limit_year("2023")

            with self.assertRaises(TypeError):
                manifest.metadata["dataset_schemas"]["index_daily"]["fields"][0][
                    "dtype"
                ] = "int64"

        self.assertTrue(year_path.name == "year=2023.parquet")
        self.assertEqual(set(year_frame["trade_date"]), {"20230831", "20230901"})

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
                (manifest.load_stk_limit_year("2023"), ("ts_code", "trade_date")),
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

    def test_rejects_same_membership_interval_returned_by_y_and_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "all_cap_source_industry_overlap"):
                self.collect(
                    root,
                    FakePro(duplicate_membership_across_states=True),
                )

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
