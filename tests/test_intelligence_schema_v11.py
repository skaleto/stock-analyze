from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATIONS, SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"
SOURCE = "tushare_announcement"
CURRENT_COMPLETION_STRATEGY = 3

INVALID_DESCENDANTS = (
    ("failed_retryable", "opaque_retry_state", CURRENT_COMPLETION_STRATEGY),
    ("failed_terminal", "opaque_terminal_state", CURRENT_COMPLETION_STRATEGY),
    (
        "failed_overflow",
        "security_items_incomplete",
        CURRENT_COMPLETION_STRATEGY,
    ),
    ("complete", "opaque_stale_strategy", 0),
)


class FailingV11Store(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del statement
        if version == 11 and statement_index == 2:
            raise RuntimeError("injected_v11_migration_failure")


class IntelligenceSchemaV11Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _connection_at_v10_with_masked_descendant(
        self,
        root: Path,
        *,
        descendant_status: str,
        descendant_error: str,
        descendant_strategy: int,
    ) -> sqlite3.Connection:
        root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(root / "intelligence.sqlite3")
        for version in range(1, 11):
            connection.executescript(MIGRATIONS[version])
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
                "legacy-v10-masked-job",
                SOURCE,
                "2023-06-20",
                "2023-06-21",
                CURRENT_COMPLETION_STRATEGY,
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
                    "opaque_parent_note",
                    NOW,
                    CURRENT_COMPLETION_STRATEGY,
                    0,
                    "config-a",
                    "compat-a",
                    2000,
                    9,
                    "catalog-9",
                    "split_children",
                ),
                (
                    SOURCE,
                    "2023-06-20",
                    "2023-06-20",
                    2000,
                    "complete",
                    "",
                    NOW,
                    CURRENT_COMPLETION_STRATEGY,
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
                    descendant_status,
                    descendant_error,
                    NOW,
                    descendant_strategy,
                    1,
                    "config-a",
                    "compat-a",
                    2000,
                    9,
                    "catalog-9",
                    "saturated_catalog_convergence",
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO backfill_job_partition_refs(
                job_id, source, partition_start, partition_end,
                created_at, evidence_status, association_provenance
            ) VALUES('legacy-v10-masked-job',?,?,?,?,?,?)
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

    def test_v10_any_invalid_probe_descendant_reopens_split_tree(
        self,
    ) -> None:
        for status, error, strategy in INVALID_DESCENDANTS:
            with self.subTest(
                status=status,
                error=error,
                strategy=strategy,
            ):
                root = self.root / f"{status}-{strategy}"
                connection = (
                    self._connection_at_v10_with_masked_descendant(
                        root,
                        descendant_status=status,
                        descendant_error=error,
                        descendant_strategy=strategy,
                    )
                )
                connection.commit()
                connection.close()

                store = IntelligenceStore(
                    root,
                    historical_cutoff=CUTOFF,
                )

                parent = store.backfill_partition(
                    SOURCE,
                    "2023-06-20",
                    "2023-06-21",
                )
                descendant = store.backfill_partition(
                    SOURCE,
                    "2023-06-21",
                    "2023-06-21",
                )
                with store.connect() as migrated:
                    job = migrated.execute(
                        """
                        SELECT status, evidence_status, generation
                        FROM backfill_jobs
                        WHERE job_id='legacy-v10-masked-job'
                        """
                    ).fetchone()
                    refs = migrated.execute(
                        """
                        SELECT partition_start, partition_end,
                               evidence_status
                        FROM backfill_job_partition_refs
                        WHERE job_id='legacy-v10-masked-job'
                        ORDER BY partition_start, partition_end
                        """
                    ).fetchall()
                    verification = migrated.execute(
                        """
                        SELECT stable_rounds, last_probe_hash
                        FROM backfill_partition_verification_state
                        WHERE source=? AND partition_start=?
                          AND partition_end=?
                        """,
                        (SOURCE, "2023-06-21", "2023-06-21"),
                    ).fetchone()

                self.assertEqual(store.schema_version(), SCHEMA_VERSION)
                self.assertEqual(parent["status"], "failed_overflow")
                self.assertEqual(
                    parent["completion_basis"],
                    "split_children_revalidation",
                )
                self.assertEqual(descendant["status"], status)
                self.assertEqual(
                    int(descendant["completion_strategy_version"]),
                    strategy,
                )
                self.assertEqual(
                    tuple(job),
                    ("partial", "needs_revalidation", 1),
                )
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

    def test_v11_generalized_migration_is_atomic_and_retryable(
        self,
    ) -> None:
        root = self.root / "atomic"
        connection = self._connection_at_v10_with_masked_descendant(
            root,
            descendant_status="failed_retryable",
            descendant_error="opaque_retry_state",
            descendant_strategy=CURRENT_COMPLETION_STRATEGY,
        )
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_v11_migration_failure",
        ):
            FailingV11Store(root, historical_cutoff=CUTOFF)

        connection = sqlite3.connect(root / "intelligence.sqlite3")
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
                WHERE job_id='legacy-v10-masked-job'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 10)
        self.assertEqual(parent_status, "complete")
        self.assertEqual(generation, 0)

        recovered = IntelligenceStore(
            root,
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
