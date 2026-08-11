from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATIONS, SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore


class IntelligenceSchemaV14Test(unittest.TestCase):
    def test_v13_store_migrates_to_artifact_worker_lease_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connection = sqlite3.connect(root / "intelligence.sqlite3")
            for version in range(1, 14):
                connection.executescript(MIGRATIONS[version])
                connection.execute(
                    """
                    INSERT INTO schema_meta(version, applied_at)
                    VALUES(?, '2026-07-30T00:00:00+00:00')
                    """,
                    (version,),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES(
                  'historical_cutoff',
                  '2026-07-17T15:59:59+00:00',
                  '2026-07-30T00:00:00+00:00'
                )
                """
            )
            connection.commit()
            connection.close()

            store = IntelligenceStore(root)

            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
            with store.connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                job_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(artifact_worker_jobs)"
                    )
                }
                item_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(artifact_worker_items)"
                    )
                }
                connection.execute(
                    """
                    INSERT INTO artifact_worker_jobs(
                      job_id, worker_id, stage, status,
                      created_at, lease_until
                    ) VALUES(
                      'awj-importing', 'ecs', 'parse', 'importing',
                      '2026-07-30T00:00:00+00:00',
                      '2026-07-30T04:00:00+00:00'
                    )
                    """
                )
                connection.commit()

            self.assertIn("artifact_worker_jobs", tables)
            self.assertIn("artifact_worker_items", tables)
            self.assertTrue(
                {
                    "job_id",
                    "worker_id",
                    "stage",
                    "status",
                    "created_at",
                    "lease_until",
                    "manifest_hash",
                    "result_hash",
                    "counts_json",
                }.issubset(job_columns)
            )
            self.assertTrue(
                {
                    "job_id",
                    "ordinal",
                    "document_id",
                    "input_hash",
                    "status",
                    "error",
                    "updated_at",
                }.issubset(item_columns)
            )


if __name__ == "__main__":
    unittest.main()
