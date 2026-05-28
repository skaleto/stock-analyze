from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.dashboard_aggregator import generate_competition_dashboard


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 10},
    ],
    "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
    "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.10},
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


def _seed_repo(tmp: Path) -> None:
    (tmp / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (tmp / "configs" / "competition_a_share.yaml").write_text(json.dumps(BASELINE_CONFIG), encoding="utf-8")
    for agent_id in ("claude", "codex"):
        path = tmp / "configs" / "agents" / f"{agent_id}_a_share.yaml"
        payload = {"agent_id": agent_id, "strategy_id": f"{agent_id}_v1", "factors": {"pe": {"weight": 1.0, "direction": "low"}}}
        path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_agent_for_dashboard(
    tmp: Path,
    agent: str,
    *,
    fragment: str | None,
    cumulative: float,
    nav_rows: list[tuple[str, float]] | None,
    positions: list[str],
) -> None:
    data_dir = tmp / "data" / "a_share" / agent
    reports_dir = tmp / "reports" / "a_share" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    if fragment is not None:
        # 2026-05-24: fragments moved out of reports/ to data/_dashboard_build/<agent>/
        # See utils.dashboard_fragment_path.
        from stock_analyze.utils import dashboard_fragment_path
        fragment_path = dashboard_fragment_path(reports_dir)
        fragment_path.parent.mkdir(parents=True, exist_ok=True)
        fragment_path.write_text(fragment, encoding="utf-8")
    perf = {
        "accounts": {
            "hs300": {
                "cumulative_return": cumulative,
                "annualized_return": cumulative * 12,
                "sharpe_ratio": 1.2,
                "information_ratio": 0.9,
                "tracking_error": 0.05,
                "max_drawdown": -0.04,
                "weekly_turnover_avg": 0.25,
                "cost_bps": 18.0,
                "round_trip_win_rate": 0.6,
            }
        },
    }
    (data_dir / "performance_summary.json").write_text(json.dumps(perf), encoding="utf-8")
    if nav_rows is not None:
        pd.DataFrame(
            [{"date": d, "account_id": "hs300", "total_value": v} for d, v in nav_rows]
        ).to_csv(data_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [{"account_id": "hs300", "code": code} for code in positions]
    ).to_csv(data_dir / "positions.csv", index=False, encoding="utf-8-sig")


class DashboardAggregatorTests(unittest.TestCase):
    def test_three_tabs_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent_for_dashboard(
                tmp_path, "claude",
                fragment='<section class="agent-dashboard" data-agent="claude"><p>claude content sentinel</p></section>',
                cumulative=0.08,
                nav_rows=[("2026-05-01", 1000000), ("2026-05-15", 1050000), ("2026-05-31", 1080000)],
                positions=["000001", "000002"],
            )
            _seed_agent_for_dashboard(
                tmp_path, "codex",
                fragment='<section class="agent-dashboard" data-agent="codex"><p>codex content sentinel</p></section>',
                cumulative=0.05,
                nav_rows=[("2026-05-01", 1000000), ("2026-05-15", 1030000), ("2026-05-31", 1050000)],
                positions=["000002", "000003"],
            )
            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn('id="tab-claude"', html)
            self.assertIn('id="tab-codex"', html)
            self.assertIn('id="tab-compare"', html)
            self.assertIn("claude content sentinel", html)
            self.assertIn("codex content sentinel", html)
            self.assertIn("comparisonNav", html)
            self.assertIn("累计收益", html)

    def test_missing_codex_fragment_shows_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent_for_dashboard(
                tmp_path, "claude",
                fragment='<section class="agent-dashboard" data-agent="claude"><p>claude content</p></section>',
                cumulative=0.08,
                nav_rows=[("2026-05-01", 1000000)],
                positions=["000001"],
            )
            _seed_agent_for_dashboard(
                tmp_path, "codex",
                fragment=None,
                cumulative=0.05,
                nav_rows=None,
                positions=["000002"],
            )
            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn("尚未生成 Codex 仪表盘", html)
            self.assertIn("claude content", html)

    def test_leaderboard_strip_uses_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent_for_dashboard(tmp_path, "claude", fragment='<section class="agent-dashboard"><p>c</p></section>', cumulative=0.08, nav_rows=[("2026-05-01", 1000000)], positions=["000001"])
            _seed_agent_for_dashboard(tmp_path, "codex", fragment='<section class="agent-dashboard"><p>c</p></section>', cumulative=0.05, nav_rows=[("2026-05-01", 1000000)], positions=["000002"])
            leaderboard_path = tmp_path / "data" / "competition" / "leaderboard.csv"
            leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"month": "2026-04", "claude_return": 0.03, "codex_return": 0.02, "winner_return": "claude", "claude_ir": 0.5, "codex_ir": 0.4, "winner_ir": "claude", "generated_at": "2026-05-01"},
                    {"month": "2026-05", "claude_return": 0.08, "codex_return": 0.05, "winner_return": "claude", "claude_ir": 0.9, "codex_ir": 1.1, "winner_ir": "codex", "generated_at": "2026-06-01"},
                ]
            ).to_csv(leaderboard_path, index=False, encoding="utf-8-sig")

            (tmp_path / "reports" / "competition").mkdir(parents=True, exist_ok=True)
            (tmp_path / "reports" / "competition" / "monthly_review_2026-05.md").write_text("# review", encoding="utf-8")

            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn("2026-05", html)
            self.assertIn("month-block", html)
            self.assertIn("monthly_review_2026-05.md", html)


class ObservationPairingTests(unittest.TestCase):
    def _seed_minimal(self, tmp: Path) -> None:
        _seed_repo(tmp)
        for agent, cum in (("claude", 0.04), ("codex", 0.03)):
            _seed_agent_for_dashboard(
                tmp,
                agent,
                fragment=f'<section class="agent-dashboard" data-agent="{agent}"><p>{agent} sentinel</p></section>',
                cumulative=cum,
                nav_rows=[("2026-05-01", 1000000)],
                positions=["000001"],
            )

    def test_both_agents_have_weekly_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_minimal(tmp_path)
            for agent, body in (("claude", "claude weekly observation"), ("codex", "codex weekly observation")):
                notes_dir = tmp_path / "data" / "a_share" / agent / "notes"
                notes_dir.mkdir(parents=True, exist_ok=True)
                (notes_dir / "2026-05-22-weekly-review.md").write_text(body, encoding="utf-8")
            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn("本周双方观察对照", html)
            self.assertIn("claude weekly observation", html)
            self.assertIn("codex weekly observation", html)
            self.assertIn("observation-grid", html)

    def test_only_one_agent_has_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_minimal(tmp_path)
            notes_dir = tmp_path / "data" / "a_share" / "claude" / "notes"
            notes_dir.mkdir(parents=True, exist_ok=True)
            (notes_dir / "2026-05-22-weekly-review.md").write_text("only claude wrote", encoding="utf-8")
            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn("only claude wrote", html)
            self.assertIn("Codex 本周无笔记", html)

    def test_no_notes_shows_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._seed_minimal(tmp_path)
            out_path = generate_competition_dashboard(agents=["claude", "codex"], repo_root=tmp_path)
            html = out_path.read_text(encoding="utf-8")
            self.assertIn("本周双方观察对照", html)
            self.assertIn("尚未生成 agent 周笔记", html)


if __name__ == "__main__":
    unittest.main()
