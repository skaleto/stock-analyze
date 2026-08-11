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
    MIGRATION_V9,
    SCHEMA_VERSION,
)
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"
SOURCE = "tushare_announcement"


class FailingV10Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 10 and statement_index == 2:
            raise RuntimeError("injected_v10_migration_failure")


class IntelligenceSchemaV10Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "intelligence"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _connection_at_v8_with_masked_leaf(
        self,
    ) -> sqlite3.Connection:
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
        connection.execute(
            """
            INSERT INTO announcement_catalog_state(
                source, revision, content_hash,
                security_count, updated_at
            ) VALUES(?,?,?,?,?)
            """,
            (SOURCE, 9, "catalog-9", 9, NOW),
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
                "legacy-masked-job",
                SOURCE,
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
        connection.executemany(
            """
            INSERT INTO backfill_partitions(
                source, partition_start, partition_end,
                request_limit, status, error, updated_at,
                completion_strategy_version,
                probe_manifest_version,
                evidence_config_hash,
                evidence_compatibility_hash,
                evidence_request_limit,
                catalog_revision, catalog_hash,
                completion_basis
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (
                    SOURCE,
                    "2023-06-20",
                    "2023-06-21",
                    2000,
                    "complete",
                    "split_complete",
                    NOW,
                    3,
                    0,
                    "config-a",
                    "compat-a",
                    2000,
                    9,
                    "catalog-9",
                    "",
                ),
                (
                    SOURCE,
                    "2023-06-20",
                    "2023-06-20",
                    2000,
                    "complete",
                    "",
                    NOW,
                    3,
                    0,
                    "config-a",
                    "compat-a",
                    2000,
                    0,
                    "",
                    "short_page",
                ),
                (
                    SOURCE,
                    "2023-06-21",
                    "2023-06-21",
                    2000,
                    "failed_overflow",
                    "catalog_growth_revalidation",
                    NOW,
                    0,
                    1,
                    "config-a",
                    "compat-a",
                    2000,
                    7,
                    "catalog-7",
                    "saturated_catalog_revalidation",
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO backfill_job_partition_refs(
                job_id, source, partition_start, partition_end,
                created_at, evidence_status, association_provenance
            ) VALUES('legacy-masked-job',?,?,?,?,?,?)
            """,
            (
                (
                    SOURCE,
                    "2023-06-20",
                    "2023-06-21",
                    NOW,
                    "exact",
                    "runtime_verified",
                ),
                (
                    SOURCE,
                    "2023-06-20",
                    "2023-06-20",
                    NOW,
                    "exact",
                    "runtime_verified",
                ),
                (
                    SOURCE,
                    "2023-06-21",
                    "2023-06-21",
                    NOW,
                    "exact",
                    "runtime_verified",
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO backfill_partition_verification_state(
                source, partition_start, partition_end,
                rounds_total, stable_rounds, last_probe_hash,
                last_new_documents, last_new_security_codes,
                updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                SOURCE,
                "2023-06-21",
                "2023-06-21",
                5,
                5,
                "stale-stable-hash",
                0,
                0,
                NOW,
            ),
        )
        return connection

    def test_v8_masked_failed_leaf_reopens_ancestors_and_jobs(
        self,
    ) -> None:
        connection = self._connection_at_v8_with_masked_leaf()
        connection.commit()
        connection.close()

        store = IntelligenceStore(self.root, historical_cutoff=CUTOFF)

        parent = store.backfill_partition(
            SOURCE,
            "2023-06-20",
            "2023-06-21",
        )
        leaf = store.backfill_partition(
            SOURCE,
            "2023-06-21",
            "2023-06-21",
        )
        with store.connect() as connection:
            job = connection.execute(
                """
                SELECT status, evidence_status, generation
                FROM backfill_jobs
                WHERE job_id='legacy-masked-job'
                """
            ).fetchone()
            refs = connection.execute(
                """
                SELECT partition_start, partition_end, evidence_status
                FROM backfill_job_partition_refs
                WHERE job_id='legacy-masked-job'
                ORDER BY partition_start, partition_end
                """
            ).fetchall()
            verification = connection.execute(
                """
                SELECT stable_rounds, last_probe_hash
                FROM backfill_partition_verification_state
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (SOURCE, "2023-06-21", "2023-06-21"),
            ).fetchone()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(parent["status"], "failed_overflow")
        self.assertEqual(
            parent["completion_basis"],
            "split_children_revalidation",
        )
        self.assertEqual(parent["catalog_revision"], 7)
        self.assertEqual(parent["catalog_hash"], "catalog-7")
        self.assertEqual(leaf["status"], "failed_overflow")
        self.assertEqual(tuple(job), ("partial", "needs_revalidation", 1))
        self.assertEqual(
            [
                (
                    row["partition_start"],
                    row["partition_end"],
                    row["evidence_status"],
                )
                for row in refs
            ],
            [
                ("2023-06-20", "2023-06-20", "exact"),
                (
                    "2023-06-20",
                    "2023-06-21",
                    "needs_revalidation",
                ),
                (
                    "2023-06-21",
                    "2023-06-21",
                    "needs_revalidation",
                ),
            ],
        )
        self.assertEqual(tuple(verification), (0, ""))

    def test_v10_masked_leaf_migration_is_atomic_and_retryable(
        self,
    ) -> None:
        connection = self._connection_at_v8_with_masked_leaf()
        connection.executescript(MIGRATION_V9)
        connection.execute(
            """
            INSERT INTO schema_meta(version, applied_at)
            VALUES(9, ?)
            """,
            (NOW,),
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v10_migration_failure",
        ):
            FailingV10Store(self.root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            parent_status = connection.execute(
                """
                SELECT status
                FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (SOURCE, "2023-06-20", "2023-06-21"),
            ).fetchone()[0]
            generation = connection.execute(
                """
                SELECT generation
                FROM backfill_jobs
                WHERE job_id='legacy-masked-job'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 9)
        self.assertEqual(parent_status, "complete")
        self.assertEqual(generation, 0)

        recovered = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            recovered.backfill_partition(
                SOURCE,
                "2023-06-20",
                "2023-06-21",
            )["status"],
            "failed_overflow",
        )


if __name__ == "__main__":
    unittest.main()
