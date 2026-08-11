from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class SemanticRepairStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = IntelligenceStore(self.root)
        self.document_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id="repair-doc",
                title="修复测试公告",
                published_at="2026-07-28T01:00:00+00:00",
                first_seen_at="2026-07-28T01:01:00+00:00",
                effective_at="2026-07-28T01:01:00+00:00",
                source_url="https://example.test/repair.pdf",
                content=b"repair",
                metadata={"ts_code": "000001.SZ"},
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_run_and_event(self, prompt: str, event_id: str) -> str:
        claim = self.store.claim_semantic_run(
            document_id=self.document_id,
            artifact_hash=("a" if prompt == "v5" else "b") * 64,
            provider="declared:codex",
            model="codex-test",
            prompt_version=prompt,
            schema_version="announcement-events-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v1",
            parser_version="announcement-layout-v1",
            input_hash=("c" if prompt == "v5" else "d") * 64,
        )
        self.store.finish_semantic_run(
            claim["run_id"],
            status="succeeded",
            output_hash=("e" if prompt == "v5" else "f") * 64,
            output_uri=f"localblob://{prompt}/output.json",
        )
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO events(
                    event_id, document_id, event_type, direction, strength,
                    confidence, novelty, horizon_days, published_at,
                    effective_at, evidence, extraction_method, metadata_json
                ) VALUES(?, ?, 'buyback', 0.1, 0.2, 0.8, 0.7, 20,
                         '2026-07-28T01:00:00+00:00',
                         '2026-07-28T01:01:00+00:00', 'evidence',
                         'semantic-v1-validated', ?)
                """,
                (event_id, self.document_id, json.dumps({"market": "a_share"})),
            )
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index,
                    event_type, lifecycle, payload_json, validation_status,
                    validation_errors_json, canonical_event_id, created_at
                ) VALUES(?, ?, ?, 0, 'buyback', 'approved', '{}',
                         'canonical', '[]', ?,
                         '2026-07-28T01:02:00+00:00')
                """,
                (f"candidate-{event_id}", claim["run_id"], self.document_id, event_id),
            )
        return str(claim["run_id"])

    def test_repair_activation_and_rollback_switch_active_event_without_delete(self) -> None:
        old_run = self._seed_run_and_event("v5", "event-old")
        new_run = self._seed_run_and_event("v6", "event-new")

        before = self.store.events_as_of(
            "2026-07-29T00:00:00+00:00",
            market="a_share",
        )
        self.assertEqual(set(before["event_id"]), {"event-old", "event-new"})

        activated = self.store.activate_semantic_repair(
            repair_id="repair-test",
            document_id=self.document_id,
            replacement_run_id=new_run,
            superseded_run_ids=[old_run],
            reason="unit-and-grounding-fix",
        )
        self.assertEqual(activated["activated"], 1)
        active = self.store.events_as_of(
            "2026-07-29T00:00:00+00:00",
            market="a_share",
        )
        self.assertEqual(list(active["event_id"]), ["event-new"])

        rolled_back = self.store.rollback_semantic_repair("repair-test")
        self.assertEqual(rolled_back["rolled_back"], 1)
        restored = self.store.events_as_of(
            "2026-07-29T00:00:00+00:00",
            market="a_share",
        )
        self.assertEqual(list(restored["event_id"]), ["event-old"])
        with self.store.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                2,
            )

    def test_older_repair_cannot_replace_a_newer_active_repair(self) -> None:
        old_run = self._seed_run_and_event("v5", "event-old")
        active_run = self._seed_run_and_event("v6", "event-active")
        stale_run = self._seed_run_and_event("v7", "event-stale")

        self.store.activate_semantic_repair(
            repair_id="repair-active",
            document_id=self.document_id,
            replacement_run_id=active_run,
            superseded_run_ids=[old_run],
            reason="newer-reviewed-repair",
        )
        stale = self.store.activate_semantic_repair(
            repair_id="repair-stale",
            document_id=self.document_id,
            replacement_run_id=stale_run,
            superseded_run_ids=[old_run],
            reason="older-job-imported-late",
        )

        self.assertEqual(stale["activated"], 0)
        self.assertEqual(stale["conflicted"], 1)
        visible = self.store.events_as_of(
            "2026-07-29T00:00:00+00:00",
            market="a_share",
        )
        self.assertEqual(list(visible["event_id"]), ["event-active"])
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT repair_id, replacement_run_id, status
                FROM semantic_run_replacements
                ORDER BY repair_id
                """
            ).fetchall()
        self.assertEqual(
            [(row["repair_id"], row["replacement_run_id"], row["status"]) for row in rows],
            [
                ("repair-active", active_run, "active"),
                ("repair-stale", stale_run, "rolled_back"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
