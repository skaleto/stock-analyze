"""Strict evidence-grounded contracts for multi-event announcement output."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TypeAlias
import unicodedata

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .taxonomy import EventTaxonomy, VALID_LIFECYCLES


SCHEMA_VERSION = "announcement-events-v1"
LITE_SCHEMA_VERSION = "announcement-events-v1-lite"
_PROMPT_FILES = {
    "announcement-event-v1": "announcement_event_v1.md",
    "announcement-event-v2": "announcement_event_v2.md",
    "semantic-extract-v1": "semantic_extract_v1.md",
    "semantic-extract-v2": "semantic_extract_v2.md",
    "semantic-extract-v3": "semantic_extract_v3.md",
    "semantic-extract-v4": "semantic_extract_v4.md",
    "semantic-extract-v5": "semantic_extract_v5.md",
    "semantic-extract-v6": "semantic_extract_v6.md",
    "semantic-extract-v7": "semantic_extract_v7.md",
    "semantic-extract-v8": "semantic_extract_v8.md",
    "semantic-extract-v9": "semantic_extract_v9.md",
    "semantic-extract-v10": "semantic_extract_v10.md",
    "semantic-extract-v11": "semantic_extract_v11.md",
    "semantic-extract-v12": "semantic_extract_v12.md",
    "semantic-extract-v13": "semantic_extract_v13.md",
    "semantic-mentions-v1": "semantic_mentions_v1.md",
    "semantic-mentions-v2": "semantic_mentions_v2.md",
    "semantic-mentions-v3": "semantic_mentions_v3.md",
    "semantic-mentions-v4": "semantic_mentions_v4.md",
    "semantic-mentions-v5": "semantic_mentions_v5.md",
    "semantic-mentions-v6": "semantic_mentions_v6.md",
    "semantic-mentions-v7": "semantic_mentions_v7.md",
    "semantic-mentions-v8": "semantic_mentions_v8.md",
    "semantic-mentions-v9": "semantic_mentions_v9.md",
    "semantic-mentions-v10": "semantic_mentions_v10.md",
    "semantic-mentions-v11": "semantic_mentions_v11.md",
    "semantic-mentions-v12": "semantic_mentions_v12.md",
    "semantic-mentions-v13": "semantic_mentions_v13.md",
    "semantic-mentions-v14": "semantic_mentions_v14.md",
    "semantic-mentions-v15": "semantic_mentions_v15.md",
    "semantic-mentions-v16": "semantic_mentions_v16.md",
    "semantic-mentions-v17": "semantic_mentions_v17.md",
}
JsonScalar: TypeAlias = str | int | float | bool | None


class SemanticContractError(ValueError):
    """A semantic result rejection with a stable machine-readable code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def load_semantic_prompt(
    repo_root: str | Path,
    prompt_version: str,
) -> str:
    filename = _PROMPT_FILES.get(str(prompt_version).strip())
    if filename is None:
        raise SemanticContractError(
            "semantic_prompt_version_unknown"
        )
    path = (
        Path(repo_root)
        / "stock_analyze"
        / "intelligence"
        / "semantic"
        / "prompts"
        / filename
    )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SemanticContractError(
            "semantic_prompt_unreadable"
        ) from exc


@dataclass(frozen=True)
class SemanticEvidence:
    evidence_id: str
    page_number: int
    chunk_id: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True)
class SemanticSubject:
    entity_id: str
    role: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticFact:
    name: str
    raw_value: str | None
    numeric_value: int | float | None
    unit: str | None
    currency: str | None
    period: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticEffectiveDate:
    kind: str
    value: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticStatement:
    name: str
    value: JsonScalar
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticEvent:
    event_type: str
    lifecycle: str
    subjects: tuple[SemanticSubject, ...]
    facts: tuple[SemanticFact, ...]
    effective_dates: tuple[SemanticEffectiveDate, ...]
    conditions: tuple[SemanticStatement, ...]
    conflicts: tuple[SemanticStatement, ...]
    missing_required_fields: tuple[str, ...]


