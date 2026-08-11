from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.schema import MIGRATIONS, SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore


CUTOFF = "2026-07-17T15:59:59+00:00"
NOW = "2026-07-25T00:00:00+00:00"


class IntelligenceSchemaV12Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _v11_store_with_legacy_raw_files(self) -> tuple[Path, Path]:
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        for version in range(1, 12):
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

        inline_payload = b"legacy-title-metadata"
        inline_hash = hashlib.sha256(inline_payload).hexdigest()
        inline_relative = Path(
            "raw/tushare_announcement/2026/07"
        ) / inline_hash
        inline_path = self.root / inline_relative
        inline_path.parent.mkdir(parents=True, exist_ok=True)
        inline_path.write_bytes(inline_payload)

        retained_payload = b"licensed-full-text"
        retained_hash = hashlib.sha256(retained_payload).hexdigest()
        retained_relative = Path(
            "raw/tushare_announcement/2026/07"
        ) / retained_hash
        retained_path = self.root / retained_relative
        retained_path.write_bytes(retained_payload)

        rows = (
            (
                "legacy-inline",
                inline_hash,
                str(inline_relative),
                {"content_scope": "title_metadata"},
            ),
            (
                "retained-full-text",
                retained_hash,
                str(retained_relative),
                {"content_scope": "licensed_full_text"},
            ),
        )
        connection.executemany(
            """
            INSERT INTO documents(
                source, source_id, title, published_at, first_seen_at,
                effective_at, source_url, mime_type, content_hash,
                raw_path, metadata_json
            ) VALUES(
                'tushare_announcement', ?, 'title', ?, ?, ?,
                'https://example.test/notice', 'text/plain', ?, ?, ?
            )
            """,
            (
                (
                    source_id,
                    NOW,
                    NOW,
                    NOW,
                    content_hash,
                    raw_path,
                    json.dumps(metadata),
                )
                for source_id, content_hash, raw_path, metadata in rows
            ),
        )
        connection.commit()
        connection.close()
        return inline_path, retained_path

    def test_v12_inlines_title_metadata_and_prunes_only_orphans(
        self,
    ) -> None:
        inline_path, retained_path = (
            self._v11_store_with_legacy_raw_files()
        )

        store = IntelligenceStore(
            self.root,
            historical_cutoff=CUTOFF,
        )
        with store.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, raw_path
                FROM documents
                ORDER BY source_id
                """
            ).fetchall()

            self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(
            [(row["source_id"], row["raw_path"]) for row in rows],
            [
                ("legacy-inline", ""),
                (
                    "retained-full-text",
                    str(retained_path.relative_to(self.root)),
                ),
            ],
        )

        result = store.prune_unreferenced_raw_files(
            source="tushare_announcement"
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(
            result["deleted_bytes"],
            len(b"legacy-title-metadata"),
        )
        self.assertEqual(result["retained_files"], 1)
        self.assertFalse(inline_path.exists())
        self.assertTrue(retained_path.exists())


if __name__ == "__main__":
    unittest.main()
