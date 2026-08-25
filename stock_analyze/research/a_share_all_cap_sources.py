"""PIT-safe, resumable reference sources for A-share all-cap research."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from . import a_share_all_cap_source_store as source_store


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
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI",
    "801110.SI", "801120.SI", "801130.SI", "801140.SI", "801150.SI",
    "801160.SI", "801170.SI", "801180.SI", "801200.SI", "801210.SI",
    "801230.SI", "801710.SI", "801720.SI", "801730.SI", "801740.SI",
    "801750.SI", "801760.SI", "801770.SI", "801780.SI", "801790.SI",
    "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
)

CSI2000_INCEPTION = date(2023, 9, 1)
_SLEEVE_INDEXES = tuple(
    code for code, sleeve in REFERENCE_INDEXES.items() if sleeve != "all_share"
)
_A_SHARE_CODE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_STOCK_BASIC_STATUSES = frozenset({"L", "D", "P"})
_INDEX_WEIGHT_COMPONENT_COUNTS = {
    "000300.SH": 300,
    "000905.SH": 500,
    "000852.SH": 1000,
    "932000.CSI": 2000,
}
_INDEX_WEIGHT_TOTAL = 100.0
_INDEX_WEIGHT_TOTAL_TOLERANCE = 0.1
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 0.35


@dataclass(frozen=True)
class AllCapSourceManifest:
    """Verified publication with lazy, yearly access to daily limit prices."""

    metadata: Mapping[str, object]
    publication_dir: Path
    _index_daily: Mapping[str, pd.DataFrame]
    _index_weights: Mapping[str, pd.DataFrame]
    _industry_membership: pd.DataFrame
    stk_limit: Mapping[str, source_store.SourcePartition]

    @property
    def index_daily(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType(
            {code: frame.copy(deep=True) for code, frame in self._index_daily.items()}
        )

    @property
    def index_weights(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType(
            {code: frame.copy(deep=True) for code, frame in self._index_weights.items()}
        )

    @property
    def industry_membership(self) -> pd.DataFrame:
        return self._industry_membership.copy(deep=True)

    def load_stk_limit_year(self, year: str | int) -> pd.DataFrame:
        key = str(year)
        partition = self.stk_limit.get(key)
        if partition is None:
            raise KeyError(f"all_cap_source_stk_limit_year:{key}")
        frame = partition.load()
        _validate_stk_limit_values(frame)
        return _normalize_identifier_dtypes(frame)


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


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


def _empty_frame(dataset: str) -> pd.DataFrame:
    return source_store.ARROW_SCHEMAS[dataset].empty_table().to_pandas(
        types_mapper=pd.ArrowDtype
    )


def _select_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    code: str,
) -> pd.DataFrame:
    if set(columns).difference(frame.columns):
        raise ValueError(code)
    return frame.loc[:, list(columns)].copy()


def _strings(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    code: str,
    nullable: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result:
            raise ValueError(code)
        values = result[column].astype("string[pyarrow]").str.strip()
        values = values.mask(values == "")
        if column not in nullable and values.isna().any():
            raise ValueError(code)
        result[column] = values.astype("string[pyarrow]")
    return result


def _dates(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    code: str,
    nullable: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result:
            raise ValueError(code)
        normalized: list[object] = []
        for value in result[column]:
            if pd.isna(value) or not str(value).strip():
                if column not in nullable:
                    raise ValueError(code)
                normalized.append(pd.NA)
            else:
                normalized.append(_date_key(value, code=code))
        result[column] = pd.Series(
            normalized,
            index=result.index,
            dtype="string[pyarrow]",
        )
    return result


def _numbers(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    code: str,
    positive: tuple[str, ...] = (),
    nonnegative: tuple[str, ...] = (),
) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result:
            raise ValueError(code)
        values = pd.to_numeric(result[column], errors="coerce").astype("float64")
        if values.isna().any() or not np.isfinite(values).all():
            raise ValueError(code)
        if column in positive and (values <= 0).any():
            raise ValueError(code)
        if column in nonnegative and (values < 0).any():
            raise ValueError(code)
        result[column] = values
    return result


def _normalize_identifier_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    identifiers = {
        "cal_date", "con_code", "in_date", "index_code", "is_new",
        "l1_code", "l2_code", "l3_code", "out_date", "snapshot_as_of",
        "trade_date", "ts_code",
    }
    for column in identifiers.intersection(result.columns):
        result[column] = result[column].astype("string[pyarrow]")
    return result


class _ProviderCaller:
    def __init__(self, interval_seconds: float) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds < 0:
            raise ValueError("all_cap_source_request_interval")
        self.interval_seconds = float(interval_seconds)
        self.last_request_started: float | None = None

    def call(self, method: Any, **kwargs: object) -> object:
        for attempt in range(_RETRY_ATTEMPTS):
            now = time.monotonic()
            if self.last_request_started is not None and self.interval_seconds:
                wait = self.interval_seconds - (now - self.last_request_started)
                if wait > 0:
                    time.sleep(wait)
            self.last_request_started = time.monotonic()
            try:
                return method(**kwargs)
            except Exception:  # noqa: BLE001 - provider failures are retryable
                if attempt + 1 == _RETRY_ATTEMPTS:
                    raise
                time.sleep(_RETRY_BASE_SECONDS * (2**attempt))
        raise RuntimeError("all_cap_source_provider_retry_unreachable")


def _month_snapshots(start: date, end: date) -> list[date]:
    snapshots: list[date] = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        snapshots.append(max(start, cursor))
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return snapshots


def _expected_pre_inception(start: date, end: date) -> list[dict[str, str]]:
    return [
        {
            "dataset": "index_weights",
            "index_code": "932000.CSI",
            "snapshot_as_of": snapshot.strftime("%Y%m%d"),
            "status": "pre_inception",
        }
        for snapshot in _month_snapshots(start, end)
        if snapshot < CSI2000_INCEPTION
    ]


def _normalize_weight_snapshot(
    source: object,
    *,
    index_code: str,
    query_start: str,
    snapshot_as_of: str,
) -> pd.DataFrame:
    code = "all_cap_source_index_weight_schema"
    frame = _source_frame(source, source_name="index_weight")
    if frame.empty:
        raise ValueError("all_cap_source_index_weight_empty")
    frame = _select_columns(
        frame,
        ("index_code", "con_code", "trade_date", "weight"),
        code=code,
    )
    frame = _strings(frame, ("index_code", "con_code", "trade_date"), code=code)
    frame = _dates(frame, ("trade_date",), code=code)
    frame = _numbers(frame, ("weight",), code=code, positive=("weight",))
    if set(frame["index_code"]) != {index_code}:
        raise ValueError(code)
    if (frame["trade_date"] > snapshot_as_of).any():
        raise ValueError("all_cap_source_index_weight_future")
    if (frame["trade_date"] < query_start).any():
        raise ValueError(code)
    latest_date = str(frame["trade_date"].max())
    latest = frame.loc[frame["trade_date"] == latest_date].copy()
    if latest.empty or latest.duplicated(["index_code", "con_code"]).any():
        raise ValueError("all_cap_source_index_weight_duplicate")
    latest.insert(1, "snapshot_as_of", snapshot_as_of)
    latest = latest[
        ["index_code", "snapshot_as_of", "con_code", "trade_date", "weight"]
    ]
    normalized = _normalize_identifier_dtypes(
        latest.sort_values("con_code").reset_index(drop=True)
    )
    _index_weight_completeness(
        normalized,
        code="all_cap_source_index_weight_completeness",
    )
    return normalized


def _index_weight_completeness(
    frame: pd.DataFrame,
    *,
    code: str,
) -> list[dict[str, object]]:
    completeness: list[dict[str, object]] = []
    for (index_code, snapshot_as_of), snapshot in frame.groupby(
        ["index_code", "snapshot_as_of"],
        sort=True,
    ):
        index_key = str(index_code)
        target_count = _INDEX_WEIGHT_COMPONENT_COUNTS.get(index_key)
        component_count = len(snapshot)
        weight_sum = math.fsum(float(value) for value in snapshot["weight"])
        if (
            target_count is None
            or component_count != target_count
            or abs(weight_sum - _INDEX_WEIGHT_TOTAL)
            > _INDEX_WEIGHT_TOTAL_TOLERANCE
        ):
            raise ValueError(
                f"{code}:{index_key}:{snapshot_as_of}:"
                f"components={component_count}:weight_sum={weight_sum}"
            )
        completeness.append(
            {
                "index_code": index_key,
                "snapshot_as_of": str(snapshot_as_of),
                "component_count": component_count,
                "weight_sum": weight_sum,
                "coverage_status": "complete",
            }
        )
    if not completeness:
        raise ValueError(code)
    return completeness


def _validate_weight_values(frame: pd.DataFrame) -> None:
    code = "all_cap_source_manifest_index_weights"
    normalized = _strings(
        frame,
        ("index_code", "snapshot_as_of", "con_code", "trade_date"),
        code=code,
    )
    normalized = _dates(
        normalized,
        ("snapshot_as_of", "trade_date"),
        code=code,
    )
    normalized = _numbers(
        normalized,
        ("weight",),
        code=code,
        positive=("weight",),
    )
    if (
        normalized.duplicated(
            ["index_code", "snapshot_as_of", "con_code"]
        ).any()
        or (normalized["trade_date"] > normalized["snapshot_as_of"]).any()
    ):
        raise ValueError(code)
    _index_weight_completeness(normalized, code=code)


def _collect_weights(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
    start: date,
    end: date,
) -> None:
    for snapshot in _month_snapshots(start, end):
        snapshot_key = snapshot.strftime("%Y%m%d")
        query_start = (snapshot - timedelta(days=95)).strftime("%Y%m%d")
        for index_code in _SLEEVE_INDEXES:
            key = f"index_weight:{index_code}:{snapshot_key}"
            existing = job.load_checkpoint(key, "index_weights")
            pre_inception = (
                index_code == "932000.CSI" and snapshot < CSI2000_INCEPTION
            )
            if existing is not None:
                if pre_inception and existing.empty:
                    continue
                try:
                    _validate_weight_values(existing)
                    if (
                        set(existing["index_code"]) == {index_code}
                        and set(existing["snapshot_as_of"]) == {snapshot_key}
                    ):
                        continue
                except ValueError:
                    pass
            if pre_inception:
                job.save_checkpoint(
                    key,
                    "index_weights",
                    _empty_frame("index_weights"),
                    status="pre_inception",
                    index_code=index_code,
                    snapshot_as_of=snapshot_key,
                )
                continue
            frame = _normalize_weight_snapshot(
                caller.call(
                    pro_client.index_weight,
                    index_code=index_code,
                    start_date=query_start,
                    end_date=snapshot_key,
                ),
                index_code=index_code,
                query_start=query_start,
                snapshot_as_of=snapshot_key,
            )
            job.save_checkpoint(
                key,
                "index_weights",
                frame,
                status="complete",
                index_code=index_code,
                snapshot_as_of=snapshot_key,
            )


def _normalize_trade_calendar(source: object, start: str, end: str) -> pd.DataFrame:
    code = "all_cap_source_trade_calendar_schema"
    frame = _source_frame(source, source_name="trade_cal")
    frame = _select_columns(frame, ("cal_date", "is_open"), code=code)
    frame = _strings(frame, ("cal_date", "is_open"), code=code)
    frame = _dates(frame, ("cal_date",), code=code)
    frame = frame.loc[frame["is_open"] == "1"].drop_duplicates("cal_date")
    frame = frame.sort_values("cal_date").reset_index(drop=True)
    if (
        frame.empty
        or (frame["cal_date"] < start).any()
        or (frame["cal_date"] > end).any()
    ):
        raise ValueError("all_cap_source_trade_calendar_empty")
    return _normalize_identifier_dtypes(frame)


def _trade_calendar(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
    start: str,
    end: str,
) -> list[str]:
    key = "trade_calendar:all"
    frame = job.load_checkpoint(key, "trade_calendar")
    if frame is not None:
        try:
            frame = _normalize_trade_calendar(frame, start, end)
        except ValueError:
            frame = None
    if frame is None:
        frame = _normalize_trade_calendar(
            caller.call(
                pro_client.trade_cal,
                exchange="",
                start_date=start,
                end_date=end,
                is_open="1",
            ),
            start,
            end,
        )
        job.save_checkpoint(key, "trade_calendar", frame)
    return [str(value) for value in frame["cal_date"]]


def _normalize_index_daily(
    source: object,
    *,
    index_code: str,
    start: str,
    end: str,
    open_dates: set[str],
) -> pd.DataFrame:
    code = "all_cap_source_index_daily_schema"
    frame = _source_frame(source, source_name="index_daily")
    if frame.empty:
        raise ValueError("all_cap_source_index_daily_empty")
    frame = _select_columns(
        frame,
        ("ts_code", "trade_date", "open", "high", "low", "close", "vol"),
        code=code,
    )
    frame = _strings(frame, ("ts_code", "trade_date"), code=code)
    frame = _dates(frame, ("trade_date",), code=code)
    frame = _numbers(
        frame,
        ("open", "high", "low", "close", "vol"),
        code=code,
        positive=("open", "high", "low", "close"),
        nonnegative=("vol",),
    )
    if (
        set(frame["ts_code"]) != {index_code}
        or (frame["trade_date"] < start).any()
        or (frame["trade_date"] > end).any()
        or frame.duplicated(["ts_code", "trade_date"]).any()
    ):
        raise ValueError(code)
    if set(frame["trade_date"]) != open_dates:
        raise ValueError("all_cap_source_index_daily_calendar")
    return _normalize_identifier_dtypes(
        frame.sort_values("trade_date").reset_index(drop=True)
    )


def _validate_index_daily_values(frame: pd.DataFrame) -> None:
    code = "all_cap_source_manifest_index_daily"
    normalized = _strings(frame, ("ts_code", "trade_date"), code=code)
    normalized = _dates(normalized, ("trade_date",), code=code)
    normalized = _numbers(
        normalized,
        ("open", "high", "low", "close", "vol"),
        code=code,
        positive=("open", "high", "low", "close"),
        nonnegative=("vol",),
    )
    if normalized.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError(code)


def _collect_index_daily(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
    start: str,
    end: str,
    open_dates: list[str],
) -> None:
    expected = set(open_dates)
    for index_code in REFERENCE_INDEXES:
        key = f"index_daily:{index_code}"
        frame = job.load_checkpoint(key, "index_daily")
        if frame is not None:
            try:
                _normalize_index_daily(
                    frame,
                    index_code=index_code,
                    start=start,
                    end=end,
                    open_dates=expected,
                )
                continue
            except ValueError:
                pass
        frame = _normalize_index_daily(
            caller.call(
                pro_client.index_daily,
                ts_code=index_code,
                start_date=start,
                end_date=end,
            ),
            index_code=index_code,
            start=start,
            end=end,
            open_dates=expected,
        )
        job.save_checkpoint(key, "index_daily", frame, index_code=index_code)


def _industry_codes(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
) -> tuple[str, ...]:
    key = "industry_codes:SW2021"
    frame = job.load_checkpoint(key, "industry_codes")
    if frame is None:
        discover = getattr(pro_client, "index_classify", None)
        if callable(discover):
            frame = _source_frame(
                caller.call(discover, level="L1", src="SW2021"),
                source_name="index_classify",
            )
            frame = _select_columns(
                frame,
                ("index_code",),
                code="all_cap_source_industry_codes",
            )
            frame = _strings(
                frame,
                ("index_code",),
                code="all_cap_source_industry_codes",
            )
            if set(frame["index_code"]) != set(_SW2021_L1_CODES):
                raise ValueError("all_cap_source_industry_codes")
            frame = frame.drop_duplicates().sort_values("index_code").reset_index(drop=True)
        else:
            frame = pd.DataFrame(
                {"index_code": pd.Series(_SW2021_L1_CODES, dtype="string[pyarrow]")}
            )
        job.save_checkpoint(key, "industry_codes", frame)
    if set(frame["index_code"]) != set(_SW2021_L1_CODES) or len(frame) != 31:
        raise ValueError("all_cap_source_industry_codes")
    return _SW2021_L1_CODES


def _normalize_industry(
    source: object,
    *,
    l1_code: str,
    is_new: str,
) -> pd.DataFrame:
    code = "all_cap_source_industry_schema"
    frame = _source_frame(source, source_name="index_member_all")
    if frame.empty:
        raise ValueError("all_cap_source_industry_empty")
    if "l1_code" not in frame:
        frame["l1_code"] = l1_code
    if "is_new" not in frame:
        frame["is_new"] = is_new
    columns = (
        "l1_code", "l2_code", "l3_code", "ts_code",
        "in_date", "out_date", "is_new",
    )
    frame = _select_columns(frame, columns, code=code)
    frame = _strings(
        frame,
        columns,
        code=code,
        nullable=frozenset({"out_date"}),
    )
    frame = _dates(
        frame,
        ("in_date", "out_date"),
        code=code,
        nullable=frozenset({"out_date"}),
    )
    frame["is_new"] = frame["is_new"].str.upper()
    if set(frame["l1_code"]) != {l1_code} or set(frame["is_new"]) != {is_new}:
        raise ValueError(code)
    closed = frame["out_date"].notna()
    if (frame.loc[closed, "out_date"] <= frame.loc[closed, "in_date"]).any():
        raise ValueError("all_cap_source_industry_interval")
    frame = frame.drop_duplicates(list(columns)).sort_values(list(columns))
    return _normalize_identifier_dtypes(frame.reset_index(drop=True))


def _reject_industry_overlaps(frame: pd.DataFrame) -> None:
    for ts_code, stock in frame.groupby("ts_code", sort=False):
        for level in ("l1_code", "l2_code", "l3_code"):
            intervals = (
                stock[[level, "in_date", "out_date", "is_new"]]
                .drop_duplicates()
                .sort_values(
                    ["in_date", "out_date", "is_new"],
                    na_position="last",
                )
            )
            latest_end = ""
            for row in intervals.itertuples(index=False, name=None):
                in_date = str(row[1])
                out_date = "99991231" if pd.isna(row[2]) else str(row[2])
                if latest_end and in_date < latest_end:
                    raise ValueError(
                        f"all_cap_source_industry_overlap:{ts_code}:{level}"
                    )
                latest_end = max(latest_end, out_date)


def _validate_industry_values(frame: pd.DataFrame) -> None:
    columns = (
        "l1_code", "l2_code", "l3_code", "ts_code",
        "in_date", "out_date", "is_new",
    )
    normalized = _strings(
        frame,
        columns,
        code="all_cap_source_manifest_industry",
        nullable=frozenset({"out_date"}),
    )
    normalized = _dates(
        normalized,
        ("in_date", "out_date"),
        code="all_cap_source_manifest_industry",
        nullable=frozenset({"out_date"}),
    )
    closed = normalized["out_date"].notna()
    if (normalized.loc[closed, "out_date"] <= normalized.loc[closed, "in_date"]).any():
        raise ValueError("all_cap_source_industry_interval")
    if normalized.duplicated(list(columns)).any():
        raise ValueError("all_cap_source_manifest_industry")
    _reject_industry_overlaps(normalized)


def _collect_industry(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
) -> None:
    for l1_code in _industry_codes(job, caller, pro_client):
        for is_new in ("Y", "N"):
            key = f"industry:{l1_code}:{is_new}"
            frame = job.load_checkpoint(key, "industry_membership")
            if frame is not None:
                try:
                    normalized = _normalize_industry(
                        frame,
                        l1_code=l1_code,
                        is_new=is_new,
                    )
                    if normalized.astype("string").equals(frame.astype("string")):
                        continue
                except ValueError:
                    pass
            frame = _normalize_industry(
                caller.call(
                    pro_client.index_member_all,
                    l1_code=l1_code,
                    is_new=is_new,
                ),
                l1_code=l1_code,
                is_new=is_new,
            )
            job.save_checkpoint(
                key,
                "industry_membership",
                frame,
                l1_code=l1_code,
                is_new=is_new,
            )


def _read_stock_master(repo_root: Path) -> tuple[pd.DataFrame, str]:
    cache_root = repo_root / "data/shared/backtest_cache"
    path = cache_root / "stock_basic.csv"
    meta_path = cache_root / "_meta.json"
    if not path.is_file():
        raise ValueError("all_cap_source_stock_master_missing")
    if not meta_path.is_file():
        raise ValueError("all_cap_source_stock_master_meta")
    if meta_path.is_symlink():
        raise ValueError("all_cap_source_symlink:stock_master_meta")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        statuses_done = meta.get("stock_basic_statuses_done")
    except Exception as exc:  # noqa: BLE001 - malformed metadata fails closed
        raise ValueError("all_cap_source_stock_master_meta") from exc
    if (
        not isinstance(meta, Mapping)
        or meta.get("stock_basic_done") is not True
        or not isinstance(statuses_done, list)
        or not _STOCK_BASIC_STATUSES.issubset(
            {str(value) for value in statuses_done}
        )
    ):
        raise ValueError("all_cap_source_stock_master_meta")
    if path.is_symlink():
        raise ValueError("all_cap_source_symlink:stock_master")
    try:
        stock_basic_bytes = path.read_bytes()
        frame = pd.read_csv(
            io.BytesIO(stock_basic_bytes),
            dtype={
                "ts_code": "string",
                "list_date": "string",
                "delist_date": "string",
                "list_status": "string",
            },
            keep_default_na=False,
        )
    except Exception as exc:  # noqa: BLE001 - malformed master fails closed
        raise ValueError("all_cap_source_stock_master_schema") from exc
    required = ("ts_code", "list_date", "delist_date", "list_status")
    try:
        frame = _select_columns(
            frame,
            required,
            code="all_cap_source_stock_master_schema",
        )
        frame = _strings(
            frame,
            required,
            code="all_cap_source_stock_master_schema",
            nullable=frozenset({"delist_date"}),
        )
        frame = _dates(
            frame,
            ("list_date", "delist_date"),
            code="all_cap_source_stock_master_schema",
            nullable=frozenset({"delist_date"}),
        )
    except ValueError as exc:
        raise ValueError("all_cap_source_stock_master_schema") from exc
    if not set(frame["list_status"]).issubset(_STOCK_BASIC_STATUSES):
        raise ValueError("all_cap_source_stock_master_status")
    b_share = frame["ts_code"].str.startswith(("200", "900"), na=False)
    frame = frame.loc[~b_share].reset_index(drop=True)
    if (
        frame.empty
        or frame["ts_code"].duplicated().any()
        or not frame["ts_code"].map(
            lambda value: bool(_A_SHARE_CODE.fullmatch(str(value)))
        ).all()
    ):
        raise ValueError("all_cap_source_stock_master_schema")
    closed = frame["delist_date"].notna()
    if (frame.loc[closed, "delist_date"] < frame.loc[closed, "list_date"]).any():
        raise ValueError("all_cap_source_stock_master_schema")
    return (
        _normalize_identifier_dtypes(frame),
        hashlib.sha256(stock_basic_bytes).hexdigest(),
    )


def _expected_stocks(master: pd.DataFrame, trade_date: str) -> set[str]:
    active = master.loc[
        (master["list_date"] <= trade_date)
        & (master["delist_date"].isna() | (master["delist_date"] >= trade_date))
    ]
    return {str(value) for value in active["ts_code"]}


def _normalize_stk_limit_response(
    source: object,
    *,
    trade_date: str,
    expected: set[str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    code = "all_cap_source_stk_limit_schema"
    frame = _source_frame(source, source_name="stk_limit")
    if frame.empty:
        raise ValueError("all_cap_source_stk_limit_empty")
    frame = _select_columns(
        frame,
        ("ts_code", "trade_date", "pre_close", "up_limit", "down_limit"),
        code=code,
    )
    frame = _strings(frame, ("ts_code", "trade_date"), code=code)
    frame = _dates(frame, ("trade_date",), code=code)
    frame = _numbers(
        frame,
        ("pre_close", "up_limit", "down_limit"),
        code=code,
        positive=("pre_close", "up_limit", "down_limit"),
    )
    if set(frame["trade_date"]) != {trade_date}:
        raise ValueError(code)
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("all_cap_source_stk_limit_duplicate")
    observed = {str(value) for value in frame["ts_code"]}
    missing = expected.difference(observed)
    if missing:
        raise ValueError(
            f"all_cap_source_stk_limit_missing:{trade_date}:{len(missing)}"
        )
    filtered = frame.loc[frame["ts_code"].isin(expected)].copy()
    filtered = filtered.sort_values("ts_code").reset_index(drop=True)
    return _normalize_identifier_dtypes(filtered), {
        "expected_count": len(expected),
        "observed_count": len(observed),
        "missing_count": 0,
        "extra_count": len(observed.difference(expected)),
    }


def _validate_stk_limit_values(frame: pd.DataFrame) -> None:
    code = "all_cap_source_manifest_stk_limit"
    normalized = _strings(frame, ("ts_code", "trade_date"), code=code)
    normalized = _dates(normalized, ("trade_date",), code=code)
    numeric_cols: list[str] = ["up_limit", "down_limit"]
    positive_cols: list[str] = ["up_limit", "down_limit"]
    if "pre_close" in frame.columns:
        numeric_cols.insert(0, "pre_close")
        positive_cols.insert(0, "pre_close")
    normalized = _numbers(
        normalized,
        tuple(numeric_cols),
        code=code,
        positive=tuple(positive_cols),
    )
    if normalized.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError(code)


def _collect_stk_limit(
    job: source_store.JobStore,
    caller: _ProviderCaller,
    pro_client: object,
    master: pd.DataFrame,
    open_dates: list[str],
) -> None:
    for trade_date in open_dates:
        expected = _expected_stocks(master, trade_date)
        if not expected:
            raise ValueError(f"all_cap_source_stock_master_empty:{trade_date}")
        key = f"stk_limit:{trade_date}"
        record = job.checkpoint_record(key)
        frame = job.load_checkpoint(key, "stk_limit")
        if frame is not None and record is not None:
            try:
                _validate_stk_limit_values(frame)
                if (
                    set(frame["ts_code"]) == expected
                    and int(record.get("expected_count") or -1) == len(expected)
                    and int(record.get("missing_count") or -1) == 0
                ):
                    continue
            except (TypeError, ValueError):
                pass
        frame, counts = _normalize_stk_limit_response(
            caller.call(pro_client.stk_limit, trade_date=trade_date),
            trade_date=trade_date,
            expected=expected,
        )
        job.save_checkpoint(key, "stk_limit", frame, **counts)


def _concat_checkpoints(
    job: source_store.JobStore,
    keys: list[str],
    dataset: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for key in keys:
        frame = job.load_checkpoint(key, dataset)
        if frame is None:
            raise ValueError(f"all_cap_source_checkpoint_incomplete:{key}")
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise ValueError(f"all_cap_source_checkpoint_incomplete:{dataset}")
    return _normalize_identifier_dtypes(
        pd.concat(frames, ignore_index=True, sort=False)
    )


def _build_candidate(
    job: source_store.JobStore,
    *,
    start: date,
    end: date,
    open_dates: list[str],
    stock_master_sha256: str,
) -> Path:
    snapshots = [value.strftime("%Y%m%d") for value in _month_snapshots(start, end)]
    weight_keys = [
        f"index_weight:{index_code}:{snapshot}"
        for snapshot in snapshots
        for index_code in _SLEEVE_INDEXES
    ]
    daily_keys = [f"index_daily:{index_code}" for index_code in REFERENCE_INDEXES]
    industry_keys = [
        f"industry:{l1_code}:{is_new}"
        for l1_code in _SW2021_L1_CODES
        for is_new in ("Y", "N")
    ]
    index_weights = _concat_checkpoints(job, weight_keys, "index_weights")
    index_weights = index_weights.sort_values(
        ["index_code", "snapshot_as_of", "con_code"]
    ).reset_index(drop=True)
    weight_completeness = _index_weight_completeness(
        index_weights,
        code="all_cap_source_manifest_index_weights",
    )
    index_daily = _concat_checkpoints(job, daily_keys, "index_daily")
    index_daily = index_daily.sort_values(["ts_code", "trade_date"]).reset_index(
        drop=True
    )
    industry = _concat_checkpoints(job, industry_keys, "industry_membership")
    industry = industry.drop_duplicates().sort_values(
        ["l1_code", "l2_code", "l3_code", "ts_code", "in_date", "out_date", "is_new"]
    ).reset_index(drop=True)
    _reject_industry_overlaps(industry)

    candidate = job.reset_candidate()
    partitions: dict[str, list[dict[str, object]]] = {
        dataset: [] for dataset in source_store.PUBLICATION_DATASETS
    }
    for dataset, frame in (
        ("index_weights", index_weights),
        ("index_daily", index_daily),
        ("industry_membership", industry),
    ):
        partitions[dataset].append(
            source_store.write_publication_frame(candidate, dataset, frame)
        )

    daily_completeness: list[dict[str, object]] = []
    by_year: dict[str, list[tuple[Path, Mapping[str, object]]]] = {}
    for trade_date in open_dates:
        key = f"stk_limit:{trade_date}"
        record = job.checkpoint_record(key)
        if record is None:
            raise ValueError(f"all_cap_source_checkpoint_incomplete:{key}")
        path = job.root.joinpath(*Path(str(record["path"])).parts)
        by_year.setdefault(trade_date[:4], []).append((path, record))
        daily_completeness.append(
            {
                "trade_date": trade_date,
                "expected_count": int(record["expected_count"]),
                "observed_count": int(record["observed_count"]),
                "missing_count": int(record["missing_count"]),
                "extra_count": int(record["extra_count"]),
            }
        )
    for year, checkpoints in sorted(by_year.items()):
        partitions["stk_limit"].append(
            source_store.merge_stk_limit_year(candidate, year, checkpoints)
        )

    files = sorted(
        [dict(item) for records in partitions.values() for item in records],
        key=lambda item: str(item["path"]),
    )
    manifest: dict[str, object] = {
        "schema_version": source_store.SCHEMA_VERSION,
        "contract_version": source_store.CONTRACT_VERSION,
        "status": "complete",
        "publication_id": job.publication_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "reference_indexes": dict(REFERENCE_INDEXES),
        "industry_contract": "SW2021",
        "industry_requests": [
            {"l1_code": l1_code, "is_new": is_new}
            for l1_code in _SW2021_L1_CODES
            for is_new in ("Y", "N")
        ],
        "pre_inception": _expected_pre_inception(start, end),
        "index_weight_completeness": weight_completeness,
        "open_trade_dates": open_dates,
        "stock_master_sha256": stock_master_sha256,
        "stk_limit_completeness": daily_completeness,
        "dataset_schemas": {
            dataset: source_store.schema_contract(dataset)
            for dataset in source_store.PUBLICATION_DATASETS
        },
        "row_counts": {
            dataset: sum(int(item["rows"]) for item in records)
            for dataset, records in partitions.items()
        },
        "partitions": partitions,
        "files": files,
    }
    source_store.write_manifest(candidate, manifest)
    return candidate


def _validate_publication_values(
    directory: Path,
) -> source_store.VerifiedPublication:
    stk_dates: dict[str, int] = {}

    def validate_partition(
        dataset: str,
        frame: pd.DataFrame,
        record: Mapping[str, object],
    ) -> None:
        if dataset == "index_weights":
            _validate_weight_values(frame)
        elif dataset == "index_daily":
            _validate_index_daily_values(frame)
        elif dataset == "industry_membership":
            _validate_industry_values(frame)
        else:
            _validate_stk_limit_values(frame)
            counts = frame.groupby("trade_date", sort=False).size()
            for trade_date, count in counts.items():
                key = str(trade_date)
                if key in stk_dates:
                    raise ValueError("all_cap_source_manifest_stk_limit")
                stk_dates[key] = int(count)

    verified = source_store.verify_publication(
        directory,
        expected_reference_indexes=REFERENCE_INDEXES,
        partition_validator=validate_partition,
    )
    manifest = verified.manifest
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
    if re.fullmatch(
        r"[a-f0-9]{64}",
        str(manifest.get("stock_master_sha256") or ""),
    ) is None:
        raise ValueError("all_cap_source_manifest_stock_master")
    start = datetime.strptime(start_key, "%Y%m%d").date()
    end = datetime.strptime(end_key, "%Y%m%d").date()
    open_dates_raw = manifest.get("open_trade_dates")
    if not isinstance(open_dates_raw, list):
        raise ValueError("all_cap_source_manifest_stk_limit")
    open_dates = [
        _date_key(value, code="all_cap_source_manifest_stk_limit")
        for value in open_dates_raw
    ]
    if (
        not open_dates
        or open_dates != open_dates_raw
        or sorted(set(open_dates)) != open_dates
        or open_dates[0] < start_key
        or open_dates[-1] > end_key
    ):
        raise ValueError("all_cap_source_manifest_stk_limit")

    weights = verified.frames["index_weights"]
    expected_snapshots = {
        (index_code, snapshot.strftime("%Y%m%d"))
        for snapshot in _month_snapshots(start, end)
        for index_code in _SLEEVE_INDEXES
        if not (index_code == "932000.CSI" and snapshot < CSI2000_INCEPTION)
    }
    observed_snapshots = set(
        zip(weights["index_code"], weights["snapshot_as_of"], strict=True)
    )
    if observed_snapshots != expected_snapshots:
        raise ValueError("all_cap_source_manifest_index_weights")
    for _, snapshot in weights.groupby(["index_code", "snapshot_as_of"]):
        if snapshot["trade_date"].nunique() != 1:
            raise ValueError("all_cap_source_manifest_index_weights")
    if manifest.get("index_weight_completeness") != _index_weight_completeness(
        weights,
        code="all_cap_source_manifest_index_weights",
    ):
        raise ValueError("all_cap_source_manifest_index_weights")
    if manifest.get("pre_inception") != _expected_pre_inception(start, end):
        raise ValueError("all_cap_source_manifest_pre_inception")

    daily = verified.frames["index_daily"]
    if set(daily["ts_code"]) != set(REFERENCE_INDEXES):
        raise ValueError("all_cap_source_manifest_index_daily")
    for index_code in REFERENCE_INDEXES:
        if set(daily.loc[daily["ts_code"] == index_code, "trade_date"]) != set(
            open_dates
        ):
            raise ValueError("all_cap_source_manifest_index_daily")

    industry = verified.frames["industry_membership"]
    expected_industry_requests = [
        {"l1_code": l1_code, "is_new": is_new}
        for l1_code in _SW2021_L1_CODES
        for is_new in ("Y", "N")
    ]
    observed_industry_requests = manifest.get("industry_requests")
    if not isinstance(observed_industry_requests, list):
        raise ValueError("all_cap_source_manifest_industry")
    normalized_observed = [
        {"l1_code": item.get("l1_code"), "is_new": item.get("is_new")}
        if isinstance(item, Mapping) else None
        for item in observed_industry_requests
    ]
    if (
        set(industry["l1_code"]) != set(_SW2021_L1_CODES)
        or set(industry["is_new"]) != {"Y", "N"}
        or manifest.get("industry_contract") != "SW2021"
        or normalized_observed != expected_industry_requests
    ):
        raise ValueError("all_cap_source_manifest_industry")

    completeness = manifest.get("stk_limit_completeness")
    if not isinstance(completeness, list):
        raise ValueError("all_cap_source_manifest_stk_limit")
    declared_daily: dict[str, int] = {}
    for item in completeness:
        if not isinstance(item, Mapping):
            raise ValueError("all_cap_source_manifest_stk_limit")
        trade_date = str(item.get("trade_date") or "")
        try:
            expected_count = int(item["expected_count"])
            observed_count = int(item["observed_count"])
            missing_count = int(item["missing_count"])
            extra_count = int(item["extra_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("all_cap_source_manifest_stk_limit") from exc
        if (
            missing_count != 0
            or observed_count != expected_count + extra_count
            or expected_count <= 0
        ):
            raise ValueError("all_cap_source_manifest_stk_limit")
        declared_daily[trade_date] = expected_count
    if declared_daily != stk_dates or set(stk_dates) != set(open_dates):
        raise ValueError("all_cap_source_manifest_stk_limit")
    return verified


def publish_all_cap_sources(
    staging_dir: str | Path,
    repo_root: str | Path,
) -> Path:
    """Verify, atomically install, verify again, then advance latest."""

    staging = Path(staging_dir).absolute()
    pubs = source_store.publications_root(repo_root).absolute()
    if staging.parent != pubs or staging.is_symlink() or not staging.is_dir():
        raise ValueError("all_cap_source_staging_path")
    verified = _validate_publication_values(staging)
    publication_id = str(verified.manifest["publication_id"])
    destination = source_store.install_publication(staging, publication_id)
    published = _validate_publication_values(destination)
    source_store.write_latest(repo_root, published.manifest)
    return destination / "manifest.json"


def _latest_verified(repo_root: str | Path) -> source_store.VerifiedPublication:
    marker = source_store.read_latest(repo_root)
    publication_dir = Path(marker["publication_dir"])
    verified = _validate_publication_values(publication_dir)
    if (
        marker.get("manifest_sha256") != verified.manifest.get("manifest_sha256")
        or publication_dir.name != verified.manifest.get("publication_id")
    ):
        raise ValueError("all_cap_source_checksum:latest_manifest")
    return verified


def _find_matching_publication(
    repo_root: Path,
    start: date,
    end: date,
    stock_master_sha256: str,
) -> Path | None:
    start_key = start.strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    root = source_store.source_root(repo_root)
    latest_path = root / "latest.json"
    if latest_path.exists() or latest_path.is_symlink():
        latest = _latest_verified(repo_root)
        if (
            latest.manifest.get("start_date") == start_key
            and latest.manifest.get("end_date") == end_key
            and latest.manifest.get("stock_master_sha256") == stock_master_sha256
        ):
            return (
                source_store.publications_root(repo_root)
                / str(latest.manifest["publication_id"])
                / "manifest.json"
            )
    pubs = source_store.publications_root(repo_root)
    if not pubs.exists():
        return None
    if pubs.is_symlink():
        raise ValueError("all_cap_source_symlink:publications")
    matches: list[source_store.VerifiedPublication] = []
    for candidate in pubs.iterdir():
        if candidate.is_symlink():
            raise ValueError(f"all_cap_source_symlink:{candidate.name}")
        if not candidate.is_dir() or not re.fullmatch(
            r"[0-9]{8}_[0-9]{8}_[a-f0-9]{32}",
            candidate.name,
        ):
            continue
        try:
            verified = _validate_publication_values(candidate)
        except ValueError as exc:
            if "all_cap_source_symlink" in str(exc):
                raise
            continue
        if (
            verified.manifest.get("start_date") == start_key
            and verified.manifest.get("end_date") == end_key
            and verified.manifest.get("stock_master_sha256") == stock_master_sha256
        ):
            matches.append(verified)
    if not matches:
        return None
    selected = max(matches, key=lambda item: str(item.manifest.get("created_at") or ""))
    source_store.write_latest(repo_root, selected.manifest)
    return (
        source_store.publications_root(repo_root)
        / str(selected.manifest["publication_id"])
        / "manifest.json"
    )


def load_verified_all_cap_sources(
    repo_root: str | Path,
) -> AllCapSourceManifest:
    """Load verified small tables and immutable yearly limit descriptors."""

    verified = _latest_verified(repo_root)
    publication_dir = (
        source_store.publications_root(repo_root)
        / str(verified.manifest["publication_id"])
    )
    daily = _normalize_identifier_dtypes(verified.frames["index_daily"])
    weights = _normalize_identifier_dtypes(verified.frames["index_weights"])
    limit_partitions = MappingProxyType(
        {
            year: source_store.SourcePartition(
                path=partition.path,
                dataset=partition.dataset,
                partition=partition.partition,
                record=_deep_freeze(dict(partition.record)),
            )
            for year, partition in sorted(verified.stk_limit.items())
        }
    )
    return AllCapSourceManifest(
        metadata=_deep_freeze(dict(verified.manifest)),
        publication_dir=publication_dir,
        _index_daily=MappingProxyType(
            {
                code: daily.loc[daily["ts_code"] == code].reset_index(drop=True).copy()
                for code in REFERENCE_INDEXES
            }
        ),
        _index_weights=MappingProxyType(
            {
                code: weights.loc[weights["index_code"] == code]
                .reset_index(drop=True)
                .copy()
                for code in _SLEEVE_INDEXES
                if code in set(weights["index_code"])
            }
        ),
        _industry_membership=_normalize_identifier_dtypes(
            verified.frames["industry_membership"]
        ),
        stk_limit=limit_partitions,
    )


def collect_all_cap_sources(
    *,
    repo_root: Path,
    pro_client: object,
    start: date,
    end: date,
    request_interval_seconds: float = 0.35,
) -> dict[str, object]:
    """Collect bounded sources with durable checkpoints and atomic publication."""

    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError("all_cap_source_interval")
    required_methods = (
        "index_weight", "index_daily", "index_member_all", "trade_cal", "stk_limit",
    )
    if any(not callable(getattr(pro_client, method, None)) for method in required_methods):
        raise ValueError("all_cap_source_client")
    repo_root = Path(repo_root).absolute()
    master, master_hash = _read_stock_master(repo_root)
    existing = _find_matching_publication(
        repo_root,
        start,
        end,
        master_hash,
    )
    if existing is not None:
        return {"status": "complete", "manifest": str(existing)}

    caller = _ProviderCaller(request_interval_seconds)
    start_key = start.strftime("%Y%m%d")
    end_key = end.strftime("%Y%m%d")
    job = source_store.JobStore(
        repo_root,
        start_key,
        end_key,
        master_hash,
    )
    open_dates = _trade_calendar(job, caller, pro_client, start_key, end_key)
    _collect_weights(job, caller, pro_client, start, end)
    _collect_index_daily(
        job, caller, pro_client, start_key, end_key, open_dates
    )
    _collect_industry(job, caller, pro_client)
    _collect_stk_limit(job, caller, pro_client, master, open_dates)
    candidate = _build_candidate(
        job,
        start=start,
        end=end,
        open_dates=open_dates,
        stock_master_sha256=master_hash,
    )
    manifest = publish_all_cap_sources(candidate, repo_root)
    return {"status": "complete", "manifest": str(manifest)}