@dataclass(frozen=True)
class SemanticDocumentResult:
    document_id: int
    schema_version: str
    events: tuple[SemanticEvent, ...]
    evidence: tuple[SemanticEvidence, ...]
    no_event_reason: str | None


def announcement_event_schema(taxonomy: EventTaxonomy) -> dict[str, object]:
    """Return the provider-neutral Draft 2020-12 response schema."""

    event_types = [event.event_type for event in taxonomy.events]
    event_constraints: list[dict[str, object]] = []
    for event in taxonomy.events:
        event_constraints.append(
            {
                "if": {
                    "properties": {"event_type": {"const": event.event_type}},
                    "required": ["event_type"],
                },
                "then": {
                    "properties": {
                        "lifecycle": {
                            "enum": list(event.allowed_lifecycle),
                        },
                        "facts": {
                            "items": {
                                "properties": {
                                    "name": {
                                        "enum": sorted(event.declared_facts),
                                    }
                                }
                            }
                        },
                    }
                },
            }
        )

    evidence_ids = {
        "type": "array",
        "items": {"$ref": "#/$defs/evidence_id"},
        "minItems": 1,
        "uniqueItems": True,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://stock-analyze.local/schemas/announcement-events-v1.json",
        "title": "Announcement semantic events",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "document_id",
            "schema_version",
            "events",
            "evidence",
            "no_event_reason",
        ],
        "properties": {
            "document_id": {"type": "integer", "minimum": 1},
            "schema_version": {"const": SCHEMA_VERSION},
            "events": {
                "type": "array",
                "items": {"$ref": "#/$defs/event"},
            },
            "evidence": {
                "type": "array",
                "items": {"$ref": "#/$defs/evidence"},
            },
            "no_event_reason": {
                "type": ["string", "null"],
            },
        },
        "oneOf": [
            {
                "properties": {
                    "events": {"minItems": 1},
                    "no_event_reason": {"type": "null"},
                }
            },
            {
                "properties": {
                    "events": {"maxItems": 0},
                    "no_event_reason": {"type": "string", "minLength": 1},
                }
            },
        ],
        "$defs": {
            "evidence_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
            },
            "subject": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_id", "role", "evidence_ids"],
                "properties": {
                    "entity_id": {"type": "string", "minLength": 1},
                    "role": {
                        "type": "string",
                        "pattern": r"^[a-z][a-z0-9_]*$",
                    },
                    "evidence_ids": evidence_ids,
                },
            },
            "fact": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "raw_value",
                    "numeric_value",
                    "unit",
                    "currency",
                    "period",
                    "evidence_ids",
                ],
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": r"^[a-z][a-z0-9_]*$",
                    },
                    "raw_value": {"type": ["string", "null"]},
                    "numeric_value": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "currency": {"type": ["string", "null"]},
                    "period": {"type": ["string", "null"]},
                    "evidence_ids": evidence_ids,
                },
            },
            "effective_date": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "value", "evidence_ids"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "pattern": r"^[a-z][a-z0-9_]*$",
                    },
                    "value": {"type": "string", "format": "date"},
                    "evidence_ids": evidence_ids,
                },
            },
            "statement": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value", "evidence_ids"],
                "properties": {
                    "name": {
                        "type": "string",
                        "pattern": r"^[a-z][a-z0-9_]*$",
                    },
                    "value": {
                        "type": ["string", "number", "boolean", "null"],
                    },
                    "evidence_ids": evidence_ids,
                },
            },
            "event": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "event_type",
                    "lifecycle",
                    "subjects",
                    "facts",
                    "effective_dates",
                    "conditions",
                    "conflicts",
                    "missing_required_fields",
                ],
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": event_types,
                    },
                    "lifecycle": {
                        "type": "string",
                        "enum": sorted(VALID_LIFECYCLES),
                    },
                    "subjects": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/subject"},
                        "minItems": 1,
                    },
                    "facts": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/fact"},
                    },
                    "effective_dates": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/effective_date"},
                    },
                    "conditions": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/statement"},
                    },
                    "conflicts": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/statement"},
                    },
                    "missing_required_fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "uniqueItems": True,
                    },
                },
                "allOf": event_constraints,
            },
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "evidence_id",
                    "page_number",
                    "chunk_id",
                    "start",
                    "end",
                    "quote",
                ],
                "properties": {
                    "evidence_id": {"$ref": "#/$defs/evidence_id"},
                    "page_number": {"type": "integer", "minimum": 1},
                    "chunk_id": {"type": "string", "minLength": 1},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                    "quote": {"type": "string", "minLength": 1},
                },
            },
        },
    }


