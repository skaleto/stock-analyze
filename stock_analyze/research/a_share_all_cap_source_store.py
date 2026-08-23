"""Private durable storage for verified A-share all-cap source collection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..utils import write_text_atomic


SCHEMA_VERSION = 2
CONTRACT_VERSION = "a-share-all-cap-sources-v2"
PARQUET_COMPRESSION = "SNAPPY"
PUBLICATION_DATASETS = (
    "index_weights",
    "index_daily",
    "industry_membership",
    "stk_limit",
)
DATE_COLUMNS = {
    "index_weights": "snapshot_as_of",
    "index_daily": "trade_date",
    "industry_membership": "in_date",
    "stk_limit": "trade_date",
    "trade_calendar": "cal_date",
    "industry_codes": None,
}
SINGLE_FILE_PATHS = {
    "index_weights": "index_weights.parquet",
    "index_daily": "index_daily.parquet",
    "industry_membership": "industry_membership.parquet",
}
ARROW_SCHEMAS = {
    "index_weights": pa.schema(
        [
            ("index_code", pa.string()),
            ("snapshot_as_of", pa.string()),
            ("con_code", pa.string()),
            ("trade_date", pa.string()),
            ("weight", pa.float64()),
        ]
    ),
    "index_daily": pa.schema(
        [
            ("ts_code", pa.string()),
            ("trade_date", pa.string()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("vol", pa.float64()),
        ]
    ),
    "industry_membership": pa.schema(
        [
            ("l1_code", pa.string()),
            ("l2_code", pa.string()),
            ("l3_code", pa.string()),
            ("ts_code", pa.string()),
            ("in_date", pa.string()),
            ("out_date", pa.string()),
            ("is_new", pa.string()),
        ]
    ),
    "stk_limit": pa.schema(
        [
            ("ts_code", pa.string()),
            ("trade_date", pa.string()),
            ("pre_close", pa.float64()),
            ("up_limit", pa.float64()),
            ("down_limit", pa.float64()),
        ]
    ),
    "trade_calendar": pa.schema(
        [("cal_date", pa.string()), ("is_open", pa.string())]
    ),
    "industry_codes": pa.schema([("index_code", pa.string())]),
}

_PUBLICATION_ID = re.compile(r"^[0-9]{8}_[0-9]{8}_[a-f0-9]{32}$")
_YEAR = re.compile(r"^[0-9]{4}$")


@dataclass(frozen=True)
class SourcePartition:
    path: Path
    dataset: str
    partition: str
    record: Mapping[str, object]

    def load(self) -> pd.DataFrame:
        return read_record(self.path.parent.parent, self.record, self.dataset)


@dataclass(frozen=True)
class VerifiedPublication:
    manifest: dict[str, object]
    frames: Mapping[str, pd.DataFrame]
    stk_limit: Mapping[str, SourcePartition]


def source_root(repo_root: str | Path) -> Path:
    return Path(repo_root).absolute() / "data/research/a_share_all_cap/v1/sources"


def publications_root(repo_root: str | Path) -> Path:
    return source_root(repo_root) / "publications"


def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
            {"name": field.name, "dtype": str(field.type)} for field in schema
        ],
    }


def _schema_error(dataset: str) -> str:
    return (
        "all_cap_source_manifest_schema:"
        f"all_cap_source_schema_contract:{dataset}"
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _safe_relative(value: object) -> PurePosixPath:
    relative = PurePosixPath(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("all_cap_source_manifest_path")
    return relative


def _assert_not_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"all_cap_source_symlink:{path.name}")


def _assert_contained(path: Path, root: Path, *, must_exist: bool = True) -> None:
    _assert_not_symlink(root.parent)
    _assert_not_symlink(root)
    current = root
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("all_cap_source_manifest_path") from exc
    for part in relative.parts:
        current = current / part
        _assert_not_symlink(current)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=must_exist)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("all_cap_source_manifest_incomplete") from exc
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("all_cap_source_manifest_path")


def _read_json(path: Path, *, missing_code: str, root: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        raise ValueError(missing_code)
    _assert_contained(path, root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(missing_code) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("all_cap_source_manifest_malformed") from exc
    if not isinstance(value, dict):
        raise ValueError("all_cap_source_manifest_malformed")
    return value


def _table_from_frame(dataset: str, frame: pd.DataFrame) -> pa.Table:
    schema = ARROW_SCHEMAS[dataset]
    if list(frame.columns) != schema.names:
        raise ValueError(_schema_error(dataset))
    try:
        return pa.Table.from_pandas(
            frame,
            schema=schema,
            preserve_index=False,
            safe=True,
        )
    except (pa.ArrowException, ValueError, TypeError) as exc:
        raise ValueError(_schema_error(dataset)) from exc


def write_fixed_parquet(path: Path, frame: pd.DataFrame, dataset: str) -> Path:
    table = _table_from_frame(dataset, frame)
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
        return path
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def _parquet_metadata(path: Path, dataset: str) -> tuple[str, int]:
    try:
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(
            ARROW_SCHEMAS[dataset],
            check_metadata=False,
        ):
            raise ValueError(_schema_error(dataset))
        codecs = {
            parquet.metadata.row_group(row_group)
            .column(column)
            .compression.upper()
            for row_group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.row_group(row_group).num_columns)
        }
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_source_parquet:{path.name}") from exc
    if codecs != {PARQUET_COMPRESSION}:
        raise ValueError("all_cap_source_manifest_compression")
    return PARQUET_COMPRESSION, parquet.metadata.num_rows


def read_fixed_parquet(path: Path, dataset: str, *, root: Path) -> pd.DataFrame:
    _assert_contained(path, root)
    _parquet_metadata(path, dataset)
    try:
        table = pq.read_table(path)
        return table.to_pandas(types_mapper=pd.ArrowDtype)
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_source_parquet:{path.name}") from exc


def physical_record(
    root: Path,
    path: Path,
    dataset: str,
    partition: str,
    frame: pd.DataFrame,
    **counts: int,
) -> dict[str, object]:
    compression, rows = _parquet_metadata(path, dataset)
    date_column = DATE_COLUMNS[dataset]
    record: dict[str, object] = {
        "path": path.relative_to(root).as_posix(),
        "dataset": dataset,
        "partition": partition,
        "rows": int(rows),
        "bytes": int(path.stat().st_size),
        "sha256": sha256(path),
        "schema": schema_contract(dataset),
        "compression": compression,
        "min_date": None,
        "max_date": None,
    }
    if date_column and not frame.empty:
        record["min_date"] = str(frame[date_column].min())
        record["max_date"] = str(frame[date_column].max())
    record.update({key: int(value) for key, value in counts.items()})
    return record


def read_record(root: Path, record: Mapping[str, object], dataset: str) -> pd.DataFrame:
    relative = _safe_relative(record.get("path"))
    path = root.joinpath(*relative.parts)
    _assert_contained(path, root)
    if not path.is_file():
        raise ValueError(f"all_cap_source_manifest_incomplete:{relative.as_posix()}")
    if sha256(path) != record.get("sha256"):
        raise ValueError(f"all_cap_source_checksum:{relative.as_posix()}")
    if path.stat().st_size != int(record.get("bytes") or -1):
        raise ValueError(f"all_cap_source_checksum:{relative.as_posix()}:bytes")
    if _plain(record.get("schema")) != schema_contract(dataset):
        raise ValueError(_schema_error(dataset))
    if record.get("compression") != PARQUET_COMPRESSION:
        raise ValueError("all_cap_source_manifest_compression")
    frame = read_fixed_parquet(path, dataset, root=root)
    if len(frame) != int(record.get("rows", -1)):
        raise ValueError(f"all_cap_source_manifest_rows:{relative.as_posix()}")
    date_column = DATE_COLUMNS[dataset]
    if date_column:
        actual_min = None if frame.empty else str(frame[date_column].min())
        actual_max = None if frame.empty else str(frame[date_column].max())
        if actual_min != record.get("min_date") or actual_max != record.get("max_date"):
            raise ValueError("all_cap_source_manifest_dates")
    return frame


class JobStore:
    """Deterministic, resumable checkpoint store for one date interval."""

    def __init__(self, repo_root: Path, start_key: str, end_key: str) -> None:
        self.publications_root = publications_root(repo_root)
        self.publications_root.mkdir(parents=True, exist_ok=True)
        _assert_not_symlink(self.publications_root)
        self.root = (
            self.publications_root
            / f".all-cap-sources-job-{start_key}-{end_key}"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        _assert_contained(self.root, self.publications_root)
        self.progress_path = self.root / "progress.json"
        if self.progress_path.exists():
            self.progress = _read_json(
                self.progress_path,
                missing_code="all_cap_source_job_missing",
                root=self.root,
            )
            publication_id = self.progress.get("publication_id")
            if (
                not isinstance(publication_id, str)
                or _PUBLICATION_ID.fullmatch(publication_id) is None
            ):
                raise ValueError("all_cap_source_job_publication_id")
            if (
                self.progress.get("schema_version") != SCHEMA_VERSION
                or self.progress.get("contract_version") != CONTRACT_VERSION
                or self.progress.get("start_date") != start_key
                or self.progress.get("end_date") != end_key
                or not isinstance(self.progress.get("checkpoints"), dict)
            ):
                raise ValueError("all_cap_source_job_contract")
        else:
            self.progress = {
                "schema_version": SCHEMA_VERSION,
                "contract_version": CONTRACT_VERSION,
                "start_date": start_key,
                "end_date": end_key,
                "publication_id": f"{start_key}_{end_key}_{uuid.uuid4().hex}",
                "checkpoints": {},
            }
            self._write_progress()

    @property
    def publication_id(self) -> str:
        return str(self.progress["publication_id"])

    @property
    def candidate_dir(self) -> Path:
        return self.publications_root / f".all-cap-publication-{self.publication_id}"

    def _write_progress(self) -> None:
        write_text_atomic(
            self.progress_path,
            json.dumps(self.progress, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    def checkpoint_record(self, key: str) -> dict[str, object] | None:
        value = self.progress["checkpoints"].get(key)
        return dict(value) if isinstance(value, Mapping) else None

    def load_checkpoint(self, key: str, dataset: str) -> pd.DataFrame | None:
        record = self.checkpoint_record(key)
        if record is None or record.get("dataset") != dataset:
            return None
        try:
            return read_record(self.root, record, dataset)
        except ValueError as exc:
            if "symlink" in str(exc) or "manifest_path" in str(exc):
                raise
            return None

    def save_checkpoint(
        self,
        key: str,
        dataset: str,
        frame: pd.DataFrame,
        **metadata: int | str,
    ) -> dict[str, object]:
        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        path = self.root / "checkpoints" / dataset / f"{token}.parquet"
        if path.is_symlink():
            raise ValueError("all_cap_source_symlink:checkpoint")
        write_fixed_parquet(path, frame, dataset)
        record = physical_record(
            self.root,
            path,
            dataset,
            key,
            frame,
            **{name: value for name, value in metadata.items() if isinstance(value, int)},
        )
        for name, value in metadata.items():
            if not isinstance(value, int):
                record[name] = value
        self.progress["checkpoints"][key] = record
        self._write_progress()
        return record

    def matching_records(self, prefix: str) -> list[dict[str, object]]:
        return [
            dict(record)
            for key, record in sorted(self.progress["checkpoints"].items())
            if key.startswith(prefix) and isinstance(record, Mapping)
        ]

    def reset_candidate(self) -> Path:
        candidate = self.candidate_dir
        if candidate.exists():
            if (
                candidate.parent != self.publications_root
                or not candidate.name.startswith(".all-cap-publication-")
                or candidate.is_symlink()
            ):
                raise ValueError("all_cap_source_staging_path")
            shutil.rmtree(candidate)
        candidate.mkdir(parents=True)
        return candidate


def write_publication_frame(
    candidate: Path,
    dataset: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    path = candidate / SINGLE_FILE_PATHS[dataset]
    write_fixed_parquet(path, frame, dataset)
    return physical_record(candidate, path, dataset, "all", frame)


def merge_stk_limit_year(
    candidate: Path,
    year: str,
    checkpoints: Sequence[tuple[Path, Mapping[str, object]]],
) -> dict[str, object]:
    path = candidate / "stk_limit" / f"year={year}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    writer: pq.ParquetWriter | None = None
    min_date: str | None = None
    max_date: str | None = None
    expected_count = observed_count = missing_count = extra_count = 0
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".parquet",
            delete=False,
        ) as handle:
            temp_name = handle.name
        writer = pq.ParquetWriter(
            temp_name,
            ARROW_SCHEMAS["stk_limit"],
            compression=PARQUET_COMPRESSION.lower(),
        )
        for checkpoint_path, record in checkpoints:
            frame = read_record(checkpoint_path.parent.parent.parent, record, "stk_limit")
            writer.write_table(_table_from_frame("stk_limit", frame))
            date_value = str(frame["trade_date"].iloc[0])
            min_date = date_value if min_date is None else min(min_date, date_value)
            max_date = date_value if max_date is None else max(max_date, date_value)
            expected_count += int(record["expected_count"])
            observed_count += int(record["observed_count"])
            missing_count += int(record["missing_count"])
            extra_count += int(record["extra_count"])
        writer.close()
        writer = None
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if writer is not None:
            writer.close()
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
    compression, rows = _parquet_metadata(path, "stk_limit")
    return {
        "path": path.relative_to(candidate).as_posix(),
        "dataset": "stk_limit",
        "partition": year,
        "rows": int(rows),
        "bytes": int(path.stat().st_size),
        "sha256": sha256(path),
        "schema": schema_contract("stk_limit"),
        "compression": compression,
        "min_date": min_date,
        "max_date": max_date,
        "expected_count": expected_count,
        "observed_count": observed_count,
        "missing_count": missing_count,
        "extra_count": extra_count,
    }


def write_manifest(candidate: Path, manifest: dict[str, object]) -> Path:
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_hash(manifest)
    path = candidate / "manifest.json"
    write_text_atomic(
        path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _validate_manifest_records(
    manifest: Mapping[str, object],
) -> dict[str, list[dict[str, object]]]:
    schemas = manifest.get("dataset_schemas")
    partitions = manifest.get("partitions")
    files = manifest.get("files")
    row_counts = manifest.get("row_counts")
    if (
        not isinstance(schemas, Mapping)
        or set(schemas) != set(PUBLICATION_DATASETS)
        or not isinstance(partitions, Mapping)
        or set(partitions) != set(PUBLICATION_DATASETS)
        or not isinstance(files, list)
        or not isinstance(row_counts, Mapping)
        or set(row_counts) != set(PUBLICATION_DATASETS)
    ):
        raise ValueError("all_cap_source_manifest_incomplete")
    normalized: dict[str, list[dict[str, object]]] = {}
    for dataset in PUBLICATION_DATASETS:
        if schemas.get(dataset) != schema_contract(dataset):
            raise ValueError(_schema_error(dataset))
        records = partitions.get(dataset)
        if not isinstance(records, list) or not records:
            raise ValueError(f"all_cap_source_manifest_incomplete:{dataset}")
        normalized[dataset] = []
        seen: set[str] = set()
        for item in records:
            if not isinstance(item, Mapping):
                raise ValueError("all_cap_source_manifest_malformed")
            record = dict(item)
            relative = _safe_relative(record.get("path")).as_posix()
            partition = str(record.get("partition") or "")
            if record.get("dataset") != dataset:
                raise ValueError("all_cap_source_manifest_partition")
            if dataset in SINGLE_FILE_PATHS:
                if (
                    len(records) != 1
                    or partition != "all"
                    or relative != SINGLE_FILE_PATHS[dataset]
                ):
                    raise ValueError("all_cap_source_manifest_partition")
            elif (
                not _YEAR.fullmatch(partition)
                or relative != f"stk_limit/year={partition}.parquet"
                or partition in seen
            ):
                raise ValueError("all_cap_source_manifest_partition")
            seen.add(partition)
            if record.get("schema") != schema_contract(dataset):
                raise ValueError(_schema_error(dataset))
            if record.get("compression") != PARQUET_COMPRESSION:
                raise ValueError("all_cap_source_manifest_compression")
            min_date = str(record.get("min_date") or "")
            max_date = str(record.get("max_date") or "")
            if (
                not re.fullmatch(r"[0-9]{8}", min_date)
                or not re.fullmatch(r"[0-9]{8}", max_date)
                or min_date > max_date
                or (
                    dataset == "stk_limit"
                    and (min_date[:4] != partition or max_date[:4] != partition)
                )
            ):
                raise ValueError("all_cap_source_manifest_dates")
            if dataset == "stk_limit":
                try:
                    expected = int(record["expected_count"])
                    observed = int(record["observed_count"])
                    missing = int(record["missing_count"])
                    extra = int(record["extra_count"])
                    rows = int(record["rows"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("all_cap_source_manifest_stk_limit") from exc
                if missing != 0 or rows != expected or observed != expected + extra:
                    raise ValueError("all_cap_source_manifest_stk_limit")
            normalized[dataset].append(record)
    file_map = {
        str(item.get("path")): dict(item)
        for item in files
        if isinstance(item, Mapping)
    }
    partition_map = {
        str(record["path"]): record
        for records in normalized.values()
        for record in records
    }
    if len(file_map) != len(files) or file_map != partition_map:
        raise ValueError("all_cap_source_manifest_incomplete:partitions")
    return normalized


def verify_publication(
    publication_dir: Path,
    *,
    expected_reference_indexes: Mapping[str, str],
    partition_validator: Callable[
        [str, pd.DataFrame, Mapping[str, object]], None
    ],
) -> VerifiedPublication:
    root = publication_dir.absolute()
    pubs = root.parent
    _assert_not_symlink(pubs.parent)
    _assert_not_symlink(pubs)
    _assert_contained(root, pubs)
    manifest = _read_json(
        root / "manifest.json",
        missing_code="all_cap_source_manifest_missing",
        root=root,
    )
    payload = dict(manifest)
    manifest_hash = payload.pop("manifest_sha256", None)
    if manifest_hash != canonical_hash(payload):
        raise ValueError("all_cap_source_checksum:manifest")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("status") != "complete"
        or manifest.get("reference_indexes") != dict(expected_reference_indexes)
        or not _PUBLICATION_ID.fullmatch(str(manifest.get("publication_id") or ""))
    ):
        raise ValueError("all_cap_source_manifest_contract")
    records = _validate_manifest_records(manifest)
    frames: dict[str, pd.DataFrame] = {}
    limit_partitions: dict[str, SourcePartition] = {}
    declared_paths: set[str] = set()
    for dataset in PUBLICATION_DATASETS:
        observed_rows = 0
        for record in records[dataset]:
            relative = _safe_relative(record["path"])
            declared_paths.add(relative.as_posix())
            frame = read_record(root, record, dataset)
            observed_rows += len(frame)
            partition_validator(dataset, frame, record)
            if dataset == "stk_limit":
                partition = str(record["partition"])
                limit_partitions[partition] = SourcePartition(
                    path=root.joinpath(*relative.parts),
                    dataset=dataset,
                    partition=partition,
                    record=dict(record),
                )
            else:
                frames[dataset] = frame
        if observed_rows != int(manifest["row_counts"][dataset]):
            raise ValueError(f"all_cap_source_manifest_rows:{dataset}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.parquet")
        if path.is_file()
    }
    if actual_paths != declared_paths:
        raise ValueError("all_cap_source_manifest_incomplete:files")
    return VerifiedPublication(manifest, frames, limit_partitions)


def install_publication(staging: Path, publication_id: str) -> Path:
    pubs = staging.parent
    if (
        staging.is_symlink()
        or pubs.name != "publications"
        or not staging.name.startswith(".all-cap-")
    ):
        raise ValueError("all_cap_source_staging_path")
    destination = pubs / publication_id
    if destination.exists():
        raise ValueError("all_cap_source_publication_exists")
    os.replace(staging, destination)
    return destination


def write_latest(repo_root: str | Path, manifest: Mapping[str, object]) -> Path:
    root = source_root(repo_root)
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


def read_latest(repo_root: str | Path) -> dict[str, object]:
    root = source_root(repo_root)
    marker = _read_json(
        root / "latest.json",
        missing_code="all_cap_source_manifest_missing",
        root=root,
    )
    required = {
        "schema_version",
        "contract_version",
        "status",
        "publication",
        "manifest_sha256",
        "marker_sha256",
    }
    if not required.issubset(marker):
        raise ValueError("all_cap_source_manifest_malformed")
    payload = dict(marker)
    marker_hash = payload.pop("marker_sha256", None)
    if marker_hash != canonical_hash(payload):
        raise ValueError("all_cap_source_checksum:latest")
    if (
        marker.get("schema_version") != SCHEMA_VERSION
        or marker.get("contract_version") != CONTRACT_VERSION
        or marker.get("status") != "complete"
    ):
        raise ValueError("all_cap_source_manifest_contract")
    relative = _safe_relative(marker.get("publication"))
    if len(relative.parts) != 2 or relative.parts[0] != "publications":
        raise ValueError("all_cap_source_manifest_path")
    marker["publication_dir"] = source_root(repo_root).joinpath(*relative.parts)
    return marker
