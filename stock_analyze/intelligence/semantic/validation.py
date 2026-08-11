"""Deterministic grounding and fact validation for semantic event candidates."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

from .contracts import (
    SemanticEffectiveDate,
    SemanticEvent,
    SemanticEvidence,
    SemanticFact,
)
from .taxonomy import EventTaxonomy, FactSpec, VALID_SUBJECT_ROLES


NORMALIZATION_VERSION = "width-line-v1"
_B_SHARE_PREFIXES = ("200", "900")
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.I),
    re.compile(r"\b(?:reveal|print|output)\s+(?:the\s+)?system\s+prompt\b", re.I),
    re.compile(r"忽略(?:以上|上述|前述|先前|所有)?(?:指令|要求|规则)"),
    re.compile(r"(?:输出|泄露|显示)(?:系统|隐藏)(?:提示词|指令)"),
)
_CURRENCY_ALIASES = {
    "CNY": "CNY",
    "RMB": "CNY",
    "人民币": "CNY",
    "人民币元": "CNY",
    "元": "CNY",
    "USD": "USD",
    "美元": "USD",
    "HKD": "HKD",
    "港元": "HKD",
    "EUR": "EUR",
    "欧元": "EUR",
}
_UNIT_ALIASES = {
    "元": "currency",
    "人民币元": "currency",
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
    "元/股": "price",
    "人民币元/股": "price",
    "CNY/share": "price",
    "股": "shares",
    "万股": "shares",
    "亿股": "shares",
    "shares": "shares",
    "%": "ratio",
    "pct": "ratio",
    "ratio": "ratio",
    "倍": "multiple",
    "个": "count",
    "人": "count",
    "项": "count",
    "天": "duration",
    "日": "duration",
    "月": "duration",
    "年": "duration",
    "吨": "mass",
    "万吨": "mass",
    "吨/年": "mass_rate",
    "万吨/年": "mass_rate",
    "台": "count",
    "万台": "count",
    "台/年": "count_rate",
    "万台/年": "count_rate",
    "片": "count",
    "万片": "count",
    "片/年": "count_rate",
    "万片/年": "count_rate",
    "平方米": "area",
    "万平方米": "area",
    "平方米/年": "area_rate",
    "万平方米/年": "area_rate",
    "MW": "power",
    "GW": "power",
    "MWh": "energy",
    "GWh": "energy",
}
_UNIT_MULTIPLIERS = {
    # Standalone unit scale factors. parse_cn_number already honours a
    # multiplier embedded in raw_value (e.g. "26692.71万元"); these factors are
    # applied ONLY when the raw text omits the scale and the executor split it
    # into the independent ``unit`` field, so an embedded scale is never
    # double-counted.
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
    "万美元": Decimal("10000"),
    "亿美元": Decimal("100000000"),
    "万欧元": Decimal("10000"),
    "亿欧元": Decimal("100000000"),
    "万港元": Decimal("10000"),
    "亿港元": Decimal("100000000"),
    "万亿": Decimal("1000000000000"),
    "万股": Decimal("10000"),
    "亿股": Decimal("100000000"),
    "千": Decimal("1000"),
}
_NUMERIC_NAME_MARKERS = (
    "amount",
    "asset",
    "count",
    "price",
    "profit",
    "ratio",
    "rate",
    "revenue",
    "share",
    "yoy",
    "holding",
    "cash_per",
    "stock_per",
    "loss",
    "value",
)
_TEXT_FACT_NAMES = {
    "action",
    "action_type",
    "approval_conditions",
    "case_number",
    "case_stage",
    "claim",
    "construction_period",
    "contract_subject",
    "counter_guarantee",
    "currency",
    "document_number",
    "exchange_decision",
    "expected_operation_date",
    "distribution_plan",
    "distribution_period",
    "financing_method",
    "forecast_reason",
    "funding_source",
    "guarantee_period",
    "issuer_role",
    "lockup_period",
    "performance_conditions",
    "project_location",
    "project_type",
    "purpose",
    "change_method",
    "change_period",
    "contract_period",
    "implementation_period",
    "regulatory_approval",
    "related_party",
    "remediation",
    "removal_conditions",
    "reason",
    "risk_type",
    "security_name_change",
    "tax_included",
    "transaction_type",
    "trigger",
    "use_of_proceeds",
}
_PERIOD_PATTERN = re.compile(
    r"^(?:\d{4}(?:Q[1-4]|H[12]|FY)?|\d{4}-\d{2}(?:-\d{2})?"
    r"(?:/\d{4}-\d{2}(?:-\d{2})?)?)$",
    re.I,
)


class CandidateValidationError(ValueError):
    """A candidate rejection with one stable machine-readable reason code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(self.code)


@dataclass(frozen=True)
class ValidatedEvidence:
    evidence_id: str
    page_number: int
    chunk_id: str
    start: int
    end: int
    quote: str
    normalized_quote_hash: str
    normalization_version: str = NORMALIZATION_VERSION


@dataclass(frozen=True)
class ValidatedFact:
    name: str
    raw_value: str | None
    numeric_value: Decimal | None
    text_value: str | None
    unit: str | None
    currency: str | None
    period: str | None
    evidence_ids: tuple[str, ...]
    provider_numeric_value: Decimal | None


