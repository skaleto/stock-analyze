"""Resumable restricted-share unlock history and PIT normalization."""

from __future__ import annotations

from datetime import date, timedelta
import bisect
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


FIELDS = (
    "ts_code,ann_date,float_date,float_share,float_ratio,holder_name,share_type"
)
PAGE_SIZE = 2000
PROTOCOL = "share-unlock-backfill-v1"


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


def share_unlock_partitions(
    start_date: str, end_date: str
) -> list[tuple[str, str]]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("share_unlock_backfill_range")
    return [
        (left.strftime("%Y%m%d"), right.strftime("%Y%m%d"))
        for left, right in _month_partitions(start, end)
    ]


def _root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve() / "data" / "research"
        / "share_unlock_structured" / "v1"
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _key(start: str, end: str) -> str:
    return f"share_float:{start}:{end}"


def _partition_path(root: Path, start: str, end: str) -> Path:
    return root / "share_float" / f"{start}_{end}.parquet"


def _load_manifest(root: Path, start: str, end: str) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != PROTOCOL
            or payload.get("start_date") != start
            or payload.get("end_date") != end
        ):
            raise ValueError("share_unlock_manifest_conflict")
        return payload
    return {
        "protocol_version": PROTOCOL, "start_date": start,
        "end_date": end, "partitions": {},
    }


def _fetch_range_pages(
    client: Any, start: str, end: str, *, ann_date: str | None = None
) -> pd.DataFrame:
    pages: list[pd.DataFrame] = []
    offset = 0
    while True:
        params: dict[str, Any] = {
            "start_date": start, "end_date": end, "limit": PAGE_SIZE,
            "offset": offset, "fields": FIELDS,
        }
        if ann_date is not None:
            params["ann_date"] = ann_date
        frame = client.share_float(**params)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("share_unlock_response")
        pages.append(frame)
        if len(frame) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True, sort=False)


def _fetch_day_confirmation_shards(
    client: Any, key: str
) -> pd.DataFrame:
    confirmed: list[pd.DataFrame] = []
    current = date.fromisoformat(f"{key[:4]}-{key[4:6]}-{key[6:8]}")
    ann_current = current - timedelta(days=30)
    while ann_current <= current:
        ann_key = ann_current.strftime("%Y%m%d")
        shard = _fetch_range_pages(client, key, key, ann_date=ann_key)
        if not shard.empty:
            confirmed.append(shard)
        ann_current += timedelta(days=1)
    return (
        pd.concat(confirmed, ignore_index=True, sort=False)
        if confirmed
        else pd.DataFrame(columns=FIELDS.split(","))
    )


def _fetch_pages(client: Any, start: str, end: str) -> pd.DataFrame:
    try:
        return _fetch_range_pages(client, start, end)
    except Exception:
        if start == end:
            return _fetch_day_confirmation_shards(client, start)
        # Some high-volume months exceed the provider's deep-pagination
        # boundary. Fall back to natural-day queries and still persist one
        # monthly atomic partition. A failing day remains fail-closed.
        left = date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:8]}")
        right = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:8]}")
        frames: list[pd.DataFrame] = []
        current = left
        while current <= right:
            key = current.strftime("%Y%m%d")
            try:
                frame = _fetch_range_pages(client, key, key)
            except Exception:
                # A single unlock day can itself exceed the provider's deep
                # pagination boundary. The protocol only retains confirmations
                # from the preceding 30 calendar days, so split that exact day
                # by each eligible ann_date and paginate every shard.
                frame = _fetch_day_confirmation_shards(client, key)
            if not frame.empty:
                frames.append(frame)
            current += timedelta(days=1)
        return (
            pd.concat(frames, ignore_index=True, sort=False)
            if frames
            else pd.DataFrame(columns=FIELDS.split(","))
        )


