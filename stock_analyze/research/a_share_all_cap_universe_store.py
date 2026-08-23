"""Private fixed-schema publication store for the all-cap universe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..utils import write_text_atomic


SCHEMA_VERSION = 2
CONTRACT_VERSION = "a-share-all-cap-universe-v1"
PARQUET_COMPRESSION = "SNAPPY"
DATASETS = ("membership", "daily_hard_status")
DATE_COLUMNS = {"membership": "review_date", "daily_hard_status": "trade_date"}
KEY_COLUMNS = {
    "membership": ("review_date", "code"),
    "daily_hard_status": ("trade_date", "code"),
}
ARROW_SCHEMAS = {
    "membership": pa.schema(
        [
            ("review_date", pa.string()),
            ("effective_date", pa.string()),
            ("code", pa.string()),
            ("eligible", pa.bool_()),
            ("exclusion_reasons", pa.string()),
            ("size_rank", pa.int64()),
            ("raw_sleeve", pa.string()),
            ("stable_sleeve", pa.string()),
            ("total_mv", pa.float64()),
            ("circ_mv", pa.float64()),
            ("total_mv_source_date", pa.string()),
            ("avg_amount_252", pa.float64()),
            ("avg_amount_source_date", pa.string()),
            ("non_trading_days_252", pa.int64()),
            ("industry_l1", pa.string()),
            ("industry_l2", pa.string()),
            ("industry_l3", pa.string()),
            ("industry_source_date", pa.string()),
            ("status_source", pa.string()),
            ("universe_contract_version", pa.string()),
        ]
    ),
    "daily_hard_status": pa.schema(
        [
            ("trade_date", pa.string()),
            ("code", pa.string()),
            ("listed", pa.bool_()),
            ("st", pa.bool_()),
            ("delisting", pa.bool_()),
            ("suspended", pa.bool_()),
            ("limit_up", pa.float64()),
            ("limit_down", pa.float64()),
            ("at_limit_up", pa.bool_()),
            ("at_limit_down", pa.bool_()),
            ("status_complete", pa.bool_()),
            ("status_conflict", pa.bool_()),
            ("buy_executable", pa.bool_()),
            ("sell_executable", pa.bool_()),
            ("prohibit_new_position", pa.bool_()),
            ("status_source", pa.string()),
            ("hard_status_version", pa.string()),
        ]
    ),
}

_PUBLICATION_ID = re.compile(r"^[0-9]{8}_[0-9]{8}_[a-f0-9]{32}$")
_YEAR = re.compile(r"^[0-9]{4}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class UniversePartition:
    publication_dir: Path
    dataset: str
    year: str
    record: Mapping[str, object]

    def load(self) -> pd.DataFrame:
        relative = _safe_relative(self.record.get("path"))
        path = self.publication_dir.joinpath(*relative.parts)
        _assert_contained(path, self.publication_dir)
        if (
            path.stat().st_size != int(self.record["bytes"])
            or sha256(path) != self.record["sha256"]
        ):
            raise ValueError(
                f"all_cap_universe_checksum:{relative.as_posix()}"
            )
        return read_partition(
            path,
            self.dataset,
            root=self.publication_dir,
        )


@dataclass(frozen=True)
class VerifiedAllCapUniverse:
    metadata: Mapping[str, object]
    publication_dir: Path
    membership: Mapping[str, UniversePartition]
    daily_hard_status: Mapping[str, UniversePartition]

    def load_membership_year(self, year: str | int) -> pd.DataFrame:
        key = str(year)
        if key not in self.membership:
            raise KeyError(f"all_cap_universe_membership_year:{key}")
        return self.membership[key].load()

    def load_hard_status_year(self, year: str | int) -> pd.DataFrame:
        key = str(year)
        if key not in self.daily_hard_status:
            raise KeyError(f"all_cap_universe_hard_status_year:{key}")
        return self.daily_hard_status[key].load()


def universe_root(repo_root: str | Path) -> Path:
    return Path(repo_root).absolute() / "data/research/a_share_all_cap/v1/universe"


def publications_root(repo_root: str | Path) -> Path:
    return universe_root(repo_root) / "publications"


def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_contract(dataset: str) -> dict[str, object]:
    schema = ARROW_SCHEMAS[dataset]
    return {
        "columns": list(schema.names),
        "fields": [
            {"name": field.name, "dtype": str(field.type)}
            for field in schema
        ],
    }


def _safe_relative(value: object) -> PurePosixPath:
    relative = PurePosixPath(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("all_cap_universe_manifest_path")
    return relative


def _assert_not_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"all_cap_universe_symlink:{path.name}")


def _assert_contained(path: Path, root: Path, *, must_exist: bool = True) -> None:
    absolute_root = root.absolute()
    absolute_path = path.absolute()
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise ValueError("all_cap_universe_manifest_path") from exc
    _assert_not_symlink(absolute_root.parent)
    _assert_not_symlink(absolute_root)
    current = absolute_root
    for part in relative.parts:
        current = current / part
        _assert_not_symlink(current)
    try:
        resolved_root = absolute_root.resolve(strict=True)
        resolved_path = absolute_path.resolve(strict=must_exist)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("all_cap_universe_manifest_incomplete") from exc
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("all_cap_universe_manifest_path")


def _read_json(path: Path, *, root: Path, missing: str) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        raise ValueError(missing)
    _assert_contained(path, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("all_cap_universe_manifest_malformed") from exc
    if not isinstance(value, dict):
        raise ValueError("all_cap_universe_manifest_malformed")
    return value


def _table_from_frame(dataset: str, frame: pd.DataFrame) -> pa.Table:
    schema = ARROW_SCHEMAS[dataset]
    if list(frame.columns) != schema.names:
        raise ValueError(f"all_cap_universe_schema:{dataset}")
    if frame.duplicated(list(KEY_COLUMNS[dataset]), keep=False).any():
        raise ValueError(f"all_cap_universe_duplicate:{dataset}")
    try:
        return pa.Table.from_pandas(
            frame,
            schema=schema,
            preserve_index=False,
            safe=True,
        )
    except (pa.ArrowException, TypeError, ValueError) as exc:
        raise ValueError(f"all_cap_universe_schema:{dataset}") from exc


def estimate_frame_bytes(dataset: str, frame: pd.DataFrame) -> int:
    return max(1, int(_table_from_frame(dataset, frame).nbytes * 1.25))


def write_partition(
    staging: Path,
    dataset: str,
    year: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    if not _YEAR.fullmatch(year):
        raise ValueError("all_cap_universe_partition")
    date_column = DATE_COLUMNS[dataset]
    dates = frame[date_column].astype(str)
    if frame.empty or not dates.str.startswith(year).all():
        raise ValueError("all_cap_universe_partition")
    table = _table_from_frame(dataset, frame)
    path = staging / dataset / f"year={year}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".parquet",
            delete=False,
        ) as handle:
            temp_name = handle.name
        pq.write_table(table, temp_name, compression=PARQUET_COMPRESSION.lower())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
    return {
        "path": path.relative_to(staging).as_posix(),
        "dataset": dataset,
        "partition": year,
        "rows": len(frame),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "schema": schema_contract(dataset),
        "compression": PARQUET_COMPRESSION,
        "min_date": dates.min(),
        "max_date": dates.max(),
    }


def write_manifest(staging: Path, manifest: dict[str, object]) -> Path:
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_hash(manifest)
    path = staging / "manifest.json"
    write_text_atomic(
        path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _parquet_metadata(path: Path, dataset: str) -> tuple[int, set[str]]:
    try:
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(
            ARROW_SCHEMAS[dataset],
            check_metadata=False,
        ):
            raise ValueError(f"all_cap_universe_schema:{dataset}")
        codecs = {
            parquet.metadata.row_group(row_group)
            .column(column)
            .compression.upper()
            for row_group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.row_group(row_group).num_columns)
        }
        return parquet.metadata.num_rows, codecs
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_universe_parquet:{dataset}") from exc


def read_partition(path: Path, dataset: str, *, root: Path) -> pd.DataFrame:
    _assert_contained(path, root)
    rows, codecs = _parquet_metadata(path, dataset)
    if codecs != {PARQUET_COMPRESSION}:
        raise ValueError("all_cap_universe_manifest_compression")
    try:
        frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_universe_parquet:{dataset}") from exc
    if len(frame) != rows:
        raise ValueError(f"all_cap_universe_manifest_rows:{dataset}")
    return frame


def _validate_record(
    publication_dir: Path,
    dataset: str,
    record: Mapping[str, object],
) -> tuple[str, UniversePartition, int]:
    year = str(record.get("partition") or "")
    relative = _safe_relative(record.get("path"))
    if (
        record.get("dataset") != dataset
        or not _YEAR.fullmatch(year)
        or relative.as_posix() != f"{dataset}/year={year}.parquet"
        or record.get("schema") != schema_contract(dataset)
        or record.get("compression") != PARQUET_COMPRESSION
    ):
        raise ValueError("all_cap_universe_manifest_partition")
    path = publication_dir.joinpath(*relative.parts)
    _assert_contained(path, publication_dir)
    if (
        not isinstance(record.get("bytes"), int)
        or int(record["bytes"]) != path.stat().st_size
        or _SHA256.fullmatch(str(record.get("sha256") or "")) is None
        or record["sha256"] != sha256(path)
    ):
        raise ValueError(f"all_cap_universe_checksum:{relative.as_posix()}")
    frame = read_partition(path, dataset, root=publication_dir)
    try:
        rows = int(record["rows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("all_cap_universe_manifest_rows") from exc
    if rows != len(frame):
        raise ValueError(f"all_cap_universe_manifest_rows:{dataset}")
    date_column = DATE_COLUMNS[dataset]
    dates = frame[date_column].astype(str)
    if (
        frame.empty
        or not dates.str.fullmatch(r"[0-9]{8}").all()
        or not dates.str.startswith(year).all()
        or record.get("min_date") != dates.min()
        or record.get("max_date") != dates.max()
    ):
        raise ValueError("all_cap_universe_manifest_dates")
    if frame.duplicated(list(KEY_COLUMNS[dataset]), keep=False).any():
        raise ValueError(f"all_cap_universe_duplicate:{dataset}")
    if dataset == "membership":
        if not frame["universe_contract_version"].eq(CONTRACT_VERSION).all():
            raise ValueError("all_cap_universe_manifest_contract")
        if not frame["effective_date"].astype(str).gt(frame["review_date"].astype(str)).all():
            raise ValueError("all_cap_universe_manifest_dates")
        review_dates = frame["review_date"].astype("string")
        for column in ("total_mv_source_date", "avg_amount_source_date"):
            source_dates = frame[column].astype("string")
            populated = source_dates.notna() & source_dates.ne("")
            if (
                (populated & ~source_dates.str.fullmatch(r"[0-9]{8}", na=False)).any()
                or (populated & source_dates.gt(review_dates)).any()
                or (frame["eligible"] & ~populated).any()
            ):
                raise ValueError("all_cap_universe_manifest_dates")
    elif not frame["hard_status_version"].eq(
        "a-share-all-cap-hard-status-v1"
    ).all():
        raise ValueError("all_cap_universe_manifest_contract")
    partition = UniversePartition(
        publication_dir=publication_dir,
        dataset=dataset,
        year=year,
        record=_deep_freeze(dict(record)),
    )
    return year, partition, len(frame)


def verify_publication(publication_dir: str | Path) -> VerifiedAllCapUniverse:
    root = Path(publication_dir).absolute()
    pubs = root.parent
    _assert_contained(root, pubs)
    manifest = _read_json(
        root / "manifest.json",
        root=root,
        missing="all_cap_universe_manifest_missing",
    )
    payload = dict(manifest)
    manifest_hash = payload.pop("manifest_sha256", None)
    if manifest_hash != canonical_hash(payload):
        raise ValueError("all_cap_universe_checksum:manifest")
    publication_id = str(manifest.get("publication_id") or "")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("status") != "complete"
        or not _PUBLICATION_ID.fullmatch(publication_id)
        or publication_id != root.name.removeprefix(".all-cap-universe-")
        or publication_id.split("_", 2)[:2]
        != [manifest.get("start_date"), manifest.get("end_date")]
    ):
        raise ValueError("all_cap_universe_manifest_contract")
    partitions = manifest.get("partitions")
    row_counts = manifest.get("row_counts")
    schemas = manifest.get("dataset_schemas")
    if (
        not isinstance(partitions, Mapping)
        or set(partitions) != set(DATASETS)
        or not isinstance(row_counts, Mapping)
        or set(row_counts) != set(DATASETS)
        or not isinstance(schemas, Mapping)
        or schemas != {dataset: schema_contract(dataset) for dataset in DATASETS}
    ):
        raise ValueError("all_cap_universe_manifest_incomplete")
    loaded: dict[str, dict[str, UniversePartition]] = {
        dataset: {} for dataset in DATASETS
    }
    declared_paths: set[str] = set()
    for dataset in DATASETS:
        records = partitions[dataset]
        if not isinstance(records, list) or not records:
            raise ValueError(f"all_cap_universe_manifest_incomplete:{dataset}")
        observed_rows = 0
        for raw_record in records:
            if not isinstance(raw_record, Mapping):
                raise ValueError("all_cap_universe_manifest_malformed")
            year, partition, rows = _validate_record(root, dataset, raw_record)
            if year in loaded[dataset]:
                raise ValueError("all_cap_universe_manifest_partition")
            loaded[dataset][year] = partition
            declared_paths.add(str(raw_record["path"]))
            observed_rows += rows
        if observed_rows != int(row_counts[dataset]):
            raise ValueError(f"all_cap_universe_manifest_rows:{dataset}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.parquet")
        if path.is_file()
    }
    if actual_paths != declared_paths:
        raise ValueError("all_cap_universe_manifest_incomplete:files")
    return VerifiedAllCapUniverse(
        metadata=_deep_freeze(dict(manifest)),
        publication_dir=root,
        membership=MappingProxyType(dict(loaded["membership"])),
        daily_hard_status=MappingProxyType(dict(loaded["daily_hard_status"])),
    )


def write_latest(repo_root: str | Path, manifest: Mapping[str, object]) -> Path:
    root = universe_root(repo_root)
    marker: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "status": "complete",
        "publication": f"publications/{manifest['publication_id']}",
        "manifest_sha256": manifest["manifest_sha256"],
    }
    marker["marker_sha256"] = canonical_hash(marker)
    path = root / "latest.json"
    write_text_atomic(
        path,
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_latest(repo_root: str | Path) -> VerifiedAllCapUniverse:
    root = universe_root(repo_root)
    marker = _read_json(
        root / "latest.json",
        root=root,
        missing="all_cap_universe_manifest_missing",
    )
    payload = dict(marker)
    marker_hash = payload.pop("marker_sha256", None)
    if marker_hash != canonical_hash(payload):
        raise ValueError("all_cap_universe_checksum:latest")
    if (
        marker.get("schema_version") != SCHEMA_VERSION
        or marker.get("contract_version") != CONTRACT_VERSION
        or marker.get("status") != "complete"
    ):
        raise ValueError("all_cap_universe_manifest_contract")
    relative = _safe_relative(marker.get("publication"))
    if len(relative.parts) != 2 or relative.parts[0] != "publications":
        raise ValueError("all_cap_universe_manifest_path")
    publication_dir = root.joinpath(*relative.parts)
    verified = verify_publication(publication_dir)
    if (
        marker.get("manifest_sha256") != verified.metadata.get("manifest_sha256")
        or publication_dir.name != verified.metadata.get("publication_id")
    ):
        raise ValueError("all_cap_universe_checksum:latest_manifest")
    return verified


def install_publication(staging: Path, publication_id: str) -> Path:
    pubs = staging.parent
    if (
        staging.is_symlink()
        or pubs.name != "publications"
        or staging.name != f".all-cap-universe-{publication_id}"
    ):
        raise ValueError("all_cap_universe_staging_path")
    destination = pubs / publication_id
    if destination.exists() or destination.is_symlink():
        raise ValueError("all_cap_universe_publication_exists")
    os.replace(staging, destination)
    return destination