@dataclass(frozen=True)
class ValidatedCandidate:
    event_type: str
    lifecycle: str
    subjects: tuple[dict[str, object], ...]
    facts: tuple[ValidatedFact, ...]
    effective_dates: tuple[dict[str, object], ...]
    evidence: tuple[ValidatedEvidence, ...]
    canonical_key: str
    required_evidence_count: int
    validated_evidence_count: int
    taxonomy_direction_rule: str
    horizon_days: int


def normalize_grounding_text(value: str) -> str:
    """Normalize only CR/LF representation and full-width code points."""

    normalized_lines = str(value).replace("\r\n", "\n").replace("\r", "\n")
    characters: list[str] = []
    for character in normalized_lines:
        codepoint = ord(character)
        if character == "\u3000" or 0xFF00 <= codepoint <= 0xFFEF:
            characters.append(unicodedata.normalize("NFKC", character))
        else:
            characters.append(character)
    return "".join(characters)


def relocate_evidence_offsets(
    payload: object,
    chunks: Mapping[str, Mapping[str, object]],
) -> object:
    """Relocate only quotes with one exact occurrence in their named chunk."""

    relocated = deepcopy(payload)
    if not isinstance(relocated, dict):
        return relocated
    evidence = relocated.get("evidence")
    if not isinstance(evidence, list):
        return relocated
    for item in evidence:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        chunk = chunks.get(str(item.get("chunk_id") or ""))
        if (
            not isinstance(quote, str)
            or not quote
            or not isinstance(chunk, Mapping)
            or item.get("page_number") != chunk.get("page_number")
        ):
            continue
        text = str(chunk.get("text") or "")
        start = item.get("start")
        end = item.get("end")
        if (
            type(start) is int
            and type(end) is int
            and 0 <= start < end <= len(text)
            and (
                text[start:end] == quote
                or normalize_grounding_text(text[start:end])
                == normalize_grounding_text(quote)
            )
        ):
            continue
        match_start = text.find(quote)
        if (
            match_start >= 0
            and text.find(quote, match_start + 1) < 0
        ):
            item["start"] = match_start
            item["end"] = match_start + len(quote)
    return relocated


def parse_cn_number(raw_value: str) -> Decimal:
    """Parse a Chinese disclosure number into its base-unit Decimal value."""

    text = normalize_grounding_text(raw_value).strip().replace(",", "")
    multiplier = Decimal("1")
    multiplier_match = re.search(r"(万亿|亿|万|千)(?:元|股|个|人|项)?", text)
    if multiplier_match:
        multiplier = {
            "千": Decimal("1000"),
            "万": Decimal("10000"),
            "亿": Decimal("100000000"),
            "万亿": Decimal("1000000000000"),
        }[multiplier_match.group(1)]

    number_match = re.search(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text)
    if number_match:
        try:
            return Decimal(number_match.group(0)) * multiplier
        except InvalidOperation as exc:
            raise CandidateValidationError("numeric_raw_value_mismatch") from exc

    chinese_match = re.search(r"[负零〇一二两三四五六七八九十百千万亿点]+", text)
    if chinese_match:
        return _parse_chinese_number(chinese_match.group(0))
    raise CandidateValidationError("numeric_raw_value_mismatch")


def parse_cn_percent(raw_value: str) -> Decimal:
    text = normalize_grounding_text(raw_value).strip()
    if "百分之" in text:
        value = parse_cn_number(text.split("百分之", 1)[1]) / Decimal("100")
    else:
        if "%" not in text:
            raise CandidateValidationError("unit_invalid")
        value = parse_cn_number(text.split("%", 1)[0]) / Decimal("100")
    if value > 0 and any(
        term in text
        for term in ("下降", "减少", "下滑", "降低", "负增长")
    ):
        return -value
    return value


def _apply_unit_multiplier(
    numeric_value: Decimal | None,
    raw_value: str | None,
    unit: str | None,
) -> Decimal | None:
    """Apply a standalone unit's scale when the raw text omits it.

    parse_cn_number already honours a multiplier embedded in the raw text
    (e.g. ``"26692.71万元"`` -> 266927100). When the executor splits the scale
    into the independent ``unit`` field (``raw_value="26692.71"``,
    ``unit="万元"``) the base-unit value must still be rescaled, otherwise a
    raw ``26692.71`` with ``unit="万元"`` would be stored as ``26692.71`` instead
    of ``266927100``. The multiplier is applied only when the raw text did NOT
    already embed a 万/亿/万亿/千 token, so an embedded scale is never
    double-counted.
    """

    if numeric_value is None or not unit:
        return numeric_value
    multiplier = _UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        return numeric_value
    if raw_value and re.search(r"(万亿|亿|万|千)", raw_value):
        return numeric_value
    return numeric_value * multiplier


def _compact_grounding_text(value: str) -> str:
    """NFKC-normalize and drop whitespace, mirroring quote relocation."""

    normalized = normalize_grounding_text(value)
    return "".join(character for character in normalized if not character.isspace())


