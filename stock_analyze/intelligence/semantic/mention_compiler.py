"""Compile minimal source mentions into validated semantic event results."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
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
    numeric_raw_value_is_explicit_zero,
    parse_cn_number,
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
    normalized_mentions = _normalize_core_numeric_semantics(
        document_result.mentions,
        document=document,
        chunks=chunks,
        document_ir=document_ir,
    )
    mentions, collapsed_contracts = _collapse_grounded_contract_total(
        normalized_mentions,
        chunks=chunks,
    )
    mentions, collapsed_guarantees = _collapse_grounded_guarantee_total(
        mentions,
        document=document,
        chunks=chunks,
        document_ir=document_ir,
    )
    accepted_events: list[dict[str, object]] = []
    accepted_evidence: list[dict[str, str]] = []
    rejected: list[RejectedMention] = []
    dropped_items = collapsed_contracts + collapsed_guarantees

    for index, mention in enumerate(mentions, start=1):
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
            mentions,
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
    core = re.sub(r"^(?:\*?ST|S\*?ST)", "", core, flags=re.I)
    core = re.sub(r"[AB]$", "", core, flags=re.I)
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
    unit: str | None
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
    "share_ratio": ("占其所持股份比例", "占其所持有股份比例"),
    "guarantee_amount": ("担保金额",),
    "guarantee_balance": ("担保余额",),
    "case_amount": ("涉案金额", "诉讼金额", "本金"),
    "contract_amount": ("合同金额", "中标金额"),
}

_REVISION_TITLE_TOKENS = ("更正", "修订", "修改", "修正", "调整")
_REVISION_CURRENT_SECTION_TOKENS = (
    "更正后",
    "修订后",
    "修改后",
    "调整后",
    "更正说明",
    "更正如下",
    "修改如下",
    "现更正",
    "现修改",
    "现补充为",
)
_REVISION_SUPERSEDED_SECTION_TOKENS = (
    "原来披露",
    "原披露",
    "原公告内容",
    "更正前",
    "修订前",
    "修改前",
    "调整前",
)


_AUTHORITATIVE_CONTRACT_TOTAL_PATTERN = re.compile(
    r"(?:中标|合同)[^。；;\n]{0,24}?(?:合计|总计|总金额)"
    r"[^。；;\n]{0,16}?(?:人民币)?"
    r"(?P<amount>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|元)"
)
_DISMISSED_ALL_CLAIMS_PATTERN = re.compile(
    r"驳回[^。；;\n]{0,96}?(?:全部|所有)[^。；;\n]{0,24}?请求"
)
_ISSUER_CASH_CONTRIBUTION_PATTERN = re.compile(
    r"(?P<contributor>[^。；;\n]{2,80}?)"
    r"以(?:人民币)?现金出资"
    r"(?P<amount>[-+]?\d[\d,]*(?:\.\d+)?)"
    r"(?P<unit>亿元|万元|元)"
)
_BUYBACK_PRICE_CAP_PATTERN = re.compile(
    r"(?:回购价格|回购股份价格|价格)?不高于[^。；;\n]{0,60}?"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"(?P<unit>人民币元/股|元/股)"
)
_EQUITY_TOTAL_SHARE_PATTERN = re.compile(
    r"发行(?:股票|股份)(?:总数|数量|数)\s*[:：]\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"(?P<unit>亿股|万股|股)"
)
_ATTRIBUTABLE_NET_PROFIT_PATTERN = re.compile(
    r"(?:归属于[^。；;\n]{0,24}?股东[^。；;\n]{0,12}?净利润|归母净利润)"
)
_LEGAL_COMPANY_NAME_PATTERN = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9*]{2,60}?"
    r"(?:股份有限公司|有限责任公司|有限公司|集团公司))"
)


def _normalize_core_numeric_semantics(
    mentions: Sequence[EventMention],
    *,
    document: Mapping[str, object],
    chunks: Mapping[str, Mapping[str, object]],
    document_ir: Mapping[str, object] | None,
) -> tuple[EventMention, ...]:
    normalized: list[EventMention] = []
    for mention in mentions:
        current = _normalize_pledge_date_aliases(mention)
        current = _normalize_pledge_boolean_action(
            current,
            document_ir=document_ir,
        )
        current = _normalize_dismissed_litigation(current, chunks=chunks)
        current = _normalize_capacity_issuer_contribution(
            current,
            document=document,
            chunks=chunks,
        )
        current = _normalize_equity_financing_share_total(
            current,
            chunks=chunks,
        )
        current = _normalize_buyback_price_cap(current, chunks=chunks)
        current = _normalize_attributable_net_profit(
            current,
            document_ir=document_ir,
        )
        normalized.append(current)
    return tuple(normalized)


def _normalize_pledge_date_aliases(mention: EventMention) -> EventMention:
    if mention.event_type != "pledge_freeze":
        return mention
    dates = tuple(
        replace(item, kind="start_date")
        if item.kind == "pledge_start_date"
        else item
        for item in mention.dates
    )
    return replace(mention, dates=dates) if dates != mention.dates else mention


def _normalize_pledge_boolean_action(
    mention: EventMention,
    *,
    document_ir: Mapping[str, object] | None,
) -> EventMention:
    if mention.event_type != "pledge_freeze" or document_ir is None:
        return mention
    action_facts = [fact for fact in mention.facts if fact.name == "action"]
    if len(action_facts) != 1:
        return mention
    action = action_facts[0]
    normalized_action = _compact(action.raw_value)
    if "质押" not in normalized_action:
        return mention
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError:
        return mention
    header_candidates: list[Mapping[str, object]] = []
    for evidence in action.evidence:
        node = nodes.get(evidence.chunk_id)
        if not isinstance(node, Mapping) or node.get("node_type") != "table_cell":
            continue
        raw_value = _compact(str(node.get("raw_value") or node.get("text") or ""))
        if raw_value not in {"是", "√", "Y", "YES"}:
            continue
        for header in _ir_path_nodes(nodes, node.get("column_header_path") or []):
            header_text = _compact(str(header.get("text") or ""))
            if normalized_action in header_text:
                header_candidates.append(header)
    unique = {
        str(header.get("node_id") or ""): header
        for header in header_candidates
        if str(header.get("node_id") or "")
    }
    if len(unique) != 1:
        return mention
    node_id, header = next(iter(unique.items()))
    quote = str(header.get("text") or "").strip()
    if not quote:
        return mention
    replacement = MentionFact(
        name="action",
        raw_value=action.raw_value,
        evidence=(MentionEvidence(chunk_id=node_id, quote=quote),),
    )
    return replace(
        mention,
        facts=tuple(
            replacement if fact.name == "action" else fact
            for fact in mention.facts
        ),
    )


def _normalize_dismissed_litigation(
    mention: EventMention,
    *,
    chunks: Mapping[str, Mapping[str, object]],
) -> EventMention:
    if mention.event_type != "litigation_arbitration":
        return mention
    fact_matches = [
        fact
        for fact in mention.facts
        if _DISMISSED_ALL_CLAIMS_PATTERN.search(_compact(fact.raw_value))
    ]
    if len(fact_matches) == 1:
        source_fact = fact_matches[0]
        zero_fact = MentionFact(
            name="judgment_amount",
            raw_value=source_fact.raw_value,
            evidence=source_fact.evidence,
        )
        facts = [fact for fact in mention.facts if fact.name != "judgment_amount"]
        facts.append(zero_fact)
        return replace(mention, facts=tuple(facts))
    cited_chunk_ids = list(
        dict.fromkeys(
            evidence.chunk_id
            for fact in mention.facts
            for evidence in fact.evidence
        )
    )
    matches: list[tuple[str, str]] = []
    for chunk_id in cited_chunk_ids:
        chunk = chunks.get(chunk_id)
        if not isinstance(chunk, Mapping):
            continue
        text = str(chunk.get("text") or "")
        matches.extend(
            (chunk_id, found.group(0))
            for found in _DISMISSED_ALL_CLAIMS_PATTERN.finditer(text)
        )
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        return mention
    chunk_id, quote = unique[0]
    zero_fact = MentionFact(
        name="judgment_amount",
        raw_value=quote,
        evidence=(MentionEvidence(chunk_id=chunk_id, quote=quote),),
    )
    facts = [fact for fact in mention.facts if fact.name != "judgment_amount"]
    facts.append(zero_fact)
    return replace(mention, facts=tuple(facts))


def _normalize_capacity_issuer_contribution(
    mention: EventMention,
    *,
    document: Mapping[str, object],
    chunks: Mapping[str, Mapping[str, object]],
) -> EventMention:
    if mention.event_type != "capacity_project":
        return mention
    capex_facts = [fact for fact in mention.facts if fact.name == "capex"]
    if len(capex_facts) != 1:
        return mention
    capex_context = _fact_source_context(capex_facts[0], chunks)
    if "注册资本" not in _compact(capex_context):
        return mention
    issuer_names = [
        subject.name
        for subject in mention.subjects
        if subject.role == "issuer"
    ]
    document_name = str(document.get("name") or "").strip()
    if document_name:
        issuer_names.append(document_name)
    legal_issuer_names = _issuer_legal_names(issuer_names, chunks)

    def is_issuer_contribution(match) -> bool:
        contributor = _compact(match.group("contributor"))
        if legal_issuer_names:
            return any(
                _compact(legal_name) in contributor
                for legal_name in legal_issuer_names
            )
        return any(
            _company_name_matches(name, contributor)
            for name in issuer_names
        )

    candidates = _adjacent_scalar_candidates(
        chunks,
        pattern=_ISSUER_CASH_CONTRIBUTION_PATTERN,
        match_predicate=is_issuer_contribution,
    )
    unique_values = {raw_value for raw_value, _ in candidates}
    if len(unique_values) != 1:
        return mention
    raw_value = next(iter(unique_values))
    evidence = min(
        (items for value, items in candidates if value == raw_value),
        key=lambda items: (len(items), tuple(item.chunk_id for item in items)),
    )
    replacement = MentionFact(
        name="capex",
        raw_value=raw_value,
        evidence=evidence,
    )
    return replace(
        mention,
        facts=tuple(
            replacement if fact.name == "capex" else fact
            for fact in mention.facts
        ),
    )


def _issuer_legal_names(
    aliases: Sequence[str],
    chunks: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    candidates: set[str] = set()
    for chunk in chunks.values():
        text = str(chunk.get("text") or "")
        if not any(token in text for token in ("独立董事", "董事会", "监事会")):
            continue
        for match in _LEGAL_COMPANY_NAME_PATTERN.finditer(_compact(text)):
            legal_name = match.group("name")
            if any(
                _company_name_matches(alias, _compact(legal_name))
                for alias in aliases
            ):
                candidates.add(legal_name)
    return tuple(sorted(candidates)) if len(candidates) == 1 else ()


def _normalize_buyback_price_cap(
    mention: EventMention,
    *,
    chunks: Mapping[str, Mapping[str, object]],
) -> EventMention:
    if mention.event_type != "buyback" or not any(
        fact.name == "price_cap" for fact in mention.facts
    ):
        return mention
    candidates = _adjacent_scalar_candidates(
        chunks,
        pattern=_BUYBACK_PRICE_CAP_PATTERN,
    )
    unique_values = {raw_value for raw_value, _ in candidates}
    if len(unique_values) != 1:
        return mention
    raw_value = next(iter(unique_values))
    evidence = min(
        (items for value, items in candidates if value == raw_value),
        key=lambda items: (len(items), tuple(item.chunk_id for item in items)),
    )
    replacement = MentionFact(
        name="price_cap",
        raw_value=raw_value,
        evidence=evidence,
    )
    return replace(
        mention,
        facts=tuple(
            replacement if fact.name == "price_cap" else fact
            for fact in mention.facts
        ),
    )


def _normalize_equity_financing_share_total(
    mention: EventMention,
    *,
    chunks: Mapping[str, Mapping[str, object]],
) -> EventMention:
    if mention.event_type != "equity_financing":
        return mention
    share_facts = [fact for fact in mention.facts if fact.name == "share_count"]
    if len(share_facts) != 1:
        return mention
    candidates = _adjacent_scalar_candidates(
        chunks,
        pattern=_EQUITY_TOTAL_SHARE_PATTERN,
    )
    unique_values = {raw_value for raw_value, _ in candidates}
    if len(unique_values) != 1:
        return mention
    raw_value = next(iter(unique_values))
    evidence = min(
        (items for value, items in candidates if value == raw_value),
        key=lambda items: (len(items), tuple(item.chunk_id for item in items)),
    )
    replacement = MentionFact(
        name="share_count",
        raw_value=raw_value,
        evidence=evidence,
    )
    return replace(
        mention,
        facts=tuple(
            replacement if fact.name == "share_count" else fact
            for fact in mention.facts
        ),
    )


def _normalize_attributable_net_profit(
    mention: EventMention,
    *,
    document_ir: Mapping[str, object] | None,
) -> EventMention:
    if mention.event_type != "earnings_flash" or document_ir is None:
        return mention
    net_profit_facts = [fact for fact in mention.facts if fact.name == "net_profit"]
    if len(net_profit_facts) != 1:
        return mention
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError:
        return mention
    cited_values = [
        nodes.get(evidence.chunk_id)
        for evidence in net_profit_facts[0].evidence
    ]
    cited_values = [
        node
        for node in cited_values
        if isinstance(node, Mapping)
        and node.get("node_type") == "table_cell"
        and node.get("semantic_role") == "value"
    ]
    if len(cited_values) != 1:
        return mention
    cited = cited_values[0]
    table_id = str(cited.get("table_id") or "")
    column_index = int(cited.get("column_index") or 0)
    candidates: list[Mapping[str, object]] = []
    for node in nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or node.get("semantic_role") != "value"
            or str(node.get("table_id") or "") != table_id
            or int(node.get("column_index") or 0) != column_index
        ):
            continue
        row_label = _ir_path_text(nodes, node.get("row_header_path"))
        if _ATTRIBUTABLE_NET_PROFIT_PATTERN.search(_compact(row_label)):
            candidates.append(node)
    if len(candidates) != 1:
        return mention
    node = candidates[0]
    raw_value = str(node.get("raw_value") or node.get("text") or "").strip()
    node_id = str(node.get("node_id") or "")
    if not raw_value or not node_id:
        return mention
    replacement = MentionFact(
        name="net_profit",
        raw_value=raw_value,
        evidence=(MentionEvidence(chunk_id=node_id, quote=raw_value),),
    )
    return replace(
        mention,
        facts=tuple(
            replacement if fact.name == "net_profit" else fact
            for fact in mention.facts
        ),
    )


def _fact_source_context(
    fact: MentionFact,
    chunks: Mapping[str, Mapping[str, object]],
) -> str:
    return "".join(
        str(chunks.get(item.chunk_id, {}).get("text") or item.quote)
        for item in fact.evidence
    )


def _adjacent_scalar_candidates(
    chunks: Mapping[str, Mapping[str, object]],
    *,
    pattern: re.Pattern[str],
    context_predicate=None,
    match_predicate=None,
) -> list[tuple[str, tuple[MentionEvidence, ...]]]:
    ordered_ids = [
        chunk_id
        for chunk_id in _ordered_chunk_ids(chunks)
        if _TABLE_CELL_PATTERN.fullmatch(chunk_id) is None
        and "-meta-" not in chunk_id
    ]
    candidates: list[tuple[str, tuple[MentionEvidence, ...]]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for start in range(len(ordered_ids)):
        for size in (1, 2, 3):
            window = ordered_ids[start : start + size]
            if len(window) != size:
                continue
            texts = [str(chunks[chunk_id].get("text") or "") for chunk_id in window]
            context = _compact("".join(texts))
            if context_predicate is not None and not context_predicate(context):
                continue
            for match in pattern.finditer(context):
                if match_predicate is not None and not match_predicate(match):
                    continue
                raw_value = match.group("amount") + match.group("unit")
                key = (raw_value, tuple(window))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    (
                        raw_value,
                        tuple(
                            MentionEvidence(chunk_id=chunk_id, quote=text)
                            for chunk_id, text in zip(window, texts)
                            if text
                        ),
                    )
                )
    return candidates


def _collapse_grounded_contract_total(
    mentions: Sequence[EventMention],
    *,
    chunks: Mapping[str, Mapping[str, object]],
) -> tuple[tuple[EventMention, ...], int]:
    contract_mentions = [
        mention for mention in mentions if mention.event_type == "major_contract"
    ]
    if len(contract_mentions) < 2:
        return tuple(mentions), 0
    issuer_names = {
        _compact(subject.name)
        for mention in contract_mentions
        for subject in mention.subjects
        if subject.role == "issuer"
    }
    if len(issuer_names) != 1:
        return tuple(mentions), 0
    amount_facts = [
        fact
        for mention in contract_mentions
        for fact in mention.facts
        if fact.name == "contract_amount"
    ]
    if len(amount_facts) != len(contract_mentions):
        return tuple(mentions), 0
    units = {
        unit
        for fact in amount_facts
        if (unit := _infer_unit(fact.raw_value, value_type="number")) is not None
    }
    if len(units) > 1:
        return tuple(mentions), 0
    emitted_total = Decimal("0")
    if units:
        try:
            for fact in amount_facts:
                emitted_total += parse_cn_number(fact.raw_value)
        except CandidateValidationError:
            return tuple(mentions), 0
    else:
        values = [_unscaled_decimal(fact.raw_value) for fact in amount_facts]
        if any(value is None for value in values):
            return tuple(mentions), 0
        emitted_total = sum(
            (value for value in values if value is not None),
            start=Decimal("0"),
        )
    matches: list[tuple[str, str, str]] = []
    for chunk_id, chunk in chunks.items():
        text = str(chunk.get("text") or "")
        for found in _AUTHORITATIVE_CONTRACT_TOTAL_PATTERN.finditer(text):
            raw_value = found.group("amount") + found.group("unit")
            if units and found.group("unit") != next(iter(units)):
                continue
            if units:
                try:
                    total = parse_cn_number(raw_value)
                except CandidateValidationError:
                    continue
            else:
                total = _unscaled_decimal(raw_value)
                if total is None:
                    continue
            if emitted_total > 0 and total >= emitted_total:
                matches.append((str(chunk_id), found.group(0), raw_value))
    unique = list(dict.fromkeys(matches))
    unique_values = {item[2] for item in unique}
    if len(unique_values) != 1:
        return tuple(mentions), 0
    raw_value = next(iter(unique_values))
    matching = [item for item in unique if item[2] == raw_value]
    chunk_id, quote, _ = min(matching, key=lambda item: (len(item[1]), item[0]))
    issuer_subjects = tuple(
        subject
        for subject in contract_mentions[0].subjects
        if subject.role == "issuer"
    )
    if not issuer_subjects:
        return tuple(mentions), 0
    aggregate = EventMention(
        mention_id=f"aggregate-{contract_mentions[0].mention_id}",
        event_type="major_contract",
        subjects=issuer_subjects,
        facts=(MentionFact(
            name="contract_amount",
            raw_value=raw_value,
            evidence=(MentionEvidence(chunk_id=chunk_id, quote=quote),),
        ),),
        dates=(),
        status=contract_mentions[0].status,
    )
    collapsed: list[EventMention] = []
    aggregate_added = False
    for mention in mentions:
        if mention.event_type != "major_contract":
            collapsed.append(mention)
            continue
        if not aggregate_added:
            collapsed.append(aggregate)
            aggregate_added = True
    return tuple(collapsed), len(contract_mentions) - 1


def _collapse_grounded_guarantee_total(
    mentions: Sequence[EventMention],
    *,
    document: Mapping[str, object],
    chunks: Mapping[str, Mapping[str, object]],
    document_ir: Mapping[str, object] | None,
) -> tuple[tuple[EventMention, ...], int]:
    guarantee_mentions = [
        mention for mention in mentions if mention.event_type == "guarantee"
    ]
    if len(guarantee_mentions) < 2 or document_ir is None:
        return tuple(mentions), 0
    source_text = _compact(
        "".join(str(chunk.get("text") or "") for chunk in chunks.values())
    )
    title = _compact(str(document.get("title") or ""))
    if "补充" not in title and "未披露担保" not in source_text:
        return tuple(mentions), 0
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError:
        return tuple(mentions), 0

    amount_total = Decimal("0")
    explicit_units: set[str] = set()
    for mention in guarantee_mentions:
        amount_facts = [
            fact for fact in mention.facts
            if fact.name == "guarantee_amount"
        ]
        if len(amount_facts) != 1:
            return tuple(mentions), 0
        amount = _unscaled_decimal(amount_facts[0].raw_value)
        if amount is None:
            return tuple(mentions), 0
        explicit_unit = _infer_unit(
            amount_facts[0].raw_value,
            value_type="number",
        )
        if explicit_unit is not None:
            explicit_units.add(explicit_unit)
        amount_total += amount
    if len(explicit_units) > 1:
        return tuple(mentions), 0

    aggregate_nodes: list[Mapping[str, object]] = []
    for node in nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or node.get("semantic_role") != "value"
        ):
            continue
        resolution = node.get("unit_resolution")
        if not isinstance(resolution, Mapping):
            continue
        aggregate_unit = str(resolution.get("value") or "")
        if not aggregate_unit:
            continue
        if explicit_units and aggregate_unit not in explicit_units:
            continue
        row_labels = _ir_path_text(nodes, node.get("row_header_path"))
        column_labels = _ir_path_text(nodes, node.get("column_header_path"))
        if not any(token in row_labels for token in ("合计", "总计")):
            continue
        if "金额" not in column_labels:
            continue
        total = _unscaled_decimal(str(node.get("raw_value") or ""))
        if total is not None and amount_total > 0 and total >= amount_total:
            aggregate_nodes.append(node)
    if len(aggregate_nodes) != 1:
        return tuple(mentions), 0

    aggregate_node = aggregate_nodes[0]
    aggregate_chunk_id = str(aggregate_node.get("node_id") or "")
    aggregate_raw_value = str(aggregate_node.get("raw_value") or "")
    aggregate_evidence = _grounded_aggregate_evidence(
        aggregate_chunk_id,
        aggregate_raw_value,
        chunks,
    )
    issuer_subjects = tuple(
        subject
        for subject in guarantee_mentions[0].subjects
        if subject.role == "issuer"
    )
    if not issuer_subjects or aggregate_evidence is None:
        return tuple(mentions), 0
    aggregate = EventMention(
        mention_id=f"aggregate-{guarantee_mentions[0].mention_id}",
        event_type="guarantee",
        subjects=issuer_subjects,
        facts=(
            MentionFact(
                name="guarantee_amount",
                raw_value=aggregate_raw_value,
                evidence=(aggregate_evidence,),
            ),
        ),
        dates=(),
        status=guarantee_mentions[0].status,
    )
    collapsed: list[EventMention] = []
    aggregate_added = False
    for mention in mentions:
        if mention.event_type != "guarantee":
            collapsed.append(mention)
            continue
        if not aggregate_added:
            collapsed.append(aggregate)
            aggregate_added = True
    return tuple(collapsed), len(guarantee_mentions) - 1


def _grounded_aggregate_evidence(
    aggregate_chunk_id: str,
    aggregate_raw_value: str,
    chunks: Mapping[str, Mapping[str, object]],
) -> MentionEvidence | None:
    pattern = re.compile(
        rf"(?:合计|总计)\s*[:：]?\s*{re.escape(aggregate_raw_value)}"
    )
    matches: list[tuple[str, str]] = []
    for chunk_id, chunk in chunks.items():
        text = str(chunk.get("text") or "")
        found = list(pattern.finditer(text))
        if len(found) == 1:
            matches.append((str(chunk_id), found[0].group(0)))
    if len(matches) != 1:
        if aggregate_chunk_id not in chunks:
            return None
        return MentionEvidence(
            chunk_id=aggregate_chunk_id,
            quote=aggregate_raw_value,
        )
    body_chunk_id, quote = matches[0]
    return MentionEvidence(chunk_id=body_chunk_id, quote=quote)


def _ir_path_text(
    nodes: Mapping[str, Mapping[str, object]],
    path: object,
) -> str:
    if not isinstance(path, list):
        return ""
    return _compact(
        " ".join(
            str(nodes.get(str(item.get("node_id") or ""), {}).get("text") or "")
            for item in path
            if isinstance(item, Mapping)
        )
    )


def _unscaled_decimal(raw_value: str) -> Decimal | None:
    match = re.search(r"[-+]?(?:\d[\d,]*)(?:\.\d+)?", _compact(raw_value))
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


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
    text_value = bool(
        spec is not None and spec.value_type in {"text", "period"}
    )
    value_ids = []
    for node_id in referenced_table_ids:
        node = nodes.get(node_id)
        if not isinstance(node, Mapping) or node.get("node_type") != "table_cell":
            continue
        if node.get("semantic_role") == "value" or (
            text_value
            and node.get("semantic_role") != "column_header"
            and bool(node.get("column_header_path"))
        ):
            value_ids.append(node_id)
    if not referenced_table_ids:
        return None
    if not value_ids:
        return None
    if len(set(value_ids)) != 1:
        raise _MentionCompileError("table_semantic_path_missing")
    value_id = value_ids[0]
    if not text_value:
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
    label_text = " ".join(
        str(item.get("text") or "")
        for item in [*row_nodes, *column_nodes]
    )
    expected_terms = tuple(getattr(spec, "evidence_terms_any", ()) or ())
    if not expected_terms:
        expected_terms = _IR_ROW_LABEL_TERMS.get(fact.name, ())
    compact_label = _compact(label_text)
    if expected_terms and not any(
        _compact(term) in compact_label for term in expected_terms
    ):
        raise _MentionCompileError("table_semantic_label_mismatch")

    resolution = node.get("unit_resolution")
    assert isinstance(resolution, Mapping)
    unit = str(resolution.get("value") or "") or None
    if not text_value and not unit:
        raise _MentionCompileError("table_semantic_unit_missing")
    period_values = [
        str(item.get("text") or "").strip()
        for item in column_nodes
        if _looks_like_period_label(str(item.get("text") or ""))
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


def _looks_like_period_label(value: str) -> bool:
    compact = _compact(value)
    if not compact:
        return False
    return bool(
        re.search(r"(?:19|20)\d{2}(?:年|年度|Q[1-4]|H[12])", compact, re.I)
        or any(
            token in compact
            for token in (
                "本报告期",
                "报告期",
                "上年同期",
                "本期",
                "上期",
                "期末",
                "年初",
            )
        )
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
    if _revision_has_no_changed_fact(
        mention,
        document=document,
        chunks=chunks,
        declared_facts=taxonomy_event.declared_facts,
        document_ir=document_ir,
    ):
        raise _MentionCompileError("mention_revision_no_changed_fact")
    row_context = _unique_ir_table_row(mention, document_ir)

    subjects = []
    dropped = 0
    for subject in mention.subjects:
        if subject.role not in VALID_SUBJECT_ROLES:
            raise _MentionCompileError("mention_subject_role_invalid")
        if (
            subject.role != "issuer"
            and subject.role not in taxonomy_event.required_subject_roles
            and row_context is None
            and not any(
                _compact(subject.name) in _compact(item.quote)
                for item in subject.evidence
            )
        ):
            dropped += 1
            continue
        canonical_issuer_id = (
            _canonical_issuer_evidence(registry, document)
            if subject.role == "issuer"
            and _issuer_evidence_needs_canonicalization(subject, document)
            else None
        )
        if canonical_issuer_id is not None:
            evidence_ids = [canonical_issuer_id]
            valid_subject_evidence = ()
        else:
            evidence_ids, valid_subject_evidence = _valid_evidence(
                registry,
                subject.evidence,
            )
        if (
            not evidence_ids
            and subject.role != "issuer"
            and row_context is not None
        ):
            recovered_subject_id = _recover_external_subject_from_row(
                row_context,
                subject.name,
                registry=registry,
            )
            if recovered_subject_id is not None:
                evidence_ids = [recovered_subject_id]
        if not evidence_ids:
            raise _MentionCompileError("mention_subject_evidence_missing")
        if subject.role != "issuer":
            exact_id = _exact_external_subject_evidence(
                registry,
                subject.name,
                valid_subject_evidence,
            )
            if exact_id is not None:
                evidence_ids = [exact_id]
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
        if _uses_superseded_revision_evidence(
            fact.evidence,
            document=document,
            chunks=chunks,
            document_ir=document_ir,
        ):
            raise _MentionCompileError(
                "mention_revision_uses_superseded_value"
            )
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
        try:
            ir_context = _ir_fact_context(
                fact,
                valid_evidence,
                document_ir=document_ir,
                spec=spec,
            )
        except _MentionCompileError as exc:
            if (
                mention.event_type == "pledge_freeze"
                and fact.name in {"share_count", "share_ratio"}
                and exc.code == "table_semantic_label_mismatch"
            ):
                dropped += 1
                continue
            raise
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
            allow_source_quote_text=(
                fact.name in taxonomy_event.default_requirements.facts
            ),
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
        unit = endpoint_unit
        if spec is not None and spec.value_type in {"number", "ratio"}:
            if ir_context is not None:
                unit = unit or ir_context.unit
            unit = unit or _infer_unit(
                raw_value,
                value_type=spec.value_type,
            )
            if unit is None:
                evidence_units = {
                    inferred
                    for item in valid_evidence
                    if (
                        inferred := _infer_unit(
                            item.quote,
                            value_type=spec.value_type,
                        )
                    )
                }
                if len(evidence_units) == 1:
                    unit = next(iter(evidence_units))
            if unit is None:
                table_unit = _table_column_unit(
                    valid_evidence,
                    chunks,
                    value_type=spec.value_type,
                )
                if table_unit is not None:
                    candidate_unit, unit_evidence = table_unit
                    try:
                        unit_evidence_id = registry.add(unit_evidence)
                    except _MentionCompileError:
                        pass
                    else:
                        unit = candidate_unit
                        evidence_ids = list(
                            dict.fromkeys([*evidence_ids, unit_evidence_id])
                        )
            if unit is None:
                document_unit = _document_declared_unit(
                    valid_evidence,
                    chunks,
                    spec=spec,
                    value_type=spec.value_type,
                )
                if document_unit is not None:
                    candidate_unit, unit_evidence = document_unit
                    try:
                        unit_evidence_id = registry.add(unit_evidence)
                    except _MentionCompileError:
                        pass
                    else:
                        unit = candidate_unit
                        evidence_ids = list(
                            dict.fromkeys([*evidence_ids, unit_evidence_id])
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
            and (
                (
                    not _SCALAR_PATTERN.search(_compact(raw_value))
                    and not numeric_raw_value_is_explicit_zero(raw_value)
                )
                or numeric_raw_value_is_ambiguous(raw_value, fact.name)
            )
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

    if mention.event_type == "pledge_freeze" and row_context is not None:
        present_facts = {str(item.get("name") or "") for item in facts}
        for _ in range(2):
            recovered_fact = _recover_pledge_core_fact(
                row_context,
                taxonomy_event=taxonomy_event,
                registry=registry,
                chunks=chunks,
                document_ir=document_ir,
                skip_fact_names=present_facts,
            )
            if recovered_fact is None:
                break
            facts.append(recovered_fact)
            present_facts.add(str(recovered_fact.get("name") or ""))

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
    lifecycle_status = ""
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
            lifecycle_status = grounded_status
        else:
            dropped += 1
            if status_evidence_ids:
                lifecycle_status = "".join(
                    item.quote for item in valid_status_evidence
                )

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
    if (
        mention.event_type == "pledge_freeze"
        and row_context is not None
        and not any(item["kind"] == "start_date" for item in dates)
    ):
        recovered_date = _recover_pledge_start_date(
            row_context,
            registry=registry,
        )
        if recovered_date is not None:
            dates.append(recovered_date)

    lifecycle = _infer_lifecycle(
        mention.event_type,
        title=str(document.get("title") or ""),
        status=lifecycle_status,
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


def _exact_external_subject_evidence(
    registry: _EvidenceRegistry,
    subject_name: str,
    evidence: Sequence[MentionEvidence],
) -> str | None:
    normalized_name = _compact(subject_name)
    if not normalized_name:
        return None
    for item in evidence:
        if normalized_name not in _compact(item.quote):
            continue
        try:
            return registry.add(
                MentionEvidence(
                    chunk_id=item.chunk_id,
                    quote=str(subject_name).strip(),
                )
            )
        except _MentionCompileError:
            continue
    return None


def _canonical_issuer_evidence(
    registry: _EvidenceRegistry,
    document: Mapping[str, object],
) -> str | None:
    issuer_name = str(document.get("name") or "").strip()
    normalized_name = _compact(issuer_name)
    if not normalized_name:
        return None
    candidates = sorted(
        (
            (
                chunk_id,
                str(chunk.get("text") or ""),
            )
            for chunk_id, chunk in registry.chunks.items()
            if "-meta-" not in chunk_id
            and "-part" not in chunk_id
            and _TABLE_CELL_PATTERN.fullmatch(chunk_id) is None
            if normalized_name in _compact(str(chunk.get("text") or ""))
        ),
        key=lambda item: (len(item[1]), item[0]),
    )
    for chunk_id, _ in candidates:
        try:
            return registry.add(
                MentionEvidence(chunk_id=chunk_id, quote=issuer_name)
            )
        except _MentionCompileError:
            continue
    security_code = str(document.get("ts_code") or "").split(".", 1)[0].strip()
    if security_code:
        for chunk_id, chunk in registry.chunks.items():
            if (
                "-meta-" in chunk_id
                or "-part" in chunk_id
                or _TABLE_CELL_PATTERN.fullmatch(chunk_id) is not None
                or security_code not in str(chunk.get("text") or "")
            ):
                continue
            try:
                return registry.add(
                    MentionEvidence(chunk_id=chunk_id, quote=security_code)
                )
            except _MentionCompileError:
                continue
    return None


def _issuer_evidence_needs_canonicalization(
    subject,
    document: Mapping[str, object],
) -> bool:
    issuer_name = _compact(str(document.get("name") or ""))
    return (
        bool(issuer_name and _compact(subject.name) != issuer_name)
        or any(
            "-meta-" in item.chunk_id
            or "-part" in item.chunk_id
            or _TABLE_CELL_PATTERN.fullmatch(item.chunk_id) is not None
            for item in subject.evidence
        )
    )


@dataclass(frozen=True)
class _IRTableRow:
    table_id: str
    row_index: int
    nodes: Mapping[str, Mapping[str, object]]


def _unique_ir_table_row(
    mention: EventMention,
    document_ir: Mapping[str, object] | None,
) -> _IRTableRow | None:
    if document_ir is None:
        return None
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError:
        return None
    evidence = [
        item
        for fact in mention.facts
        for item in fact.evidence
    ] + [
        item
        for date_item in mention.dates
        for item in date_item.evidence
    ]
    rows: set[tuple[str, int]] = set()
    for item in evidence:
        node = nodes.get(item.chunk_id)
        if not isinstance(node, Mapping):
            continue
        if (
            node.get("node_type") != "table_cell"
            or node.get("semantic_role") == "column_header"
            or not node.get("column_header_path")
        ):
            continue
        rows.add(
            (
                str(node.get("table_id") or ""),
                int(node.get("row_index") or 0),
            )
        )
    if len(rows) == 1:
        table_id, row_index = next(iter(rows))
        if not table_id:
            return None
        return _IRTableRow(table_id=table_id, row_index=row_index, nodes=nodes)
    if rows and mention.event_type == "pledge_freeze":
        ranked_rows = sorted(
            (
                _pledge_row_score(nodes, table_id, row_index),
                table_id,
                row_index,
            )
            for table_id, row_index in rows
        )
        best_score = ranked_rows[-1][0]
        best_rows = [item for item in ranked_rows if item[0] == best_score]
        if best_score > 0 and len(best_rows) == 1:
            _, table_id, row_index = best_rows[0]
            return _IRTableRow(
                table_id=table_id,
                row_index=row_index,
                nodes=nodes,
            )
        return None
    if rows or mention.event_type != "pledge_freeze":
        return None

    holder_names = {
        _compact(subject.name)
        for subject in mention.subjects
        if subject.role == "holder" and _compact(subject.name)
    }
    holder_rows: set[tuple[str, int]] = set()
    for node in nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or node.get("semantic_role") == "column_header"
        ):
            continue
        raw_value = _compact(str(node.get("raw_value") or node.get("text") or ""))
        if not any(name in raw_value for name in holder_names):
            continue
        table_id = str(node.get("table_id") or "")
        if table_id:
            holder_rows.add((table_id, int(node.get("row_index") or 0)))
    ranked = sorted(
        (
            _pledge_row_score(nodes, table_id, row_index),
            table_id,
            row_index,
        )
        for table_id, row_index in holder_rows
    )
    if not ranked or ranked[-1][0] <= 0:
        return None
    best_score = ranked[-1][0]
    best = [item for item in ranked if item[0] == best_score]
    if len(best) != 1:
        return None
    _, table_id, row_index = best[0]
    if not table_id:
        return None
    return _IRTableRow(table_id=table_id, row_index=row_index, nodes=nodes)


def _pledge_row_score(
    nodes: Mapping[str, Mapping[str, object]],
    table_id: str,
    row_index: int,
) -> int:
    headers: set[str] = set()
    for node in nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or str(node.get("table_id") or "") != table_id
            or int(node.get("row_index") or 0) != row_index
        ):
            continue
        for header in _ir_path_nodes(nodes, node.get("column_header_path") or []):
            headers.add(_compact(str(header.get("text") or "")))
    terms = ("本次质押", "质押起始日", "质押开始日", "是否补充质押")
    return sum(any(_compact(term) in header for header in headers) for term in terms)


def _recover_pledge_core_fact(
    row: _IRTableRow,
    *,
    taxonomy_event,
    registry: _EvidenceRegistry,
    chunks: Mapping[str, Mapping[str, object]],
    document_ir: Mapping[str, object] | None,
    skip_fact_names: set[str] | None = None,
) -> dict[str, object] | None:
    candidates = (
        (
            "share_count",
            ("本次质押数量", "质押数量", "本次质押股数", "质押股数"),
        ),
        ("share_ratio", ("占其所持股份比例",)),
    )
    for fact_name, terms in candidates:
        if fact_name in (skip_fact_names or set()):
            continue
        node = _row_cell_for_header(row, terms)
        if node is None:
            continue
        raw_value = str(node.get("raw_value") or node.get("text") or "").strip()
        if not raw_value:
            continue
        evidence = MentionEvidence(
            chunk_id=str(node.get("node_id") or ""),
            quote=raw_value,
        )
        spec = taxonomy_event.fact_specs.get(fact_name)
        try:
            context = _ir_fact_context(
                MentionFact(
                    name=fact_name,
                    raw_value=raw_value,
                    evidence=(evidence,),
                ),
                (evidence,),
                document_ir=document_ir,
                spec=spec,
            )
            evidence_ids = [registry.add(evidence)]
            if context is not None:
                evidence_ids.extend(registry.add_many(context.evidence))
        except _MentionCompileError:
            continue
        return _semantic_fact(
            fact_name,
            raw_value,
            list(dict.fromkeys(evidence_ids)),
            unit=(context.unit if context is not None else None),
            period=(context.period if context is not None else None),
        )
    return None


def _recover_external_subject_from_row(
    row: _IRTableRow,
    subject_name: str,
    *,
    registry: _EvidenceRegistry,
) -> str | None:
    normalized_name = _compact(subject_name)
    if not normalized_name:
        return None
    candidates = []
    for node in row.nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or str(node.get("table_id") or "") != row.table_id
            or int(node.get("row_index") or 0) != row.row_index
        ):
            continue
        raw_value = str(node.get("raw_value") or node.get("text") or "").strip()
        if normalized_name in _compact(raw_value):
            candidates.append((node, raw_value))
    if len(candidates) != 1:
        return None
    node, raw_value = candidates[0]
    quote = str(subject_name).strip()
    if quote not in raw_value:
        quote = raw_value
    try:
        return registry.add(
            MentionEvidence(
                chunk_id=str(node.get("node_id") or ""),
                quote=quote,
            )
        )
    except _MentionCompileError:
        return None


def _recover_pledge_start_date(
    row: _IRTableRow,
    *,
    registry: _EvidenceRegistry,
) -> dict[str, object] | None:
    node = _row_cell_for_header(row, ("质押起始日", "质押开始日", "起始日"))
    if node is None:
        return None
    raw_value = str(node.get("raw_value") or node.get("text") or "").strip()
    normalized = _normalize_mention_date(raw_value)
    if normalized is None:
        return None
    try:
        evidence_id = registry.add(
            MentionEvidence(
                chunk_id=str(node.get("node_id") or ""),
                quote=raw_value,
            )
        )
    except _MentionCompileError:
        return None
    return {
        "kind": "start_date",
        "value": normalized,
        "evidence_ids": [evidence_id],
    }


def _row_cell_for_header(
    row: _IRTableRow,
    terms: Sequence[str],
) -> Mapping[str, object] | None:
    matches: list[Mapping[str, object]] = []
    for node in row.nodes.values():
        if (
            node.get("node_type") != "table_cell"
            or str(node.get("table_id") or "") != row.table_id
            or int(node.get("row_index") or 0) != row.row_index
        ):
            continue
        header_nodes = _ir_path_nodes(
            row.nodes,
            node.get("column_header_path") or [],
        )
        header_text = _compact(
            " ".join(str(item.get("text") or "") for item in header_nodes)
        )
        if any(_compact(term) in header_text for term in terms):
            matches.append(node)
    return matches[0] if len(matches) == 1 else None


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
        ordered_ids = _ordered_chunk_ids(chunks)
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
    allow_source_quote_text: bool = False,
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
    if value_type == "text" and allow_source_quote_text:
        source_quotes = [
            item.quote.strip()
            for item in evidence
            if item.quote.strip()
            and len(item.quote.strip()) <= 800
        ]
        if source_quotes:
            return max(source_quotes, key=len), None
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
_TEXT_CHUNK_ORDINAL_PATTERN = re.compile(
    r"(?:^|-)p(?P<page>\d+)-c(?P<ordinal>\d+)(?:-|$)"
)


def _ordered_chunk_ids(
    chunks: Mapping[str, Mapping[str, object]],
) -> list[str]:
    indexed = list(enumerate(chunks.items()))

    def order_key(item):
        index, (chunk_id, chunk) = item
        match = _TEXT_CHUNK_ORDINAL_PATTERN.search(chunk_id)
        if match is not None:
            return (
                int(match.group("page")),
                1,
                int(match.group("ordinal")),
                index,
            )
        page_number = int(chunk.get("page_number") or 0)
        if "-meta-" in chunk_id:
            return (0, 0, 0, index)
        table_match = _TABLE_CELL_PATTERN.fullmatch(chunk_id)
        if table_match is not None:
            return (
                page_number,
                2,
                int(table_match.group("row")) * 10_000
                + int(table_match.group("column")),
                index,
            )
        return (page_number, 3, index, index)

    return [chunk_id for _, (chunk_id, _) in sorted(indexed, key=order_key)]


def _uses_superseded_revision_evidence(
    evidence: Sequence[MentionEvidence],
    *,
    document: Mapping[str, object],
    chunks: Mapping[str, Mapping[str, object]],
    document_ir: Mapping[str, object] | None,
) -> bool:
    title = _compact(str(document.get("title") or ""))
    if not any(token in title for token in _REVISION_TITLE_TOKENS):
        return False
    evidence_chunk_ids = {item.chunk_id for item in evidence}
    if not evidence_chunk_ids:
        return False
    state = "neutral"
    for chunk_id in _revision_ordered_chunk_ids(chunks, document_ir):
        text = _compact(str(chunks[chunk_id].get("text") or ""))
        state = _revision_state_after_text(text, state)
        if chunk_id in evidence_chunk_ids and state == "superseded":
            return True
    return False


def _revision_has_no_changed_fact(
    mention: EventMention,
    *,
    document: Mapping[str, object],
    chunks: Mapping[str, Mapping[str, object]],
    declared_facts: Sequence[str],
    document_ir: Mapping[str, object] | None,
) -> bool:
    title = _compact(str(document.get("title") or ""))
    if not any(token in title for token in _REVISION_TITLE_TOKENS):
        return False
    state = "neutral"
    superseded_text: list[str] = []
    current_text: list[str] = []
    ordered_ids = _revision_ordered_chunk_ids(chunks, document_ir)
    for chunk_id in ordered_ids:
        if (
            document_ir is None
            and _TEXT_CHUNK_ORDINAL_PATTERN.search(chunk_id) is None
        ):
            continue
        text = _compact(str(chunks[chunk_id].get("text") or ""))
        state = _revision_state_after_text(text, state)
        if state == "current":
            current_text.append(text)
        elif state == "superseded":
            superseded_text.append(text)
    if not current_text or not superseded_text:
        return False
    old = "".join(superseded_text)
    current = "".join(current_text)
    facts = [
        fact
        for fact in mention.facts
        if fact.name in declared_facts and _compact(fact.raw_value)
    ]
    return bool(
        facts
        and all(
            _compact(fact.raw_value) in old
            and _compact(fact.raw_value) in current
            for fact in facts
        )
    )


def _revision_ordered_chunk_ids(
    chunks: Mapping[str, Mapping[str, object]],
    document_ir: Mapping[str, object] | None,
) -> list[str]:
    if document_ir is None:
        return _ordered_chunk_ids(chunks)
    try:
        nodes = ir_nodes_by_id(document_ir)
    except DocumentIRPreflightError:
        return _ordered_chunk_ids(chunks)
    indexed = list(enumerate(chunks.items()))

    def order_key(item):
        index, (chunk_id, chunk) = item
        if "-meta-" in chunk_id:
            return (-1, -1.0, -1.0, index)
        node = nodes.get(chunk_id, {})
        page = int(node.get("page_number") or chunk.get("page_number") or 0)
        bbox = node.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            return (page, float(bbox[1]), float(bbox[0]), index)
        match = _TEXT_CHUNK_ORDINAL_PATTERN.search(chunk_id)
        ordinal = int(match.group("ordinal")) if match is not None else index
        return (page, float(10_000 + ordinal), 0.0, index)

    return [
        chunk_id
        for _, (chunk_id, _) in sorted(indexed, key=order_key)
    ]


def _revision_state_after_text(text: str, state: str) -> str:
    current_position = max(
        (text.rfind(token) for token in _REVISION_CURRENT_SECTION_TOKENS),
        default=-1,
    )
    old_position = max(
        (text.rfind(token) for token in _REVISION_SUPERSEDED_SECTION_TOKENS),
        default=-1,
    )
    if current_position < 0 and old_position < 0:
        return state
    return "current" if current_position > old_position else "superseded"


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


def _document_declared_unit(
    evidence: Sequence[MentionEvidence],
    chunks: Mapping[str, Mapping[str, object]],
    *,
    spec,
    value_type: str,
) -> tuple[str, MentionEvidence] | None:
    """Resolve one source-declared unit without guessing from the fact type.

    Prefer the full source chunk behind a cited quote (for labels such as
    ``营业收入（万元）``). Otherwise accept only a single compatible unit from
    explicit document-level ``单位：...`` declarations. The unit itself is
    persisted as evidence so canonical validation remains source-grounded.
    """

    cited_candidates: list[tuple[str, str]] = []
    for item in evidence:
        source = chunks.get(item.chunk_id)
        if not isinstance(source, Mapping):
            continue
        text = str(source.get("text") or "")
        unit = _infer_unit(text, value_type=value_type)
        if unit is not None and _unit_allowed_for_spec(unit, spec):
            cited_candidates.append((unit, item.chunk_id))
    cited_units = {unit for unit, _ in cited_candidates}
    if len(cited_units) == 1:
        unit = next(iter(cited_units))
        chunk_id = next(
            chunk_id
            for candidate, chunk_id in cited_candidates
            if candidate == unit
        )
        return unit, MentionEvidence(chunk_id=chunk_id, quote=unit)

    declared_candidates: list[tuple[str, str]] = []
    for chunk_id, chunk in chunks.items():
        if not isinstance(chunk, Mapping):
            continue
        text = str(chunk.get("text") or "")
        if "单位" not in text:
            continue
        for match in re.finditer(r"单位\s*[：:]?\s*([^\n。；;]{0,40})", text):
            unit = _infer_unit(match.group(0), value_type=value_type)
            if unit is not None and _unit_allowed_for_spec(unit, spec):
                declared_candidates.append((unit, str(chunk_id)))
    declared_units = {unit for unit, _ in declared_candidates}
    if len(declared_units) != 1:
        return None
    unit = next(iter(declared_units))
    chunk_id = next(
        chunk_id
        for candidate, chunk_id in declared_candidates
        if candidate == unit
    )
    return unit, MentionEvidence(chunk_id=chunk_id, quote=unit)


def _unit_allowed_for_spec(unit: str, spec) -> bool:
    return _UNIT_KINDS.get(unit) in spec.allowed_unit_kinds


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
        return (
            "unitless" in spec.allowed_unit_kinds
            or numeric_raw_value_is_explicit_zero(fact.raw_value)
        )
    return _UNIT_KINDS.get(unit) in spec.allowed_unit_kinds


def _schema_implied_unit(spec, raw_value: str) -> str | None:
    """Resolve only units that are unambiguous from the frozen fact schema."""

    if spec.value_type != "number":
        return None
    allowed_kinds = tuple(spec.allowed_unit_kinds)
    if numeric_raw_value_is_explicit_zero(raw_value):
        if allowed_kinds == ("shares",):
            return "股"
    if allowed_kinds != ("shares",):
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
    ordered_chunk_ids = _ordered_chunk_ids(chunks)
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
    if any(
        token in headline_status
        for token in ("修订", "更正", "修改", "调整", "补充协议")
    ):
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
