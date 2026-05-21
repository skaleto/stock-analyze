from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.reporting import (
    read_agent_proposals,
    render_latest_briefing_panel,
    render_strategy_evolution_panel,
)


def _write_proposal(target_dir: Path, month: str, payload: dict) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{month}-strategy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_leaderboard(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


class StrategyEvolutionPanelTests(unittest.TestCase):
    def test_empty_state_when_no_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = render_strategy_evolution_panel(tmp)
            self.assertIn("尚未生成策略提案", html)

    def test_lists_proposals_in_descending_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            _write_proposal(proposals, "2026-04", {"rationale": "april reason", "patch": {"factors": {"pe": {"weight": 0.1}}}, "risks": ["r1"], "no_change": False, "expected_effect": "保持收益"})
            _write_proposal(proposals, "2026-05", {"rationale": "may reason", "patch": {}, "no_change": True, "risks": []})
            html = render_strategy_evolution_panel(data_dir)
            idx_may = html.find("2026-05")
            idx_apr = html.find("2026-04")
            self.assertGreater(idx_apr, idx_may)  # April appears later (descending)
            self.assertIn("proposal-no-change", html)
            self.assertIn("proposal-change", html)
            self.assertIn("factors.pe", html)
            self.assertIn("本月维持", html)

    def test_leaderboard_pairing_fills_current_and_next_month_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            _write_proposal(proposals, "2026-04", {"rationale": "x", "patch": {}, "no_change": True, "risks": []})
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

    def test_decision_status_is_rendered_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            _write_proposal(proposals, "2026-05", {"rationale": "x", "patch": {}, "no_change": True, "risks": []})
            decisions = Path(tmp) / "competition" / "decisions"
            decisions.mkdir(parents=True)
            (decisions / "2026-05-claude.json").write_text(
                json.dumps({"decision": "approved", "risk_level": "low"}, ensure_ascii=False),
                encoding="utf-8",
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("裁判通过 / low", html)

    def test_expected_effect_column_is_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            _write_proposal(
                proposals,
                "2026-05",
                {
                    "rationale": "本月数据稳定",
                    "expected_effect": "提高 ROE 暴露",
                    "patch": {},
                    "no_change": True,
                    "risks": [],
                },
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("预期效果", html)  # header
            self.assertIn("提高 ROE 暴露", html)  # cell content

    def test_proposal_hash_drift_is_flagged(self) -> None:
        from stock_analyze.proposal_judge import _hash_mapping

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            original = {
                "rationale": "原版理由",
                "expected_effect": "保持稳定",
                "patch": {},
                "no_change": True,
                "risks": [],
            }
            _write_proposal(proposals, "2026-05", original)
            decisions = Path(tmp) / "competition" / "decisions"
            decisions.mkdir(parents=True)
            (decisions / "2026-05-claude.json").write_text(
                json.dumps(
                    {
                        "decision": "approved",
                        "risk_level": "low",
                        "proposal_hash": _hash_mapping(original),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            html = render_strategy_evolution_panel(data_dir)
            self.assertNotIn("提案已变", html)

            # Modify proposal after decision was recorded.
            _write_proposal(proposals, "2026-05", {**original, "rationale": "新理由"})
            html = render_strategy_evolution_panel(data_dir)
            self.assertIn("提案已变", html)
            self.assertIn("proposal-drift", html)

    def test_html_escape_in_rationale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            _write_proposal(
                proposals,
                "2026-05",
                {
                    "rationale": "本月调整 <动量> 因子",
                    "patch": {},
                    "no_change": True,
                    "risks": [],
                },
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


class ReadAgentProposalsTests(unittest.TestCase):
    def test_skips_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "claude"
            proposals = data_dir / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "2026-04-strategy.json").write_text("not-json", encoding="utf-8")
            _write_proposal(proposals, "2026-05", {"rationale": "good", "patch": {}, "no_change": True, "risks": []})
            results = read_agent_proposals(data_dir)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["month"], "2026-05")


if __name__ == "__main__":
    unittest.main()
