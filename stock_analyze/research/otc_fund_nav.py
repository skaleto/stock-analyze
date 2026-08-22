"""Durable, research-only OTC fund NAV history artifacts."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import time
from datetime import date, timedelta
from pathlib import Path
from statistics import stdev
from typing import Any, Mapping

from ..utils import write_text_atomic


_CODE = re.compile(r"^[0-9]{6}\.OF$")
_DATE = re.compile(r"^[0-9]{8}$")
_COLUMNS = ("ts_code", "ann_date", "nav_date", "unit_nav", "accum_nav", "adj_nav")
_SCHEMA = "otc-fund-nav-v1"
_REQUEST_INTERVAL_SECONDS = 0.30


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
        raise ValueError("otc_fund_nav_source_invalid")
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("otc_fund_nav_source_invalid")
    return [dict(row) for row in rows]


def _artifact_root(root: Path) -> Path:
    return root / "data/research/otc_fund_nav/v1"


def _active_manifest(root: Path) -> dict[str, object]:
    try:
        value = json.loads((_artifact_root(root) / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def otc_fund_nav_artifact_warning(
    repo_root: str | Path,
    code: str,
    *,
    expected_as_of: str | None = None,
) -> str | None:
    """Return a warning unless a complete, current NAV manifest owns ``code``."""
    normalized = str(code or "").upper()
    if not _CODE.fullmatch(normalized):
        return "场外基金代码无效。"
    manifest = _active_manifest(Path(repo_root).resolve())
    if manifest.get("schema_version") != _SCHEMA or manifest.get("status") != "complete":
        return "场外基金净值采集尚未完整完成。"
    if expected_as_of and manifest.get("as_of") != _date_key(expected_as_of):
        return "场外基金净值与当前目录日期不一致，未展示旧缓存。"
    completed = manifest.get("completed")
    if not isinstance(completed, Mapping) or normalized not in completed:
        return "该场外基金未通过当前净值采集。"
    return None


def _catalog_codes(root: Path, scopes: tuple[str, ...]) -> list[str]:
    try:
        payload = json.loads((root / "data/research/universe_catalogs/latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("otc_fund_nav_catalog_unavailable") from exc
    records = ((payload.get("funds") or {}).get("records") or [])
    return sorted({
        str(row.get("ts_code") or "").upper()
        for row in records
        if isinstance(row, Mapping)
        and row.get("market_source") == "otc"
        and row.get("research_only") is True
        and row.get("overseas_scope") in scopes
        and _CODE.fullmatch(str(row.get("ts_code") or "").upper())
    })


def _normalized_nav(rows: list[dict[str, object]], *, code: str, as_of: str) -> list[dict[str, object]]:
    minimum = (date(int(as_of[:4]), int(as_of[4:6]), int(as_of[6:])) - timedelta(days=3 * 365)).strftime("%Y%m%d")
    by_date: dict[str, dict[str, object]] = {}
    for row in rows:
        nav_date = _date_key(row.get("nav_date"))
        ann_date = _date_key(row.get("ann_date"))
        current_code = str(row.get("ts_code") or "").upper()
        unit_nav, accum_nav, adj_nav = (_number(row.get(key)) for key in ("unit_nav", "accum_nav", "adj_nav"))
        adjusted = adj_nav or accum_nav or unit_nav
        if (
            current_code != code
            or not nav_date
            or not ann_date
            or nav_date < minimum
            or nav_date > as_of
            or ann_date > as_of
            or adjusted is None
            or adjusted <= 0
        ):
            continue
        previous = by_date.get(nav_date)
        if previous is None or ann_date >= str(previous["ann_date"]):
            by_date[nav_date] = {
                "ts_code": code, "ann_date": ann_date, "nav_date": nav_date,
                "unit_nav": unit_nav, "accum_nav": accum_nav, "adj_nav": adj_nav,
            }
    return [by_date[key] for key in sorted(by_date)]


def _csv_text(rows: list[dict[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def refresh_otc_fund_nav(
    *, repo_root: str | Path, pro_client: object, as_of: str, scopes: tuple[str, ...] = ("nasdaq_100", "sp_500")
) -> dict[str, object]:
    """Fetch selected OTC NAV histories into a Dashboard-read-only artifact."""
    snapshot = _date_key(as_of)
    if not snapshot or not scopes or not hasattr(pro_client, "fund_nav"):
        raise ValueError("otc_fund_nav_request_invalid")
    root = Path(repo_root).resolve()
    codes = _catalog_codes(root, tuple(scopes))
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
            source = pro_client.fund_nav(
                ts_code=code,
                end_date=snapshot,
                fields="ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
            )
            rows = _normalized_nav(_rows(source), code=code, as_of=snapshot)
            if len(rows) < 2:
                raise ValueError("insufficient_history")
            write_text_atomic(artifact_root / f"{code}.csv", _csv_text(rows))
            completed[code] = {"rows": len(rows), "first_nav_date": rows[0]["nav_date"], "last_nav_date": rows[-1]["nav_date"]}
        except Exception as exc:  # noqa: BLE001 - preserve any prior valid file
            failures[code] = type(exc).__name__
    status = "complete" if not failures else "partial"
    manifest = {"schema_version": _SCHEMA, "status": status, "as_of": snapshot, "scopes": list(scopes), "requested": len(codes), "completed": completed, "failures": failures}
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    write_text_atomic(artifact_root / "runs" / f"{snapshot}.json", serialized)
    if not failures:
        write_text_atomic(artifact_root / "latest.json", serialized)
    return {"status": status, "as_of": snapshot, "requested": len(codes), "completed": len(completed), "failed": len(failures)}


def _metric(key: str, label: str, explanation: str, value: float) -> dict[str, object]:
    return {"key": key, "label": label, "explanation": explanation, "value": value, "format": "percent"}


def read_otc_fund_nav_detail(repo_root: str | Path, code: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, object]]]:
    """Read one validated NAV curve and calculate descriptive, non-trading metrics."""
    normalized = str(code or "").upper()
    if not _CODE.fullmatch(normalized):
        return [], None, []
    root = Path(repo_root).resolve()
    if otc_fund_nav_artifact_warning(root, normalized) is not None:
        return [], None, []
    as_of = _date_key(_active_manifest(root).get("as_of"))
    if not as_of:
        return [], None, []
    path = _artifact_root(root) / f"{normalized}.csv"
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_COLUMNS):
                return [], None, []
            source_rows = [dict(row) for row in reader]
            rows = _normalized_nav(source_rows, code=normalized, as_of=as_of)
    except OSError:
        return [], None, []
    series = [
        {
            "date": f"{row['nav_date'][:4]}-{row['nav_date'][4:6]}-{row['nav_date'][6:8]}",
            "unitNav": row["unit_nav"], "accumNav": row["accum_nav"],
            "adjustedNav": row["adj_nav"] or row["accum_nav"] or row["unit_nav"],
        }
        for row in rows[-756:]
    ]
    if not series:
        return [], None, []
    values = [float(item["adjustedNav"]) for item in series]
    metrics: list[dict[str, object]] = []
    for sessions, key, label in ((21, "nav_return_1m", "近1月收益"), (63, "nav_return_3m", "近3月收益"), (252, "nav_return_1y", "近1年收益")):
        if len(values) > sessions and values[-sessions - 1] > 0:
            metrics.append(_metric(key, label, "按复权净值计算的区间收益。", values[-1] / values[-sessions - 1] - 1.0))
    returns = [current / previous - 1.0 for previous, current in zip(values, values[1:]) if previous > 0]
    if len(returns) >= 20 and values[0] > 0:
        metrics.append(_metric("annualized_return", "年化收益", "按已缓存交易日折算的复权净值年化收益。", (values[-1] / values[0]) ** (252 / len(returns)) - 1.0))
        metrics.append(_metric("annualized_volatility", "年化波动", "复权净值日收益的年化标准差。", stdev(returns) * math.sqrt(252)))
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)
    metrics.append(_metric("max_drawdown", "最大回撤", "复权净值从历史高点到后续低点的最大跌幅。", max_drawdown))
    return series, dict(series[-1]), metrics
