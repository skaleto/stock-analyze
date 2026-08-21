"""Audited multi-role research built from this project's persisted evidence.

This is intentionally a research artifact workflow.  It never mutates the
formal strategy configuration, candidate registry, paper accounts, pending
orders, or execution data.  Model calls are only made by an explicit CLI
command; Dashboard readers consume finished artifacts only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import pyarrow.parquet as pq

from ..utils import write_text_atomic
from .storage import ResearchStore


MULTI_AGENT_SCHEMA_VERSION = "multi-agent-research-v1"
_MARKETS = frozenset({"a_share", "cn_qdii_etf"})
_TECHNICAL_COLUMNS = (
    "open", "high", "low", "close", "momentum_5", "momentum_20",
    "momentum_60", "rsi_14", "macd", "macd_signal", "macd_hist",
    "realized_volatility_20", "volume_ratio_5_20", "turnover_rate",
)
_FUNDAMENTAL_COLUMNS = (
    "pe_ttm", "pb", "dv_ttm", "roe", "roic", "grossprofit_margin",
    "debt_to_assets", "netprofit_yoy", "revenue_yoy", "q_sales_yoy",
    "q_op_qoq", "total_mv",
)


@dataclass(frozen=True)
class ResearchLLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    status: str = "ok"
    error: str = ""
    wall_seconds: float = 0.0


class ResearchLLMClient(Protocol):
    """Minimal transport boundary for explicitly invoked research models."""

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        model: str,
        max_output_tokens: int,
    ) -> ResearchLLMResponse:
        ...


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract one model JSON object, accepting an optional Markdown fence."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.S)
    candidate = fenced.group(1) if fenced else text
    decoder = json.JSONDecoder()
    for offset, character in enumerate(candidate):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(candidate[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("multi_agent_model_json_invalid")


class ArkCLIResearchClient:
    """Explicit local ``arkcli`` adapter; no token is read or persisted here."""

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        self.timeout_seconds = int(timeout_seconds)

    def complete(
        self,
        *,
        role: str,
        prompt: str,
        model: str,
        max_output_tokens: int,
    ) -> ResearchLLMResponse:
        command = [
            "arkcli", "+chat", prompt,
            "--model", model,
            "--max-output-tokens", str(max_output_tokens),
            "--reasoning-effort", "low",
            "--text-format", "json_object",
            "--no-progress",
            "--format", "json",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
            outer = extract_json_object(completed.stdout) if completed.stdout.strip() else {}
            if completed.returncode != 0 or outer.get("ok") is False:
                message = ""
                if isinstance(outer.get("error"), dict):
                    message = str(outer["error"].get("message") or "")
                raise RuntimeError(message or completed.stderr.strip() or "arkcli_call_failed")
            usage = outer.get("usage") if isinstance(outer.get("usage"), dict) else {}
            return ResearchLLMResponse(
                content=str(outer.get("content") or ""),
                model=model,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                wall_seconds=round(time.monotonic() - started, 3),
            )
        except Exception as exc:  # noqa: BLE001 - role failures degrade the report
            return ResearchLLMResponse(
                content="",
                model=model,
                status="error",
                error=str(exc),
                wall_seconds=round(time.monotonic() - started, 3),
            )


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _date_key(value: object) -> str:
    raw = _text(value).replace("-", "")
    return raw[:8] if len(raw) >= 8 and raw[:8].isdigit() else ""


def _code_key(value: object) -> str:
    raw = _text(value).upper()
    match = re.search(r"\d{6}", raw)
    return match.group(0) if match else raw


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return _text(value)


def _numeric_facts(row: MappingLike, columns: tuple[str, ...]) -> dict[str, object]:
    values: dict[str, object] = {}
    for column in columns:
        if column not in row:
            continue
        value = _json_safe(row[column])
        if value is not None and value != "":
            values[column] = value
    return values


class MappingLike(Protocol):
    def __contains__(self, key: object) -> bool:
        ...

    def __getitem__(self, key: str) -> object:
        ...


def _read_catalog_record(root: Path, *, market: str, code: str) -> dict[str, object] | None:
    paths = [root / "data" / "research" / "universe_catalogs" / "latest.json"]
    if market == "cn_qdii_etf":
        paths.append(root / "data" / "cn_qdii_etf" / "research" / "catalog_latest.json")
    target = _code_key(code)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        candidates: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            if market == "a_share":
                candidates.extend(
                    item for item in ((payload.get("a_share") or {}).get("records") or [])
                    if isinstance(item, dict)
                )
            else:
                candidates.extend(
                    item for item in ((payload.get("funds") or {}).get("records") or [])
                    if isinstance(item, dict)
                )
                for rows in (payload.get("scopes") or {}).values():
                    if isinstance(rows, list):
                        candidates.extend(item for item in rows if isinstance(item, dict))
        for item in candidates:
            if _code_key(item.get("ts_code") or item.get("code")) == target:
                return {
                    key: _json_safe(value)
                    for key, value in item.items()
                    if key in {
                        "ts_code", "code", "name", "research_scopes", "membership_date",
                        "research_scope", "scope", "index_key", "theme", "country",
                        "market_source", "tradability", "classification_status",
                        "classification_evidence", "overseas_scope", "source",
                    }
                }
    return None


def build_research_evidence(
    *,
    repo_root: str | Path,
    market: str,
    code: str,
    as_of: str | None = None,
) -> dict[str, object]:
    """Read the latest persisted feature snapshot for one instrument.

    No provider is called here.  Missing current evidence is an error rather
    than an excuse to fill a research report with live or unverifiable data.
    """
    if market not in _MARKETS:
        raise ValueError(f"research_evidence_market_invalid:{market}")
    root = Path(repo_root)
    cutoff = _date_key(as_of) or datetime.now(timezone.utc).strftime("%Y%m%d")
    store = ResearchStore(root / "data" / "research")
    try:
        snapshot_date = store.latest_feature_snapshot_date(market, as_of=cutoff)
    except FileNotFoundError as exc:
        raise ValueError(f"research_evidence_snapshot_missing:{market}") from exc
    feature_path = store.feature_snapshot_path(market, snapshot_date)
    schema = set(pq.read_schema(feature_path).names)
    code_column = "code" if "code" in schema else "ts_code" if "ts_code" in schema else ""
    if not code_column:
        raise ValueError("research_evidence_code_column_missing")
    selected_columns = [
        column for column in (
            code_column, "trade_date", "name", "industry", "feature_observed_at",
            *_TECHNICAL_COLUMNS, *_FUNDAMENTAL_COLUMNS,
        ) if column in schema
    ]
    selected_columns.extend(
        sorted(column for column in schema if column.startswith("event_") and column not in selected_columns)
    )
    selected_columns.extend(
        sorted(
            column for column in schema
            if any(token in column.lower() for token in ("spx", "ixic", "dji", "hsi", "usdcnh"))
            and column not in selected_columns
        )
    )
    frame = pd.read_parquet(feature_path, columns=selected_columns)
    target = _code_key(code)
    matches = frame.loc[frame[code_column].map(_code_key).eq(target)].copy()
    if matches.empty:
        raise ValueError(f"research_evidence_code_missing:{market}:{code}")
    if "trade_date" in matches.columns:
        matches["_trade_date_key"] = matches["trade_date"].map(_date_key)
        matches = matches.sort_values("_trade_date_key")
    row = matches.iloc[-1]
    row_mapping = row.to_dict()
    observed_at = _date_key(row_mapping.get("trade_date")) or snapshot_date
    event_columns = tuple(column for column in row_mapping if column.startswith("event_"))
    context_columns = tuple(
        column for column in row_mapping
        if any(token in column.lower() for token in ("spx", "ixic", "dji", "hsi", "usdcnh"))
    )
    instrument = {
        "code": _text(row_mapping.get(code_column)) or _text(code),
        "name": _text(row_mapping.get("name")) or _text(code),
    }
    if _text(row_mapping.get("industry")):
        instrument["industry"] = _text(row_mapping.get("industry"))
    source_relative = feature_path.relative_to(root).as_posix()
    evidence: dict[str, object] = {
        "schema_version": MULTI_AGENT_SCHEMA_VERSION,
        "market": market,
        "as_of": observed_at,
        "instrument": instrument,
        "research_only": True,
        "execution_effect": "none_research_only",
        "sources": [{
            "kind": "feature_snapshot",
            "path": source_relative,
            "snapshot_date": snapshot_date,
            "sha256": _sha256_path(feature_path),
            "matched_rows": int(len(matches)),
        }],
        "facts": {
            "technical": _numeric_facts(row_mapping, _TECHNICAL_COLUMNS),
            "fundamentals": _numeric_facts(row_mapping, _FUNDAMENTAL_COLUMNS),
            "event_lite": _numeric_facts(row_mapping, event_columns),
            "global_context": _numeric_facts(row_mapping, context_columns),
        },
        "limitations": [
            "Only persisted, point-in-time research features are supplied.",
            "This artifact cannot create orders or alter formal accounts.",
            "Absent facts must remain unverified rather than inferred.",
        ],
    }
    catalog_record = _read_catalog_record(root, market=market, code=instrument["code"])
    if catalog_record is not None:
        evidence["research_catalog"] = catalog_record
    return evidence


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prompt(role: str, *, evidence: dict[str, object], prior: dict[str, object]) -> str:
    role_labels = {
        "market": "市场与技术研究员",
        "fundamentals": "基本面研究员",
        "news": "事件与新闻研究员",
        "bull": "多头研究员",
        "bear": "空头研究员",
        "risk": "风险委员会",
        "digest": "投研简报编辑",
    }
    supplied = {
        "evidence": evidence,
        "upstream_roles": prior,
    }
    return (
        f"你是{role_labels[role]}，只基于给定的项目内证据完成研究。\n"
        f"证据：{json.dumps(supplied, ensure_ascii=False, default=str)}\n"
        "规则：只输出一个 JSON 对象；区分事实、推断和待核验项；不能编造具体数字、公告或来源；"
        "不得给出交易指令、目标价或收益承诺。stance 只能是 observe、risk_off、"
        "needs_verification、research_positive 四者之一。输出至少包含 summary、stance、"
        "risks（数组）、confidence（0-100 整数）、facts_to_verify（数组）。"
    )


def _fallback(role: str, error: str) -> dict[str, object]:
    return {
        "status": "degraded",
        "summary": f"{role} role unavailable; retain project evidence for manual verification.",
        "stance": "needs_verification",
        "risks": ["model_output_unavailable"],
        "facts_to_verify": ["Review the persisted evidence and source manifests."],
        "confidence": 0,
        "error": error[:1000],
    }


def _run_role(
    *,
    client: ResearchLLMClient,
    role: str,
    evidence: dict[str, object],
    prior: dict[str, object],
    model: str,
    audit_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    prompt = _prompt(role, evidence=evidence, prior=prior)
    try:
        response = client.complete(
            role=role,
            prompt=prompt,
            model=model,
            max_output_tokens=1800 if role == "digest" else 3000,
        )
    except Exception as exc:  # noqa: BLE001 - a single role must not erase the audit trail
        response = ResearchLLMResponse(
            content="",
            model=model,
            status="error",
            error=str(exc),
        )
    parsed: dict[str, object]
    if response.status != "ok":
        parsed = _fallback(role, response.error or "model_call_failed")
    else:
        try:
            parsed = {
                key: _json_safe(value)
                for key, value in extract_json_object(response.content).items()
            }
            parsed["status"] = "complete"
        except ValueError as exc:
            parsed = _fallback(role, str(exc))
    audit = {
        "schema_version": MULTI_AGENT_SCHEMA_VERSION,
        "role": role,
        "prompt": prompt,
        "prompt_sha256": _canonical_hash(prompt),
        "response": asdict(response),
        "parsed": parsed,
    }
    write_text_atomic(audit_path, json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return parsed, audit


def _render_full_report(
    *,
    evidence: dict[str, object],
    roles: dict[str, dict[str, object]],
    model: str,
) -> str:
    instrument = evidence.get("instrument") if isinstance(evidence.get("instrument"), dict) else {}
    title = f"{instrument.get('name') or instrument.get('code') or 'instrument'} 多角色投研报告"
    lines = [
        f"# {title}",
        "",
        f"- 数据截止：{evidence.get('as_of')}",
        f"- 模型：{model}",
        "- 边界：仅研究，不产生订单、不改变正式账户或模型注册表。",
        "",
        "## 项目内证据",
        "",
        "```json",
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
        "```",
    ]
    for role, result in roles.items():
        lines.extend([
            "",
            f"## {role}",
            "",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            "```",
        ])
    return "\n".join(lines) + "\n"


def _render_digest(evidence: dict[str, object], roles: dict[str, dict[str, object]]) -> str:
    digest = roles.get("digest") or {}
    risk = roles.get("risk") or {}
    summary = _text(digest.get("summary")) or _text(risk.get("summary"))
    stance = _text(digest.get("stance")) or _text(risk.get("stance")) or "needs_verification"
    risks = digest.get("risks") if isinstance(digest.get("risks"), list) else risk.get("risks")
    lines = [
        f"# 研究简报：{(evidence.get('instrument') or {}).get('name', '')}",
        "",
        f"- 数据截止：{evidence.get('as_of')}",
        f"- 研究状态：{stance}",
        f"- 摘要：{summary or '模型输出待人工核验。'}",
        "- 风险：",
    ]
    for item in (risks or ["项目内证据有限，需人工核验。"])[:3]:
        lines.append(f"  - {_text(item)}")
    lines.extend([
        "",
        "> 仅供研究与信息整理；不构成投资建议，不产生交易指令。",
        "",
    ])
    return "\n".join(lines)


def run_multi_agent_research(
    *,
    repo_root: str | Path,
    evidence: dict[str, object],
    llm_client: ResearchLLMClient,
    model: str,
    run_id: str | None = None,
) -> dict[str, object]:
    """Run the seven-role workflow and durably persist every model exchange."""
    market = _text(evidence.get("market"))
    if market not in _MARKETS:
        raise ValueError(f"multi_agent_market_invalid:{market}")
    instrument = evidence.get("instrument")
    if not isinstance(instrument, dict) or not _text(instrument.get("code")):
        raise ValueError("multi_agent_instrument_missing")
    code = _text(instrument["code"])
    safe_code = re.sub(r"[^A-Za-z0-9._-]+", "_", code)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chosen_run_id = run_id or f"{timestamp}-{safe_code}-{uuid.uuid4().hex[:8]}"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", chosen_run_id):
        raise ValueError("multi_agent_run_id_invalid")
    output_dir = (
        Path(repo_root) / "reports" / "research" / "multi_agent" / market / safe_code / chosen_run_id
    )
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=False)

    roles: dict[str, dict[str, object]] = {}
    audits: list[dict[str, object]] = []
    role_order = ("market", "fundamentals", "news", "bull", "bear", "risk", "digest")
    for index, role in enumerate(role_order, start=1):
        prior = dict(roles)
        parsed, audit = _run_role(
            client=llm_client,
            role=role,
            evidence=evidence,
            prior=prior,
            model=model,
            audit_path=audit_dir / f"{index:02d}_{role}.json",
        )
        roles[role] = parsed
        audits.append(audit)

    degraded_roles = [role for role, value in roles.items() if value.get("status") != "complete"]
    status = "completed_with_degradation" if degraded_roles else "complete"
    result_payload = {
        "schema_version": MULTI_AGENT_SCHEMA_VERSION,
        "status": status,
        "market": market,
        "as_of": evidence.get("as_of"),
        "instrument": instrument,
        "evidence": evidence,
        "roles": roles,
        "execution_effect": "none_research_only",
    }
    manifest = {
        "schema_version": MULTI_AGENT_SCHEMA_VERSION,
        "run_id": chosen_run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "market": market,
        "instrument": instrument,
        "model": model,
        "evidence_sha256": _canonical_hash(evidence),
        "role_order": list(role_order),
        "degraded_roles": degraded_roles,
        "usage": {
            "prompt_tokens": sum(int(item["response"].get("prompt_tokens") or 0) for item in audits),
            "completion_tokens": sum(int(item["response"].get("completion_tokens") or 0) for item in audits),
            "wall_seconds": round(sum(float(item["response"].get("wall_seconds") or 0.0) for item in audits), 3),
        },
        "execution_effect": "none_research_only",
    }
    write_text_atomic(output_dir / "result.json", json.dumps(result_payload, ensure_ascii=False, indent=2, default=str))
    write_text_atomic(output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    write_text_atomic(output_dir / "digest.md", _render_digest(evidence, roles))
    write_text_atomic(output_dir / "full_report.md", _render_full_report(evidence=evidence, roles=roles, model=model))
    return {
        "status": status,
        "run_id": chosen_run_id,
        "output_dir": output_dir.as_posix(),
        "manifest_path": (output_dir / "manifest.json").as_posix(),
        "result_path": (output_dir / "result.json").as_posix(),
        "degraded_roles": degraded_roles,
    }


__all__ = [
    "ArkCLIResearchClient",
    "ResearchLLMClient",
    "ResearchLLMResponse",
    "build_research_evidence",
    "extract_json_object",
    "run_multi_agent_research",
]
