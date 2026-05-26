"""Tests for the pipeline-status dashboard panel (today's task list + 7-day rollup)."""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _seed_runs(repo: Path, agent: str, rows: list[dict]) -> None:
    """Write a minimal runs.csv with the given row dicts."""
    csv_dir = repo / "data" / agent
    csv_dir.mkdir(parents=True, exist_ok=True)
    header = "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version\n"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r.get('run_id', 'r')},{r['command']},{r.get('as_of', '')},"
            f"{r['started_at']},{r.get('finished_at', '')},"
            f"{r.get('duration_ms', '')},{r['status']},"
            f"{r.get('error_summary', '')},h,v\n"
        )
    (csv_dir / "runs.csv").write_text("".join(lines))


def _seed_market_snapshot(repo: Path, iso_date: str, status: str = "success") -> None:
    path = repo / "data" / "shared" / f"market_snapshot_{iso_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "as_of": iso_date,
        "started_at": f"{iso_date}T17:25:00",
        "finished_at": f"{iso_date}T17:34:00",
        "duration_ms": 540000,
        "status": status,
        "candidates_fetched": 800,
        "errors": [],
    }))


class RenderPipelineStatusPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        # Today's runs (success for both daily agents)
        for agent in ("claude", "codex"):
            _seed_runs(self.repo, agent, [
                {"command": "run-daily", "as_of": "2026-05-26",
                 "started_at": "2026-05-26T17:34:33", "finished_at": "2026-05-26T17:34:35",
                 "duration_ms": 2200, "status": "success"},
                {"command": "run-daily", "as_of": "2026-05-25",
                 "started_at": "2026-05-25T17:34:30", "finished_at": "2026-05-25T17:34:32",
                 "duration_ms": 4033, "status": "success"},
                {"command": "run-weekly", "as_of": "2026-05-22",
                 "started_at": "2026-05-24T18:10:36", "finished_at": "2026-05-24T18:10:50",
                 "duration_ms": 13000, "status": "success"},
            ])
        _seed_market_snapshot(self.repo, "2026-05-26")
        _seed_market_snapshot(self.repo, "2026-05-25")

    def tearDown(self):
        self.tmp.cleanup()

    def test_panel_renders_today_section(self):
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(repo_root=self.repo)
        self.assertIn("Pipeline 任务", html)
        self.assertIn("2026-05-26", html)
        self.assertIn("prepare-market-data", html)
        self.assertIn("claude-daily", html)
        self.assertIn("codex-daily", html)

    def test_panel_marks_success_with_check_mark(self):
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(repo_root=self.repo)
        # Multiple ✓ marks should appear (one per task success)
        self.assertGreaterEqual(html.count("✓"), 3)

    def test_panel_shows_failure_status(self):
        # Add a failed daily run for codex
        _seed_runs(self.repo, "codex", [
            {"command": "run-daily", "as_of": "2026-05-26",
             "started_at": "2026-05-26T17:34:34",
             "finished_at": "2026-05-26T17:34:36",
             "duration_ms": 2300, "status": "failed",
             "error_summary": "CacheMiss: history_000001_20260526_220"},
        ])
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(repo_root=self.repo)
        self.assertIn("✗", html)
        self.assertIn("CacheMiss", html)

    def test_panel_shows_pending_when_task_not_yet_run_today(self):
        # Wipe codex runs so today's codex-daily is "pending"
        _seed_runs(self.repo, "codex", [
            {"command": "run-daily", "as_of": "2026-05-25",
             "started_at": "2026-05-25T17:34:30", "duration_ms": 4000, "status": "success"},
        ])
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(repo_root=self.repo)
        # Pending marker shown for codex daily today
        self.assertIn("⏸", html)

    def test_panel_includes_7day_rollup(self):
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(repo_root=self.repo)
        # 7-day section visible
        self.assertIn("7 日", html)

    def test_panel_includes_recent_failures_if_present(self):
        # Seed PIPELINE_FAILURES.log
        pf = self.repo / "logs" / "PIPELINE_FAILURES.log"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(
            "2026-05-23T10:00:02+08:00\tFAILED\tstock-analyze-claude-weekly\n"
            "stub journal context\n"
            "---\n"
        )
        from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
        with patch("stock_analyze.dashboard_aggregator._today",
                    return_value=date(2026, 5, 26)):
            html = render_pipeline_status_panel(
                repo_root=self.repo,
                pipeline_failures_log=pf,
            )
        self.assertIn("2026-05-23", html)
        self.assertIn("claude-weekly", html)

    def test_panel_empty_state_when_nothing_yet(self):
        with TemporaryDirectory() as empty:
            from stock_analyze.dashboard_aggregator import render_pipeline_status_panel
            with patch("stock_analyze.dashboard_aggregator._today",
                        return_value=date(2026, 5, 26)):
                html = render_pipeline_status_panel(repo_root=Path(empty))
        # Should render without error and still show task list with all "pending"
        self.assertIn("Pipeline 任务", html)
        self.assertIn("⏸", html)


if __name__ == "__main__":
    unittest.main()