def _fact_raw_value_grounded(
    fact: SemanticFact,
    evidence_by_id: Mapping[str, ValidatedEvidence],
) -> bool:
    """True when raw_value is a verbatim (whitespace-insensitive) substring of
    at least one cited evidence quote.

    Raw values that concatenate or rewrite source text (e.g. a distribution
    plan assembled from two sentences) are not grounded by any single quote and
    must be rejected. The comparison mirrors the whitespace-insensitive
    compaction used by quote relocation so minor spacing differences
    (``"26692.71 万元"`` vs ``"26692.71万元"``) still ground.
    """

    raw_value = (
        fact.raw_value.strip() if isinstance(fact.raw_value, str) else ""
    )
    if not raw_value:
        return True
    compact_raw = _compact_grounding_text(raw_value)
    if not compact_raw:
        return True
    cited_quotes: list[str] = []
    for evidence_id in fact.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        compact_quote = _compact_grounding_text(str(evidence.quote))
        cited_quotes.append(compact_quote)
        if compact_raw in compact_quote:
            return True
    if cited_quotes and compact_raw in "".join(cited_quotes):
        return True
    return False


def _shrink_text_fact_to_exact_evidence(
    fact: SemanticFact,
    evidence_by_id: Mapping[str, ValidatedEvidence],
) -> str | None:
    """Return one exact cited quote when the model only added extra affixes."""

    if len(fact.evidence_ids) != 1:
        return None
    evidence = evidence_by_id.get(fact.evidence_ids[0])
    raw_value = fact.raw_value.strip() if isinstance(fact.raw_value, str) else ""
    if evidence is None or not raw_value:
        return None
    quote = str(evidence.quote).strip()
    compact_quote = _compact_grounding_text(quote)
    compact_raw = _compact_grounding_text(raw_value)
    if len(compact_quote) < 4 or compact_quote not in compact_raw:
        return None
    return quote


def validate_candidate(
    event: SemanticEvent | object,
    evidence: Sequence[SemanticEvidence],
    chunks: Mapping[str, Mapping[str, object]],
    *,
    taxonomy: EventTaxonomy,
    issuer_entity_id: str,
    entity_whitelist: Mapping[str, Iterable[str]],
    document_metadata: Mapping[str, object] | None = None,
    prior_events: Sequence[Mapping[str, object] | ValidatedCandidate] | None = None,
) -> ValidatedCandidate:
    """Validate and canonicalize one already schema-parsed event."""

    if not isinstance(event, SemanticEvent):
        raise CandidateValidationError("schema_invalid")
    try:
        taxonomy_event = taxonomy.event(event.event_type)
    except KeyError as exc:
        raise CandidateValidationError("schema_invalid") from exc
    if event.lifecycle not in taxonomy_event.allowed_lifecycle:
        raise CandidateValidationError("schema_invalid")
    if event.event_type == "merger_restructuring":
        target_count = sum(
            1 for subject in event.subjects if subject.role == "target"
        )
        if target_count > 1:
            raise CandidateValidationError("merger_target_ambiguous")

    validated_evidence = _validate_evidence(evidence, chunks)
    evidence_ids = {item.evidence_id for item in validated_evidence}
    evidence_by_id = {
        item.evidence_id: item for item in validated_evidence
    }
    allowed = {
        str(entity_id).strip(): frozenset(str(role) for role in roles)
        for entity_id, roles in entity_whitelist.items()
    }
    issuer = str(issuer_entity_id).strip()
    subjects = _validate_subjects(
        event,
        required_roles=taxonomy_event.required_subject_roles,
        evidence_ids=evidence_ids,
        evidence_by_id=evidence_by_id,
        issuer_entity_id=issuer,
        entity_whitelist=allowed,
    )
    validated_facts = tuple(
        _validate_fact(
            fact,
            evidence_ids=evidence_ids,
            evidence_by_id=evidence_by_id,
            spec=taxonomy_event.fact_specs.get(fact.name),
        )
        for fact in event.facts
    )
    _validate_fact_ranges(validated_facts)
    effective_dates = _validate_dates(
        event.effective_dates,
        evidence_ids=evidence_ids,
    )
    _validate_required_fields(
        event,
        facts=validated_facts,
        effective_dates=effective_dates,
        taxonomy_event=taxonomy_event,
        prior_events=prior_events,
    )
    metadata = {
        str(key): str(value)
        for key, value in (document_metadata or {}).items()
        if value is not None
    }
    requirements = taxonomy_event.requirements_for(event.lifecycle)
    try:
        canonical_key = _canonical_key(
            event_type=event.event_type,
            dedupe_fields=taxonomy_event.dedupe_fields,
            subjects=subjects,
            facts=validated_facts,
            dates=effective_dates,
            metadata=metadata,
        )
    except CandidateValidationError as exc:
        if (
            exc.code == "required_fact_missing"
            and requirements.inherit_prior == "required"
        ):
            raise CandidateValidationError("revision_conflict") from exc
        raise
    prior_keys = _prior_canonical_keys(prior_events)
    matched_prior = canonical_key in prior_keys
    if requirements.inherit_prior == "required" and not matched_prior:
        raise CandidateValidationError("revision_conflict")
    if (
        requirements.inherit_prior == "if_matched"
        and not matched_prior
        and requirements.unmatched_fallback == "validate_default"
    ):
        _validate_requirement_presence(
            facts=validated_facts,
            effective_dates=effective_dates,
            requirements=taxonomy_event.default_requirements,
        )
    referenced = {
        evidence_id
        for collection in (
            event.subjects,
            event.facts,
            event.effective_dates,
            event.conditions,
            event.conflicts,
        )
        for item in collection
        for evidence_id in item.evidence_ids
    }
    if not referenced.issubset(evidence_ids):
        raise CandidateValidationError("evidence_chunk_missing")
    return ValidatedCandidate(
        event_type=event.event_type,
        lifecycle=event.lifecycle,
        subjects=subjects,
        facts=validated_facts,
        effective_dates=effective_dates,
        evidence=validated_evidence,
        canonical_key=canonical_key,
        required_evidence_count=max(1, len(referenced)),
        validated_evidence_count=len(referenced & evidence_ids),
        taxonomy_direction_rule=taxonomy_event.direction_rule,
        horizon_days=taxonomy_event.default_horizon_days,
    )


