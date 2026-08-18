"""Resumable implemented-dividend history and annual PIT normalization."""

from __future__ import annotations

from datetime import date, timedelta
import bisect
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..utils import write_text_atomic
from .storage import ResearchStore


FIELDS = (
    "ts_code,end_date,ann_date,div_proc,cash_div,cash_div_tax,record_date,"
    "ex_date,pay_date,imp_ann_date,base_date,base_share"
)
PROTOCOL = "dividend-growth-backfill-v1"
PAGE_SIZE = 2000


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _date_partitions(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def dividend_partitions(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(str(start_date)[:10])
    end = date.fromisoformat(str(end_date)[:10])
    if start > end:
        raise ValueError("dividend_growth_backfill_range")
    return [value.strftime("%Y%m%d") for value in _date_partitions(start, end)]


def _root(repo_root: str | Path) -> Path:
    return (
        Path(repo_root).resolve() / "data" / "research"
        / "dividend_growth_structured" / "v1"
    )


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _key(day: str) -> str:
    return f"dividend:{day}"


def _partition_path(root: Path, day: str) -> Path:
    return root / "dividend" / f"{day}.parquet"


def _load_manifest(root: Path, start: str, end: str) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            payload.get("protocol_version") != PROTOCOL
            or payload.get("start_date") != start
            or payload.get("end_date") != end
        ):
            raise ValueError("dividend_growth_manifest_conflict")
        return payload
    return {
        "protocol_version": PROTOCOL, "start_date": start,
        "end_date": end, "partitions": {},
    }


def _fetch_day(client: Any, day: str) -> pd.DataFrame:
    pages: list[pd.DataFrame] = []
    offset = 0
    while True:
        frame = client.dividend(
            imp_ann_date=day, limit=PAGE_SIZE, offset=offset, fields=FIELDS
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("dividend_growth_response")
        pages.append(frame)
        if len(frame) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return pd.concat(pages, ignore_index=True, sort=False)


def _validate_partition(frame: pd.DataFrame, day: str) -> pd.DataFrame:
    required = {
        "ts_code", "end_date", "ann_date", "div_proc",
        "cash_div_tax", "record_date", "ex_date", "pay_date",
        "imp_ann_date", "base_share",
    }
    if required.difference(frame.columns):
        raise ValueError("dividend_growth_shape")
    result = frame.copy()
    for column in (
        "ts_code", "end_date", "ann_date", "div_proc",
        "record_date", "ex_date", "pay_date", "imp_ann_date",
        "base_date",
    ):
        if column in result:
            result[column] = result[column].astype("string")
    result["imp_ann_date"] = result["imp_ann_date"].map(_date_key)
    result["end_date"] = result["end_date"].map(_date_key)
    if not result.empty and not result["imp_ann_date"].eq(day).all():
        raise ValueError(f"dividend_growth_partition_leak:{day}")
    result = result.loc[
        result["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    return (
        result.sort_values(
            ["ts_code", "end_date", "imp_ann_date", "record_date"],
            kind="stable",
        ).drop_duplicates(keep="last").reset_index(drop=True)
    )


def run_dividend_growth_backfill(
    repo_root: str | Path, client: Any, *,
    start_date: str = "2018-01-01", end_date: str = "2024-12-31",
    max_partitions: int | None = None,
) -> dict[str, Any]:
    root = _root(repo_root); root.mkdir(parents=True, exist_ok=True)
    start_key, end_key = _date_key(start_date), _date_key(end_date)
    manifest = _load_manifest(root, start_key, end_key)
    completed = manifest["partitions"]
    partitions = dividend_partitions(start_date, end_date)
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
        raise ValueError("dividend_growth_partition_path")
    path = (repo_root / relative).resolve()
    if not path.is_relative_to(repo_root.resolve()):
        raise ValueError("dividend_growth_partition_path")
    return path


def _load_facts(
    repo_root: Path, start_date: str, end_date: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = _root(repo_root); partitions = dividend_partitions(start_date, end_date)
    expected = {_key(day) for day in partitions}
    if not _manifest_path(root).exists():
        return pd.DataFrame(), {
            "complete": False, "completed_partitions": 0,
            "total_partitions": len(partitions), "rows": 0,
        }
    manifest = _load_manifest(root, _date_key(start_date), _date_key(end_date))
    facts: list[pd.DataFrame] = []; rows = 0
    fact_columns = [
        "ts_code", "end_date", "imp_ann_date", "record_date",
        "ex_date", "pay_date", "cash_div_tax", "base_share",
    ]
    for day in partitions:
        key = _key(day); record = manifest["partitions"].get(key)
        if record is None: continue
        expected_path = _partition_path(root, day)
        if (
            record.get("day") != day
            or record.get("path") != expected_path.relative_to(repo_root).as_posix()
        ):
            raise ValueError(f"dividend_growth_partition_record:{key}")
        path = _safe_path(repo_root, record.get("path"))
        if (
            not path.exists()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != str(record.get("sha256"))
        ):
            raise ValueError(f"dividend_growth_partition_tampered:{key}")
        frame = _validate_partition(pd.read_parquet(path), day)
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"dividend_growth_partition_rows:{key}")
        rows += len(frame)
        frame["div_proc"] = frame["div_proc"].astype("string").str.strip()
        frame["cash_div_tax"] = pd.to_numeric(frame["cash_div_tax"], errors="coerce")
        frame["base_share"] = pd.to_numeric(frame["base_share"], errors="coerce")
        eligible = frame.loc[
            frame["div_proc"].eq("实施")
            & frame["end_date"].str.endswith("1231", na=False)
            & frame["cash_div_tax"].gt(0)
            & frame["base_share"].gt(0),
            fact_columns,
        ].copy()
        if not eligible.empty:
            facts.append(eligible.drop_duplicates(fact_columns))
    combined = (
        pd.concat(facts, ignore_index=True, sort=False).drop_duplicates(fact_columns)
        if facts else pd.DataFrame(columns=fact_columns)
    )
    return combined, {
        "complete": set(manifest["partitions"]) == expected,
        "completed_partitions": len(set(manifest["partitions"]).intersection(expected)),
        "total_partitions": len(partitions), "rows": int(rows),
        "implemented_annual_facts": int(len(combined)),
    }


def _pit_market_values(
    repo_root: Path, requested: pd.DataFrame
) -> dict[tuple[str, str], float]:
    daily_root = repo_root / "data" / "shared" / "backtest_cache" / "daily_basic"
    available = sorted((p.stem.replace("-", ""), p) for p in daily_root.glob("*.csv"))
    dates = [item[0] for item in available]; result: dict[tuple[str, str], float] = {}
    for imp_date, group in requested.groupby("imp_ann_date", sort=True):
        position = bisect.bisect_right(dates, str(imp_date)) - 1
        if position < 0: continue
        frame = pd.read_csv(
            available[position][1], usecols=["ts_code", "total_mv"],
            dtype={"ts_code": str},
        )
        frame = frame.loc[frame["ts_code"].isin(set(group["ts_code"].astype(str)))]
        for row in frame.itertuples(index=False):
            value = pd.to_numeric(pd.Series([row.total_mv]), errors="coerce").iloc[0]
            if pd.notna(value) and float(value) > 0:
                result[(str(row.ts_code), str(imp_date))] = float(value) * 10000.0
    return result


def load_dividend_growth_events(
    repo_root: str | Path, *, start_date: str = "2018-01-01",
    end_date: str = "2024-12-31",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    root = Path(repo_root).resolve(); facts, audit = _load_facts(root, start_date, end_date)
    if facts.empty:
        audit.update({"ambiguous_fiscal_years": 0, "events": 0})
        return pd.DataFrame(), audit
    counts = facts.groupby(["ts_code", "end_date"]).size()
    valid_keys = counts.loc[counts.eq(1)].index
    ambiguous = int(counts.gt(1).sum())
    annual = (
        facts.set_index(["ts_code", "end_date"]).loc[valid_keys]
        .reset_index().sort_values(["ts_code", "end_date"], kind="stable")
    )
    annual["total_cash_yuan"] = (
        annual["cash_div_tax"] * annual["base_share"] * 10000.0
    )
    annual["fiscal_year"] = annual["end_date"].str[:4].astype(int)
    market_values = _pit_market_values(root, annual[["ts_code", "imp_ann_date"]])
    lookup = {(str(row.ts_code), int(row.fiscal_year)): row for row in annual.itertuples(index=False)}
    rows: list[dict[str, Any]] = []; missing_market_value = 0
    for current in annual.itertuples(index=False):
        previous = lookup.get((str(current.ts_code), int(current.fiscal_year) - 1))
        if previous is None or str(previous.imp_ann_date) > str(current.imp_ann_date):
            continue
        market_value = market_values.get((str(current.ts_code), str(current.imp_ann_date)))
        if not market_value:
            missing_market_value += 1; continue
        growth = float(current.total_cash_yuan) / float(previous.total_cash_yuan) - 1.0
        if growth > 0.0:
            family = "annual_dividend_growth"
        elif growth < 0.0:
            family = "annual_dividend_cut"
        else:
            continue
        materiality = float(current.total_cash_yuan) / market_value
        rows.append({
            "event_id": hashlib.sha256(f"annual-dividend|{current.ts_code}|{current.end_date}|{current.imp_ann_date}".encode()).hexdigest()[:24],
            "family": family, "code": str(current.ts_code).split(".")[0],
            "ann_date": str(current.imp_ann_date),
            "available_at": pd.Timestamp(str(current.imp_ann_date)).tz_localize("Asia/Shanghai").replace(hour=16).tz_convert("UTC").isoformat(),
            "end_date": str(current.end_date), "previous_end_date": str(previous.end_date),
            "total_cash_yuan": float(current.total_cash_yuan),
            "previous_total_cash_yuan": float(previous.total_cash_yuan),
            "dividend_growth": growth, "dividend_market_cap_ratio": materiality,
            "materiality": materiality, "eligible": True,
            "source": "tushare_dividend",
        })
    events = pd.DataFrame(rows)
    audit.update({
        "ambiguous_fiscal_years": ambiguous, "annual_facts": int(len(annual)),
        "missing_market_values": missing_market_value, "events": int(len(events)),
    })
    return events, audit
