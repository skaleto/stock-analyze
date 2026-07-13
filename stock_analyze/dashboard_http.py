"""HTTP representation and short-lived response cache for dashboard JSON."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class InvalidDashboardQuery(ValueError):
    """A dashboard query parameter could not be parsed safely."""


@dataclass(frozen=True)
class CachedJSON:
    identity: bytes
    gzip_body: bytes
    etag: str
    created_at: float
    build_ms: float


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class DashboardResponseCache:
    """Small lock-protected TTL cache that also prevents request stampedes."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: dict[str, CachedJSON] = {}
        self._key_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    def get_or_build(
        self,
        key: str,
        builder: Callable[[], dict[str, Any]],
    ) -> tuple[CachedJSON, str]:
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and now - cached.created_at < self.ttl_seconds:
                return cached, "hit"
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            now = self._clock()
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None and now - cached.created_at < self.ttl_seconds:
                    return cached, "hit"
            started = time.perf_counter()
            payload = builder()
            identity = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            entry = CachedJSON(
                identity=identity,
                gzip_body=gzip.compress(identity, compresslevel=5, mtime=0),
                etag=f'"{hashlib.sha256(identity).hexdigest()}"',
                created_at=now,
                build_ms=(time.perf_counter() - started) * 1000.0,
            )
            with self._lock:
                self._entries[key] = entry
            return entry, "miss"

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._key_locks.clear()


def _accepts_gzip(value: str) -> bool:
    for token in value.split(","):
        parts = [part.strip() for part in token.split(";")]
        if not parts or parts[0].lower() != "gzip":
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, raw = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(raw)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def build_http_response(
    entry: CachedJSON,
    *,
    accept_encoding: str,
    if_none_match: str | None,
    cache_status: str,
    request_id: str,
    elapsed_ms: float,
) -> HTTPResponse:
    """Select a representation and attach cache/observability headers."""

    common = {
        "Cache-Control": "private, max-age=15, stale-while-revalidate=45",
        "ETag": entry.etag,
        "Vary": "Accept-Encoding",
        "X-Cache": cache_status.upper(),
        "X-Request-ID": request_id,
        "X-Response-Time-Ms": f"{elapsed_ms:.1f}",
        "Server-Timing": f"app;dur={elapsed_ms:.1f}, build;dur={entry.build_ms:.1f}",
    }
    if if_none_match == entry.etag:
        return HTTPResponse(
            status=304,
            headers={**common, "Content-Length": "0"},
            body=b"",
        )
    use_gzip = _accepts_gzip(accept_encoding) and len(entry.identity) >= 256
    body = entry.gzip_body if use_gzip else entry.identity
    headers = {
        **common,
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
    }
    if use_gzip:
        headers["Content-Encoding"] = "gzip"
    return HTTPResponse(status=200, headers=headers, body=body)
