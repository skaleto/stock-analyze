from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..types import SourceDocument


@dataclass(frozen=True)
class FetchBatch:
    documents: tuple[SourceDocument, ...]
    cursor: str
    complete: bool = True
    warnings: tuple[str, ...] = ()
    metrics: dict[str, int] = field(default_factory=dict)


class SourceAdapter(Protocol):
    source: str

    def fetch_since(self, cursor: str, until: str) -> FetchBatch: ...


@dataclass(frozen=True)
class UnavailableSourceAdapter:
    """Observable placeholder for a configured source that is not usable yet."""

    source: str
    reason: str

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        return FetchBatch(
            (),
            cursor,
            complete=False,
            warnings=(f"source_unavailable:{self.reason}",),
        )
