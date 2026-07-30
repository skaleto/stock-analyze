"""HTTP representation and short-lived response cache for dashboard JSON."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


class InvalidDashboardQuery(ValueError):
    """A dashboard query parameter could not be parsed safely."""


class DashboardResourceNotFound(LookupError):
    """A bounded dashboard detail resource does not exist."""


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


@dataclass
class _KeyLock:
    lock: threading.Lock
    users: int = 0


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic_value(item)
            for key, item in value.items()
            if key != "generated_at"
        }
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    return value


class DashboardResponseCache:
    """Bounded JSON cache with stale serving and background refresh."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 15.0,
        stale_seconds: float = 300.0,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds < 0 or stale_seconds < 0:
            raise ValueError("cache lifetimes must be non-negative")
        self.ttl_seconds = float(ttl_seconds)
        self.stale_seconds = float(stale_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._entries: OrderedDict[str, CachedJSON] = OrderedDict()
        self._key_locks: dict[str, _KeyLock] = {}
        self._lock = threading.RLock()

    def _cached_entry(
        self,
        key: str,
        now: float,
    ) -> tuple[CachedJSON | None, str]:
        cached = self._entries.get(key)
        if cached is None:
            return None, "missing"
        age = now - cached.created_at
        if age < self.ttl_seconds:
            self._entries.move_to_end(key)
            return cached, "fresh"
        if age < self.ttl_seconds + self.stale_seconds:
            self._entries.move_to_end(key)
            return cached, "stale"
        if self._entries.get(key) is cached:
            del self._entries[key]
        return None, "expired"

    def _store(self, key: str, entry: CachedJSON) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def _build_entry(self, builder: Callable[[], dict[str, Any]]) -> CachedJSON:
        started = time.perf_counter()
        payload = builder()
        identity = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        semantic_identity = json.dumps(
            _semantic_value(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return CachedJSON(
            identity=identity,
            gzip_body=gzip.compress(identity, compresslevel=5, mtime=0),
            etag=f'"{hashlib.sha256(semantic_identity).hexdigest()}"',
            created_at=self._clock(),
            build_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _refresh_stale(
        self,
        key: str,
        key_lock: _KeyLock,
        builder: Callable[[], dict[str, Any]],
    ) -> None:
        try:
            with key_lock.lock:
                entry = self._build_entry(builder)
                with self._lock:
                    self._store(key, entry)
        except Exception:
            # Keep serving the prior snapshot; a later stale request retries.
            pass
        finally:
            with self._lock:
                key_lock.users -= 1
                if key_lock.users == 0 and self._key_locks.get(key) is key_lock:
                    del self._key_locks[key]

    def get_or_build(
        self,
        key: str,
        builder: Callable[[], dict[str, Any]],
    ) -> tuple[CachedJSON, str]:
        with self._lock:
            cached, state = self._cached_entry(key, self._clock())
            if state == "fresh":
                return cached, "hit"
            if state == "stale":
                key_lock = self._key_locks.get(key)
                if key_lock is None:
                    key_lock = _KeyLock(threading.Lock(), users=1)
                    self._key_locks[key] = key_lock
                    threading.Thread(
                        target=self._refresh_stale,
                        args=(key, key_lock, builder),
                        daemon=True,
                        name=f"dashboard-cache-refresh-{key[:24]}",
                    ).start()
                return cached, "stale"
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = _KeyLock(threading.Lock())
                self._key_locks[key] = key_lock
            key_lock.users += 1
        try:
            with key_lock.lock:
                with self._lock:
                    cached, state = self._cached_entry(key, self._clock())
                    if state == "fresh":
                        return cached, "hit"
                entry = self._build_entry(builder)
                with self._lock:
                    self._store(key, entry)
                return entry, "miss"
        finally:
            with self._lock:
                key_lock.users -= 1
                if key_lock.users == 0 and self._key_locks.get(key) is key_lock:
                    del self._key_locks[key]

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


def _etag_matches(value: str | None, etag: str) -> bool:
    if not value:
        return False
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate[:2].lower() == "w/":
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
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
        "Cache-Control": "private, max-age=15, stale-while-revalidate=86400",
        "ETag": entry.etag,
        "Vary": "Accept-Encoding",
        "X-Cache": cache_status.upper(),
        "X-Request-ID": request_id,
        "X-Response-Time-Ms": f"{elapsed_ms:.1f}",
        "Server-Timing": f"app;dur={elapsed_ms:.1f}, build;dur={entry.build_ms:.1f}",
    }
    if _etag_matches(if_none_match, entry.etag):
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
