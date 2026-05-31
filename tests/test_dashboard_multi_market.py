from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.dashboard_aggregator import generate_competition_dashboard


def _seed_market_repo(root: Path) -> None:
    (root / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    for market in ("a_share", "hk", "us"):
        (root / "configs" / f"competition_{market}.yaml").write_text(
            json.dumps({"competition_id": f"{market}_test", "initial_cash": 1000000}),
            encoding="utf-8",
        )
        for agent in ("claude", "codex"):
            (root / "configs" / "agents" / f"{agent}_{market}.yaml").write_text(
                json.dumps({"agent_id": agent, "strategy_id": f"{agent}_{market}_v1", "factors": {}}),
                encoding="utf-8",
            )
            data_dir = root / "data" / market / agent
            reports_dir = root / "reports" / market / agent
            data_dir.mkdir(parents=True, exist_ok=True)
            reports_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"date": "2026-05-29", "account_id": "main", "total_value": 1000000},
                    {"date": "2026-05-30", "account_id": "main", "total_value": 1010000},
                ]
            ).to_csv(data_dir / "daily_nav.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "run_id": "r1",
                        "command": "run-daily",
                        "as_of": "2026-05-30",
                        "started_at": "2026-05-30T08:00:00",
                        "finished_at": "2026-05-30T08:00:03",
                        "duration_ms": 3000,
                        "status": "success",
                        "error_summary": "",
                        "config_hash": "h",
                        "code_version": "v",
                    },
                    {
                        "run_id": "r2",
                        "command": "run-weekly",
                        "as_of": "2026-05-30",
                        "started_at": "2026-05-30T10:00:00",
                        "finished_at": "2026-05-30T10:00:07",
                        "duration_ms": 7000,
                        "status": "success",
                        "error_summary": "",
                        "config_hash": "h",
                        "code_version": "v",
                    },
                ]
            ).to_csv(data_dir / "runs.csv", index=False)
            (data_dir / "pending_orders.json").write_text(
                json.dumps(
                    [
                        {"side": "buy", "code": f"{market}-{agent}-1"},
                        {"side": "sell", "code": f"{market}-{agent}-2"},
                    ]
                ),
                encoding="utf-8",
            )
            (reports_dir / "dashboard.html").write_text(
                f"<html>{market} {agent} dashboard sentinel</html>",
                encoding="utf-8",
            )
            (reports_dir / "weekly_report.md").write_text(
                f"# {market} {agent} weekly decision",
                encoding="utf-8",
            )


class MultiMarketDashboardTests(unittest.TestCase):
    def test_competition_dashboard_surfaces_three_markets_and_task_cadences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_market_repo(root)

            out_path = generate_competition_dashboard(
                agents=["claude", "codex"],
                repo_root=root,
                markets=["a_share", "hk", "us"],
            )
            html = out_path.read_text(encoding="utf-8")

            self.assertIn("三市场总览", html)
            self.assertIn("A股", html)
            self.assertIn("港股", html)
            self.assertIn("美股", html)
            self.assertIn("日任务", html)
            self.assertIn("周任务", html)
            self.assertIn("月任务", html)
            self.assertIn("run-daily", html)
            self.assertIn("run-weekly", html)
            self.assertIn("competition-monthly-review", html)
            self.assertIn("/pro/a_share/claude.html", html)
            self.assertIn("/pro/hk/codex.html", html)
            self.assertIn("/pro/us/claude.html", html)
            self.assertIn("目标订单", html)

    def test_all_market_dashboard_includes_agents_that_exist_only_outside_a_share(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_market_repo(root)
            agent = "gemini"
            market = "hk"
            (root / "configs" / "agents" / f"{agent}_{market}.yaml").write_text(
                json.dumps({"agent_id": agent, "strategy_id": "gemini_hk_v1", "factors": {}}),
                encoding="utf-8",
            )
            data_dir = root / "data" / market / agent
            reports_dir = root / "reports" / market / agent
            data_dir.mkdir(parents=True, exist_ok=True)
            reports_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [{"date": "2026-05-30", "account_id": "main", "total_value": 1005000}]
            ).to_csv(data_dir / "daily_nav.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "run_id": "r1",
                        "command": "run-weekly",
                        "as_of": "2026-05-30",
                        "started_at": "2026-05-30T10:00:00",
                        "finished_at": "2026-05-30T10:00:03",
                        "duration_ms": 3000,
                        "status": "success",
                        "error_summary": "",
                        "config_hash": "h",
                        "code_version": "v",
                    }
                ]
            ).to_csv(data_dir / "runs.csv", index=False)
            (data_dir / "pending_orders.json").write_text(
                json.dumps([{"side": "buy", "code": "0005.HK"}]),
                encoding="utf-8",
            )
            (reports_dir / "dashboard.html").write_text("gemini hk dashboard", encoding="utf-8")

            out_path = generate_competition_dashboard(repo_root=root, markets=["a_share", "hk", "us"])
            html = out_path.read_text(encoding="utf-8")

            self.assertIn("gemini", html)
            self.assertIn("/pro/hk/gemini.html", html)


if __name__ == "__main__":
    unittest.main()
