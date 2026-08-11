from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path

from stock_analyze.intelligence import IntelligenceStore, MarketEvent, SourceDocument
from stock_analyze.intelligence.schema import MIGRATION_V1, SCHEMA_VERSION


HISTORICAL_CUTOFF = "2026-07-17T23:59:59+08:00"


class FailingMigrationStore(IntelligenceStore):
    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        if version == 2 and statement_index == 2:
            raise RuntimeError("injected_v2_migration_failure")


class IntelligenceSchemaV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_database_creates_all_v2_tables_and_indexes(self) -> None:
        store = self._store()

        with store.connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertTrue(
            {
                "intelligence_settings",
                "document_availability",
                "backfill_partitions",
                "document_artifacts",
                "document_chunks",
                "document_tables",
                "semantic_runs",
                "event_candidates",
                "event_evidence",
                "event_facts",
                "event_scores",
                "event_relations",
            }.issubset(tables)
        )
        self.assertTrue(
            {
                "idx_events_id_document_unique",
                "idx_backfill_partitions_status_date",
                "idx_document_artifacts_status_document",
                "idx_semantic_runs_status_document",
                "idx_event_candidates_validation",
                "idx_event_evidence_chunk",
            }.issubset(indexes)
        )
        self.assertEqual(store.integrity_check(), "ok")

    def test_real_v1_database_migrates_without_losing_rows(self) -> None:
        self._create_v1_database()

        store = self._store()

        self.assertEqual(store.schema_version(), SCHEMA_VERSION)
        self.assertEqual(store.integrity_check(), "ok")
        self.assertEqual(len(store.documents()), 1)
        self.assertEqual(store.cursor("tushare_announcement"), "2026-07-24")
        events = store.events_as_of("2026-07-24T23:59:59+08:00")
        self.assertEqual(events["event_id"].tolist(), ["legacy-event"])
        availability = store.document_availability(1)
        self.assertEqual(availability["availability_provenance"], "observed")
        self.assertEqual(
            availability["research_available_at"],
            "2026-07-24T02:00:00+00:00",
        )

    def test_v1_migration_normalizes_all_aware_times_to_utc(self) -> None:
        self._create_v1_database()
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            connection.execute(
                """
                UPDATE documents
                SET published_at=?,
                    first_seen_at=?,
                    effective_at=?,
                    revised_at=?
                WHERE id=1
                """,
                (
                    "2021-03-15T17:00:00+08:00",
                    "2026-07-24T10:00:00+08:00",
                    "2021-03-15T09:00:00Z",
                    "2021-03-15T18:00:00+08:00",
                ),
            )
            connection.execute(
                """
                UPDATE events
                SET published_at=?, effective_at=?
                WHERE event_id='legacy-event'
                """,
                (
                    "2021-03-15T17:00:00+08:00",
                    "2021-03-15T09:00:00Z",
                ),
            )
            connection.execute(
                "UPDATE schema_meta SET applied_at=? WHERE version=1",
                ("2026-07-24T02:00:00Z",),
            )
            connection.execute(
                "UPDATE source_cursors SET updated_at=? WHERE source=?",
                (
                    "2026-07-24T10:00:00+08:00",
                    "tushare_announcement",
                ),
            )
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, source, started_at, finished_at, status
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "legacy-run",
                    "tushare_announcement",
                    "2026-07-24T10:00:00+08:00",
                    "2026-07-24T02:01:00Z",
                    "success",
                ),
            )
            connection.execute(
                """
                INSERT INTO quality_results(
                    run_id, source, metric, value, measured_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "legacy-run",
                    "tushare_announcement",
                    "coverage",
                    1.0,
                    "2026-07-24T10:02:00+08:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        store = self._store()

        with store.connect() as connection:
            document = connection.execute(
                """
                SELECT published_at, first_seen_at, effective_at, revised_at
                FROM documents WHERE id=1
                """
            ).fetchone()
            event = connection.execute(
                """
                SELECT published_at, effective_at
                FROM events WHERE event_id='legacy-event'
                """
            ).fetchone()
            run = connection.execute(
                """
                SELECT started_at, finished_at
                FROM ingestion_runs WHERE run_id='legacy-run'
                """
            ).fetchone()
            cursor_updated_at = connection.execute(
                """
                SELECT updated_at FROM source_cursors
                WHERE source='tushare_announcement'
                """
            ).fetchone()[0]
            measured_at = connection.execute(
                """
                SELECT measured_at FROM quality_results
                WHERE run_id='legacy-run'
                """
            ).fetchone()[0]
            applied_times = [
                str(row[0])
                for row in connection.execute(
                    "SELECT applied_at FROM schema_meta ORDER BY version"
                ).fetchall()
            ]
        self.assertEqual(
            tuple(document),
            (
                "2021-03-15T09:00:00+00:00",
                "2026-07-24T02:00:00+00:00",
                "2021-03-15T09:00:00+00:00",
                "2021-03-15T10:00:00+00:00",
            ),
        )
        self.assertEqual(
            tuple(event),
            (
                "2021-03-15T09:00:00+00:00",
                "2021-03-15T09:00:00+00:00",
            ),
        )
        self.assertEqual(
            tuple(run),
            (
                "2026-07-24T02:00:00+00:00",
                "2026-07-24T02:01:00+00:00",
            ),
        )
        self.assertEqual(cursor_updated_at, "2026-07-24T02:00:00+00:00")
        self.assertEqual(measured_at, "2026-07-24T02:02:00+00:00")
        self.assertTrue(
            all(value.endswith("+00:00") for value in applied_times)
        )
        visible = store.events_as_of("2026-07-24T02:00:00Z")
        self.assertEqual(visible["event_id"].tolist(), ["legacy-event"])

    def test_reopening_migrated_database_is_idempotent(self) -> None:
        self._create_v1_database()

        self._store()
        reopened = self._store()

        self.assertEqual(reopened.schema_version(), SCHEMA_VERSION)
        self.assertEqual(len(reopened.documents()), 1)
        self.assertEqual(
            reopened.events_as_of("2026-07-24T23:59:59+08:00")[
                "event_id"
            ].tolist(),
            ["legacy-event"],
        )
        with reopened.connect() as connection:
            availability_count = connection.execute(
                "SELECT COUNT(*) FROM document_availability"
            ).fetchone()[0]
        self.assertEqual(availability_count, 1)

    def test_migration_failure_rolls_back_all_v2_changes_and_can_retry(self) -> None:
        self._create_v1_database()

        with self.assertRaisesRegex(
            RuntimeError,
            "^injected_v2_migration_failure$",
        ):
            FailingMigrationStore(
                self.root,
                historical_cutoff=HISTORICAL_CUTOFF,
            )

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            v2_tables = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name='document_availability'
                """
            ).fetchone()[0]
            document_count = connection.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 1)
        self.assertEqual(v2_tables, 0)
        self.assertEqual(document_count, 1)

        recovered = self._store()
        self.assertEqual(recovered.schema_version(), SCHEMA_VERSION)
        self.assertEqual(len(recovered.documents()), 1)

    def test_concurrent_store_initialization_is_serialized_and_idempotent(self) -> None:
        concurrent_root = self.root / "concurrent"
        barrier = threading.Barrier(8)

        def initialize(_: int) -> int:
            barrier.wait()
            return IntelligenceStore(
                concurrent_root,
                historical_cutoff=HISTORICAL_CUTOFF,
            ).schema_version()

        with ThreadPoolExecutor(max_workers=8) as executor:
            versions = list(executor.map(initialize, range(8)))

        self.assertEqual(versions, [SCHEMA_VERSION] * 8)
        store = IntelligenceStore(
            concurrent_root,
            historical_cutoff=HISTORICAL_CUTOFF,
        )
        self.assertEqual(store.integrity_check(), "ok")

    def test_v1_naive_timestamp_aborts_migration_without_version_drift(self) -> None:
        self._create_v1_database(naive_first_seen=True)

        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_v1_naive_timestamp:1$",
        ):
            self._store()

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            v2_tables = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name='document_availability'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 1)
        self.assertEqual(v2_tables, 0)

    def test_v1_invalid_timestamp_aborts_migration_without_version_drift(
        self,
    ) -> None:
        self._create_v1_database()
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            connection.execute(
                "UPDATE events SET effective_at='not-a-time'"
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_v1_invalid_timestamp:1$",
        ):
            self._store()

        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM schema_meta"
            ).fetchone()[0]
            v2_tables = connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name='document_availability'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, 1)
        self.assertEqual(v2_tables, 0)

    def test_cutoff_is_store_owned_and_source_document_cannot_override_it(self) -> None:
        store = self._store()

        self.assertEqual(
            store.historical_cutoff,
            "2026-07-17T15:59:59+00:00",
        )
        with self.assertRaises(AttributeError):
            store.historical_cutoff = "2027-01-01T00:00:00+00:00"

        source_fields = {field.name for field in fields(SourceDocument)}
        self.assertTrue(
            {
                "historical_cutoff",
                "source_recorded_at",
                "research_available_at",
                "availability_provenance",
            }.isdisjoint(source_fields)
        )

        document_id, _ = store.insert_document(
            self._source_document(
                source_id="spoofed",
                published_at="2021-03-15T09:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
                metadata={
                    "historical_cutoff": "2099-12-31T23:59:59+08:00",
                    "research_available_at": "2021-03-15T09:01:00+08:00",
                    "availability_provenance": "reconstructed_rec_time",
                },
            )
        )
        availability = store.document_availability(document_id)
        self.assertEqual(availability["availability_provenance"], "observed")
        self.assertEqual(
            availability["research_available_at"],
            "2026-07-24T02:00:00+00:00",
        )

    def test_precise_pre_cutoff_record_time_is_research_visible_only(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="precise",
            published_at="2021-03-15T17:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        store.upsert_reconstructed_availability(
            document_id,
            source_recorded_at="2021-03-15T18:31:22+08:00",
            research_available_at="2021-03-15T18:31:22+08:00",
            provenance="reconstructed_rec_time",
        )

        self.assertTrue(
            store.events_as_of(
                "2021-03-15T18:31:22+08:00",
                availability_policy="observed",
            ).empty
        )
        research = store.events_as_of(
            "2021-03-15T18:31:22+08:00",
            availability_policy="research",
        )
        self.assertEqual(research["event_id"].tolist(), ["event-precise"])
        self.assertEqual(
            research.iloc[0]["available_at"],
            "2021-03-15T10:31:22+00:00",
        )
        availability = store.document_availability(document_id)
        self.assertEqual(
            availability["availability_provenance"],
            "reconstructed_rec_time",
        )
        self.assertEqual(
            availability["source_recorded_at"],
            "2021-03-15T10:31:22+00:00",
        )

    def test_date_only_pre_cutoff_record_uses_next_open_for_research(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="date-only",
            published_at="2021-03-15T00:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        store.upsert_reconstructed_availability(
            document_id,
            source_recorded_at=None,
            research_available_at="2021-03-16T09:30:00+08:00",
            provenance="reconstructed_next_open",
        )

        self.assertTrue(
            store.events_as_of(
                "2021-03-16T09:29:59+08:00",
                availability_policy="research",
            ).empty
        )
        visible = store.events_as_of(
            "2021-03-16T09:30:00+08:00",
            availability_policy="research",
        )
        self.assertEqual(visible["event_id"].tolist(), ["event-date-only"])
        self.assertEqual(
            store.document_availability(document_id)["research_available_at"],
            "2021-03-16T01:30:00+00:00",
        )

    def test_rec_time_requires_a_matching_source_recorded_at(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="rec-time-contract",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )

        with self.assertRaisesRegex(
            ValueError,
            "^reconstructed_rec_time_source_recorded_at_required$",
        ):
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=None,
                research_available_at="2021-03-15T18:00:00+08:00",
                provenance="reconstructed_rec_time",
            )
        with self.assertRaisesRegex(
            ValueError,
            "^reconstructed_rec_time_timestamp_mismatch$",
        ):
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at="2021-03-15T18:00:00+08:00",
                research_available_at="2021-03-15T18:01:00+08:00",
                provenance="reconstructed_rec_time",
            )
        self.assertEqual(
            store.document_availability(document_id)[
                "availability_provenance"
            ],
            "observed",
        )

        store.upsert_reconstructed_availability(
            document_id,
            source_recorded_at="2021-03-15T18:00:00+08:00",
            research_available_at="2021-03-15T10:00:00+00:00",
            provenance="reconstructed_rec_time",
        )
        availability = store.document_availability(document_id)
        self.assertEqual(
            availability["availability_provenance"],
            "reconstructed_rec_time",
        )
        self.assertEqual(
            availability["source_recorded_at"],
            availability["research_available_at"],
        )

    def test_rec_time_database_constraint_rejects_forged_priority(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="rec-time-forgery",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )

        for source_recorded_at, research_available_at in (
            (None, "2021-03-15T10:00:00+00:00"),
            (
                "2021-03-15T10:00:00+00:00",
                "2021-03-15T10:01:00+00:00",
            ),
        ):
            with self.subTest(
                source_recorded_at=source_recorded_at,
                research_available_at=research_available_at,
            ):
                self._assert_integrity_error(
                    store,
                    """
                    UPDATE document_availability
                    SET source_recorded_at=?,
                        research_available_at=?,
                        availability_provenance='reconstructed_rec_time'
                    WHERE document_id=?
                    """,
                    (
                        source_recorded_at,
                        research_available_at,
                        document_id,
                    ),
                )

    def test_rec_time_rejects_values_outside_document_observation_window(
        self,
    ) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="rec-time-window",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2021-03-20T10:00:00+08:00",
        )

        for timestamp in (
            "2021-03-15T08:59:59+08:00",
            "2021-03-20T10:00:01+08:00",
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(
                    ValueError,
                    "^reconstructed_availability_out_of_bounds$",
                ):
                    store.upsert_reconstructed_availability(
                        document_id,
                        source_recorded_at=timestamp,
                        research_available_at=timestamp,
                        provenance="reconstructed_rec_time",
                    )

    def test_next_open_requires_a_trusted_resolver(self) -> None:
        store = IntelligenceStore(
            self.root,
            historical_cutoff=HISTORICAL_CUTOFF,
        )
        document_id = self._insert_document_and_event(
            store,
            source_id="next-open-no-resolver",
            published_at="2021-03-13T00:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )

        with self.assertRaisesRegex(
            ValueError,
            "^reconstructed_next_open_resolver_required$",
        ):
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=None,
                research_available_at="2021-03-15T09:30:00+08:00",
                provenance="reconstructed_next_open",
            )

    def test_next_open_uses_weekend_and_holiday_calendar_fixture(self) -> None:
        expected = {
            "2021-03-12T16:00:00+00:00": "2021-03-15T09:30:00+08:00",
            "2021-09-30T16:00:00+00:00": "2021-10-08T09:30:00+08:00",
        }
        calls: list[str] = []

        def resolver(published_at: str) -> str:
            calls.append(published_at)
            return expected[published_at]

        store = IntelligenceStore(
            self.root,
            historical_cutoff=HISTORICAL_CUTOFF,
            next_market_open_resolver=resolver,
        )
        fixtures = (
            (
                "weekend",
                "2021-03-13T00:00:00+08:00",
                None,
                "2021-03-15T01:30:00+00:00",
            ),
            (
                "national-day",
                "2021-10-01T00:00:00+08:00",
                "2021-10-08T01:30:00+00:00",
                "2021-10-08T01:30:00+00:00",
            ),
        )
        for source_id, published_at, supplied, expected_utc in fixtures:
            document_id = self._insert_document_and_event(
                store,
                source_id=source_id,
                published_at=published_at,
                first_seen_at="2026-07-24T10:00:00+08:00",
            )
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=None,
                research_available_at=supplied,
                provenance="reconstructed_next_open",
            )
            self.assertEqual(
                store.document_availability(document_id)[
                    "research_available_at"
                ],
                expected_utc,
            )

        self.assertEqual(calls, list(expected))

    def test_next_open_rejects_caller_mismatch_and_resolver_out_of_bounds(
        self,
    ) -> None:
        store = IntelligenceStore(
            self.root,
            historical_cutoff=HISTORICAL_CUTOFF,
            next_market_open_resolver=lambda _: "2021-03-15T09:30:00+08:00",
        )
        document_id = self._insert_document_and_event(
            store,
            source_id="next-open-mismatch",
            published_at="2021-03-13T00:00:00+08:00",
            first_seen_at="2021-03-20T10:00:00+08:00",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^reconstructed_next_open_timestamp_mismatch$",
        ):
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=None,
                research_available_at="2021-03-16T09:30:00+08:00",
                provenance="reconstructed_next_open",
            )

        out_of_bounds_root = self.root / "next-open-out-of-bounds"
        out_of_bounds_store = IntelligenceStore(
            out_of_bounds_root,
            historical_cutoff=HISTORICAL_CUTOFF,
            next_market_open_resolver=lambda _: "2021-03-21T09:30:00+08:00",
        )
        out_of_bounds_id = self._insert_document_and_event(
            out_of_bounds_store,
            source_id="next-open-out-of-bounds",
            published_at="2021-03-13T00:00:00+08:00",
            first_seen_at="2021-03-20T10:00:00+08:00",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^reconstructed_availability_out_of_bounds$",
        ):
            out_of_bounds_store.upsert_reconstructed_availability(
                out_of_bounds_id,
                source_recorded_at=None,
                research_available_at=None,
                provenance="reconstructed_next_open",
            )

    def test_post_cutoff_reconstruction_and_forged_row_are_fail_closed(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="post-cutoff",
            published_at="2026-07-18T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        store.upsert_reconstructed_availability(
            document_id,
            source_recorded_at="2026-07-18T09:01:00+08:00",
            research_available_at="2026-07-18T09:01:00+08:00",
            provenance="reconstructed_rec_time",
        )

        availability = store.document_availability(document_id)
        self.assertEqual(availability["availability_provenance"], "observed")
        self.assertEqual(
            availability["research_available_at"],
            "2026-07-24T02:00:00+00:00",
        )

        with store.connect() as connection:
            connection.execute(
                """
                UPDATE document_availability
                SET source_recorded_at=?,
                    research_available_at=?,
                    availability_provenance='reconstructed_rec_time',
                    historical_cutoff=?
                WHERE document_id=?
                """,
                (
                    "2026-07-18T01:01:00+00:00",
                    "2026-07-18T01:01:00+00:00",
                    store.historical_cutoff,
                    document_id,
                ),
            )

        self.assertTrue(
            store.events_as_of(
                "2026-07-24T09:59:59+08:00",
                availability_policy="research",
            ).empty
        )
        visible = store.events_as_of(
            "2026-07-24T10:00:00+08:00",
            availability_policy="research",
        )
        self.assertEqual(visible["event_id"].tolist(), ["event-post-cutoff"])
        self.assertEqual(
            visible.iloc[0]["available_at"],
            "2026-07-24T02:00:00+00:00",
        )

    def test_ordinary_live_document_defaults_to_observed(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="live",
            published_at="2026-07-24T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )

        availability = store.document_availability(document_id)
        self.assertEqual(availability["availability_provenance"], "observed")
        self.assertIsNone(availability["source_recorded_at"])
        self.assertEqual(
            availability["research_available_at"],
            "2026-07-24T02:00:00+00:00",
        )
        self.assertEqual(
            availability["historical_cutoff"],
            "2026-07-17T15:59:59+00:00",
        )

    def test_availability_merge_is_order_independent_and_uses_earliest_peer(self) -> None:
        store = self._store()
        document_ids = {
            name: self._insert_document_and_event(
                store,
                source_id=name,
                published_at="2021-03-15T09:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
            )
            for name in (
                "forward",
                "reverse",
                "peer-forward",
                "peer-reverse",
            )
        }
        next_open = {
            "source_recorded_at": None,
            "research_available_at": "2021-03-16T09:30:00+08:00",
            "provenance": "reconstructed_next_open",
        }
        rec_time = {
            "source_recorded_at": "2021-03-15T18:31:22+08:00",
            "research_available_at": "2021-03-15T18:31:22+08:00",
            "provenance": "reconstructed_rec_time",
        }
        early_rec_time = {
            "source_recorded_at": "2021-03-15T18:00:00+08:00",
            "research_available_at": "2021-03-15T18:00:00+08:00",
            "provenance": "reconstructed_rec_time",
        }

        store.upsert_reconstructed_availability(
            document_ids["forward"], **next_open
        )
        store.upsert_reconstructed_availability(
            document_ids["forward"], **rec_time
        )
        store.upsert_reconstructed_availability(
            document_ids["reverse"], **rec_time
        )
        store.upsert_reconstructed_availability(
            document_ids["reverse"], **next_open
        )
        store.upsert_reconstructed_availability(
            document_ids["peer-forward"], **rec_time
        )
        store.upsert_reconstructed_availability(
            document_ids["peer-forward"], **early_rec_time
        )
        store.upsert_reconstructed_availability(
            document_ids["peer-reverse"], **early_rec_time
        )
        store.upsert_reconstructed_availability(
            document_ids["peer-reverse"], **rec_time
        )
        forward = store.document_availability(document_ids["forward"])
        reverse = store.document_availability(document_ids["reverse"])
        peer_forward = store.document_availability(document_ids["peer-forward"])
        peer_reverse = store.document_availability(document_ids["peer-reverse"])
        self.assertEqual(
            (
                forward["availability_provenance"],
                forward["research_available_at"],
            ),
            (
                reverse["availability_provenance"],
                reverse["research_available_at"],
            ),
        )
        self.assertEqual(
            (
                peer_forward["availability_provenance"],
                peer_forward["research_available_at"],
            ),
            (
                "reconstructed_rec_time",
                "2021-03-15T10:00:00+00:00",
            ),
        )
        self.assertEqual(
            peer_forward["research_available_at"],
            peer_reverse["research_available_at"],
        )

    def test_concurrent_availability_updates_converge_deterministically(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="concurrent-availability",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        candidates = [
            (
                None,
                "2021-03-16T09:30:00+08:00",
                "reconstructed_next_open",
            ),
            (
                "2021-03-15T18:31:22+08:00",
                "2021-03-15T18:31:22+08:00",
                "reconstructed_rec_time",
            ),
            (
                "2021-03-15T18:00:00+08:00",
                "2021-03-15T18:00:00+08:00",
                "reconstructed_rec_time",
            ),
        ] * 4
        barrier = threading.Barrier(len(candidates))

        def apply(candidate: tuple[str | None, str, str]) -> None:
            barrier.wait()
            worker_store = IntelligenceStore(
                self.root,
                historical_cutoff=HISTORICAL_CUTOFF,
                next_market_open_resolver=self._fixture_next_market_open,
            )
            worker_store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=candidate[0],
                research_available_at=candidate[1],
                provenance=candidate[2],
            )

        with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
            list(executor.map(apply, candidates))

        availability = store.document_availability(document_id)
        self.assertEqual(
            availability["availability_provenance"],
            "reconstructed_rec_time",
        )
        self.assertEqual(
            availability["research_available_at"],
            "2021-03-15T10:00:00+00:00",
        )

    def test_database_cutoff_is_persisted_and_mismatch_is_rejected(self) -> None:
        store = self._store()
        with store.connect() as connection:
            row = connection.execute(
                """
                SELECT value FROM intelligence_settings
                WHERE key='historical_cutoff'
                """
            ).fetchone()
        self.assertEqual(
            row["value"],
            "2026-07-17T15:59:59+00:00",
        )
        for mismatched_cutoff in (
            "2026-07-16T23:59:59+08:00",
            "2026-07-18T23:59:59+08:00",
        ):
            with self.subTest(historical_cutoff=mismatched_cutoff):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "^intelligence_historical_cutoff_mismatch:",
                ):
                    IntelligenceStore(
                        self.root,
                        historical_cutoff=mismatched_cutoff,
                    )
        reopened = self._store()
        self.assertEqual(
            reopened.historical_cutoff,
            "2026-07-17T15:59:59+00:00",
        )

    def test_store_rejects_naive_new_times_without_changing_utc_iso(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_naive_timestamp:historical_cutoff$",
        ):
            IntelligenceStore(
                self.root / "naive-cutoff",
                historical_cutoff="2026-07-17T23:59:59",
            )

        store = self._store()
        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_naive_timestamp:document.published_at$",
        ):
            store.insert_document(
                self._source_document(
                    source_id="naive-document",
                    published_at="2021-03-15T09:00:00",
                    first_seen_at="2026-07-24T10:00:00+08:00",
                )
            )
        self.assertTrue(store.documents().empty)

        document_id = self._insert_document_and_event(
            store,
            source_id="aware",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_naive_timestamp:research_available_at$",
        ):
            store.upsert_reconstructed_availability(
                document_id,
                source_recorded_at=None,
                research_available_at="2021-03-16T09:30:00",
                provenance="reconstructed_next_open",
            )
        with self.assertRaisesRegex(
            ValueError,
            "^intelligence_naive_timestamp:as_of$",
        ):
            store.events_as_of("2021-03-16T09:30:00")

    def test_composite_foreign_keys_reject_cross_document_lineage(self) -> None:
        store = self._store()
        document_1 = self._insert_document_and_event(
            store,
            source_id="lineage-1",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        document_2 = self._insert_document_and_event(
            store,
            source_id="lineage-2",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        self._insert_artifact(store, "artifact-1", document_1)
        self._insert_semantic_run(store, "run-1", document_1)
        self._insert_semantic_run(store, "run-2", document_2)

        self._assert_integrity_error(
            store,
            """
            INSERT INTO document_chunks(
                chunk_id, document_id, artifact_id, sequence_no, page_number,
                bbox_json, text, text_hash, parser_version
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "chunk-mismatch",
                document_2,
                "artifact-1",
                0,
                0,
                "{}",
                "text",
                "hash",
                "parser-v1",
            ),
        )
        self._assert_integrity_error(
            store,
            """
            INSERT INTO document_tables(
                table_id, document_id, artifact_id, page_number, sequence_no,
                bbox_json, cells_json, parser_version
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "table-mismatch",
                document_2,
                "artifact-1",
                0,
                0,
                "{}",
                "[]",
                "parser-v1",
            ),
        )
        self._assert_integrity_error(
            store,
            """
            INSERT INTO event_candidates(
                candidate_id, run_id, document_id, event_index, event_type,
                lifecycle, payload_json, validation_status, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "candidate-run-mismatch",
                "run-1",
                document_2,
                0,
                "buyback",
                "announced",
                "{}",
                "pending",
                "2026-07-24T02:00:00+00:00",
            ),
        )
        self._assert_integrity_error(
            store,
            """
            INSERT INTO event_candidates(
                candidate_id, run_id, document_id, event_index, event_type,
                lifecycle, payload_json, validation_status,
                canonical_event_id, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "candidate-event-mismatch",
                "run-2",
                document_2,
                0,
                "buyback",
                "announced",
                "{}",
                "canonical",
                "event-lineage-1",
                "2026-07-24T02:00:00+00:00",
            ),
        )

    def test_event_evidence_requires_candidate_and_chunk_from_same_document(
        self,
    ) -> None:
        store = self._store()
        document_1 = self._insert_document_and_event(
            store,
            source_id="evidence-1",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        document_2 = self._insert_document_and_event(
            store,
            source_id="evidence-2",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        self._insert_artifact(store, "evidence-artifact-1", document_1)
        self._insert_artifact(store, "evidence-artifact-2", document_2)
        self._insert_chunk(
            store,
            "evidence-chunk-1",
            document_1,
            "evidence-artifact-1",
        )
        self._insert_chunk(
            store,
            "evidence-chunk-2",
            document_2,
            "evidence-artifact-2",
        )
        self._insert_semantic_run(store, "evidence-run-1", document_1)
        self._insert_semantic_run(store, "evidence-run-2", document_2)
        self._insert_candidate(
            store,
            "evidence-candidate-1",
            "evidence-run-1",
            document_1,
        )
        self._insert_candidate(
            store,
            "evidence-candidate-2",
            "evidence-run-2",
            document_2,
        )

        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO event_evidence(
                    candidate_id, document_id, evidence_id, chunk_id,
                    page_number, start_char, end_char, quote,
                    normalized_quote_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "evidence-candidate-1",
                    document_1,
                    "evidence-valid",
                    "evidence-chunk-1",
                    0,
                    0,
                    5,
                    "quote",
                    "quote-hash",
                ),
            )

        for candidate_id, document_id, chunk_id, page_number in (
            (
                "evidence-candidate-1",
                document_1,
                "evidence-chunk-2",
                0,
            ),
            (
                "evidence-candidate-2",
                document_1,
                "evidence-chunk-1",
                0,
            ),
            (
                "evidence-candidate-1",
                document_1,
                "evidence-chunk-1",
                1,
            ),
        ):
            with self.subTest(
                candidate_id=candidate_id,
                document_id=document_id,
                chunk_id=chunk_id,
                page_number=page_number,
            ):
                self._assert_integrity_error(
                    store,
                    """
                    INSERT INTO event_evidence(
                        candidate_id, document_id, evidence_id, chunk_id,
                        page_number, start_char, end_char, quote,
                        normalized_quote_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate_id,
                        document_id,
                        f"cross-{candidate_id}-{chunk_id}",
                        chunk_id,
                        page_number,
                        0,
                        5,
                        "quote",
                        "quote-hash",
                    ),
                )

    def test_event_candidate_status_matches_canonical_event_presence(
        self,
    ) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="candidate-status",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        self._insert_semantic_run(store, "candidate-status-run", document_id)

        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index, event_type,
                    lifecycle, payload_json, validation_status,
                    canonical_event_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "candidate-status-valid-canonical",
                    "candidate-status-run",
                    document_id,
                    0,
                    "buyback",
                    "announced",
                    "{}",
                    "canonical",
                    "event-candidate-status",
                    "2026-07-24T02:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index, event_type,
                    lifecycle, payload_json, validation_status, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "candidate-status-valid-quarantined",
                    "candidate-status-run",
                    document_id,
                    1,
                    "buyback",
                    "announced",
                    "{}",
                    "quarantined",
                    "2026-07-24T02:00:00+00:00",
                ),
            )

        invalid_rows = (
            (
                "candidate-status-canonical-without-event",
                2,
                "canonical",
                None,
            ),
            (
                "candidate-status-pending-with-event",
                3,
                "pending",
                "event-candidate-status",
            ),
            (
                "candidate-status-quarantined-with-event",
                4,
                "quarantined",
                "event-candidate-status",
            ),
        )
        for candidate_id, event_index, status, canonical_event_id in invalid_rows:
            with self.subTest(status=status, canonical_event_id=canonical_event_id):
                self._assert_integrity_error(
                    store,
                    """
                    INSERT INTO event_candidates(
                        candidate_id, run_id, document_id, event_index,
                        event_type, lifecycle, payload_json,
                        validation_status, canonical_event_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate_id,
                        "candidate-status-run",
                        document_id,
                        event_index,
                        "buyback",
                        "announced",
                        "{}",
                        status,
                        canonical_event_id,
                        "2026-07-24T02:00:00+00:00",
                    ),
                )

    def test_v2_check_constraints_reject_invalid_values(self) -> None:
        store = self._store()
        document_id = self._insert_document_and_event(
            store,
            source_id="checks",
            published_at="2021-03-15T09:00:00+08:00",
            first_seen_at="2026-07-24T10:00:00+08:00",
        )
        self._insert_artifact(store, "artifact-valid", document_id)
        self._insert_chunk(store, "chunk-valid", document_id, "artifact-valid")
        self._insert_semantic_run(store, "run-valid", document_id)
        self._insert_candidate(
            store,
            "candidate-valid",
            "run-valid",
            document_id,
        )

        probes = [
            (
                """
                INSERT INTO backfill_partitions(
                    source, partition_start, partition_end, status, updated_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    "tushare",
                    "2021-02-01",
                    "2021-01-01",
                    "pending",
                    "2026-07-24T02:00:00+00:00",
                ),
            ),
            (
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "artifact-negative",
                    document_id,
                    "pdf",
                    "hash",
                    "oss://bucket/key",
                    "application/pdf",
                    -1,
                    "downloaded",
                    "2026-07-24T02:00:00+00:00",
                    "2026-07-24T02:00:00+00:00",
                ),
            ),
            (
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, ocr_used, parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "chunk-negative-sequence",
                    document_id,
                    "artifact-valid",
                    -1,
                    0,
                    "{}",
                    "text",
                    "hash",
                    0,
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, ocr_used, parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "chunk-negative-page",
                    document_id,
                    "artifact-valid",
                    1,
                    -1,
                    "{}",
                    "text",
                    "hash",
                    0,
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, ocr_used, parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "chunk-invalid-ocr",
                    document_id,
                    "artifact-valid",
                    1,
                    0,
                    "{}",
                    "text",
                    "hash",
                    2,
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, ocr_used, ocr_confidence,
                    parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "chunk-invalid-confidence",
                    document_id,
                    "artifact-valid",
                    1,
                    0,
                    "{}",
                    "text",
                    "hash",
                    1,
                    1.1,
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "chunk-invalid-json",
                    document_id,
                    "artifact-valid",
                    1,
                    0,
                    "{",
                    "text",
                    "hash",
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO document_tables(
                    table_id, document_id, artifact_id, page_number, sequence_no,
                    bbox_json, cells_json, parser_version
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    "table-invalid-json",
                    document_id,
                    "artifact-valid",
                    0,
                    0,
                    "{}",
                    "[",
                    "parser-v1",
                ),
            ),
            (
                """
                INSERT INTO semantic_runs(
                    run_id, document_id, artifact_hash, provider, model,
                    prompt_version, schema_version, taxonomy_version,
                    parser_version, input_hash, status, input_tokens, started_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run-negative-token",
                    document_id,
                    "artifact-hash",
                    "deepseek",
                    "model",
                    "prompt-v1",
                    "schema-v1",
                    "taxonomy-v1",
                    "parser-v1",
                    "input-hash",
                    "running",
                    -1,
                    "2026-07-24T02:00:00+00:00",
                ),
            ),
            (
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index, event_type,
                    lifecycle, payload_json, validation_status,
                    validation_errors_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "candidate-invalid-json",
                    "run-valid",
                    document_id,
                    1,
                    "buyback",
                    "announced",
                    "{",
                    "pending",
                    "[]",
                    "2026-07-24T02:00:00+00:00",
                ),
            ),
            (
                """
                INSERT INTO event_evidence(
                    candidate_id, document_id, evidence_id, chunk_id,
                    page_number, start_char, end_char, quote,
                    normalized_quote_hash
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    "candidate-valid",
                    document_id,
                    "evidence-invalid-range",
                    "chunk-valid",
                    0,
                    9,
                    3,
                    "quote",
                    "quote-hash",
                ),
            ),
            (
                """
                INSERT INTO event_facts(
                    event_id, fact_name, evidence_ids_json, provenance
                ) VALUES(?,?,?,?)
                """,
                ("event-checks", "amount", "[", "semantic"),
            ),
            (
                """
                INSERT INTO event_scores(
                    event_id, relevance, novelty, certainty, source_credibility,
                    direction, confidence, scoring_version, inputs_json, scored_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "event-checks",
                    1.0,
                    1.0,
                    1.0,
                    1.0,
                    0.0,
                    1.0,
                    "score-v1",
                    "{",
                    "2026-07-24T02:00:00+00:00",
                ),
            ),
        ]
        for sql, params in probes:
            with self.subTest(sql=" ".join(sql.split())[:80]):
                self._assert_integrity_error(store, sql, params)

    def test_event_score_ranges_accept_boundaries_and_null_materiality(
        self,
    ) -> None:
        store = self._store()
        boundaries = (
            ("score-lower", 0.0, -1.0, 0.0),
            ("score-upper", 1.0, 1.0, 1.0),
            ("score-null", 0.5, 0.0, None),
        )
        for source_id, bounded_value, direction, materiality in boundaries:
            self._insert_document_and_event(
                store,
                source_id=source_id,
                published_at="2021-03-15T09:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
            )
            with store.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO event_scores(
                        event_id, relevance, novelty, materiality, certainty,
                        source_credibility, direction, confidence,
                        scoring_version, inputs_json, scored_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"event-{source_id}",
                        bounded_value,
                        bounded_value,
                        materiality,
                        bounded_value,
                        bounded_value,
                        direction,
                        bounded_value,
                        "score-v1",
                        "{}",
                        "2026-07-24T02:00:00+00:00",
                    ),
                )

        with store.connect() as connection:
            rows = connection.execute(
                "SELECT event_id FROM event_scores ORDER BY event_id"
            ).fetchall()
        self.assertEqual(len(rows), 3)

    def test_event_score_ranges_reject_every_out_of_range_field(self) -> None:
        store = self._store()
        invalid_cases = (
            ("relevance", -0.01),
            ("novelty", 1.01),
            ("materiality", -0.01),
            ("certainty", 1.01),
            ("source_credibility", -0.01),
            ("direction", -1.01),
            ("direction", 1.01),
            ("confidence", 1.01),
        )
        columns = (
            "relevance",
            "novelty",
            "materiality",
            "certainty",
            "source_credibility",
            "direction",
            "confidence",
        )
        for index, (field, invalid_value) in enumerate(invalid_cases):
            source_id = f"score-invalid-{index}"
            self._insert_document_and_event(
                store,
                source_id=source_id,
                published_at="2021-03-15T09:00:00+08:00",
                first_seen_at="2026-07-24T10:00:00+08:00",
            )
            values = {column: 0.5 for column in columns}
            values[field] = invalid_value
            with self.subTest(field=field, value=invalid_value):
                self._assert_integrity_error(
                    store,
                    """
                    INSERT INTO event_scores(
                        event_id, relevance, novelty, materiality, certainty,
                        source_credibility, direction, confidence,
                        scoring_version, inputs_json, scored_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"event-{source_id}",
                        values["relevance"],
                        values["novelty"],
                        values["materiality"],
                        values["certainty"],
                        values["source_credibility"],
                        values["direction"],
                        values["confidence"],
                        "score-v1",
                        "{}",
                        "2026-07-24T02:00:00+00:00",
                    ),
                )

    def test_integrity_check_reports_foreign_key_violations(self) -> None:
        store = self._store()
        connection = sqlite3.connect(store.db_path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                """
                INSERT INTO events(
                    event_id, document_id, event_type, direction, strength,
                    confidence, novelty, horizon_days, published_at, effective_at,
                    evidence, extraction_method, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "orphan-event",
                    999,
                    "announcement",
                    0.0,
                    0.5,
                    0.8,
                    1.0,
                    20,
                    "2021-03-15T01:00:00+00:00",
                    "2021-03-15T01:00:00+00:00",
                    "orphan",
                    "test",
                    "{}",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertEqual(store.integrity_check(), "foreign_key_violation:1")

    def _store(self) -> IntelligenceStore:
        return IntelligenceStore(
            self.root,
            historical_cutoff=HISTORICAL_CUTOFF,
            next_market_open_resolver=self._fixture_next_market_open,
        )

    @staticmethod
    def _fixture_next_market_open(_: str) -> str:
        return "2021-03-16T09:30:00+08:00"

    def _source_document(
        self,
        *,
        source_id: str,
        published_at: str,
        first_seen_at: str,
        metadata: dict[str, str] | None = None,
    ) -> SourceDocument:
        return SourceDocument(
            source="tushare_announcement",
            source_id=source_id,
            title=f"Announcement {source_id}",
            published_at=published_at,
            first_seen_at=first_seen_at,
            effective_at=published_at,
            source_url=f"https://example.test/{source_id}.pdf",
            content=source_id.encode(),
            metadata=metadata or {},
        )

    def _insert_document_and_event(
        self,
        store: IntelligenceStore,
        *,
        source_id: str,
        published_at: str,
        first_seen_at: str,
    ) -> int:
        document_id, inserted = store.insert_document(
            self._source_document(
                source_id=source_id,
                published_at=published_at,
                first_seen_at=first_seen_at,
            )
        )
        self.assertTrue(inserted)
        self.assertTrue(
            store.insert_event(
                MarketEvent(
                    event_id=f"event-{source_id}",
                    document_id=document_id,
                    event_type="announcement",
                    direction=0.0,
                    strength=0.5,
                    confidence=0.8,
                    novelty=1.0,
                    horizon_days=20,
                    published_at=published_at,
                    effective_at=published_at,
                    evidence=f"Evidence {source_id}",
                )
            )
        )
        return document_id

    def _insert_artifact(
        self,
        store: IntelligenceStore,
        artifact_id: str,
        document_id: int,
    ) -> None:
        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    artifact_id,
                    document_id,
                    "pdf",
                    f"hash-{artifact_id}",
                    f"oss://bucket/{artifact_id}",
                    "application/pdf",
                    100,
                    "downloaded",
                    "2026-07-24T02:00:00+00:00",
                    "2026-07-24T02:00:00+00:00",
                ),
            )

    def _insert_chunk(
        self,
        store: IntelligenceStore,
        chunk_id: str,
        document_id: int,
        artifact_id: str,
    ) -> None:
        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no, page_number,
                    bbox_json, text, text_hash, parser_version
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    chunk_id,
                    document_id,
                    artifact_id,
                    0,
                    0,
                    "{}",
                    "text",
                    f"hash-{chunk_id}",
                    "parser-v1",
                ),
            )

    def _insert_semantic_run(
        self,
        store: IntelligenceStore,
        run_id: str,
        document_id: int,
    ) -> None:
        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO semantic_runs(
                    run_id, document_id, artifact_hash, provider, model,
                    prompt_version, schema_version, taxonomy_version,
                    parser_version, input_hash, status, started_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    document_id,
                    f"artifact-hash-{run_id}",
                    "deepseek",
                    "model",
                    "prompt-v1",
                    "schema-v1",
                    "taxonomy-v1",
                    "parser-v1",
                    f"input-hash-{run_id}",
                    "running",
                    "2026-07-24T02:00:00+00:00",
                ),
            )

    def _insert_candidate(
        self,
        store: IntelligenceStore,
        candidate_id: str,
        run_id: str,
        document_id: int,
    ) -> None:
        with store.connect() as connection:
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index, event_type,
                    lifecycle, payload_json, validation_status, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    run_id,
                    document_id,
                    0,
                    "buyback",
                    "announced",
                    "{}",
                    "pending",
                    "2026-07-24T02:00:00+00:00",
                ),
            )

    def _assert_integrity_error(
        self,
        store: IntelligenceStore,
        sql: str,
        params: tuple[object, ...],
    ) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with store.connect() as connection:
                connection.execute(sql, params)

    def _create_v1_database(self, *, naive_first_seen: bool = False) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "intelligence.sqlite3")
        try:
            connection.executescript(MIGRATION_V1)
            connection.execute(
                "INSERT INTO schema_meta(version, applied_at) VALUES(1, ?)",
                ("2026-07-24T02:00:00+00:00",),
            )
            connection.execute(
                """
                INSERT INTO documents(
                    id, source, source_id, title, published_at, first_seen_at,
                    effective_at, revised_at, revision_of, source_url, mime_type,
                    content_hash, raw_path, metadata_json, status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    1,
                    "tushare_announcement",
                    "legacy-document",
                    "Legacy announcement",
                    "2021-03-15T09:00:00+00:00",
                    (
                        "2026-07-24T02:00:00"
                        if naive_first_seen
                        else "2026-07-24T02:00:00+00:00"
                    ),
                    "2021-03-15T09:00:00+00:00",
                    None,
                    None,
                    "https://example.test/legacy.pdf",
                    "application/pdf",
                    "legacy-hash",
                    "raw/legacy",
                    "{}",
                    "processed",
                ),
            )
            connection.execute(
                """
                INSERT INTO events(
                    event_id, document_id, event_type, direction, strength,
                    confidence, novelty, horizon_days, published_at, effective_at,
                    evidence, extraction_method, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "legacy-event",
                    1,
                    "announcement",
                    0.0,
                    0.5,
                    0.8,
                    1.0,
                    20,
                    "2021-03-15T09:00:00+00:00",
                    "2021-03-15T09:00:00+00:00",
                    "Legacy evidence",
                    "rules-v1",
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO source_cursors(source, cursor, updated_at) VALUES(?,?,?)",
                (
                    "tushare_announcement",
                    "2026-07-24",
                    "2026-07-24T02:00:00+00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
