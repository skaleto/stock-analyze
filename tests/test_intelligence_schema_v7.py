from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import (
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    MIGRATION_V4,
    MIGRATION_V5,
    MIGRATION_V6,
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"


class FailingV7Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 7 and statement_index == 2:
            raise RuntimeError("injected_v7_migration_failure")


class IntelligenceSchemaV7Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _connection_at_version(self, version: int) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        migrations = (
            MIGRATION_V1,
            MIGRATION_V2,
            MIGRATION_V3,
            MIGRATION_V4,
            MIGRATION_V5,
            MIGRATION_V6,
        )
        for migration_version, migration in enumerate(migrations, start=1):
            if migration_version > version:
                break
            connection.executescript(migration)
            connection.execute(
                """
                INSERT INTO schema_meta(version, applied_at)
                VALUES(?, ?)
                """,
                (migration_version, NOW),
            )
        connection.execute(
            """
            INSERT INTO intelligence_settings(key, value, created_at)
            VALUES('historical_cutoff', ?, ?)
            """,
            (CUTOFF, NOW),
        )
        return connection

    def _create_real_v5_shape(self) -> tuple[int, int]:
        connection = self._connection_at_version(5)
        try:
            processed_id = connection.execute(
                """
                INSERT INTO documents(
                    source, source_id, title, published_at,
                    first_seen_at, effective_at, source_url,
                    mime_type, content_hash, raw_path, metadata_json,
                    status, queue_priority, live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "shared-probe",
                    "联合公告",
                    "2023-06-21T01:30:00+00:00",
                    NOW,
                    "2023-06-21T01:30:00+00:00",
                    "https://example.test/shared.pdf",
                    "text/plain",
                    "stable-shared-content",
                    "raw/shared.txt",
                    json.dumps(
                        {
                            "security_codes": [
                                "300114.SZ",
                                "831152.BJ",
                            ],
                            "security_links": [
                                {
                                    "ts_code": "831152.BJ",
                                    "name": "昆工科技",
                                },
                                {
                                    "ts_code": "300114.SZ",
                                    "name": "中航电测",
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "processed",
                    10,
                    0,
                ),
            ).lastrowid
            no_event_id = connection.execute(
                """
                INSERT INTO documents(
                    source, source_id, title, published_at,
                    first_seen_at, effective_at, source_url,
                    mime_type, content_hash, raw_path, metadata_json,
                    status, queue_priority, live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "single-link",
                    "单证券公告",
                    "2023-06-21T01:31:00+00:00",
                    NOW,
                    "2023-06-21T01:31:00+00:00",
                    "https://example.test/single.pdf",
                    "text/plain",
                    "stable-single-content",
                    "raw/single.txt",
                    json.dumps(
                        {
                            "ts_code": "159756.SZ",
                            "name": "建信智能汽车ETF",
                        },
                        ensure_ascii=False,
                    ),
                    "no_event",
                    10,
                    0,
                ),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO document_security_links(
                    document_id, ts_code, name, provenance,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    processed_id,
                    "300114.SZ",
                    "中航电测",
                    "legacy_metadata",
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end,
                    request_limit, status, fetched, inserted,
                    attempts, updated_at,
                    completion_strategy_version,
                    probe_manifest_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    2000,
                    "complete",
                    2000,
                    2000,
                    1,
                    NOW,
                    2,
                    1,
                ),
            )
            for code in ("300114.SZ", "831152.BJ"):
                connection.execute(
                    """
                    INSERT INTO backfill_partition_probe_documents(
                        source, partition_start, partition_end,
                        source_id, content_hash, ts_code,
                        document_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        "tushare_announcement",
                        "2023-06-21",
                        "2023-06-21",
                        "shared-probe",
                        "stable-shared-content",
                        code,
                        processed_id,
                        NOW,
                    ),
                )
            connection.commit()
            return int(processed_id), int(no_event_id)
        finally:
            connection.close()

    def test_v5_links_and_probe_are_imported_before_revision_reextract(self) -> None:
        processed_id, no_event_id = self._create_real_v5_shape()

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        with store.connect() as connection:
            documents = connection.execute(
                """
                SELECT id, status, link_revision, extracted_link_revision
                FROM documents
                WHERE id IN (?, ?)
                ORDER BY id
                """,
                (processed_id, no_event_id),
            ).fetchall()
            links = connection.execute(
                """
                SELECT document_id, ts_code, name
                FROM document_security_links
                ORDER BY document_id, ts_code
                """
            ).fetchall()
            catalog = connection.execute(
                """
                SELECT ts_code, name
                FROM announcement_security_catalog
                ORDER BY ts_code
                """
            ).fetchall()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            [tuple(row) for row in documents],
            [
                (processed_id, "collected", 2, 0),
                (no_event_id, "collected", 1, 0),
            ],
        )
        self.assertEqual(
            [tuple(row) for row in links],
            [
                (processed_id, "300114.SZ", "中航电测"),
                (processed_id, "831152.BJ", "昆工科技"),
                (no_event_id, "159756.SZ", "建信智能汽车ETF"),
            ],
        )
        self.assertEqual(
            [tuple(row) for row in catalog],
            [
                ("159756.SZ", "建信智能汽车ETF"),
                ("300114.SZ", "中航电测"),
                ("831152.BJ", "昆工科技"),
            ],
        )

    def test_v7_migration_failure_is_atomic_and_retryable(self) -> None:
        connection = self._connection_at_version(6)
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v7_migration_failure",
        ):
            FailingV7Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            refs_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table'
                  AND name='backfill_job_partition_refs'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(version, 6)
        self.assertIsNone(refs_table)
        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)

    def test_v6_owned_partition_becomes_canonical_evidence_reference(self) -> None:
        connection = self._connection_at_version(6)
        try:
            connection.execute(
                """
                INSERT INTO backfill_jobs(
                    job_id, source, start_date, end_date,
                    completion_strategy_version, config_hash,
                    request_limit, verification_required,
                    status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-job",
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    3,
                    "config-a",
                    2000,
                    2,
                    "partial",
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end,
                    request_limit, status, fetched, inserted,
                    attempts, updated_at, completion_strategy_version,
                    probe_manifest_version, job_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    2000,
                    "failed_overflow",
                    2000,
                    1998,
                    4,
                    NOW,
                    0,
                    1,
                    "legacy-job",
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partition_verification_state(
                    job_id, source, partition_start, partition_end,
                    rounds_total, stable_rounds, last_probe_hash,
                    last_new_documents, last_new_security_codes,
                    updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-job",
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    3,
                    2,
                    "stable-hash",
                    0,
                    0,
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_verification_rounds(
                    job_id, source, partition_start, partition_end,
                    round_no, probe_hash, probe_documents,
                    probe_security_codes, new_documents,
                    new_security_codes, stable_rounds, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-job",
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    3,
                    "stable-hash",
                    2,
                    2,
                    0,
                    0,
                    2,
                    NOW,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        with store.connect() as connection:
            partition = connection.execute(
                """
                SELECT job_id, evidence_config_hash, status, attempts
                FROM backfill_partitions
                WHERE source='tushare_announcement'
                  AND partition_start='2023-06-21'
                  AND partition_end='2023-06-21'
                """
            ).fetchone()
            refs = connection.execute(
                """
                SELECT job_id
                FROM backfill_job_partition_refs
                """
            ).fetchall()
            verification = connection.execute(
                """
                SELECT rounds_total, stable_rounds, last_probe_hash
                FROM backfill_partition_verification_state
                """
            ).fetchone()

        self.assertEqual(partition["job_id"], "")
        self.assertEqual(
            partition["evidence_config_hash"],
            "config-a",
        )
        self.assertEqual(partition["status"], "failed_overflow")
        self.assertEqual(partition["attempts"], 4)
        self.assertEqual(
            [str(row["job_id"]) for row in refs],
            ["legacy-job"],
        )
        self.assertEqual(
            tuple(verification),
            (3, 2, "stable-hash"),
        )
        progress = store.backfill_job_progress("legacy-job")
        self.assertEqual(progress["partitions_total"], 1)
        self.assertEqual(progress["verification"]["rounds_total"], 3)

    def test_buggy_v6_link_revision_shape_is_recomputed_and_requeued(self) -> None:
        connection = self._connection_at_version(6)
        try:
            document_id = connection.execute(
                """
                INSERT INTO documents(
                    source, source_id, title, published_at,
                    first_seen_at, effective_at, source_url,
                    mime_type, content_hash, raw_path, metadata_json,
                    status, queue_priority, live_observed,
                    link_revision, extracted_link_revision
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "already-v6",
                    "旧 V6 公告",
                    "2023-06-21T01:30:00+00:00",
                    NOW,
                    "2023-06-21T01:30:00+00:00",
                    "https://example.test/already-v6.pdf",
                    "text/plain",
                    "already-v6-content",
                    "raw/already-v6.txt",
                    json.dumps(
                        {
                            "security_codes": [
                                "300114.SZ",
                                "831152.BJ",
                            ],
                        }
                    ),
                    "no_event",
                    10,
                    0,
                    1,
                    1,
                ),
            ).lastrowid
            for code, name in (
                ("300114.SZ", "中航电测"),
                ("831152.BJ", "昆工科技"),
            ):
                connection.execute(
                    """
                    INSERT INTO document_security_links(
                        document_id, ts_code, name, provenance,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        document_id,
                        code,
                        name,
                        "legacy_probe",
                        NOW,
                        NOW,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        with store.connect() as connection:
            row = connection.execute(
                """
                SELECT status, link_revision, extracted_link_revision
                FROM documents
                WHERE id=?
                """,
                (document_id,),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            ("collected", 2, 0),
        )
        self.assertEqual(
            [int(item["id"]) for item in store.pending_documents()],
            [document_id],
        )


if __name__ == "__main__":
    unittest.main()
