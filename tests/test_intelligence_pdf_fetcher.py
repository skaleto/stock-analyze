from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import httpx

from stock_analyze.intelligence import IntelligenceStore, SourceDocument
from stock_analyze.intelligence.blob_store import (
    BlobStoreError,
    LocalBlobStore,
)
from stock_analyze.intelligence.pdf_fetcher import (
    AnnouncementPdfFetcher,
    DownloadedPdf,
    PdfArtifactConflict,
    RetryablePdfFetchError,
    SecurePdfDownloader,
    TerminalPdfFetchError,
    _PinnedNetworkBackend,
)


PDF = b"%PDF-1.7\nsecure artifact\n%%EOF\n"
OTHER_PDF = b"%PDF-1.7\nother artifact\n%%EOF\n"
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


class FakeNetworkStream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, name: str):
        if name == "server_addr":
            return (self.address, 443)
        return None


def streamed_response(
    request: httpx.Request,
    payload: bytes = PDF,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    chunks: list[bytes] | None = None,
    peer_address: str | None = None,
) -> httpx.Response:
    extensions = {}
    if peer_address is not None:
        extensions["network_stream"] = FakeNetworkStream(peer_address)
    return httpx.Response(
        status,
        headers=headers,
        stream=ChunkStream(chunks or [payload]),
        request=request,
        extensions=extensions,
    )


def public_resolver(host: str, _port: int) -> list[str]:
    del host
    return [PUBLIC_V4]


