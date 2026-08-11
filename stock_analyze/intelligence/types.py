"""Typed records shared by intelligence sources, storage, and factors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_iso(value: str | datetime | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SourceDocument:
    source: str
    source_id: str
    title: str
    published_at: str
    first_seen_at: str
    effective_at: str
    source_url: str
    content: bytes = b""
    mime_type: str = "text/html"
    revised_at: str | None = None
    revision_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    document_id: int
    event_type: str
    direction: float
    strength: float
    confidence: float
    novelty: float
    horizon_days: int
    published_at: str
    effective_at: str
    evidence: str
    extraction_method: str = "rules-v1"
    entities: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    valid_to: str | None = None
    source_class: str = "unknown"
    source_credibility: float = 0.0
    document_fingerprint: str = ""
    event_fingerprint: str = ""
    tradable: bool = False
