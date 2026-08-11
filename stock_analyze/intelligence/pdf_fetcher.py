"""Secure PDF acquisition and immutable artifact registration."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from .blob_store import (
    BlobConflictError,
    BlobStore,
    BlobStoreError,
    pdf_object_key,
)
from .store import DocumentArtifactConflict, IntelligenceStore


DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class PdfFetchError(RuntimeError):
    retryable = False


class RetryablePdfFetchError(PdfFetchError):
    retryable = True


class TerminalPdfFetchError(PdfFetchError):
    retryable = False


class PdfArtifactConflict(TerminalPdfFetchError):
    pass


@dataclass(frozen=True)
class DownloadedPdf:
    path: Path
    sha256: str
    byte_size: int
    mime_type: str

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> DownloadedPdf:
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.cleanup()


Resolver = Callable[[str, int], Iterable[str]]


class _PinnedNetworkBackend:
    """Resolve outside the transport, then connect only to approved IPs."""

    def __init__(self, delegate: object | None = None) -> None:
        self._delegate = delegate
        self._pins: dict[str, tuple[str, ...]] = {}
        self._lock = threading.RLock()

    def pin(self, host: str, addresses: Iterable[str]) -> None:
        normalized_host = _normalized_hostname(host)
        normalized_addresses = tuple(
            sorted({str(ipaddress.ip_address(value)) for value in addresses})
        )
        if (
            not normalized_addresses
            or any(
                not ipaddress.ip_address(value).is_global
                for value in normalized_addresses
            )
        ):
            raise TerminalPdfFetchError(
                "pdf_fetch_private_target"
            )
        with self._lock:
            self._pins[normalized_host] = normalized_addresses

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        normalized_host = _normalized_hostname(host)
        with self._lock:
            addresses = self._pins.get(normalized_host, ())
        if not addresses:
            raise OSError("pdf_fetch_dns_pin_missing")
        if self._delegate is None:
            raise OSError("pdf_fetch_network_backend_missing")
        deadline = (
            time.monotonic() + float(timeout)
            if timeout is not None
            else None
        )
        last_error: Exception | None = None
        for address in addresses:
            attempt_timeout = timeout
            if deadline is not None:
                attempt_timeout = deadline - time.monotonic()
                if attempt_timeout <= 0:
                    break
            try:
                return self._delegate.connect_tcp(
                    address,
                    port,
                    timeout=attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise OSError("pdf_fetch_connect_timeout")
        raise last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options=None,
    ):
        raise OSError("pdf_fetch_unix_socket_forbidden")


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise RetryablePdfFetchError(
            "pdf_fetch_dns_failed"
        ) from None
    return tuple(
        sorted({str(row[4][0]) for row in rows if row[4]})
    )


def _normalized_hostname(value: str) -> str:
    try:
        return str(value).strip().rstrip(".").encode("idna").decode(
            "ascii"
        ).casefold()
    except UnicodeError:
        raise TerminalPdfFetchError(
            "pdf_fetch_host_invalid"
        ) from None


class SecurePdfDownloader:
    """Bounded HTTP downloader that revalidates every redirect target."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        resolver: Resolver | None = None,
        client: httpx.Client | None = None,
        max_bytes: int = DEFAULT_MAX_PDF_BYTES,
        max_attempts: int = 3,
        max_redirects: int = 5,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 30.0,
        total_timeout_seconds: float = 60.0,
        retry_sleep: Callable[[float], None] = time.sleep,
        temp_root: str | Path | None = None,
    ) -> None:
        hosts = {
            _normalized_hostname(host)
            for host in allowed_hosts
            if str(host).strip()
        }
        if not hosts:
            raise ValueError("pdf_fetch_allowlist_empty")
        self.allowed_hosts = frozenset(hosts)
        self.resolver = resolver or _default_resolver
        self.max_bytes = int(max_bytes)
        self.max_attempts = max(1, int(max_attempts))
        self.max_redirects = max(0, int(max_redirects))
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.read_timeout_seconds = float(read_timeout_seconds)
        self.total_timeout_seconds = float(total_timeout_seconds)
        if (
            self.max_bytes <= 0
            or self.connect_timeout_seconds <= 0
            or self.read_timeout_seconds <= 0
            or self.total_timeout_seconds <= 0
        ):
            raise ValueError("pdf_fetch_limits_invalid")
        self.retry_sleep = retry_sleep
        self.temp_root = (
            Path(temp_root).expanduser().resolve()
            if temp_root is not None
            else None
        )
        if self.temp_root is not None:
            self.temp_root.mkdir(parents=True, exist_ok=True)
        self._pinned_backend: _PinnedNetworkBackend | None = None
        if client is None:
            transport = httpx.HTTPTransport(
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=0,
                ),
            )
            pool = getattr(transport, "_pool", None)
            if pool is None or not hasattr(pool, "_network_backend"):
                transport.close()
                raise RuntimeError(
                    "pdf_fetch_pinning_unavailable"
                )
            self._pinned_backend = _PinnedNetworkBackend(
                delegate=pool._network_backend
            )
            pool._network_backend = self._pinned_backend
            self.client = httpx.Client(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            )
        else:
            self.client = client
        if self.client.follow_redirects:
            raise ValueError("pdf_fetch_automatic_redirects_forbidden")

    def _validate_url(
        self,
        url: str,
    ) -> tuple[str, frozenset[str]]:
        try:
            parsed = urlsplit(str(url))
            port = parsed.port
        except ValueError:
            raise TerminalPdfFetchError(
                "pdf_fetch_url_invalid"
            ) from None
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
        ):
            raise TerminalPdfFetchError(
                "pdf_fetch_https_required"
            )
        host = _normalized_hostname(parsed.hostname)
        if host not in self.allowed_hosts:
            raise TerminalPdfFetchError(
                "pdf_fetch_host_not_allowed"
            )
        literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = (literal,)
        else:
            try:
                resolved = tuple(self.resolver(host, 443))
            except RetryablePdfFetchError:
                raise
            except (OSError, socket.gaierror):
                raise RetryablePdfFetchError(
                    "pdf_fetch_dns_failed"
                ) from None
            if not resolved:
                raise RetryablePdfFetchError(
                    "pdf_fetch_dns_empty"
                )
            try:
                addresses = tuple(
                    ipaddress.ip_address(str(address))
                    for address in resolved
                )
            except ValueError:
                raise RetryablePdfFetchError(
                    "pdf_fetch_dns_invalid"
                ) from None
        if any(not address.is_global for address in addresses):
            raise TerminalPdfFetchError(
                "pdf_fetch_private_target"
            )
        return (
            str(url),
            frozenset(str(address) for address in addresses),
        )

    @staticmethod
    def _validate_connected_peer(
        response: httpx.Response,
        expected_addresses: frozenset[str],
    ) -> None:
        stream = response.extensions.get("network_stream")
        if stream is None:
            return
        try:
            server_address = stream.get_extra_info("server_addr")
            peer_text = str(server_address[0])
            peer = ipaddress.ip_address(peer_text)
        except (AttributeError, IndexError, TypeError, ValueError):
            raise TerminalPdfFetchError(
                "pdf_fetch_peer_unverifiable"
            ) from None
        if not peer.is_global:
            raise TerminalPdfFetchError(
                "pdf_fetch_private_target"
            )
        if str(peer) not in expected_addresses:
            raise TerminalPdfFetchError(
                "pdf_fetch_dns_rebind"
            )

    @staticmethod
    def _expected_hash(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().casefold()
        if (
            len(normalized) != 64
            or any(
                character not in "0123456789abcdef"
                for character in normalized
            )
        ):
            raise TerminalPdfFetchError(
                "pdf_fetch_expected_hash_invalid"
            )
        return normalized

    def _timeout(self, deadline: float) -> httpx.Timeout:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RetryablePdfFetchError(
                "pdf_fetch_total_timeout"
            )
        return httpx.Timeout(
            connect=min(self.connect_timeout_seconds, remaining),
            read=min(self.read_timeout_seconds, remaining),
            write=min(self.read_timeout_seconds, remaining),
            pool=min(self.connect_timeout_seconds, remaining),
        )

    @staticmethod
    def _content_length(response: httpx.Response) -> int | None:
        raw = response.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise TerminalPdfFetchError(
                "pdf_fetch_content_length_invalid"
            ) from None
        if value < 0:
            raise TerminalPdfFetchError(
                "pdf_fetch_content_length_invalid"
            )
        return value

    @staticmethod
    def _response_mime(response: httpx.Response) -> str:
        return (
            response.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .casefold()
        )

    def _temporary_path(self) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".intelligence-pdf-",
            suffix=".pdf",
            dir=self.temp_root,
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        return path

    def _stream_response(
        self,
        response: httpx.Response,
        *,
        expected_sha256: str | None,
        deadline: float,
    ) -> DownloadedPdf:
        declared_size = self._content_length(response)
        if declared_size is not None and declared_size > self.max_bytes:
            raise TerminalPdfFetchError("pdf_fetch_too_large")
        content_encoding = response.headers.get(
            "Content-Encoding",
            "",
        ).strip().casefold()
        if content_encoding not in {"", "identity"}:
            raise TerminalPdfFetchError(
                "pdf_fetch_content_encoding_unsupported"
            )
        mime_type = self._response_mime(response)
        path = self._temporary_path()
        digest = hashlib.sha256()
        byte_size = 0
        magic = bytearray()
        try:
            with path.open("wb") as handle:
                try:
                    for chunk in response.iter_raw():
                        if time.monotonic() > deadline:
                            raise RetryablePdfFetchError(
                                "pdf_fetch_total_timeout"
                            )
                        if not chunk:
                            continue
                        byte_size += len(chunk)
                        if byte_size > self.max_bytes:
                            raise TerminalPdfFetchError(
                                "pdf_fetch_too_large"
                            )
                        if len(magic) < 5:
                            magic.extend(chunk[: 5 - len(magic)])
                        digest.update(chunk)
                        handle.write(chunk)
                except httpx.RemoteProtocolError:
                    if declared_size is not None:
                        raise TerminalPdfFetchError(
                            "pdf_fetch_content_length_mismatch"
                        ) from None
                    raise RetryablePdfFetchError(
                        "pdf_fetch_protocol_error"
                    ) from None
            if (
                declared_size is not None
                and byte_size != declared_size
            ):
                raise TerminalPdfFetchError(
                    "pdf_fetch_content_length_mismatch"
                )
            if (
                mime_type != "application/pdf"
                and bytes(magic) != b"%PDF-"
            ):
                raise TerminalPdfFetchError("pdf_fetch_not_pdf")
            actual_hash = digest.hexdigest()
            if (
                expected_sha256 is not None
                and actual_hash != expected_sha256
            ):
                raise TerminalPdfFetchError(
                    "pdf_fetch_hash_mismatch"
                )
            return DownloadedPdf(
                path=path,
                sha256=actual_hash,
                byte_size=byte_size,
                mime_type="application/pdf",
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _download_once(
        self,
        url: str,
        *,
        expected_sha256: str | None,
        deadline: float,
    ) -> DownloadedPdf:
        current_url = str(url)
        redirects = 0
        while True:
            current_url, expected_addresses = self._validate_url(
                current_url
            )
            if self._pinned_backend is not None:
                parsed = urlsplit(current_url)
                assert parsed.hostname is not None
                self._pinned_backend.pin(
                    parsed.hostname,
                    expected_addresses,
                )
            timeout = self._timeout(deadline)
            try:
                with self.client.stream(
                    "GET",
                    current_url,
                    timeout=timeout,
                ) as response:
                    self._validate_connected_peer(
                        response,
                        expected_addresses,
                    )
                    status = int(response.status_code)
                    if status in _REDIRECT_STATUSES:
                        location = response.headers.get(
                            "Location",
                            "",
                        ).strip()
                        if not location:
                            raise TerminalPdfFetchError(
                                "pdf_fetch_redirect_missing_location"
                            )
                        redirects += 1
                        if redirects > self.max_redirects:
                            raise TerminalPdfFetchError(
                                "pdf_fetch_too_many_redirects"
                            )
                        target = urljoin(current_url, location)
                        self._validate_url(target)
                        current_url = target
                        continue
                    if status == 429 or 500 <= status <= 599:
                        raise RetryablePdfFetchError(
                            f"pdf_fetch_http_{status}"
                        )
                    if 400 <= status <= 499:
                        raise TerminalPdfFetchError(
                            f"pdf_fetch_http_{status}"
                        )
                    if status != 200:
                        raise TerminalPdfFetchError(
                            f"pdf_fetch_http_{status}"
                        )
                    return self._stream_response(
                        response,
                        expected_sha256=expected_sha256,
                        deadline=deadline,
                    )
            except PdfFetchError:
                raise
            except httpx.TimeoutException:
                raise RetryablePdfFetchError(
                    "pdf_fetch_timeout"
                ) from None
            except httpx.NetworkError:
                raise RetryablePdfFetchError(
                    "pdf_fetch_network_error"
                ) from None

    def fetch(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadedPdf:
        expected = self._expected_hash(expected_sha256)
        deadline = time.monotonic() + self.total_timeout_seconds
        last_error: RetryablePdfFetchError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._download_once(
                    url,
                    expected_sha256=expected,
                    deadline=deadline,
                )
            except RetryablePdfFetchError as exc:
                last_error = exc
                if (
                    attempt >= self.max_attempts
                    or time.monotonic() >= deadline
                ):
                    raise
                self.retry_sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
        assert last_error is not None
        raise last_error


class PdfDownloader(Protocol):
    def fetch(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadedPdf:
        ...


class AnnouncementPdfFetcher:
    """Fetch one document's PDF and atomically activate one artifact row."""

    def __init__(
        self,
        store: IntelligenceStore,
        blob_store: BlobStore,
        downloader: PdfDownloader,
    ) -> None:
        self.store = store
        self.blob_store = blob_store
        self.downloader = downloader

    @staticmethod
    def _metadata(document: dict[str, object]) -> dict[str, object]:
        try:
            value = json.loads(str(document.get("metadata_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise TerminalPdfFetchError(
                "pdf_fetch_document_metadata_invalid"
            ) from None
        if not isinstance(value, dict):
            raise TerminalPdfFetchError(
                "pdf_fetch_document_metadata_invalid"
            )
        return value

    @staticmethod
    def _expected_sha256(metadata: dict[str, object]) -> str | None:
        for key in (
            "pdf_sha256",
            "expected_pdf_sha256",
            "source_pdf_sha256",
        ):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    def _verified_current(
        self,
        document_id: int,
    ) -> dict[str, object] | None:
        try:
            current = self.store.current_pdf_artifact(document_id)
        except DocumentArtifactConflict as exc:
            raise PdfArtifactConflict(str(exc)) from None
        if current is None:
            return None
        uri = str(current["storage_uri"])
        if not self.blob_store.exists(uri):
            return None
        payload = self.blob_store.read(uri)
        actual_hash = hashlib.sha256(payload).hexdigest()
        if (
            actual_hash != str(current["content_hash"])
            or len(payload) != int(current["byte_size"])
        ):
            raise PdfArtifactConflict(
                "intelligence_pdf_artifact_conflict:"
                "object_db_mismatch"
            )
        return current

    def _record_fetch_failure(
        self,
        document_id: int,
        *,
        status: str,
        error: str,
    ) -> None:
        self.store.record_pdf_artifact_failure(
            document_id=document_id,
            status=status,
            error=error,
        )

    def fetch(self, document_id: int) -> dict[str, object]:
        document = self.store.document_for_pdf_fetch(document_id)
        if document is None:
            raise TerminalPdfFetchError(
                "pdf_fetch_document_not_found"
            )
        current = self._verified_current(document_id)
        if current is not None:
            return current
        source_url = str(document.get("source_url") or "").strip()
        if not source_url:
            error = TerminalPdfFetchError(
                "pdf_fetch_source_url_missing"
            )
            self._record_fetch_failure(
                document_id,
                status="failed_terminal",
                error=str(error),
            )
            raise error
        try:
            metadata = self._metadata(document)
            expected_sha256 = self._expected_sha256(metadata)
            with self.downloader.fetch(
                source_url,
                expected_sha256=expected_sha256,
            ) as downloaded:
                payload = downloaded.path.read_bytes()
                actual_hash = hashlib.sha256(payload).hexdigest()
                if (
                    actual_hash != downloaded.sha256
                    or len(payload) != downloaded.byte_size
                ):
                    raise TerminalPdfFetchError(
                        "pdf_fetch_temporary_file_mismatch"
                    )
                key = pdf_object_key(downloaded.sha256)
                uri = self.blob_store.put_if_absent(
                    key,
                    payload,
                    downloaded.mime_type,
                )
                try:
                    return self.store.commit_pdf_artifact(
                        document_id=document_id,
                        content_hash=downloaded.sha256,
                        storage_uri=uri,
                        mime_type=downloaded.mime_type,
                        byte_size=downloaded.byte_size,
                    )
                except DocumentArtifactConflict as exc:
                    raise PdfArtifactConflict(str(exc)) from None
        except PdfArtifactConflict:
            raise
        except RetryablePdfFetchError as exc:
            self._record_fetch_failure(
                document_id,
                status="failed_retryable",
                error=str(exc),
            )
            raise
        except TerminalPdfFetchError as exc:
            self._record_fetch_failure(
                document_id,
                status="failed_terminal",
                error=str(exc),
            )
            raise
        except BlobConflictError as exc:
            error = PdfArtifactConflict(str(exc))
            self._record_fetch_failure(
                document_id,
                status="failed_terminal",
                error=str(error),
            )
            raise error from None
        except BlobStoreError as exc:
            self._record_fetch_failure(
                document_id,
                status="failed_retryable",
                error=str(exc),
            )
            raise
