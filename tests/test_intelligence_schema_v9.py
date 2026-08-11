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
    MIGRATION_V6,
    MIGRATION_V7,
    MIGRATION_V8,
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"


class FailingV9Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 9 and statement_index == 2:
            raise RuntimeError("injected_v9_migration_failure")


class IntelligenceSchemaV9Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _connection_at_v8(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        for version, migration in enumerate(
            (
                MIGRATION_V1,
                MIGRATION_V2,
                MIGRATION_V3,
                MIGRATION_V4,
                MIGRATION_V5,
                MIGRATION_V6,
                MIGRATION_V7,
                MIGRATION_V8,
            ),
            start=1,
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
        return connection

    def test_v8_split_parent_inherits_topology_and_catalog_watermark(
        self,
    ) -> None:
        connection = self._connection_at_v8()
        try:
            connection.execute(
                """
                INSERT INTO announcement_catalog_state(
                    source, revision, content_hash,
                    security_count, updated_at
                ) VALUES(?,?,?,?,?)
                """,
                ("tushare_announcement", 9, "catalog-9", 9, NOW),
            )
            connection.execute(
                """
                INSERT INTO backfill_jobs(
                    job_id, source, start_date, end_date,
                    completion_strategy_version, config_hash,
                    request_limit, verification_required,
                    status, created_at, updated_at,
                    exact_config_hash, compatibility_hash,
                    config_json, evidence_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-v8-job",
                    "tushare_announcement",
                    "2023-06-20",
                    "2023-06-21",
                    3,
                    "config-a",
                    2000,
                    5,
                    "complete",
                    NOW,
                    NOW,
                    "config-a",
                    "compat-a",
                    "{}",
                    "current",
                ),
            )
            rows = (
                (
                    "2023-06-20",
                    "2023-06-21",
                    0,
                    "",
                    "",
                    "",
                ),
                (
                    "2023-06-20",
                    "2023-06-20",
                    0,
                    "",
                    "short_page",
                    "",
                ),
                (
                    "2023-06-21",
                    "2023-06-21",
                    7,
                    "catalog-7",
                    "saturated_catalog_convergence",
                    "",
                ),
            )
            connection.executemany(
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end,
                    request_limit, status, updated_at,
                    completion_strategy_version,
                    evidence_config_hash,
                    evidence_compatibility_hash,
                    evidence_request_limit,
                    catalog_revision, catalog_hash,
                    completion_basis, error
                ) VALUES(
                    'tushare_announcement', ?, ?, 2000,
                    'complete', ?, 3, 'config-a',
                    'compat-a', 2000, ?, ?, ?, ?
                )
                """,
                [
                    (
                        start_date,
                        end_date,
                        NOW,
                        revision,
                        catalog_hash,
                        completion_basis,
                        error,
                    )
                    for (
                        start_date,
                        end_date,
                        revision,
                        catalog_hash,
                        completion_basis,
                        error,
                    ) in rows
                ],
            )
            connection.commit()
        finally:
            connection.close()

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        parent = store.backfill_partition(
            "tushare_announcement",
            "2023-06-20",
            "2023-06-21",
        )
        with store.connect() as connection:
            generation = connection.execute(
                """
                SELECT generation FROM backfill_jobs
                WHERE job_id='legacy-v8-job'
                """
            ).fetchone()["generation"]
        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(parent["completion_basis"], "split_children")
        self.assertEqual(parent["catalog_revision"], 7)
        self.assertEqual(parent["catalog_hash"], "catalog-7")
        self.assertEqual(generation, 0)

    def test_v9_migration_failure_is_atomic_and_retryable(self) -> None:
        connection = self._connection_at_v8()
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v9_migration_failure",
        ):
            FailingV9Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            job_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(backfill_jobs)"
                )
            }
        finally:
            connection.close()
        self.assertEqual(version, 8)
        self.assertNotIn("generation", job_columns)

        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