def announcement_event_lite_schema(
    taxonomy: EventTaxonomy,
) -> dict[str, object]:
    """Return the executor-facing schema without provider-supplied offsets."""

    schema = deepcopy(announcement_event_schema(taxonomy))
    schema["$id"] = (
        "https://stock-analyze.local/schemas/"
        "announcement-events-v1-lite.json"
    )
    schema["title"] = "Provider-neutral announcement semantic events"
    schema["properties"]["schema_version"] = {
        "const": LITE_SCHEMA_VERSION,
    }
    schema["$defs"]["evidence"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_id", "chunk_id", "quote"],
        "properties": {
            "evidence_id": {"$ref": "#/$defs/evidence_id"},
            "chunk_id": {"type": "string", "minLength": 1},
            "quote": {"type": "string", "minLength": 1},
        },
    }
    return schema


def parse_lite_semantic_document_result(
    payload: object,
    taxonomy: EventTaxonomy,
    chunks: Mapping[str, Mapping[str, object]],
) -> SemanticDocumentResult:
    """Validate executor output and locate every evidence span locally."""

    schema = announcement_event_lite_schema(taxonomy)
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    errors = tuple(
        sorted(
            validator.iter_errors(payload),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                str(error.validator),
                error.message,
            ),
        )
    )
    if errors:
        code = _schema_error_code(payload, taxonomy, errors)
        raise SemanticContractError(code, detail=_error_location(errors[0]))

    assert isinstance(payload, dict)
    normalized = deepcopy(payload)
    _split_multichunk_lite_evidence(normalized, chunks)
    normalized["schema_version"] = SCHEMA_VERSION
    for evidence in normalized["evidence"]:
        chunk_id = str(evidence["chunk_id"])
        chunk = chunks.get(chunk_id)
        if not isinstance(chunk, Mapping):
            raise SemanticContractError(
                "semantic_evidence_chunk_missing",
                detail=chunk_id,
            )
        quote = str(evidence["quote"])
        text = str(chunk.get("text") or "")
        start, end, source_quote = _relocate_lite_quote(
            text,
            quote,
            chunk_id=chunk_id,
        )
        page_number = chunk.get("page_number")
        if type(page_number) is not int or page_number < 1:
            raise SemanticContractError(
                "semantic_evidence_page_invalid",
                detail=chunk_id,
            )
        evidence["page_number"] = page_number
        evidence["start"] = start
        evidence["end"] = end
        evidence["quote"] = source_quote
    return parse_semantic_document_result(normalized, taxonomy)


