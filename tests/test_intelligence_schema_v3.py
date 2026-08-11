from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATION_V1, MIGRATION_V2, SCHEMA_VERSION
from stock_analyze.intelligence.store import (
    BackfillGenerationConflict,
    BackfillLeaseBusy,
    BackfillProgressRegression,
    IntelligenceStore,
)


CUTOFF = "2026-07-17T15:59:59+00:00"


class FailingV3Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 3 and statement_index == 2:
            raise RuntimeError("injected_v3_migration_failure")


class IntelligenceSchemaV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_v2_database(self) -> None:
        self.root.mkdir(parents=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            connection.executescript(MIGRATION_V1)
            connection.execute(
                "INSERT INTO schema_meta(version, applied_at) VALUES(1, ?)",
                ("2026-07-24T00:00:00+00:00",),
            )
            connection.executescript(MIGRATION_V2)
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (CUTOFF, "2026-07-24T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO schema_meta(version, applied_at) VALUES(2, ?)",
                ("2026-07-24T00:00:01+00:00",),
            )
            connection.execute(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end,
                    status, updated_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                    "pending",
                    "2026-07-24T00:00:00+00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_v2_migrates_to_v3_with_structured_universe_item_tables(self) -> None:
        self._create_v2_database()

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
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(backfill_partitions)"
                )
            }
            partition_count = connection.execute(
                "SELECT COUNT(*) FROM backfill_partitions"
            ).fetchone()[0]
        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertTrue({
            "backfill_universe_snapshots",
            "backfill_universe_members",
            "backfill_partition_universes",
            "backfill_partition_items",
        }.issubset(tables))
        self.assertIn("request_limit", columns)
        self.assertEqual(partition_count, 1)
        self.assertEqual(store.integrity_check(), "ok")

    def test_v3_migration_failure_is_atomic_and_retryable(self) -> None:
        self._create_v2_database()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v3_migration_failure",
        ):
            FailingV3Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='backfill_universe_snapshots'
                """
            ).fetchone()
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(backfill_partitions)"
                )
            }
        finally:
            connection.close()
        self.assertEqual(version, 2)
        self.assertIsNone(table)
        self.assertNotIn("request_limit", columns)

        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)

    def test_partition_generation_lease_and_monotonic_progress_are_cas_guarded(self) -> None:
        store = IntelligenceStore(self.root)
        args = (
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        first = store.start_backfill_partition(
            *args,
            resume=True,
            request_limit=2000,
            lease_seconds=300,
            now="2026-07-24T00:00:00+00:00",
        )
        generation_one = int(first["generation"])

        with self.assertRaises(BackfillLeaseBusy):
            store.start_backfill_partition(
                *args,
                resume=True,
                request_limit=2000,
                lease_seconds=300,
                now="2026-07-24T00:04:59+00:00",
            )

        second = store.start_backfill_partition(
            *args,
            resume=True,
            request_limit=2000,
            lease_seconds=300,
            now="2026-07-24T00:05:01+00:00",
        )
        generation_two = int(second["generation"])
        self.assertGreater(generation_two, generation_one)

        with self.assertRaises(BackfillGenerationConflict):
            store.record_backfill_page(
                *args,
                generation=generation_one,
                next_offset=2000,
                fetched=2000,
                inserted=0,
                b_share_filtered=0,
            )

        store.record_backfill_page(
            *args,
            generation=generation_two,
            next_offset=2000,
            fetched=2000,
            inserted=0,
            b_share_filtered=0,
        )
        with self.assertRaises(BackfillProgressRegression):
            store.record_backfill_page(
                *args,
                generation=generation_two,
                next_offset=1000,
                fetched=0,
                inserted=0,
                b_share_filtered=0,
            )

        store.finish_backfill_partition(
            *args,
            generation=generation_two,
            status="complete",
        )
        with self.assertRaises(BackfillGenerationConflict):
            store.finish_backfill_partition(
                *args,
                generation=generation_one,
                status="failed_retryable",
            )
        self.assertEqual(store.backfill_partition(*args)["status"], "complete")

    def test_item_generation_prevents_stale_worker_completion(self) -> None:
        store = IntelligenceStore(self.root)
        args = (
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        parent = store.start_backfill_partition(
            *args,
            resume=True,
            request_limit=2000,
            now="2026-07-24T00:00:00+00:00",
        )
        store.finish_backfill_partition(
            *args,
            generation=int(parent["generation"]),
            status="failed_overflow",
        )
        store.bind_backfill_universe(
            *args,
            security_codes=("000001.SZ",),
            request_limit=2000,
        )

        first = store.start_backfill_item(
            *args,
            "000001.SZ",
            resume=True,
            request_limit=2000,
            lease_seconds=300,
            now="2026-07-24T00:00:00+00:00",
        )
        with self.assertRaises(BackfillLeaseBusy):
            store.start_backfill_item(
                *args,
                "000001.SZ",
                resume=True,
                request_limit=2000,
                lease_seconds=300,
                now="2026-07-24T00:04:59+00:00",
            )
        second = store.start_backfill_item(
            *args,
            "000001.SZ",
            resume=True,
            request_limit=2000,
            lease_seconds=300,
            now="2026-07-24T00:05:01+00:00",
        )

        with self.assertRaises(BackfillGenerationConflict):
            store.finish_backfill_item(
                *args,
                "000001.SZ",
                generation=int(first["generation"]),
                status="complete",
            )
        store.commit_backfill_item_leaf(
            *args,
            "000001.SZ",
            generation=int(second["generation"]),
            writes=(),
            fetched=0,
            b_share_filtered=0,
        )
        with self.assertRaises(BackfillGenerationConflict):
            store.finish_backfill_item(
                *args,
                "000001.SZ",
                generation=int(first["generation"]),
                status="failed_retryable",
            )
        self.assertEqual(
            store.backfill_partition_items(*args)[0]["status"],
            "complete",
        )


if __name__ == "__main__":
    unittest.main()