def _validate_partition(
    frame: pd.DataFrame, start: str, end: str
) -> pd.DataFrame:
    required = {
        "ts_code", "ann_date", "float_date", "float_share",
        "float_ratio", "holder_name", "share_type",
    }
    if required.difference(frame.columns):
        raise ValueError("share_unlock_shape")
    result = frame.copy()
    for column in ("ts_code", "ann_date", "float_date", "holder_name", "share_type"):
        result[column] = result[column].astype("string")
    result["ann_date"] = result["ann_date"].map(_date_key)
    result["float_date"] = result["float_date"].map(_date_key)
    if not result.empty and not result["float_date"].between(start, end).all():
        raise ValueError(f"share_unlock_partition_leak:{start}:{end}")
    result = result.loc[
        result["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    return (
        result.sort_values(
            ["ts_code", "float_date", "share_type", "ann_date", "holder_name"],
            kind="stable",
        ).drop_duplicates(keep="last").reset_index(drop=True)
    )


def run_share_unlock_backfill(
    repo_root: str | Path, client: Any, *,
    start_date: str = "2018-01-01", end_date: str = "2024-12-31",
    max_partitions: int | None = None,
) -> dict[str, Any]:
    root = _root(repo_root); root.mkdir(parents=True, exist_ok=True)
    start_key, end_key = _date_key(start_date), _date_key(end_date)
    manifest = _load_manifest(root, start_key, end_key)
    completed = manifest["partitions"]
    partitions = share_unlock_partitions(start_date, end_date)
    processed = fetched_rows = 0
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
            "start_date": start, "end_date": end, "rows": int(len(frame)),
            "path": path.relative_to(Path(repo_root).resolve()).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        write_text_atomic(
            _manifest_path(root),
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        processed += 1; fetched_rows += len(frame)
    return {
        "status": "complete" if len(completed) == len(partitions) else "in_progress",
        "processed_partitions": processed, "completed_partitions": len(completed),
        "total_partitions": len(partitions), "fetched_rows": int(fetched_rows),
        "manifest": str(_manifest_path(root)),
    }


def _safe_path(repo_root: Path, raw: object) -> Path:
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("share_unlock_partition_path")
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ValueError("share_unlock_partition_path")
    return path


def _load_raw(
    repo_root: Path, start_date: str, end_date: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = _root(repo_root); partitions = share_unlock_partitions(start_date, end_date)
    expected = {_key(start, end) for start, end in partitions}
    if not _manifest_path(root).exists():
        return pd.DataFrame(), {"complete": False, "completed_partitions": 0, "total_partitions": len(partitions), "rows": 0}
    manifest = _load_manifest(root, _date_key(start_date), _date_key(end_date))
    frames: list[pd.DataFrame] = []
    for start, end in partitions:
        key = _key(start, end); record = manifest["partitions"].get(key)
        if record is None: continue
        expected_path = _partition_path(root, start, end)
        if (record.get("start_date") != start or record.get("end_date") != end or record.get("path") != expected_path.relative_to(repo_root).as_posix()):
            raise ValueError(f"share_unlock_partition_record:{key}")
        path = _safe_path(repo_root, record.get("path"))
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != str(record.get("sha256")):
            raise ValueError(f"share_unlock_partition_tampered:{key}")
        frame = _validate_partition(pd.read_parquet(path), start, end)
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"share_unlock_partition_rows:{key}")
        if not frame.empty: frames.append(frame)
    raw = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    return raw, {
        "complete": set(manifest["partitions"]) == expected,
        "completed_partitions": len(set(manifest["partitions"]).intersection(expected)),
        "total_partitions": len(partitions), "rows": int(len(raw)),
    }


def _normalize_raw_partition(
    raw: pd.DataFrame, confirmation_days: int
) -> tuple[pd.DataFrame, int, int]:
    """Reduce one unlock-month partition to stock-date aggregates."""

    if raw.empty:
        return pd.DataFrame(), 0, 0
    frame = raw.copy()
    for column in ("float_share", "float_ratio"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    announced = pd.to_datetime(
        frame["ann_date"], format="%Y%m%d", errors="coerce"
    )
    floating = pd.to_datetime(
        frame["float_date"], format="%Y%m%d", errors="coerce"
    )
    age = (floating - announced).dt.days
    valid_age = (
        announced.notna()
        & floating.notna()
        & age.between(0, int(confirmation_days))
    )
    stale_rows = int((~valid_age).sum())
    frame = frame.loc[
        valid_age & frame["float_share"].gt(0)
    ].copy()
    if frame.empty:
        return pd.DataFrame(), stale_rows, 0
    latest = frame.groupby(
        ["ts_code", "float_date", "share_type"]
    )["ann_date"].transform("max")
    frame = frame.loc[frame["ann_date"].eq(latest)].copy()
    frame["holder_name"] = (
        frame["holder_name"].astype("string").str.strip()
    )
    invalid_keys: set[tuple[str, str, str]] = set()
    group_columns = ["ts_code", "float_date", "share_type"]
    for key, group in frame.groupby(group_columns, dropna=False):
        normalized_key = tuple(str(value) for value in key)
        if (
            group["holder_name"].isna().any()
            or group["holder_name"].eq("").any()
        ):
            invalid_keys.add(normalized_key)
            continue
        conflicts = group.groupby("holder_name")[[
            "float_share", "float_ratio"
        ]].nunique(dropna=False)
        if bool(conflicts.gt(1).any(axis=None)):
            invalid_keys.add(normalized_key)
    keys = list(zip(
        frame["ts_code"].astype(str),
        frame["float_date"].astype(str),
        frame["share_type"].astype(str),
    ))
    frame = frame.loc[
        [key not in invalid_keys for key in keys]
    ].copy()
    frame = frame.drop_duplicates(
        [*group_columns, "holder_name"], keep="last"
    )
    tranches = frame.groupby(group_columns, sort=True).agg(
        ann_date=("ann_date", "max"),
        unlocked_shares=("float_share", "sum"),
        reported_ratio_pct=(
            "float_ratio", lambda values: values.sum(min_count=1)
        ),
        holders=("holder_name", "nunique"),
    ).reset_index()
    aggregated = tranches.groupby(
        ["ts_code", "float_date"], sort=True
    ).agg(
        confirmation_date=("ann_date", "max"),
        unlocked_shares=("unlocked_shares", "sum"),
        reported_ratio_pct=(
            "reported_ratio_pct", lambda values: values.sum(min_count=1)
        ),
        tranche_count=("share_type", "nunique"),
        holder_count=("holders", "sum"),
    ).reset_index()
    return aggregated, stale_rows, len(invalid_keys)


def _load_normalized_months(
    repo_root: Path,
    start_date: str,
    end_date: str,
    confirmation_days: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Verify and normalize partitions one at a time to bound memory."""

    root = _root(repo_root)
    partitions = share_unlock_partitions(start_date, end_date)
    expected = {_key(start, end) for start, end in partitions}
    if not _manifest_path(root).exists():
        return pd.DataFrame(), {
            "complete": False, "completed_partitions": 0,
            "total_partitions": len(partitions), "rows": 0,
            "stale_rows_excluded": 0, "invalid_tranches": 0,
        }
    manifest = _load_manifest(
        root, _date_key(start_date), _date_key(end_date)
    )
    aggregates: list[pd.DataFrame] = []
    raw_rows = stale_rows = invalid_tranches = 0
    for start, end in partitions:
        key = _key(start, end)
        record = manifest["partitions"].get(key)
        if record is None:
            continue
        expected_path = _partition_path(root, start, end)
        expected_relative = expected_path.relative_to(repo_root).as_posix()
        if (
            record.get("start_date") != start
            or record.get("end_date") != end
            or record.get("path") != expected_relative
        ):
            raise ValueError(f"share_unlock_partition_record:{key}")
        path = _safe_path(repo_root, record.get("path"))
        if (
            not path.exists()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != str(record.get("sha256"))
        ):
            raise ValueError(f"share_unlock_partition_tampered:{key}")
        frame = _validate_partition(
            pd.read_parquet(path), start, end
        )
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"share_unlock_partition_rows:{key}")
        raw_rows += len(frame)
        normalized, stale, invalid = _normalize_raw_partition(
            frame, confirmation_days
        )
        stale_rows += stale
        invalid_tranches += invalid
        if not normalized.empty:
            aggregates.append(normalized)
    aggregated = (
        pd.concat(aggregates, ignore_index=True, sort=False)
        if aggregates else pd.DataFrame()
    )
    return aggregated, {
        "complete": set(manifest["partitions"]) == expected,
        "completed_partitions": len(
            set(manifest["partitions"]).intersection(expected)
        ),
        "total_partitions": len(partitions),
        "rows": int(raw_rows),
        "stale_rows_excluded": int(stale_rows),
        "invalid_tranches": int(invalid_tranches),
    }


def _pit_total_shares(repo_root: Path, events: pd.DataFrame) -> dict[tuple[str, str], float]:
    daily_root = repo_root / "data" / "shared" / "backtest_cache" / "daily_basic"
    available = sorted((p.stem.replace("-", ""), p) for p in daily_root.glob("*.csv"))
    dates = [item[0] for item in available]; result: dict[tuple[str, str], float] = {}
    for float_date, group in events.groupby("float_date", sort=True):
        position = bisect.bisect_right(dates, str(float_date)) - 1
        if position < 0: continue
        frame = pd.read_csv(available[position][1], usecols=["ts_code", "total_share"], dtype={"ts_code": str})
        frame = frame.loc[frame["ts_code"].isin(set(group["ts_code"].astype(str)))]
        for row in frame.itertuples(index=False):
            value = pd.to_numeric(pd.Series([row.total_share]), errors="coerce").iloc[0]
            if pd.notna(value) and float(value) > 0:
                result[(str(row.ts_code), str(float_date))] = float(value) * 10000.0
    return result


def load_share_unlock_events(
    repo_root: str | Path, *, start_date: str = "2018-01-01",
    end_date: str = "2024-12-31", confirmation_days: int = 30,
    maximum_ratio_disagreement: float = 0.01,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(repo_root).resolve()
    aggregated, audit = _load_normalized_months(
        root, start_date, end_date, confirmation_days
    )
    if aggregated.empty:
        audit.setdefault("stale_rows_excluded", 0)
        audit.setdefault("invalid_tranches", 0)
        audit.update({
            "events": 0, "normalized_stock_dates": 0,
            "missing_denominators": 0, "ratio_disagreements": 0,
        })
        return pd.DataFrame(), audit
    total_shares = _pit_total_shares(root, aggregated)
    rows: list[dict[str, Any]] = []; disagreements = missing_denominator = 0
    for event in aggregated.itertuples(index=False):
        denominator = total_shares.get((str(event.ts_code), str(event.float_date)))
        if not denominator: missing_denominator += 1; continue
        ratio = float(event.unlocked_shares) / denominator
        reported = float(event.reported_ratio_pct) / 100.0 if pd.notna(event.reported_ratio_pct) else np.nan
        if not (0.0 < ratio <= 1.0) or not np.isfinite(reported) or reported <= 0.0 or abs(ratio - reported) > float(maximum_ratio_disagreement):
            disagreements += 1; continue
        rows.append({
            "event_id": hashlib.sha256(f"share-unlock|{event.ts_code}|{event.float_date}".encode()).hexdigest()[:24],
            "family": "share_unlock", "code": str(event.ts_code).split(".")[0],
            "ann_date": str(event.float_date), "float_date": str(event.float_date),
            "confirmation_date": str(event.confirmation_date),
            "available_at": pd.Timestamp(str(event.float_date)).tz_localize("Asia/Shanghai").replace(hour=16).tz_convert("UTC").isoformat(),
            "unlocked_shares": float(event.unlocked_shares), "total_shares": denominator,
            "unlock_ratio": ratio, "reported_ratio": reported,
            "materiality": ratio, "eligible": True,
            "tranche_count": int(event.tranche_count), "holder_count": int(event.holder_count),
            "source": "tushare_share_float",
        })
    events = pd.DataFrame(rows)
    audit.update({
        "normalized_stock_dates": int(len(aggregated)), "missing_denominators": missing_denominator,
        "ratio_disagreements": disagreements, "events": int(len(events)),
    })
    return events, audit