def _validate_evidence(
    evidence: Sequence[SemanticEvidence],
    chunks: Mapping[str, Mapping[str, object]],
) -> tuple[ValidatedEvidence, ...]:
    validated: list[ValidatedEvidence] = []
    seen: set[str] = set()
    for item in evidence:
        if not isinstance(item, SemanticEvidence):
            raise CandidateValidationError("schema_invalid")
        if item.evidence_id in seen:
            raise CandidateValidationError("schema_invalid")
        seen.add(item.evidence_id)
        chunk = chunks.get(item.chunk_id)
        if chunk is None:
            raise CandidateValidationError("evidence_chunk_missing")
        text = str(chunk.get("text") or "")
        if item.start < 0 or item.end <= item.start or item.end > len(text):
            raise CandidateValidationError("evidence_span_out_of_bounds")
        try:
            page_number = int(chunk.get("page_number"))
        except (TypeError, ValueError) as exc:
            raise CandidateValidationError("evidence_chunk_missing") from exc
        if page_number != item.page_number:
            raise CandidateValidationError("evidence_chunk_missing")
        source_span = text[item.start:item.end]
        if (
            source_span != item.quote
            and normalize_grounding_text(source_span)
            != normalize_grounding_text(item.quote)
        ):
            raise CandidateValidationError("evidence_quote_mismatch")
        normalized_quote = normalize_grounding_text(item.quote)
        if any(pattern.search(normalized_quote) for pattern in _PROMPT_INJECTION_PATTERNS):
            raise CandidateValidationError("prompt_injection_pattern")
        validated.append(
            ValidatedEvidence(
                evidence_id=item.evidence_id,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                start=item.start,
                end=item.end,
                quote=item.quote,
                normalized_quote_hash=hashlib.sha256(
                    normalized_quote.encode("utf-8")
                ).hexdigest(),
            )
        )
    return tuple(validated)


def _validate_subjects(
    event: SemanticEvent,
    *,
    required_roles: Sequence[str],
    evidence_ids: set[str],
    evidence_by_id: Mapping[str, ValidatedEvidence],
    issuer_entity_id: str,
    entity_whitelist: Mapping[str, frozenset[str]],
) -> tuple[dict[str, object], ...]:
    subjects: list[dict[str, object]] = []
    observed_roles: set[str] = set()
    for subject in event.subjects:
        entity_id = str(subject.entity_id).strip()
        role = str(subject.role).strip()
        if _is_b_share(entity_id):
            raise CandidateValidationError("b_share_rejected")
        if role not in VALID_SUBJECT_ROLES:
            raise CandidateValidationError("subject_role_invalid")
        if entity_id == issuer_entity_id:
            allowed_roles = entity_whitelist.get(entity_id, frozenset({"issuer"}))
            if role != "issuer" or role not in allowed_roles:
                raise CandidateValidationError("subject_role_invalid")
        elif entity_id.startswith("external:"):
            if role == "issuer":
                raise CandidateValidationError("subject_role_invalid")
            external_name = _normalize_entity_name(
                entity_id.removeprefix("external:")
            )
            if (
                not external_name
                or len(external_name) > 128
                or not _entity_name_matches_evidence(
                    external_name,
                    tuple(
                        evidence_by_id[evidence_id].quote
                        for evidence_id in subject.evidence_ids
                        if evidence_id in evidence_by_id
                    ),
                )
            ):
                raise CandidateValidationError("entity_not_whitelisted")
        elif entity_id not in entity_whitelist:
            raise CandidateValidationError("entity_not_whitelisted")
        elif role not in entity_whitelist[entity_id]:
            raise CandidateValidationError("subject_role_invalid")
        if not set(subject.evidence_ids).issubset(evidence_ids):
            raise CandidateValidationError("evidence_chunk_missing")
        observed_roles.add(role)
        subjects.append(
            {
                "entity_id": entity_id,
                "role": role,
                "evidence_ids": tuple(subject.evidence_ids),
            }
        )
    if not set(required_roles).issubset(observed_roles):
        raise CandidateValidationError("subject_role_invalid")
    return tuple(subjects)


def _normalize_entity_name(value: str) -> str:
    """Remove PDF layout whitespace without changing entity characters."""

    return "".join(
        character
        for character in normalize_grounding_text(value)
        if not character.isspace()
    )


def _entity_name_matches_evidence(
    entity_name: str,
    evidence_quotes: Sequence[str],
) -> bool:
    normalized_name = _normalize_entity_name(entity_name)
    normalized_quotes = tuple(
        _normalize_entity_name(quote)
        for quote in evidence_quotes
        if _normalize_entity_name(quote)
    )
    return bool(
        normalized_name
        and (
            normalized_name in normalized_quotes
            or "".join(normalized_quotes) == normalized_name
        )
    )


