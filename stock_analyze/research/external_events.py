"""Optional news, announcement, and policy source contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: str
    message: str = ""
    rows: int = 0


@dataclass(frozen=True)
class EventFetchResult:
    events: tuple[dict[str, Any], ...]
    health: SourceHealth


class ExternalEventAdapter(Protocol):
    source: str

    def fetch(self, start: datetime, end: datetime) -> EventFetchResult: ...


class DisabledEventAdapter:
    def __init__(self, source: str) -> None:
        self.source = source

    def fetch(self, start: datetime, end: datetime) -> EventFetchResult:
        del start, end
        return EventFetchResult((), SourceHealth(self.source, "source_unavailable"))


class TushareEventAdapter:
    """Explicitly gated adapter; disabled means no endpoint lookup or call."""

    def __init__(self, source: str, client: Any, *, endpoint: str, enabled: bool = False) -> None:
        self.source = source
        self.client = client
        self.endpoint = endpoint
        self.enabled = enabled

    def fetch(self, start: datetime, end: datetime) -> EventFetchResult:
        if not self.enabled:
            return EventFetchResult((), SourceHealth(self.source, "source_unavailable"))
        try:
            frame = getattr(self.client, self.endpoint)(
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:  # noqa: BLE001 - source health is the contract
            return EventFetchResult((), SourceHealth(self.source, "source_error", str(exc)[:200]))
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return EventFetchResult((), SourceHealth(self.source, "empty", rows=0))
        events = tuple(
            {
                **{key: value for key, value in row.items() if pd.notna(value)},
                "source": self.source,
                "observed_at": end.astimezone().isoformat(timespec="seconds"),
            }
            for row in frame.to_dict(orient="records")
        )
        return EventFetchResult(events, SourceHealth(self.source, "ok", rows=len(events)))


def default_external_adapters() -> tuple[ExternalEventAdapter, ...]:
    return tuple(DisabledEventAdapter(source) for source in ("news", "announcement", "policy"))
