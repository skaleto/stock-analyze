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
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"


class FailingV5Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 5 and statement_index == 2:
            raise RuntimeError("injected_v5_migration_failure")


class IntelligenceSchemaV5Test(unittest.TestCase):
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
                    (version, f"2026-07-25T00:00:0{version}+00:00"),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (CUTOFF, "2026-07-25T00:00:00+00:00"),
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
                    "shared-pdf",
                    "Shared announcement",
                    "2026-07-01T00:00:00+00:00",
                    "2026-07-25T00:00:00+00:00",
                    "2026-07-01T00:00:00+00:00",
                    "https://example.test/shared.pdf",
                    "text/plain",
                    "hash-shared",
                    "raw/shared.txt",
                    (
                        '{"security_codes":["159756.SZ","300114.SZ"],'
                        '"security_links":['
                        '{"ts_code":"300114.SZ","name":"中航电测"},'
                        '{"ts_code":"159756.SZ","name":"互联网ETF"}]}'
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
                (
                    document_id,
                    None,
                    "2026-07-25T00:00:00+00:00",
                    "observed",
                    CUTOFF,
                    "2026-07-25T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end,
                    request_limit, status, fetched, inserted,
                    attempts, updated_at, completion_strategy_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    2000,
                    "complete",
                    2,
                    2,
                    1,
                    "2026-07-25T00:00:00+00:00",
                    2,
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_universe_snapshots(
                    snapshot_id, source, content_hash, security_count,
                    list_statuses, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    "legacy-active-filtered",
                    "tushare_announcement",
                    "legacy-hash",
                    1,
                    '["stock:L"]',
                    "2026-07-25T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_universe_members(
                    snapshot_id, ordinal, ts_code, security_type,
                    list_date, delist_date, listing_status
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    "legacy-active-filtered",
                    0,
                    "000001.SZ",
                    "stock",
                    "19910403",
                    "",
                    "L",
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partition_universes(
                    source, partition_start, partition_end,
                    snapshot_id, created_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    "legacy-active-filtered",
                    "2026-07-25T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partition_items(
                    source, partition_start, partition_end, snapshot_id,
                    ts_code, request_limit, status, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    "legacy-active-filtered",
                    "000001.SZ",
                    2000,
                    "complete",
                    "2026-07-25T00:00:00+00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_v4_migrates_to_v5_with_retry_links_and_frozen_universe_tables(self) -> None:
        self._create_v4_database()

        store = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )

        with store.connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            links = connection.execute(
                """
                SELECT ts_code, name, provenance
                FROM document_security_links ORDER BY ts_code
                """
            ).fetchall()
            partition = connection.execute(
                "SELECT * FROM backfill_partitions"
            ).fetchone()
            old_items = connection.execute(
                "SELECT COUNT(*) FROM backfill_partition_items"
            ).fetchone()[0]

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertIn("source_retry_windows", tables)
        self.assertIn("document_security_links", tables)
        self.assertIn("backfill_source_universes", tables)
        self.assertEqual(
            [tuple(row) for row in links],
            [
                ("159756.SZ", "互联网ETF", "legacy_metadata"),
                ("300114.SZ", "中航电测", "legacy_metadata"),
            ],
        )
        self.assertEqual(partition["status"], "pending")
        self.assertEqual(partition["fetched"], 0)
        self.assertEqual(partition["inserted"], 0)
        self.assertEqual(old_items, 0)
        self.assertEqual(store.integrity_check(), "ok")

    def test_v5_migration_failure_is_atomic_and_retryable(self) -> None:
        self._create_v4_database()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v5_migration_failure",
        ):
            FailingV5Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            retry_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='source_retry_windows'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(version, 4)
        self.assertIsNone(retry_table)
        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)
