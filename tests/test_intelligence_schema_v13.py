from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATIONS, SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore


class IntelligenceSchemaV13Test(unittest.TestCase):
    def test_v12_store_migrates_to_cross_source_audit_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connection = sqlite3.connect(root / "intelligence.sqlite3")
            for version in range(1, 13):
                connection.executescript(MIGRATIONS[version])
                connection.execute(
                    """
                    INSERT INTO schema_meta(version, applied_at)
                    VALUES(?, '2026-07-27T00:00:00+00:00')
                    """,
                    (version,),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES(
                  'historical_cutoff',
                  '2026-07-17T15:59:59+00:00',
                  '2026-07-27T00:00:00+00:00'
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
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='table'
                        """
                    )
                }
                run_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(source_audit_runs)"
                    )
                }
                item_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(source_audit_items)"
                    )
                }

            self.assertIn("source_audit_runs", tables)
            self.assertIn("source_audit_items", tables)
            self.assertTrue(
                {
                    "run_id",
                    "as_of_date",
                    "status",
                    "metrics_json",
                    "started_at",
                    "finished_at",
                }.issubset(run_columns)
            )
            self.assertTrue(
                {
                    "run_id",
                    "dataset",
                    "item_key",
                    "comparison_status",
                    "detail_json",
                }.issubset(item_columns)
            )


if __name__ == "__main__":
    unittest.main()
