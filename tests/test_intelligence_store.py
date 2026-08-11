from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence import IntelligenceStore, MarketEvent, SourceDocument


class IntelligenceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def document(self, content: bytes = b"first", **overrides) -> SourceDocument:
        values = {
            "source": "cninfo",
            "source_id": "notice-1",
            "title": "Company buyback",
            "published_at": "2026-07-18T09:00:00+08:00",
            "first_seen_at": "2026-07-18T09:01:00+08:00",
            "effective_at": "2026-07-18T09:01:00+08:00",
            "source_url": "https://example.test/1",
            "content": content,
        }
        values.update(overrides)
        return SourceDocument(**values)

    def test_schema_is_wal_and_integrity_is_ok(self) -> None:
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 30_000)
        self.assertEqual(self.store.integrity_check(), "ok")
        self.assertEqual(self.store.quick_integrity_check(), "ok")

    def test_document_is_idempotent_and_revision_is_preserved(self) -> None:
        first_id, inserted = self.store.insert_document(self.document())
        repeated_id, repeated = self.store.insert_document(self.document())
        revised_id, revised = self.store.insert_document(
            self.document(b"corrected", revised_at="2026-07-18T10:00:00+08:00", revision_of="notice-1")
        )
        self.assertTrue(inserted)
        self.assertFalse(repeated)
        self.assertEqual(first_id, repeated_id)
        self.assertTrue(revised)
        self.assertNotEqual(first_id, revised_id)
        self.assertEqual(len(self.store.documents()), 2)

    def test_tushare_metadata_is_inline_without_one_raw_file_per_notice(self) -> None:
        document_id, inserted = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="announcement-1",
                content=b"tushare_announcement|announcement-1|https://x",
                metadata={
                    "content_scope": "title_metadata",
                    "ingestion_mode": "history",
                    "ts_code": "000001.SZ",
                },
            )
        )

        self.assertTrue(inserted)
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id=?",
                (document_id,),
            ).fetchone()
        self.assertEqual(row["raw_path"], "")
        self.assertEqual(self.store.document_content(row), b"")
        self.assertEqual(
            [path for path in self.store.raw_root.rglob("*") if path.is_file()],
            [],
        )

    def test_title_metadata_url_change_reuses_canonical_document(self) -> None:
        first_id, first_inserted = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="1225438134",
                source_url=(
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=002520&announcementId=1225438134"
                ),
                content=b"first-url-order",
                metadata={
                    "content_scope": "title_metadata",
                    "provider": "tushare",
                    "ts_code": "002520.SZ",
                    "rec_time": "",
                },
            )
        )
        repeated_id, repeated_inserted = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="1225438134",
                source_url=(
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "announcementId=1225438134&stockCode=002520"
                ),
                content=b"second-url-order",
                first_seen_at="2026-07-18T10:01:00+08:00",
                metadata={
                    "content_scope": "title_metadata",
                    "provider": "tushare",
                    "ts_code": "002520.SZ",
                    "rec_time": "2026-07-18 09:31:45",
                },
            )
        )

        self.assertTrue(first_inserted)
        self.assertFalse(repeated_inserted)
        self.assertEqual(first_id, repeated_id)
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_url, metadata_json
                FROM documents
                WHERE source='tushare_announcement'
                  AND source_id='1225438134'
                """
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn(
            "announcementId=1225438134&stockCode=002520",
            rows[0]["source_url"],
        )
        self.assertIn("2026-07-18 09:31:45", rows[0]["metadata_json"])

    def test_event_visibility_respects_effective_at(self) -> None:
        document_id, _ = self.store.insert_document(self.document())
        self.store.insert_event(
            MarketEvent(
                event_id="event-1", document_id=document_id, event_type="buyback",
                direction=1, strength=0.8, confidence=0.9, novelty=1,
                horizon_days=20, published_at="2026-07-18T01:00:00Z",
                effective_at="2026-07-21T01:00:00Z", evidence="buyback plan",
                entities=({"entity_id": "000001", "entity_name": "Ping An Bank", "confidence": 1},),
            )
        )
        self.assertTrue(self.store.events_as_of("2026-07-20T23:00:00Z").empty)
        visible = self.store.events_as_of("2026-07-21T02:00:00Z")
        self.assertEqual(visible.iloc[0]["entity_id"], "000001")
        explicit_observed = self.store.events_as_of(
            "2026-07-21T02:00:00Z",
            availability_policy="observed",
        )
        self.assertEqual(
            visible["event_id"].tolist(),
            explicit_observed["event_id"].tolist(),
        )

    def test_unknown_availability_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^unknown_availability_policy:future$",
        ):
            self.store.events_as_of(
                "2026-07-21T02:00:00Z",
                availability_policy="future",
            )

    def test_event_is_not_visible_before_document_was_first_seen(self) -> None:
        document_id, _ = self.store.insert_document(
            self.document(
                published_at="2023-01-02T01:00:00Z",
                effective_at="2023-01-02T01:00:00Z",
                first_seen_at="2026-07-18T01:00:00Z",
            )
        )
        self.store.insert_event(
            MarketEvent(
                event_id="event-late-backfill", document_id=document_id,
                event_type="industry_support", direction=1, strength=0.7,
                confidence=0.8, novelty=1, horizon_days=20,
                published_at="2023-01-02T01:00:00Z",
                effective_at="2023-01-02T01:00:00Z",
                evidence="historical policy collected during a later backfill",
            )
        )

        self.assertTrue(self.store.events_as_of("2024-01-01T00:00:00Z").empty)
        visible = self.store.events_as_of("2026-07-18T01:00:00Z")
        self.assertEqual(visible.iloc[0]["event_id"], "event-late-backfill")
        self.assertEqual(visible.iloc[0]["available_at"], "2026-07-18T01:00:00+00:00")

    def test_cursor_advances_only_after_success(self) -> None:
        self.store.start_run("r1", "cninfo")
        self.store.finish_run("r1", status="failed", cursor="page-2")
        self.assertEqual(self.store.cursor("cninfo"), "")
        self.store.start_run("r2", "cninfo")
        self.store.finish_run("r2", status="success", cursor="page-2", fetched=1)
        self.assertEqual(self.store.cursor("cninfo"), "page-2")

    def test_successful_empty_run_does_not_advance_cursor(self) -> None:
        self.store.start_run("r1", "cninfo")
        self.store.finish_run("r1", status="success", cursor="page-2", fetched=0)
        self.assertEqual(self.store.cursor("cninfo"), "")

    def test_historical_backfill_never_regresses_source_cursor(self) -> None:
        self.store.start_run("latest", "tushare_announcement")
        self.store.finish_run(
            "latest",
            status="success",
            cursor="2026-07-24T07:25:00+00:00",
            fetched=1,
        )
        self.store.start_run("backfill", "tushare_announcement")
        self.store.finish_run(
            "backfill",
            status="success",
            cursor="2026-07-22T15:59:59+00:00",
            fetched=100,
        )

        self.assertEqual(
            self.store.cursor("tushare_announcement"),
            "2026-07-24T07:25:00+00:00",
        )

    def test_known_fingerprints_are_loaded_for_cross_source_novelty(self) -> None:
        document_id, _ = self.store.insert_document(self.document())
        self.store.insert_event(
            MarketEvent(
                event_id="event-fingerprint", document_id=document_id,
                event_type="buyback", direction=1, strength=0.8,
                confidence=0.9, novelty=0.5, horizon_days=20,
                published_at="2026-07-18T01:00:00Z",
                effective_at="2026-07-18T01:00:00Z", evidence="buyback",
                document_fingerprint="doc-hash", event_fingerprint="event-hash",
                metadata={
                    "document_fingerprint": "doc-hash",
                    "event_fingerprint": "event-hash",
                },
            )
        )

        self.assertEqual(
            self.store.known_fingerprints(),
            {"doc-hash", "event-hash"},
        )

    def test_pending_documents_prioritize_live_over_history_backlog(self) -> None:
        self.store.insert_document(
            self.document(
                source_id="history-old",
                published_at="1990-12-19T00:00:00+08:00",
                effective_at="1990-12-19T00:00:00+08:00",
                metadata={"ingestion_mode": "history"},
            )
        )
        self.store.insert_document(
            self.document(
                source_id="live-today",
                published_at="2026-07-24T09:30:00+08:00",
                effective_at="2026-07-24T09:30:00+08:00",
                metadata={"ingestion_mode": "live"},
            )
        )

        pending = self.store.pending_documents(limit=1)

        self.assertEqual(pending[0]["source_id"], "live-today")

    def test_live_duplicate_promotes_history_queue_without_losing_provenance(self) -> None:
        history = self.document(
            source="tushare_announcement",
            source_id="same-announcement",
            metadata={
                "ingestion_mode": "history",
                "history_provenance": "full_backfill",
            },
        )
        document_id, inserted = self.store.insert_document(history)
        self.assertTrue(inserted)
        self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="older-history-backlog",
                published_at="1990-12-19T00:00:00+08:00",
                effective_at="1990-12-19T00:00:00+08:00",
                metadata={"ingestion_mode": "history"},
            )
        )

        repeated_id, repeated = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="same-announcement",
                first_seen_at="2026-07-25T09:01:00+08:00",
                metadata={"ingestion_mode": "live"},
            )
        )

        self.assertFalse(repeated)
        self.assertEqual(repeated_id, document_id)
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT queue_priority, live_observed, metadata_json
                FROM documents WHERE id=?
                """,
                (document_id,),
            ).fetchone()
        self.assertEqual(row["queue_priority"], 100)
        self.assertEqual(row["live_observed"], 1)
        self.assertEqual(
            json.loads(row["metadata_json"]),
            {
                "history_provenance": "full_backfill",
                "ingestion_mode": "history",
            },
        )
        self.assertEqual(
            self.store.pending_documents(limit=1)[0]["source_id"],
            "same-announcement",
        )

    def test_security_link_merge_is_independent_of_arrival_order(self) -> None:
        def run_order(root: Path, order: tuple[SourceDocument, ...]):
            store = IntelligenceStore(root)
            document_id = -1
            for document in order:
                document_id, _ = store.insert_document(document)
            link = store.document_security_links(document_id)[0]
            with store.connect() as connection:
                catalog = connection.execute(
                    """
                    SELECT ts_code, name, provenance,
                           first_seen_at, last_seen_at
                    FROM announcement_security_catalog
                    WHERE source='tushare_announcement'
                    """
                ).fetchone()
            return (
                link["ts_code"],
                link["name"],
                link["provenance"],
                link["created_at"],
                link["updated_at"],
                tuple(catalog),
            )

        first = self.document(
            source="tushare_announcement",
            source_id="stable-link",
            first_seen_at="2026-07-18T02:00:00+08:00",
            content=b"stable-link-content",
            metadata={
                "ingestion_mode": "history",
                "security_links": [{
                    "ts_code": "831689.BJ",
                    "name": "  名称乙 ",
                    "provenance": "z_provider",
                }],
            },
        )
        second = self.document(
            source="tushare_announcement",
            source_id="stable-link",
            first_seen_at="2026-07-18T01:00:00+08:00",
            content=b"stable-link-content",
            metadata={
                "ingestion_mode": "live",
                "security_links": [{
                    "ts_code": "831689.BJ",
                    "name": "名称甲",
                    "provenance": "a_provider",
                }],
            },
        )
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            forward = run_order(Path(left), (first, second))
            reverse = run_order(Path(right), (second, first))

        self.assertEqual(forward, reverse)
        self.assertEqual(forward[1], "名称乙")
        self.assertEqual(forward[2], "a_provider")

    def test_duplicate_links_inside_document_are_order_independent(self) -> None:
        def run_order(
            root: Path,
            links: list[dict[str, str]],
        ) -> tuple[str, str]:
            store = IntelligenceStore(root)
            document_id, _ = store.insert_document(
                self.document(
                    source="tushare_announcement",
                    source_id="same-payload-links",
                    content=b"same-payload-links",
                    metadata={"security_links": links},
                )
            )
            link = store.document_security_links(document_id)[0]
            return str(link["name"]), str(link["provenance"])

        links = [
            {
                "ts_code": "831689.BJ",
                "name": "名称甲",
                "provenance": "z_provider",
            },
            {
                "ts_code": "831689.BJ",
                "name": "名称乙",
                "provenance": "a_provider",
            },
        ]
        with (
            tempfile.TemporaryDirectory() as left,
            tempfile.TemporaryDirectory() as right,
        ):
            forward = run_order(Path(left), links)
            reverse = run_order(Path(right), list(reversed(links)))

        self.assertEqual(forward, reverse)
        self.assertEqual(forward, ("名称乙", "a_provider"))

    def test_new_security_link_requeues_processed_document(self) -> None:
        original = self.document(
            source="tushare_announcement",
            source_id="link-revision",
            content=b"stable-link-revision",
            metadata={
                "security_links": [{
                    "ts_code": "300114.SZ",
                    "name": "中航电测",
                    "provenance": "tushare_anns_d",
                }],
            },
        )
        document_id, _ = self.store.insert_document(original)
        self.store.mark_document(document_id, "no_event")

        repeated_id, inserted = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="link-revision",
                content=b"stable-link-revision",
                metadata={
                    "security_links": [
                        {
                            "ts_code": "300114.SZ",
                            "name": "中航电测",
                            "provenance": "tushare_anns_d",
                        },
                        {
                            "ts_code": "833429.BJ",
                            "name": "康比特",
                            "provenance": "probe_discovery",
                        },
                    ],
                },
            )
        )

        self.assertFalse(inserted)
        self.assertEqual(repeated_id, document_id)
        pending = self.store.pending_documents()
        self.assertEqual([int(row["id"]) for row in pending], [document_id])
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT status, link_revision,
                       extracted_link_revision
                FROM documents WHERE id=?
                """,
                (document_id,),
            ).fetchone()
        self.assertEqual(row["status"], "collected")
        self.assertGreater(
            int(row["link_revision"]),
            int(row["extracted_link_revision"]),
        )

    def test_reextraction_supplements_entities_on_existing_event(self) -> None:
        document_id, _ = self.store.insert_document(
            self.document(
                source="tushare_announcement",
                source_id="entity-revision",
            )
        )
        base = {
            "document_id": document_id,
            "event_type": "buyback",
            "direction": 1,
            "strength": 0.8,
            "confidence": 0.9,
            "novelty": 1,
            "horizon_days": 20,
            "published_at": "2026-07-18T01:00:00Z",
            "effective_at": "2026-07-18T01:00:00Z",
            "evidence": "buyback plan",
        }
        self.store.insert_event(MarketEvent(
            event_id="event-before-link",
            entities=({
                "entity_type": "security",
                "entity_id": "300114",
                "entity_name": "中航电测",
                "confidence": 0.98,
            },),
            **base,
        ))
        self.store.insert_event(MarketEvent(
            event_id="event-after-link",
            entities=(
                {
                    "entity_type": "security",
                    "entity_id": "300114",
                    "entity_name": "中航电测",
                    "confidence": 0.98,
                },
                {
                    "entity_type": "security",
                    "entity_id": "833429",
                    "entity_name": "康比特",
                    "confidence": 0.98,
                },
            ),
            **base,
        ))

        with self.store.connect() as connection:
            old_entities = connection.execute(
                """
                SELECT entity_id
                FROM event_entities
                WHERE event_id='event-before-link'
                ORDER BY entity_id
                """
            ).fetchall()
        self.assertEqual(
            [str(row["entity_id"]) for row in old_entities],
            ["300114", "833429"],
        )


if __name__ == "__main__":
    unittest.main()
