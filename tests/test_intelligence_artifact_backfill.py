from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_analyze.intelligence.artifact_backfill import (
    PHASES,
    RuntimeHealth,
    _defer_parse_document,
    _next_parse_document,
    choose_phase,
    guard_reason,
)


class IntelligenceArtifactBackfillTest(unittest.TestCase):
    def test_phase_a_and_b_are_bounded_microbatches(self) -> None:
        self.assertEqual(PHASES["a"].download_limit, 180)
        self.assertEqual(PHASES["a"].parse_batches, 75)
        self.assertEqual(PHASES["a"].parse_batch_size, 1)
        self.assertEqual(PHASES["b"].download_limit, 240)
        self.assertEqual(PHASES["b"].parse_batches, 100)
        self.assertEqual(PHASES["b"].parse_batch_size, 1)

    def test_phase_b_requires_24h_and_20_clean_phase_a_runs(self) -> None:
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        history = [
            {
                "status": "success",
                "duration_seconds": 600,
                "peak_rss_mib": 700,
            }
            for _ in range(20)
        ]
        eligible = {
            "phase": "a",
            "phase_started_at": (now - timedelta(hours=25)).isoformat(),
            "history": history,
            "consecutive_breaches": 0,
        }
        too_early = {
            **eligible,
            "phase_started_at": (now - timedelta(hours=23)).isoformat(),
        }

        self.assertEqual(choose_phase(eligible, now=now), "b")
        self.assertEqual(choose_phase(too_early, now=now), "a")

    def test_resource_guard_defers_before_work(self) -> None:
        health = RuntimeHealth(
            memory_available_mib=400,
            swap_used_mib=0,
            load_1m=0.2,
            disk_free_gib=20,
            reconcile_active=False,
            semantic_active=False,
        )
        self.assertEqual(guard_reason(health), "memory_available_low")

    def test_semantic_batch_gets_priority_over_historical_backfill(
        self,
    ) -> None:
        health = RuntimeHealth(
            memory_available_mib=900,
            swap_used_mib=0,
            load_1m=0.2,
            disk_free_gib=20,
            reconcile_active=False,
            semantic_active=True,
        )

        self.assertEqual(guard_reason(health), "semantic_active")

    def test_daily_critical_window_defers_historical_backfill(self) -> None:
        health = RuntimeHealth(
            memory_available_mib=900,
            swap_used_mib=0,
            load_1m=0.2,
            disk_free_gib=20,
            reconcile_active=False,
            semantic_active=False,
            critical_window=True,
        )

        self.assertEqual(guard_reason(health), "daily_critical_window")

    def test_formal_pipeline_gets_priority_outside_scheduled_window(self) -> None:
        health = RuntimeHealth(
            memory_available_mib=900,
            swap_used_mib=0,
            load_1m=0.2,
            disk_free_gib=20,
            reconcile_active=False,
            semantic_active=False,
            formal_pipeline_active=True,
        )

        self.assertEqual(guard_reason(health), "formal_pipeline_active")

    def test_timed_out_parse_rotates_to_the_back_of_the_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = (
                root
                / "data"
                / "shared"
                / "intelligence"
                / "intelligence.sqlite3"
            )
            database.parent.mkdir(parents=True)
            config = root / "configs" / "intelligence_semantic.yaml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "parser:\n  version: announcement-layout-v1\n",
                encoding="utf-8",
            )
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE documents (
                      id INTEGER PRIMARY KEY,
                      queue_priority INTEGER NOT NULL,
                      live_observed INTEGER NOT NULL
                    );
                    CREATE TABLE document_artifacts (
                      document_id INTEGER NOT NULL,
                      artifact_type TEXT NOT NULL,
                      status TEXT NOT NULL,
                      parser_version TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      error TEXT NOT NULL
                    );
                    INSERT INTO documents VALUES(1, 1, 1);
                    INSERT INTO documents VALUES(2, 1, 1);
                    INSERT INTO document_artifacts
                    VALUES(1, 'pdf', 'downloaded', '', '2026-01-01', '');
                    INSERT INTO document_artifacts
                    VALUES(2, 'pdf', 'downloaded', '', '2026-01-02', '');
                    """
                )

            self.assertEqual(_next_parse_document(root), 1)
            self.assertTrue(
                _defer_parse_document(
                    root,
                    1,
                    reason="parse_timeout_deferred",
                )
            )
            self.assertEqual(_next_parse_document(root), 2)


if __name__ == "__main__":
    unittest.main()
