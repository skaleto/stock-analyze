from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.reporting import (
    read_agent_evolutions,
    read_agent_proposals,
    render_latest_briefing_panel,
    render_strategy_evolution_panel,
)


def _write_evolution(
    data_dir: Path,
    month: str,
    diff_payload: dict,
    log_text: str | None = None,
) -> tuple[Path, Path]:
    """Write a pair of evolution_diff/<month>.json + evolution_log/<month>.md.

    Mirrors what :mod:`stock_analyze.evolution_writer` would produce.
    """

    diff_dir = data_dir / "evolution_diff"
    log_dir = data_dir / "evolution_log"
    diff_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"{month}.json"
    diff_path.write_text(json.dumps(diff_payload, ensure_ascii=False), encoding="utf-8")
    log_path = log_dir / f"{month}.md"
    if log_text is None:
        log_text = f"# {month} 演化记录\n\n_测试用占位 markdown_"
    log_path.write_text(log_text, encoding="utf-8")
    return diff_path, log_path


def _write_leaderboard(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


class StrategyEvolutionPanelTests(unittest.TestCase):
    def test_empty_state_when_no_evolutions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = render_strategy_evolution_panel(tmp)
            self.assertIn("尚未生成策略演化记录", html)

    def test_lists_evolutions_in_descending_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            _write_evolution(
                data_dir,
                "2026-04",
                {
                    "agent_id": "claude",
                    "month": "2026-04",
                    "from_config_hash": "aaaaaaaaaaaa",
                    "to_config_hash": "bbbbbbbbbbbb",
                    "diff": {"factors.pe.weight": {"from": 0.17, "to": 0.20}},
                },
                log_text="april reasoning",
            )
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "bbbbbbbbbbbb",
                    "to_config_hash": "cccccccccccc",
                    "diff": {},
                },
                log_text="may reasoning (no change)",
            )
            html = render_strategy_evolution_panel(data_dir)
            idx_may = html.find("2026-05")
            idx_apr = html.find("2026-04")
            self.assertGreater(idx_apr, idx_may)  # 2026-04 appears below 2026-05
            self.assertIn("proposal-no-change", html)  # May has empty diff
            self.assertIn("proposal-change", html)  # April has a diff entry
            self.assertIn("factors.pe.weight", html)
            self.assertIn("本月维持", html)
            self.assertIn("已演化", html)
            self.assertIn("aaaaaaaaaaaa", html)

    def test_leaderboard_pairing_fills_current_and_next_month_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            _write_evolution(
                data_dir,
                "2026-04",
                {
                    "agent_id": "claude",
                    "month": "2026-04",
                    "from_config_hash": "aaaa",
                    "to_config_hash": "bbbb",
                    "diff": {},
                },
            )
            leaderboard = Path(tmp) / "competition" / "leaderboard.csv"
            _write_leaderboard(
                leaderboard,
                [
                    {"month": "2026-04", "claude_return": 0.04, "codex_return": 0.03, "winner_return": "claude"},
                    {"month": "2026-05", "claude_return": 0.06, "codex_return": 0.05, "winner_return": "claude"},
                ],
            )
            html = render_strategy_evolution_panel(data_dir, leaderboard_path=leaderboard)
            self.assertIn("4.00%", html)
            self.assertIn("6.00%", html)

    def test_log_excerpt_is_rendered_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "1234567890ab",
                    "to_config_hash": "abcdef123456",
                    "diff": {"factors.pe.weight": {"from": 0.17, "to": 0.20}},
                },
                log_text="本月 pe 因子领涨，加权",
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("本月 pe 因子领涨", html)
            self.assertIn("阅读", html)
            self.assertIn("1234567890ab", html)
            self.assertIn("abcdef123456", html)

    def test_diff_summary_truncates_when_many_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            diff = {
                f"factors.f{i}.weight": {"from": 0.0, "to": 0.1}
                for i in range(10)
            }
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "x",
                    "to_config_hash": "y",
                    "diff": diff,
                },
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("…", html)

    def test_html_escape_in_log_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "x",
                    "to_config_hash": "y",
                    "diff": {},
                },
                log_text="本月调整 <动量> 因子",
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("&lt;动量&gt;", html)
            self.assertNotIn("<动量>", html)


class LatestBriefingPanelTests(unittest.TestCase):
    def test_empty_state_when_no_briefings_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            data_dir.mkdir(parents=True)
            html = render_latest_briefing_panel(data_dir)
            self.assertIn("还没生成 briefing", html)

    def test_shows_latest_weekly_and_monthly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            briefings = data_dir / "notes" / "briefings"
            briefings.mkdir(parents=True)
            weekly_old = briefings / "2026-05-15-weekly.md"
            weekly_new = briefings / "2026-05-22-weekly.md"
            monthly = briefings / "2026-05-monthly.md"
            weekly_old.write_text("old weekly briefing", encoding="utf-8")
            weekly_new.write_text("# 角色\n new weekly briefing", encoding="utf-8")
            monthly.write_text("# 角色\n monthly briefing", encoding="utf-8")
            # Set monthly mtime older than weekly_new, weekly_old oldest.
            now = time.time()
            os.utime(weekly_old, (now - 200, now - 200))
            os.utime(monthly, (now - 100, now - 100))
            os.utime(weekly_new, (now, now))
            html = render_latest_briefing_panel(data_dir)
            self.assertIn("2026-05-22-weekly.md", html)
            self.assertIn("2026-05-monthly.md", html)
            self.assertNotIn("2026-05-15-weekly.md", html)

    def test_truncates_overlong_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            briefings = data_dir / "notes" / "briefings"
            briefings.mkdir(parents=True)
            big = "A" * (20 * 1024)
            (briefings / "2026-05-22-weekly.md").write_text(big, encoding="utf-8")
            html = render_latest_briefing_panel(data_dir)
            self.assertIn("…(truncated)", html)


class ReadAgentEvolutionsTests(unittest.TestCase):
    def test_skips_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            diff_dir = data_dir / "evolution_diff"
            diff_dir.mkdir(parents=True)
            (diff_dir / "2026-04.json").write_text("not-json", encoding="utf-8")
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "x",
                    "to_config_hash": "y",
                    "diff": {},
                },
            )
            results = read_agent_evolutions(data_dir)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["month"], "2026-05")

    def test_alias_read_agent_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            _write_evolution(
                data_dir,
                "2026-05",
                {
                    "agent_id": "claude",
                    "month": "2026-05",
                    "from_config_hash": "x",
                    "to_config_hash": "y",
                    "diff": {},
                },
            )
            # alias should return same payload
            results = read_agent_proposals(data_dir)
            self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
