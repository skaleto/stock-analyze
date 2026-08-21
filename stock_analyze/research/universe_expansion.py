"""Research-only universe catalog builders.

The formal HS300 and ZZ500 paper accounts remain governed by their locked
competition configuration.  This module creates a separate, provenance-rich
catalog that can be consumed by research workflows without changing account
scope, orders, or execution eligibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..utils import write_text_atomic


RESEARCH_UNIVERSE_SCHEMA_VERSION = "research-universe-catalog-v1"
A_SHARE_INDEXES = {
    "hs300": "000300.SH",
    "zz500": "000905.SH",
    "csi1000": "000852.SH",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "nat", "<na>", "none", "null"} else text


def _compact_date(value: object) -> str:
    raw = _text(value).replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return raw


def _row_text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _optional_number(value: object) -> float | None:
    if value is None or _text(value) == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_a_share_research_catalog(
    memberships: Mapping[str, Iterable[Mapping[str, object]]],
    *,
    as_of: str,
) -> dict[str, object]:
    """Build an exact-date A-share research membership catalog.

    Each index must supply a non-empty membership snapshot on or before the
    requested ``as_of`` date.  This intentional fail-closed behavior prevents
    a partial index response from quietly changing the research cross-section.
    """
    snapshot_date = _compact_date(as_of)
    if not snapshot_date:
        raise ValueError("research_universe_as_of_invalid")

    by_code: dict[str, dict[str, Any]] = {}
    scope_counts: dict[str, int] = {}
    membership_dates: dict[str, str] = {}
    for scope, index_code in A_SHARE_INDEXES.items():
        candidates: list[tuple[str, Mapping[str, object]]] = []
        for row in memberships.get(scope, ()):  # unknown scopes are ignored
            code = _row_text(row, "con_code", "ts_code")
            membership_date = _compact_date(row.get("trade_date"))
            if code and membership_date and membership_date <= snapshot_date:
                candidates.append((membership_date, row))
        if not candidates:
            raise ValueError(f"a_share_membership_missing:{scope}")

        latest_date = max(candidate[0] for candidate in candidates)
        selected = [row for date_value, row in candidates if date_value == latest_date]
        selected_codes = {
            _row_text(row, "con_code", "ts_code")
            for row in selected
            if _row_text(row, "con_code", "ts_code")
        }
        if not selected_codes:
            raise ValueError(f"a_share_membership_missing:{scope}")
        membership_dates[scope] = latest_date
        scope_counts[scope] = len(selected_codes)

        for row in selected:
            code = _row_text(row, "con_code", "ts_code")
            if not code:
                continue
            record = by_code.setdefault(
                code,
                {
                    "ts_code": code,
                    "record_kind": "a_share_equity",
                    "research_only": True,
                    "research_scopes": [],
                    "memberships": [],
                },
            )
            record["research_scopes"].append(scope)
            membership = {
                "scope": scope,
                "index_code": index_code,
                "membership_date": latest_date,
            }
            weight = _optional_number(row.get("weight"))
            if weight is not None:
                membership["weight"] = weight
            record["memberships"].append(membership)

    records: list[dict[str, object]] = []
    for code in sorted(by_code):
        record = by_code[code]
        record["research_scopes"] = sorted(set(record["research_scopes"]))
        record["memberships"] = sorted(
            record["memberships"], key=lambda item: str(item["scope"])
        )
        latest = max(str(item["membership_date"]) for item in record["memberships"])
        record["membership_date"] = latest
        records.append(record)

    summary = {
        "scope_counts": scope_counts,
        "index_codes": dict(A_SHARE_INDEXES),
        "membership_dates": membership_dates,
        "unique_instruments": len(records),
        "records_sha256": _canonical_hash(records),
    }
    return {
        "schema_version": RESEARCH_UNIVERSE_SCHEMA_VERSION,
        "catalog_kind": "a_share_research",
        "as_of": snapshot_date,
        "records": records,
        "summary": summary,
    }


def _fund_scope(name: str, benchmark: str) -> tuple[str | None, list[str]]:
    corpus = f"{name} {benchmark}".lower()
    evidence: list[str] = []
    for field, value in (("name", name), ("benchmark", benchmark)):
        lowered = value.lower()
        if "nasdaq" in lowered or "纳斯达克" in value:
            evidence.append(f"{field}:nasdaq")
        if "标普500" in value or re.search(r"s\s*&\s*p\s*500|sp\s*500", lowered):
            evidence.append(f"{field}:sp500")
        if any(token in value for token in ("恒生", "港股", "香港")) or "hang seng" in lowered:
            evidence.append(f"{field}:hong_kong")
        if any(token in value for token in ("日经", "日本")) or "nikkei" in lowered:
            evidence.append(f"{field}:japan")
        if any(token in value for token in ("欧洲", "德国", "法国")) or "euro stoxx" in lowered:
            evidence.append(f"{field}:europe")
        if "印度" in value or "india" in lowered:
            evidence.append(f"{field}:india")
        if "沙特" in value or "saudi" in lowered:
            evidence.append(f"{field}:saudi")
        if any(token in value for token in ("黄金", "原油", "石油", "商品")):
            evidence.append(f"{field}:commodity")
        if "全球" in value or "world" in lowered:
            evidence.append(f"{field}:global")

    # "S&P China" is a domestic China index family, not US exposure.
    domestic_sp = any(token in corpus for token in ("标普中国", "s&p china", "sp china"))
    if not domestic_sp and any(item.endswith(":nasdaq") for item in evidence):
        return "nasdaq_100", evidence
    if not domestic_sp and any(item.endswith(":sp500") for item in evidence):
        return "sp_500", evidence
    if any(item.endswith(":hong_kong") for item in evidence):
        return "hk_exposure", evidence
    if any(item.endswith(":japan") for item in evidence):
        return "japan_exposure", evidence
    if any(item.endswith(":europe") for item in evidence):
        return "europe_exposure", evidence
    if any(item.endswith(":india") for item in evidence):
        return "india_exposure", evidence
    if any(item.endswith(":saudi") for item in evidence):
        return "saudi_exposure", evidence
    if any(item.endswith(":commodity") for item in evidence):
        return "commodity_exposure", evidence
    if any(item.endswith(":global") for item in evidence):
        return "global_exposure", evidence
    return None, evidence


def _fund_record(row: Mapping[str, object], *, source: str) -> dict[str, object] | None:
    code = _row_text(row, "ts_code", "code")
    if not code:
        return None
    name = _text(row.get("name"))
    benchmark = _text(row.get("benchmark"))
    corpus = f"{name} {benchmark}".lower()
    scope, evidence = _fund_scope(name, benchmark)
    explicit_qdii = "qdii" in corpus or "合格境内机构投资者" in corpus
    if explicit_qdii:
        evidence.append("explicit:qdii")
    record: dict[str, object] = {
        "ts_code": code,
        "record_kind": "fund",
        "name": name,
        "market_source": source,
        "tradability": (
            "exchange_research_only"
            if source == "exchange"
            else "otc_non_tradable_research_only"
        ),
        "research_only": True,
        "status": _text(row.get("status")),
        "fund_type": _row_text(row, "fund_type", "type"),
        "invest_type": _text(row.get("invest_type")),
        "benchmark": benchmark,
        "list_date": _compact_date(row.get("list_date")),
        "overseas_scope": scope,
        "classification_status": (
            "explicit_qdii"
            if explicit_qdii
            else "name_benchmark_inferred"
            if scope is not None
            else "unclassified"
        ),
        "classification_evidence": sorted(set(evidence)),
    }
    return record


def build_fund_research_catalog(
    *,
    exchange_basic: Iterable[Mapping[str, object]],
    otc_basic: Iterable[Mapping[str, object]],
    as_of: str,
) -> dict[str, object]:
    """Build a metadata-only fund catalog for exchange and OTC sources.

    OTC records are intentionally represented as non-tradable research context;
    the catalog contains no NAV, quote, or execution fields for either source.
    """
    snapshot_date = _compact_date(as_of)
    if not snapshot_date:
        raise ValueError("research_universe_as_of_invalid")

    records: list[dict[str, object]] = []
    for source, rows in (("exchange", exchange_basic), ("otc", otc_basic)):
        for row in rows:
            if _text(row.get("status")) not in {"", "L"}:
                continue
            record = _fund_record(row, source=source)
            if record is not None:
                records.append(record)
    records.sort(key=lambda item: (str(item["market_source"]), str(item["ts_code"])))

    source_counts = Counter(str(record["market_source"]) for record in records)
    scope_counts = Counter(
        str(record["overseas_scope"])
        for record in records
        if record["overseas_scope"] is not None
    )
    summary = {
        "source_counts": {
            "exchange": source_counts["exchange"],
            "otc": source_counts["otc"],
        },
        "overseas_scope_counts": dict(sorted(scope_counts.items())),
        "classification_counts": dict(
            sorted(Counter(str(record["classification_status"]) for record in records).items())
        ),
        "records_sha256": _canonical_hash(records),
    }
    return {
        "schema_version": RESEARCH_UNIVERSE_SCHEMA_VERSION,
        "catalog_kind": "fund_research",
        "as_of": snapshot_date,
        "records": records,
        "summary": summary,
    }


def _rows(source: object, *, source_name: str) -> list[dict[str, object]]:
    """Normalize a collection-job transport without serializing a DataFrame."""
    if hasattr(source, "to_dict"):
        rows = source.to_dict("records")  # pandas DataFrame transport
    elif isinstance(source, Iterable) and not isinstance(source, (str, bytes, Mapping)):
        rows = list(source)
    else:
        raise ValueError(f"research_universe_source_invalid:{source_name}")
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"research_universe_source_invalid:{source_name}")
        normalized.append(dict(row))
    return normalized


def _membership_start(snapshot_date: str) -> str:
    requested = date(
        int(snapshot_date[:4]), int(snapshot_date[4:6]), int(snapshot_date[6:])
    )
    return (requested - timedelta(days=45)).strftime("%Y%m%d")


def build_research_universe_snapshot(
    *,
    a_share: Mapping[str, object],
    funds: Mapping[str, object],
    as_of: str,
    source_metadata: Mapping[str, object],
) -> dict[str, object]:
    """Combine validated subcatalogs into the immutable dated snapshot payload."""
    snapshot_date = _compact_date(as_of)
    if not snapshot_date:
        raise ValueError("research_universe_as_of_invalid")
    if a_share.get("as_of") != snapshot_date or funds.get("as_of") != snapshot_date:
        raise ValueError("research_universe_as_of_mismatch")
    payload: dict[str, object] = {
        "schema_version": RESEARCH_UNIVERSE_SCHEMA_VERSION,
        "catalog_kind": "research_universe_snapshot",
        "as_of": snapshot_date,
        "sources": dict(source_metadata),
        "a_share": dict(a_share),
        "funds": dict(funds),
        "summary": {
            "a_share": dict(a_share.get("summary") or {}),
            "funds": dict(funds.get("summary") or {}),
        },
    }
    payload["content_sha256"] = _canonical_hash(payload)
    return payload


def _write_snapshot(repo_root: Path, payload: Mapping[str, object]) -> tuple[Path, Path]:
    snapshot_date = _compact_date(payload.get("as_of"))
    if not snapshot_date:
        raise ValueError("research_universe_as_of_invalid")
    root = repo_root / "data" / "research" / "universe_catalogs"
    dated_path = root / snapshot_date / "catalog.json"
    latest_path = root / "latest.json"
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    # The dated artifact is durable before latest advances.  No output is
    # touched until all collection and validation steps above have completed.
    write_text_atomic(dated_path, serialized)
    write_text_atomic(latest_path, serialized)
    return dated_path, latest_path


def refresh_research_universes(
    *,
    repo_root: str | Path,
    pro_client: object,
    as_of: str,
) -> dict[str, object]:
    """Collect a bounded first research universe snapshot from Tushare.

    This collection job intentionally fetches metadata and current index
    membership only.  It does not alter formal account configurations, write
    execution caches, retrieve NAV histories, or reach a broker.
    """
    snapshot_date = _compact_date(as_of)
    if not snapshot_date:
        raise ValueError("research_universe_as_of_invalid")
    if not hasattr(pro_client, "index_weight") or not hasattr(pro_client, "fund_basic"):
        raise ValueError("research_universe_client_invalid")

    start_date = _membership_start(snapshot_date)
    memberships: dict[str, list[dict[str, object]]] = {}
    for scope, index_code in A_SHARE_INDEXES.items():
        response = pro_client.index_weight(
            index_code=index_code,
            start_date=start_date,
            end_date=snapshot_date,
        )
        memberships[scope] = _rows(
            response,
            source_name=f"index_weight:{scope}",
        )
    exchange_basic = _rows(
        pro_client.fund_basic(market="E", status="L"),
        source_name="fund_basic:exchange",
    )
    otc_basic = _rows(
        pro_client.fund_basic(market="O", status="L"),
        source_name="fund_basic:otc",
    )

    a_share = build_a_share_research_catalog(memberships, as_of=snapshot_date)
    funds = build_fund_research_catalog(
        exchange_basic=exchange_basic,
        otc_basic=otc_basic,
        as_of=snapshot_date,
    )
    payload = build_research_universe_snapshot(
        a_share=a_share,
        funds=funds,
        as_of=snapshot_date,
        source_metadata={
            "provider": "tushare",
            "a_share_membership_start": start_date,
            "a_share_indexes": dict(A_SHARE_INDEXES),
            "fund_masters": {
                "exchange": {"market": "E", "status": "L", "rows": len(exchange_basic)},
                "otc": {"market": "O", "status": "L", "rows": len(otc_basic)},
            },
        },
    )
    dated_path, latest_path = _write_snapshot(Path(repo_root), payload)
    return {
        "status": "complete",
        "as_of": snapshot_date,
        "catalog_path": dated_path.as_posix(),
        "latest_path": latest_path.as_posix(),
        "summary": payload["summary"],
        "content_sha256": payload["content_sha256"],
    }
