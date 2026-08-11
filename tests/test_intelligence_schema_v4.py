from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from stock_analyze.intelligence.backfill import AnnouncementBackfill
from stock_analyze.intelligence.schema import (
    MIGRATION_V1,
    MIGRATION_V2,
    MIGRATION_V3,
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.sources.official import (
    TushareAnnouncementAdapter,
)
from stock_analyze.intelligence.store import (
    BACKFILL_COMPLETION_STRATEGY_VERSION,
    IntelligenceStore,
)


CUTOFF = "2026-07-17T15:59:59+00:00"


class FailingV4Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 4 and statement_index == 2:
            raise RuntimeError("injected_v4_migration_failure")


class IntelligenceSchemaV4Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_v3_database(self) -> None:
        self.root.mkdir(parents=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            for version, migration in (
                (1, MIGRATION_V1),
                (2, MIGRATION_V2),
                (3, MIGRATION_V3),
            ):
                connection.executescript(migration)
                connection.execute(
                    """
                    INSERT INTO schema_meta(version, applied_at)
                    VALUES(?, ?)
                    """,
                    (version, f"2026-07-24T00:00:0{version}+00:00"),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (CUTOFF, "2026-07-24T00:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end, next_offset,
                    request_limit, status, fetched, inserted, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    4000,
                    0,
                    "complete",
                    4000,
                    0,
                    "2026-07-24T00:00:00+00:00",
                ),
            )
            for source_id, ingestion_mode in (
                ("history", "history"),
                ("live", "live"),
            ):
                connection.execute(
                    """
                    INSERT INTO documents(
                        source, source_id, title, published_at,
                        first_seen_at, effective_at, source_url,
                        mime_type, content_hash, raw_path, metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "tushare_announcement",
                        source_id,
                        source_id,
                        "2026-07-01T00:00:00+00:00",
                        "2026-07-24T00:00:00+00:00",
                        "2026-07-01T00:00:00+00:00",
                        "",
                        "text/plain",
                        f"hash-{source_id}",
                        f"raw/{source_id}.txt",
                        f'{{"ingestion_mode":"{ingestion_mode}"}}',
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def test_v3_migrates_to_v4_and_invalidates_legacy_completion(self) -> None:
        self._create_v3_database()

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
            document_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(documents)"
                )
            }
            member_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(backfill_universe_members)"
                )
            }
            partition_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(backfill_partitions)"
                )
            }
            probe_primary_key = [
                str(row["name"])
                for row in sorted(
                    connection.execute(
                        """
                        PRAGMA table_info(
                          backfill_partition_probe_documents
                        )
                        """
                    ).fetchall(),
                    key=lambda row: int(row["pk"]),
                )
                if int(row["pk"]) > 0
            ]
            partition = connection.execute(
                "SELECT * FROM backfill_partitions"
            ).fetchone()
            queue_rows = connection.execute(
                """
                SELECT source_id, queue_priority, live_observed
                FROM documents ORDER BY source_id
                """
            ).fetchall()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertIn("backfill_partition_probe_documents", tables)
        self.assertTrue(
            {"queue_priority", "live_observed"}.issubset(document_columns)
        )
        self.assertTrue(
            {
                "security_type",
                "list_date",
                "delist_date",
                "listing_status",
            }.issubset(member_columns)
        )
        self.assertIn(
            "completion_strategy_version",
            partition_columns,
        )
        self.assertIn("probe_manifest_version", partition_columns)
        self.assertEqual(
            probe_primary_key,
            [
                "source",
                "partition_start",
                "partition_end",
                "source_id",
                "content_hash",
                "ts_code",
            ],
        )
        self.assertEqual(partition["status"], "pending")
        self.assertEqual(partition["next_offset"], 0)
        self.assertEqual(partition["fetched"], 0)
        self.assertEqual(partition["inserted"], 0)
        self.assertEqual(
            [tuple(row) for row in queue_rows],
            [("history", 10, 0), ("live", 100, 1)],
        )
        self.assertEqual(store.integrity_check(), "ok")

    def test_v4_migration_failure_is_atomic_and_retryable(self) -> None:
        self._create_v3_database()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v4_migration_failure",
        ):
            FailingV4Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            document_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(documents)"
                )
            }
            probe_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table'
                  AND name='backfill_partition_probe_documents'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(version, 3)
        self.assertNotIn("queue_priority", document_columns)
        self.assertIsNone(probe_table)

        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)

    def test_legacy_complete_partition_is_requeried_before_new_completion(self) -> None:
        self._create_v3_database()
        store = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )

        class EmptyAnnouncementClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def anns_d(self, **kwargs):
                self.calls.append(dict(kwargs))
                return pd.DataFrame(columns=[
                    "ann_date",
                    "ts_code",
                    "name",
                    "title",
                    "url",
                    "rec_time",
                ])

        client = EmptyAnnouncementClient()
        coordinator = AnnouncementBackfill(
            store=store,
            adapter=TushareAnnouncementAdapter(
                client,
                enabled=True,
                page_size=2000,
            ),
        )

        result = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["offset"], 0)
        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(partition["fetched"], 0)
        self.assertEqual(partition["inserted"], 0)
        self.assertEqual(
            partition["completion_strategy_version"],
            BACKFILL_COMPLETION_STRATEGY_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
