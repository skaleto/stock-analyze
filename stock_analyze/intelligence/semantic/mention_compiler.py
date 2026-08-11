"""Compile minimal source mentions into validated semantic event results."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from .contracts import (
    LITE_SCHEMA_VERSION,
    SemanticContractError,
    _relocate_lite_quote,
    parse_lite_semantic_document_result,
)
from .document_ir import (
    DocumentIRPreflightError,
    ir_nodes_by_id,
    preflight_evidence_packet,
)
from .mention_contracts import (
    EventMention,
    MentionDocumentResult,
    MentionEvidence,
    MentionFact,
)
from .taxonomy import EventTaxonomy, VALID_SUBJECT_ROLES
from .validation import (
    CandidateValidationError,
    numeric_raw_value_is_ambiguous,
    validate_candidate,
)


MENTION_COMPILER_VERSION = "mention-compiler-v1"
IR_MENTION_COMPILER_VERSION = "mention-compiler-v3-ir"


@dataclass(frozen=True)
class RejectedMention:
    mention_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class MentionCompilation:
    result: dict[str, object]
    accepted_mentions: int
    rejected_mentions: tuple[RejectedMention, ...]
    dropped_items: int


class _MentionCompileError(ValueError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class _EvidenceRegistry:
    def __init__(
        self,
        prefix: str,
        chunks: Mapping[str, Mapping[str, object]],
    ) -> None:
        self.prefix = prefix
        self.chunks = chunks
        self._ids: dict[tuple[str, str], str] = {}
        self.rows: list[dict[str, str]] = []

    def add_many(
        self,
        evidence: Sequence[MentionEvidence],
    ) -> list[str]:
        return [self.add(item) for item in evidence]

    def add(self, evidence: MentionEvidence) -> str:
        chunk_id = evidence.chunk_id
        chunk = self.chunks.get(chunk_id)
        if not isinstance(chunk, Mapping):
            matches = [
                candidate
                for candidate in self.chunks
                if candidate.startswith(f"{chunk_id}-")
            ]
            if len(matches) == 1:
                chunk_id = matches[0]
                chunk = self.chunks[chunk_id]
        if not isinstance(chunk, Mapping):
            raise _MentionCompileError("mention_evidence_chunk_missing")
        try:
            _, _, source_quote = _relocate_lite_quote(
                str(chunk.get("text") or ""),
                evidence.quote,
                chunk_id=chunk_id,
            )
        except SemanticContractError as exc:
            raise _MentionCompileError(exc.code) from exc
        key = (chunk_id, source_quote)
        if key in self._ids:
            return self._ids[key]
        evidence_id = f"{self.prefix}-e{len(self.rows) + 1}"
        self._ids[key] = evidence_id
        self.rows.append(
            {
                "evidence_id": evidence_id,
                "chunk_id": chunk_id,
                "quote": source_quote,
            }
        )
        return evidence_id


def compile_mentions(
    document_result: MentionDocumentResult,
    *,
    taxonomy: EventTaxonomy,
    chunks: Mapping[str, Mapping[str, object]],
    document: Mapping[str, object],
    entity_whitelist: Sequence[Mapping[str, object]],
    taxonomy_candidates: Sequence[str] = (),
    document_ir: Mapping[str, object] | None = None,
) -> MentionCompilation:
    allowed_types = set(taxonomy_candidates) & taxonomy.event_types
    if not allowed_types:
        allowed_types = set(taxonomy.event_types)
    whitelist = {
        str(item.get("entity_id") or ""): frozenset(
            str(role) for role in item.get("allowed_roles", [])
        )
        for item in entity_whitelist
        if str(item.get("entity_id") or "")
    }
    accepted_events: list[dict[str, object]] = []
    accepted_evidence: list[dict[str, str]] = []
    rejected: list[RejectedMention] = []
    dropped_items = 0

    for index, mention in enumerate(document_result.mentions, start=1):
        if mention.event_type not in allowed_types:
            rejected.append(
                RejectedMention(
                    mention.mention_id,
                    ("mention_event_type_not_routed",),
                )
            )
            continue
        if _guarantee_counter_scope_mismatch(
            mention,
            document_result.mentions,
        ):
            rejected.append(
                RejectedMention(
                    mention.mention_id,
                    ("mention_counter_guarantee_subject_mismatch",),
                )
            )
            continue
        registry = _EvidenceRegistry(f"m{index}", chunks)
        try:
            event, dropped = _compile_one(
                mention,
                taxonomy=taxonomy,
                registry=registry,
                chunks=chunks,
                document=document,
                whitelist=whitelist,
                document_ir=document_ir,
            )
            dropped_items += dropped
        except _MentionCompileError as exc:
            reason = exc.code
            if exc.detail:
                reason = f"{reason}:{exc.detail}"
            rejected.append(
                RejectedMention(mention.mention_id, (reason,))
            )
            continue
        accepted_events.append(event)
        accepted_evidence.extend(registry.rows)

    if accepted_events:
        no_event_reason = None
    elif document_result.mentions:
        reasons = sorted(
            {
                reason
                for item in rejected
                for reason in item.reason_codes
            }
        )
        no_event_reason = "all_mentions_rejected:" + ",".join(reasons)
    else:
        no_event_reason = document_result.no_event_reason or "no mentions"
    return MentionCompilation(
        result={
            "document_id": document_result.document_id,
            "schema_version": LITE_SCHEMA_VERSION,
            "events": accepted_events,
            "evidence": accepted_evidence,
            "no_event_reason": no_event_reason,
        },
        accepted_mentions=len(accepted_events),
        rejected_mentions=tuple(rejected),
        dropped_items=dropped_items,
    )


_COMPANY_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "公司",
)


def _guarantee_counter_scope_mismatch(
    mention: EventMention,
    mentions: Sequence[EventMention],
) -> bool:
    if mention.event_type != "guarantee":
        return False
    counter_facts = [
        fact for fact in mention.facts if fact.name == "counter_guarantee"
    ]
    if not counter_facts:
        return False
    current_names = {
        subject.name
        for subject in mention.subjects
        if subject.role == "beneficiary"
    }
    all_names = {
        subject.name
        for item in mentions
        if item.event_type == "guarantee"
        for subject in item.subjects
        if subject.role == "beneficiary"
    }
    if len(all_names) < 2:
        return False
    for fact in counter_facts:
        context = _compact(
            fact.raw_value + "".join(item.quote for item in fact.evidence)
        )
        matched_names = {
            name for name in all_names if _company_name_matches(name, context)
        }
        if matched_names and matched_names.isdisjoint(current_names):
            return True
    return False


def _company_name_matches(name: str, context: str) -> bool:
    core = _compact(name)
    for suffix in _COMPANY_SUFFIXES:
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    candidates = [core]
    if len(core) >= 4:
        candidates.append(core[:4])
    return any(len(candidate) >= 3 and candidate in context for candidate in candidates)


@dataclass(frozen=True)
class _IRFactContext:
    unit: str
    period: str | None
    evidence: tuple[MentionEvidence, ...]


_IR_ROW_LABEL_TERMS = {
    "revenue": ("营业收入", "收入"),
    "revenue_lower": ("营业收入", "收入"),
    "revenue_upper": ("营业收入", "收入"),
    "revenue_yoy": ("营业收入", "收入"),
    "net_profit": ("归属于上市公司股东的净利润", "归母净利润", "净利润"),
    "net_profit_lower": ("归属于上市公司股东的净利润", "归母净利润", "净利润"),
    "net_profit_upper": ("归属于上市公司股东的净利润", "归母净利润", "净利润"),
    "net_profit_yoy": ("归属于上市公司股东的净利润", "归母净利润", "净利润"),
    "total_assets": ("总资产",),
    "net_assets": ("净资产", "归属于上市公司股东的所有者权益"),
    "share_count": ("股份数量", "股数", "数量"),
    "cumulative_share_count": ("累计", "合计", "持股数量"),
    "share_ratio": ("比例", "持股比例"),
    "guarantee_amount": ("担保金额",),
    "guarantee_balance": ("担保余额",),
    "case_amount": ("涉案金额", "诉讼金额", "本金"),
    "contract_amount": ("合同金额", "中标金额"),
}


def _ir_fact_context(
    fact: MentionFact,
    evidence: Sequence[MentionEvidence],
    *,
    document_ir: Mapping[str, object] | None,
    spec,
) -> _IRFactContext | None:
    if document_ir is None:
        return None
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError as exc:
        raise _MentionCompileError("table_semantic_ir_invalid", detail=exc.code) from exc
    referenced_table_ids = [
        item.chunk_id
        for item in evidence
        if _TABLE_CELL_PATTERN.fullmatch(item.chunk_id)
    ]
    value_ids = [
        node_id
        for node_id in referenced_table_ids
        if isinstance(nodes.get(node_id), Mapping)
        and nodes[node_id].get("node_type") == "table_cell"
        and nodes[node_id].get("semantic_role") == "value"
    ]
    if not referenced_table_ids:
        return None
    if len(set(value_ids)) != 1:
        raise _MentionCompileError("table_semantic_path_missing")
    value_id = value_ids[0]
    try:
        preflight_evidence_packet(document_ir, [value_id])
    except DocumentIRPreflightError as exc:
        translated = {
            "evidence_packet_table_path_missing": "table_semantic_path_missing",
            "evidence_packet_unit_conflict": "table_semantic_unit_conflict",
            "evidence_packet_unit_missing": "table_semantic_unit_missing",
        }.get(exc.code, "table_semantic_ir_invalid")
        raise _MentionCompileError(translated, detail=exc.code) from exc
    node = nodes[value_id]
    if _compact(str(node.get("raw_value") or "")) != _compact(fact.raw_value):
        raise _MentionCompileError("table_semantic_raw_value_mismatch")

    row_path = node.get("row_header_path")
    column_path = node.get("column_header_path")
    assert isinstance(row_path, list)
    assert isinstance(column_path, list)
    row_nodes = _ir_path_nodes(nodes, row_path)
    column_nodes = _ir_path_nodes(nodes, column_path)
    row_text = " ".join(str(item.get("text") or "") for item in row_nodes)
    expected_terms = tuple(getattr(spec, "evidence_terms_any", ()) or ())
    if not expected_terms:
        expected_terms = _IR_ROW_LABEL_TERMS.get(fact.name, ())
    if expected_terms and not any(term in row_text for term in expected_terms):
        raise _MentionCompileError("table_semantic_label_mismatch")

    resolution = node.get("unit_resolution")
    assert isinstance(resolution, Mapping)
    unit = str(resolution.get("value") or "")
    if not unit:
        raise _MentionCompileError("table_semantic_unit_missing")
    period_values = [
        str(item.get("text") or "").strip()
        for item in column_nodes
        if str(item.get("text") or "").strip()
    ]
    derived_nodes = [*row_nodes, *column_nodes]
    unit_source_id = str(resolution.get("source_node_id") or "")
    if unit_source_id and unit_source_id in nodes:
        derived_nodes.append(nodes[unit_source_id])
    derived: list[MentionEvidence] = []
    seen: set[tuple[str, str]] = set()
    for derived_node in derived_nodes:
        node_id = str(derived_node.get("node_id") or "")
        quote = str(
            derived_node.get("text")
            or derived_node.get("raw_value")
            or ""
        )
        key = (node_id, quote)
        if node_id and quote and key not in seen:
            seen.add(key)
            derived.append(MentionEvidence(chunk_id=node_id, quote=quote))
    return _IRFactContext(
        unit=unit,
        period=period_values[-1] if period_values else None,
        evidence=tuple(derived),
    )


def _ir_path_nodes(
    nodes: Mapping[str, Mapping[str, object]],
    path: Sequence[object],
) -> list[Mapping[str, object]]:
    resolved: list[Mapping[str, object]] = []
    for raw in path:
        if not isinstance(raw, Mapping):
            raise _MentionCompileError("table_semantic_path_missing")
        node = nodes.get(str(raw.get("node_id") or ""))
        if node is None:
            raise _MentionCompileError("table_semantic_path_missing")
        resolved.append(node)
    return resolved


def _compile_one(
    mention: EventMention,
    *,
    taxonomy: EventTaxonomy,
    registry: _EvidenceRegistry,
    chunks: Mapping[str, Mapping[str, object]],
    document: Mapping[str, object],
    whitelist: Mapping[str, frozenset[str]],
    document_ir: Mapping[str, object] | None,
) -> tuple[dict[str, object], int]:
    try:
        taxonomy_event = taxonomy.event(mention.event_type)
    except KeyError as exc:
        raise _MentionCompileError("mention_event_type_unknown") from exc

    subjects = []
    for subject in mention.subjects:
        if subject.role not in VALID_SUBJECT_ROLES:
            raise _MentionCompileError("mention_subject_role_invalid")
        evidence_ids, _ = _valid_evidence(registry, subject.evidence)
        if not evidence_ids:
            raise _MentionCompileError("mention_subject_evidence_missing")
        entity_id = (
            str(document.get("ts_code") or "").strip()
            if subject.role == "issuer"
            else f"external:{subject.name.strip()}"
        )
        if not entity_id or entity_id == "external:":
            raise _MentionCompileError("mention_subject_missing")
        subjects.append(
            {
                "entity_id": entity_id,
                "role": subject.role,
                "evidence_ids": evidence_ids,
            }
        )

    facts: list[dict[str, object]] = []
    dropped = 0
    shared_unit = None
    shared_unit_evidence_ids: list[str] = []
    for fact in mention.facts:
        if fact.name != "currency":
            continue
        inferred = _infer_unit(fact.raw_value)
        if inferred is None:
            continue
        evidence_ids, _ = _valid_evidence(registry, fact.evidence)
        if evidence_ids:
            shared_unit = inferred
            shared_unit_evidence_ids = evidence_ids
            break
    source_facts = list(mention.facts)
    source_facts.extend(
        MentionFact(
            name=item.kind,
            raw_value=item.raw_value,
            evidence=item.evidence,
        )
        for item in mention.dates
        if item.kind in taxonomy_event.declared_facts
    )
    ir_periods: dict[str, str] = {}
    for fact in source_facts:
        if fact.name not in taxonomy_event.declared_facts:
            dropped += 1
            continue
        evidence_ids, valid_evidence = _valid_evidence(
            registry,
            fact.evidence,
        )
        if not _raw_value_grounded(fact.raw_value, valid_evidence):
            recovered_ids, recovered_evidence = _cross_chunk_evidence(
                registry,
                fact.evidence,
                fact.raw_value,
                chunks,
            )
            if recovered_ids:
                evidence_ids = recovered_ids
                valid_evidence = recovered_evidence
        if not evidence_ids:
            dropped += 1
            continue
        spec = taxonomy_event.fact_specs.get(fact.name)
        ir_context = _ir_fact_context(
            fact,
            valid_evidence,
            document_ir=document_ir,
            spec=spec,
        )
        if ir_context is not None:
            derived_ids = registry.add_many(ir_context.evidence)
            evidence_ids = list(
                dict.fromkeys([*evidence_ids, *derived_ids])
            )
            valid_evidence = tuple(
                dict.fromkeys([*valid_evidence, *ir_context.evidence])
            )
        raw_value, endpoint_unit = _resolve_fact_raw_value(
            fact,
            valid_evidence,
            value_type=(spec.value_type if spec is not None else None),
        )
        if raw_value is None:
            dropped += 1
            continue
        if (
            mention.event_type == "shareholder_change"
            and fact.name == "action"
            and not _valid_shareholder_action(raw_value)
        ):
            raise _MentionCompileError("mention_shareholder_action_invalid")
        resolved = MentionFact(
            name=fact.name,
            raw_value=raw_value,
            evidence=valid_evidence,
        )
        unit = endpoint_unit or (
            ir_context.unit if ir_context is not None else None
        )
        if spec is not None and spec.value_type in {"number", "ratio"}:
            unit = unit or _infer_unit(
                raw_value,
                value_type=spec.value_type,
            )
            if unit is None:
                table_unit = _table_column_unit(
                    valid_evidence,
                    chunks,
                    value_type=spec.value_type,
                )
                if table_unit is not None:
                    unit, unit_evidence = table_unit
                    evidence_ids = list(
                        dict.fromkeys(
                            [*evidence_ids, registry.add(unit_evidence)]
                        )
                    )
            unit = unit or _schema_implied_unit(spec, raw_value)
            if unit is None and spec.value_type == "number":
                unit = shared_unit
                if unit is not None:
                    evidence_ids = list(
                        dict.fromkeys(
                            [*evidence_ids, *shared_unit_evidence_ids]
                        )
                    )
        if spec is not None and not _fact_source_compatible(
            resolved,
            spec,
            unit=unit,
        ):
            dropped += 1
            continue
        range_facts = (
            _split_numeric_range(resolved, taxonomy_event.declared_facts)
            if spec is not None and spec.value_type == "number"
            else None
        )
        if range_facts is not None:
            for name, raw_value, unit in range_facts:
                facts.append(
                    _semantic_fact(
                        name,
                        raw_value,
                        evidence_ids,
                        unit=unit,
                        period=(
                            ir_context.period
                            if ir_context is not None
                            else None
                        ),
                    )
                )
            continue
        if (
            spec is not None
            and spec.value_type in {"number", "ratio"}
            and numeric_raw_value_is_ambiguous(raw_value, fact.name)
        ):
            dropped += 1
            continue
        facts.append(
            _semantic_fact(
                fact.name,
                raw_value,
                evidence_ids,
                unit=unit,
                period=(
                    ir_context.period if ir_context is not None else None
                ),
            )
        )
        if ir_context is not None and ir_context.period:
            ir_periods[fact.name] = ir_context.period

    if mention.event_type == "earnings_flash":
        compared = {
            ir_periods[name]
            for name in ("revenue", "net_profit")
            if name in ir_periods
        }
        if len(compared) > 1:
            raise _MentionCompileError("table_semantic_period_mismatch")

    if (
        mention.event_type == "litigation_arbitration"
        and not any(item["name"] == "case_amount" for item in facts)
    ):
        derived = _litigation_principal_fact(mention, registry, chunks)
        if derived is not None:
            facts.append(derived)
    if (
        mention.event_type == "litigation_arbitration"
        and not any(item["name"] == "issuer_role" for item in facts)
    ):
        derived = _litigation_issuer_role_fact(mention, registry, chunks)
        if derived is not None:
            facts.append(derived)
    grounded_status = ""
    status_evidence_ids: list[str] = []
    if mention.status is not None:
        status_evidence_ids, valid_status_evidence = _valid_evidence(
            registry,
            mention.status.evidence,
        )
        if status_evidence_ids and _raw_value_grounded(
            mention.status.raw_value,
            valid_status_evidence,
        ):
            grounded_status = mention.status.raw_value
        else:
            dropped += 1

    if (
        grounded_status
        and "case_stage" in taxonomy_event.declared_facts
        and not any(item["name"] == "case_stage" for item in facts)
    ):
        facts.append(
            _semantic_fact(
                "case_stage",
                grounded_status,
                status_evidence_ids,
                unit=None,
            )
        )

    dates = []
    for item in mention.dates:
        if item.kind in taxonomy_event.declared_facts:
            continue
        evidence_ids, valid_evidence = _valid_evidence(
            registry,
            item.evidence,
        )
        if not evidence_ids:
            dropped += 1
            continue
        if not _raw_value_grounded(item.raw_value, valid_evidence):
            dropped += 1
            continue
        normalized = _normalize_mention_date(item.raw_value)
        if normalized is None:
            dropped += 1
            continue
        dates.append(
            {
                "kind": item.kind,
                "value": normalized,
                "evidence_ids": evidence_ids,
            }
        )

    lifecycle = _infer_lifecycle(
        mention.event_type,
        title=str(document.get("title") or ""),
        status=grounded_status,
        source_text="".join(str(row.get("quote") or "") for row in registry.rows),
        require_cited_status=document_ir is not None,
    )
    if lifecycle not in taxonomy_event.allowed_lifecycle:
        lifecycle = "uncertain"
    event = {
        "event_type": mention.event_type,
        "lifecycle": lifecycle,
        "subjects": subjects,
        "facts": facts,
        "effective_dates": dates,
        "conditions": [],
        "conflicts": [],
        "missing_required_fields": [],
    }
    single_result = {
        "document_id": int(document.get("id") or 0),
        "schema_version": LITE_SCHEMA_VERSION,
        "events": [event],
        "evidence": registry.rows,
        "no_event_reason": None,
    }
    try:
        parsed = parse_lite_semantic_document_result(
            single_result,
            taxonomy,
            chunks,
        )
        validate_candidate(
            parsed.events[0],
            parsed.evidence,
            chunks,
            taxonomy=taxonomy,
            issuer_entity_id=str(document.get("ts_code") or ""),
            entity_whitelist=whitelist,
            document_metadata=document,
        )
    except (SemanticContractError, CandidateValidationError) as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        raise _MentionCompileError(
            f"mention_candidate_{code}",
            detail=str(getattr(exc, "detail", "") or ""),
        ) from exc
    return event, dropped


def _semantic_fact(
    name: str,
    raw_value: str,
    evidence_ids: Sequence[str],
    *,
    unit: str | None,
    period: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "raw_value": raw_value,
        "numeric_value": None,
        "unit": unit,
        "currency": None,
        "period": period,
        "evidence_ids": list(evidence_ids),
    }


_SHAREHOLDER_ACTION_TERMS = (
    "增持",
    "减持",
    "增加",
    "减少",
    "上升",
    "下降",
    "获得股份",
    "股份转让",
    "协议转让",
    "无偿划转",
    "划转",
    "被动稀释",
    "稀释",
    "收购",
    "出售",
)


def _valid_shareholder_action(raw_value: str) -> bool:
    compact = _compact(raw_value)
    return bool(
        compact
        and len(compact) <= 80
        and any(term in compact for term in _SHAREHOLDER_ACTION_TERMS)
    )


def _raw_value_grounded(
    raw_value: str,
    evidence: Sequence[MentionEvidence],
) -> bool:
    compact_raw = _compact(raw_value)
    compact_quotes = [_compact(item.quote) for item in evidence]
    return bool(
        compact_raw
        and (
            any(compact_raw in quote for quote in compact_quotes)
            or compact_raw in "".join(compact_quotes)
        )
    )


def _valid_evidence(
    registry: _EvidenceRegistry,
    evidence: Sequence[MentionEvidence],
) -> tuple[list[str], tuple[MentionEvidence, ...]]:
    evidence_ids: list[str] = []
    valid: list[MentionEvidence] = []
    for item in evidence:
        try:
            evidence_id = registry.add(item)
        except _MentionCompileError:
            continue
        evidence_ids.append(evidence_id)
        valid.append(item)
    return evidence_ids, tuple(valid)


def _cross_chunk_evidence(
    registry: _EvidenceRegistry,
    evidence: Sequence[MentionEvidence],
    raw_value: str,
    chunks: Mapping[str, Mapping[str, object]],
) -> tuple[list[str], tuple[MentionEvidence, ...]]:
    chunk_ids = list(dict.fromkeys(item.chunk_id for item in evidence))
    source_rows = [
        (chunk_id, str(chunks.get(chunk_id, {}).get("text") or ""))
        for chunk_id in chunk_ids
    ]
    compact_raw = _compact(raw_value)
    if not source_rows or not compact_raw:
        return [], ()
    if compact_raw not in _compact("".join(text for _, text in source_rows)):
        ordered_ids = list(chunks)
        candidate_windows: list[list[str]] = []
        for chunk_id in chunk_ids:
            if chunk_id not in chunks:
                continue
            index = ordered_ids.index(chunk_id)
            for start in range(max(0, index - 1), index + 1):
                for end in range(index + 1, min(len(ordered_ids), index + 3) + 1):
                    window = ordered_ids[start:end]
                    if compact_raw in _compact(
                        "".join(str(chunks[item].get("text") or "") for item in window)
                    ):
                        candidate_windows.append(window)
        if not candidate_windows:
            return [], ()
        best = min(candidate_windows, key=lambda window: (len(window), window))
        source_rows = [
            (chunk_id, str(chunks[chunk_id].get("text") or ""))
            for chunk_id in best
        ]
    recovered = tuple(
        MentionEvidence(chunk_id=chunk_id, quote=text)
        for chunk_id, text in source_rows
        if text
    )
    return registry.add_many(recovered), recovered


def _resolve_fact_raw_value(
    fact: MentionFact,
    evidence: Sequence[MentionEvidence],
    *,
    value_type: str | None,
) -> tuple[str | None, str | None]:
    if _raw_value_grounded(fact.raw_value, evidence):
        return fact.raw_value, None
    if value_type in {"number", "ratio"}:
        split_scalar = _scalar_with_separate_table_unit(
            fact.raw_value,
            evidence,
            value_type=value_type,
        )
        if split_scalar is not None:
            return split_scalar
    if value_type in {"text", "period"}:
        return None, None
    endpoint = _range_endpoint(fact.name, evidence)
    if endpoint is not None:
        return endpoint
    return None, None


_SCALAR_PATTERN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_TABLE_CELL_PATTERN = re.compile(
    r"^(?P<table>.+)-r(?P<row>\d+)-c(?P<column>\d+)$"
)


def _scalar_with_separate_table_unit(
    raw_value: str,
    evidence: Sequence[MentionEvidence],
    *,
    value_type: str,
) -> tuple[str, str] | None:
    """Split a model-combined scalar/unit only when both table cells are cited."""

    matches = _SCALAR_PATTERN.findall(_compact(raw_value))
    unit = _infer_unit(raw_value, value_type=value_type)
    if len(matches) != 1 or unit is None:
        return None
    scalar = matches[0]
    scalar_grounded = any(
        _compact(scalar) in _compact(item.quote) for item in evidence
    )
    unit_grounded = any(
        _infer_unit(item.quote, value_type=value_type) == unit
        for item in evidence
    )
    if not scalar_grounded or not unit_grounded:
        return None
    return scalar, unit


def _table_column_unit(
    evidence: Sequence[MentionEvidence],
    chunks: Mapping[str, Mapping[str, object]],
    *,
    value_type: str,
) -> tuple[str, MentionEvidence] | None:
    if value_type != "ratio":
        return None
    for item in evidence:
        match = _TABLE_CELL_PATTERN.fullmatch(item.chunk_id)
        if match is None:
            continue
        row = int(match.group("row"))
        prefix = match.group("table")
        column = match.group("column")
        candidates: list[tuple[int, str, str]] = []
        for chunk_id, chunk in chunks.items():
            header_match = _TABLE_CELL_PATTERN.fullmatch(chunk_id)
            if (
                header_match is None
                or header_match.group("table") != prefix
                or header_match.group("column") != column
            ):
                continue
            header_row = int(header_match.group("row"))
            text = str(chunk.get("text") or "")
            if header_row < row and "%" in text:
                candidates.append((row - header_row, chunk_id, text))
        if candidates:
            _, chunk_id, _ = min(candidates)
            return "%", MentionEvidence(chunk_id=chunk_id, quote="%")
    return None


_UNIT_KINDS = {
    "元": "currency",
    "万元": "currency",
    "亿元": "currency",
    "美元": "currency",
    "万美元": "currency",
    "亿美元": "currency",
    "欧元": "currency",
    "万欧元": "currency",
    "亿欧元": "currency",
    "港元": "currency",
    "万港元": "currency",
    "亿港元": "currency",
    "人民币元/股": "price",
    "元/股": "price",
    "股": "shares",
    "万股": "shares",
    "亿股": "shares",
    "%": "ratio",
    "ratio": "ratio",
    "吨": "mass",
    "万吨": "mass",
    "吨/年": "mass_rate",
    "万吨/年": "mass_rate",
    "台": "count",
    "万台": "count",
    "片": "count",
    "万片": "count",
    "平方米": "area",
    "万平方米": "area",
    "台/年": "count_rate",
    "万台/年": "count_rate",
    "片/年": "count_rate",
    "万片/年": "count_rate",
    "平方米/年": "area_rate",
    "万平方米/年": "area_rate",
    "MW": "power",
    "GW": "power",
    "MWh": "energy",
    "GWh": "energy",
}


def _fact_source_compatible(
    fact: MentionFact,
    spec,
    *,
    unit: str | None,
) -> bool:
    context = "".join(item.quote for item in fact.evidence)
    if fact.name == "case_amount" and any(
        token in context
        for token in ("受理费", "诉讼费", "保全费", "律师费")
    ):
        return False
    if spec.evidence_terms_any and not any(
        term in context for term in spec.evidence_terms_any
    ):
        return False
    if spec.value_type not in {"number", "ratio"}:
        return True
    if unit is None:
        return "unitless" in spec.allowed_unit_kinds
    return _UNIT_KINDS.get(unit) in spec.allowed_unit_kinds


def _schema_implied_unit(spec, raw_value: str) -> str | None:
    """Resolve only units that are unambiguous from the frozen fact schema."""

    if spec.value_type != "number" or tuple(spec.allowed_unit_kinds) != ("shares",):
        return None
    return "股" if len(_SCALAR_PATTERN.findall(_compact(raw_value))) == 1 else None


_PRINCIPAL_PATTERN = re.compile(
    r"(?:借款)?本金\s*(?P<amount>[-+]?\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元|元))"
)


def _litigation_principal_fact(
    mention: EventMention,
    registry: _EvidenceRegistry,
    chunks: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    for fact in mention.facts:
        if fact.name not in {"judgment_amount", "claim"}:
            continue
        for evidence in fact.evidence:
            chunk = chunks.get(evidence.chunk_id)
            source_text = (
                str(chunk.get("text") or "")
                if isinstance(chunk, Mapping)
                else ""
            )
            match = _PRINCIPAL_PATTERN.search(source_text)
            if match is None:
                continue
            source_evidence = MentionEvidence(
                chunk_id=evidence.chunk_id,
                quote=match.group(0),
            )
            evidence_id = registry.add(source_evidence)
            raw_value = match.group("amount")
            return _semantic_fact(
                "case_amount",
                raw_value,
                [evidence_id],
                unit=_infer_unit(raw_value),
            )
    return None


_ISSUER_ROLE_PATTERN = re.compile(
    r"本公司[^。；;]{0,30}?(?P<role>承担连带清偿责任|承担清偿责任|偿还|赔偿|支付)"
)


def _litigation_issuer_role_fact(
    mention: EventMention,
    registry: _EvidenceRegistry,
    chunks: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    ordered_chunk_ids = list(chunks)
    referenced = list(
        dict.fromkeys(
            evidence.chunk_id
            for fact in mention.facts
            for evidence in fact.evidence
            if evidence.chunk_id in chunks
        )
    )
    candidate_indices: list[int] = []
    for chunk_id in referenced:
        index = ordered_chunk_ids.index(chunk_id)
        for candidate in (index, index + 1, index - 1):
            if (
                0 <= candidate < len(ordered_chunk_ids)
                and candidate not in candidate_indices
            ):
                candidate_indices.append(candidate)
    for index in candidate_indices:
        chunk_id = ordered_chunk_ids[index]
        chunk = chunks[chunk_id]
        text = str(chunk.get("text") or "")
        match = _ISSUER_ROLE_PATTERN.search(text)
        if match is None:
            continue
        evidence_id = registry.add(
            MentionEvidence(chunk_id=chunk_id, quote=match.group(0))
        )
        return _semantic_fact(
            "issuer_role",
            match.group("role"),
            [evidence_id],
            unit=None,
        )
    return None


def _compact(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value))
        if not character.isspace()
    )


_RANGE_PATTERN = re.compile(
    r"(?P<lower>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:[-–—~～至])\s*"
    r"(?P<upper>[-+]?\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>万元|亿元|元/股|万股|亿股|元|股)?"
)


def _range_endpoint(
    fact_name: str,
    evidence: Sequence[MentionEvidence],
) -> tuple[str, str | None] | None:
    if fact_name.endswith("_lower"):
        endpoint = "lower"
    elif fact_name.endswith("_upper"):
        endpoint = "upper"
    else:
        return None
    for item in evidence:
        match = _RANGE_PATTERN.search(_compact(item.quote))
        if match is not None:
            return (
                match.group(endpoint),
                match.group("unit") or _infer_unit(item.quote),
            )
    return None


def _split_numeric_range(
    fact: MentionFact,
    declared_facts: frozenset[str],
) -> tuple[tuple[str, str, str | None], ...] | None:
    match = _RANGE_PATTERN.search(_compact(fact.raw_value))
    if match is None:
        return None
    lower_name = f"{fact.name}_lower"
    upper_name = f"{fact.name}_upper"
    if lower_name not in declared_facts or upper_name not in declared_facts:
        return None
    unit = match.group("unit") or _infer_unit(fact.raw_value)
    return (
        (lower_name, match.group("lower"), unit),
        (upper_name, match.group("upper"), unit),
    )


def _infer_unit(
    raw_value: str,
    *,
    value_type: str | None = None,
) -> str | None:
    compact = _compact(raw_value)
    if value_type == "ratio":
        if "%" in compact:
            return "%"
        if re.search(r"每\d+(?:\.\d+)?股(?:送|配|转|增)\d+(?:\.\d+)?股", compact):
            return "ratio"
    for unit in (
        "人民币元/股",
        "亿美元",
        "亿欧元",
        "亿港元",
        "万美元",
        "万欧元",
        "万港元",
        "万平方米/年",
        "平方米/年",
        "万吨/年",
        "吨/年",
        "万台/年",
        "台/年",
        "万片/年",
        "片/年",
        "元/股",
        "万元",
        "亿元",
        "美元",
        "欧元",
        "港元",
        "万股",
        "亿股",
        "万吨",
        "万台",
        "万片",
        "万平方米",
        "MWh",
        "GWh",
        "MW",
        "GW",
        "%",
        "元",
        "股",
        "吨",
        "台",
        "片",
        "平方米",
    ):
        if unit in compact:
            return unit
    return None


def _normalize_mention_date(raw_value: str) -> str | None:
    compact = _compact(raw_value)
    try:
        return date.fromisoformat(compact).isoformat()
    except ValueError:
        pass
    numeric = re.fullmatch(
        r"(?P<year>\d{4})[/.](?P<month>\d{1,2})[/.](?P<day>\d{1,2})",
        compact,
    )
    if numeric is not None:
        try:
            return date(
                int(numeric.group("year")),
                int(numeric.group("month")),
                int(numeric.group("day")),
            ).isoformat()
        except ValueError:
            return None
    match = re.search(
        r"(?P<year>[0-9〇零一二三四五六七八九]{4})年"
        r"(?P<month>[0-9零一二两三四五六七八九十]{1,3})月"
        r"(?P<day>[0-9零一二两三四五六七八九十廿卅]{1,3})日",
        compact,
    )
    if match is None:
        return None
    year = _year_number(match.group("year"))
    month = _small_cn_number(match.group("month"))
    day = _small_cn_number(match.group("day"))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _year_number(value: str) -> int:
    return int("".join(str(_DIGITS.get(character, character)) for character in value))


def _small_cn_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value.startswith("廿"):
        return 20 + _DIGITS.get(value[1], 0) if len(value) > 1 else 20
    if value.startswith("卅"):
        return 30 + _DIGITS.get(value[1], 0) if len(value) > 1 else 30
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _DIGITS.get(left, 1) if left else 1
        return tens * 10 + (_DIGITS.get(right, 0) if right else 0)
    if len(value) == 1 and value in _DIGITS:
        return _DIGITS[value]
    return 0


def _infer_lifecycle(
    event_type: str,
    *,
    title: str,
    status: str,
    source_text: str = "",
    require_cited_status: bool = False,
) -> str:
    normalized_status = str(status or "")
    if require_cited_status and not normalized_status:
        return "uncertain"
    headline_status = (
        normalized_status
        if require_cited_status
        else f"{title}{normalized_status}"
    )
    text = (
        normalized_status
        if require_cited_status
        else f"{headline_status}{source_text}"
    )
    if any(token in headline_status for token in ("终止", "取消", "撤回")):
        return "cancelled"
    if any(token in headline_status for token in ("修订", "更正", "调整")):
        return "revised"
    if any(
        token in headline_status
        for token in (
            "已完成",
            "实施完毕",
            "已履行",
            "建成投产",
            "正式投产",
            "投入运行",
        )
    ):
        return "completed"
    if re.search(
        r"(?:尚需|尚待|仍需|需提交)[^。；;]{0,40}(?:审议|批准|审批)",
        text,
    ):
        return "planned"
    if event_type == "earnings_flash":
        return "completed"
    if event_type == "dividend":
        return "approved"
    if event_type == "litigation_arbitration" and any(
        token in text for token in ("判决", "判令", "裁决")
    ):
        return "completed"
    if any(token in text for token in ("审议通过", "获批", "签署", "中标", "议案")):
        return "approved"
    if any(token in text for token in ("正在", "执行中", "建设中")):
        return "in_progress"
    if any(token in text for token in ("拟", "计划", "预计", "新建")):
        return "planned"
    return "uncertain"


__all__ = [
    "MENTION_COMPILER_VERSION",
    "MentionCompilation",
    "RejectedMention",
    "compile_mentions",
]
