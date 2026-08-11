from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATIONS, SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore


class IntelligenceSchemaV16Test(unittest.TestCase):
    def test_v15_store_migrates_to_semantic_execution_state_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            connection = sqlite3.connect(root / "intelligence.sqlite3")
            for version in range(1, 16):
                connection.executescript(MIGRATIONS[version])
                connection.execute(
                    "INSERT INTO schema_meta(version, applied_at) VALUES(?, ?)",
                    (version, "2026-08-09T00:00:00+00:00"),
                )
            connection.execute(
                """
                INSERT INTO intelligence_settings(key, value, created_at)
                VALUES('historical_cutoff', ?, ?)
                """,
                (
                    "2026-07-17T15:59:59+00:00",
                    "2026-08-09T00:00:00+00:00",
                ),
            )
            connection.commit()
            connection.close()

            store = IntelligenceStore(root)

            self.assertEqual(SCHEMA_VERSION, 16)
            self.assertEqual(store.schema_version(), 16)
            with store.connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertTrue(
                {
                    "semantic_contract_profiles",
                    "semantic_executor_bindings",
                    "semantic_tasks",
                    "semantic_execution_jobs",
                }.issubset(tables)
            )


if __name__ == "__main__":
    unittest.main()
