"""Deterministically materialize A-share backtest caches for research.

The backfill cache is endpoint-shaped.  Research needs point-in-time,
instrument-shaped histories plus cumulative source snapshots.  This module is
the fail-closed bridge between those two contracts; it never reaches the
network and writes the completion manifest only after every output is durable.
"""

from __future__ import annotations

import hashlib
import errno
import fcntl
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..utils import write_dataframe_csv_atomic, write_text_atomic


MATERIALIZATION_SCHEMA_VERSION = "a-share-materialization-v1"
SNAPSHOT_SCHEMA_VERSION = 1
INDEXES = ("000300", "000905")
MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE = 0.98
ADJ_FACTOR_WARMUP_SESSIONS = 60
TEXT_COLUMNS = {
    "ann_date",
    "cal_date",
    "code",
    "con_code",
    "delist_date",
    "end_date",
    "index_code",
    "list_date",
    "start_date",
    "trade_date",
    "ts_code",
}

DAILY_COLUMNS = (
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "amount", "volume",
)
DAILY_BASIC_COLUMNS = (
    "ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm",
    "turnover_rate", "total_mv",
)
SUSPEND_COLUMNS = (
    "ts_code", "trade_date", "suspend_timing", "suspend_type",
)

RAW_SCHEMAS = {
    "daily_basic": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("pe_ttm", pa.float64()),
        pa.field("pb", pa.float64()),
        pa.field("dv_ttm", pa.float64()),
        pa.field("turnover_rate", pa.float64()),
        pa.field("total_mv", pa.float64()),
    ]),
    "fina_indicator": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("ann_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("roe", pa.float64()),
        pa.field("grossprofit_margin", pa.float64()),
        pa.field("debt_to_assets", pa.float64()),
        pa.field("netprofit_yoy", pa.float64()),
        pa.field("roic", pa.float64()),
        pa.field("netprofit_margin", pa.float64()),
        pa.field("current_ratio", pa.float64()),
        pa.field("quick_ratio", pa.float64()),
        pa.field("assets_turn", pa.float64()),
        pa.field("q_sales_yoy", pa.float64()),
        pa.field("q_op_qoq", pa.float64()),
        pa.field("ocf_yoy", pa.float64()),
    ]),
    "income": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("ann_date", pa.string()),
        pa.field("f_ann_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("report_type", pa.string()),
        pa.field("update_flag", pa.string()),
        pa.field("revenue", pa.float64()),
        pa.field("operate_profit", pa.float64()),
        pa.field("n_income", pa.float64()),
        pa.field("total_cogs", pa.float64()),
        pa.field("rd_exp", pa.float64()),
    ]),
    "balancesheet": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("ann_date", pa.string()),
        pa.field("f_ann_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("report_type", pa.string()),
        pa.field("update_flag", pa.string()),
        pa.field("total_assets", pa.float64()),
    ]),
    "cashflow": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("ann_date", pa.string()),
        pa.field("f_ann_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("report_type", pa.string()),
        pa.field("update_flag", pa.string()),
        pa.field("n_cashflow_act", pa.float64()),
        pa.field("free_cashflow", pa.float64()),
    ]),
    "stock_basic": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("name", pa.string()),
        pa.field("area", pa.string()),
        pa.field("industry", pa.string()),
        pa.field("list_date", pa.string()),
        pa.field("delist_date", pa.string()),
        pa.field("list_status", pa.string()),
    ]),
    "security_status": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("security_status", pa.string()),
        pa.field("is_st", pa.int64()),
        pa.field("is_suspended", pa.int64()),
        pa.field("is_tradable", pa.int64()),
        pa.field("status_conflict", pa.int64()),
        pa.field("baostock_tradestatus", pa.string()),
        pa.field("st_source", pa.string()),
        pa.field("tushare_suspend_event", pa.int64()),
        pa.field("tushare_full_day_suspend_event", pa.int64()),
        pa.field("partial_suspension_event", pa.int64()),
        pa.field("tushare_suspend_timing", pa.string()),
        pa.field("tushare_suspend_type", pa.string()),
        pa.field("tushare_resume_event", pa.int64()),
        pa.field("tushare_resume_timing", pa.string()),
        pa.field("tushare_resume_type", pa.string()),
        pa.field("tushare_other_status_event", pa.int64()),
        pa.field("tushare_event_types", pa.string()),
        pa.field("suspension_status_source", pa.string()),
    ]),
    "suspend_d": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("suspend_timing", pa.string()),
        pa.field("suspend_type", pa.string()),
    ]),
    "baostock_status": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("tradestatus", pa.string()),
        pa.field("is_st", pa.string()),
        pa.field("st_source", pa.string()),
        pa.field("code", pa.string()),
    ]),
    "adj_factor": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("adj_factor", pa.float64()),
    ]),
    "namechange": pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("name", pa.string()),
        pa.field("start_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("ann_date", pa.string()),
        pa.field("change_reason", pa.string()),
    ]),
    "index_weight": pa.schema([
        pa.field("index_code", pa.string()),
        pa.field("con_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("weight", pa.float64()),
    ]),
}


