from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_analyze.intelligence.ingestion import IntelligencePipeline
from stock_analyze.intelligence.sources.base import FetchBatch
from stock_analyze.intelligence.sources.official import (
    TushareAnnouncementAdapter,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class StubAdapter:
    def __init__(self, source: str, batch: FetchBatch) -> None:
        self.source = source
        self.batch = batch
        self.last_cursor = ""

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        self.last_cursor = cursor
        return self.batch


class FailingTushareAdapter:
    source = "tushare_announcement"
    initial_lookback_days = 3

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        del cursor, until
        raise RuntimeError("injected_fetch_failure")


class IntelligenceIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pipeline = IntelligencePipeline.__new__(IntelligencePipeline)
        self.pipeline.repo_root = root
        self.pipeline.config_path = root / "unused.yaml"
        self.pipeline.store = IntelligenceStore(root / "intelligence")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def seed_cursor(self, source: str, cursor: str = "old-cursor") -> None:
        self.pipeline.store.start_run("seed", source)
        self.pipeline.store.finish_run(
            "seed", status="success", cursor=cursor, fetched=1, inserted=1
        )

    def latest_run_status(self, source: str) -> str:
        with self.pipeline.store.connect() as connection:
            row = connection.execute(
                """
                SELECT status FROM ingestion_runs
                WHERE source=?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (source,),
            ).fetchone()
        return str(row["status"])

    def document(self, source: str) -> SourceDocument:
        return SourceDocument(
            source=source,
            source_id="doc-1",
            title="Policy update",
            published_at="2026-07-18T01:00:00Z",
            first_seen_at="2026-07-18T02:00:00Z",
            effective_at="2026-07-18T02:00:00Z",
            source_url="https://example.test/doc-1",
            content=b"policy",
        )

    def run_batch(self, adapter: StubAdapter) -> dict:
        with patch(
            "stock_analyze.intelligence.ingestion.build_adapters",
            return_value=(adapter,),
        ):
            return self.pipeline.ingest(until="2026-07-19T00:00:00Z")

    def test_degraded_status_is_persisted_and_does_not_advance_cursor(self) -> None:
        source = "official_policy"
        self.seed_cursor(source)
        result = self.run_batch(
            StubAdapter(
                source,
                FetchBatch(
                    documents=(self.document(source),),
                    cursor="new-cursor",
                    warnings=("pagination_truncated:1000",),
                ),
            )
        )

        self.assertEqual(result["sources"][0]["status"], "degraded")
        self.assertEqual(self.latest_run_status(source), "degraded")
        self.assertEqual(self.pipeline.store.cursor(source), "old-cursor")

    def test_unavailable_status_is_persisted_and_does_not_advance_cursor(self) -> None:
        source = "licensed_announcements"
        self.seed_cursor(source)
        result = self.run_batch(
            StubAdapter(
                source,
                FetchBatch(
                    documents=(),
                    cursor="new-cursor",
                    warnings=("source_unavailable:entitlement_disabled",),
                ),
            )
        )

        self.assertEqual(result["sources"][0]["status"], "unavailable")
        self.assertEqual(self.latest_run_status(source), "unavailable")
        self.assertEqual(self.pipeline.store.cursor(source), "old-cursor")

    def test_empty_success_does_not_advance_cursor(self) -> None:
        source = "official_policy"
        self.seed_cursor(source)
        result = self.run_batch(
            StubAdapter(source, FetchBatch(documents=(), cursor="new-cursor"))
        )

        self.assertEqual(result["sources"][0]["status"], "success")
        self.assertEqual(self.pipeline.store.cursor(source), "old-cursor")

    def test_saturated_bootstrap_day_persists_until_successfully_recovered(self) -> None:
        phase = {"value": 0}
        calls_by_phase: dict[int, list[str]] = {
            1: [],
            2: [],
            3: [],
            4: [],
        }

        def announcement(identifier: str, ann_date: str) -> dict:
            return {
                "ann_date": ann_date,
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "title": f"Announcement {identifier}",
                "url": (
                    "https://example.test/disclosure?"
                    f"announcementId={identifier}"
                ),
                "rec_time": "",
            }

        client = unittest.mock.Mock()

        def anns_d(**kwargs):
            current_phase = int(phase["value"])
            ann_date = str(kwargs["ann_date"])
            calls_by_phase[current_phase].append(ann_date)
            if ann_date == "20260701" and current_phase < 4:
                return pd.DataFrame([
                    announcement("july-1-a", ann_date),
                    announcement("july-1-b", ann_date),
                ])
            if ann_date == "20260701" and current_phase == 4:
                return pd.DataFrame([
                    announcement("july-1-a", ann_date),
                ])
            if ann_date == "20260705" and current_phase == 4:
                return pd.DataFrame([
                    announcement("july-5", ann_date),
                ])
            return pd.DataFrame()

        client.anns_d.side_effect = anns_d
        adapter = TushareAnnouncementAdapter(
            client,
            enabled=True,
            initial_lookback_days=1,
            page_size=2,
        )

        with patch(
            "stock_analyze.intelligence.ingestion.build_adapters",
            return_value=(adapter,),
        ):
            for current_phase, until in (
                (1, "2026-07-01T23:59:59+08:00"),
                (2, "2026-07-03T23:59:59+08:00"),
                (3, "2026-07-04T23:59:59+08:00"),
            ):
                phase["value"] = current_phase
                result = self.pipeline.ingest(until=until)
                self.assertEqual(
                    result["sources"][0]["status"],
                    "degraded",
                )
                self.assertEqual(
                    self.pipeline.store.source_retry_window(
                        "tushare_announcement"
                    )["unresolved_day"],
                    "2026-07-01",
                )
                self.assertEqual(
                    self.pipeline.store.cursor(
                        "tushare_announcement"
                    ),
                    "",
                )

            phase["value"] = 4
            recovered = self.pipeline.ingest(
                until="2026-07-05T23:59:59+08:00"
            )

        self.assertEqual(
            [calls[0] for calls in calls_by_phase.values()],
            ["20260701", "20260701", "20260701", "20260701"],
        )
        self.assertEqual(
            recovered["sources"][0]["status"],
            "success",
        )
        self.assertIsNone(
            self.pipeline.store.source_retry_window(
                "tushare_announcement"
            )
        )
        self.assertEqual(
            self.pipeline.store.cursor("tushare_announcement"),
            "2026-07-05T15:59:59+00:00",
        )

    def test_empty_cursor_persists_provisional_floor_before_fetch_failure(self) -> None:
        with patch(
            "stock_analyze.intelligence.ingestion.build_adapters",
            return_value=(FailingTushareAdapter(),),
        ):
            result = self.pipeline.ingest(
                until="2026-07-05T23:59:59+08:00"
            )

        self.assertEqual(result["sources"][0]["status"], "failed")
        retry = self.pipeline.store.source_retry_window(
            "tushare_announcement"
        )
        self.assertEqual(retry["unresolved_day"], "2026-07-03")
        self.assertEqual(retry["reason"], "provisional_scan_floor")
        self.assertEqual(
            self.pipeline.store.cursor("tushare_announcement"),
            "",
        )

    def test_successful_bootstrap_clears_provisional_floor_in_same_run(self) -> None:
        source = "tushare_announcement"
        adapter = StubAdapter(
            source,
            FetchBatch(
                documents=(self.document(source),),
                cursor="2026-07-19T00:00:00+00:00",
            ),
        )

        result = self.run_batch(adapter)

        self.assertEqual(result["sources"][0]["status"], "success")
        self.assertIsNone(
            self.pipeline.store.source_retry_window(source)
        )
        self.assertEqual(
            self.pipeline.store.cursor(source),
            "2026-07-19T00:00:00+00:00",
        )

    def test_pipeline_gets_effective_cursor_only_from_atomic_run_claim(self) -> None:
        source = "official_policy"
        self.seed_cursor(
            source,
            "2026-07-03T15:59:59+00:00",
        )
        adapter = StubAdapter(
            source,
            FetchBatch(documents=(), cursor="unchanged"),
        )

        with (
            patch(
                "stock_analyze.intelligence.ingestion.build_adapters",
                return_value=(adapter,),
            ),
            patch.object(
                self.pipeline.store,
                "cursor",
                side_effect=AssertionError("preclaim_cursor_read"),
            ),
            patch.object(
                self.pipeline.store,
                "source_retry_window",
                side_effect=AssertionError("preclaim_retry_read"),
            ),
        ):
            result = self.pipeline.ingest(
                until="2026-07-05T23:59:59+08:00"
            )

        self.assertEqual(result["sources"][0]["status"], "success")
        self.assertEqual(
            adapter.last_cursor,
            "2026-07-03T15:59:59+00:00",
        )

    def test_atomic_claim_overrides_stale_cursor_with_new_retry_floor(self) -> None:
        source = "tushare_announcement"
        self.seed_cursor(
            source,
            "2026-07-03T15:59:59+00:00",
        )
        stale_cursor = self.pipeline.store.cursor(source)
        failed_claim = self.pipeline.store.start_run(
            "new-failure",
            source,
            owner="worker-b",
        )
        self.pipeline.store.finish_run(
            "new-failure",
            status="degraded",
            retry_unresolved_day="2026-07-01",
            retry_reason="day_saturated:20260701",
            generation=int(failed_claim["generation"]),
            owner="worker-b",
        )

        recovering_claim = self.pipeline.store.start_run(
            "stale-reader",
            source,
            cursor=stale_cursor,
            owner="worker-a",
        )

        self.assertEqual(
            recovering_claim["cursor"],
            "2026-07-01T00:00:00+08:00",
        )
        self.assertEqual(
            recovering_claim["retry_unresolved_day"],
            "2026-07-01",
        )

    def test_retry_clear_requires_matching_unresolved_covered_floor(self) -> None:
        source = "tushare_announcement"
        claim = self.pipeline.store.start_run(
            "coverage-mismatch",
            source,
            owner="worker-a",
            provisional_retry_day="2026-07-01",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "intelligence_ingestion_retry_clear_conflict",
        ):
            self.pipeline.store.finish_run(
                "coverage-mismatch",
                status="success",
                cursor="2026-07-05T15:59:59+00:00",
                fetched=1,
                inserted=1,
                retry_window_scanned=True,
                retry_covered_floor="2026-07-02",
                generation=int(claim["generation"]),
                owner="worker-a",
            )

        retry = self.pipeline.store.source_retry_window(source)
        self.assertEqual(retry["unresolved_day"], "2026-07-01")
        self.assertEqual(self.pipeline.store.cursor(source), "")

    def test_stale_success_worker_cannot_clear_newer_failure_or_advance_cursor(self) -> None:
        source = "tushare_announcement"
        started = datetime(
            2026,
            7,
            1,
            tzinfo=timezone.utc,
        )
        old_claim = self.pipeline.store.start_run(
            "old-worker",
            source,
            owner="worker-a",
            lease_seconds=1,
            now=started,
            provisional_retry_day="2026-07-01",
        )
        new_claim = self.pipeline.store.start_run(
            "new-worker",
            source,
            owner="worker-b",
            lease_seconds=30,
            now=started + timedelta(seconds=2),
            provisional_retry_day="2026-07-01",
        )
        self.pipeline.store.finish_run(
            "new-worker",
            status="degraded",
            fetched=2,
            inserted=2,
            retry_unresolved_day="2026-07-01",
            retry_reason="day_saturated:20260701",
            retry_window_scanned=True,
            generation=int(new_claim["generation"]),
            owner="worker-b",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "intelligence_ingestion_generation_conflict",
        ):
            self.pipeline.store.finish_run(
                "old-worker",
                status="success",
                cursor="2026-07-05T15:59:59+00:00",
                fetched=1,
                inserted=1,
                retry_window_scanned=True,
                generation=int(old_claim["generation"]),
                owner="worker-a",
            )

        retry = self.pipeline.store.source_retry_window(source)
        self.assertEqual(retry["unresolved_day"], "2026-07-01")
        self.assertEqual(
            retry["generation"],
            int(new_claim["generation"]),
        )
        self.assertEqual(self.pipeline.store.cursor(source), "")

    def test_backfill_production_factory_uses_typed_https_transport(self) -> None:
        self.pipeline.config_path.write_text(
            """
            schema_version: 1
            sources:
              tushare_announcement:
                type: tushare_announcement
                enabled: true
                entitled: true
                full_history_start: "1990-12-19"
                page_size: 2000
                trade_calendar_mode: full_natural_days
                trade_calendar_boundary_buffer_days: 45
                trade_calendar_cache: reference/tushare_sse_trade_calendar.csv
                endpoint: https://api.tushare.pro
            """,
            encoding="utf-8",
        )
        transport = unittest.mock.Mock()
        transport.trade_cal.return_value = pd.DataFrame([
            {
                "cal_date": value.strftime("%Y%m%d"),
                "is_open": int(value.weekday() < 5),
            }
            for value in pd.date_range("2026-07-24", "2026-09-07")
        ])
        transport.anns_d.return_value = pd.DataFrame(
            columns=(
                "ann_date", "ts_code", "name", "title", "url", "rec_time",
            )
        )

        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "secret"}),
            patch(
                "stock_analyze.intelligence.ingestion.TushareProTransport",
                return_value=transport,
            ) as transport_type,
        ):
            result = self.pipeline.backfill(
                source="tushare_announcement",
                start_date="2026-07-24",
                end_date="2026-07-24",
                max_partitions=1,
                resume=True,
            )

        self.assertEqual(result["status"], "complete")
        transport_type.assert_called_once_with(
            "secret",
            endpoint="https://api.tushare.pro",
        )
        self.assertTrue(
            (
                self.pipeline.store.root
                / "reference"
                / "tushare_sse_trade_calendar.csv"
            ).exists()
        )
        self.assertNotIn(
            "is_open",
            transport.trade_cal.call_args.kwargs,
        )

        transport.trade_cal.reset_mock()
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "secret"}),
            patch(
                "stock_analyze.intelligence.ingestion.TushareProTransport",
                return_value=transport,
            ),
        ):
            self.pipeline.backfill(
                source="tushare_announcement",
                start_date="2026-07-24",
                end_date="2026-07-24",
                max_partitions=1,
                resume=True,
            )
        transport.trade_cal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
