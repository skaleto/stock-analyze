from __future__ import annotations

import hashlib
import gzip
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz

from stock_analyze.intelligence.artifact_exchange import (
    ArtifactExchangeError,
    artifact_worker_status,
    export_artifact_job,
    import_artifact_job,
    run_artifact_job,
)
from stock_analyze.intelligence.blob_store import (
    LocalBlobStore,
    pdf_object_key,
)
from stock_analyze.intelligence.artifact_backfill import (
    _next_parse_document,
)
from stock_analyze.intelligence.operations import (
    DefaultIntelligenceStageRunner,
)
from stock_analyze.intelligence.pdf_fetcher import (
    DownloadedPdf,
    RetryablePdfFetchError,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument
from stock_analyze.cli import main


def _text_pdf(text: str = "Historical announcement body.") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


class _FakeDownloader:
    def __init__(self, root: Path, payload: bytes) -> None:
        self.root = root
        self.payload = payload
        self.urls: list[str] = []

    def fetch(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
    ) -> DownloadedPdf:
        self.urls.append(url)
        digest = hashlib.sha256(self.payload).hexdigest()
        if expected_sha256 is not None:
            self.assertEqual(expected_sha256, digest)
        path = self.root / f"{digest}.pdf"
        path.write_bytes(self.payload)
        return DownloadedPdf(
            path=path,
            sha256=digest,
            byte_size=len(self.payload),
            mime_type="application/pdf",
        )

    @staticmethod
    def assertEqual(left: str, right: str) -> None:
        if left != right:
            raise AssertionError((left, right))


class _RetryableDownloader:
    def fetch(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
    ):
        del url, expected_sha256
        raise RetryablePdfFetchError("pdf_fetch_timeout")


class IntelligenceArtifactExchangeTest(unittest.TestCase):
    def _root(self, tmp: str) -> tuple[
        Path,
        IntelligenceStore,
        LocalBlobStore,
        DefaultIntelligenceStageRunner,
    ]:
        root = Path(tmp)
        (root / "configs").mkdir()
        (root / "configs" / "intelligence_semantic.yaml").write_text(
            """
artifact_store:
  production_kind: oss
  development_kind: local
  key_prefix: announcements
  local_root: data/shared/intelligence/artifacts
  allowed_hosts:
    - static.cninfo.com.cn
  max_pdf_bytes: 52428800
  download_max_attempts: 2
  download_connect_timeout_seconds: 5
  download_read_timeout_seconds: 15
  download_total_timeout_seconds: 30
parser:
  version: announcement-layout-v1
  min_text_characters_per_page: 20
  ocr_languages: chi_sim+eng
  ocr_render_dpi: 300
  extract_tables: true
""".strip()
            + "\n",
            encoding="utf-8",
        )
        store = IntelligenceStore(
            root / "data" / "shared" / "intelligence"
        )
        blobs = LocalBlobStore(
            root / "data" / "shared" / "intelligence" / "artifacts"
        )
        runner = DefaultIntelligenceStageRunner(root)
        runner._blob_store = blobs
        return root, store, blobs, runner

    @staticmethod
    def _insert_document(
        store: IntelligenceStore,
        *,
        source_id: str,
        source_url: str,
        published_at: str = "2026-07-01T00:00:00Z",
    ) -> int:
        document_id, _ = store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id=source_id,
                title=f"历史公告 {source_id}",
                published_at=published_at,
                first_seen_at="2026-07-30T00:00:00Z",
                effective_at="2026-07-01T00:00:00Z",
                source_url=source_url,
                content=b"metadata",
                metadata={"ingestion_mode": "history"},
            )
        )
        return document_id

    def _commit_pdf(
        self,
        store: IntelligenceStore,
        blobs: LocalBlobStore,
        document_id: int,
        payload: bytes,
    ) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        uri = blobs.put_if_absent(
            pdf_object_key(digest),
            payload,
            "application/pdf",
        )
        store.commit_pdf_artifact(
            document_id=document_id,
            content_hash=digest,
            storage_uri=uri,
            mime_type="application/pdf",
            byte_size=len(payload),
        )

    def test_parse_job_is_leased_run_and_imported_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-1",
                source_url="https://static.cninfo.com.cn/parse-1.pdf",
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)

            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-a",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )

            self.assertEqual(exported["status"], "leased")
            job_dir = Path(str(exported["job_dir"]))
            self.assertTrue((job_dir / "job.json").is_file())
            self.assertTrue((job_dir / "inputs" / "000000.pdf").is_file())
            duplicate = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-b",
                lease_seconds=3600,
                now=now + timedelta(minutes=1),
                runner=runner,
            )
            self.assertEqual(duplicate["status"], "empty")
            self.assertEqual(runner.parse(limit=1).processed, 0)
            self.assertIsNone(_next_parse_document(root))

            local = run_artifact_job(
                root,
                job_dir,
                workers=1,
            )
            self.assertEqual(local["status"], "ready_to_import")
            imported = import_artifact_job(
                root,
                job_dir,
                now=now + timedelta(minutes=2),
                runner=runner,
            )

            self.assertEqual(imported["status"], "imported")
            self.assertEqual(imported["succeeded"], 1)
            self.assertFalse((job_dir / "inputs").exists())
            self.assertFalse((job_dir / "outputs").exists())
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                [document_id],
            )
            repeated = import_artifact_job(
                root,
                job_dir,
                now=now + timedelta(minutes=3),
                runner=runner,
            )
            self.assertEqual(repeated["status"], "imported")
            self.assertTrue(repeated["reused"])

    def test_history_export_requires_explicit_history_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, _, runner = self._root(tmp)
            document_id, _ = store.insert_document(
                SourceDocument(
                    source="tushare_announcement",
                    source_id="ambiguous-current",
                    title="没有历史标记的公告",
                    published_at="2026-07-30T00:00:00Z",
                    first_seen_at="2026-07-30T00:01:00Z",
                    effective_at="2026-07-30T00:00:00Z",
                    source_url=(
                        "https://static.cninfo.com.cn/"
                        "ambiguous-current.pdf"
                    ),
                    content=b"metadata",
                    metadata={},
                )
            )
            runner.enqueue_missing_artifacts(limit=1)

            exported = export_artifact_job(
                root,
                stage="download",
                limit=1,
                worker_id="history-boundary",
                lease_seconds=3600,
                runner=runner,
            )

        self.assertEqual(exported["status"], "empty")

    def test_download_export_prioritizes_recent_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, _, runner = self._root(tmp)
            older = self._insert_document(
                store,
                source_id="older-history",
                source_url=(
                    "https://static.cninfo.com.cn/older-history.pdf"
                ),
                published_at="2001-01-01T00:00:00Z",
            )
            newer = self._insert_document(
                store,
                source_id="newer-history",
                source_url=(
                    "https://static.cninfo.com.cn/newer-history.pdf"
                ),
                published_at="2006-01-01T00:00:00Z",
            )
            runner.enqueue_missing_artifacts(limit=2)

            exported = export_artifact_job(
                root,
                stage="download",
                limit=1,
                worker_id="recent-first",
                lease_seconds=3600,
                runner=runner,
            )
            manifest = json.loads(
                (
                    Path(str(exported["job_dir"])) / "job.json"
                ).read_text(encoding="utf-8")
            )

        self.assertNotEqual(older, newer)
        self.assertEqual(
            manifest["items"][0]["document_id"],
            newer,
        )

    def test_import_fence_prevents_status_expiry_during_persistence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="fenced-import",
                source_url=(
                    "https://static.cninfo.com.cn/fenced-import.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf("Fenced import body."),
            )
            now = datetime(2026, 7, 30, tzinfo=timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="fenced-import",
                lease_seconds=60,
                now=now,
                runner=runner,
            )
            run_artifact_job(
                root,
                exported["job_dir"],
                workers=1,
            )
            persist = runner._persist_parse_result

            def persist_with_status_check(**kwargs):
                artifact_worker_status(
                    root,
                    now=now + timedelta(minutes=2),
                    runner=runner,
                )
                return persist(**kwargs)

            runner._persist_parse_result = persist_with_status_check
            imported = import_artifact_job(
                root,
                exported["job_dir"],
                now=now + timedelta(seconds=30),
                runner=runner,
            )
            with store.connect() as connection:
                job_status = connection.execute(
                    """
                    SELECT status
                    FROM artifact_worker_jobs
                    WHERE job_id=?
                    """,
                    (exported["job_id"],),
                ).fetchone()["status"]

        self.assertEqual(imported["status"], "imported")
        self.assertEqual(job_status, "imported")

    def test_parse_job_uses_multiple_process_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_ids = []
            for index in range(2):
                document_id = self._insert_document(
                    store,
                    source_id=f"parse-parallel-{index}",
                    source_url=(
                        "https://static.cninfo.com.cn/"
                        f"parse-parallel-{index}.pdf"
                    ),
                )
                self._commit_pdf(
                    store,
                    blobs,
                    document_id,
                    _text_pdf(f"Parallel historical body {index}."),
                )
                document_ids.append(document_id)
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=2,
                worker_id="coding-plan-parallel",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))

            local = run_artifact_job(root, job_dir, workers=2)
            imported = import_artifact_job(
                root,
                job_dir,
                now=now + timedelta(minutes=1),
                runner=runner,
            )

            self.assertEqual(local["succeeded"], 2)
            self.assertEqual(imported["succeeded"], 2)
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                document_ids,
            )

    def test_parse_job_uses_the_frozen_non_default_parser_version(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            config_path = (
                root / "configs" / "intelligence_semantic.yaml"
            )
            config_path.write_text(
                config_path.read_text(encoding="utf-8").replace(
                    "version: announcement-layout-v1",
                    "version: announcement-layout-v2",
                ),
                encoding="utf-8",
            )
            runner._config = None
            document_id = self._insert_document(
                store,
                source_id="parse-version-v2",
                source_url=(
                    "https://static.cninfo.com.cn/parse-version-v2.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-version",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))

            local = run_artifact_job(root, job_dir, workers=1)
            imported = import_artifact_job(
                root,
                job_dir,
                now=now + timedelta(minutes=1),
                runner=runner,
            )

            self.assertEqual(local["status"], "ready_to_import")
            self.assertEqual(imported["status"], "imported")
            snapshot = store.semantic_document_snapshot(document_id)
            self.assertEqual(
                snapshot["artifact"]["parser_version"],
                "announcement-layout-v2",
            )

    def test_expired_parse_lease_can_be_exported_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-expired",
                source_url=(
                    "https://static.cninfo.com.cn/parse-expired.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            first = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-a",
                lease_seconds=60,
                now=now,
                runner=runner,
            )

            second = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-b",
                lease_seconds=3600,
                now=now + timedelta(seconds=61),
                runner=runner,
            )

            self.assertEqual(second["status"], "leased")
            self.assertNotEqual(first["job_id"], second["job_id"])
            with store.connect() as connection:
                first_status = connection.execute(
                    """
                    SELECT status FROM artifact_worker_jobs
                    WHERE job_id=?
                    """,
                    (first["job_id"],),
                ).fetchone()[0]
            self.assertEqual(first_status, "expired")

    def test_status_expires_stale_leases_before_reporting_active_jobs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-status-expired",
                source_url=(
                    "https://static.cninfo.com.cn/"
                    "parse-status-expired.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-status",
                lease_seconds=60,
                now=now,
                runner=runner,
            )

            status = artifact_worker_status(
                root,
                now=now + timedelta(seconds=61),
                runner=runner,
            )

            self.assertEqual(status["active_leases"], [])
            self.assertEqual(len(status["jobs"]), 1)
            self.assertEqual(status["jobs"][0]["stage"], "parse")
            self.assertEqual(status["jobs"][0]["status"], "expired")
            self.assertEqual(status["jobs"][0]["count"], 1)

    def test_import_rejects_tampered_parse_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-tamper",
                source_url=(
                    "https://static.cninfo.com.cn/parse-tamper.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-a",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))
            run_artifact_job(root, job_dir, workers=1)
            output = next((job_dir / "outputs").glob("*.json.gz"))
            output.write_bytes(output.read_bytes() + b"tampered")

            with self.assertRaises(ArtifactExchangeError) as raised:
                import_artifact_job(
                    root,
                    job_dir,
                    now=now + timedelta(minutes=1),
                    runner=runner,
                )

            self.assertEqual(
                raised.exception.code,
                "artifact_job_output_hash_mismatch",
            )
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                [],
            )

    def test_import_rejects_tampered_parse_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-input-tamper",
                source_url=(
                    "https://static.cninfo.com.cn/"
                    "parse-input-tamper.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-a",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))
            run_artifact_job(root, job_dir, workers=1)
            input_path = job_dir / "inputs" / "000000.pdf"
            input_path.write_bytes(input_path.read_bytes() + b"tampered")

            with self.assertRaises(ArtifactExchangeError) as raised:
                import_artifact_job(
                    root,
                    job_dir,
                    now=now + timedelta(minutes=1),
                    runner=runner,
                )

            self.assertEqual(
                raised.exception.code,
                "artifact_job_input_hash_mismatch",
            )
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                [],
            )

    def test_import_classifies_malformed_parsed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-malformed",
                source_url=(
                    "https://static.cninfo.com.cn/parse-malformed.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-malformed",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))
            run_artifact_job(root, job_dir, workers=1)
            output = next((job_dir / "outputs").glob("*.json.gz"))
            malformed = gzip.compress(b'{"parser_version":"v"}', mtime=0)
            output.write_bytes(malformed)
            result_path = job_dir / "result.jsonl"
            result = json.loads(
                result_path.read_text(encoding="utf-8")
            )
            result["output_hash"] = hashlib.sha256(
                malformed
            ).hexdigest()
            result["output_bytes"] = len(malformed)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ArtifactExchangeError) as raised:
                import_artifact_job(
                    root,
                    job_dir,
                    now=now + timedelta(minutes=1),
                    runner=runner,
                )

            self.assertEqual(
                raised.exception.code,
                "artifact_job_parsed_payload_invalid",
            )

    def test_import_rejects_chunk_text_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="parse-text-tamper",
                source_url=(
                    "https://static.cninfo.com.cn/"
                    "parse-text-tamper.pdf"
                ),
            )
            self._commit_pdf(
                store,
                blobs,
                document_id,
                _text_pdf(),
            )
            now = datetime.now(timezone.utc)
            exported = export_artifact_job(
                root,
                stage="parse",
                limit=1,
                worker_id="coding-plan-text-tamper",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))
            run_artifact_job(root, job_dir, workers=1)
            output = next((job_dir / "outputs").glob("*.json.gz"))
            parsed = json.loads(gzip.decompress(output.read_bytes()))
            parsed["pages"][0]["chunks"][0]["text"] = "fabricated"
            tampered = gzip.compress(
                json.dumps(
                    parsed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                mtime=0,
            )
            output.write_bytes(tampered)
            result_path = job_dir / "result.jsonl"
            result = json.loads(
                result_path.read_text(encoding="utf-8")
            )
            result["output_hash"] = hashlib.sha256(
                tampered
            ).hexdigest()
            result["output_bytes"] = len(tampered)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ArtifactExchangeError) as raised:
                import_artifact_job(
                    root,
                    job_dir,
                    now=now + timedelta(minutes=1),
                    runner=runner,
                )

            self.assertEqual(
                raised.exception.code,
                "artifact_job_parsed_identity_mismatch",
            )

    def test_download_job_returns_pdf_for_authoritative_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, blobs, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="download-1",
                source_url=(
                    "http://static.cninfo.com.cn/fina/"
                    "2026-07-01/download-1.PDF"
                ),
            )
            runner.enqueue_missing_artifacts(limit=1)
            payload = _text_pdf("Downloaded historical announcement.")
            fake = _FakeDownloader(root, payload)
            now = datetime.now(timezone.utc)

            exported = export_artifact_job(
                root,
                stage="download",
                limit=1,
                worker_id="coding-plan-download",
                lease_seconds=3600,
                now=now,
                runner=runner,
            )
            job_dir = Path(str(exported["job_dir"]))
            local = run_artifact_job(
                root,
                job_dir,
                workers=1,
                downloader=fake,
            )
            imported = import_artifact_job(
                root,
                job_dir,
                now=now + timedelta(minutes=1),
                runner=runner,
            )

            self.assertEqual(local["status"], "ready_to_import")
            self.assertEqual(imported["status"], "imported")
            self.assertEqual(
                fake.urls,
                [
                    "https://static.cninfo.com.cn/finalpage/"
                    "2026-07-01/download-1.PDF"
                ],
            )
            artifact = store.current_pdf_artifact(document_id)
            self.assertIsNotNone(artifact)
            self.assertEqual(
                artifact["content_hash"],
                hashlib.sha256(payload).hexdigest(),
            )
            self.assertTrue(
                blobs.exists(str(artifact["storage_uri"]))
            )

    def test_retryable_download_uses_backoff_and_quarantines_after_three(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, store, _, runner = self._root(tmp)
            document_id = self._insert_document(
                store,
                source_id="retry-backoff",
                source_url=(
                    "https://static.cninfo.com.cn/retry-backoff.pdf"
                ),
            )
            runner.enqueue_missing_artifacts(limit=1)
            now = datetime(2026, 7, 30, tzinfo=timezone.utc)

            for attempt in range(3):
                attempt_at = now + timedelta(hours=attempt * 2)
                exported = export_artifact_job(
                    root,
                    stage="download",
                    limit=1,
                    worker_id=f"retry-{attempt}",
                    lease_seconds=3600,
                    now=attempt_at,
                    runner=runner,
                )
                self.assertEqual(exported["status"], "leased")
                run_artifact_job(
                    root,
                    exported["job_dir"],
                    workers=1,
                    downloader=_RetryableDownloader(),
                )
                imported = import_artifact_job(
                    root,
                    exported["job_dir"],
                    now=attempt_at + timedelta(minutes=1),
                    runner=runner,
                )
                self.assertEqual(imported["status"], "partial")
                immediate = export_artifact_job(
                    root,
                    stage="download",
                    limit=1,
                    worker_id=f"immediate-{attempt}",
                    lease_seconds=3600,
                    now=attempt_at + timedelta(minutes=2),
                    runner=runner,
                )
                self.assertEqual(immediate["status"], "empty")

            after_backoff = export_artifact_job(
                root,
                stage="download",
                limit=1,
                worker_id="after-max-attempts",
                lease_seconds=3600,
                now=now + timedelta(hours=8),
                runner=runner,
            )
            status = artifact_worker_status(
                root,
                now=now + timedelta(hours=8),
                runner=runner,
            )

        self.assertEqual(after_backoff["status"], "empty")
        self.assertEqual(
            status["quarantined_documents"]["download"],
            1,
        )

    def test_cli_exposes_export_and_status_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _, _, _ = self._root(tmp)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "intelligence-artifact-job-export",
                        "--repo-root",
                        str(root),
                        "--stage",
                        "parse",
                        "--limit",
                        "2",
                        "--worker-id",
                        "coding-plan-cli",
                        "--lease-seconds",
                        "3600",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["status"],
                "empty",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "intelligence-artifact-job-status",
                        "--repo-root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["status"],
                "ok",
            )


if __name__ == "__main__":
    unittest.main()
