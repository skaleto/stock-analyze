"""Resumable A-share block-trade history and PIT event normalization."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


FIELDS = "ts_code,trade_date,price,vol,amount,buyer,seller"
PAGE_SIZE = 2000
PROTOCOL = "block-trade-backfill-v1"


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def block_trade_partitions(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("block_trade_backfill_range")
    result: list[str] = []
    current = start
    while current <= end:
        result.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return result


def _root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve() / "data" / "research"
        / "block_trade_structured" / "v1"
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _key(day: str) -> str:
    return f"block_trade:{day}"


def _partition_path(root: Path, day: str) -> Path:
    return root / "block_trade" / f"{day}.parquet"


def _load_manifest(root: Path, start: str, end: str) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != PROTOCOL
            or payload.get("start_date") != start
            or payload.get("end_date") != end
        ):
            raise ValueError("block_trade_manifest_conflict")
        return payload
    return {
        "protocol_version": PROTOCOL, "start_date": start,
        "end_date": end, "partitions": {},
    }


def _fetch_day(client: Any, day: str) -> pd.DataFrame:
    pages: list[pd.DataFrame] = []
    offset = 0
    while True:
        frame = client.block_trade(
            trade_date=day, limit=PAGE_SIZE, offset=offset, fields=FIELDS
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("block_trade_response")
        pages.append(frame)
        if len(frame) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True, sort=False)


def _validate_partition(frame: pd.DataFrame, day: str) -> pd.DataFrame:
    required = {
        "ts_code", "trade_date", "price", "vol", "amount",
        "buyer", "seller",
    }
    if required.difference(frame.columns):
        raise ValueError("block_trade_shape")
    result = frame.copy()
    for column in ("ts_code", "trade_date", "buyer", "seller"):
        result[column] = result[column].astype("string")
    result["trade_date"] = result["trade_date"].map(_date_key)
    if not result.empty and not result["trade_date"].eq(day).all():
        raise ValueError(f"block_trade_partition_leak:{day}")
    result = result.loc[
        result["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    return (
        result.sort_values(
            ["ts_code", "trade_date", "price", "vol", "amount",
             "buyer", "seller"], kind="stable",
        ).drop_duplicates(keep="last").reset_index(drop=True)
    )


def run_block_trade_backfill(
    repo_root: str | Path, client: Any, *,
    start_date: str = "2018-01-01", end_date: str = "2024-12-31",
    max_partitions: int | None = None,
) -> dict[str, Any]:
    root = _root(repo_root); root.mkdir(parents=True, exist_ok=True)
    start_key, end_key = _date_key(start_date), _date_key(end_date)
    manifest = _load_manifest(root, start_key, end_key)
    completed = manifest["partitions"]
    partitions = block_trade_partitions(start_date, end_date)
    processed = fetched_rows = 0
    for day in partitions:
        key = _key(day)
        if key in completed:
            continue
        if max_partitions is not None and processed >= max(0, int(max_partitions)):
            break
        frame = _validate_partition(_fetch_day(client, day), day)
        path = _partition_path(root, day)
        ResearchStore(root).write_parquet_atomic(path, frame)
        completed[key] = {
            "day": day, "rows": int(len(frame)),
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
        raise ValueError("block_trade_partition_path")
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ValueError("block_trade_partition_path")
    return path


def _history_paths(repo_root: Path, snapshot_date: str) -> dict[str, Path]:
    snapshot_key = _date_key(snapshot_date)
    manifest_path = (
        repo_root / "data" / "research" / "raw" / "a_share"
        / snapshot_key / "materialization_manifest.json"
    )
    if not manifest_path.exists():
        raise FileNotFoundError("block_trade_materialization_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("block_trade_materialization_incomplete")
    result: dict[str, Path] = {}
    for record in (manifest.get("outputs") or {}).values():
        relative = Path(str(record.get("path") or ""))
        match = re.search(r"history_(\d{6})_", relative.name)
        if match is None:
            continue
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("block_trade_history_path")
        path = (repo_root / relative).resolve()
        if not path.is_relative_to(repo_root) or not path.exists():
            raise ValueError("block_trade_history_path")
        result[match.group(1)] = path
    return result


def _raw_closes(
    repo_root: Path, snapshot_date: str, requested: pd.DataFrame
) -> dict[tuple[str, str], float]:
    paths = _history_paths(repo_root, snapshot_date)
    result: dict[tuple[str, str], float] = {}
    for code, group in requested.groupby("code"):
        path = paths.get(str(code))
        if path is None:
            continue
        dates = set(group["trade_date"].astype(str))
        frame = pd.read_csv(
            path, usecols=["trade_date", "close"],
            dtype={"trade_date": str},
        )
        frame["trade_date"] = frame["trade_date"].astype("string").map(_date_key)
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.loc[
            frame["trade_date"].isin(dates) & frame["close"].gt(0)
        ].drop_duplicates("trade_date", keep="last")
        for row in frame.itertuples(index=False):
            result[(str(code), str(row.trade_date))] = float(row.close)
    return result


def _market_caps(
    repo_root: Path, requested: pd.DataFrame
) -> dict[tuple[str, str], float]:
    daily_root = repo_root / "data" / "shared" / "backtest_cache" / "daily_basic"
    by_key = {path.stem.replace("-", ""): path for path in daily_root.glob("*.csv")}
    result: dict[tuple[str, str], float] = {}
    for trade_date, group in requested.groupby("trade_date"):
        path = by_key.get(str(trade_date))
        if path is None:
            continue
        frame = pd.read_csv(
            path, usecols=["ts_code", "total_mv"], dtype={"ts_code": str}
        )
        frame = frame.loc[frame["ts_code"].isin(set(group["ts_code"].astype(str)))]
        for row in frame.itertuples(index=False):
            value = pd.to_numeric(pd.Series([row.total_mv]), errors="coerce").iloc[0]
            if pd.notna(value) and float(value) > 0:
                result[(str(row.ts_code), str(trade_date))] = float(value)
    return result


def load_block_trade_events(
    repo_root: str | Path, *, snapshot_date: str,
    start_date: str = "2018-01-01", end_date: str = "2024-12-31",
    maximum_amount_absolute_error_wan: float = 5.0,
    maximum_amount_relative_error: float = 0.001,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(repo_root).resolve(); store = _root(root)
    partitions = block_trade_partitions(start_date, end_date)
    expected = {_key(day) for day in partitions}
    if not _manifest_path(store).exists():
        return pd.DataFrame(), {"complete":False,"completed_partitions":0,"total_partitions":len(partitions),"rows":0,"events":0}
    manifest = _load_manifest(store, _date_key(start_date), _date_key(end_date))
    aggregated_parts: list[pd.DataFrame] = []; rows = invalid_stock_days = 0
    for day in partitions:
        key = _key(day); record = manifest["partitions"].get(key)
        if record is None: continue
        expected_path = _partition_path(store, day)
        if record.get("day") != day or record.get("path") != expected_path.relative_to(root).as_posix():
            raise ValueError(f"block_trade_partition_record:{key}")
        path = _safe_path(root, record.get("path"))
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != str(record.get("sha256")):
            raise ValueError(f"block_trade_partition_tampered:{key}")
        frame = _validate_partition(pd.read_parquet(path), day)
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"block_trade_partition_rows:{key}")
        rows += len(frame)
        for column in ("price", "vol", "amount"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["unit_error"] = (frame["price"] * frame["vol"] - frame["amount"]).abs()
        valid_row = (
            frame["price"].gt(0) & frame["vol"].gt(0) & frame["amount"].gt(0)
            & frame["unit_error"].le(
                frame["amount"].mul(float(maximum_amount_relative_error)).clip(
                    lower=float(maximum_amount_absolute_error_wan)
                )
            )
        )
        bad_codes = set(frame.loc[~valid_row, "ts_code"].astype(str))
        invalid_stock_days += len(bad_codes)
        frame = frame.loc[valid_row & ~frame["ts_code"].astype(str).isin(bad_codes)].copy()
        if frame.empty: continue
        grouped = frame.groupby(["ts_code", "trade_date"], sort=True).agg(
            volume_wan=("vol", "sum"), amount_wan=("amount", "sum"),
            trade_count=("price", "size"), buyer_count=("buyer", "nunique"),
            seller_count=("seller", "nunique"),
        ).reset_index()
        grouped["block_vwap"] = grouped["amount_wan"] / grouped["volume_wan"]
        grouped["code"] = grouped["ts_code"].astype(str).str.split(".").str[0]
        aggregated_parts.append(grouped)
    aggregated = pd.concat(aggregated_parts, ignore_index=True, sort=False) if aggregated_parts else pd.DataFrame()
    if aggregated.empty:
        return pd.DataFrame(), {"complete":set(manifest["partitions"])==expected,"completed_partitions":len(set(manifest["partitions"]).intersection(expected)),"total_partitions":len(partitions),"rows":rows,"invalid_stock_days":invalid_stock_days,"events":0}
    closes = _raw_closes(root, snapshot_date, aggregated[["code", "trade_date"]])
    market_caps = _market_caps(root, aggregated[["ts_code", "trade_date"]])
    event_rows: list[dict[str, Any]] = []; missing_close = missing_market_cap = 0
    for event in aggregated.itertuples(index=False):
        close = closes.get((str(event.code), str(event.trade_date)))
        market_cap = market_caps.get((str(event.ts_code), str(event.trade_date)))
        if close is None: missing_close += 1; continue
        if market_cap is None: missing_market_cap += 1; continue
        premium = float(event.block_vwap) / close - 1.0
        intensity = float(event.amount_wan) / market_cap
        event_rows.append({
            "event_id": hashlib.sha256(f"block-trade|{event.ts_code}|{event.trade_date}".encode()).hexdigest()[:24],
            "family": "block_trade", "code": str(event.code),
            "ann_date": str(event.trade_date), "trade_date": str(event.trade_date),
            "available_at": pd.Timestamp(str(event.trade_date)).tz_localize("Asia/Shanghai").replace(hour=16).tz_convert("UTC").isoformat(),
            "block_vwap": float(event.block_vwap), "raw_close": close,
            "premium": premium, "amount_wan": float(event.amount_wan),
            "amount_market_cap_ratio": intensity, "materiality": intensity,
            "trade_count": int(event.trade_count), "buyer_count": int(event.buyer_count),
            "seller_count": int(event.seller_count), "eligible": True,
            "source": "tushare_block_trade",
        })
    events = pd.DataFrame(event_rows)
    audit = {
        "complete": set(manifest["partitions"]) == expected,
        "completed_partitions": len(set(manifest["partitions"]).intersection(expected)),
        "total_partitions": len(partitions), "rows": int(rows),
        "invalid_stock_days": int(invalid_stock_days),
        "aggregated_stock_days": int(len(aggregated)),
        "missing_closes": int(missing_close), "missing_market_caps": int(missing_market_cap),
        "events": int(len(events)),
    }
    return events, audit
