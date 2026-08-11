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
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"


class FailingV8Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 8 and statement_index == 2:
            raise RuntimeError("injected_v8_migration_failure")


class IntelligenceSchemaV8Test(unittest.TestCase):
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
            MIGRATION_V7,
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

    def _create_overlapping_v6_jobs(
        self,
        *,
        old_config: str,
        new_config: str,
    ) -> None:
        connection = self._connection_at_version(6)
        try:
            for (
                job_id,
                config_hash,
                request_limit,
                start_date,
                end_date,
            ) in (
                (
                    "old-job",
                    old_config,
                    1000,
                    "2023-06-01",
                    "2023-06-30",
                ),
                (
                    "new-job",
                    new_config,
                    2000,
                    "2023-06-21",
                    "2023-07-31",
                ),
            ):
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
                        job_id,
                        "tushare_announcement",
                        start_date,
                        end_date,
                        3,
                        config_hash,
                        request_limit,
                        2,
                        "complete",
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
                    "complete",
                    2000,
                    1998,
                    4,
                    NOW,
                    3,
                    1,
                    "new-job",
                ),
            )
            for (
                job_id,
                rounds_total,
                stable_rounds,
                probe_hash,
                updated_at,
            ) in (
                (
                    "new-job",
                    2,
                    2,
                    "new-owner-hash",
                    "2026-07-24T00:00:00+00:00",
                ),
                (
                    "old-job",
                    9,
                    9,
                    "old-later-hash",
                    "2026-07-25T00:00:00+00:00",
                ),
            ):
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
                        job_id,
                        "tushare_announcement",
                        "2023-06-21",
                        "2023-06-21",
                        rounds_total,
                        stable_rounds,
                        probe_hash,
                        0,
                        0,
                        updated_at,
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
                        job_id,
                        "tushare_announcement",
                        "2023-06-21",
                        "2023-06-21",
                        1,
                        probe_hash,
                        2,
                        2,
                        0,
                        0,
                        min(stable_rounds, 2),
                        updated_at,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def test_v6_overlap_preserves_all_refs_and_selects_owner_evidence(self) -> None:
        self._create_overlapping_v6_jobs(
            old_config="config-old",
            new_config="config-new",
        )

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        with store.connect() as connection:
            refs = connection.execute(
                """
                SELECT job_id, evidence_status
                FROM backfill_job_partition_refs
                ORDER BY job_id
                """
            ).fetchall()
            jobs = connection.execute(
                """
                SELECT job_id, status, evidence_status
                FROM backfill_jobs
                ORDER BY job_id
                """
            ).fetchall()
            state = connection.execute(
                """
                SELECT rounds_total, stable_rounds, last_probe_hash
                FROM backfill_partition_verification_state
                """
            ).fetchone()
            rounds = connection.execute(
                """
                SELECT probe_hash
                FROM backfill_verification_rounds
                ORDER BY round_no
                """
            ).fetchall()
            partition = connection.execute(
                """
                SELECT evidence_config_hash, evidence_request_limit
                FROM backfill_partitions
                """
            ).fetchone()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            [tuple(row) for row in refs],
            [
                ("new-job", "exact"),
                ("old-job", "needs_revalidation"),
            ],
        )
        self.assertEqual(
            [tuple(row) for row in jobs],
            [
                ("new-job", "complete", "current"),
                ("old-job", "partial", "needs_revalidation"),
            ],
        )
        self.assertEqual(
            tuple(state),
            (2, 2, "new-owner-hash"),
        )
        self.assertEqual(
            [str(row["probe_hash"]) for row in rounds],
            ["new-owner-hash"],
        )
        self.assertEqual(
            tuple(partition),
            ("config-new", 2000),
        )
        new_progress = store.backfill_job_progress("new-job")
        old_progress = store.backfill_job_progress("old-job")
        self.assertEqual(new_progress["partitions_complete"], 1)
        self.assertEqual(old_progress["partitions_complete"], 0)
        self.assertEqual(old_progress["partitions_needs_revalidation"], 1)

    def test_v6_compatible_overlap_keeps_old_job_progress(self) -> None:
        self._create_overlapping_v6_jobs(
            old_config="config-shared",
            new_config="config-shared",
        )

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        with store.connect() as connection:
            refs = connection.execute(
                """
                SELECT job_id, evidence_status
                FROM backfill_job_partition_refs
                ORDER BY job_id
                """
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in refs],
            [
                ("new-job", "exact"),
                ("old-job", "exact"),
            ],
        )
        self.assertEqual(
            store.backfill_job_progress(
                "old-job"
            )["partitions_complete"],
            1,
        )

    def test_v8_migration_failure_is_atomic_and_retryable(self) -> None:
        connection = self._connection_at_version(7)
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v8_migration_failure",
        ):
            FailingV8Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            catalog_state = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table'
                  AND name='announcement_catalog_state'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(version, 7)
        self.assertIsNone(catalog_state)
        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
