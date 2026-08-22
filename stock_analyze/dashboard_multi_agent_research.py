"""Read-only Dashboard projection for completed multi-agent research runs."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .dashboard_finance import (
    InstrumentDataError,
    build_history_metrics,
    read_instrument_history,
    read_latest_research_values,
)
from .dashboard_http import InvalidDashboardQuery


MAX_DIGEST_CHARS = 4_000
MAX_MANIFESTS_TO_INSPECT = 200
RESEARCH_UNIVERSE_BROWSER_SCHEMA = "research-universe-browser-v1"
RESEARCH_UNIVERSE_INSTRUMENT_SCHEMA = "research-universe-instrument-v1"
RESEARCH_UNIVERSE_KINDS = frozenset({"a_share", "exchange_fund", "otc_fund"})
RESEARCH_UNIVERSE_PAGE_SIZES = frozenset({20, 50, 100})
MAX_RESEARCH_UNIVERSE_QUERY_CHARS = 80
MAX_RESEARCH_UNIVERSE_SCOPE_CHARS = 128
MAX_RESEARCH_UNIVERSE_CODE_CHARS = 64


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object, *, limit: int = 512) -> str:
    return str(value or "").strip()[:limit]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _text_list(value: object, *, limit: int = 128) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({_text(item, limit=limit) for item in value if _text(item, limit=limit)})


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            return _json_safe(value.tolist())
        except (AttributeError, TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (AttributeError, TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _catalog_records(payload: Mapping[str, object], *keys: str) -> list[dict[str, Any]]:
    current: object = payload
    for key in keys:
        current = _mapping(current).get(key)
    records = _mapping(current).get("records")
    return [_mapping(row) for row in records] if isinstance(records, list) else []


def _normalize_research_universe_query(
    *,
    kind: str,
    query: str,
    scope: str | None,
    page: int,
    page_size: int,
) -> tuple[str, str, str | None, int, int]:
    if kind not in RESEARCH_UNIVERSE_KINDS:
        raise InvalidDashboardQuery("kind must be a supported research universe")
    if not isinstance(query, str) or len(query.strip()) > MAX_RESEARCH_UNIVERSE_QUERY_CHARS:
        raise InvalidDashboardQuery("query must be a string up to 80 characters")
    if scope is not None and (
        not isinstance(scope, str) or len(scope.strip()) > MAX_RESEARCH_UNIVERSE_SCOPE_CHARS
    ):
        raise InvalidDashboardQuery("scope must be a string up to 128 characters")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise InvalidDashboardQuery("page must be a positive integer")
    if page_size not in RESEARCH_UNIVERSE_PAGE_SIZES:
        raise InvalidDashboardQuery("page_size must be one of 20, 50, or 100")
    return kind, query.strip(), scope.strip() if scope else None, page, page_size


def _a_share_browser_records(payload: Mapping[str, object]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in _catalog_records(payload, "a_share"):
        code = _text(row.get("ts_code"), limit=64)
        if not code or row.get("research_only") is not True:
            continue
        records.append({
            "code": code,
            "name": _text(row.get("name"), limit=256),
            "recordKind": _text(row.get("record_kind"), limit=64) or "a_share_equity",
            "researchOnly": True,
            "researchScopes": _text_list(row.get("research_scopes")),
            "membershipDate": _text(row.get("membership_date"), limit=16) or None,
        })
    return records


def _fund_browser_records(
    payload: Mapping[str, object], *, source: str
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in _catalog_records(payload, "funds"):
        code = _text(row.get("ts_code"), limit=64)
        if (
            not code
            or row.get("market_source") != source
            or row.get("research_only") is not True
        ):
            continue
        records.append({
            "code": code,
            "name": _text(row.get("name"), limit=256),
            "recordKind": _text(row.get("record_kind"), limit=64) or "fund",
            "researchOnly": True,
            "fundType": _text(row.get("fund_type"), limit=128),
            "benchmark": _text(row.get("benchmark"), limit=256),
            "overseasScope": _text(row.get("overseas_scope"), limit=128) or None,
            "classificationStatus": _text(row.get("classification_status"), limit=128),
            "tradability": _text(row.get("tradability"), limit=128),
        })
    return records


def _empty_research_universe_browser(
    *,
    kind: str,
    query: str,
    scope: str | None,
    page: int,
    page_size: int,
) -> dict[str, object]:
    return {
        "schemaVersion": RESEARCH_UNIVERSE_BROWSER_SCHEMA,
        "status": "unavailable",
        "asOf": None,
        "kind": kind,
        "query": query,
        "scope": scope,
        "page": page,
        "pageSize": page_size,
        "total": 0,
        "scopeOptions": [],
        "records": [],
        "executionEffect": "none_research_only",
    }


def build_dashboard_research_universe_data(
    *,
    repo_root: str | Path | None = None,
    kind: str,
    query: str,
    scope: str | None,
    page: int,
    page_size: int,
) -> dict[str, object]:
    """Project a single persisted research catalog page without provider access."""
    kind, query, scope, page, page_size = _normalize_research_universe_query(
        kind=kind,
        query=query,
        scope=scope,
        page=page,
        page_size=page_size,
    )
    root = Path(repo_root or ".").resolve()
    payload = _read_json(root / "data" / "research" / "universe_catalogs" / "latest.json")
    if not payload:
        return _empty_research_universe_browser(
            kind=kind,
            query=query,
            scope=scope,
            page=page,
            page_size=page_size,
        )

    records = (
        _a_share_browser_records(payload)
        if kind == "a_share"
        else _fund_browser_records(payload, source="exchange" if kind == "exchange_fund" else "otc")
    )
    scope_key = "researchScopes" if kind == "a_share" else "overseasScope"
    scope_options = sorted({
        item
        for record in records
        for item in (
            record[scope_key]
            if isinstance(record[scope_key], list)
            else [record[scope_key]]
        )
        if isinstance(item, str) and item
    })
    normalized_query = query.casefold()
    filtered = [
        record for record in records
        if (
            not normalized_query
            or normalized_query in str(record["code"]).casefold()
            or normalized_query in str(record["name"]).casefold()
        )
        and (
            scope is None
            or (
                scope in record[scope_key]
                if isinstance(record[scope_key], list)
                else scope == record[scope_key]
            )
        )
    ]
    filtered.sort(key=lambda record: str(record["code"]))
    start = (page - 1) * page_size
    return {
        "schemaVersion": RESEARCH_UNIVERSE_BROWSER_SCHEMA,
        "status": "available",
        "asOf": _text(payload.get("as_of"), limit=16) or None,
        "kind": kind,
        "query": query,
        "scope": scope,
        "page": page,
        "pageSize": page_size,
        "total": len(filtered),
        "scopeOptions": scope_options,
        "records": filtered[start:start + page_size],
        "executionEffect": "none_research_only",
    }


def _normalize_research_universe_instrument_query(
    *, kind: str, code: str
) -> tuple[str, str]:
    if kind not in RESEARCH_UNIVERSE_KINDS:
        raise InvalidDashboardQuery("kind must be a supported research universe")
    if not isinstance(code, str) or not code.strip() or len(code.strip()) > MAX_RESEARCH_UNIVERSE_CODE_CHARS:
        raise InvalidDashboardQuery("code must be a non-empty string up to 64 characters")
    return kind, code.strip().upper()


def _research_universe_records(
    payload: Mapping[str, object], *, kind: str
) -> list[dict[str, object]]:
    if kind == "a_share":
        return _a_share_browser_records(payload)
    return _fund_browser_records(
        payload,
        source="exchange" if kind == "exchange_fund" else "otc",
    )


def _empty_research_universe_instrument(
    *,
    kind: str,
    code: str,
    as_of: str | None,
    warning: str,
) -> dict[str, object]:
    return {
        "schemaVersion": RESEARCH_UNIVERSE_INSTRUMENT_SCHEMA,
        "status": "unavailable",
        "asOf": as_of,
        "kind": kind,
        "code": code,
        "instrument": None,
        "market": None,
        "latest": None,
        "candles": [],
        "metrics": [],
        "warning": warning,
        "executionEffect": "none_research_only",
    }


def _research_universe_instrument_metadata(
    record: Mapping[str, object], *, kind: str
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "code": _text(record.get("code"), limit=MAX_RESEARCH_UNIVERSE_CODE_CHARS),
        "name": _text(record.get("name"), limit=256),
        "recordKind": _text(record.get("recordKind"), limit=64),
        "researchOnly": True,
    }
    if kind == "a_share":
        metadata.update({
            "researchScopes": _text_list(record.get("researchScopes")),
            "membershipDate": _text(record.get("membershipDate"), limit=16) or None,
        })
    else:
        metadata.update({
            "fundType": _text(record.get("fundType"), limit=128),
            "benchmark": _text(record.get("benchmark"), limit=256),
            "overseasScope": _text(record.get("overseasScope"), limit=128) or None,
            "classificationStatus": _text(record.get("classificationStatus"), limit=128),
            "tradability": _text(record.get("tradability"), limit=128),
        })
    return metadata


def _latest_quote(candles: list[dict[str, Any]]) -> dict[str, object] | None:
    if not candles:
        return None
    latest = dict(candles[-1])
    latest["changePct"] = None
    if len(candles) > 1:
        previous_close = candles[-2].get("close")
        latest_close = latest.get("close")
        if isinstance(previous_close, (int, float)) and isinstance(latest_close, (int, float)) and previous_close:
            latest["changePct"] = latest_close / previous_close - 1.0
    return latest


def build_dashboard_research_universe_instrument_data(
    *,
    repo_root: str | Path | None = None,
    kind: str,
    code: str,
) -> dict[str, object]:
    """Return a catalog-scoped, account-isolated research instrument detail."""
    kind, code = _normalize_research_universe_instrument_query(kind=kind, code=code)
    root = Path(repo_root or ".").resolve()
    payload = _read_json(root / "data" / "research" / "universe_catalogs" / "latest.json")
    as_of = _text(payload.get("as_of"), limit=16) or None
    if not payload:
        return _empty_research_universe_instrument(
            kind=kind,
            code=code,
            as_of=None,
            warning="研究目录快照暂不可用。",
        )
    record = next(
        (
            row
            for row in _research_universe_records(payload, kind=kind)
            if _text(row.get("code"), limit=MAX_RESEARCH_UNIVERSE_CODE_CHARS).upper() == code
        ),
        None,
    )
    if record is None:
        return _empty_research_universe_instrument(
            kind=kind,
            code=code,
            as_of=as_of,
            warning="该标的不在当前研究目录快照中。",
        )

    instrument = _research_universe_instrument_metadata(record, kind=kind)
    if kind == "otc_fund":
        return {
            "schemaVersion": RESEARCH_UNIVERSE_INSTRUMENT_SCHEMA,
            "status": "available",
            "asOf": as_of,
            "kind": kind,
            "code": code,
            "instrument": instrument,
            "market": None,
            "latest": None,
            "candles": [],
            "metrics": [],
            "warning": "场外基金为非交易研究对照，当前不展示场内 K 线。",
            "executionEffect": "none_research_only",
        }

    market = "a_share" if kind == "a_share" else "cn_qdii_etf"
    try:
        normalized, candles, warning = read_instrument_history(root, market, code)
        metrics = build_history_metrics(
            candles,
            read_latest_research_values(root, market, normalized),
        )
    except (InstrumentDataError, ValueError):
        normalized = code
        candles = []
        metrics = []
        warning = "历史行情或研究指标缓存不可读。"
    return _json_safe({
        "schemaVersion": RESEARCH_UNIVERSE_INSTRUMENT_SCHEMA,
        "status": "available",
        "asOf": as_of,
        "kind": kind,
        "code": normalized,
        "instrument": instrument,
        "market": market,
        "latest": _latest_quote(candles),
        "candles": candles,
        "metrics": metrics,
        "warning": warning,
        "executionEffect": "none_research_only",
    })


def _empty_universe() -> dict[str, object]:
    return {
        "status": "unavailable",
        "asOf": None,
        "aShare": {"scopeCounts": {}},
        "funds": {"sourceCounts": {}, "overseasScopeCounts": {}},
    }


def _universe_summary(root: Path) -> dict[str, object]:
    payload = _read_json(root / "data" / "research" / "universe_catalogs" / "latest.json")
    if not payload:
        return _empty_universe()
    a_share = _mapping(_mapping(payload.get("a_share")).get("summary"))
    funds = _mapping(_mapping(payload.get("funds")).get("summary"))
    return {
        "status": "available",
        "asOf": _text(payload.get("as_of"), limit=16) or None,
        "aShare": {
            "scopeCounts": _mapping(a_share.get("scope_counts")),
            "uniqueInstruments": a_share.get("unique_instruments"),
        },
        "funds": {
            "sourceCounts": _mapping(funds.get("source_counts")),
            "overseasScopeCounts": _mapping(funds.get("overseas_scope_counts")),
            "classificationCounts": _mapping(funds.get("classification_counts")),
        },
    }


def _latest_run(root: Path) -> dict[str, object] | None:
    artifact_root = root / "reports" / "research" / "multi_agent"
    if not artifact_root.exists():
        return None
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    paths = sorted(artifact_root.rglob("manifest.json"), reverse=True)
    for path in paths[:MAX_MANIFESTS_TO_INSPECT]:
        manifest = _read_json(path)
        instrument = _mapping(manifest.get("instrument"))
        if (
            not manifest
            or not _text(manifest.get("run_id"))
            or not _text(manifest.get("market"))
            or not _text(instrument.get("code"))
            or manifest.get("execution_effect") != "none_research_only"
        ):
            continue
        created = _text(manifest.get("created_at"))
        candidates.append((created, path, manifest))
    if not candidates:
        return None
    _, manifest_path, manifest = max(candidates, key=lambda item: (item[0], item[1].as_posix()))
    output = manifest_path.parent
    instrument = _mapping(manifest.get("instrument"))
    try:
        digest = (output / "digest.md").read_text(encoding="utf-8").strip()[:MAX_DIGEST_CHARS]
    except OSError:
        digest = ""
    return {
        "runId": _text(manifest.get("run_id"), limit=160),
        "createdAt": _text(manifest.get("created_at"), limit=64) or None,
        "status": _text(manifest.get("status"), limit=64) or "unknown",
        "market": _text(manifest.get("market"), limit=64),
        "instrument": {
            "code": _text(instrument.get("code"), limit=64),
            "name": _text(instrument.get("name"), limit=256),
        },
        "model": _text(manifest.get("model"), limit=128) or None,
        "degradedRoles": [
            _text(item, limit=64)
            for item in (manifest.get("degraded_roles") or [])
            if _text(item, limit=64)
        ][:8],
        "digest": digest,
        "executionEffect": "none_research_only",
        "reportPath": (output / "full_report.md").relative_to(root).as_posix(),
    }


def build_dashboard_multi_agent_research_data(
    *,
    repo_root: str | Path | None = None,
) -> dict[str, object]:
    """Return bounded persisted artifacts without fetching providers or models."""
    root = Path(repo_root or ".").resolve()
    latest = _latest_run(root)
    return {
        "schemaVersion": "multi-agent-research-dashboard-v1",
        "status": "available" if latest is not None else "empty",
        "latestRun": latest,
        "universe": _universe_summary(root),
        "executionEffect": "none_research_only",
    }


__all__ = [
    "build_dashboard_multi_agent_research_data",
    "build_dashboard_research_universe_instrument_data",
    "build_dashboard_research_universe_data",
]