def _benchmark_schema() -> pa.Schema:
    return pa.schema([
        pa.field("ts_code", pa.string()),
        pa.field("trade_date", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("amount", pa.float64()),
    ])


class _IncrementalParquetWriter:
    def __init__(self, path: Path, schema: pa.Schema) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.schema = schema
        self.rows = 0
        self.min_date: str | None = None
        self.max_date: str | None = None
        self._closed = False
        self._writer = pq.ParquetWriter(path, schema, compression="snappy")

    def write(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        normalized = frame.reindex(columns=self.schema.names).copy()
        for field in self.schema:
            if pa.types.is_string(field.type):
                normalized[field.name] = normalized[field.name].astype("string")
            elif pa.types.is_floating(field.type):
                normalized[field.name] = pd.to_numeric(
                    normalized[field.name], errors="coerce"
                )
            elif pa.types.is_integer(field.type):
                normalized[field.name] = pd.to_numeric(
                    normalized[field.name], errors="coerce"
                ).astype("Int64")
        table = pa.Table.from_pandas(
            normalized,
            schema=self.schema,
            preserve_index=False,
            safe=False,
        )
        self._writer.write_table(table)
        self.rows += len(normalized)
        date_info = _date_range(normalized)
        current_min = date_info["min_date"]
        current_max = date_info["max_date"]
        if current_min is not None:
            self.min_date = min(self.min_date, current_min) if self.min_date else current_min
        if current_max is not None:
            self.max_date = max(self.max_date, current_max) if self.max_date else current_max

    def close(self) -> None:
        if not self._closed:
            self._writer.close()
            self._closed = True

    def output_record(self, repo_root: Path, published_path: Path) -> dict[str, object]:
        return {
            "path": published_path.relative_to(repo_root).as_posix(),
            "rows": self.rows,
            "min_date": self.min_date,
            "max_date": self.max_date,
            "sha256": _sha256(self.path),
        }


class _MaterializationLease:
    def __init__(self, lock_path: Path, run_key: str) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = lock_path
        self.run_key = run_key
        self.owner = f"{os.uname().nodename}:{os.getpid()}"
        self.generation = uuid.uuid4().hex
        self._handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(
                self._handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except OSError as exc:
            self._handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ValueError(
                    f"a_share_materialization_locked:{run_key}"
                ) from exc
            raise
        self._released = False

    def marker_text(self) -> str:
        return json.dumps(
            {
                "as_of": self.run_key,
                "generation": self.generation,
                "owner": self.owner,
                "owner_pid": os.getpid(),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"

    def release(self) -> None:
        if self._released:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._released = True


def _date_key(value: object) -> str:
    text = str(value or "").strip().replace("-", "")
    if text.endswith(".0"):
        text = text[:-2]
    return text[:8]


def _as_of_key(value: str) -> str:
    raw = str(value).strip()
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", raw)
    dashed = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    match = compact or dashed
    if match is None:
        raise ValueError(f"materialization_as_of_invalid:{value}")
    try:
        parsed = date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise ValueError(f"materialization_as_of_invalid:{value}") from exc
    return parsed.strftime("%Y%m%d")


def _months(start: date, end: date) -> list[str]:
    current = date(start.year, start.month, 1)
    result: list[str] = []
    while current <= end:
        result.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(f"materialization_source_invalid:{path}") from exc
    for column in TEXT_COLUMNS.intersection(frame.columns):
        frame[column] = frame[column].map(_date_key) if column.endswith("date") else frame[column].astype("string")
    return frame


def _require_csv(
    path: Path,
    required: set[str],
    sources: dict[str, Path],
    *,
    cache_root: Path,
    alternatives: tuple[set[str], ...] = (),
) -> None:
    relative = path.relative_to(cache_root).as_posix()
    if not path.is_file():
        raise ValueError(f"materialization_source_missing:{relative}")
    try:
        columns = set(pd.read_csv(path, nrows=0).columns)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(f"materialization_source_invalid:{relative}") from exc
    if not required.issubset(columns) or any(not option.intersection(columns) for option in alternatives):
        missing = sorted(required - columns)
        raise ValueError(
            f"materialization_schema_invalid:{relative}:missing={missing}:columns={sorted(columns)}"
        )
    sources[relative] = path


def _require_partition_identity(
    frame: pd.DataFrame,
    *,
    relative: str,
    column: str,
    expected: str,
) -> None:
    if frame.empty:
        return
    values = set(frame[column].astype(str))
    if values != {expected}:
        raise ValueError(
            f"materialization_source_invalid:{relative}:{column}={sorted(values)}"
        )


def _valid_source_date(value: str) -> bool:
    if not re.fullmatch(r"\d{8}", value):
        return False
    try:
        date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return False
    return True


def _validate_sources(
    cache_root: Path,
    *,
    start: date,
    end: date,
) -> tuple[list[str], list[str], dict[str, Path], dict[str, pd.DataFrame]]:
    sources: dict[str, Path] = {}
    _require_csv(
        cache_root / "trade_cal.csv",
        {"cal_date", "is_open"},
        sources,
        cache_root=cache_root,
    )
    _require_csv(
        cache_root / "stock_basic.csv",
        {"ts_code", "name", "list_date", "delist_date", "list_status"},
        sources,
        cache_root=cache_root,
    )
    calendar = _read_csv(cache_root / "trade_cal.csv")
    start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    is_open = pd.to_numeric(calendar["is_open"], errors="coerce").eq(1)
    open_dates = sorted(
        set(
            calendar.loc[
                is_open & calendar["cal_date"].between(start_key, end_key),
                "cal_date",
            ].astype(str)
        )
    )
    if not open_dates:
        raise ValueError("materialization_source_invalid:trade_cal:no_open_dates")

    for raw_date in open_dates:
        iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        _require_csv(
            cache_root / "daily" / f"{iso}.csv",
            {"ts_code", "trade_date", "open", "high", "low", "close", "amount"},
            sources,
            cache_root=cache_root,
            alternatives=({"vol", "volume"},),
        )
        _require_csv(
            cache_root / "daily_basic" / f"{iso}.csv",
            {
                "ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm",
                "turnover_rate", "total_mv",
            },
            sources,
            cache_root=cache_root,
        )
        _require_csv(
            cache_root / "suspend_d" / f"{iso}.csv",
            {"ts_code", "trade_date", "suspend_timing", "suspend_type"},
            sources,
            cache_root=cache_root,
        )

    index_frames: dict[str, pd.DataFrame] = {}
    union_ts_codes: set[str] = set()
    for month in _months(start, end):
        for index in INDEXES:
            path = cache_root / "index_weight" / f"{index}_{month}.csv"
            _require_csv(
                path,
                {"index_code", "con_code", "trade_date"},
                sources,
                cache_root=cache_root,
            )
            frame = _read_csv(path)
            if frame.empty:
                raise ValueError(f"materialization_source_invalid:{path.relative_to(cache_root)}:empty")
            relative = path.relative_to(cache_root).as_posix()
            _require_partition_identity(
                frame,
                relative=relative,
                column="index_code",
                expected=f"{index}.SH",
            )
            members = frame["con_code"].astype(str)
            invalid_members = sorted(
                {
                    code
                    for code in members
                    if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", code)
                }
            )
            if invalid_members:
                raise ValueError(
                    f"materialization_source_invalid:{relative}:con_code={invalid_members}"
                )
            year, month_number = (int(part) for part in month.split("-"))
            month_end = date(
                year,
                month_number,
                monthrange(year, month_number)[1],
            ).strftime("%Y%m%d")
            cutoff = min(month_end, end_key)
            invalid_dates = sorted(
                {
                    raw_date
                    for raw_date in frame["trade_date"].astype(str)
                    if not _valid_source_date(raw_date) or raw_date > cutoff
                }
            )
            if invalid_dates:
                raise ValueError(
                    f"materialization_source_invalid:{relative}:trade_date={invalid_dates}:cutoff={cutoff}"
                )
            frame = frame.loc[
                ~members.str.startswith(("200", "900"), na=False)
            ].copy()
            index_frames[path.name] = frame
            union_ts_codes.update(frame["con_code"].astype(str))

    if not union_ts_codes:
        raise ValueError("materialization_source_invalid:index_weight:no_a_share_members")

    master = _read_csv(cache_root / "stock_basic.csv")
    master_codes = set(master["ts_code"].astype(str))
    missing_master = sorted(union_ts_codes - master_codes)
    if missing_master:
        raise ValueError(f"materialization_source_invalid:stock_basic:missing={missing_master}")

    per_code_schemas = {
        "fina_indicator": {
            "ts_code", "ann_date", "end_date", "roe",
            "grossprofit_margin", "debt_to_assets", "netprofit_yoy",
        },
        "adj_factor": {"ts_code", "trade_date", "adj_factor"},
        "namechange": {"ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"},
        "baostock_status": {"ts_code", "trade_date", "tradestatus", "is_st", "st_source"},
    }
    for code in sorted(union_ts_codes):
        for endpoint, schema in per_code_schemas.items():
            path = cache_root / endpoint / f"{code}.csv"
            _require_csv(
                path,
                schema,
                sources,
                cache_root=cache_root,
            )
            _require_partition_identity(
                _read_csv(path),
                relative=path.relative_to(cache_root).as_posix(),
                column="ts_code",
                expected=code,
            )

    optional_financial_schemas = {
        "income": {"ts_code", "ann_date", "end_date", "revenue", "n_income"},
        "balancesheet": {"ts_code", "ann_date", "end_date", "total_assets"},
        "cashflow": {"ts_code", "ann_date", "end_date", "n_cashflow_act"},
    }
    for endpoint, schema in optional_financial_schemas.items():
        paths = [cache_root / endpoint / f"{code}.csv" for code in sorted(union_ts_codes)]
        present = [path for path in paths if path.exists()]
        if present and len(present) != len(paths):
            raise ValueError(
                f"materialization_source_incomplete:{endpoint}:"
                f"files={len(present)}/{len(paths)}"
            )
        for code, path in zip(sorted(union_ts_codes), paths):
            if not path.exists():
                continue
            _require_csv(path, schema, sources, cache_root=cache_root)
            _require_partition_identity(
                _read_csv(path),
                relative=path.relative_to(cache_root).as_posix(),
                column="ts_code",
                expected=code,
            )

    for index in INDEXES:
        _require_csv(
            cache_root / "benchmark_daily" / f"{index}.csv",
            {"ts_code", "trade_date", "open", "high", "low", "close"},
            sources,
            cache_root=cache_root,
        )
        benchmark = _read_csv(cache_root / "benchmark_daily" / f"{index}.csv")
        _require_partition_identity(
            benchmark,
            relative=f"benchmark_daily/{index}.csv",
            column="ts_code",
            expected=f"{index}.SH",
        )
        available_dates = set(
            benchmark.loc[benchmark["trade_date"].between(start_key, end_key), "trade_date"]
        )
        missing_dates = sorted(set(open_dates) - available_dates)
        if missing_dates:
            raise ValueError(
                f"materialization_source_invalid:benchmark_daily/{index}.csv:missing_dates={missing_dates}"
            )

    return open_dates, sorted(union_ts_codes), sources, index_frames


def _read_partition(
    cache_root: Path,
    endpoint: str,
    raw_date: str,
) -> pd.DataFrame:
    iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    path = cache_root / endpoint / f"{iso}.csv"
    if endpoint == "daily":
        source_columns = set(pd.read_csv(path, nrows=0).columns)
        volume_source = "volume" if "volume" in source_columns else "vol"
        usecols = [*DAILY_COLUMNS[:-1], volume_source]
        dtypes: dict[str, object] = {
            "ts_code": "string",
            "trade_date": "string",
            **{
                column: "float64"
                for column in usecols
                if column not in {"ts_code", "trade_date"}
            },
        }
    elif endpoint == "daily_basic":
        usecols = list(DAILY_BASIC_COLUMNS)
        dtypes = {
            "ts_code": "string",
            "trade_date": "string",
            **{
                column: "float64"
                for column in usecols
                if column not in {"ts_code", "trade_date"}
            },
        }
    elif endpoint == "suspend_d":
        usecols = list(SUSPEND_COLUMNS)
        dtypes = {column: "string" for column in usecols}
    else:
        raise ValueError(f"materialization_endpoint_unsupported:{endpoint}")
    try:
        frame = pd.read_csv(
            path,
            usecols=usecols,
            dtype=dtypes,
            keep_default_na=endpoint != "suspend_d",
        )
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, ValueError) as exc:
        raise ValueError(
            f"materialization_source_invalid:{endpoint}/{iso}.csv"
        ) from exc
    if endpoint == "daily" and volume_source == "vol":
        frame = frame.rename(columns={"vol": "volume"})
    frame["trade_date"] = frame["trade_date"].map(_date_key).astype("string")
    relative = f"{endpoint}/{iso}.csv"
    if frame.empty and endpoint in {"daily", "daily_basic"}:
        raise ValueError(f"materialization_source_invalid:{relative}:empty")
    _require_partition_identity(
        frame,
        relative=relative,
        column="trade_date",
        expected=raw_date,
    )
    return frame


def _create_partition_store(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE daily ("
        "ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL, "
        "low REAL, close REAL, pre_close REAL, amount REAL, volume REAL)"
    )
    connection.execute(
        "CREATE TABLE daily_basic ("
        "ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, pe_ttm REAL, pb REAL, "
        "dv_ttm REAL, turnover_rate REAL, total_mv REAL)"
    )
    connection.execute(
        "CREATE TABLE suspend_d ("
        "ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, "
        "suspend_timing TEXT, suspend_type TEXT)"
    )
    return connection


def _stream_market_partitions(
    *,
    cache_root: Path,
    open_dates: Iterable[str],
    union_ts_codes: Iterable[str],
    connection: sqlite3.Connection,
    daily_basic_writer: _IncrementalParquetWriter,
    suspend_writer: _IncrementalParquetWriter,
) -> dict[str, dict[str, object]]:
    code_scope = set(union_ts_codes)
    coverage = {
        name: {"files": 0, "rows": 0, "min_date": None, "max_date": None}
        for name in ("daily", "daily_basic", "suspend_d")
    }
    for raw_date in open_dates:
        for endpoint in ("daily", "daily_basic", "suspend_d"):
            frame = _read_partition(cache_root, endpoint, raw_date)
            frame = frame.loc[frame["ts_code"].isin(code_scope)].copy()
            frame = _sort_frame(frame, ("ts_code", "trade_date", "suspend_timing"))
            frame.to_sql(endpoint, connection, if_exists="append", index=False)
            if endpoint == "daily_basic":
                daily_basic_writer.write(frame)
            elif endpoint == "suspend_d":
                suspend_writer.write(frame)
            item = coverage[endpoint]
            item["files"] = int(item["files"]) + 1
            item["rows"] = int(item["rows"]) + len(frame)
            if not frame.empty:
                item["min_date"] = min(str(item["min_date"] or raw_date), raw_date)
                item["max_date"] = max(str(item["max_date"] or raw_date), raw_date)
        connection.commit()
    for table in ("daily", "daily_basic", "suspend_d"):
        connection.execute(
            f"CREATE INDEX idx_{table}_code_date ON {table} (ts_code, trade_date)"
        )
    connection.commit()
    return coverage


def _read_code_partition(
    connection: sqlite3.Connection,
    table: str,
    code: str,
) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT * FROM {table} WHERE ts_code = ? ORDER BY trade_date",
        connection,
        params=(code,),
    )


def _sort_frame(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    order = [column for column in columns if column in frame.columns]
    result = frame.copy()
    for column in TEXT_COLUMNS.intersection(result.columns):
        result[column] = result[column].astype("string")
    if order:
        result = result.sort_values(order, kind="stable")
    return result.reset_index(drop=True)


def _date_range(frame: pd.DataFrame) -> dict[str, str | None]:
    for column in ("trade_date", "ann_date", "cal_date", "start_date"):
        if column in frame.columns and not frame.empty:
            values = frame[column].astype("string").replace("", pd.NA).dropna()
            if not values.empty:
                return {"min_date": str(values.min()), "max_date": str(values.max())}
    return {"min_date": None, "max_date": None}


def _point_in_time_names(
    dates: pd.Series,
    changes: pd.DataFrame,
) -> tuple[list[object], list[str]]:
    if changes.empty:
        return [pd.NA] * len(dates), ["unavailable_point_in_time"] * len(dates)
    records = changes.sort_values(["start_date", "ann_date"], kind="stable").to_dict("records")
    names: list[object] = []
    sources: list[str] = []
    for raw_date in dates.astype(str):
        visible = [
            record
            for record in records
            if _date_key(record.get("start_date")) <= raw_date
            and (not _date_key(record.get("end_date")) or _date_key(record.get("end_date")) >= raw_date)
            and (not _date_key(record.get("ann_date")) or _date_key(record.get("ann_date")) <= raw_date)
        ]
        if visible:
            names.append(str(visible[-1].get("name") or "") or pd.NA)
            sources.append("tushare_namechange_point_in_time")
        else:
            names.append(pd.NA)
            sources.append("unavailable_point_in_time")
    return names, sources


def _aggregate_suspend_events(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ts_code", "trade_date", "tushare_suspend_event",
        "tushare_full_day_suspend_event", "partial_suspension_event",
        "tushare_suspend_timing", "tushare_suspend_type",
        "tushare_resume_event", "tushare_resume_timing", "tushare_resume_type",
        "tushare_other_status_event", "tushare_event_types",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    source = frame.copy()
    source["suspend_timing"] = source["suspend_timing"].astype(str)
    source["suspend_type"] = source["suspend_type"].astype(str)

    def classify(raw_type: str) -> str:
        value = str(raw_type).strip()
        upper = value.upper()
        if upper == "R" or "复牌" in value:
            return "resume"
        if upper == "S" or ("停牌" in value and "复牌" not in value):
            return "suspend"
        return "other"

    def is_full_day(raw_timing: str) -> bool:
        value = str(raw_timing).replace(" ", "")
        return "全天" in value or value in {"09:30-15:00", "9:30-15:00"}

    source["event_class"] = source["suspend_type"].map(classify)
    rows: list[dict[str, object]] = []
    for (code, raw_date), group in source.groupby(
        ["ts_code", "trade_date"], sort=True, dropna=False
    ):
        suspended = group.loc[group["event_class"].eq("suspend")]
        resumed = group.loc[group["event_class"].eq("resume")]
        other = group.loc[group["event_class"].eq("other")]
        full_day = bool(
            not suspended.empty
            and suspended["suspend_timing"].map(is_full_day).any()
        )
        rows.append({
            "ts_code": code,
            "trade_date": raw_date,
            "tushare_suspend_event": int(not suspended.empty),
            "tushare_full_day_suspend_event": int(full_day),
            "partial_suspension_event": int(not suspended.empty and not full_day),
            "tushare_suspend_timing": "|".join(sorted(set(suspended["suspend_timing"]) - {""})),
            "tushare_suspend_type": "|".join(sorted(set(suspended["suspend_type"]) - {""})),
            "tushare_resume_event": int(not resumed.empty),
            "tushare_resume_timing": "|".join(sorted(set(resumed["suspend_timing"]) - {""})),
            "tushare_resume_type": "|".join(sorted(set(resumed["suspend_type"]) - {""})),
            "tushare_other_status_event": int(not other.empty),
            "tushare_event_types": "|".join(sorted(set(group["suspend_type"]) - {""})),
        })
    return pd.DataFrame(rows, columns=columns)


def _build_history(
    *,
    code: str,
    open_dates: list[str],
    master_row: pd.Series,
    daily: pd.DataFrame,
    daily_basic: pd.DataFrame,
    suspend_events: pd.DataFrame,
    baostock: pd.DataFrame,
    names: pd.DataFrame,
    adjustments: pd.DataFrame,
) -> pd.DataFrame:
    list_date = _date_key(master_row.get("list_date"))
    delist_date = _date_key(master_row.get("delist_date"))
    active_dates = [
        raw_date
        for raw_date in open_dates
        if (not list_date or raw_date >= list_date)
        and (not delist_date or raw_date <= delist_date)
    ]
    history = pd.DataFrame({"trade_date": active_dates})
    history["ts_code"] = code
    history["code"] = code[:6]

    quote = daily.copy()
    quote = quote.drop_duplicates(["ts_code", "trade_date"], keep="last")
    quote = quote.drop(columns=["ts_code"], errors="ignore")
    history = history.merge(quote, on="trade_date", how="left", sort=False)
    if "volume" not in history.columns and "vol" in history.columns:
        history["volume"] = history["vol"]
    history = history.drop(columns="vol", errors="ignore")
    # Tushare daily.amount is in thousand yuan. Research liquidity filters and
    # execution-cost calculations use yuan, so preserve both representations.
    amount_thousand_yuan = pd.to_numeric(history.get("amount"), errors="coerce")
    history["amount_thousand_yuan"] = amount_thousand_yuan
    history["amount_yuan"] = amount_thousand_yuan * 1_000.0
    history["amount"] = history["amount_yuan"]
    history["amount_unit"] = "yuan"

    valuation = daily_basic.copy()
    valuation = valuation.drop_duplicates(["ts_code", "trade_date"], keep="last")
    valuation = valuation[[column for column in ("trade_date", "turnover_rate") if column in valuation.columns]]
    if "turnover_rate" in valuation.columns:
        history = history.merge(valuation, on="trade_date", how="left", sort=False)

    adj = adjustments.copy()
    adj = adj.drop_duplicates(["ts_code", "trade_date"], keep="last")
    adj = adj[[column for column in ("trade_date", "adj_factor") if column in adj.columns]]
    history = history.merge(adj, on="trade_date", how="left", sort=False)

    status = baostock.copy()
    status = status.drop_duplicates(["ts_code", "trade_date"], keep="last")
    status = status.rename(columns={"tradestatus": "baostock_tradestatus"})
    status_columns = [
        column
        for column in ("trade_date", "baostock_tradestatus", "is_st", "st_source")
        if column in status.columns
    ]
    history = history.merge(status[status_columns], on="trade_date", how="left", sort=False)

    events = suspend_events.copy()
    events = events.drop(columns=["ts_code"], errors="ignore")
    history = history.merge(events, on="trade_date", how="left", sort=False)
    history["tushare_suspend_event"] = pd.to_numeric(
        history.get("tushare_suspend_event"), errors="coerce"
    ).fillna(0).astype("Int64")
    for event_column in (
        "tushare_full_day_suspend_event", "partial_suspension_event",
        "tushare_resume_event", "tushare_other_status_event",
    ):
        history[event_column] = pd.to_numeric(
            history.get(event_column), errors="coerce"
        ).fillna(0).astype("Int64")

    point_names, name_sources = _point_in_time_names(
        history["trade_date"], names
    )
    history["name"] = point_names
    history["name_source"] = name_sources
    history["list_date"] = list_date
    history["delist_date"] = [
        delist_date if delist_date and raw_date >= delist_date else pd.NA
        for raw_date in history["trade_date"].astype(str)
    ]
    history["industry"] = str(master_row.get("industry") or "")
    history["list_status_source"] = "tushare_stock_basic"

    trade_status = history.get(
        "baostock_tradestatus", pd.Series(pd.NA, index=history.index)
    ).astype("string")
    event = history["tushare_suspend_event"].eq(1)
    full_day_event = history["tushare_full_day_suspend_event"].eq(1)
    partial_event = history["partial_suspension_event"].eq(1)
    quote_available = history[["open", "high", "low", "close"]].apply(
        pd.to_numeric, errors="coerce"
    ).notna().all(axis=1)
    baostock_trading = trade_status.eq("1").fillna(False)
    baostock_suspended = trade_status.eq("0").fillna(False)
    conflict = (
        (full_day_event & baostock_trading)
        | (baostock_suspended & quote_available)
        | (baostock_trading & ~quote_available & ~event)
    ).fillna(False)
    full_day_suspension = baostock_suspended
    unknown = trade_status.isna()
    history["status_conflict"] = conflict.astype("Int64")
    history["is_suspended"] = (
        full_day_suspension.astype("Int64").mask(unknown, pd.NA)
    )
    history["is_st"] = pd.to_numeric(history.get("is_st"), errors="coerce").astype("Int64")
    history["is_tradable"] = (
        baostock_trading & ~event & quote_available & ~conflict & ~unknown
    ).astype("Int64")
    history["suspension_status_source"] = [
        "baostock+tushare" if has_event and pd.notna(status_value)
        else "tushare" if has_event
        else "baostock" if pd.notna(status_value)
        else "unknown"
        for has_event, status_value in zip(event, trade_status)
    ]

    statuses: list[str] = []
    for index, raw_date in enumerate(history["trade_date"].astype(str)):
        if delist_date and raw_date >= delist_date:
            statuses.append("delisted")
        elif bool(conflict.iloc[index]):
            statuses.append("status_conflict")
        elif bool(full_day_suspension.iloc[index]):
            statuses.append("suspended")
        elif bool(partial_event.iloc[index]):
            statuses.append("partial_suspension_event")
        elif bool(event.iloc[index]):
            statuses.append("suspension_event_unconfirmed")
        elif bool(unknown.iloc[index]):
            statuses.append("status_unknown")
        elif not bool(quote_available.iloc[index]):
            statuses.append("quote_missing")
        elif bool(history.iloc[index]["is_tradable"]):
            statuses.append("trading")
        else:
            statuses.append("status_unknown")
    history["security_status"] = statuses
    return _sort_frame(history, ("trade_date", "code"))


def _staged_output_record(
    staging_path: Path,
    published_path: Path,
    frame: pd.DataFrame,
    repo_root: Path,
) -> dict[str, object]:
    return {
        "path": published_path.relative_to(repo_root).as_posix(),
        "rows": int(len(frame)),
        **_date_range(frame),
        "sha256": _sha256(staging_path),
    }


def _source_hashes(source_paths: dict[str, Path]) -> dict[str, str]:
    return {
        relative: _sha256(path)
        for relative, path in sorted(source_paths.items())
    }


def _assert_sources_unchanged(
    source_paths: dict[str, Path],
    expected: dict[str, str],
) -> None:
    changed: list[str] = []
    for relative, expected_hash in sorted(expected.items()):
        try:
            current_hash = _sha256(source_paths[relative])
        except (KeyError, OSError):
            changed.append(relative)
            continue
        if current_hash != expected_hash:
            changed.append(relative)
    if changed:
        raise ValueError(f"materialization_source_changed:{','.join(changed)}")


def _unlink_owned_marker(path: Path, generation: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(payload, dict) and payload.get("generation") == generation:
        path.unlink(missing_ok=True)


def _publish_staged_outputs(
    *,
    staging_root: Path,
    staging_raw: Path,
    staging_history: Path,
    raw_root: Path,
    history_root: Path,
    run_key: str,
    parent_marker: Path,
    generation: str,
) -> None:
    backup_raw = staging_root / "previous_raw"
    backup_history = staging_root / "previous_history"
    backup_history.mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)
    raw_backed_up = False
    raw_published = False
    moved_histories: list[Path] = []
    try:
        for existing in sorted(history_root.glob(f"history_*_{run_key}_*.csv")):
            os.replace(existing, backup_history / existing.name)
        for staged in sorted(staging_history.glob("history_*.csv")):
            destination = history_root / staged.name
            os.replace(staged, destination)
            moved_histories.append(destination)
        if raw_root.exists():
            os.replace(raw_root, backup_raw)
            raw_backed_up = True
        os.replace(staging_raw, raw_root)
        raw_published = True
        _unlink_owned_marker(
            raw_root / ".materialization_in_progress", generation
        )
        _unlink_owned_marker(parent_marker, generation)
    except Exception:
        if raw_published and raw_root.exists():
            shutil.rmtree(raw_root)
        if raw_backed_up and backup_raw.exists():
            os.replace(backup_raw, raw_root)
        for path in moved_histories:
            path.unlink(missing_ok=True)
        for previous in sorted(backup_history.glob("history_*.csv")):
            os.replace(previous, history_root / previous.name)
        _unlink_owned_marker(
            raw_root / ".materialization_in_progress", generation
        )
        _unlink_owned_marker(parent_marker, generation)
        raise
    if backup_raw.exists():
        shutil.rmtree(backup_raw)
    shutil.rmtree(backup_history, ignore_errors=True)


def materialize_a_share_research_data(
    *,
    repo_root: Path,
    cache_root: Path,
    start: date,
    end: date,
    as_of: str,
) -> dict[str, object]:
    """Materialize validated A-share endpoint caches into research inputs."""
    repo_root = Path(repo_root).absolute()
    cache_root = Path(cache_root).absolute()
    if end < start:
        raise ValueError("materialization_date_range_invalid:end_before_start")
    run_key = _as_of_key(as_of)
    if run_key < end.strftime("%Y%m%d"):
        raise ValueError("materialization_as_of_invalid:before_end")

    research_root = repo_root / "data" / "research"
    market_root = research_root / "raw" / "a_share"
    raw_root = market_root / run_key
    history_root = repo_root / "data" / "shared" / "cache"
    run_marker = raw_root / ".materialization_in_progress"
    parent_marker = market_root / f".materialization_in_progress.{run_key}"
    market_root.mkdir(parents=True, exist_ok=True)
    lease = _MaterializationLease(
        market_root / ".materialization_locks" / f"{run_key}.lock",
        run_key,
    )
    staging_root: Path | None = None
    writers: dict[str, _IncrementalParquetWriter] = {}
    connection: sqlite3.Connection | None = None
    published = False
    try:
        marker_text = lease.marker_text()
        raw_root.mkdir(parents=True, exist_ok=True)
        write_text_atomic(run_marker, marker_text)
        write_text_atomic(parent_marker, marker_text)
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".materialization-staging-{run_key}-",
                dir=research_root,
            )
        )
        staging_raw = staging_root / "raw"
        staging_history = staging_root / "history"
        staging_raw.mkdir(parents=True)
        staging_history.mkdir(parents=True)
        write_text_atomic(
            staging_raw / ".materialization_in_progress",
            marker_text,
        )
        open_dates, union_ts_codes, source_paths, index_frames = _validate_sources(
            cache_root, start=start, end=end
        )
        initial_source_hashes = _source_hashes(source_paths)
        source_path_list = sorted(source_paths)
        union_codes = sorted(code[:6] for code in union_ts_codes)
        start_key, end_key = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        membership_months_by_code: dict[str, set[str]] = {
            code: set() for code in union_ts_codes
        }
        for filename, frame in index_frames.items():
            match = re.search(r"_(\d{4})-(\d{2})\.csv$", filename)
            if match is None:
                raise ValueError(
                    f"materialization_source_invalid:index_weight/{filename}:filename"
                )
            month_key = "".join(match.groups())
            for code in frame["con_code"].astype(str).unique():
                membership_months_by_code.setdefault(code, set()).add(month_key)

        for name, schema in RAW_SCHEMAS.items():
            writers[name] = _IncrementalParquetWriter(
                staging_raw / f"{name}.parquet", schema
            )
        for index in INDEXES:
            writers[f"benchmark_{index}"] = _IncrementalParquetWriter(
                staging_raw / f"benchmark_{index}.parquet", _benchmark_schema()
            )

        connection = _create_partition_store(staging_root / "partitions.sqlite3")
        partition_coverage = _stream_market_partitions(
            cache_root=cache_root,
            open_dates=open_dates,
            union_ts_codes=union_ts_codes,
            connection=connection,
            daily_basic_writer=writers["daily_basic"],
            suspend_writer=writers["suspend_d"],
        )

        stock_basic = _read_csv(cache_root / "stock_basic.csv")
        stock_basic = _sort_frame(
            stock_basic.loc[stock_basic["ts_code"].isin(union_ts_codes)],
            ("ts_code",),
        )
        writers["stock_basic"].write(stock_basic)
        master_by_code = {
            str(row["ts_code"]): row
            for _, row in stock_basic.iterrows()
        }

        index_rows = 0
        index_min: str | None = None
        index_max: str | None = None
        for filename in sorted(index_frames):
            frame = _sort_frame(
                index_frames[filename],
                ("trade_date", "index_code", "con_code"),
            )
            writers["index_weight"].write(frame)
            index_rows += len(frame)
            dates = _date_range(frame)
            if dates["min_date"] is not None:
                index_min = min(index_min, dates["min_date"]) if index_min else dates["min_date"]
                index_max = max(index_max, dates["max_date"]) if index_max else dates["max_date"]

        benchmark_rows = 0
        for index in INDEXES:
            benchmark = _read_csv(cache_root / "benchmark_daily" / f"{index}.csv")
            benchmark = _sort_frame(
                benchmark.loc[benchmark["trade_date"].between(start_key, end_key)],
                ("trade_date", "ts_code"),
            )
            if "volume" not in benchmark.columns and "vol" in benchmark.columns:
                benchmark["volume"] = benchmark["vol"]
            writers[f"benchmark_{index}"].write(benchmark)
            benchmark_rows += len(benchmark)

        outputs: dict[str, dict[str, object]] = {}
        history_rows = 0
        adjustment_active_expected_rows = 0
        adjustment_active_available_rows = 0
        adjustment_membership_expected_rows = 0
        adjustment_membership_available_rows = 0
        adjustment_warmup_expected_rows = 0
        adjustment_warmup_available_rows = 0
        adjustment_warmup_failures: list[tuple[str, float, int, int]] = []
        endpoint_rows = {
            "fina_indicator": 0,
            "income": 0,
            "balancesheet": 0,
            "cashflow": 0,
            "adj_factor": 0,
            "namechange": 0,
            "baostock_status": 0,
        }
        endpoint_files = {name: 0 for name in endpoint_rows}
        endpoint_ranges: dict[str, dict[str, str | None]] = {
            name: {"min_date": None, "max_date": None}
            for name in endpoint_rows
        }
        status_columns = RAW_SCHEMAS["security_status"].names
        for ts_code in union_ts_codes:
            fina_indicator = _read_csv(
                cache_root / "fina_indicator" / f"{ts_code}.csv"
            )
            fina_indicator = _sort_frame(
                fina_indicator.loc[fina_indicator["ann_date"].le(end_key)],
                ("ts_code", "end_date", "ann_date"),
            )
            statement_frames: dict[str, pd.DataFrame] = {}
            for endpoint in ("income", "balancesheet", "cashflow"):
                statement_path = cache_root / endpoint / f"{ts_code}.csv"
                if statement_path.exists():
                    statement = _read_csv(statement_path)
                    statement = _sort_frame(
                        statement.loc[statement["ann_date"].le(end_key)],
                        ("ts_code", "end_date", "ann_date", "update_flag"),
                    )
                else:
                    statement = pd.DataFrame(columns=RAW_SCHEMAS[endpoint].names)
                statement_frames[endpoint] = statement
            adjustments = _read_csv(
                cache_root / "adj_factor" / f"{ts_code}.csv"
            )
            adjustments = _sort_frame(
                adjustments.loc[
                    adjustments["trade_date"].between(start_key, end_key)
                ],
                ("ts_code", "trade_date"),
            )
            names = _read_csv(cache_root / "namechange" / f"{ts_code}.csv")
            visible_names = names["ann_date"].eq("") | names["ann_date"].le(end_key)
            names = _sort_frame(
                names.loc[visible_names],
                ("ts_code", "start_date", "ann_date"),
            )
            baostock = _read_csv(
                cache_root / "baostock_status" / f"{ts_code}.csv"
            )
            baostock = _sort_frame(
                baostock.loc[baostock["trade_date"].between(start_key, end_key)],
                ("ts_code", "trade_date"),
            )
            per_code_frames = {
                "fina_indicator": fina_indicator,
                **statement_frames,
                "adj_factor": adjustments,
                "namechange": names,
                "baostock_status": baostock,
            }
            for endpoint, frame in per_code_frames.items():
                writers[endpoint].write(frame)
                if (cache_root / endpoint / f"{ts_code}.csv").exists():
                    endpoint_files[endpoint] += 1
                endpoint_rows[endpoint] += len(frame)
                date_info = _date_range(frame)
                current = endpoint_ranges[endpoint]
                if date_info["min_date"] is not None:
                    current["min_date"] = (
                        min(str(current["min_date"]), date_info["min_date"])
                        if current["min_date"] else date_info["min_date"]
                    )
                    current["max_date"] = (
                        max(str(current["max_date"]), date_info["max_date"])
                        if current["max_date"] else date_info["max_date"]
                    )

            daily = _read_code_partition(connection, "daily", ts_code)
            daily_basic = _read_code_partition(connection, "daily_basic", ts_code)
            suspend = _read_code_partition(connection, "suspend_d", ts_code)
            suspend_events = _aggregate_suspend_events(suspend)
            history = _build_history(
                code=ts_code,
                open_dates=open_dates,
                master_row=master_by_code[ts_code],
                daily=daily,
                daily_basic=daily_basic,
                suspend_events=suspend_events,
                baostock=baostock,
                names=names,
                adjustments=adjustments,
            )
            adjustment_available = pd.to_numeric(
                history["adj_factor"], errors="coerce"
            ).gt(0)
            adjustment_required = pd.to_numeric(
                history["close"], errors="coerce"
            ).notna()
            adjustment_active_expected_rows += int(adjustment_required.sum())
            adjustment_active_available_rows += int(
                (adjustment_required & adjustment_available).sum()
            )
            membership_mask = history["trade_date"].astype(str).str[:6].isin(
                membership_months_by_code.get(ts_code, set())
            )
            membership_required = membership_mask & adjustment_required
            adjustment_membership_expected_rows += int(membership_required.sum())
            adjustment_membership_available_rows += int(
                (membership_required & adjustment_available).sum()
            )
            warmup_values = membership_mask.to_numpy(copy=True)
            previous = False
            for position, is_member in enumerate(warmup_values):
                if is_member and not previous:
                    warmup_values[
                        max(0, position - ADJ_FACTOR_WARMUP_SESSIONS):position
                    ] = True
                previous = bool(is_member)
            warmup_mask = pd.Series(warmup_values, index=history.index)
            warmup_required = warmup_mask & adjustment_required
            warmup_expected = int(warmup_required.sum())
            warmup_available = int(
                (warmup_required & adjustment_available).sum()
            )
            adjustment_warmup_expected_rows += warmup_expected
            adjustment_warmup_available_rows += warmup_available
            warmup_coverage = (
                warmup_available / warmup_expected if warmup_expected else 1.0
            )
            if warmup_coverage < MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE:
                adjustment_warmup_failures.append(
                    (ts_code, warmup_coverage, warmup_available, warmup_expected)
                )
            history_name = f"history_{ts_code[:6]}_{run_key}_{len(history)}.csv"
            staged_history_path = staging_history / history_name
            published_history_path = history_root / history_name
            write_dataframe_csv_atomic(history, staged_history_path, index=False)
            record = _staged_output_record(
                staged_history_path,
                published_history_path,
                history,
                repo_root,
            )
            outputs[record["path"]] = record
            history_rows += len(history)
            writers["security_status"].write(history[status_columns].copy())

        adjustment_active_coverage = (
            adjustment_active_available_rows / adjustment_active_expected_rows
            if adjustment_active_expected_rows else 0.0
        )
        adjustment_membership_coverage = (
            adjustment_membership_available_rows
            / adjustment_membership_expected_rows
            if adjustment_membership_expected_rows else 0.0
        )
        adjustment_warmup_coverage = (
            adjustment_warmup_available_rows / adjustment_warmup_expected_rows
            if adjustment_warmup_expected_rows else 0.0
        )
        if (
            adjustment_membership_coverage
            < MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE
        ):
            raise ValueError(
                "materialization_source_incomplete:adj_factor_coverage:"
                f"{adjustment_membership_coverage:.6f}<"
                f"{MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE:.6f}"
            )
        if (
            adjustment_warmup_coverage
            < MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE
            or adjustment_warmup_failures
        ):
            failure_sample = ",".join(
                f"{code}={coverage:.6f}({available}/{expected})"
                for code, coverage, available, expected
                in adjustment_warmup_failures[:10]
            )
            raise ValueError(
                "materialization_source_incomplete:adj_factor_warmup_coverage:"
                f"{adjustment_warmup_coverage:.6f}<"
                f"{MIN_ADJ_FACTOR_POINT_IN_TIME_COVERAGE:.6f}:"
                f"codes={failure_sample}"
            )

        if connection is not None:
            connection.close()
            connection = None
        for writer in writers.values():
            writer.close()

        for name, writer in sorted(writers.items()):
            published_path = raw_root / f"{name}.parquet"
            record = writer.output_record(repo_root, published_path)
            outputs[record["path"]] = record

        _assert_sources_unchanged(source_paths, initial_source_hashes)
        source_digest = _canonical_digest(initial_source_hashes)
        outputs = dict(sorted(outputs.items()))
        output_digest = _canonical_digest(outputs)
        endpoint_coverage = {
            "trade_cal": {
                "files": 1,
                "rows": len(open_dates),
                "min_date": open_dates[0],
                "max_date": open_dates[-1],
            },
            **partition_coverage,
            "index_weight": {
                "files": len(index_frames),
                "rows": index_rows,
                "min_date": index_min,
                "max_date": index_max,
            },
            **{
                endpoint: {
                    "files": endpoint_files[endpoint],
                    "rows": endpoint_rows[endpoint],
                    **endpoint_ranges[endpoint],
                }
                for endpoint in endpoint_rows
            },
            "benchmark_daily": {
                "files": len(INDEXES),
                "rows": benchmark_rows,
                "min_date": open_dates[0],
                "max_date": open_dates[-1],
            },
            "stock_basic": {
                "files": 1,
                "rows": len(stock_basic),
                "min_date": None,
                "max_date": None,
            },
        }
        endpoint_coverage["adj_factor"]["active_lifecycle_coverage"] = (
            adjustment_active_coverage
        )
        endpoint_coverage["adj_factor"]["membership_point_in_time_coverage"] = (
            adjustment_membership_coverage
        )
        endpoint_coverage["adj_factor"]["membership_warmup_point_in_time_coverage"] = (
            adjustment_warmup_coverage
        )
        endpoint_coverage["adj_factor"]["warmup_sessions"] = (
            ADJ_FACTOR_WARMUP_SESSIONS
        )
        audit_contract = {
            "status": "complete",
            "market": "a_share",
            "as_of": run_key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "historical_union_codes": union_codes,
            "historical_union_count": len(union_codes),
            "endpoint_coverage": endpoint_coverage,
            "sources": source_path_list,
            "source_paths": source_path_list,
            "source_hashes": initial_source_hashes,
            "source_digest": source_digest,
            "outputs": outputs,
            "output_digest": output_digest,
        }
        snapshot_manifest = {
            **audit_contract,
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "mode": "cumulative",
            "completion_manifest": "materialization_manifest.json",
        }
        materialization_manifest = {
            **audit_contract,
            "schema_version": MATERIALIZATION_SCHEMA_VERSION,
            "field_semantics": {
                "is_suspended": "baostock_full_day_tradestatus_zero",
                "tushare_suspend_event": "tushare_intraday_or_interval_event_observed",
                "tushare_resume_event": "tushare_resume_record_provenance_only",
                "partial_suspension_event": "tushare_suspension_event_not_classified_as_full_day",
                "is_tradable": "fail_closed_requires_quote_and_baostock_trading_and_no_tushare_event_or_conflict",
                "status_conflict": "full_day_or_quote_evidence_disagrees; partial_suspension_with_full_day_trading_is_not_a_conflict",
                "status_unknown": "missing_full_day_baostock_status_is_not_assumed_tradable",
                "history_prices": "raw_execution_prices; technical_features_apply_same_day_adj_factor",
                "amount": "yuan; amount_thousand_yuan_preserves_tushare_daily_source_unit",
            },
        }
        staging_snapshot = staging_raw / "snapshot_manifest.json"
        staging_manifest = staging_raw / "materialization_manifest.json"
        write_text_atomic(
            staging_snapshot,
            json.dumps(snapshot_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        write_text_atomic(
            staging_manifest,
            json.dumps(materialization_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        manifest_sha256 = _sha256(staging_manifest)
        _publish_staged_outputs(
            staging_root=staging_root,
            staging_raw=staging_raw,
            staging_history=staging_history,
            raw_root=raw_root,
            history_root=history_root,
            run_key=run_key,
            parent_marker=parent_marker,
            generation=lease.generation,
        )
        published = True
        manifest_path = raw_root / "materialization_manifest.json"
        return {
            "status": "complete",
            "schema_version": MATERIALIZATION_SCHEMA_VERSION,
            "as_of": run_key,
            "historical_union_codes": union_codes,
            "historical_union_count": len(union_codes),
            "history_rows": history_rows,
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "source_digest": source_digest,
            "output_digest": output_digest,
        }
    finally:
        try:
            if connection is not None:
                connection.close()
            for writer in writers.values():
                try:
                    writer.close()
                except Exception:
                    pass
            if not published:
                _unlink_owned_marker(run_marker, lease.generation)
                _unlink_owned_marker(parent_marker, lease.generation)
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)
        finally:
            lease.release()
