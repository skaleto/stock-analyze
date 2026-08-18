"""Resumable shareholder-count history and PIT event normalization."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


FIELDS = "ts_code,ann_date,end_date,holder_num"
PAGE_SIZE = 2000
PROTOCOL = "holder-concentration-backfill-v1"


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


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


def holder_count_partitions(
    start_date: str, end_date: str
) -> list[tuple[str, str]]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("holder_concentration_backfill_range")
    return [
        (left.strftime("%Y%m%d"), right.strftime("%Y%m%d"))
        for left, right in _month_partitions(start, end)
    ]


def _root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve() / "data" / "research"
        / "holder_concentration_structured" / "v1"
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _key(start: str, end: str) -> str:
    return f"stk_holdernumber:{start}:{end}"


def _partition_path(root: Path, start: str, end: str) -> Path:
    return root / "stk_holdernumber" / f"{start}_{end}.parquet"


def _load_manifest(root: Path, start: str, end: str) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != PROTOCOL
            or payload.get("start_date") != start
            or payload.get("end_date") != end
        ):
            raise ValueError("holder_concentration_manifest_conflict")
        return payload
    return {
        "protocol_version": PROTOCOL,
        "start_date": start,
        "end_date": end,
        "partitions": {},
    }


def _fetch_pages(client: Any, start: str, end: str) -> pd.DataFrame:
    pages: list[pd.DataFrame] = []
    offset = 0
    while True:
        frame = client.stk_holdernumber(
            start_date=start,
            end_date=end,
            limit=PAGE_SIZE,
            offset=offset,
            fields=FIELDS,
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("holder_concentration_response")
        pages.append(frame)
        if len(frame) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True, sort=False)


def _validate_partition(
    frame: pd.DataFrame, start: str, end: str
) -> pd.DataFrame:
    required = {"ts_code", "ann_date", "end_date", "holder_num"}
    if required.difference(frame.columns):
        raise ValueError("holder_concentration_shape")
    result = frame.copy()
    for column in ("ts_code", "ann_date", "end_date"):
        result[column] = result[column].astype("string")
    result["ann_date"] = result["ann_date"].map(_date_key)
    result["end_date"] = result["end_date"].map(_date_key)
    if not result.empty and not result["ann_date"].between(start, end).all():
        raise ValueError(f"holder_concentration_partition_leak:{start}:{end}")
    result = result.loc[
        result["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    return (
        result.sort_values(
            ["ts_code", "ann_date", "end_date", "holder_num"],
            kind="stable",
        )
        .drop_duplicates(keep="last")
        .reset_index(drop=True)
    )


def run_holder_concentration_backfill(
    repo_root: str | Path,
    client: Any,
    *,
    start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
    max_partitions: int | None = None,
) -> dict[str, Any]:
    root = _root(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    start_key, end_key = _date_key(start_date), _date_key(end_date)
    manifest = _load_manifest(root, start_key, end_key)
    completed = manifest["partitions"]
    partitions = holder_count_partitions(start_date, end_date)
    processed = 0
    fetched_rows = 0
    for start, end in partitions:
        key = _key(start, end)
        if key in completed:
            continue
        if max_partitions is not None and processed >= max(0, int(max_partitions)):
            break
        frame = _validate_partition(_fetch_pages(client, start, end), start, end)
        path = _partition_path(root, start, end)
        ResearchStore(root).write_parquet_atomic(path, frame)
        completed[key] = {
            "start_date": start,
            "end_date": end,
            "rows": int(len(frame)),
            "path": path.relative_to(Path(repo_root).resolve()).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        write_text_atomic(
            _manifest_path(root),
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        processed += 1
        fetched_rows += len(frame)
    return {
        "status": "complete" if len(completed) == len(partitions) else "in_progress",
        "processed_partitions": processed,
        "completed_partitions": len(completed),
        "total_partitions": len(partitions),
        "fetched_rows": int(fetched_rows),
        "manifest": str(_manifest_path(root)),
    }


def _safe_path(repo_root: Path, raw: object) -> Path:
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("holder_concentration_partition_path")
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ValueError("holder_concentration_partition_path")
    return path


def _load_raw(
    repo_root: Path, start_date: str, end_date: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = _root(repo_root)
    partitions = holder_count_partitions(start_date, end_date)
    expected = {_key(start, end) for start, end in partitions}
    if not _manifest_path(root).exists():
        return pd.DataFrame(), {
            "complete": False, "completed_partitions": 0,
            "total_partitions": len(partitions), "rows": 0,
        }
    manifest = _load_manifest(root, _date_key(start_date), _date_key(end_date))
    frames: list[pd.DataFrame] = []
    for start, end in partitions:
        key = _key(start, end)
        record = manifest["partitions"].get(key)
        if record is None:
            continue
        expected_path = _partition_path(root, start, end)
        if (
            record.get("start_date") != start
            or record.get("end_date") != end
            or record.get("path")
            != expected_path.relative_to(repo_root).as_posix()
        ):
            raise ValueError(f"holder_concentration_partition_record:{key}")
        path = _safe_path(repo_root, record.get("path"))
        if (
            not path.exists()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != str(record.get("sha256"))
        ):
            raise ValueError(f"holder_concentration_partition_tampered:{key}")
        frame = _validate_partition(pd.read_parquet(path), start, end)
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"holder_concentration_partition_rows:{key}")
        if not frame.empty:
            frames.append(frame)
    raw = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    return raw, {
        "complete": set(manifest["partitions"]) == expected,
        "completed_partitions": len(set(manifest["partitions"]).intersection(expected)),
        "total_partitions": len(partitions),
        "rows": int(len(raw)),
    }


def _previous_quarter_end(value: str) -> str:
    timestamp = pd.Timestamp(value)
    return (timestamp.to_period("Q") - 1).end_time.strftime("%Y%m%d")


def _load_listing_dates(
    repo_root: Path, snapshot_date: str
) -> dict[str, str]:
    snapshot_key = _date_key(snapshot_date)
    materialized = (
        repo_root / "data" / "research" / "raw" / "a_share"
        / snapshot_key / "stock_basic.parquet"
    )
    canonical = (
        repo_root / "data" / "shared" / "backtest_cache"
        / "stock_basic.csv"
    )
    if materialized.exists():
        frame = pd.read_parquet(
            materialized, columns=["ts_code", "list_date"]
        )
    elif canonical.exists():
        frame = pd.read_csv(
            canonical, usecols=["ts_code", "list_date"],
            dtype={"ts_code": str, "list_date": str},
        )
    else:
        raise FileNotFoundError("holder_concentration_stock_basic_missing")
    frame["ts_code"] = frame["ts_code"].astype("string")
    frame["list_date"] = (
        frame["list_date"].astype("string").map(_date_key)
    )
    valid = (
        frame["ts_code"].str.endswith((".SH", ".SZ"), na=False)
        & frame["list_date"].str.fullmatch(r"\d{8}", na=False)
    )
    frame = frame.loc[valid].drop_duplicates("ts_code", keep="last")
    return dict(
        zip(frame["ts_code"].astype(str), frame["list_date"].astype(str))
    )


def load_holder_concentration_events(
    repo_root: str | Path,
    *,
    start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
    snapshot_date: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(repo_root).resolve()
    raw, audit = _load_raw(root, start_date, end_date)
    if raw.empty:
        audit.update({"observations": 0, "ambiguous_quarters": 0, "events": 0})
        return pd.DataFrame(), audit
    frame = raw.copy()
    frame["holder_num"] = pd.to_numeric(frame["holder_num"], errors="coerce")
    frame = frame.loc[frame["holder_num"].gt(0)].copy()
    parsed_end = pd.to_datetime(frame["end_date"], format="%Y%m%d", errors="coerce")
    frame = frame.loc[
        parsed_end.notna()
        & parsed_end.dt.is_quarter_end
        & frame["ann_date"].str.fullmatch(r"\d{8}", na=False)
    ].copy()
    earliest = (
        frame.groupby(["ts_code", "end_date"])["ann_date"]
        .transform("min")
    )
    earliest_rows = frame.loc[frame["ann_date"].eq(earliest)].copy()
    conflicts = (
        earliest_rows.groupby(["ts_code", "end_date"])["holder_num"]
        .nunique()
    )
    valid_keys = conflicts.loc[conflicts.eq(1)].index
    observations = (
        earliest_rows.set_index(["ts_code", "end_date"])
        .loc[valid_keys]
        .reset_index()
        .drop_duplicates(["ts_code", "end_date"], keep="first")
        .sort_values(["ts_code", "end_date"], kind="stable")
    )
    lookup = {
        (str(row.ts_code), str(row.end_date)): row
        for row in observations.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    listing_dates = (
        _load_listing_dates(root, str(snapshot_date))
        if snapshot_date is not None
        else None
    )
    pre_listing_excluded = 0
    for current in observations.itertuples(index=False):
        previous_end = _previous_quarter_end(str(current.end_date))
        previous = lookup.get((str(current.ts_code), previous_end))
        if previous is None or str(previous.ann_date) > str(current.ann_date):
            continue
        if listing_dates is not None:
            list_date = listing_dates.get(str(current.ts_code))
            if (
                list_date is None
                or str(current.end_date) < list_date
                or previous_end < list_date
            ):
                pre_listing_excluded += 1
                continue
        change = float(current.holder_num) / float(previous.holder_num) - 1.0
        if change < 0.0:
            family = "holder_concentration"
        elif change > 0.0:
            family = "holder_dispersion"
        else:
            continue
        rows.append({
            "event_id": hashlib.sha256(
                f"holder-count|{current.ts_code}|{current.end_date}|{current.ann_date}".encode()
            ).hexdigest()[:24],
            "family": family,
            "code": str(current.ts_code).split(".")[0],
            "ann_date": str(current.ann_date),
            "available_at": (
                pd.Timestamp(str(current.ann_date))
                .tz_localize("Asia/Shanghai")
                .replace(hour=16)
                .tz_convert("UTC")
                .isoformat()
            ),
            "end_date": str(current.end_date),
            "previous_end_date": previous_end,
            "holder_num": float(current.holder_num),
            "previous_holder_num": float(previous.holder_num),
            "holder_count_change": change,
            "materiality": abs(change),
            "eligible": True,
            "source": "tushare_stk_holdernumber",
        })
    events = pd.DataFrame(rows)
    audit.update({
        "observations": int(len(observations)),
        "ambiguous_quarters": int(conflicts.gt(1).sum()),
        "pre_listing_pairs_excluded": int(pre_listing_excluded),
        "events": int(len(events)),
    })
    return events, audit