class SecurePdfDownloaderTest(unittest.TestCase):
    def test_default_network_backend_connects_to_prevalidated_ip(
        self,
    ) -> None:
        calls: list[str] = []

        class Delegate:
            def connect_tcp(
                self,
                host: str,
                port: int,
                timeout=None,
                local_address=None,
                socket_options=None,
            ):
                del port, timeout, local_address, socket_options
                calls.append(host)
                return object()

        backend = _PinnedNetworkBackend(delegate=Delegate())
        backend.pin("allowed.test", (PUBLIC_V4,))

        backend.connect_tcp("allowed.test", 443)

        self.assertEqual(calls, [PUBLIC_V4])

    def test_default_network_backend_fails_closed_without_pin(self) -> None:
        backend = _PinnedNetworkBackend(delegate=mock.Mock())

        with self.assertRaisesRegex(
            OSError,
            "pdf_fetch_dns_pin_missing",
        ):
            backend.connect_tcp("allowed.test", 443)

    def make_downloader(
        self,
        handler,
        *,
        allowed_hosts: tuple[str, ...] = ("allowed.test",),
        resolver=public_resolver,
        max_bytes: int = 1024,
        max_attempts: int = 3,
        temp_root: Path | None = None,
    ) -> tuple[SecurePdfDownloader, list[httpx.Request]]:
        requests: list[httpx.Request] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        client = httpx.Client(
            transport=httpx.MockTransport(recording_handler),
            follow_redirects=False,
        )
        return (
            SecurePdfDownloader(
                allowed_hosts=allowed_hosts,
                resolver=resolver,
                client=client,
                max_bytes=max_bytes,
                max_attempts=max_attempts,
                connect_timeout_seconds=0.1,
                read_timeout_seconds=0.1,
                total_timeout_seconds=1.0,
                retry_sleep=lambda _seconds: None,
                temp_root=temp_root,
            ),
            requests,
        )

    def test_rejects_non_https_userinfo_and_non_default_port(self) -> None:
        downloader, requests = self.make_downloader(
            lambda request: streamed_response(request),
        )
        invalid = (
            "http://allowed.test/a.pdf",
            "https://user:pass@allowed.test/a.pdf",
            "https://allowed.test:8443/a.pdf",
        )

        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(TerminalPdfFetchError):
                    downloader.fetch(url)
        self.assertEqual(requests, [])

    def test_rejects_host_outside_allowlist(self) -> None:
        downloader, requests = self.make_downloader(
            lambda request: streamed_response(request),
        )

        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_host_not_allowed",
        ):
            downloader.fetch("https://evil.test/a.pdf")

        self.assertEqual(requests, [])

    def test_redirect_target_is_revalidated_and_private_target_rejected(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"Location": "https://private.allowed.test/a.pdf"},
                request=request,
            )

        def resolver(host: str, _port: int) -> list[str]:
            if host == "private.allowed.test":
                return ["127.0.0.1"]
            return [PUBLIC_V4]

        downloader, requests = self.make_downloader(
            handler,
            allowed_hosts=("allowed.test", "private.allowed.test"),
            resolver=resolver,
        )

        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_private_target",
        ):
            downloader.fetch("https://allowed.test/start")

        self.assertEqual(len(requests), 1)

    def test_redirect_downgrade_is_rejected_without_second_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"Location": "http://allowed.test/a.pdf"},
                request=request,
            )

        downloader, requests = self.make_downloader(handler)

        with self.assertRaises(TerminalPdfFetchError):
            downloader.fetch("https://allowed.test/start")

        self.assertEqual(len(requests), 1)

    def test_ipv6_loopback_and_mixed_public_private_dns_are_rejected(
        self,
    ) -> None:
        cases = (
            ["::1"],
            [PUBLIC_V6, "10.0.0.8"],
            ["::ffff:127.0.0.1"],
        )
        for addresses in cases:
            with self.subTest(addresses=addresses):
                downloader, requests = self.make_downloader(
                    lambda request: streamed_response(request),
                    resolver=lambda _host, _port, values=addresses: values,
                )
                with self.assertRaisesRegex(
                    TerminalPdfFetchError,
                    "pdf_fetch_private_target",
                ):
                    downloader.fetch("https://allowed.test/a.pdf")
                self.assertEqual(requests, [])

    def test_connected_peer_must_match_prevalidated_dns_addresses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            downloader, requests = self.make_downloader(
                lambda request: streamed_response(
                    request,
                    peer_address="10.0.0.9",
                ),
                temp_root=Path(tmp),
            )

            with self.assertRaisesRegex(
                TerminalPdfFetchError,
                "pdf_fetch_private_target",
            ):
                downloader.fetch("https://allowed.test/a.pdf")

            self.assertEqual(len(requests), 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_dns_resolution_failure_is_retryable(self) -> None:
        def resolver(_host: str, _port: int) -> list[str]:
            raise socket.gaierror("temporary failure")

        downloader, _requests = self.make_downloader(
            lambda request: streamed_response(request),
            resolver=resolver,
        )

        with self.assertRaisesRegex(
            RetryablePdfFetchError,
            "pdf_fetch_dns_failed",
        ):
            downloader.fetch("https://allowed.test/a.pdf")

    def test_content_length_and_streamed_bytes_are_both_bounded(self) -> None:
        too_large_header, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": "2048",
                },
            ),
        )
        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_too_large",
        ):
            too_large_header.fetch("https://allowed.test/a.pdf")

        chunked, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                headers={"Content-Type": "application/pdf"},
                chunks=[b"%PDF-", b"x" * 700, b"y" * 700],
            ),
        )
        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_too_large",
        ):
            chunked.fetch("https://allowed.test/a.pdf")

    def test_wrong_content_length_fails_closed(self) -> None:
        downloader, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Length": str(len(PDF) + 10),
                },
            ),
        )

        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_content_length_mismatch",
        ):
            downloader.fetch("https://allowed.test/a.pdf")

    def test_total_timeout_is_enforced_while_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            downloader, _requests = self.make_downloader(
                lambda request: streamed_response(request),
                max_attempts=1,
                temp_root=Path(tmp),
            )

            with mock.patch(
                "stock_analyze.intelligence.pdf_fetcher.time.monotonic",
                side_effect=[0.0, 0.0, 2.0],
            ):
                with self.assertRaisesRegex(
                    RetryablePdfFetchError,
                    "pdf_fetch_total_timeout",
                ):
                    downloader.fetch("https://allowed.test/a.pdf")

            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_pdf_mime_or_magic_is_required(self) -> None:
        downloader, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                b"not a pdf",
                headers={"Content-Type": "text/plain"},
            ),
        )
        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_not_pdf",
        ):
            downloader.fetch("https://allowed.test/a.pdf")

        magic_only, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                headers={"Content-Type": "application/octet-stream"},
            ),
        )
        downloaded = magic_only.fetch("https://allowed.test/a.pdf")
        try:
            self.assertEqual(downloaded.path.read_bytes(), PDF)
        finally:
            downloaded.cleanup()

    def test_hash_mismatch_is_terminal(self) -> None:
        downloader, _requests = self.make_downloader(
            lambda request: streamed_response(
                request,
                headers={"Content-Type": "application/pdf"},
            ),
        )

        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_hash_mismatch",
        ):
            downloader.fetch(
                "https://allowed.test/a.pdf",
                expected_sha256="0" * 64,
            )

    def test_429_timeout_and_5xx_retry_then_succeed(self) -> None:
        for first in ("timeout", 429, 503):
            attempts = 0

            def handler(
                request: httpx.Request,
                first_result=first,
            ) -> httpx.Response:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    if first_result == "timeout":
                        raise httpx.ReadTimeout(
                            "read timed out",
                            request=request,
                        )
                    return httpx.Response(
                        int(first_result),
                        request=request,
                    )
                return streamed_response(
                    request,
                    headers={"Content-Type": "application/pdf"},
                )

            downloader, _requests = self.make_downloader(handler)
            downloaded = downloader.fetch("https://allowed.test/a.pdf")
            downloaded.cleanup()
            self.assertEqual(attempts, 2)

    def test_network_exception_chain_does_not_expose_url_secrets(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "provider echoed never-leak-this",
                request=request,
            )

        downloader, _requests = self.make_downloader(
            handler,
            max_attempts=1,
        )

        with self.assertRaises(RetryablePdfFetchError) as raised:
            downloader.fetch(
                "https://allowed.test/a.pdf?"
                "token=never-leak-this"
            )

        self.assertNotIn("never-leak-this", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_403_404_and_other_4xx_are_terminal_without_retry(self) -> None:
        for status in (400, 403, 404, 422):
            downloader, requests = self.make_downloader(
                lambda request, value=status: httpx.Response(
                    value,
                    request=request,
                )
            )
            with self.subTest(status=status):
                with self.assertRaises(TerminalPdfFetchError):
                    downloader.fetch("https://allowed.test/a.pdf")
                self.assertEqual(len(requests), 1)

    def test_temporary_files_are_cleaned_by_context_manager_and_on_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloader, _requests = self.make_downloader(
                lambda request: streamed_response(
                    request,
                    headers={"Content-Type": "application/pdf"},
                ),
                temp_root=root,
            )
            with downloader.fetch("https://allowed.test/a.pdf") as result:
                self.assertTrue(result.path.exists())
            self.assertEqual(list(root.iterdir()), [])

            failing, _requests = self.make_downloader(
                lambda request: httpx.Response(404, request=request),
                temp_root=root,
            )
            with self.assertRaises(TerminalPdfFetchError):
                failing.fetch("https://allowed.test/missing.pdf")
            self.assertEqual(list(root.iterdir()), [])


class FakeDownloader:
    def __init__(
        self,
        payload: bytes = PDF,
        *,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.payload = payload
        self.calls = 0
        self.barrier = barrier

    def fetch(
        self,
        _url: str,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadedPdf:
        self.calls += 1
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if (
            expected_sha256 is not None
            and expected_sha256 != hashlib.sha256(self.payload).hexdigest()
        ):
            raise TerminalPdfFetchError("pdf_fetch_hash_mismatch")
        handle = tempfile.NamedTemporaryFile(delete=False)
        path = Path(handle.name)
        try:
            handle.write(self.payload)
        finally:
            handle.close()
        return DownloadedPdf(
            path=path,
            sha256=hashlib.sha256(self.payload).hexdigest(),
            byte_size=len(self.payload),
            mime_type="application/pdf",
        )


class RecordingBlobStore(LocalBlobStore):
    def __init__(self, root: Path, events: list[str]) -> None:
        super().__init__(root, key_prefix="announcements")
        self.events = events
        self.put_calls = 0
        self.fail = False

    def put_if_absent(
        self,
        key: str,
        payload: bytes,
        content_type: str,
    ) -> str:
        self.events.append("oss")
        self.put_calls += 1
        if self.fail:
            raise BlobStoreError("intelligence_oss_error:put")
        return super().put_if_absent(key, payload, content_type)


class WriteValidatedBlobStore(RecordingBlobStore):
    def read(self, uri: str) -> bytes:
        raise AssertionError(
            f"new upload must trust put_if_absent validation: {uri}"
        )


class AnnouncementPdfFetcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = IntelligenceStore(self.root / "db")
        self.blob = RecordingBlobStore(self.root / "blobs", [])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def insert_document(
        self,
        *,
        source_id: str = "announcement-1",
        metadata: dict[str, object] | None = None,
    ) -> int:
        document_id, _inserted = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id=source_id,
                title="Announcement",
                published_at="2026-07-24T01:00:00Z",
                first_seen_at="2026-07-24T01:05:00Z",
                effective_at="2026-07-24T01:05:00Z",
                source_url="https://allowed.test/a.pdf",
                content=b"metadata only",
                mime_type="application/pdf",
                metadata=metadata or {},
            )
        )
        return document_id

    def artifact_rows(self, document_id: int) -> list[dict[str, object]]:
        with self.store.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM document_artifacts
                    WHERE document_id=?
                    ORDER BY created_at, artifact_id
                    """,
                    (document_id,),
                )
            ]

    def test_fetch_uploads_before_short_database_commit(self) -> None:
        document_id = self.insert_document()
        events = self.blob.events
        original = self.store.commit_pdf_artifact

        def commit(**kwargs):
            events.append("db")
            return original(**kwargs)

        with mock.patch.object(
            self.store,
            "commit_pdf_artifact",
            side_effect=commit,
        ):
            result = AnnouncementPdfFetcher(
                self.store,
                self.blob,
                FakeDownloader(),
            ).fetch(document_id)

        self.assertEqual(events, ["oss", "db"])
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(result["content_hash"], hashlib.sha256(PDF).hexdigest())

    def test_new_upload_does_not_download_the_verified_object_again(
        self,
    ) -> None:
        document_id = self.insert_document(source_id="single-pass")
        blob = WriteValidatedBlobStore(
            self.root / "single-pass-blobs",
            [],
        )

        result = AnnouncementPdfFetcher(
            self.store,
            blob,
            FakeDownloader(),
        ).fetch(document_id)

        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(blob.put_calls, 1)

    def test_repeated_fetch_uses_current_verified_artifact_without_http(
        self,
    ) -> None:
        document_id = self.insert_document()
        downloader = FakeDownloader()
        fetcher = AnnouncementPdfFetcher(
            self.store,
            self.blob,
            downloader,
        )

        first = fetcher.fetch(document_id)
        second = fetcher.fetch(document_id)

        self.assertEqual(first["artifact_id"], second["artifact_id"])
        self.assertEqual(downloader.calls, 1)
        self.assertEqual(self.blob.put_calls, 1)

    def test_expected_hash_is_read_from_document_metadata(self) -> None:
        document_id = self.insert_document(
            metadata={"pdf_sha256": "0" * 64},
        )

        with self.assertRaises(TerminalPdfFetchError):
            AnnouncementPdfFetcher(
                self.store,
                self.blob,
                FakeDownloader(),
            ).fetch(document_id)

        [row] = self.artifact_rows(document_id)
        self.assertEqual(row["status"], "failed_terminal")

    def test_oss_failure_records_retryable_state_without_active_artifact(
        self,
    ) -> None:
        document_id = self.insert_document()
        self.blob.fail = True

        with self.assertRaises(BlobStoreError):
            AnnouncementPdfFetcher(
                self.store,
                self.blob,
                FakeDownloader(),
            ).fetch(document_id)

        [row] = self.artifact_rows(document_id)
        self.assertEqual(row["status"], "failed_retryable")
        self.assertEqual(row["storage_uri"], "")

    def test_db_failure_leaves_immutable_object_for_idempotent_retry(
        self,
    ) -> None:
        document_id = self.insert_document()
        original = self.store.commit_pdf_artifact
        attempts = 0

        def flaky_commit(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("database unavailable")
            return original(**kwargs)

        fetcher = AnnouncementPdfFetcher(
            self.store,
            self.blob,
            FakeDownloader(),
        )
        with mock.patch.object(
            self.store,
            "commit_pdf_artifact",
            side_effect=flaky_commit,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "database unavailable",
            ):
                fetcher.fetch(document_id)

        self.assertEqual(len(list((self.root / "blobs").rglob("*.pdf"))), 1)
        result = fetcher.fetch(document_id)
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(len(list((self.root / "blobs").rglob("*.pdf"))), 1)

    def test_two_workers_with_different_payloads_commit_only_one_active_artifact(
        self,
    ) -> None:
        document_id = self.insert_document()
        barrier = threading.Barrier(2)
        outcomes: list[object] = []

        def worker(payload: bytes) -> None:
            try:
                outcomes.append(
                    AnnouncementPdfFetcher(
                        self.store,
                        self.blob,
                        FakeDownloader(payload, barrier=barrier),
                    ).fetch(document_id)
                )
            except Exception as exc:  # captured for deterministic assertions
                outcomes.append(exc)

        threads = [
            threading.Thread(target=worker, args=(PDF,)),
            threading.Thread(target=worker, args=(OTHER_PDF,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            sum(isinstance(item, PdfArtifactConflict) for item in outcomes),
            1,
        )
        rows = self.artifact_rows(document_id)
        active = [
            row
            for row in rows
            if row["status"] in {
                "downloaded",
                "parsed",
                "ocr_required",
                "ocr_failed",
            }
        ]
        self.assertEqual(len(active), 1)

    def test_failure_from_losing_worker_cannot_overwrite_success(self) -> None:
        document_id = self.insert_document()
        success = AnnouncementPdfFetcher(
            self.store,
            self.blob,
            FakeDownloader(),
        ).fetch(document_id)

        self.store.record_pdf_artifact_failure(
            document_id=document_id,
            status="failed_retryable",
            error="late_worker_timeout",
        )

        current = self.store.current_pdf_artifact(document_id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current["artifact_id"], success["artifact_id"])
        self.assertEqual(current["status"], "downloaded")

    def test_document_not_found_is_terminal_without_network(self) -> None:
        downloader = FakeDownloader()

        with self.assertRaisesRegex(
            TerminalPdfFetchError,
            "pdf_fetch_document_not_found",
        ):
            AnnouncementPdfFetcher(
                self.store,
                self.blob,
                downloader,
            ).fetch(999)

        self.assertEqual(downloader.calls, 0)

    def test_error_persistence_does_not_include_url_query_secrets(self) -> None:
        document_id, _inserted = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id="secret-url",
                title="Announcement",
                published_at="2026-07-24T01:00:00Z",
                first_seen_at="2026-07-24T01:05:00Z",
                effective_at="2026-07-24T01:05:00Z",
                source_url=(
                    "https://allowed.test/a.pdf?"
                    "token=never-persist-this"
                ),
                content=b"metadata only",
                mime_type="application/pdf",
            )
        )
        downloader = mock.Mock()
        downloader.fetch.side_effect = RetryablePdfFetchError(
            "pdf_fetch_http_503"
        )

        with self.assertRaises(RetryablePdfFetchError):
            AnnouncementPdfFetcher(
                self.store,
                self.blob,
                downloader,
            ).fetch(document_id)

        serialized = json.dumps(
            self.artifact_rows(document_id),
            ensure_ascii=False,
        )
        self.assertNotIn("never-persist-this", serialized)

    def test_store_rejects_free_form_failure_text_instead_of_scrubbing_it(
        self,
    ) -> None:
        document_id = self.insert_document(source_id="direct-secret")

        self.store.record_pdf_artifact_failure(
            document_id=document_id,
            status="failed_retryable",
            error=(
                "https://allowed.test/a.pdf?"
                "token=neverpersistthis"
            ),
        )

        serialized = json.dumps(
            self.artifact_rows(document_id),
            ensure_ascii=False,
        )
        self.assertNotIn("neverpersistthis", serialized)
        self.assertIn("pdf_fetch_failed", serialized)


if __name__ == "__main__":
    unittest.main()
