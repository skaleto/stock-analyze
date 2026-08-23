"""Private fixed-schema publication store for the all-cap universe."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import fcntl
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..utils import write_text_atomic


SCHEMA_VERSION = 4
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
_SLEEVES = ("large", "mid", "small", "micro", "nano_watch")
_SLEEVE_INDEX = {sleeve: index for index, sleeve in enumerate(_SLEEVES)}


@dataclass(frozen=True)
class UniversePartition:
    publication_dir: Path
    dataset: str
    year: str
    record: Mapping[str, object]

    def load(self) -> pd.DataFrame:
        relative = _safe_relative(self.record.get("path"))
        path = self.publication_dir.joinpath(*relative.parts)
        return read_partition(
            path,
            self.dataset,
            root=self.publication_dir,
            expected_bytes=int(self.record["bytes"]),
            expected_sha256=str(self.record["sha256"]),
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


_UNIVERSE_RELATIVE = Path("data/research/a_share_all_cap/v1/universe")


def assert_universe_root(
    repo_root: str | Path,
    *,
    must_exist: bool = False,
) -> Path:
    trusted = Path(repo_root).absolute()
    target = trusted / _UNIVERSE_RELATIVE
    _assert_not_symlink(trusted)
    current = trusted
    for part in _UNIVERSE_RELATIVE.parts:
        current = current / part
        _assert_not_symlink(current)
    try:
        resolved_trusted = trusted.resolve(strict=True)
        resolved_target = target.resolve(strict=must_exist)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("all_cap_universe_manifest_path") from exc
    if not resolved_target.is_relative_to(resolved_trusted):
        raise ValueError("all_cap_universe_manifest_path")
    return target


def universe_root(repo_root: str | Path) -> Path:
    return assert_universe_root(repo_root)


def publications_root(repo_root: str | Path) -> Path:
    root = universe_root(repo_root)
    target = root / "publications"
    _assert_not_symlink(target)
    try:
        resolved_root = root.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
    except OSError as exc:
        raise ValueError("all_cap_universe_manifest_path") from exc
    if not resolved_target.is_relative_to(resolved_root):
        raise ValueError("all_cap_universe_manifest_path")
    return target


def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        _deep_thaw(payload),
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


def _deep_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_thaw(item) for item in value]
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_csv_hash(path: Path) -> tuple[int, str]:
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError("all_cap_universe_cache_identity") from exc
    return _normalized_csv_frame_hash(frame)


def _normalized_csv_frame_hash(frame: pd.DataFrame) -> tuple[int, str]:
    columns = sorted(str(column) for column in frame.columns)
    normalized = frame.loc[:, columns].astype(str)
    if columns and not normalized.empty:
        normalized = normalized.sort_values(columns, kind="stable")
    digest = hashlib.sha256()
    digest.update(
        json.dumps(columns, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    )
    digest.update(b"\n")
    for row in normalized.itertuples(index=False, name=None):
        digest.update(
            json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return len(normalized), digest.hexdigest()


def cache_csv_identity_record(
    relative_path: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    relative = _safe_relative(relative_path)
    rows, content_hash = _normalized_csv_frame_hash(frame)
    return {
        "path": relative.as_posix(),
        "kind": "csv",
        "rows": rows,
        "sha256": content_hash,
    }


def cache_json_identity_record(
    relative_path: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    relative = _safe_relative(relative_path)
    return {
        "path": relative.as_posix(),
        "kind": "json",
        "rows": 1,
        "sha256": hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def missing_cache_identity_record(relative_path: str) -> dict[str, object]:
    relative = _safe_relative(relative_path)
    return {
        "path": relative.as_posix(),
        "kind": "missing",
        "rows": 0,
        "sha256": canonical_hash(
            {"path": relative.as_posix(), "missing": True}
        ),
    }


def build_cache_identity_from_records(
    records: list[Mapping[str, object]],
) -> dict[str, object]:
    ordered = sorted(
        (dict(record) for record in records),
        key=lambda record: str(record.get("path") or ""),
    )
    identity: dict[str, object] = {
        "version": "normalized-cache-v1",
        "files": ordered,
    }
    _validate_cache_identity({**identity, "sha256": canonical_hash(identity)})
    identity["sha256"] = canonical_hash(identity)
    return identity


def build_cache_identity(
    cache_root: str | Path,
    relative_paths: list[str] | tuple[str, ...],
) -> dict[str, object]:
    root = Path(cache_root).absolute()
    records: list[dict[str, object]] = []
    for raw_relative in sorted(set(relative_paths)):
        relative = _safe_relative(raw_relative)
        path = root.joinpath(*relative.parts)
        assert_cache_path(path, root, must_exist=False)
        if not path.exists() and not path.is_symlink():
            records.append(missing_cache_identity_record(relative.as_posix()))
            continue
        _assert_contained(path, root)
        if relative.as_posix() == "_meta.json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("all_cap_universe_cache_identity") from exc
            record = cache_json_identity_record(relative.as_posix(), payload)
        else:
            rows, content_hash = _normalized_csv_hash(path)
            record = {
                "path": relative.as_posix(),
                "kind": "csv",
                "rows": rows,
                "sha256": content_hash,
            }
        records.append(record)
    return build_cache_identity_from_records(records)


def _validate_cache_identity(identity: object) -> dict[str, object]:
    if not isinstance(identity, Mapping):
        raise ValueError("all_cap_universe_cache_identity_manifest")
    thawed = _deep_thaw(identity)
    if not isinstance(thawed, dict):
        raise ValueError("all_cap_universe_cache_identity_manifest")
    payload = dict(thawed)
    identity_hash = payload.pop("sha256", None)
    records = thawed.get("files")
    if (
        thawed.get("version") != "normalized-cache-v1"
        or _SHA256.fullmatch(str(identity_hash or "")) is None
        or identity_hash != canonical_hash(payload)
        or not isinstance(records, (list, tuple))
        or not records
    ):
        raise ValueError("all_cap_universe_cache_identity_manifest")
    observed_paths: set[str] = set()
    for record in records:
        if (
            not isinstance(record, Mapping)
            or record.get("kind") not in {"csv", "json", "missing"}
            or not isinstance(record.get("rows"), int)
            or int(record["rows"]) < 0
            or _SHA256.fullmatch(str(record.get("sha256") or "")) is None
        ):
            raise ValueError("all_cap_universe_cache_identity_manifest")
        relative = _safe_relative(record.get("path")).as_posix()
        if relative in observed_paths:
            raise ValueError("all_cap_universe_cache_identity_manifest")
        observed_paths.add(relative)
    return thawed


def _cache_date_key(value: object) -> str:
    key = str(value or "").strip().replace("-", "")
    if re.fullmatch(r"[0-9]{8}", key) is None:
        raise ValueError("all_cap_universe_cache_identity")
    return key


def _supported_all_cap_code(code: str) -> bool:
    match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", code)
    if match is None:
        raise ValueError("all_cap_universe_cache_identity")
    symbol, exchange = match.groups()
    if exchange == "BJ":
        return symbol.startswith(("4", "8", "9"))
    if exchange == "SH":
        return symbol.startswith(("600", "601", "603", "605", "688"))
    return symbol.startswith(("000", "001", "002", "003", "300", "301"))


def _expected_cache_identity_paths(
    cache_root: Path,
    *,
    start_date: object,
    end_date: object,
) -> set[str]:
    start_key = _cache_date_key(start_date)
    end_key = _cache_date_key(end_date)
    if start_key > end_key:
        raise ValueError("all_cap_universe_cache_identity")
    calendar_path = cache_root / "trade_cal.csv"
    master_path = cache_root / "stock_basic.csv"
    for path in (calendar_path, master_path):
        assert_cache_path(path, cache_root)
    try:
        calendar = pd.read_csv(calendar_path, dtype=str, keep_default_na=False)
        master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError("all_cap_universe_cache_identity") from exc
    if (
        not {"cal_date", "is_open"}.issubset(calendar.columns)
        or not {"ts_code", "list_date", "delist_date"}.issubset(master.columns)
    ):
        raise ValueError("all_cap_universe_cache_identity")
    open_dates = sorted(
        {
            _cache_date_key(row.cal_date)
            for row in calendar.itertuples(index=False)
            if str(row.is_open).strip() in {"1", "True", "true"}
            and start_key <= _cache_date_key(row.cal_date) <= end_key
        }
    )
    if not open_dates:
        raise ValueError("all_cap_universe_cache_identity")
    expected = {"_meta.json", "trade_cal.csv", "stock_basic.csv"}
    all_open_dates = sorted(
        {
            _cache_date_key(row.cal_date)
            for row in calendar.itertuples(index=False)
            if str(row.is_open).strip() in {"1", "True", "true"}
        }
    )
    next_by_date = {
        current: following
        for current, following in zip(all_open_dates, all_open_dates[1:])
    }

    def quarter(value: str) -> tuple[str, int]:
        return value[:4], (int(value[4:6]) - 1) // 3 + 1

    reviews = [
        value
        for value in open_dates
        if value in next_by_date
        and quarter(value) != quarter(next_by_date[value])
    ]
    if not reviews:
        raise ValueError("all_cap_universe_cache_identity")
    first_review = reviews[0]
    first_review_window = [
        _cache_date_key(row.cal_date)
        for row in calendar.itertuples(index=False)
        if str(row.is_open).strip() in {"1", "True", "true"}
        and _cache_date_key(row.cal_date) <= first_review
    ][-252:]
    liquidity_dates = set(open_dates)
    for row in master.itertuples(index=False):
        code = str(row.ts_code).strip()
        list_key = _cache_date_key(row.list_date)
        delist_raw = str(row.delist_date).strip()
        delist_key = _cache_date_key(delist_raw) if delist_raw else None
        if (
            _supported_all_cap_code(code)
            and list_key <= first_review
            and (delist_key is None or first_review <= delist_key)
        ):
            liquidity_dates.update(
                value for value in first_review_window if value >= list_key
            )
    for trade_key in sorted(liquidity_dates):
        if trade_key >= start_key:
            continue
        expected.add(
            f"daily/{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}.csv"
        )
    for trade_key in open_dates:
        dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
        for dataset in ("daily", "daily_basic", "suspend_d"):
            expected.add(f"{dataset}/{dashed}.csv")
    for raw_code in master["ts_code"]:
        code = str(raw_code).strip()
        if _supported_all_cap_code(code):
            for dataset in ("baostock_status", "namechange", "adj_factor"):
                expected.add(f"{dataset}/{code}.csv")
    return expected


def verify_cache_identity(
    cache_root: str | Path,
    identity: object,
    *,
    start_date: object,
    end_date: object,
) -> None:
    validated = _validate_cache_identity(identity)
    declared_paths = {
        str(record["path"]) for record in validated["files"]
    }
    current = build_cache_identity(
        cache_root,
        sorted(declared_paths),
    )
    if current != validated:
        raise ValueError("all_cap_universe_cache_identity_mismatch")
    expected_paths = _expected_cache_identity_paths(
        Path(cache_root).absolute(),
        start_date=start_date,
        end_date=end_date,
    )
    if declared_paths != expected_paths:
        raise ValueError("all_cap_universe_cache_identity_manifest")


def schema_contract(dataset: str) -> dict[str, object]:
    schema = ARROW_SCHEMAS[dataset]
    return {
        "columns": list(schema.names),
        "fields": [
            {"name": field.name, "dtype": str(field.type)}
            for field in schema
        ],
    }


def _code_set_hash(codes: list[str] | tuple[str, ...]) -> str:
    return canonical_hash({"codes": sorted(str(code) for code in codes)})


def build_cross_section_contract(
    *,
    codes: list[str] | tuple[str, ...],
    membership_dates: list[str] | tuple[str, ...],
    daily_dates: list[str] | tuple[str, ...],
) -> dict[str, object]:
    normalized_codes = sorted(str(code) for code in codes)
    if (
        not normalized_codes
        or len(normalized_codes) != len(set(normalized_codes))
        or any(
            re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", code) is None
            for code in normalized_codes
        )
    ):
        raise ValueError("all_cap_universe_cross_section")
    code_hash = _code_set_hash(tuple(normalized_codes))
    result: dict[str, object] = {}
    for dataset, raw_dates in (
        ("membership", membership_dates),
        ("daily_hard_status", daily_dates),
    ):
        dates = sorted(str(value) for value in raw_dates)
        if (
            not dates
            or len(dates) != len(set(dates))
            or any(re.fullmatch(r"[0-9]{8}", value) is None for value in dates)
        ):
            raise ValueError("all_cap_universe_cross_section")
        by_year: dict[str, object] = {}
        for year in sorted({value[:4] for value in dates}):
            year_dates = [value for value in dates if value.startswith(year)]
            by_year[year] = {
                "dates": year_dates,
                "code_count": len(normalized_codes),
                "codes_sha256": code_hash,
                "rows": len(year_dates) * len(normalized_codes),
            }
        result[dataset] = by_year
    return result


def _validate_cross_sections(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(DATASETS):
        raise ValueError("all_cap_universe_cross_section")
    normalized = _deep_thaw(value)
    if not isinstance(normalized, dict):
        raise ValueError("all_cap_universe_cross_section")
    for dataset in DATASETS:
        by_year = normalized[dataset]
        if not isinstance(by_year, dict) or not by_year:
            raise ValueError("all_cap_universe_cross_section")
        for year, record in by_year.items():
            if (
                _YEAR.fullmatch(str(year)) is None
                or not isinstance(record, dict)
                or set(record)
                != {"dates", "code_count", "codes_sha256", "rows"}
                or not isinstance(record["dates"], list)
                or not record["dates"]
                or record["dates"] != sorted(set(record["dates"]))
                or any(
                    re.fullmatch(r"[0-9]{8}", str(value)) is None
                    or not str(value).startswith(str(year))
                    for value in record["dates"]
                )
                or not isinstance(record["code_count"], int)
                or record["code_count"] <= 0
                or _SHA256.fullmatch(str(record["codes_sha256"])) is None
                or record["rows"]
                != len(record["dates"]) * record["code_count"]
            ):
                raise ValueError("all_cap_universe_cross_section")
    return normalized


def _validate_coverage(value: object) -> None:
    thresholds = {
        "daily": 0.99,
        "daily_basic": 0.99,
        "adjustment": 0.98,
    }
    if not isinstance(value, Mapping) or set(value) != set(thresholds):
        raise ValueError("all_cap_universe_manifest_coverage")
    for dataset, threshold in thresholds.items():
        record = value[dataset]
        if not isinstance(record, Mapping):
            raise ValueError("all_cap_universe_manifest_coverage")
        try:
            declared_threshold = float(record["threshold"])
            minimum = float(record["minimum"])
            expected_rows = int(record["expected_rows"])
            observed_rows = int(record["observed_rows"])
            missing_rows = int(record["missing_rows"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("all_cap_universe_manifest_coverage") from exc
        if (
            not math.isclose(declared_threshold, threshold, abs_tol=1e-12)
            or not 0.0 <= minimum <= 1.0
            or minimum + 1e-12 < threshold
            or expected_rows < 0
            or not 0 <= observed_rows <= expected_rows
            or missing_rows != expected_rows - observed_rows
        ):
            raise ValueError("all_cap_universe_manifest_coverage")


def _safe_relative(value: object) -> PurePosixPath:
    relative = PurePosixPath(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("all_cap_universe_manifest_path")
    return relative


def _assert_not_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"all_cap_universe_symlink:{path.name}")


def assert_cache_root(cache_root: Path, repo_root: Path) -> None:
    trusted = Path(repo_root).absolute()
    target = Path(cache_root).absolute()
    try:
        relative = target.relative_to(trusted)
    except ValueError as exc:
        raise ValueError("all_cap_universe_cache_path") from exc
    _assert_not_symlink(trusted)
    current = trusted
    for part in relative.parts:
        current = current / part
        _assert_not_symlink(current)
    try:
        resolved_trusted = trusted.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("all_cap_universe_cache_path") from exc
    if not resolved_target.is_relative_to(resolved_trusted):
        raise ValueError("all_cap_universe_cache_path")


def assert_cache_path(
    path: Path,
    cache_root: Path,
    *,
    must_exist: bool = True,
) -> None:
    try:
        relative = path.relative_to(cache_root)
    except ValueError as exc:
        raise ValueError("all_cap_universe_cache_path") from exc
    if ".." in relative.parts:
        raise ValueError("all_cap_universe_cache_path")
    _assert_contained(path, cache_root, must_exist=must_exist)


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


def _parquet_metadata(parquet: pq.ParquetFile, dataset: str) -> tuple[int, set[str]]:
    try:
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


def read_partition(
    path: Path,
    dataset: str,
    *,
    root: Path,
    expected_bytes: int,
    expected_sha256: str,
) -> pd.DataFrame:
    _assert_contained(path, root)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"all_cap_universe_parquet:{dataset}") from exc
    if (
        len(payload) != expected_bytes
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise ValueError(
            f"all_cap_universe_checksum:{path.relative_to(root).as_posix()}"
        )
    try:
        parquet = pq.ParquetFile(pa.BufferReader(payload))
        rows, codecs = _parquet_metadata(parquet, dataset)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_universe_parquet:{dataset}") from exc
    if codecs != {PARQUET_COMPRESSION}:
        raise ValueError("all_cap_universe_manifest_compression")
    try:
        frame = parquet.read().to_pandas(types_mapper=pd.ArrowDtype)
    except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
        raise ValueError(f"all_cap_universe_parquet:{dataset}") from exc
    if len(frame) != rows:
        raise ValueError(f"all_cap_universe_manifest_rows:{dataset}")
    return frame


def _semantic_error(dataset: str) -> None:
    raise ValueError(f"all_cap_universe_manifest_semantics:{dataset}")


def _valid_numeric(values: pd.Series, *, positive: bool) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric.notna() & numeric.map(
        lambda value: math.isfinite(float(value)) if not pd.isna(value) else False
    )
    return finite & (numeric.gt(0.0) if positive else numeric.ge(0.0))


def _membership_reason_sets(frame: pd.DataFrame) -> list[frozenset[str]]:
    parsed: list[frozenset[str]] = []
    for raw in frame["exclusion_reasons"]:
        if pd.isna(raw):
            _semantic_error("membership")
        value = str(raw)
        reasons = value.split(";") if value else []
        if (
            any(not reason for reason in reasons)
            or len(reasons) != len(set(reasons))
            or value != ";".join(sorted(reasons))
        ):
            _semantic_error("membership")
        parsed.append(frozenset(reasons))
    return parsed


def _validate_membership_semantics(frame: pd.DataFrame) -> None:
    if frame.empty or frame["eligible"].isna().any():
        _semantic_error("membership")
    reason_sets = _membership_reason_sets(frame)
    eligible = frame["eligible"].astype(bool)
    has_reasons = pd.Series(
        [bool(reasons) for reasons in reason_sets],
        index=frame.index,
    )
    populated_rank = frame["size_rank"].notna()
    populated_raw = frame["raw_sleeve"].notna() & frame["raw_sleeve"].ne("")
    populated_stable = (
        frame["stable_sleeve"].notna() & frame["stable_sleeve"].ne("")
    )
    if (
        (eligible & has_reasons).any()
        or ((~eligible) & ~has_reasons).any()
        or (eligible & ~(populated_rank & populated_raw & populated_stable)).any()
        or ((~eligible) & (populated_rank | populated_raw | populated_stable)).any()
    ):
        _semantic_error("membership")

    total_valid = _valid_numeric(frame["total_mv"], positive=True)
    circ_valid = _valid_numeric(frame["circ_mv"], positive=True)
    amount_valid = _valid_numeric(frame["avg_amount_252"], positive=False)
    total_invalid_reason = pd.Series(
        ["total_mv_invalid" in reasons for reasons in reason_sets],
        index=frame.index,
    )
    circ_invalid_reason = pd.Series(
        ["circ_mv_invalid" in reasons for reasons in reason_sets],
        index=frame.index,
    )
    amount_invalid_reason = pd.Series(
        ["amount_invalid" in reasons for reasons in reason_sets],
        index=frame.index,
    )
    total_source = frame["total_mv_source_date"].notna() & frame[
        "total_mv_source_date"
    ].ne("")
    amount_source = frame["avg_amount_source_date"].notna() & frame[
        "avg_amount_source_date"
    ].ne("")
    status_source = frame["status_source"].notna() & frame["status_source"].ne("")
    status_missing_reason = pd.Series(
        ["status_missing" in reasons for reasons in reason_sets],
        index=frame.index,
    )
    non_trading = pd.to_numeric(frame["non_trading_days_252"], errors="coerce")
    if (
        (total_invalid_reason != ~total_valid).any()
        or (circ_invalid_reason != ~circ_valid).any()
        or (amount_invalid_reason != ~amount_valid).any()
        or (total_valid & ~total_source).any()
        or (amount_valid & ~amount_source).any()
        or (status_missing_reason != ~status_source).any()
        or (eligible & ~(total_valid & circ_valid & amount_valid)).any()
        or (
            eligible
            & pd.to_numeric(frame["avg_amount_252"], errors="coerce").le(0.0)
        ).any()
        or (
            total_valid
            & circ_valid
            & pd.to_numeric(frame["circ_mv"], errors="coerce").gt(
                pd.to_numeric(frame["total_mv"], errors="coerce")
            )
        ).any()
        or non_trading.isna().any()
        or non_trading.lt(0).any()
    ):
        _semantic_error("membership")

    previous_sleeves: dict[str, str] = {}
    for review_date in sorted(frame["review_date"].astype(str).unique()):
        current = frame.loc[frame["review_date"].astype(str).eq(review_date)]
        ranked = current.loc[current["eligible"]].copy()
        if ranked.empty:
            previous_sleeves = {}
            continue
        ranked["size_rank"] = pd.to_numeric(ranked["size_rank"], errors="coerce")
        ranked = ranked.sort_values("size_rank", kind="stable")
        if ranked["size_rank"].tolist() != list(range(1, len(ranked) + 1)):
            _semantic_error("membership")
        market_order = current.loc[current["eligible"]].sort_values(
            ["total_mv", "code"],
            ascending=[False, True],
            kind="stable",
        )
        if (
            ranked["code"].astype(str).tolist()
            != market_order["code"].astype(str).tolist()
        ):
            _semantic_error("membership")
        raw_sleeves = ranked["raw_sleeve"].astype(str).tolist()
        if any(value not in _SLEEVE_INDEX for value in raw_sleeves):
            _semantic_error("membership")
        raw_indices = [_SLEEVE_INDEX[value] for value in raw_sleeves]
        if (
            raw_sleeves[0] != "large"
            or raw_indices != sorted(raw_indices)
            or any(
                right - left > 1
                for left, right in zip(raw_indices, raw_indices[1:])
            )
        ):
            _semantic_error("membership")
        current_sleeves: dict[str, str] = {}
        for row in ranked.itertuples(index=False):
            code = str(row.code)
            raw_sleeve = str(row.raw_sleeve)
            stable_sleeve = str(row.stable_sleeve)
            if (
                stable_sleeve not in _SLEEVE_INDEX
                or (
                    stable_sleeve != raw_sleeve
                    and previous_sleeves.get(code) != stable_sleeve
                )
            ):
                _semantic_error("membership")
            current_sleeves[code] = stable_sleeve
        previous_sleeves = current_sleeves


def _validate_hard_status_semantics(frame: pd.DataFrame) -> None:
    boolean_columns = (
        "listed", "st", "delisting", "suspended", "at_limit_up",
        "at_limit_down", "status_complete", "status_conflict",
        "buy_executable", "sell_executable", "prohibit_new_position",
    )
    if frame.empty or any(frame[column].isna().any() for column in boolean_columns):
        _semantic_error("daily_hard_status")
    source = frame["status_source"].astype("string")
    if source.isna().any() or source.eq("").any():
        _semantic_error("daily_hard_status")
    tokens = source.str.split(";")
    status_missing = tokens.map(
        lambda values: any(value.startswith("missing:") for value in values)
    )
    conflict_reason = tokens.map(
        lambda values: any(value.startswith("conflict:") for value in values)
    )
    status_complete = frame["status_complete"].astype(bool)
    status_conflict = frame["status_conflict"].astype(bool)
    buy_executable = frame["buy_executable"].astype(bool)
    sell_executable = frame["sell_executable"].astype(bool)
    suspended = frame["suspended"].astype(bool)
    listed = frame["listed"].astype(bool)
    at_limit_up = frame["at_limit_up"].astype(bool)
    at_limit_down = frame["at_limit_down"].astype(bool)
    buy_blocked = (
        ~status_complete
        | status_conflict
        | suspended
        | ~listed
        | frame["st"].astype(bool)
        | frame["delisting"].astype(bool)
        | at_limit_up
    )
    sell_blocked = (
        ~status_complete
        | status_conflict
        | suspended
        | ~listed
        | at_limit_down
    )
    valid_limit_up = _valid_numeric(frame["limit_up"], positive=True)
    valid_limit_down = _valid_numeric(frame["limit_down"], positive=True)
    if (
        (status_conflict != conflict_reason).any()
        or (status_complete != ~(status_missing | status_conflict)).any()
        or (status_complete & ~(valid_limit_up & valid_limit_down)).any()
        or (
            status_complete
            & pd.to_numeric(frame["limit_up"], errors="coerce").le(
                pd.to_numeric(frame["limit_down"], errors="coerce")
            )
        ).any()
        or (at_limit_up & at_limit_down).any()
        or (buy_blocked & buy_executable).any()
        or (sell_blocked & sell_executable).any()
        or (frame["prohibit_new_position"].astype(bool) != ~buy_executable).any()
    ):
        _semantic_error("daily_hard_status")


def _validate_record(
    publication_dir: Path,
    dataset: str,
    record: Mapping[str, object],
    cross_section: Mapping[str, object],
) -> tuple[str, UniversePartition, int, pd.DataFrame]:
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
    if (
        not isinstance(record.get("bytes"), int)
        or _SHA256.fullmatch(str(record.get("sha256") or "")) is None
    ):
        raise ValueError(f"all_cap_universe_checksum:{relative.as_posix()}")
    frame = read_partition(
        path,
        dataset,
        root=publication_dir,
        expected_bytes=int(record["bytes"]),
        expected_sha256=str(record["sha256"]),
    )
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
    actual_dates = sorted(frame[date_column].astype(str).unique().tolist())
    if (
        actual_dates != list(cross_section["dates"])
        or len(frame) != int(cross_section["rows"])
    ):
        raise ValueError(f"all_cap_universe_cross_section:{dataset}:{year}")
    for trade_key, rows_for_date in frame.groupby(date_column, sort=False):
        codes = sorted(rows_for_date["code"].astype(str).tolist())
        if (
            len(codes) != int(cross_section["code_count"])
            or len(codes) != len(set(codes))
            or _code_set_hash(tuple(codes)) != cross_section["codes_sha256"]
        ):
            raise ValueError(
                f"all_cap_universe_cross_section:{dataset}:{trade_key}"
            )
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
        classified_industry = pd.Series(False, index=frame.index)
        for column in ("industry_l1", "industry_l2", "industry_l3"):
            values = frame[column].astype("string")
            classified_industry |= (
                values.notna()
                & values.ne("")
                & values.ne("unclassified")
            )
        industry_source_dates = frame["industry_source_date"].astype("string")
        normalized_industry_dates = industry_source_dates.fillna("")
        valid_industry_dates = (
            normalized_industry_dates.str.fullmatch(r"[0-9]{8}", na=False)
            & pd.to_datetime(
                normalized_industry_dates,
                format="%Y%m%d",
                errors="coerce",
            ).notna()
            & normalized_industry_dates.le(review_dates)
        )
        if (classified_industry & ~valid_industry_dates).any():
            raise ValueError("all_cap_universe_manifest_dates")
    else:
        if not frame["hard_status_version"].eq(
            "a-share-all-cap-hard-status-v1"
        ).all():
            raise ValueError("all_cap_universe_manifest_contract")
        _validate_hard_status_semantics(frame)
    partition = UniversePartition(
        publication_dir=publication_dir,
        dataset=dataset,
        year=year,
        record=_deep_freeze(dict(record)),
    )
    return year, partition, len(frame), frame


def _validate_readiness(
    readiness: object,
    cache_identity: Mapping[str, object],
) -> None:
    if not isinstance(readiness, Mapping):
        raise ValueError("all_cap_universe_manifest_readiness")
    prefixes = {
        "missing_baostock_status_codes": "baostock_status/",
        "missing_namechange_codes": "namechange/",
        "missing_adj_factor_codes": "adj_factor/",
    }
    expected: dict[str, list[str]] = {field: [] for field in prefixes}
    for record in cache_identity["files"]:
        if record["kind"] != "missing":
            continue
        path = str(record["path"])
        for field, prefix in prefixes.items():
            if path.startswith(prefix) and path.endswith(".csv"):
                expected[field].append(path.removeprefix(prefix).removesuffix(".csv"))
    normalized = {
        str(field): sorted(str(code) for code in codes)
        for field, codes in readiness.items()
        if isinstance(codes, (list, tuple))
    }
    for codes in expected.values():
        codes.sort()
    if normalized != expected:
        raise ValueError("all_cap_universe_manifest_readiness")


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
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("all_cap_universe_manifest_schema")
    if (
        manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("status") != "complete"
        or _SHA256.fullmatch(
            str(manifest.get("contract_sha256") or "")
        ) is None
        or not _PUBLICATION_ID.fullmatch(publication_id)
        or publication_id != root.name.removeprefix(".all-cap-universe-")
        or publication_id.split("_", 2)[:2]
        != [manifest.get("start_date"), manifest.get("end_date")]
    ):
        raise ValueError("all_cap_universe_manifest_contract")
    cache_identity = _validate_cache_identity(manifest.get("cache_identity"))
    _validate_readiness(manifest.get("readiness"), cache_identity)
    _validate_coverage(manifest.get("coverage"))
    cross_sections = _validate_cross_sections(manifest.get("cross_sections"))
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
    membership_frames: list[pd.DataFrame] = []
    declared_paths: set[str] = set()
    for dataset in DATASETS:
        records = partitions[dataset]
        if not isinstance(records, list) or not records:
            raise ValueError(f"all_cap_universe_manifest_incomplete:{dataset}")
        observed_rows = 0
        for raw_record in records:
            if not isinstance(raw_record, Mapping):
                raise ValueError("all_cap_universe_manifest_malformed")
            if str(raw_record.get("partition") or "") not in cross_sections[dataset]:
                raise ValueError("all_cap_universe_cross_section")
            year, partition, rows, frame = _validate_record(
                root,
                dataset,
                raw_record,
                cross_sections[dataset][str(raw_record["partition"])],
            )
            if year in loaded[dataset]:
                raise ValueError("all_cap_universe_manifest_partition")
            loaded[dataset][year] = partition
            declared_paths.add(str(raw_record["path"]))
            observed_rows += rows
            if dataset == "membership":
                membership_frames.append(frame)
        if observed_rows != int(row_counts[dataset]):
            raise ValueError(f"all_cap_universe_manifest_rows:{dataset}")
        if set(loaded[dataset]) != set(cross_sections[dataset]):
            raise ValueError("all_cap_universe_cross_section")
    _validate_membership_semantics(
        pd.concat(membership_frames, ignore_index=True)
    )
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


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


@contextmanager
def _latest_publish_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".publish.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def publish_latest_if_cache_unchanged(
    repo_root: str | Path,
    manifest: Mapping[str, object],
    *,
    cache_root: str | Path,
    cache_identity: object,
    start_date: object,
    end_date: object,
) -> Path:
    root = universe_root(repo_root)
    latest = root / "latest.json"
    with _latest_publish_lock(root):
        previous = latest.read_bytes() if latest.is_file() else None
        verify_cache_identity(
            cache_root,
            cache_identity,
            start_date=start_date,
            end_date=end_date,
        )
        try:
            path = write_latest(repo_root, manifest)
            verify_cache_identity(
                cache_root,
                cache_identity,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            if previous is None:
                latest.unlink(missing_ok=True)
            else:
                _write_bytes_atomic(latest, previous)
            raise
    return path


def remove_publication_if_unreferenced(
    repo_root: str | Path,
    publication_dir: str | Path,
) -> bool:
    root = assert_universe_root(repo_root, must_exist=True)
    pubs = publications_root(repo_root)
    publication = Path(publication_dir).absolute()
    if (
        publication.parent != pubs.absolute()
        or _PUBLICATION_ID.fullmatch(publication.name) is None
        or publication.is_symlink()
    ):
        raise ValueError("all_cap_universe_manifest_path")
    _assert_contained(publication, pubs)
    with _latest_publish_lock(root):
        latest = root / "latest.json"
        if latest.exists() or latest.is_symlink():
            try:
                marker = _read_json(
                    latest,
                    root=root,
                    missing="all_cap_universe_manifest_missing",
                )
            except ValueError:
                return False
            if marker.get("publication") == f"publications/{publication.name}":
                return False
        shutil.rmtree(publication)
        return True


def _expected_cross_sections_from_cache(
    cache_root: Path,
    *,
    start_date: object,
    end_date: object,
) -> dict[str, object]:
    start_key = _cache_date_key(start_date)
    end_key = _cache_date_key(end_date)
    try:
        calendar = pd.read_csv(
            cache_root / "trade_cal.csv",
            dtype=str,
            keep_default_na=False,
        )
        master = pd.read_csv(
            cache_root / "stock_basic.csv",
            dtype=str,
            keep_default_na=False,
        )
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError("all_cap_universe_cross_section") from exc
    if (
        not {"cal_date", "is_open"}.issubset(calendar.columns)
        or "ts_code" not in master.columns
    ):
        raise ValueError("all_cap_universe_cross_section")
    all_open = sorted(
        {
            _cache_date_key(row.cal_date)
            for row in calendar.itertuples(index=False)
            if str(row.is_open).strip() in {"1", "True", "true"}
        }
    )
    daily_dates = [
        value for value in all_open if start_key <= value <= end_key
    ]
    next_by_date = {
        current: following for current, following in zip(all_open, all_open[1:])
    }

    def quarter(value: str) -> tuple[str, int]:
        return value[:4], (int(value[4:6]) - 1) // 3 + 1

    membership_dates = [
        value
        for value in daily_dates
        if value in next_by_date
        and quarter(value) != quarter(next_by_date[value])
    ]
    return build_cross_section_contract(
        codes=tuple(master["ts_code"].astype(str)),
        membership_dates=tuple(membership_dates),
        daily_dates=tuple(daily_dates),
    )


def load_latest(repo_root: str | Path) -> VerifiedAllCapUniverse:
    repo = Path(repo_root).absolute()
    cache_root = repo / "data/shared/backtest_cache"
    assert_cache_root(cache_root, repo)
    root = assert_universe_root(repo, must_exist=True)
    marker = _read_json(
        root / "latest.json",
        root=root,
        missing="all_cap_universe_manifest_missing",
    )
    payload = dict(marker)
    marker_hash = payload.pop("marker_sha256", None)
    if marker_hash != canonical_hash(payload):
        raise ValueError("all_cap_universe_checksum:latest")
    if marker.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("all_cap_universe_manifest_schema")
    if (
        marker.get("contract_version") != CONTRACT_VERSION
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
    verify_cache_identity(
        cache_root,
        verified.metadata.get("cache_identity"),
        start_date=verified.metadata.get("start_date"),
        end_date=verified.metadata.get("end_date"),
    )
    expected_cross_sections = _expected_cross_sections_from_cache(
        cache_root,
        start_date=verified.metadata.get("start_date"),
        end_date=verified.metadata.get("end_date"),
    )
    if (
        _deep_thaw(verified.metadata.get("cross_sections"))
        != expected_cross_sections
    ):
        raise ValueError("all_cap_universe_cross_section")
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
