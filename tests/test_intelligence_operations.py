from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import fitz

from stock_analyze.intelligence.blob_store import (
    LocalBlobStore,
    OssBlobStore,
    pdf_object_key,
)
from stock_analyze.intelligence.operations import (
    DefaultIntelligenceStageRunner,
    FatalOperationError,
    SourceWideFailure,
    StageResult,
    _AliyunOssBucketCompatibility,
    _resolve_source_pdf_url,
    run_intelligence_enrich,
    run_intelligence_reconcile,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


ROOT = Path(__file__).resolve().parents[1]


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.failures: dict[str, Exception] = {}
        self.results: dict[str, StageResult] = {}

    def _run(self, name: str, value: object) -> StageResult:
        self.calls.append((name, value))
        if name in self.failures:
            raise self.failures[name]
        return self.results.get(
            name,
            StageResult(
                stage=name,
                processed=1,
                succeeded=1,
                next_queue_depth=0,
            ),
        )

    def reconcile_metadata(self, *, lookback_days: int, limit: int) -> StageResult:
        return self._run("metadata", (lookback_days, limit))

    def enqueue_missing_artifacts(self, *, limit: int) -> StageResult:
        return self._run("enqueue", limit)

    def download(self, *, limit: int) -> StageResult:
        return self._run("download", limit)

    def parse(self, *, limit: int) -> StageResult:
        return self._run("parse", limit)

    def status(self) -> dict[str, object]:
        self.calls.append(("status", None))
        return {
            "metadata": {"documents": 1, "latest_rec_time": None, "date_gaps": []},
            "artifacts": {
                "queued": 0,
                "downloaded": 1,
                "parsed": 1,
                "ocr_failed": 0,
            },
            "semantic": {
                "queued": 0,
                "succeeded": 1,
                "no_event": 0,
                "quarantined": 0,
                "failed": 0,
            },
            "quality": {
                "evidence_grounding": None,
                "entity_accuracy": None,
                "numeric_exact_match": None,
            },
            "versions": {
                "profile": "a-share-announcement-v1",
                "prompt": "semantic-extract-v3",
                "schema": "announcement-events-v1-lite",
                "taxonomy": "cn-announcement-taxonomy-v1",
                "parser": "announcement-layout-v1",
            },
            "capacity": {
                "sqlite_bytes": 10,
                "local_artifact_bytes": 20,
                "oss_bytes": None,
            },
        }


class IntelligenceOperationsTest(unittest.TestCase):
    def test_parse_stops_before_disk_reserve_is_breached(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                ROOT / "configs" / "intelligence_semantic.yaml",
                root / "configs" / "intelligence_semantic.yaml",
            )
            runner = DefaultIntelligenceStageRunner(root)

            with mock.patch(
                "stock_analyze.intelligence.operations."
                "shutil.disk_usage",
                return_value=shutil._ntuple_diskusage(
                    10_000,
                    9_999,
                    1,
                ),
            ):
                with self.assertRaises(SourceWideFailure) as raised:
                    runner.parse(limit=1)

            self.assertEqual(raised.exception.category, "capacity")
            self.assertEqual(
                raised.exception.code,
                "intelligence_ecs_free_space_below_floor",
            )

    def test_pdf_fetcher_uses_bounded_runtime_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                ROOT / "configs" / "intelligence_semantic.yaml",
                root / "configs" / "intelligence_semantic.yaml",
            )
            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = LocalBlobStore(
                root / "blobs",
                key_prefix="announcements",
            )

            with mock.patch(
                "stock_analyze.intelligence.operations."
                "SecurePdfDownloader"
            ) as downloader:
                runner._pdf_fetcher()

            _, kwargs = downloader.call_args
            self.assertEqual(kwargs["max_attempts"], 2)
            self.assertEqual(
                kwargs["connect_timeout_seconds"],
                5,
            )
            self.assertEqual(
                kwargs["read_timeout_seconds"],
                15,
            )
            self.assertEqual(
                kwargs["total_timeout_seconds"],
                30,
            )

    def test_aliyun_oss_compatibility_uses_native_atomic_write_header(
        self,
    ) -> None:
        class Bucket:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}

            def put_object(
                self,
                key: str,
                payload: bytes,
                *,
                headers: dict[str, str],
            ) -> str:
                del key, payload
                self.headers = headers
                return "ok"

        bucket = Bucket()
        adapter = _AliyunOssBucketCompatibility(bucket)
        supplied = {
            "Content-Type": "application/pdf",
            "If-None-Match": "*",
            "x-oss-forbid-overwrite": "true",
        }

        self.assertEqual(
            adapter.put_object("key", b"pdf", headers=supplied),
            "ok",
        )
        self.assertNotIn("If-None-Match", bucket.headers)
        self.assertEqual(
            bucket.headers["x-oss-forbid-overwrite"],
            "true",
        )
        self.assertIn("If-None-Match", supplied)

    def test_tushare_cninfo_detail_url_resolves_to_https_pdf(self) -> None:
        source_url = (
            "http://www.cninfo.com.cn/new/disclosure/detail"
            "?stockCode=002470"
            "&announcementId=1225429685"
            "&orgId=9900014252"
            "&announcementTime=2026-07-18"
        )

        self.assertEqual(
            _resolve_source_pdf_url(source_url),
            (
                "https://static.cninfo.com.cn/finalpage/"
                "2026-07-18/1225429685.PDF"
            ),
        )
        self.assertEqual(
            _resolve_source_pdf_url(
                "https://static.cninfo.com.cn/finalpage/"
                "2026-07-18/1225429685.PDF"
            ),
            (
                "https://static.cninfo.com.cn/finalpage/"
                "2026-07-18/1225429685.PDF"
            ),
        )
        self.assertEqual(
            _resolve_source_pdf_url(
                "http://static.cninfo.com.cn/finalpage/"
                "2001-07-19/516246.PDF"
            ),
            (
                "https://static.cninfo.com.cn/finalpage/"
                "2001-07-19/516246.PDF"
            ),
        )
        self.assertEqual(
            _resolve_source_pdf_url(
                "http://dataclouds.cninfo.com.cn/sjother/"
                "regulatory_announcement/szse/example.pdf"
            ),
            (
                "https://dataclouds.cninfo.com.cn/sjother/"
                "regulatory_announcement/szse/example.pdf"
            ),
        )

    def test_legacy_cninfo_fina_url_resolves_without_http_redirect(
        self,
    ) -> None:
        self.assertEqual(
            _resolve_source_pdf_url(
                "http://static.cninfo.com.cn/fina/"
                "2006-10-11/18437851.PDF"
            ),
            (
                "https://static.cninfo.com.cn/finalpage/"
                "2006-10-11/18437851.PDF"
            ),
        )

    def test_reconcile_runs_fixed_stage_order_and_writes_durable_status(self) -> None:
        runner = RecordingRunner()
        now = datetime(2026, 7, 25, 1, 2, 3, 456789, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_intelligence_reconcile(
                tmp,
                lookback_days=2,
                limit=7,
                runner=runner,
                now=lambda: now,
            )

            self.assertEqual(
                [name for name, _ in runner.calls],
                [
                    "metadata",
                    "enqueue",
                    "download",
                    "parse",
                    "status",
                ],
            )
            self.assertEqual(runner.calls[0][1], (2, 7))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["counts"]["parse"]["succeeded"], 1)
            self.assertEqual(result["retryable_failures"], 0)
            self.assertEqual(result["terminal_failures"], 0)
            self.assertEqual(result["next_queue_depth"], 0)
            report = (
                Path(tmp)
                / "reports"
                / "intelligence"
                / "semantic_status_20260725T010203456789Z.json"
            )
            latest = report.with_name("semantic_status_latest.json")
            self.assertTrue(report.exists())
            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8")),
                json.loads(latest.read_text(encoding="utf-8")),
            )

    def test_document_failures_are_counted_and_later_stages_continue(self) -> None:
        runner = RecordingRunner()
        runner.results["download"] = StageResult(
            stage="download",
            processed=3,
            succeeded=1,
            retryable_failures=1,
            terminal_failures=1,
            next_queue_depth=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = run_intelligence_reconcile(
                tmp,
                runner=runner,
                now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
            )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["retryable_failures"], 1)
        self.assertEqual(result["terminal_failures"], 1)
        self.assertIn(("parse", 500), runner.calls)

    def test_source_wide_failure_stops_with_exit_two_contract_after_status(self) -> None:
        runner = RecordingRunner()
        runner.failures["metadata"] = SourceWideFailure(
            "authorization",
            "tushare_authorization_failed",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FatalOperationError) as context:
                run_intelligence_reconcile(
                    tmp,
                    runner=runner,
                    now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
                )

            self.assertEqual(context.exception.category, "authorization")
            self.assertEqual(context.exception.report["status"], "failed")
            self.assertEqual(runner.calls[-1], ("status", None))
            self.assertTrue(
                (
                    Path(tmp)
                    / "reports"
                    / "intelligence"
                    / "semantic_status_latest.json"
                ).exists()
            )

    def test_enrich_rejects_retired_semantic_stages(self) -> None:
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                ValueError,
                "intelligence_operation_stage_invalid",
            ):
                run_intelligence_enrich(
                    tmp,
                    limit=9,
                    stages=("download", "parse", "semantic"),
                    runner=runner,
                )

    def test_reconcile_accepts_an_ordered_stage_subset(self) -> None:
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as tmp:
            result = run_intelligence_reconcile(
                tmp,
                limit=9,
                stages=("download", "parse"),
                runner=runner,
                now=lambda: datetime(
                    2026, 7, 25, tzinfo=timezone.utc
                ),
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            [name for name, _ in runner.calls],
            ["download", "parse", "status"],
        )

    def test_enrich_rejects_unknown_or_out_of_order_stages(self) -> None:
        runner = RecordingRunner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_intelligence_enrich(
                    tmp,
                    stages=("parse", "download"),
                    runner=runner,
                )
            with self.assertRaises(ValueError):
                run_intelligence_enrich(
                    tmp,
                    stages=("download", "future"),
                    runner=runner,
                )

    def test_default_parse_stage_persists_real_pdf_layout_for_semantic_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "configs" / "intelligence_semantic.yaml").write_text(
                """
artifact_store:
  production_kind: oss
  development_kind: local
  key_prefix: announcements
  local_root: data/shared/intelligence/artifacts
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
            document_id, _ = store.insert_document(
                SourceDocument(
                    source="tushare_announcement",
                    source_id="ann-1",
                    title="重大合同公告",
                    published_at="2026-07-25T00:00:00Z",
                    first_seen_at="2026-07-25T00:01:00Z",
                    effective_at="2026-07-25T00:00:00Z",
                    source_url="https://static.cninfo.com.cn/ann-1.pdf",
                    content=b"metadata",
                )
            )
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text(
                (72, 72),
                "Major contract announcement with amount 100 million.",
            )
            payload = pdf.tobytes()
            pdf.close()
            blobs = LocalBlobStore(
                root / "data" / "shared" / "intelligence" / "artifacts"
            )
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
            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = blobs

            result = runner.parse(limit=10)

            self.assertEqual(result.succeeded, 1)
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                [document_id],
            )
            snapshot = store.semantic_document_snapshot(document_id)
            self.assertEqual(snapshot["artifact"]["status"], "parsed")
            self.assertTrue(snapshot["chunks"])

    def test_default_parse_stage_prioritizes_high_priority_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "configs" / "intelligence_semantic.yaml").write_text(
                """
artifact_store:
  production_kind: oss
  development_kind: local
  key_prefix: announcements
  local_root: data/shared/intelligence/artifacts
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
            document_ids: list[int] = []
            for index in range(2):
                document_id, _ = store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"priority-ann-{index}",
                        title=f"Priority announcement {index}",
                        published_at=f"2026-07-2{index}T00:00:00Z",
                        first_seen_at=f"2026-07-2{index}T00:01:00Z",
                        effective_at=f"2026-07-2{index}T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"priority-ann-{index}.pdf"
                        ),
                        content=b"metadata",
                    )
                )
                document_ids.append(document_id)
                pdf = fitz.open()
                page = pdf.new_page()
                page.insert_text(
                    (72, 72),
                    f"Priority announcement body {index}.",
                )
                payload = pdf.tobytes()
                pdf.close()
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
            with store.connect() as connection:
                connection.execute(
                    "UPDATE documents SET queue_priority=1000 WHERE id=?",
                    (document_ids[1],),
                )

            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = blobs
            result = runner.parse(limit=1)

            self.assertEqual(result.succeeded, 1)
            self.assertEqual(
                store.semantic_ready_document_ids(limit=10),
                [document_ids[1]],
            )

    def test_default_parse_stage_uses_bounded_configured_workers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                ROOT / "configs" / "intelligence_semantic.yaml",
                root / "configs" / "intelligence_semantic.yaml",
            )
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            blobs = LocalBlobStore(
                root / "data" / "shared" / "intelligence" / "artifacts"
            )
            for index in range(4):
                document_id, _ = store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"parallel-parse-{index}",
                        title=f"并发解析公告 {index}",
                        published_at="2026-07-24T00:00:00Z",
                        first_seen_at="2026-07-24T00:01:00Z",
                        effective_at="2026-07-24T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"parallel-parse-{index}.pdf"
                        ),
                        content=b"metadata",
                    )
                )
                pdf = fitz.open()
                page = pdf.new_page()
                page.insert_text(
                    (72, 72),
                    f"Parallel parser body {index}.",
                )
                payload = pdf.tobytes()
                pdf.close()
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

            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = blobs
            delegate = runner._parser()

            class ConcurrentParser:
                config = delegate.config

                def __init__(self) -> None:
                    self.active = 0
                    self.maximum = 0
                    self.lock = threading.Lock()

                def parse(self, *args, **kwargs):
                    with self.lock:
                        self.active += 1
                        self.maximum = max(
                            self.maximum,
                            self.active,
                        )
                    time.sleep(0.03)
                    try:
                        return delegate.parse(*args, **kwargs)
                    finally:
                        with self.lock:
                            self.active -= 1

            parser = ConcurrentParser()
            runner._parser = lambda: parser

            result = runner.parse(limit=4)

            self.assertEqual(result.succeeded, 4)
            self.assertEqual(parser.maximum, 1)

    def test_default_enqueue_stage_uses_one_bounded_sql_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            document_ids = []
            for index in range(3):
                document_id, _ = store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"ann-{index}",
                        title=f"公告 {index}",
                        published_at=f"2026-07-2{index}T00:00:00Z",
                        first_seen_at=f"2026-07-2{index}T00:01:00Z",
                        effective_at=f"2026-07-2{index}T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"ann-{index}.pdf"
                        ),
                        content=b"metadata",
                    )
                )
                document_ids.append(document_id)

            result = DefaultIntelligenceStageRunner(
                root
            ).enqueue_missing_artifacts(limit=2)

            self.assertEqual(result.processed, 2)
            self.assertEqual(result.succeeded, 2)
            self.assertEqual(result.next_queue_depth, 1)
            with store.connect() as connection:
                queued = connection.execute(
                    """
                    SELECT document_id
                    FROM document_artifacts
                    WHERE artifact_type='pdf' AND status='queued'
                    ORDER BY document_id
                    """
                ).fetchall()
            self.assertEqual(
                [int(row["document_id"]) for row in queued],
                document_ids[:2],
            )

    def test_default_download_stage_uses_bounded_configured_workers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                ROOT / "configs" / "intelligence_semantic.yaml",
                root / "configs" / "intelligence_semantic.yaml",
            )
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            document_ids = []
            for index in range(8):
                document_id, _ = store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"parallel-{index}",
                        title=f"并发公告 {index}",
                        published_at="2026-07-24T00:00:00Z",
                        first_seen_at="2026-07-24T00:01:00Z",
                        effective_at="2026-07-24T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"parallel-{index}.pdf"
                        ),
                        content=b"metadata",
                    )
                )
                document_ids.append(document_id)
            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = LocalBlobStore(
                root / "blobs",
                key_prefix="announcements",
            )
            runner.enqueue_missing_artifacts(limit=8)

            class ConcurrentFetcher:
                def __init__(self) -> None:
                    self.active = 0
                    self.maximum = 0
                    self.lock = threading.Lock()

                def fetch(self, document_id: int) -> dict[str, object]:
                    with self.lock:
                        self.active += 1
                        self.maximum = max(self.maximum, self.active)
                    time.sleep(0.03)
                    with self.lock:
                        self.active -= 1
                    return {"document_id": document_id}

            fetcher = ConcurrentFetcher()
            runner._pdf_fetcher = lambda: fetcher

            result = runner.download(limit=8)

            self.assertEqual(result.succeeded, 8)
            self.assertEqual(result.retryable_failures, 0)
            self.assertEqual(result.terminal_failures, 0)
            self.assertEqual(fetcher.maximum, 4)

    def test_download_workers_use_isolated_oss_clients(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                ROOT / "configs" / "intelligence_semantic.yaml",
                root / "configs" / "intelligence_semantic.yaml",
            )
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            for index in range(8):
                store.insert_document(
                    SourceDocument(
                        source="tushare_announcement",
                        source_id=f"isolated-oss-{index}",
                        title=f"独立 OSS 客户端公告 {index}",
                        published_at="2026-07-24T00:00:00Z",
                        first_seen_at="2026-07-24T00:01:00Z",
                        effective_at="2026-07-24T00:00:00Z",
                        source_url=(
                            "https://static.cninfo.com.cn/"
                            f"isolated-oss-{index}.pdf"
                        ),
                        content=b"metadata",
                    )
                )

            class Bucket:
                pass

            runner = DefaultIntelligenceStageRunner(root)
            runner._blob_store = OssBlobStore(
                endpoint="https://oss-cn-hangzhou-internal.aliyuncs.com",
                bucket_name="stock-analyze-hz",
                key_prefix="announcements",
                bucket_client=Bucket(),
            )
            runner.enqueue_missing_artifacts(limit=8)

            built_stores: list[LocalBlobStore] = []
            built_lock = threading.Lock()

            def build_store() -> LocalBlobStore:
                with built_lock:
                    worker_number = len(built_stores)
                    blob_store = LocalBlobStore(
                        root / f"worker-{worker_number}",
                        key_prefix="announcements",
                    )
                    built_stores.append(blob_store)
                    return blob_store

            used_store_ids: set[int] = set()
            used_lock = threading.Lock()

            class Fetcher:
                def __init__(self, blob_store) -> None:
                    self.blob_store = blob_store

                def fetch(self, document_id: int) -> dict[str, object]:
                    with used_lock:
                        used_store_ids.add(id(self.blob_store))
                    time.sleep(0.03)
                    return {"document_id": document_id}

            runner._build_blob_store = build_store
            runner._pdf_fetcher = (
                lambda *, blob_store=None: Fetcher(blob_store)
            )

            result = runner.download(limit=8)

            self.assertEqual(result.succeeded, 8)
            self.assertEqual(len(built_stores), 4)
            self.assertEqual(len(used_store_ids), 4)
            self.assertNotIn(id(runner.blob_store), used_store_ids)

    def test_service_is_locked_bounded_and_uses_secret_environment(self) -> None:
        text = (ROOT / "deploy/systemd/stock-analyze-intelligence.service").read_text()
        self.assertIn("EnvironmentFile=-/etc/stock-analyze/secrets.env", text)
        self.assertIn("/usr/bin/flock --nonblock", text)
        self.assertIn("TimeoutStartSec=25min", text)
        self.assertIn("intelligence-ingest", text)
        self.assertIn("intelligence-extract", text)

    def test_timer_is_persistent_and_not_high_frequency(self) -> None:
        text = (ROOT / "deploy/systemd/stock-analyze-intelligence.timer").read_text()
        self.assertIn(
            "OnCalendar=Mon..Fri *-*-* 09,12,16,23:30:00 Asia/Shanghai",
            text,
        )
        self.assertIn("OnCalendar=Mon..Fri *-*-* 21:45:00 Asia/Shanghai", text)
        self.assertIn("Persistent=true", text)

    def test_market_data_runs_bounded_ifind_audits(self) -> None:
        text = (ROOT / "deploy/systemd/stock-analyze-market-data.service").read_text()
        self.assertIn(
            "intelligence-source-audit --repo-root /opt/stock-analyze/app "
            "--datasets market --supplement",
            text,
        )
        self.assertIn(
            "intelligence-source-audit --repo-root /opt/stock-analyze/app "
            "--datasets announcement --announcement-scope operational "
            "--supplement",
            text,
        )

    def test_intelligence_runtime_has_scoped_units_config_and_health_check(self) -> None:
        service = (
            ROOT / "deploy/systemd/stock-analyze-intelligence.service"
        ).read_text()
        timer = (
            ROOT / "deploy/systemd/stock-analyze-intelligence.timer"
        ).read_text()
        health_check = (ROOT / "scripts/check-ecs-timers.sh").read_text()

        self.assertIn("intelligence-ingest", service)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("stock-analyze-intelligence.timer", health_check)
        self.assertTrue((ROOT / "configs/intelligence_factors.json").is_file())

    def test_research_refreshes_factor_diagnostics(self) -> None:
        text = (ROOT / "deploy/systemd/stock-analyze-research.service").read_text()
        self.assertNotIn("stock-analyze-intelligence.service", text)
        self.assertIn("intelligence-evaluate", text)
        market_data = (ROOT / "deploy/systemd/stock-analyze-market-data.service").read_text()
        self.assertNotIn("stock-analyze-intelligence.service", market_data)


if __name__ == "__main__":
    unittest.main()
