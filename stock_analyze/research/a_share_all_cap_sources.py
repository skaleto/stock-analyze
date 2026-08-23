"""Verified reference sources for the research-only A-share all-cap campaign."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


__all__ = [
    "REFERENCE_INDEXES",
    "AllCapSourceManifest",
    "collect_all_cap_sources",
    "load_verified_all_cap_sources",
    "publish_all_cap_sources",
]


REFERENCE_INDEXES = {
    "000300.SH": "large",
    "000905.SH": "mid",
    "000852.SH": "small",
    "932000.CSI": "micro",
    "000985.CSI": "all_share",
}

_SW2021_L1_CODES = (
    "801010.SI",
    "801030.SI",
    "801040.SI",
    "801050.SI",
    "801080.SI",
    "801110.SI",
    "801120.SI",
    "801130.SI",
    "801140.SI",
    "801150.SI",
    "801160.SI",
    "801170.SI",
    "801180.SI",
    "801200.SI",
    "801210.SI",
    "801230.SI",
    "801710.SI",
    "801720.SI",
    "801730.SI",
    "801740.SI",
    "801750.SI",
    "801760.SI",
    "801770.SI",
    "801780.SI",
    "801790.SI",
    "801880.SI",
    "801890.SI",
    "801950.SI",
    "801960.SI",
    "801970.SI",
    "801980.SI",
)

_SOURCE_SCHEMA_VERSION = 1
_SOURCE_CONTRACT_VERSION = "a-share-all-cap-sources-v1"
CSI2000_INCEPTION = date(2023, 9, 1)

_SLEEVE_INDEXES = tuple(
    code for code, sleeve in REFERENCE_INDEXES.items() if sleeve != "all_share"
)
_PUBLICATION_ID = re.compile(r"^[0-9]{8}_[0-9]{8}_[a-f0-9]{32}$")
_IDENTIFIER_COLUMNS = frozenset(
    {
        "cal_date",
        "con_code",
        "in_date",
        "index_code",
        "is_new",
        "l1_code",
        "l2_code",
        "l3_code",
        "out_date",
        "trade_date",
        "ts_code",
    }
)
_DATASETS = frozenset(
    {"index_weights", "index_daily", "industry_membership", "stk_limit"}
)


@dataclass(frozen=True)
class AllCapSourceManifest:
    """A fully verified source publication and its normalized tables."""

    metadata: Mapping[str, object]
    publication_dir: Path
    index_daily: Mapping[str, pd.DataFrame]
    index_weights: Mapping[str, pd.DataFrame]
    industry_membership: pd.DataFrame
    stk_limit: pd.DataFrame


def _source_root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve()
        / "data/research/a_share_all_cap/v1/sources"
    )


def _date_key(value: object, *, code: str) -> str:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        raw = str(value or "").strip()
        try:
            parsed = (
                date.fromisoformat(raw)
                if "-" in raw
                else datetime.strptime(raw, "%Y%m%d").date()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(code) from exc
    return parsed.strftime("%Y%m%d")


def _source_frame(value: object, *, source_name: str) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return pd.DataFrame([dict(row) for row in value])
    raise ValueError(f"all_cap_source_transport:{source_name}")


def _string_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    error_code: str,
    nullable: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    missing = set(columns).difference(frame.columns)
    if missing:
        raise ValueError(error_code)
    result = frame.copy()
    for column in columns:
        values = result[column].astype("string[pyarrow]").str.strip()
        values = values.mask(values == "")
        if column not in nullable and values.isna().any():
            raise ValueError(error_code)
        result[column] = values.astype("string[pyarrow]")
    return result


def _date_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    error_code: str,
    nullable: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result.columns:
            raise ValueError(error_code)
        normalized: list[object] = []
        for value in result[column]:
            if pd.isna(value) or not str(value).strip():
                if column not in nullable:
                    raise ValueError(error_code)
                normalized.append(pd.NA)
                continue
            normalized.append(_date_key(value, code=error_code))
        result[column] = pd.Series(
            normalized,
            index=result.index,
            dtype="string[pyarrow]",
        )
    return result


def _numeric_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    error_code: str,
    positive: bool = False,
) -> pd.DataFrame:
    result = frame.copy()
    if set(columns).difference(result.columns):
        raise ValueError(error_code)
    for column in columns:
        values = pd.to_numeric(result[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.astype(float)).all():
            raise ValueError(error_code)
        if positive and (values <= 0).any():
            raise ValueError(error_code)
        result[column] = values
    return result


def _normalize_reloaded_identifiers(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in _IDENTIFIER_COLUMNS.intersection(normalized.columns):
        normalized[column] = normalized[column].astype("string[pyarrow]")
    return normalized


def _month_periods(start: date, end: date) -> list[tuple[date, date]]:
    periods: list[tuple[date, date]] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        month_end = date(
            cursor.year,
            cursor.month,
            calendar.monthrange(cursor.year, cursor.month)[1],
        )
        periods.append((max(start, cursor), min(end, month_end)))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return periods


def _expected_pre_inception(start: date, end: date) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for period_start, period_end in _month_periods(start, end):
        if period_end < CSI2000_INCEPTION:
            records.append(
                {
                    "dataset": "index_weights",
                    "ts_code": "932000.CSI",
                    "period_start": period_start.strftime("%Y%m%d"),
                    "period_end": period_end.strftime("%Y%m%d"),
                    "status": "pre_inception",
                }
            )
    return records


def _normalize_index_weights(
    source: object,
    *,
    index_code: str,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    code = "all_cap_source_index_weight_schema"
    frame = _source_frame(source, source_name="index_weight")
    if frame.empty:
        raise ValueError("all_cap_source_index_weight_empty")
    frame = _string_columns(
        frame,
        ("index_code", "con_code", "trade_date"),
        error_code=code,
    )
    frame = _date_columns(frame, ("trade_date",), error_code=code)
    frame = _numeric_columns(frame, ("weight",), error_code=code)
    if (
        set(frame["index_code"]) != {index_code}
        or (frame["trade_date"] < period_start).any()
        or (frame["trade_date"] > period_end).any()
        or (frame["weight"] < 0).any()
    ):
        raise ValueError(code)
    frame = frame.drop_duplicates().reset_index(drop=True)
    if frame.duplicated(["index_code", "con_code", "trade_date"]).any():
        raise ValueError("all_cap_source_index_weight_duplicate")
    return frame


def _collect_index_weights(
    pro_client: object,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    pieces: list[pd.DataFrame] = []
    pre_inception = _expected_pre_inception(start, end)
    for period_start, period_end in _month_periods(start, end):
        for index_code in _SLEEVE_INDEXES:
            if index_code == "932000.CSI" and period_end < CSI2000_INCEPTION:
                continue
            query_start = max(period_start, CSI2000_INCEPTION)
            if index_code != "932000.CSI":
                query_start = period_start
            start_key = query_start.strftime("%Y%m%d")
            end_key = period_end.strftime("%Y%m%d")
            source = pro_client.index_weight(
                index_code=index_code,
                start_date=start_key,
                end_date=end_key,
            )
            pieces.append(
                _normalize_index_weights(
                    source,
                    index_code=index_code,
                    period_start=start_key,
                    period_end=end_key,
                )
            )
    if not pieces:
        raise ValueError("all_cap_source_index_weight_empty")
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    combined = combined.sort_values(
        ["index_code", "trade_date", "con_code"]
    ).reset_index(drop=True)
    return _normalize_reloaded_identifiers(combined), pre_inception


def _normalize_index_daily(
    source: object,
    *,
    index_code: str,
    start_key: str,
    end_key: str,
) -> pd.DataFrame:
    code = "all_cap_source_index_daily_schema"
    frame = _source_frame(source, source_name="index_daily")
    if frame.empty:
        raise ValueError("all_cap_source_index_daily_empty")
    frame = _string_columns(
        frame,
        ("ts_code", "trade_date"),
        error_code=code,
    )
    frame = _date_columns(frame, ("trade_date",), error_code=code)
    frame = _numeric_columns(
        frame,
        ("close",),
        error_code=code,
        positive=True,
    )
    if (
        set(frame["ts_code"]) != {index_code}
        or (frame["trade_date"] < start_key).any()
        or (frame["trade_date"] > end_key).any()
    ):
        raise ValueError(code)
    frame = frame.drop_duplicates().reset_index(drop=True)
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("all_cap_source_index_daily_duplicate")
    return frame


def _collect_index_daily(
    pro_client: object,
    start: date,
    end: date,
) -> pd.DataFrame:
    start_key = start.strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    pieces = [
        _normalize_index_daily(
            pro_client.index_daily(
                ts_code=index_code,
                start_date=start_key,
                end_date=end_key,
            ),
            index_code=index_code,
            start_key=start_key,
            end_key=end_key,
        )
        for index_code in REFERENCE_INDEXES
    ]
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    return _normalize_reloaded_identifiers(
        combined.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    )


def _industry_codes(pro_client: object) -> tuple[str, ...]:
    discover = getattr(pro_client, "index_classify", None)
    if not callable(discover):
        return _SW2021_L1_CODES
    source = _source_frame(
        discover(level="L1", src="SW2021"),
        source_name="index_classify",
    )
    source = _string_columns(
        source,
        ("index_code",),
        error_code="all_cap_source_industry_codes",
    )
    discovered = set(source["index_code"])
    if discovered != set(_SW2021_L1_CODES) or len(source["index_code"].unique()) != 31:
        raise ValueError("all_cap_source_industry_codes")
    return _SW2021_L1_CODES


def _normalize_industry_membership(
    source: object,
    *,
    l1_code: str,
    is_new: str,
) -> pd.DataFrame:
    code = "all_cap_source_industry_schema"
    frame = _source_frame(source, source_name="index_member_all")
    if frame.empty:
        raise ValueError("all_cap_source_industry_empty")
    if "l1_code" not in frame.columns:
        frame["l1_code"] = l1_code
    if "is_new" not in frame.columns:
        frame["is_new"] = is_new
    frame = _string_columns(
        frame,
        (
            "l1_code",
            "l2_code",
            "l3_code",
            "ts_code",
            "in_date",
            "out_date",
            "is_new",
        ),
        error_code=code,
        nullable=frozenset({"out_date"}),
    )
    frame = _date_columns(
        frame,
        ("in_date", "out_date"),
        error_code=code,
        nullable=frozenset({"out_date"}),
    )
    frame["is_new"] = frame["is_new"].str.upper()
    if set(frame["l1_code"]) != {l1_code} or set(frame["is_new"]) != {is_new}:
        raise ValueError(code)
    closed = frame["out_date"].notna()
    if (frame.loc[closed, "out_date"] <= frame.loc[closed, "in_date"]).any():
        raise ValueError("all_cap_source_industry_interval")
    return frame


def _reject_industry_overlaps(frame: pd.DataFrame) -> None:
    for _, stock in frame.groupby("ts_code", sort=False):
        for level in ("l1_code", "l2_code", "l3_code"):
            intervals = (
                stock[[level, "in_date", "out_date", "is_new"]]
                .drop_duplicates()
                .sort_values(["in_date", "out_date"], na_position="last")
            )
            latest_end = ""
            for row in intervals.itertuples(index=False, name=None):
                in_date = str(row[1])
                out_date = "99991231" if pd.isna(row[2]) else str(row[2])
                if latest_end and in_date < latest_end:
                    raise ValueError(
                        f"all_cap_source_industry_overlap:{stock.iloc[0]['ts_code']}:{level}"
                    )
                latest_end = max(latest_end, out_date)


def _collect_industry_membership(
    pro_client: object,
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    pieces: list[pd.DataFrame] = []
    requests: list[dict[str, str]] = []
    for l1_code in _industry_codes(pro_client):
        for is_new in ("Y", "N"):
            pieces.append(
                _normalize_industry_membership(
                    pro_client.index_member_all(
                        l1_code=l1_code,
                        is_new=is_new,
                    ),
                    l1_code=l1_code,
                    is_new=is_new,
                )
            )
            requests.append({"l1_code": l1_code, "is_new": is_new})
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    key = [
        "l1_code",
        "l2_code",
        "l3_code",
        "ts_code",
        "in_date",
        "out_date",
        "is_new",
    ]
    combined = combined.drop_duplicates(key).sort_values(key).reset_index(drop=True)
    _reject_industry_overlaps(combined)
    return _normalize_reloaded_identifiers(combined), requests


def _open_trade_dates(pro_client: object, start: date, end: date) -> list[str]:
    code = "all_cap_source_trade_calendar_schema"
    start_key = start.strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    source = _source_frame(
        pro_client.trade_cal(
            exchange="",
            start_date=start_key,
            end_date=end_key,
            is_open="1",
        ),
        source_name="trade_cal",
    )
    source = _string_columns(
        source,
        ("cal_date", "is_open"),
        error_code=code,
    )
    source = _date_columns(source, ("cal_date",), error_code=code)
    source = source[source["is_open"] == "1"]
    dates = sorted(set(source["cal_date"]))
    if not dates or dates[0] < start_key or dates[-1] > end_key:
        raise ValueError("all_cap_source_trade_calendar_empty")
    return dates


def _normalize_stk_limit(source: object, *, trade_date: str) -> pd.DataFrame:
    code = "all_cap_source_stk_limit_schema"
    frame = _source_frame(source, source_name="stk_limit")
    if frame.empty:
        raise ValueError("all_cap_source_stk_limit_empty")
    frame = _string_columns(
        frame,
        ("ts_code", "trade_date"),
        error_code=code,
    )
    frame = _date_columns(frame, ("trade_date",), error_code=code)
    frame = _numeric_columns(
        frame,
        ("up_limit", "down_limit"),
        error_code=code,
        positive=True,
    )
    if set(frame["trade_date"]) != {trade_date}:
        raise ValueError(code)
    frame = frame.drop_duplicates().reset_index(drop=True)
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("all_cap_source_stk_limit_duplicate")
    return frame


def _collect_stk_limit(
    pro_client: object,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[str]]:
    open_dates = _open_trade_dates(pro_client, start, end)
    pieces = [
        _normalize_stk_limit(
            pro_client.stk_limit(trade_date=trade_date),
            trade_date=trade_date,
        )
        for trade_date in open_dates
    ]
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    combined = combined.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    return _normalize_reloaded_identifiers(combined), open_dates


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_record(
    staging_dir: Path,
    path: Path,
    *,
    dataset: str,
    partition: str,
    frame: pd.DataFrame,
) -> dict[str, object]:
    date_column = "trade_date" if "trade_date" in frame.columns else None
    return {
        "path": path.relative_to(staging_dir).as_posix(),
        "dataset": dataset,
        "partition": partition,
        "rows": int(len(frame)),
        "bytes": int(path.stat().st_size),
        "sha256": _sha256(path),
        "compression": "snappy",
        "min_date": (
            str(frame[date_column].min()) if date_column and not frame.empty else None
        ),
        "max_date": (
            str(frame[date_column].max()) if date_column and not frame.empty else None
        ),
    }


def _write_source_files(
    staging_dir: Path,
    *,
    index_weights: pd.DataFrame,
    index_daily: pd.DataFrame,
    industry_membership: pd.DataFrame,
    stk_limit: pd.DataFrame,
) -> dict[str, list[dict[str, object]]]:
    store = ResearchStore(staging_dir)
    partitions: dict[str, list[dict[str, object]]] = {
        dataset: [] for dataset in _DATASETS
    }
    for dataset, frame in (
        ("index_weights", index_weights),
        ("index_daily", index_daily),
        ("industry_membership", industry_membership),
    ):
        path = store.write_parquet_atomic(
            staging_dir / f"{dataset}.parquet",
            frame,
        )
        partitions[dataset].append(
            _file_record(
                staging_dir,
                path,
                dataset=dataset,
                partition="all",
                frame=frame,
            )
        )
    years = stk_limit["trade_date"].str.slice(0, 4)
    for year in sorted(set(years)):
        frame = stk_limit.loc[years == year].reset_index(drop=True)
        path = store.write_parquet_atomic(
            staging_dir / "stk_limit" / f"year={year}.parquet",
            frame,
        )
        partitions["stk_limit"].append(
            _file_record(
                staging_dir,
                path,
                dataset="stk_limit",
                partition=str(year),
                frame=frame,
            )
        )
    return partitions


def _build_manifest(
    staging_dir: Path,
    *,
    publication_id: str,
    start: date,
    end: date,
    partitions: Mapping[str, list[dict[str, object]]],
    pre_inception: list[dict[str, str]],
    industry_requests: list[dict[str, str]],
    open_dates: list[str],
) -> dict[str, object]:
    files = sorted(
        [dict(item) for records in partitions.values() for item in records],
        key=lambda item: str(item["path"]),
    )
    manifest: dict[str, object] = {
        "schema_version": _SOURCE_SCHEMA_VERSION,
        "contract_version": _SOURCE_CONTRACT_VERSION,
        "status": "complete",
        "publication_id": publication_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "reference_indexes": dict(REFERENCE_INDEXES),
        "industry_contract": "SW2021",
        "industry_requests": industry_requests,
        "pre_inception": pre_inception,
        "open_trade_dates": open_dates,
        "row_counts": {
            dataset: sum(int(item["rows"]) for item in records)
            for dataset, records in partitions.items()
        },
        "partitions": {
            dataset: [dict(item) for item in records]
            for dataset, records in sorted(partitions.items())
        },
        "files": files,
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    write_text_atomic(
        staging_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _read_json(path: Path, *, missing_code: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(missing_code) from exc
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("all_cap_source_manifest_malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("all_cap_source_manifest_malformed")
    return payload


def _safe_relative_path(value: object) -> PurePosixPath:
    path = PurePosixPath(str(value or ""))
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise ValueError("all_cap_source_manifest_path")
    return path


def _manifest_without_hash(manifest: Mapping[str, object]) -> dict[str, object]:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return payload


def _verify_manifest_shape(manifest: Mapping[str, object]) -> None:
    if (
        manifest.get("schema_version") != _SOURCE_SCHEMA_VERSION
        or manifest.get("contract_version") != _SOURCE_CONTRACT_VERSION
        or manifest.get("status") != "complete"
        or dict(manifest.get("reference_indexes") or {}) != REFERENCE_INDEXES
        or manifest.get("industry_contract") != "SW2021"
    ):
        raise ValueError("all_cap_source_manifest_contract")
    publication_id = str(manifest.get("publication_id") or "")
    if not _PUBLICATION_ID.fullmatch(publication_id):
        raise ValueError("all_cap_source_manifest_publication")
    start_key = _date_key(
        manifest.get("start_date"),
        code="all_cap_source_manifest_dates",
    )
    end_key = _date_key(
        manifest.get("end_date"),
        code="all_cap_source_manifest_dates",
    )
    if start_key > end_key:
        raise ValueError("all_cap_source_manifest_dates")
    expected_hash = manifest.get("manifest_sha256")
    if expected_hash != _canonical_hash(_manifest_without_hash(manifest)):
        raise ValueError("all_cap_source_checksum:manifest")


def _read_verified_frames(
    publication_dir: Path,
    manifest: Mapping[str, object],
) -> dict[str, list[pd.DataFrame]]:
    files = manifest.get("files")
    partitions = manifest.get("partitions")
    row_counts = manifest.get("row_counts")
    if (
        not isinstance(files, list)
        or not isinstance(partitions, Mapping)
        or not isinstance(row_counts, Mapping)
        or set(partitions) != _DATASETS
        or set(row_counts) != _DATASETS
    ):
        raise ValueError("all_cap_source_manifest_incomplete")
    by_dataset: dict[str, list[pd.DataFrame]] = {
        dataset: [] for dataset in _DATASETS
    }
    declared_paths: set[str] = set()
    records_by_dataset: dict[str, list[str]] = {
        dataset: [] for dataset in _DATASETS
    }
    for record in files:
        if not isinstance(record, Mapping):
            raise ValueError("all_cap_source_manifest_malformed")
        relative = _safe_relative_path(record.get("path"))
        relative_text = relative.as_posix()
        dataset = str(record.get("dataset") or "")
        if dataset not in _DATASETS or relative_text in declared_paths:
            raise ValueError("all_cap_source_manifest_incomplete")
        declared_paths.add(relative_text)
        records_by_dataset[dataset].append(relative_text)
        path = publication_dir.joinpath(*relative.parts)
        if not path.is_file():
            raise ValueError(f"all_cap_source_manifest_incomplete:{relative_text}")
        if _sha256(path) != record.get("sha256"):
            raise ValueError(f"all_cap_source_checksum:{relative_text}")
        if path.stat().st_size != int(record.get("bytes") or -1):
            raise ValueError(f"all_cap_source_checksum:{relative_text}:bytes")
        try:
            frame = pd.read_parquet(path, dtype_backend="pyarrow")
        except Exception as exc:  # noqa: BLE001 - corrupt parquet fails closed
            raise ValueError(f"all_cap_source_parquet:{relative_text}") from exc
        frame = _normalize_reloaded_identifiers(frame)
        if len(frame) != int(record.get("rows") or -1):
            raise ValueError(f"all_cap_source_manifest_rows:{relative_text}")
        by_dataset[dataset].append(frame)
    actual_paths = {
        path.relative_to(publication_dir).as_posix()
        for path in publication_dir.rglob("*.parquet")
        if path.is_file()
    }
    if actual_paths != declared_paths:
        raise ValueError("all_cap_source_manifest_incomplete:files")
    for dataset in _DATASETS:
        records = partitions.get(dataset)
        if not isinstance(records, list) or not records:
            raise ValueError(f"all_cap_source_manifest_incomplete:{dataset}")
        partition_paths = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("all_cap_source_manifest_malformed")
            partition_paths.append(_safe_relative_path(record.get("path")).as_posix())
        if sorted(partition_paths) != sorted(records_by_dataset[dataset]):
            raise ValueError(f"all_cap_source_manifest_incomplete:{dataset}:partitions")
        observed_rows = sum(len(frame) for frame in by_dataset[dataset])
        if observed_rows != int(row_counts.get(dataset) or -1):
            raise ValueError(f"all_cap_source_manifest_rows:{dataset}")
    return by_dataset


def _combined(
    frames: Mapping[str, list[pd.DataFrame]],
    dataset: str,
) -> pd.DataFrame:
    values = frames[dataset]
    if not values:
        raise ValueError(f"all_cap_source_manifest_incomplete:{dataset}")
    return _normalize_reloaded_identifiers(
        pd.concat(values, ignore_index=True, sort=False)
    )


def _verify_loaded_semantics(
    manifest: Mapping[str, object],
    frames: Mapping[str, list[pd.DataFrame]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start_key = str(manifest["start_date"])
    end_key = str(manifest["end_date"])
    index_daily = _combined(frames, "index_daily")
    index_weights = _combined(frames, "index_weights")
    membership = _combined(frames, "industry_membership")
    stk_limit = _combined(frames, "stk_limit")
    if (
        set(index_daily.get("ts_code", [])) != set(REFERENCE_INDEXES)
        or index_daily.duplicated(["ts_code", "trade_date"]).any()
        or (index_daily["trade_date"] < start_key).any()
        or (index_daily["trade_date"] > end_key).any()
    ):
        raise ValueError("all_cap_source_manifest_index_daily")
    expected_weight_codes = set(_SLEEVE_INDEXES)
    if end_key < CSI2000_INCEPTION.strftime("%Y%m%d"):
        expected_weight_codes.remove("932000.CSI")
    if (
        set(index_weights.get("index_code", [])) != expected_weight_codes
        or index_weights.duplicated(
            ["index_code", "con_code", "trade_date"]
        ).any()
        or (
            (index_weights["index_code"] == "932000.CSI")
            & (index_weights["trade_date"] < "20230901")
        ).any()
    ):
        raise ValueError("all_cap_source_manifest_index_weights")
    membership_key = [
        "l1_code",
        "l2_code",
        "l3_code",
        "ts_code",
        "in_date",
        "out_date",
        "is_new",
    ]
    if (
        set(membership.get("l1_code", [])) != set(_SW2021_L1_CODES)
        or set(membership.get("is_new", [])) != {"Y", "N"}
        or membership.duplicated(membership_key).any()
    ):
        raise ValueError("all_cap_source_manifest_industry")
    _reject_industry_overlaps(membership)
    expected_requests = [
        {"l1_code": industry_code, "is_new": is_new}
        for industry_code in _SW2021_L1_CODES
        for is_new in ("Y", "N")
    ]
    if manifest.get("industry_requests") != expected_requests:
        raise ValueError("all_cap_source_manifest_industry_requests")
    expected_pre_inception = _expected_pre_inception(
        datetime.strptime(start_key, "%Y%m%d").date(),
        datetime.strptime(end_key, "%Y%m%d").date(),
    )
    if manifest.get("pre_inception") != expected_pre_inception:
        raise ValueError("all_cap_source_manifest_pre_inception")
    open_dates = manifest.get("open_trade_dates")
    if (
        not isinstance(open_dates, list)
        or sorted(set(str(value) for value in open_dates)) != open_dates
        or set(stk_limit.get("trade_date", [])) != set(open_dates)
        or stk_limit.duplicated(["ts_code", "trade_date"]).any()
    ):
        raise ValueError("all_cap_source_manifest_stk_limit")
    return index_weights, index_daily, membership, stk_limit


def _read_verified_publication(
    publication_dir: Path,
) -> tuple[dict[str, object], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    manifest = _read_json(
        publication_dir / "manifest.json",
        missing_code="all_cap_source_manifest_missing",
    )
    _verify_manifest_shape(manifest)
    frames = _read_verified_frames(publication_dir, manifest)
    normalized = _verify_loaded_semantics(manifest, frames)
    return manifest, normalized


def publish_all_cap_sources(
    staging_dir: str | Path,
    repo_root: str | Path,
) -> Path:
    """Verify and atomically advance the all-cap source publication marker."""

    source_root = _source_root(repo_root)
    publications_root = source_root / "publications"
    publications_root.mkdir(parents=True, exist_ok=True)
    staging = Path(staging_dir).resolve()
    if staging.parent != publications_root.resolve() or not staging.is_dir():
        raise ValueError("all_cap_source_staging_path")
    manifest, _ = _read_verified_publication(staging)
    publication_id = str(manifest["publication_id"])
    destination = publications_root / publication_id
    if destination.exists():
        raise ValueError("all_cap_source_publication_exists")
    os.replace(staging, destination)
    published_manifest, _ = _read_verified_publication(destination)
    marker: dict[str, object] = {
        "schema_version": _SOURCE_SCHEMA_VERSION,
        "contract_version": _SOURCE_CONTRACT_VERSION,
        "status": "complete",
        "publication": f"publications/{publication_id}",
        "manifest_sha256": published_manifest["manifest_sha256"],
    }
    marker["marker_sha256"] = _canonical_hash(marker)
    write_text_atomic(
        source_root / "latest.json",
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination / "manifest.json"


def load_verified_all_cap_sources(
    repo_root: str | Path,
) -> AllCapSourceManifest:
    """Load only the publication currently named by a valid atomic marker."""

    source_root = _source_root(repo_root)
    marker = _read_json(
        source_root / "latest.json",
        missing_code="all_cap_source_manifest_missing",
    )
    required_marker_fields = {
        "schema_version",
        "contract_version",
        "status",
        "publication",
        "manifest_sha256",
        "marker_sha256",
    }
    if not required_marker_fields.issubset(marker):
        raise ValueError("all_cap_source_manifest_malformed")
    marker_payload = dict(marker)
    marker_hash = marker_payload.pop("marker_sha256", None)
    if marker_hash != _canonical_hash(marker_payload):
        raise ValueError("all_cap_source_checksum:latest")
    if (
        marker.get("schema_version") != _SOURCE_SCHEMA_VERSION
        or marker.get("contract_version") != _SOURCE_CONTRACT_VERSION
        or marker.get("status") != "complete"
    ):
        raise ValueError("all_cap_source_manifest_contract")
    relative = _safe_relative_path(marker.get("publication"))
    if len(relative.parts) != 2 or relative.parts[0] != "publications":
        raise ValueError("all_cap_source_manifest_path")
    publication_dir = source_root.joinpath(*relative.parts)
    manifest, normalized = _read_verified_publication(publication_dir)
    if (
        str(manifest["publication_id"]) != relative.parts[1]
        or marker.get("manifest_sha256") != manifest.get("manifest_sha256")
    ):
        raise ValueError("all_cap_source_checksum:latest_manifest")
    index_weights, index_daily, membership, stk_limit = normalized
    daily_by_code = MappingProxyType(
        {
            code: index_daily.loc[index_daily["ts_code"] == code]
            .reset_index(drop=True)
            .copy()
            for code in REFERENCE_INDEXES
        }
    )
    weights_by_code = MappingProxyType(
        {
            code: index_weights.loc[index_weights["index_code"] == code]
            .reset_index(drop=True)
            .copy()
            for code in _SLEEVE_INDEXES
            if code in set(index_weights["index_code"])
        }
    )
    return AllCapSourceManifest(
        metadata=MappingProxyType(dict(manifest)),
        publication_dir=publication_dir,
        index_daily=daily_by_code,
        index_weights=weights_by_code,
        industry_membership=membership.copy(),
        stk_limit=stk_limit.copy(),
    )


def collect_all_cap_sources(
    *,
    repo_root: Path,
    pro_client: object,
    start: date,
    end: date,
) -> dict[str, object]:
    """Collect and publish bounded all-cap reference data without formal writes."""

    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError("all_cap_source_interval")
    required_methods = (
        "index_weight",
        "index_daily",
        "index_member_all",
        "trade_cal",
        "stk_limit",
    )
    if any(not callable(getattr(pro_client, method, None)) for method in required_methods):
        raise ValueError("all_cap_source_client")
    source_root = _source_root(repo_root)
    publications_root = source_root / "publications"
    publications_root.mkdir(parents=True, exist_ok=True)
    publication_id = (
        f"{start:%Y%m%d}_{end:%Y%m%d}_{uuid.uuid4().hex}"
    )
    staging = Path(
        tempfile.mkdtemp(
            prefix=".all-cap-sources-",
            dir=publications_root,
        )
    )
    try:
        index_weights, pre_inception = _collect_index_weights(
            pro_client,
            start,
            end,
        )
        index_daily = _collect_index_daily(pro_client, start, end)
        membership, industry_requests = _collect_industry_membership(pro_client)
        stk_limit, open_dates = _collect_stk_limit(pro_client, start, end)
        partitions = _write_source_files(
            staging,
            index_weights=index_weights,
            index_daily=index_daily,
            industry_membership=membership,
            stk_limit=stk_limit,
        )
        _build_manifest(
            staging,
            publication_id=publication_id,
            start=start,
            end=end,
            partitions=partitions,
            pre_inception=pre_inception,
            industry_requests=industry_requests,
            open_dates=open_dates,
        )
        manifest_path = publish_all_cap_sources(staging, repo_root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {"status": "complete", "manifest": str(manifest_path)}
