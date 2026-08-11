from __future__ import annotations

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
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"


class FailingV6Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 6 and statement_index == 2:
            raise RuntimeError("injected_v6_migration_failure")


class IntelligenceSchemaV6Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_v4_database(self) -> None:
        self.root.mkdir(parents=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            for version, migration in (
                (1, MIGRATION_V1),
                (2, MIGRATION_V2),
                (3, MIGRATION_V3),
                (4, MIGRATION_V4),
            ):
                connection.executescript(migration)
                connection.execute(
                    """
                    INSERT INTO schema_meta(version, applied_at)
                    VALUES(?, ?)
                    """,
                    (version, NOW),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (CUTOFF, NOW),
            )
            document_id = connection.execute(
                """
                INSERT INTO documents(
                    source, source_id, title, published_at,
                    first_seen_at, effective_at, source_url,
                    mime_type, content_hash, raw_path, metadata_json,
                    queue_priority, live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    (
                        '{"security_codes":["300114.SZ","831152.BJ"],'
                        '"security_links":['
                        '{"ts_code":"300114.SZ","name":"中航电测"},'
                        '{"ts_code":"831152.BJ","name":"昆工科技"}]}'
                    ),
                    10,
                    0,
                ),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO document_availability(
                    document_id, source_recorded_at,
                    research_available_at, availability_provenance,
                    historical_cutoff, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (document_id, None, NOW, "observed", CUTOFF, NOW),
            )
            for partition_start, partition_end, fetched in (
                ("2023-06-21", "2023-06-21", 2000),
                ("2023-07-01", "2023-07-31", 12),
            ):
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
                        partition_start,
                        partition_end,
                        2000,
                        "complete",
                        fetched,
                        fetched,
                        1,
                        NOW,
                        2,
                        int(fetched == 2000),
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
                        document_id,
                        NOW,
                    ),
                )
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, source, started_at, finished_at, status,
                    cursor_in, cursor_out, fetched, inserted, error
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-saturated",
                    "tushare_announcement",
                    "2023-07-01T16:00:00+00:00",
                    "2023-07-01T16:01:00+00:00",
                    "degraded",
                    "",
                    "",
                    2000,
                    2000,
                    "day_saturated:20230701",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _create_v5_database(self) -> None:
        self.root.mkdir(parents=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            for version, migration in (
                (1, MIGRATION_V1),
                (2, MIGRATION_V2),
                (3, MIGRATION_V3),
                (4, MIGRATION_V4),
                (5, MIGRATION_V5),
            ):
                connection.executescript(migration)
                connection.execute(
                    """
                    INSERT INTO schema_meta(version, applied_at)
                    VALUES(?, ?)
                    """,
                    (version, NOW),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (CUTOFF, NOW),
            )
            connection.commit()
        finally:
            connection.close()

    def test_v4_probe_links_catalog_retry_and_all_complete_are_revalidated(self) -> None:
        self._create_v4_database()

        store = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )

        with store.connect() as connection:
            links = connection.execute(
                """
                SELECT ts_code, name
                FROM document_security_links
                ORDER BY ts_code
                """
            ).fetchall()
            catalog = connection.execute(
                """
                SELECT ts_code, name
                FROM announcement_security_catalog
                WHERE source='tushare_announcement'
                ORDER BY ts_code
                """
            ).fetchall()
            partitions = connection.execute(
                """
                SELECT partition_start, status,
                       completion_strategy_version,
                       probe_manifest_version
                FROM backfill_partitions
                ORDER BY partition_start
                """
            ).fetchall()
            probe_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM backfill_partition_probe_documents
                """
            ).fetchone()[0]

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            [tuple(row) for row in links],
            [
                ("300114.SZ", "中航电测"),
                ("831152.BJ", "昆工科技"),
            ],
        )
        self.assertEqual(
            [tuple(row) for row in catalog],
            [
                ("300114.SZ", "中航电测"),
                ("831152.BJ", "昆工科技"),
            ],
        )
        self.assertEqual(
            [tuple(row) for row in partitions],
            [
                ("2023-06-21", "pending", 0, 0),
                ("2023-07-01", "pending", 0, 0),
            ],
        )
        self.assertEqual(probe_count, 0)
        self.assertEqual(
            store.source_retry_window(
                "tushare_announcement"
            )["unresolved_day"],
            "2023-07-01",
        )
        self.assertEqual(store.integrity_check(), "ok")

    def test_v6_migration_failure_is_atomic_and_retryable(self) -> None:
        self._create_v5_database()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v6_migration_failure",
        ):
            FailingV6Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            catalog_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table'
                  AND name='announcement_security_catalog'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(version, 5)
        self.assertIsNone(catalog_table)
        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)
