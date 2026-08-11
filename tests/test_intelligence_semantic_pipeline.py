from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

from stock_analyze.intelligence.blob_store import LocalBlobStore
from stock_analyze.intelligence.ingestion import IntelligencePipeline
from stock_analyze.intelligence.semantic.pipeline import (
    SemanticPipeline,
    _segment_evidence_chunks,
)
from stock_analyze.intelligence.semantic.provider import (
    SemanticProviderError,
    SemanticProviderIdentity,
    SemanticProviderResponse,
)
from stock_analyze.intelligence.semantic.taxonomy import EventTaxonomy
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class FakeSemanticProvider:
    def __init__(
        self,
        *,
        model: str = "deepseek-v4-test",
        failures: int = 0,
    ) -> None:
        self._identity = SemanticProviderIdentity(
            provider="fake-openai-compatible",
            model=model,
            endpoint_host="provider.invalid",
        )
        self.failures = failures
        self.calls = []

    @property
    def identity(self) -> SemanticProviderIdentity:
        return self._identity

    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        if len(self.calls) <= self.failures:
            raise SemanticProviderError(
                "semantic_provider_timeout",
                retryable=True,
            )
        payload = {
            "document_id": int(bundle.document_id),
            "schema_version": bundle.schema_version,
            "events": [],
            "evidence": [],
            "no_event_reason": "no_material_event",
        }
        raw_output = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=payload,
            raw_output=raw_output,
            input_hash=hashlib.sha256(
                json.dumps(
                    bundle.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            output_hash=hashlib.sha256(raw_output.encode()).hexdigest(),
            request_id="request-secret-must-not-be-persisted",
            response_model=self.identity.model,
            input_tokens=101,
            output_tokens=23,
            total_tokens=124,
            latency_ms=17,
        )


class BlockingSemanticProvider(FakeSemanticProvider):
    def __init__(self, store: IntelligenceStore) -> None:
        super().__init__()
        self.store = store
        self.started = threading.Event()
        self.release = threading.Event()

    def extract(self, bundle, *, response_schema):
        with self.store.connect() as connection:
            status = connection.execute(
                """
                SELECT status
                FROM semantic_runs
                WHERE document_id=?
                """,
                (int(bundle.document_id),),
            ).fetchone()["status"]
        if status != "running":
            raise AssertionError("semantic run was not persisted before I/O")
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("test provider release timed out")
        return super().extract(bundle, response_schema=response_schema)


class SemanticPipelineTest(unittest.TestCase):
    def test_long_chunks_are_tagged_as_durable_semantic_segments(self) -> None:
        original = "甲" * 4_100

        parts = _segment_evidence_chunks([{
            "chunk_id": "source-1",
            "page_number": 1,
            "section": "body",
            "bbox": [],
            "text": original,
        }])

        self.assertEqual(len(parts), 2)
        self.assertEqual("".join(str(part["text"]) for part in parts), original)
        self.assertEqual(
            [part["section"] for part in parts],
            ["semantic_segment", "semantic_segment"],
        )
        self.assertEqual(
            [(part["source_start"], part["source_end"]) for part in parts],
            [(0, 4_000), (4_000, 4_100)],
        )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = IntelligenceStore(self.root / "intelligence")
        self.blobs = LocalBlobStore(self.root / "private-artifacts")
        self.taxonomy = EventTaxonomy.load(
            Path(__file__).parents[1]
            / "configs"
            / "intelligence_event_taxonomy_v1.json"
        )
        self.document_id = self._seed_document()
        self._seed_parsed_artifact(self.document_id)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_document(
        self,
        source_id: str = "announcement-1",
    ) -> int:
        document_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id=source_id,
                title="关于以集中竞价方式回购股份的公告",
                published_at="2026-07-20T09:00:00+08:00",
                first_seen_at="2026-07-20T09:01:00+08:00",
                effective_at="2026-07-20T09:01:00+08:00",
                source_url="https://static.cninfo.com.cn/finalpage.pdf",
                content=f"source metadata only:{source_id}".encode(),
                metadata={
                    "rec_time": "2026-07-20 09:01:00",
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "security_links": [
                        {
                            "ts_code": "000001.SZ",
                            "name": "平安银行",
                            "provenance": "anns_d",
                        }
                    ],
                    "subsequent_return": 999,
                    "portfolio_position": "secret",
                    "future_statement": {"profit": 1},
                },
            )
        )
        return document_id

    def _seed_parsed_artifact(
        self,
        document_id: int,
        *,
        status: str = "parsed",
        with_chunk: bool = True,
    ) -> None:
        timestamp = "2026-07-20T01:02:00+00:00"
        artifact_hash = hashlib.sha256(
            f"artifact-{document_id}".encode()
        ).hexdigest()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(?, ?, 'parsed', ?, ?, 'application/json', 100,
                         'announcement-layout-v1', ?, '', ?, ?)
                """,
                (
                    f"parsed-{document_id}",
                    document_id,
                    artifact_hash,
                    f"localblob://artifacts/announcements/parsed/{artifact_hash}",
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            if with_chunk:
                text = "公司拟以自有资金回购股份，回购金额不超过一亿元。"
                connection.execute(
                    """
                    INSERT INTO document_chunks(
                        chunk_id, document_id, artifact_id, sequence_no,
                        page_number, section, bbox_json, text, text_hash,
                        ocr_used, ocr_confidence, parser_version
                    ) VALUES(?, ?, ?, 0, 1, 'body', ?, ?, ?, 0, NULL, ?)
                    """,
                    (
                        f"chunk-{document_id}",
                        document_id,
                        f"parsed-{document_id}",
                        json.dumps([0, 0, 100, 40]),
                        text,
                        hashlib.sha256(text.encode()).hexdigest(),
                        "announcement-layout-v1",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO document_tables(
                        table_id, document_id, artifact_id, page_number,
                        sequence_no, bbox_json, cells_json, parser_version
                    ) VALUES(?, ?, ?, 1, 0, ?, ?, ?)
                    """,
                    (
                        f"table-{document_id}",
                        document_id,
                        f"parsed-{document_id}",
                        json.dumps([0, 50, 100, 100]),
                        json.dumps(
                            [
                                {
                                    "row_index": 0,
                                    "column_index": 0,
                                    "text": "回购金额",
                                    "bbox": [0, 50, 50, 70],
                                },
                                {
                                    "row_index": 1,
                                    "column_index": 0,
                                    "text": "2026年7月21日",
                                    "bbox": [0, 70, 50, 90],
                                },
                            ],
                            ensure_ascii=False,
                        ),
                        "announcement-layout-v1",
                    ),
                )

    def pipeline(
        self,
        provider: FakeSemanticProvider,
        *,
        prompt_version: str = "announcement-event-v1",
    ) -> SemanticPipeline:
        return SemanticPipeline(
            store=self.store,
            blob_store=self.blobs,
            provider=provider,
            taxonomy=self.taxonomy,
            prompt_version=prompt_version,
            schema_version="announcement-events-v1",
            audit_sample_rate=0.05,
        )

    def test_bundle_contains_only_the_plan_whitelist(self) -> None:
        provider = FakeSemanticProvider()
        bundle = self.pipeline(provider).build_bundle(self.document_id)

        self.assertEqual(
            set(bundle.payload),
            {
                "document",
                "taxonomy_candidates",
                "entity_whitelist",
                "chunks",
                "tables",
                "revision_context",
                "route_context",
            },
        )
        self.assertEqual(
            bundle.payload["route_context"],
            {
                "document_kind": "event_announcement",
                "extraction_purpose": "canonical_event",
                "difficulty_tags": ["table_heavy"],
                "reason_codes": [
                    "title_taxonomy_match",
                ],
            },
        )
        self.assertEqual(
            set(bundle.payload["document"]),
            {
                "id",
                "title",
                "ts_code",
                "name",
                "published_at",
                "rec_time",
                "source_url",
            },
        )
        serialized = json.dumps(bundle.payload, ensure_ascii=False)
        for forbidden in (
            "subsequent_return",
            "portfolio_position",
            "future_statement",
            "price",
            "label",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(
            bundle.payload["entity_whitelist"],
            [
                {
                    "entity_id": "000001.SZ",
                    "name": "平安银行",
                    "allowed_roles": ["issuer"],
                }
            ],
        )
        self.assertIn(
            {
                "chunk_id": f"table-{self.document_id}-r0-c0",
                "page_number": 1,
                "section": "table_cell",
                "bbox": [0, 50, 50, 70],
                "text": "回购金额",
            },
            bundle.payload["chunks"],
        )
        self.assertIn(
            f"table-{self.document_id}-r1-c0",
            {
                chunk["chunk_id"]
                for chunk in bundle.payload["chunks"]
            },
        )
        self.assertIn(
            {
                "chunk_id": f"doc{self.document_id}-meta-title",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "关于以集中竞价方式回购股份的公告",
            },
            bundle.payload["chunks"],
        )
        self.assertIn(
            {
                "chunk_id": f"doc{self.document_id}-meta-issuer",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "平安银行",
            },
            bundle.payload["chunks"],
        )

    def test_revision_context_loads_parent_without_scanning_children(self) -> None:
        parent_source_id = "announcement-parent"
        parent_id = self._seed_document(parent_source_id)
        child_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id="announcement-child",
                title="关于回购股份方案修订的公告",
                published_at="2026-07-21T09:00:00+08:00",
                first_seen_at="2026-07-21T09:01:00+08:00",
                effective_at="2026-07-21T09:01:00+08:00",
                source_url="https://static.cninfo.com.cn/child.pdf",
                revision_of=parent_source_id,
                content=b"child",
                metadata={
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "security_links": [
                        {
                            "ts_code": "000001.SZ",
                            "name": "平安银行",
                            "provenance": "anns_d",
                        }
                    ],
                },
            )
        )
        self._seed_parsed_artifact(child_id)

        bundle = self.pipeline(FakeSemanticProvider()).build_bundle(child_id)

        self.assertEqual(
            bundle.payload["revision_context"],
            [
                {
                    "document_id": parent_id,
                    "title": "关于以集中竞价方式回购股份的公告",
                    "published_at": "2026-07-20T01:00:00+00:00",
                    "relation": "prior_revision",
                }
            ],
        )

    def test_semantic_snapshot_performance_indexes_are_present(self) -> None:
        with self.store.connect() as connection:
            chunk_indexes = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA index_list(document_chunks)"
                )
            }
            table_indexes = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA index_list(document_tables)"
                )
            }

        self.assertIn(
            "idx_document_chunks_document_artifact",
            chunk_indexes,
        )
        self.assertIn(
            "idx_document_tables_document_artifact",
            table_indexes,
        )

    def test_identical_run_reuses_result_and_raw_output_is_private(self) -> None:
        provider = FakeSemanticProvider()
        pipeline = self.pipeline(provider)

        first = pipeline.process_document(self.document_id)
        repeated = pipeline.process_document(self.document_id)

        self.assertEqual(first.status, "no_event")
        self.assertFalse(first.reused)
        self.assertEqual(repeated.status, "no_event")
        self.assertTrue(repeated.reused)
        self.assertEqual(len(provider.calls), 1)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM semantic_runs"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        row = dict(rows[0])
        self.assertEqual(row["status"], "no_event")
        self.assertEqual(row["input_tokens"], 101)
        self.assertEqual(row["output_tokens"], 23)
        self.assertEqual(row["latency_ms"], 17)
        self.assertTrue(self.blobs.exists(str(row["output_uri"])))
        self.assertEqual(
            hashlib.sha256(
                self.blobs.read(str(row["output_uri"]))
            ).hexdigest(),
            row["output_hash"],
        )
        self.assertNotIn("request-secret", json.dumps(row))
        sqlite_bytes = self.store.db_path.read_bytes()
        self.assertNotIn(b"no_material_event", sqlite_bytes)

    def test_model_or_prompt_change_creates_new_immutable_run(self) -> None:
        first_provider = FakeSemanticProvider(model="model-a")
        second_provider = FakeSemanticProvider(model="model-b")

        self.pipeline(first_provider).process_document(self.document_id)
        self.pipeline(
            first_provider,
            prompt_version="announcement-event-v2",
        ).process_document(self.document_id)
        self.pipeline(second_provider).process_document(self.document_id)

        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT model, prompt_version, status
                FROM semantic_runs
                ORDER BY model, prompt_version
                """
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {(row["model"], row["prompt_version"]) for row in rows},
            {
                ("model-a", "announcement-event-v1"),
                ("model-a", "announcement-event-v2"),
                ("model-b", "announcement-event-v1"),
            },
        )
        self.assertEqual(
            {row["status"] for row in rows},
            {"no_event"},
        )

    def test_retryable_failure_reuses_run_id_and_can_retry(self) -> None:
        provider = FakeSemanticProvider(failures=1)
        pipeline = self.pipeline(provider)

        failed = pipeline.process_document(self.document_id)
        succeeded = pipeline.process_document(self.document_id)

        self.assertEqual(failed.status, "failed_retryable")
        self.assertEqual(succeeded.status, "no_event")
        self.assertEqual(failed.run_id, succeeded.run_id)
        self.assertEqual(len(provider.calls), 2)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT status, error FROM semantic_runs"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "no_event")
        self.assertEqual(rows[0]["error"], "")

    def test_payment_required_is_persisted_as_budget_deferred(
        self,
    ) -> None:
        class PaymentRequiredProvider(FakeSemanticProvider):
            def extract(self, bundle, *, response_schema):
                self.calls.append((bundle, response_schema))
                raise SemanticProviderError(
                    "semantic_provider_payment_required",
                    retryable=True,
                    status_code=402,
                )

        result = self.pipeline(
            PaymentRequiredProvider()
        ).process_document(self.document_id)

        self.assertEqual(result.status, "budget_deferred")
        self.assertEqual(
            result.error,
            "semantic_provider_payment_required",
        )

    def test_account_overdue_is_persisted_as_budget_deferred(
        self,
    ) -> None:
        class AccountOverdueProvider(FakeSemanticProvider):
            def extract(self, bundle, *, response_schema):
                self.calls.append((bundle, response_schema))
                raise SemanticProviderError(
                    "semantic_provider_account_overdue",
                    retryable=True,
                    status_code=403,
                )

        result = self.pipeline(
            AccountOverdueProvider()
        ).process_document(self.document_id)

        self.assertEqual(result.status, "budget_deferred")
        self.assertEqual(
            result.error,
            "semantic_provider_account_overdue",
        )

    def test_running_is_persisted_before_call_and_concurrent_run_reuses(self) -> None:
        provider = BlockingSemanticProvider(self.store)
        pipeline = self.pipeline(provider)
        worker_result = []

        worker = threading.Thread(
            target=lambda: worker_result.append(
                pipeline.process_document(self.document_id)
            )
        )
        worker.start()
        self.assertTrue(provider.started.wait(timeout=5))

        concurrent = pipeline.process_document(self.document_id)
        self.assertEqual(concurrent.status, "running")
        self.assertTrue(concurrent.reused)
        self.assertEqual(len(provider.calls), 0)

        provider.release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_result[0].status, "no_event")
        self.assertEqual(len(provider.calls), 1)

    def test_semantic_processing_does_not_depend_on_document_pending(self) -> None:
        self.store.mark_document(self.document_id, "no_event")
        provider = FakeSemanticProvider()
        semantic_pipeline = self.pipeline(provider)

        rule_pipeline = IntelligencePipeline.__new__(IntelligencePipeline)
        rule_pipeline.store = self.store
        summary = rule_pipeline.extract_semantic(
            semantic_pipeline,
            limit=10,
        )

        self.assertEqual(summary["documents"], 1)
        self.assertEqual(summary["statuses"], {"no_event": 1})
        self.assertEqual(len(provider.calls), 1)

    def test_ocr_failed_artifact_is_blocked_without_a_run(self) -> None:
        blocked_id = self._seed_document("announcement-blocked")
        self._seed_parsed_artifact(
            blocked_id,
            status="ocr_failed",
            with_chunk=False,
        )
        provider = FakeSemanticProvider()

        result = self.pipeline(provider).process_document(blocked_id)

        self.assertEqual(result.status, "blocked_artifact")
        self.assertEqual(len(provider.calls), 0)
        with self.store.connect() as connection:
            run_count = connection.execute(
                "SELECT COUNT(*) FROM semantic_runs WHERE document_id=?",
                (blocked_id,),
            ).fetchone()[0]
        self.assertEqual(run_count, 0)


if __name__ == "__main__":
    unittest.main()