def _validate_fact(
    fact: SemanticFact,
    *,
    evidence_ids: set[str],
    evidence_by_id: Mapping[str, ValidatedEvidence],
    spec: FactSpec | None = None,
) -> ValidatedFact:
    if not set(fact.evidence_ids).issubset(evidence_ids):
        raise CandidateValidationError("evidence_chunk_missing")
    raw_value = fact.raw_value.strip() if isinstance(fact.raw_value, str) else None
    unit = _normalize_unit(fact.unit)
    currency = _normalize_currency(fact.currency)
    raw_currency = _currency_from_raw(
        " ".join(
            value
            for value in (raw_value, fact.unit)
            if isinstance(value, str)
        )
    )
    if raw_currency and currency and raw_currency != currency:
        raise CandidateValidationError("currency_invalid")
    if fact.currency is not None and currency is None:
        raise CandidateValidationError("currency_invalid")
    if fact.unit is not None and unit is None:
        raise CandidateValidationError("unit_invalid")
    if spec is None:
        expected_unit = _expected_unit(fact.name, raw_value or "")
        if unit is not None and expected_unit is not None:
            if _UNIT_ALIASES[unit] != expected_unit:
                raise CandidateValidationError("unit_invalid")
    else:
        explicit_unit_kind = _UNIT_ALIASES.get(unit) if unit else None
        raw_unit_kind = _unit_kind_from_raw(raw_value or "")
        if (
            explicit_unit_kind is not None
            and raw_unit_kind is not None
            and explicit_unit_kind != raw_unit_kind
        ):
            raise CandidateValidationError("fact_unit_incompatible")
        observed_unit_kind = explicit_unit_kind or raw_unit_kind
        if spec.value_type in {"number", "ratio"}:
            if observed_unit_kind not in spec.allowed_unit_kinds:
                raise CandidateValidationError("fact_unit_incompatible")
        elif fact.unit is not None:
            raise CandidateValidationError("fact_type_incompatible")

    provider_numeric = (
        Decimal(str(fact.numeric_value))
        if fact.numeric_value is not None
        else None
    )
    # Unified grounding contract: every non-empty raw_value must be a verbatim
    # (whitespace/NFKC-insensitive) substring of at least one cited evidence
    # quote. This catches numeric facts whose raw is absent from the quote AND
    # text facts whose raw rewrites/concatenates source text (e.g. a
    # distribution_plan assembled from two sentences). A fact whose raw_value
    # is not grounded by its own evidence cannot be trusted. Facts without a
    # raw_value (null per schema) follow the existing text/null handling.
    if raw_value and not _fact_raw_value_grounded(fact, evidence_by_id):
        repaired_raw = (
            _shrink_text_fact_to_exact_evidence(fact, evidence_by_id)
            if spec is not None and spec.value_type == "text"
            else None
        )
        if repaired_raw is None:
            raise CandidateValidationError("fact_raw_value_unsupported")
        raw_value = repaired_raw
    if spec is not None and spec.evidence_terms_any:
        cited_context = " ".join(
            _compact_grounding_text(evidence_by_id[evidence_id].quote)
            for evidence_id in fact.evidence_ids
            if evidence_id in evidence_by_id
        ).casefold()
        if not any(
            _compact_grounding_text(term).casefold() in cited_context
            for term in spec.evidence_terms_any
        ):
            raise CandidateValidationError("fact_evidence_context_missing")
    numeric_value: Decimal | None = None
    text_value: str | None = None
    if spec is None:
        is_numeric = _is_numeric_fact(
            fact.name,
            raw_value,
            fact.numeric_value,
        )
        is_percent = bool(raw_value) and _is_percent_fact(
            fact.name,
            raw_value,
        )
    else:
        is_numeric = spec.value_type in {"number", "ratio"}
        is_percent = spec.value_type == "ratio"
        if (
            spec.value_type in {"text", "period"}
            and provider_numeric is not None
        ):
            raise CandidateValidationError("fact_type_incompatible")
    if is_numeric:
        if raw_value is None:
            raise CandidateValidationError("numeric_raw_value_mismatch")
        if numeric_raw_value_is_ambiguous(raw_value, fact.name):
            raise CandidateValidationError("numeric_raw_value_ambiguous")
        cash_per_share = (
            _parse_cash_per_share(fact, evidence_by_id)
            if fact.name == "cash_per_share"
            else None
        )
        if is_percent:
            share_allotment_ratio = _parse_share_allotment_ratio(raw_value)
            if share_allotment_ratio is not None:
                numeric_value = share_allotment_ratio
            elif "%" in raw_value or "百分之" in raw_value:
                numeric_value = parse_cn_percent(raw_value)
            elif unit is not None and _UNIT_ALIASES[unit] == "ratio":
                numeric_value = parse_cn_number(raw_value) / Decimal("100")
                if numeric_value > 0 and any(
                    term in raw_value
                    for term in ("下降", "减少", "下滑", "降低", "负增长")
                ):
                    numeric_value = -numeric_value
            else:
                raise CandidateValidationError("unit_invalid")
        elif cash_per_share is not None:
            numeric_value = cash_per_share
        else:
            numeric_value = parse_cn_number(raw_value)
        numeric_value = _apply_unit_multiplier(numeric_value, raw_value, unit)
        if provider_numeric is not None and provider_numeric != numeric_value:
            raise CandidateValidationError("numeric_raw_value_mismatch")
    else:
        text_value = raw_value

    is_period = (
        spec.value_type == "period"
        if spec is not None
        else fact.name == "period"
    )
    period = _normalize_period(
        fact.period or (raw_value if is_period else None)
    )
    if is_period and period is None:
        raise CandidateValidationError("date_invalid")
    return ValidatedFact(
        name=fact.name,
        raw_value=raw_value,
        numeric_value=numeric_value,
        text_value=text_value,
        unit=unit,
        currency=currency or raw_currency,
        period=period,
        evidence_ids=tuple(fact.evidence_ids),
        provider_numeric_value=provider_numeric,
    )


