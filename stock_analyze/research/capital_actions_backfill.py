"""Resumable structured repurchase and holder-trade research inputs."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


REPURCHASE_FIELDS = (
    "ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit"
)
HOLDER_TRADE_FIELDS = (
    "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,"
    "after_share,after_ratio,avg_price,total_share"
)
ENDPOINTS = ("repurchase", "stk_holdertrade")
PAGE_SIZE = 2000


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


def capital_action_partitions(
    start_date: str,
    end_date: str,
) -> list[tuple[str, str, str]]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("capital_actions_backfill_range")
    return [
        (endpoint, left.strftime("%Y%m%d"), right.strftime("%Y%m%d"))
        for endpoint in ENDPOINTS
        for left, right in _month_partitions(start, end)
    ]


def _root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve() / "data" / "research"
        / "capital_actions_structured" / "v1"
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _partition_key(endpoint: str, start: str, end: str) -> str:
    return f"{endpoint}:{start}:{end}"


def _partition_path(root: Path, endpoint: str, start: str, end: str) -> Path:
    return root / endpoint / f"{start}_{end}.parquet"


def _load_manifest(root: Path, start: str, end: str) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != "capital-actions-backfill-v1"
            or payload.get("start_date") != start
            or payload.get("end_date") != end
        ):
            raise ValueError("capital_actions_manifest_conflict")
        return payload
    return {
        "protocol_version": "capital-actions-backfill-v1",
        "start_date": start,
        "end_date": end,
        "partitions": {},
    }


def _fetch_pages(
    client: Any,
    endpoint: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    fields = REPURCHASE_FIELDS if endpoint == "repurchase" else HOLDER_TRADE_FIELDS
    pages: list[pd.DataFrame] = []
    offset = 0
    while True:
        frame = getattr(client, endpoint)(
            start_date=start,
            end_date=end,
            limit=PAGE_SIZE,
            offset=offset,
            fields=fields,
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"capital_actions_response:{endpoint}")
        pages.append(frame)
        if len(frame) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True, sort=False) if pages else pd.DataFrame()


def _validate_partition(
    frame: pd.DataFrame,
    endpoint: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    required = (
        {"ts_code", "ann_date", "proc", "amount", "vol"}
        if endpoint == "repurchase"
        else {
            "ts_code", "ann_date", "holder_type", "in_de",
            "change_vol", "change_ratio", "total_share",
        }
    )
    if required.difference(frame.columns):
        raise ValueError(f"capital_actions_shape:{endpoint}")
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype("string")
    result["ann_date"] = result["ann_date"].astype("string").map(_date_key)
    if not result.empty and not result["ann_date"].between(start, end).all():
        raise ValueError(f"capital_actions_partition_leak:{endpoint}:{start}:{end}")
    result = result.loc[
        result["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    sort_columns = (
        ["ts_code", "ann_date", "end_date", "proc"]
        if endpoint == "repurchase"
        else ["ts_code", "ann_date", "holder_type", "in_de", "holder_name"]
    )
    available = [column for column in sort_columns if column in result]
    # Provider pages can overlap, so remove exact duplicate records only.
    # Do not collapse distinct same-holder/same-day trades before the frozen
    # aggregation rule has summed their materiality.
    return (
        result.sort_values(available, kind="stable")
        .drop_duplicates(keep="last")
        .reset_index(drop=True)
    )


def run_capital_actions_backfill(
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
    processed = 0
    fetched_rows = 0
    partitions = capital_action_partitions(start_date, end_date)
    for endpoint, start, end in partitions:
        key = _partition_key(endpoint, start, end)
        if key in completed:
            continue
        if max_partitions is not None and processed >= max(0, int(max_partitions)):
            break
        frame = _validate_partition(
            _fetch_pages(client, endpoint, start, end), endpoint, start, end
        )
        path = _partition_path(root, endpoint, start, end)
        ResearchStore(root).write_parquet_atomic(path, frame)
        completed[key] = {
            "endpoint": endpoint,
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


def _safe_partition_path(repo_root: Path, raw: object) -> Path:
    relative = Path(str(raw))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("capital_actions_partition_path")
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ValueError("capital_actions_partition_path")
    return path


def _load_frames(
    repo_root: Path,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    root = _root(repo_root)
    partitions = capital_action_partitions(start_date, end_date)
    if not _manifest_path(root).exists():
        return {}, {"complete": False, "completed_partitions": 0, "total_partitions": len(partitions)}
    manifest = _load_manifest(root, _date_key(start_date), _date_key(end_date))
    by_endpoint: dict[str, list[pd.DataFrame]] = {endpoint: [] for endpoint in ENDPOINTS}
    expected_keys = {
        _partition_key(endpoint, start, end)
        for endpoint, start, end in partitions
    }
    for endpoint, start, end in partitions:
        key = _partition_key(endpoint, start, end)
        record = manifest["partitions"].get(key)
        if record is None:
            continue
        expected_path = _partition_path(root, endpoint, start, end)
        expected_relative = expected_path.relative_to(repo_root).as_posix()
        if (
            record.get("endpoint") != endpoint
            or record.get("start_date") != start
            or record.get("end_date") != end
            or record.get("path") != expected_relative
        ):
            raise ValueError(f"capital_actions_partition_record:{key}")
        path = _safe_partition_path(repo_root, record.get("path"))
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != str(record.get("sha256")):
            raise ValueError(f"capital_actions_partition_tampered:{key}")
        frame = _validate_partition(pd.read_parquet(path), endpoint, start, end)
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"capital_actions_partition_rows:{key}")
        if not frame.empty:
            by_endpoint[endpoint].append(frame)
    frames = {
        endpoint: pd.concat(items, ignore_index=True, sort=False)
        if items else pd.DataFrame()
        for endpoint, items in by_endpoint.items()
    }
    return frames, {
        "complete": set(manifest["partitions"]) == expected_keys,
        "completed_partitions": len(
            set(manifest["partitions"]).intersection(expected_keys)
        ),
        "total_partitions": len(partitions),
        "rows": int(sum(len(frame) for frame in frames.values())),
    }


def _latest_market_values(
    repo_root: Path,
    requested: pd.DataFrame,
) -> dict[tuple[str, str], float]:
    daily_root = repo_root / "data" / "shared" / "backtest_cache" / "daily_basic"
    available = sorted(
        (path.stem.replace("-", ""), path) for path in daily_root.glob("*.csv")
    )
    if not available:
        return {}
    dates = [item[0] for item in available]
    result: dict[tuple[str, str], float] = {}
    import bisect
    for ann_date, group in requested.groupby("ann_date", sort=True):
        position = bisect.bisect_right(dates, str(ann_date)) - 1
        if position < 0:
            continue
        frame = pd.read_csv(
            available[position][1],
            usecols=["ts_code", "total_mv"],
            dtype={"ts_code": str},
        )
        frame = frame.loc[frame["ts_code"].isin(set(group["ts_code"].astype(str)))]
        for _, row in frame.iterrows():
            value = pd.to_numeric(pd.Series([row["total_mv"]]), errors="coerce").iloc[0]
            if pd.notna(value) and float(value) > 0:
                result[(str(row["ts_code"]), str(ann_date))] = float(value) * 10000.0
    return result


def load_capital_action_events(
    repo_root: str | Path,
    *,
    start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(repo_root).resolve()
    frames, audit = _load_frames(root, start_date, end_date)
    repurchase = frames.get("repurchase", pd.DataFrame()).copy()
    holder = frames.get("stk_holdertrade", pd.DataFrame()).copy()
    rows: list[dict[str, Any]] = []
    if not repurchase.empty:
        repurchase["ann_date"] = repurchase["ann_date"].astype("string").map(_date_key)
        market_values = _latest_market_values(root, repurchase[["ts_code", "ann_date"]])
        proc_family = {
            "完成": "repurchase_completed",
            "预案": "repurchase_plan",
            "股东大会通过": "repurchase_plan",
            "实施": "repurchase_implemented",
            "停止": "repurchase_stopped",
        }
        repurchase["family"] = repurchase["proc"].astype(str).map(proc_family)
        repurchase["amount"] = pd.to_numeric(
            repurchase["amount"], errors="coerce"
        )
        # One stock-day is one tradable announcement. Multiple disclosed
        # programmes in the same lifecycle family are summed before applying
        # materiality so their shared return is never duplicated.
        grouped_repurchase = (
            repurchase.dropna(subset=["family"])
            .groupby(["ts_code", "ann_date", "family"], sort=True)
            .agg(amount=("amount", lambda values: values.sum(min_count=1)))
            .reset_index()
        )
        for _, row in grouped_repurchase.iterrows():
            family = str(row["family"])
            amount = row["amount"]
            market_value = market_values.get((str(row["ts_code"]), str(row["ann_date"])))
            ratio = float(amount) / market_value if pd.notna(amount) and market_value else np.nan
            rows.append({
                "event_id": hashlib.sha256(
                    f"repurchase|{row['ts_code']}|{row['ann_date']}|{family}".encode()
                ).hexdigest()[:24],
                "family": family,
                "code": str(row["ts_code"]).split(".")[0],
                "ann_date": str(row["ann_date"]),
                "available_at": pd.Timestamp(str(row["ann_date"])).tz_localize("Asia/Shanghai").replace(hour=16).tz_convert("UTC").isoformat(),
                "materiality": ratio,
                "eligible": bool(family == "repurchase_completed" and pd.notna(ratio)),
                "source": "tushare_repurchase",
            })
    if not holder.empty:
        holder["ann_date"] = holder["ann_date"].astype("string").map(_date_key)
        for column in ("change_ratio", "change_vol", "total_share"):
            holder[column] = pd.to_numeric(holder[column], errors="coerce")
        grouped = holder.groupby(
            ["ts_code", "ann_date", "holder_type", "in_de"],
            dropna=False,
            sort=True,
        ).agg(
            change_ratio=("change_ratio", "sum"),
            change_vol=("change_vol", "sum"),
            total_share=("total_share", "max"),
        ).reset_index()
        holder_name = {"C": "company", "G": "management", "P": "individual"}
        direction_name = {"IN": "increase", "DE": "decrease"}
        for _, row in grouped.iterrows():
            subject = holder_name.get(str(row["holder_type"]))
            direction = direction_name.get(str(row["in_de"]))
            if subject is None or direction is None:
                continue
            ratio = abs(float(row["change_ratio"])) / 100.0 if pd.notna(row["change_ratio"]) else np.nan
            rows.append({
                "event_id": hashlib.sha256(f"holder|{row['ts_code']}|{row['ann_date']}|{subject}|{direction}".encode()).hexdigest()[:24],
                "family": f"holder_{subject}_{direction}",
                "code": str(row["ts_code"]).split(".")[0],
                "ann_date": str(row["ann_date"]),
                "available_at": pd.Timestamp(str(row["ann_date"])).tz_localize("Asia/Shanghai").replace(hour=16).tz_convert("UTC").isoformat(),
                "materiality": ratio,
                # Decreases are retained for risk diagnostics only.  The
                # preregistered long-only candidate set contains increases.
                "eligible": bool(direction == "increase" and pd.notna(ratio)),
                "source": "tushare_stk_holdertrade",
            })
    events = pd.DataFrame(rows)
    audit["events"] = int(len(events))
    return events, audit
