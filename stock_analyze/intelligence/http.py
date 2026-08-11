"""Bounded HTTP client with host allowlisting and conditional caching."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class IntelligenceHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        cache_dir: str | Path,
        min_interval_seconds: float = 0.8,
        timeout_seconds: float = 20.0,
        retries: int = 2,
    ) -> None:
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.timeout_seconds = timeout_seconds
        self.retries = max(0, retries)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "stock-analyze-intelligence/1.0 (research; low-rate)"})
        self._last_request = 0.0

    def get(self, url: str) -> requests.Response:
        host = (urlparse(url).hostname or "").lower()
        if host not in self.allowed_hosts:
            raise ValueError(f"intelligence_host_not_allowed:{host}")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        metadata_path = self.cache_dir / f"{key}.headers"
        headers: dict[str, str] = {}
        if metadata_path.exists():
            for line in metadata_path.read_text(encoding="utf-8").splitlines():
                name, _, value = line.partition(":")
                if name in {"ETag", "Last-Modified"} and value:
                    headers["If-None-Match" if name == "ETag" else "If-Modified-Since"] = value.strip()
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            delay = self.min_interval_seconds - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout_seconds)
                self._last_request = time.monotonic()
                response.raise_for_status()
                metadata_path.write_text(
                    "\n".join(
                        f"{name}:{response.headers[name]}"
                        for name in ("ETag", "Last-Modified")
                        if response.headers.get(name)
                    ),
                    encoding="utf-8",
                )
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"intelligence_http_failed:{host}:{type(last_error).__name__}") from last_error