def _split_multichunk_lite_evidence(
    payload: dict[str, object],
    chunks: Mapping[str, Mapping[str, object]],
) -> None:
    raw_evidence = payload.get("evidence")
    raw_events = payload.get("events")
    if not isinstance(raw_evidence, list) or not isinstance(raw_events, list):
        return
    existing_ids = {
        str(item.get("evidence_id") or "")
        for item in raw_evidence
        if isinstance(item, Mapping)
    }
    replacements: dict[str, tuple[str, ...]] = {}
    expanded: list[object] = []
    for raw_item in raw_evidence:
        if not isinstance(raw_item, dict):
            expanded.append(raw_item)
            continue
        chunk_id = str(raw_item.get("chunk_id") or "")
        quote = str(raw_item.get("quote") or "")
        chunk = chunks.get(chunk_id)
        if chunk is None:
            expanded.append(raw_item)
            continue
        try:
            _relocate_lite_quote(
                str(chunk.get("text") or ""),
                quote,
                chunk_id=chunk_id,
            )
        except SemanticContractError as exc:
            if exc.code != "semantic_evidence_quote_missing":
                expanded.append(raw_item)
                continue
        else:
            expanded.append(raw_item)
            continue

        parts = _locate_quote_across_chunks(
            quote,
            named_chunk_id=chunk_id,
            chunks=chunks,
        )
        if len(parts) < 2:
            expanded.append(raw_item)
            continue
        evidence_id = str(raw_item.get("evidence_id") or "")
        part_ids = tuple(
            evidence_id if index == 1 else f"{evidence_id}__part{index}"
            for index in range(1, len(parts) + 1)
        )
        if any(
            part_id in existing_ids and part_id != evidence_id
            for part_id in part_ids
        ):
            expanded.append(raw_item)
            continue
        replacements[evidence_id] = part_ids
        existing_ids.update(part_ids)
        for part_id, (part_chunk_id, part_quote) in zip(part_ids, parts):
            expanded.append(
                {
                    "evidence_id": part_id,
                    "chunk_id": part_chunk_id,
                    "quote": part_quote,
                }
            )
    if not replacements:
        return
    payload["evidence"] = expanded
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            continue
        for collection_name in (
            "subjects",
            "facts",
            "effective_dates",
            "conditions",
            "conflicts",
        ):
            collection = raw_event.get(collection_name)
            if not isinstance(collection, list):
                continue
            for raw_value in collection:
                if not isinstance(raw_value, dict):
                    continue
                evidence_ids = raw_value.get("evidence_ids")
                if not isinstance(evidence_ids, list):
                    continue
                raw_value["evidence_ids"] = [
                    replacement_id
                    for evidence_id in evidence_ids
                    for replacement_id in replacements.get(
                        str(evidence_id),
                        (str(evidence_id),),
                    )
                ]


def _locate_quote_across_chunks(
    quote: str,
    *,
    named_chunk_id: str,
    chunks: Mapping[str, Mapping[str, object]],
) -> tuple[tuple[str, str], ...]:
    named_chunk = chunks.get(named_chunk_id)
    if named_chunk is None:
        return ()
    page_number = named_chunk.get("page_number")
    page_chunks = [
        (chunk_id, chunk)
        for chunk_id, chunk in chunks.items()
        if chunk.get("page_number") == page_number
    ]
    compact_quote, _, _ = _compact_quote_text(quote)
    if not compact_quote:
        return ()

    compact_source: list[str] = []
    source_locations: list[tuple[str, int, int]] = []
    source_texts: dict[str, str] = {}
    for chunk_id, chunk in page_chunks:
        text = str(chunk.get("text") or "")
        source_texts[chunk_id] = text
        for source_index, character in enumerate(text):
            for normalized_character in unicodedata.normalize("NFKC", character):
                if normalized_character.isspace():
                    continue
                compact_source.append(normalized_character)
                source_locations.append(
                    (chunk_id, source_index, source_index + 1)
                )
    compact_page = "".join(compact_source)
    match_start = compact_page.find(compact_quote)
    if match_start < 0:
        return ()
    if compact_page.find(compact_quote, match_start + 1) >= 0:
        return ()
    matched = source_locations[
        match_start : match_start + len(compact_quote)
    ]
    if not matched or named_chunk_id not in {item[0] for item in matched}:
        return ()

    spans: list[tuple[str, int, int]] = []
    for chunk_id, start, end in matched:
        if spans and spans[-1][0] == chunk_id:
            previous_id, previous_start, _ = spans[-1]
            spans[-1] = (previous_id, previous_start, end)
        else:
            spans.append((chunk_id, start, end))
    if len(spans) < 2:
        return ()
    return tuple(
        (chunk_id, source_texts[chunk_id][start:end])
        for chunk_id, start, end in spans
    )


