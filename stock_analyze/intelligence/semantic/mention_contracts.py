"""Compact provider-neutral contract for source-grounded event mentions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from jsonschema import Draft202012Validator


MENTION_SCHEMA_VERSION = "announcement-mentions-v1-lite"


class MentionContractError(ValueError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(self.code)


@dataclass(frozen=True)
class MentionEvidence:
    chunk_id: str
    quote: str


@dataclass(frozen=True)
class MentionSubject:
    role: str
    name: str
    evidence: tuple[MentionEvidence, ...]


@dataclass(frozen=True)
class MentionFact:
    name: str
    raw_value: str
    evidence: tuple[MentionEvidence, ...]


@dataclass(frozen=True)
class MentionDate:
    kind: str
    raw_value: str
    evidence: tuple[MentionEvidence, ...]


@dataclass(frozen=True)
class MentionStatus:
    raw_value: str
    evidence: tuple[MentionEvidence, ...]


@dataclass(frozen=True)
class EventMention:
    mention_id: str
    event_type: str
    subjects: tuple[MentionSubject, ...]
    facts: tuple[MentionFact, ...]
    dates: tuple[MentionDate, ...]
    status: MentionStatus | None


@dataclass(frozen=True)
class MentionDocumentResult:
    document_id: int
    mentions: tuple[EventMention, ...]
    no_event_reason: str | None
    schema_version: str = MENTION_SCHEMA_VERSION


def announcement_mention_lite_schema() -> dict[str, object]:
    evidence_array = {
        "type": "array",
        "minItems": 1,
        "items": {"$ref": "#/$defs/evidence"},
    }
    named_value = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "raw_value", "evidence"],
        "properties": {
            "name": {"$ref": "#/$defs/snake_name"},
            "raw_value": {"type": "string", "minLength": 1},
            "evidence": evidence_array,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "document_id",
            "schema_version",
            "mentions",
            "no_event_reason",
        ],
        "properties": {
            "document_id": {"type": "integer", "minimum": 1},
            "schema_version": {"const": MENTION_SCHEMA_VERSION},
            "mentions": {
                "type": "array",
                "items": {"$ref": "#/$defs/mention"},
            },
            "no_event_reason": {"type": ["string", "null"]},
        },
        "oneOf": [
            {
                "properties": {
                    "mentions": {"minItems": 1},
                    "no_event_reason": {"type": "null"},
                }
            },
            {
                "properties": {
                    "mentions": {"maxItems": 0},
                    "no_event_reason": {"type": "string", "minLength": 1},
                }
            },
        ],
        "$defs": {
            "snake_name": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]*$",
                "minLength": 1,
                "maxLength": 64,
            },
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chunk_id", "quote"],
                "properties": {
                    "chunk_id": {"type": "string", "minLength": 1},
                    "quote": {"type": "string", "minLength": 1},
                },
            },
            "subject": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "name", "evidence"],
                "properties": {
                    "role": {"$ref": "#/$defs/snake_name"},
                    "name": {"type": "string", "minLength": 1},
                    "evidence": evidence_array,
                },
            },
            "fact": named_value,
            "date": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "raw_value", "evidence"],
                "properties": {
                    "kind": {"$ref": "#/$defs/snake_name"},
                    "raw_value": {"type": "string", "minLength": 1},
                    "evidence": evidence_array,
                },
            },
            "status": {
                "type": "object",
                "additionalProperties": False,
                "required": ["raw_value", "evidence"],
                "properties": {
                    "raw_value": {"type": "string", "minLength": 1},
                    "evidence": evidence_array,
                },
            },
            "mention": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "mention_id",
                    "event_type",
                    "subjects",
                    "facts",
                    "dates",
                    "status",
                ],
                "properties": {
                    "mention_id": {"type": "string", "minLength": 1},
                    "event_type": {"$ref": "#/$defs/snake_name"},
                    "subjects": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"$ref": "#/$defs/subject"},
                    },
                    "facts": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/fact"},
                    },
                    "dates": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/date"},
                    },
                    "status": {
                        "oneOf": [
                            {"$ref": "#/$defs/status"},
                            {"type": "null"},
                        ]
                    },
                },
            },
        },
    }


def parse_mention_document_result(payload: object) -> MentionDocumentResult:
    schema = announcement_mention_lite_schema()
    errors = tuple(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        first = sorted(
            errors,
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )[0]
        detail = ".".join(str(part) for part in first.absolute_path)
        raise MentionContractError("mention_schema_invalid", detail=detail)
    assert isinstance(payload, Mapping)
    return MentionDocumentResult(
        document_id=int(payload["document_id"]),
        mentions=tuple(_parse_mention(item) for item in payload["mentions"]),
        no_event_reason=(
            str(payload["no_event_reason"])
            if payload["no_event_reason"] is not None
            else None
        ),
    )


def _parse_mention(payload: Mapping[str, object]) -> EventMention:
    return EventMention(
        mention_id=str(payload["mention_id"]),
        event_type=str(payload["event_type"]),
        subjects=tuple(
            MentionSubject(
                role=str(item["role"]),
                name=str(item["name"]),
                evidence=_parse_evidence(item["evidence"]),
            )
            for item in payload["subjects"]
        ),
        facts=tuple(
            MentionFact(
                name=str(item["name"]),
                raw_value=str(item["raw_value"]),
                evidence=_parse_evidence(item["evidence"]),
            )
            for item in payload["facts"]
        ),
        dates=tuple(
            MentionDate(
                kind=str(item["kind"]),
                raw_value=str(item["raw_value"]),
                evidence=_parse_evidence(item["evidence"]),
            )
            for item in payload["dates"]
        ),
        status=(
            MentionStatus(
                raw_value=str(payload["status"]["raw_value"]),
                evidence=_parse_evidence(payload["status"]["evidence"]),
            )
            if payload["status"] is not None
            else None
        ),
    )


def _parse_evidence(payload: object) -> tuple[MentionEvidence, ...]:
    return tuple(
        MentionEvidence(
            chunk_id=str(item["chunk_id"]),
            quote=str(item["quote"]),
        )
        for item in payload
    )


__all__ = [
    "MENTION_SCHEMA_VERSION",
    "EventMention",
    "MentionContractError",
    "MentionDate",
    "MentionDocumentResult",
    "MentionEvidence",
    "MentionFact",
    "MentionStatus",
    "MentionSubject",
    "announcement_mention_lite_schema",
    "parse_mention_document_result",
]