def _validate_dates(
    effective_dates: Sequence[SemanticEffectiveDate],
    *,
    evidence_ids: set[str],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for item in effective_dates:
        if not set(item.evidence_ids).issubset(evidence_ids):
            raise CandidateValidationError("evidence_chunk_missing")
        try:
            normalized = date.fromisoformat(str(item.value)).isoformat()
        except (TypeError, ValueError) as exc:
            raise CandidateValidationError("date_invalid") from exc
        rows.append(
            {
                "kind": item.kind,
                "value": normalized,
                "evidence_ids": tuple(item.evidence_ids),
            }
        )
    return tuple(rows)


def _validate_required_fields(
    event: SemanticEvent,
    *,
    facts: Sequence[ValidatedFact],
    effective_dates: Sequence[Mapping[str, object]],
    taxonomy_event,
    prior_events: Sequence[Mapping[str, object] | ValidatedCandidate] | None,
) -> None:
    if event.missing_required_fields:
        raise CandidateValidationError(
            "required_fact_missing",
            detail=(
                "declared_missing="
                + ",".join(sorted(event.missing_required_fields))
            ),
        )
    requirements = taxonomy_event.requirements_for(event.lifecycle)
    if requirements.inherit_prior == "required" and not prior_events:
        raise CandidateValidationError("revision_conflict")
    _validate_requirement_presence(
        facts=facts,
        effective_dates=effective_dates,
        requirements=requirements,
    )


def _validate_requirement_presence(
    *,
    facts: Sequence[ValidatedFact],
    effective_dates: Sequence[Mapping[str, object]],
    requirements,
) -> None:
    fact_names = {fact.name for fact in facts}
    missing_facts = sorted(set(requirements.all_of) - fact_names)
    if missing_facts:
        raise CandidateValidationError(
            "required_fact_missing",
            detail="missing_facts=" + ",".join(missing_facts),
        )
    if requirements.one_of_sets and not any(
        set(group).issubset(fact_names)
        for group in requirements.one_of_sets
    ):
        alternatives = "|".join(
            "+".join(group) for group in requirements.one_of_sets
        )
        raise CandidateValidationError(
            "required_fact_missing",
            detail=f"missing_one_of={alternatives}",
        )
    date_names = {str(item["kind"]) for item in effective_dates}
    missing_dates = sorted(set(requirements.required_dates) - date_names)
    if missing_dates:
        raise CandidateValidationError(
            "required_fact_missing",
            detail="missing_dates=" + ",".join(missing_dates),
        )


def _validate_fact_ranges(facts: Sequence[ValidatedFact]) -> None:
    values = {
        fact.name: fact.numeric_value
        for fact in facts
        if fact.numeric_value is not None
    }
    for lower_name, lower_value in values.items():
        if not lower_name.endswith("_lower"):
            continue
        upper_name = f"{lower_name[:-6]}_upper"
        upper_value = values.get(upper_name)
        if upper_value is None:
            continue
        if lower_value > upper_value:
            raise CandidateValidationError("numeric_raw_value_mismatch")
        lower_fact = next(
            fact for fact in facts if fact.name == lower_name
        )
        upper_fact = next(
            fact for fact in facts if fact.name == upper_name
        )
        if (
            lower_fact.currency
            and upper_fact.currency
            and lower_fact.currency != upper_fact.currency
        ):
            raise CandidateValidationError("currency_invalid")
        if (
            lower_fact.unit
            and upper_fact.unit
            and _UNIT_ALIASES[lower_fact.unit]
            != _UNIT_ALIASES[upper_fact.unit]
        ):
            raise CandidateValidationError("unit_invalid")


def _canonical_key(
    *,
    event_type: str,
    dedupe_fields: Sequence[str],
    subjects: Sequence[Mapping[str, object]],
    facts: Sequence[ValidatedFact],
    dates: Sequence[Mapping[str, object]],
    metadata: Mapping[str, str],
) -> str:
    subject_by_role = {
        str(subject["role"]): str(subject["entity_id"])
        for subject in subjects
    }
    fact_by_name = {fact.name: fact for fact in facts}
    date_by_kind = {
        str(item["kind"]): str(item["value"])
        for item in dates
    }
    components = [event_type]
    for field in dedupe_fields:
        namespace, name = field.split(":", 1)
        if namespace == "subject":
            value = subject_by_role.get(name)
        elif namespace == "fact":
            fact = fact_by_name.get(name)
            value = _fact_key_value(fact) if fact is not None else None
        elif namespace == "date":
            value = date_by_kind.get(name)
        else:
            value = metadata.get(name)
        if value is None or str(value).strip() == "":
            raise CandidateValidationError(
                "required_fact_missing",
                detail=f"missing_dedupe={field}",
            )
        components.append(f"{field}={str(value).strip()}")
    return "|".join(components)


def _fact_key_value(fact: ValidatedFact) -> str | None:
    if fact.numeric_value is not None:
        return format(fact.numeric_value.normalize(), "f")
    return fact.period or fact.text_value or fact.raw_value


def _prior_canonical_keys(
    prior_events: Sequence[Mapping[str, object] | ValidatedCandidate] | None,
) -> frozenset[str]:
    keys: set[str] = set()
    for prior in prior_events or ():
        if isinstance(prior, ValidatedCandidate):
            value = prior.canonical_key
        elif isinstance(prior, Mapping):
            value = prior.get("canonical_key")
        else:
            value = None
        if value:
            keys.add(str(value))
    return frozenset(keys)


def _normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None
    return _CURRENCY_ALIASES.get(normalize_grounding_text(value).strip())


def _normalize_unit(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", "", normalize_grounding_text(value).strip())
    if text in _UNIT_ALIASES:
        return text
    unit_only = text
    for currency_marker in (
        "人民币",
        "RMB",
        "CNY",
        "美元",
        "USD",
        "港元",
        "HKD",
        "欧元",
        "EUR",
    ):
        unit_only = unit_only.replace(currency_marker, "")
    return unit_only if unit_only in _UNIT_ALIASES else None


def _currency_from_raw(value: str) -> str | None:
    upper = value.upper()
    if "美元" in value or "USD" in upper:
        return "USD"
    if "港元" in value or "HKD" in upper:
        return "HKD"
    if "欧元" in value or "EUR" in upper:
        return "EUR"
    if "人民币" in value or "RMB" in upper or "CNY" in upper or "元" in value:
        return "CNY"
    return None


def _unit_kind_from_raw(raw_value: str) -> str | None:
    text = normalize_grounding_text(raw_value).strip()
    if not text:
        return None
    if _parse_share_allotment_ratio(text) is not None:
        return "ratio"
    if "%" in text or "百分之" in text:
        return "ratio"
    if "元/股" in text or "人民币元/股" in text or "CNY/share" in text:
        return "price"
    if "年产" in text or "/年" in text:
        for token, kind in (
            ("平方米", "area_rate"),
            ("万平方米", "area_rate"),
            ("万吨", "mass_rate"),
            ("吨", "mass_rate"),
            ("万台", "count_rate"),
            ("台", "count_rate"),
            ("万片", "count_rate"),
            ("片", "count_rate"),
        ):
            if token in text:
                return kind
    for token, kind in sorted(
        _UNIT_ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if token in text:
            return kind
    return None


def _expected_unit(name: str, raw_value: str) -> str | None:
    if _is_percent_fact(name, raw_value):
        return "ratio"
    if "股" in raw_value and "元/股" not in raw_value:
        return "shares"
    if "元/股" in raw_value:
        return "price"
    if any(
        _fact_name_has_marker(name, marker)
        for marker in ("price", "cash_per")
    ):
        return "currency"
    if any(
        _fact_name_has_marker(name, marker)
        for marker in ("amount", "asset", "profit", "revenue", "loss", "value")
    ):
        return "currency"
    if any(
        _fact_name_has_marker(name, marker)
        for marker in ("count", "share_count", "holding_after")
    ):
        return "shares" if "share" in name or "holding" in name else "count"
    return None


def _fact_name_has_marker(name: str, marker: str) -> bool:
    normalized = str(name).strip().lower()
    normalized_marker = str(marker).strip().lower()
    if "_" in normalized_marker:
        return normalized_marker in normalized
    return normalized_marker in normalized.split("_")


def _is_numeric_fact(
    name: str,
    raw_value: str | None,
    provider_numeric_value: int | float | None,
) -> bool:
    if name in _TEXT_FACT_NAMES or name == "period":
        return False
    return (
        provider_numeric_value is not None
        or any(
            _fact_name_has_marker(name, marker)
            for marker in _NUMERIC_NAME_MARKERS
        )
        or bool(raw_value and re.search(r"\d", raw_value))
    )


def _is_percent_fact(name: str, raw_value: str) -> bool:
    return (
        "%" in raw_value
        or "百分之" in raw_value
        or any(
            _fact_name_has_marker(name, marker)
            for marker in ("ratio", "rate", "yoy")
        )
    )


def _normalize_period(value: str | None) -> str | None:
    if value is None:
        return None
    text = normalize_grounding_text(value).strip()
    compact = re.sub(r"\s+", "", text)
    half_year_match = re.fullmatch(
        r"(\d{4})年(?:半年度|上半年)",
        compact,
    )
    if half_year_match:
        return f"{half_year_match.group(1)}H1"
    chinese_match = re.fullmatch(
        r"(\d{4})年(?:度)?(?:第)?([一二三四1234])季度",
        compact,
    )
    if chinese_match:
        quarter_map = {"一": "1", "二": "2", "三": "3", "四": "4"}
        return (
            f"{chinese_match.group(1)}Q"
            f"{quarter_map.get(chinese_match.group(2), chinese_match.group(2))}"
        )
    annual_match = re.fullmatch(r"(\d{4})年(?:度)?", compact)
    if annual_match:
        return annual_match.group(1)
    date_range_match = re.fullmatch(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:至|[-–—~～])"
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        compact,
    )
    if date_range_match:
        start_year, start_month, start_day = (
            int(date_range_match.group(index)) for index in (1, 2, 3)
        )
        end_year, end_month, end_day = (
            int(date_range_match.group(index)) for index in (4, 5, 6)
        )
        if (
            start_year == end_year
            and (start_month, start_day) == (1, 1)
            and (end_month, end_day) == (12, 31)
        ):
            return str(start_year)
        if (
            start_year == end_year
            and (start_month, start_day) == (1, 1)
            and (end_month, end_day) == (6, 30)
        ):
            return f"{start_year}H1"
    if _PERIOD_PATTERN.fullmatch(text):
        return text.upper()
    return None


def _parse_share_allotment_ratio(value: str) -> Decimal | None:
    text = re.sub(r"\s+", "", normalize_grounding_text(value))
    match = re.search(
        r"每(?P<base>[\d,.\u96f6〇一二两三四五六七八九十百千万]+)股"
        r"(?:配|送|转增|转)"
        r"(?P<allotment>[\d,.\u96f6〇一二两三四五六七八九十百千万]+)股",
        text,
    )
    if match is None:
        return None
    base = parse_cn_number(match.group("base"))
    allotment = parse_cn_number(match.group("allotment"))
    if base <= 0 or allotment < 0:
        raise CandidateValidationError("numeric_raw_value_mismatch")
    return allotment / base


def _parse_cash_per_share(
    fact: SemanticFact,
    evidence_by_id: Mapping[str, ValidatedEvidence],
) -> Decimal | None:
    raw_value = fact.raw_value.strip() if isinstance(fact.raw_value, str) else ""
    raw_numbers = _numeric_tokens(raw_value)
    cited_context = "".join(
        _compact_grounding_text(evidence_by_id[evidence_id].quote)
        for evidence_id in fact.evidence_ids
        if evidence_id in evidence_by_id
    )
    pattern = re.compile(
        r"每(?P<base>[\d,.]+)股"
        r"(?:派(?:发)?(?:现金)?|分配(?:现金)?)"
        r"(?P<cash>[\d,.]+)元"
    )
    for match in pattern.finditer(cited_context):
        base = parse_cn_number(match.group("base"))
        cash = parse_cn_number(match.group("cash"))
        if base <= 0:
            raise CandidateValidationError("numeric_raw_value_mismatch")
        if (
            len(raw_numbers) == 1
            and raw_numbers[0] == cash
        ) or _compact_grounding_text(match.group(0)) in _compact_grounding_text(raw_value):
            return cash / base
    return None


def _numeric_tokens(value: str) -> tuple[Decimal, ...]:
    tokens: list[Decimal] = []
    for match in re.finditer(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)", value):
        try:
            tokens.append(Decimal(match.group(0).replace(",", "")))
        except InvalidOperation as exc:
            raise CandidateValidationError("numeric_raw_value_mismatch") from exc
    return tuple(tokens)


def numeric_raw_value_is_ambiguous(raw_value: str, fact_name: str) -> bool:
    """Whether one scalar fact contains multiple unsupported numeric values."""

    if fact_name in {"dilution_ratio", "stock_per_share"}:
        if _parse_share_allotment_ratio(raw_value) is not None:
            return False
    if fact_name == "cash_per_share" and re.search(
        r"每\s*[\d,.]+\s*股\s*(?:派(?:发)?(?:现金)?|分配(?:现金)?)\s*[\d,.]+\s*元",
        normalize_grounding_text(raw_value),
    ):
        return False
    return len(_numeric_tokens(raw_value)) > 1


def _parse_chinese_number(value: str) -> Decimal:
    negative = value.startswith("负")
    text = value[1:] if negative else value
    if "点" in text:
        integer_text, decimal_text = text.split("点", 1)
        integer = _parse_chinese_integer(integer_text)
        digits = "".join(
            str(_CHINESE_DIGITS[character])
            for character in decimal_text
            if character in _CHINESE_DIGITS
        )
        if not digits:
            raise CandidateValidationError("numeric_raw_value_mismatch")
        result = Decimal(f"{integer}.{digits}")
    else:
        result = Decimal(_parse_chinese_integer(text))
    return -result if negative else result


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
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
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}


def _parse_chinese_integer(value: str) -> int:
    if not value:
        return 0
    if all(character in _CHINESE_DIGITS for character in value):
        return int("".join(str(_CHINESE_DIGITS[character]) for character in value))
    total = 0
    section = 0
    number = 0
    for character in value:
        if character in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[character]
        elif character in _SMALL_UNITS:
            section += (number or 1) * _SMALL_UNITS[character]
            number = 0
        elif character in _LARGE_UNITS:
            section += number
            total += section * _LARGE_UNITS[character]
            section = 0
            number = 0
        else:
            raise CandidateValidationError("numeric_raw_value_mismatch")
    return total + section + number


def _is_b_share(entity_id: str) -> bool:
    code = re.sub(r"\D", "", str(entity_id))
    return code.startswith(_B_SHARE_PREFIXES)


__all__ = [
    "CandidateValidationError",
    "NORMALIZATION_VERSION",
    "ValidatedCandidate",
    "ValidatedEvidence",
    "ValidatedFact",
    "normalize_grounding_text",
    "numeric_raw_value_is_ambiguous",
    "parse_cn_number",
    "parse_cn_percent",
    "relocate_evidence_offsets",
    "validate_candidate",
]