def _relocate_lite_quote(
    text: str,
    quote: str,
    *,
    chunk_id: str,
) -> tuple[int, int, str]:
    start = text.find(quote)
    if start >= 0:
        if text.find(quote, start + 1) >= 0:
            raise SemanticContractError(
                "semantic_evidence_quote_ambiguous",
                detail=chunk_id,
            )
        return start, start + len(quote), quote

    compact_text, starts, ends = _compact_quote_text(text)
    compact_quote, _, _ = _compact_quote_text(quote)
    if not compact_quote:
        raise SemanticContractError(
            "semantic_evidence_quote_missing",
            detail=chunk_id,
        )
    compact_start = compact_text.find(compact_quote)
    if compact_start < 0:
        raise SemanticContractError(
            "semantic_evidence_quote_missing",
            detail=chunk_id,
        )
    if compact_text.find(compact_quote, compact_start + 1) >= 0:
        raise SemanticContractError(
            "semantic_evidence_quote_ambiguous",
            detail=chunk_id,
        )
    compact_end = compact_start + len(compact_quote)
    source_start = starts[compact_start]
    source_end = ends[compact_end - 1]
    return source_start, source_end, text[source_start:source_end]


def _compact_quote_text(
    value: str,
) -> tuple[str, list[int], list[int]]:
    characters: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, character in enumerate(value):
        normalized = unicodedata.normalize("NFKC", character)
        for normalized_character in normalized:
            if normalized_character.isspace():
                continue
            characters.append(normalized_character)
            starts.append(index)
            ends.append(index + 1)
    return "".join(characters), starts, ends


def parse_semantic_document_result(
    payload: object,
    taxonomy: EventTaxonomy,
) -> SemanticDocumentResult:
    """Validate first, then materialize an immutable semantic result."""

    schema = announcement_event_schema(taxonomy)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = tuple(
        sorted(
            validator.iter_errors(payload),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                str(error.validator),
                error.message,
            ),
        )
    )
    if errors:
        code = _schema_error_code(payload, taxonomy, errors)
        raise SemanticContractError(code, detail=_error_location(errors[0]))

    # The validator proves the exact object shapes before any field is read.
    assert isinstance(payload, dict)
    raw_events = payload["events"]
    raw_evidence = payload["evidence"]
    assert isinstance(raw_events, list)
    assert isinstance(raw_evidence, list)

    evidence_ids: set[str] = set()
    for raw_item in raw_evidence:
        evidence_id = raw_item["evidence_id"]
        if evidence_id in evidence_ids:
            raise SemanticContractError("semantic_evidence_duplicate")
        evidence_ids.add(evidence_id)
        if raw_item["end"] <= raw_item["start"]:
            raise SemanticContractError("semantic_evidence_span_invalid")

    referenced_ids: set[str] = set()
    for raw_event in raw_events:
        for collection_name in (
            "subjects",
            "facts",
            "effective_dates",
            "conditions",
            "conflicts",
        ):
            for raw_item in raw_event[collection_name]:
                referenced_ids.update(raw_item["evidence_ids"])
    if not referenced_ids.issubset(evidence_ids):
        raise SemanticContractError("semantic_evidence_dangling")

    return SemanticDocumentResult(
        document_id=payload["document_id"],
        schema_version=payload["schema_version"],
        events=tuple(_parse_event(raw_event) for raw_event in raw_events),
        evidence=tuple(
            SemanticEvidence(
                evidence_id=raw_item["evidence_id"],
                page_number=raw_item["page_number"],
                chunk_id=raw_item["chunk_id"],
                start=raw_item["start"],
                end=raw_item["end"],
                quote=raw_item["quote"],
            )
            for raw_item in raw_evidence
        ),
        no_event_reason=payload["no_event_reason"],
    )


