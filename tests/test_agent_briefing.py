from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.agent_briefing import (
    build_monthly_briefing,
    build_weekly_briefing,
    monthly_briefing_path,
    weekly_briefing_path,
    write_briefing,
)
from stock_analyze.competition import BASELINE_LOCKED_PATHS, resolve_agent_paths
from stock_analyze.reporting import render_agent_notes_panel


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 10},
        {"id": "zz500", "scope": "zz500", "benchmark": "000905", "cash": 500000, "top_n": 10},
    ],
    "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
    "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.10},
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


def _seed_repo(tmp: Path) -> None:
    (tmp / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (tmp / "configs" / "competition.yaml").write_text(json.dumps(BASELINE_CONFIG), encoding="utf-8")
    for agent_id, factors in (
        ("claude", {"pe": {"weight": 0.5, "direction": "low"}, "roe": {"weight": 0.5, "direction": "high"}}),
        ("codex", {"roe": {"weight": 0.5, "direction": "high"}, "low_volatility_60": {"weight": 0.5, "direction": "low"}}),
    ):
        path = tmp / "configs" / "agents" / f"{agent_id}.yaml"
        path.write_text(
            json.dumps({"agent_id": agent_id, "strategy_id": f"{agent_id}_v1", "name": f"{agent_id} test", "factors": factors}),
            encoding="utf-8",
        )


def _seed_agent_data(tmp: Path, agent: str) -> None:
    data_dir = tmp / "data" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "competition" / "monthly_reviews").mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        [
            {"date": "2026-05-15", "account_id": "hs300", "cash": 500000, "market_value": 0, "total_value": 1000000, "benchmark_code": "000300", "benchmark_close": 4000.0, "benchmark_date": "2026-05-15", "notes": ""},
            {"date": "2026-05-16", "account_id": "hs300", "cash": 500000, "market_value": 0, "total_value": 1010000, "benchmark_code": "000300", "benchmark_close": 4020.0, "benchmark_date": "2026-05-16", "notes": ""},
        ]
    ).to_csv(data_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame([{"account_id": "hs300", "code": "000001", "name": "S1", "industry": "金融", "shares": 100, "market_value": 1000.0, "avg_cost": 10.0, "last_price": 10.0, "hold_since": "2026-05-15", "unrealized_pnl": 0}]).to_csv(data_dir / "positions.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame(
        [
            {"run_id": "run-1", "command": "run-weekly", "as_of": "2026-05-15", "started_at": "2026-05-15T17:00:00", "finished_at": "2026-05-15T17:00:10", "duration_ms": 10000, "status": "success", "error_summary": "", "config_hash": "abc123def456", "code_version": "abc1234"},
        ]
    ).to_csv(data_dir / "runs.csv", index=False, encoding="utf-8-sig")


class WeeklyBriefingTests(unittest.TestCase):
    def test_five_sections_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_agent_data(root, "claude")
            text = build_weekly_briefing("claude", as_of="2026-05-22", repo_root=root)
            for header in ("# 角色", "# 数据快照", "# 任务", "# 输出契约", "# 可选参考"):
                self.assertIn(header, text)
            self.assertIn("data/claude/notes/2026-05-22-weekly-review.md", text)
            self.assertIn("**本周不要修改任何 `configs/`", text)

    def test_write_briefing_to_canonical_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_agent_data(root, "claude")
            paths = resolve_agent_paths("claude", repo_root=root)
            text = build_weekly_briefing("claude", as_of="2026-05-22", repo_root=root)
            target = weekly_briefing_path(paths, as_of="2026-05-22")
            write_briefing(text, target)
            self.assertTrue(target.exists())
            content = target.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# 角色"))


class MonthlyBriefingTests(unittest.TestCase):
    def test_locked_paths_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_agent_data(root, "claude")
            text = build_monthly_briefing("claude", "2026-05", repo_root=root)
            for path in BASELINE_LOCKED_PATHS:
                self.assertIn(path, text)
            self.assertIn("data/claude/notes/2026-05-monthly-review.md", text)
            self.assertIn("data/claude/proposals/2026-05-strategy.json", text)
            # JSON proposal schema is described.
            self.assertIn("based_on_config_hash", text)
            self.assertIn("no_change", text)

    def test_baseline_excerpt_includes_initial_cash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_agent_data(root, "claude")
            text = build_monthly_briefing("claude", "2026-05", repo_root=root)
            self.assertIn("`initial_cash`: `1000000`", text)
            self.assertIn("`competition_id`: `test_competition`", text)

    def test_monthly_review_excerpt_included_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_repo(root)
            _seed_agent_data(root, "claude")
            payload = {
                "review_period": "2026-05",
                "agents": {"claude": {"cumulative_return": 0.08, "information_ratio": 1.1}, "codex": {"cumulative_return": 0.05, "information_ratio": 0.9}},
                "comparison": {
                    "winner_cumulative_return": "claude",
                    "winner_information_ratio": "claude",
                    "spread_cumulative_return": 0.03,
                    "position_overlap_ratio": 0.4,
                    "daily_return_correlation": 0.7,
                    "shared_factor_drivers": ["roe"],
                    "divergent_factor_drivers": {"claude_only": ["pe"], "codex_only": ["low_volatility_60"]},
                },
            }
            (root / "data" / "competition" / "monthly_reviews" / "2026-05.json").write_text(json.dumps(payload), encoding="utf-8")
            text = build_monthly_briefing("claude", "2026-05", repo_root=root)
            self.assertIn("月度对比报告", text)
            self.assertIn("胜方", text)
            self.assertIn("claude_only=[pe]", text)
            self.assertIn("codex_only=[low_volatility_60]", text)
            self.assertIn("| codex |", text)


class AgentNotesPanelTests(unittest.TestCase):
    def test_empty_state_when_no_notes_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = render_agent_notes_panel(tmp)
            self.assertIn("尚无 agent 笔记", html)

    def test_lists_latest_five_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes_dir = Path(tmp) / "notes"
            notes_dir.mkdir(parents=True)
            (notes_dir / "briefings").mkdir()
            (notes_dir / "briefings" / "2026-05-22-weekly.md").write_text("briefing body", encoding="utf-8")
            for i in range(7):
                path = notes_dir / f"2026-05-{i + 10}-weekly-review.md"
                path.write_text(f"note {i}\n" * 5, encoding="utf-8")
                # Bump mtime so the newest sort prefers later index.
                os.utime(path, (time.time() + i, time.time() + i))
            html = render_agent_notes_panel(tmp)
            self.assertEqual(html.count("<details>"), 5)
            self.assertNotIn("briefing body", html)


if __name__ == "__main__":
    unittest.main()
