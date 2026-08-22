"""Durable, research-only A-share OHLCV artifacts for Dashboard detail views."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from ..utils import write_text_atomic


_CODE = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_DATE = re.compile(r"^[0-9]{8}$")
_COLUMNS = ("ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount")
_SCHEMA = "a-share-research-prices-v1"
_REQUEST_INTERVAL_SECONDS = 0.15


def _date_key(value: object) -> str:
    raw = str(value or "").strip().replace("-", "")[:8]
    return raw if _DATE.fullmatch(raw) else ""


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rows(value: object) -> list[dict[str, object]]:
    if hasattr(value, "to_dict"):
        rows = value.to_dict("records")
    elif isinstance(value, list):
        rows = value
    else:
        raise ValueError("a_share_research_prices_source_invalid")
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("a_share_research_prices_source_invalid")
    return [dict(row) for row in rows]


def _catalog_codes(root: Path, scope: str) -> list[str]:
    try:
        payload = json.loads(
            (root / "data/research/universe_catalogs/latest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("a_share_research_prices_catalog_unavailable") from exc
    records = ((payload.get("a_share") or {}).get("records") or [])
    return sorted({
        str(row.get("ts_code") or "").upper()
        for row in records
        if isinstance(row, Mapping)
        and row.get("research_only") is True
        and scope in (row.get("research_scopes") or [])
        and _CODE.fullmatch(str(row.get("ts_code") or "").upper())
    })


def _normalized_history(rows: list[dict[str, object]], *, code: str, as_of: str) -> list[dict[str, object]]:
    minimum = (date(int(as_of[:4]), int(as_of[4:6]), int(as_of[6:])) - timedelta(days=3 * 365)).strftime("%Y%m%d")
    by_date: dict[str, dict[str, object]] = {}
    for row in rows:
        current_code = str(row.get("ts_code") or "").upper()
        trade_date = _date_key(row.get("trade_date"))
        values = {field: _number(row.get(field)) for field in ("open", "high", "low", "close")}
        if current_code != code or not trade_date or trade_date < minimum or trade_date > as_of:
            continue
        if any(value is None or value <= 0 for value in values.values()):
            continue
        by_date[trade_date] = {
            "ts_code": code,
            "trade_date": trade_date,
            **{field: float(value) for field, value in values.items()},
            "vol": _number(row.get("vol", row.get("volume"))),
            "amount": _number(row.get("amount")),
        }
    return [by_date[key] for key in sorted(by_date)]


def _csv_text(rows: list[dict[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _artifact_root(root: Path) -> Path:
    return root / "data/research/a_share_prices/v1"


def refresh_a_share_research_prices(
    *, repo_root: str | Path, pro_client: object, as_of: str, scope: str = "csi1000"
) -> dict[str, object]:
    """Collect a scoped current OHLCV snapshot without touching formal caches."""
    snapshot = _date_key(as_of)
    if not snapshot or not hasattr(pro_client, "daily"):
        raise ValueError("a_share_research_prices_request_invalid")
    root = Path(repo_root).resolve()
    codes = _catalog_codes(root, scope)
    start = (date(int(snapshot[:4]), int(snapshot[4:6]), int(snapshot[6:])) - timedelta(days=3 * 365)).strftime("%Y%m%d")
    artifact_root = _artifact_root(root)
    completed: dict[str, dict[str, object]] = {}
    failures: dict[str, str] = {}
    next_request_at = 0.0
    for code in codes:
        try:
            delay = next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_request_at = time.monotonic() + _REQUEST_INTERVAL_SECONDS
            rows = _normalized_history(_rows(pro_client.daily(ts_code=code, start_date=start, end_date=snapshot)), code=code, as_of=snapshot)
            if len(rows) < 2:
                raise ValueError("insufficient_history")
            write_text_atomic(artifact_root / f"{code}.csv", _csv_text(rows))
            completed[code] = {"rows": len(rows), "first_trade_date": rows[0]["trade_date"], "last_trade_date": rows[-1]["trade_date"]}
        except Exception as exc:  # noqa: BLE001 - retain prior file and report bounded status
            failures[code] = type(exc).__name__
    manifest = {
        "schema_version": _SCHEMA,
        "as_of": snapshot,
        "scope": scope,
        "requested": len(codes),
        "completed": completed,
        "failures": failures,
    }
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    write_text_atomic(artifact_root / "runs" / f"{snapshot}-{scope}.json", serialized)
    write_text_atomic(artifact_root / "latest.json", serialized)
    return {"status": "complete" if not failures else "partial", "as_of": snapshot, "requested": len(codes), "completed": len(completed), "failed": len(failures)}


def read_a_share_research_history(repo_root: str | Path, code: str) -> list[dict[str, Any]]:
    """Read a bounded validated research price file for one A-share code."""
    normalized = str(code or "").upper()
    if not _CODE.fullmatch(normalized):
        return []
    path = _artifact_root(Path(repo_root).resolve()) / f"{normalized}.csv"
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_COLUMNS):
                return []
            source_rows = [dict(row) for row in reader]
            as_of = max((_date_key(row.get("trade_date")) for row in source_rows), default="")
            rows = _normalized_history(source_rows, code=normalized, as_of=as_of) if as_of else []
    except OSError:
        return []
    return [
        {
            "date": f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:8]}",
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
            "volume": row["vol"], "amount": row["amount"],
        }
        for row in rows[-756:]
    ]