def _parse_event(raw_event: dict[str, object]) -> SemanticEvent:
    return SemanticEvent(
        event_type=raw_event["event_type"],
        lifecycle=raw_event["lifecycle"],
        subjects=tuple(
            SemanticSubject(
                entity_id=item["entity_id"],
                role=item["role"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in raw_event["subjects"]
        ),
        facts=tuple(
            SemanticFact(
                name=item["name"],
                raw_value=item["raw_value"],
                numeric_value=item["numeric_value"],
                unit=item["unit"],
                currency=item["currency"],
                period=item["period"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in raw_event["facts"]
        ),
        effective_dates=tuple(
            SemanticEffectiveDate(
                kind=item["kind"],
                value=item["value"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in raw_event["effective_dates"]
        ),
        conditions=tuple(
            SemanticStatement(
                name=item["name"],
                value=item["value"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in raw_event["conditions"]
        ),
        conflicts=tuple(
            SemanticStatement(
                name=item["name"],
                value=item["value"],
                evidence_ids=tuple(item["evidence_ids"]),
            )
            for item in raw_event["conflicts"]
        ),
        missing_required_fields=tuple(raw_event["missing_required_fields"]),
    )


def _schema_error_code(
    payload: object,
    taxonomy: EventTaxonomy,
    errors: tuple[ValidationError, ...],
) -> str:
    if isinstance(payload, dict):
        events = payload.get("events")
        reason = payload.get("no_event_reason")
        raw_evidence = payload.get("evidence")
        if isinstance(raw_evidence, list):
            for evidence in raw_evidence:
                if not isinstance(evidence, dict):
                    continue
                start = evidence.get("start")
                end = evidence.get("end")
                if (
                    type(start) is int
                    and type(end) is int
                    and end <= start
                ):
                    return "semantic_evidence_span_invalid"
        if isinstance(events, list):
            if events and reason is not None:
                return "semantic_no_event_conflict"
            if not events and (not isinstance(reason, str) or not reason):
                return "semantic_no_event_reason_required"

            for raw_event in events:
                if not isinstance(raw_event, dict):
                    continue
                event_type = raw_event.get("event_type")
                lifecycle = raw_event.get("lifecycle")
                if isinstance(event_type, str) and event_type not in taxonomy.event_types:
                    return "semantic_event_type_unknown"
                if isinstance(lifecycle, str) and lifecycle not in VALID_LIFECYCLES:
                    return "semantic_lifecycle_unknown"
                if (
                    isinstance(event_type, str)
                    and event_type in taxonomy.event_types
                    and isinstance(lifecycle, str)
                    and lifecycle in VALID_LIFECYCLES
                    and lifecycle
                    not in taxonomy.event(event_type).allowed_lifecycle
                ):
                    return "semantic_lifecycle_not_allowed"
                if isinstance(event_type, str) and event_type in taxonomy.event_types:
                    declared_facts = taxonomy.event(event_type).declared_facts
                    facts = raw_event.get("facts")
                    if isinstance(facts, list):
                        for fact in facts:
                            if (
                                isinstance(fact, dict)
                                and isinstance(fact.get("name"), str)
                                and fact["name"] not in declared_facts
                            ):
                                return "semantic_fact_name_unknown"
                for collection_name in (
                    "subjects",
                    "facts",
                    "effective_dates",
                    "conditions",
                    "conflicts",
                ):
                    items = raw_event.get(collection_name)
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if (
                            isinstance(item, dict)
                            and isinstance(item.get("evidence_ids"), list)
                            and not item["evidence_ids"]
                        ):
                            return "semantic_evidence_required"

    if any(error.validator == "additionalProperties" for error in errors):
        return "semantic_schema_extra_property"
    return "semantic_schema_invalid"


def _error_location(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return path or "$"


__all__ = [
    "LITE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SemanticContractError",
    "SemanticDocumentResult",
    "SemanticEffectiveDate",
    "SemanticEvidence",
    "SemanticEvent",
    "SemanticFact",
    "SemanticStatement",
    "SemanticSubject",
    "announcement_event_lite_schema",
    "announcement_event_schema",
    "load_semantic_prompt",
    "parse_lite_semantic_document_result",
    "parse_semantic_document_result",
]
