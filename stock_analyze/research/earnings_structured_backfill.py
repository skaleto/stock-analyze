"""Resumable Tushare forecast/express history backfill and PIT normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


FORECAST_FIELDS = (
    "ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
    "net_profit_min,net_profit_max,last_parent_net,first_ann_date,"
    "summary,change_reason,update_flag"
)
EXPRESS_FIELDS = (
    "ts_code,ann_date,end_date,revenue,operate_profit,total_profit,n_income,"
    "total_assets,total_hldr_eqy_exc_min_int,diluted_eps,diluted_roe,"
    "yoy_net_profit,bps,perf_summary,update_flag"
)


@dataclass(frozen=True)
class EarningsBackfillPartition:
    endpoint: str
    start_date: str
    end_date: str

    @property
    def key(self) -> str:
        return f"{self.endpoint}:{self.start_date}:{self.end_date}"


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _month_partitions(start: date, end: date) -> Iterable[tuple[date, date]]:
    current = start.replace(day=1)
    while current <= end:
        following = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
        yield max(start, current), min(end, following - timedelta(days=1))
        current = following


def earnings_partitions(
    start_date: str,
    end_date: str,
) -> list[EarningsBackfillPartition]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("earnings_backfill_range")
    forecast = [
        EarningsBackfillPartition(
            "forecast", value.strftime("%Y%m%d"), value.strftime("%Y%m%d")
        )
        for value in _date_range(start, end)
    ]
    express = [
        EarningsBackfillPartition(
            "express", left.strftime("%Y%m%d"), right.strftime("%Y%m%d")
        )
        for left, right in _month_partitions(start, end)
    ]
    return [*forecast, *express]


def _state_path(root: Path) -> Path:
    return root / "manifest.json"


def _load_state(root: Path, start_date: str, end_date: str) -> dict[str, Any]:
    path = _state_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != "earnings-structured-backfill-v1"
            or payload.get("start_date") != start_date
            or payload.get("end_date") != end_date
        ):
            raise ValueError("earnings_backfill_manifest_conflict")
        return payload
    return {
        "protocol_version": "earnings-structured-backfill-v1",
        "start_date": start_date,
        "end_date": end_date,
        "partitions": {},
    }


def _partition_path(root: Path, partition: EarningsBackfillPartition) -> Path:
    return (
        root / partition.endpoint
        / f"{partition.start_date}_{partition.end_date}.parquet"
    )


def _validate_frame(
    frame: pd.DataFrame,
    partition: EarningsBackfillPartition,
) -> pd.DataFrame:
    required = {
        "ts_code", "ann_date", "end_date", "update_flag",
        *( {"type", "first_ann_date"} if partition.endpoint == "forecast" else {"n_income"} ),
    }
    if required.difference(frame.columns):
        raise ValueError(f"earnings_backfill_shape:{partition.endpoint}")
    normalized = frame.copy()
    for column in ("ts_code", "ann_date", "end_date", "first_ann_date", "update_flag"):
        if column in normalized:
            normalized[column] = normalized[column].astype("string")
    normalized["ann_date"] = normalized["ann_date"].map(_date_key)
    if not normalized.empty and not normalized["ann_date"].between(
        partition.start_date, partition.end_date
    ).all():
        raise ValueError(f"earnings_backfill_partition_leak:{partition.key}")
    normalized = normalized.loc[
        normalized["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    normalized = normalized.sort_values(
        ["ts_code", "ann_date", "end_date", "update_flag"], kind="stable"
    ).drop_duplicates(
        ["ts_code", "ann_date", "end_date", "update_flag"], keep="last"
    )
    return normalized.reset_index(drop=True)


def _fetch_partition(client: Any, partition: EarningsBackfillPartition) -> pd.DataFrame:
    if partition.endpoint == "forecast":
        return client.forecast(
            ann_date=partition.start_date,
            fields=FORECAST_FIELDS,
        )
    return client.express(
        start_date=partition.start_date,
        end_date=partition.end_date,
        fields=EXPRESS_FIELDS,
    )


def run_structured_earnings_backfill(
    repo_root: str | Path,
    client: Any,
    *,
    start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
    max_partitions: int | None = None,
    on_partition: Callable[[EarningsBackfillPartition], None] | None = None,
) -> dict[str, Any]:
    root = (
        Path(repo_root).resolve() / "data" / "research"
        / "earnings_structured" / "v1"
    )
    root.mkdir(parents=True, exist_ok=True)
    start_key, end_key = _date_key(start_date), _date_key(end_date)
    state = _load_state(root, start_key, end_key)
    completed = state["partitions"]
    processed = 0
    fetched_rows = 0
    for partition in earnings_partitions(start_date, end_date):
        if partition.key in completed:
            continue
        if max_partitions is not None and processed >= max(0, int(max_partitions)):
            break
        if on_partition is not None:
            on_partition(partition)
        frame = _validate_frame(_fetch_partition(client, partition), partition)
        path = _partition_path(root, partition)
        ResearchStore(root).write_parquet_atomic(path, frame)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        completed[partition.key] = {
            "endpoint": partition.endpoint,
            "start_date": partition.start_date,
            "end_date": partition.end_date,
            "rows": int(len(frame)),
            "path": path.relative_to(Path(repo_root).resolve()).as_posix(),
            "sha256": digest,
        }
        write_text_atomic(
            _state_path(root),
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        processed += 1
        fetched_rows += len(frame)
    total_partitions = len(earnings_partitions(start_date, end_date))
    return {
        "status": (
            "complete" if len(completed) == total_partitions else "in_progress"
        ),
        "processed_partitions": processed,
        "completed_partitions": len(completed),
        "total_partitions": total_partitions,
        "fetched_rows": int(fetched_rows),
        "manifest": str(_state_path(root)),
    }
