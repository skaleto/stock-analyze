from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.beginner_dashboard import (
    render_beginner_agent_html,
    render_beginner_competition_html,
    write_beginner_views,
)
from stock_analyze.competition import resolve_agent_paths


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "start_date": "2026-05-15",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 10},
    ],
    "schedule": {
        "rebalance": "weekly_after_close",
        "signal_day": "last_trading_day_of_week",
        "execution": "next_trading_day_open",
    },
    "trading": {
        "lot_size": 100,
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.0005,
        "slippage_rate": 0.0005,
        "max_single_weight": 0.10,
    },
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


def _seed_repo(tmp: Path) -> None:
    (tmp / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (tmp / "configs" / "competition_a_share.yaml").write_text(
        json.dumps(BASELINE_CONFIG), encoding="utf-8"
    )
    for agent in ("claude", "codex"):
        (tmp / "configs" / "agents" / f"{agent}_a_share.yaml").write_text(
            json.dumps(
                {
                    "agent_id": agent,
                    "strategy_id": f"{agent}_v1",
                    "factors": {"pe": {"weight": 1.0, "direction": "low"}},
                }
            ),
            encoding="utf-8",
        )


def _seed_agent(
    tmp: Path,
    agent: str,
    *,
    perf: dict | None = None,
    nav_rows: list[dict] | None = None,
    positions: list[dict] | None = None,
    trades: list[dict] | None = None,
    evolution_log: dict[str, str] | None = None,
) -> None:
    data_dir = tmp / "data" / "a_share" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "a_share" / agent).mkdir(parents=True, exist_ok=True)
    if perf is not None:
        (data_dir / "performance_summary.json").write_text(
            json.dumps(perf), encoding="utf-8"
        )
    if nav_rows is not None:
        pd.DataFrame(nav_rows).to_csv(
            data_dir / "daily_nav.csv", index=False, encoding="utf-8-sig"
        )
    if positions is not None:
        pd.DataFrame(positions).to_csv(
            data_dir / "positions.csv", index=False, encoding="utf-8-sig"
        )
    if trades is not None:
        pd.DataFrame(trades).to_csv(
            data_dir / "trades.csv", index=False, encoding="utf-8-sig"
        )
    if evolution_log is not None:
        log_dir = data_dir / "evolution_log"
        log_dir.mkdir(parents=True, exist_ok=True)
        for month, body in evolution_log.items():
            (log_dir / f"{month}.md").write_text(body, encoding="utf-8")


def _default_perf(cumulative: float, excess: float) -> dict:
    return {
        "accounts": {
            "hs300": {
                "cumulative_return": cumulative,
                "cumulative_excess_return": excess,
                "information_ratio": 1.5,
                "annualized_return": cumulative * 12,
                "sharpe_ratio": 1.2,
                "tracking_error": 0.05,
                "max_drawdown": -0.04,
            }
        }
    }


def _default_nav_rows(start_value: float = 1_000_000) -> list[dict]:
    rows = []
    base_benchmark = 4800
    for i, d in enumerate(
        [
            "2026-05-15",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
            "2026-05-21",
            "2026-05-22",
        ]
    ):
        rows.append(
            {
                "date": d,
                "account_id": "hs300",
                "cash": 500000,
                "market_value": start_value * (1 + i * 0.002) - 500000,
                "total_value": start_value * (1 + i * 0.002),
                "benchmark_code": "000300",
                "benchmark_close": base_benchmark * (1 + i * 0.001),
                "benchmark_date": d,
                "notes": "daily nav",
            }
        )
    return rows


def _default_positions() -> list[dict]:
    return [
        {
            "account_id": "hs300",
            "code": "600519",
            "name": "贵州茅台",
            "industry": "C15酒、饮料和精制茶制造业",
            "shares": 100,
            "available_shares": 100,
            "avg_cost": 1340.0,
            "last_price": 1295.0,
            "market_value": 129500.0,
            "unrealized_pnl": -4500.0,
            "hold_since": "2026-05-18",
        },
        {
            "account_id": "hs300",
            "code": "000333",
            "name": "美的集团",
            "industry": "C38电气机械和器材制造业",
            "shares": 200,
            "available_shares": 200,
            "avg_cost": 80.0,
            "last_price": 82.5,
            "market_value": 16500.0,
            "unrealized_pnl": 500.0,
            "hold_since": "2026-05-18",
        },
    ]


def _default_trades() -> list[dict]:
    return [
        {
            "trade_date": "2026-05-18",
            "account_id": "hs300",
            "code": "600519",
            "name": "贵州茅台",
            "side": "buy",
            "shares": 100,
            "price": 1340.0,
            "gross_amount": 134000.0,
            "commission": 5.0,
            "stamp_tax": 0.0,
            "slippage": 67.0,
            "reason": "weekly_rebalance",
        },
        {
            "trade_date": "2026-05-22",
            "account_id": "hs300",
            "code": "000333",
            "name": "美的集团",
            "side": "buy",
            "shares": 200,
            "price": 80.0,
            "gross_amount": 16000.0,
            "commission": 5.0,
            "stamp_tax": 0.0,
            "slippage": 8.0,
            "reason": "weekly_rebalance",
        },
    ]


class TabBarAndShellTests(unittest.TestCase):
    def test_top_nav_marks_simple_combined_as_active(self) -> None:
        # 2026-05-24 IA refactor: legacy tab-bar (class="tab active") replaced
        # by the unified .dashboard-nav from _dashboard_assets.render_nav_html.
        # Active state is now signaled via data-active="true" on the matching link.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                    positions=_default_positions(),
                    trades=_default_trades(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            # Unified nav present and the simple-combined entry is active.
            self.assertIn('class="dashboard-nav"', html)
            self.assertIn('href="/" data-active="true"', html)
            # Top nav exposes simple per-agent pages plus the tri-market pro entry.
            self.assertIn('href="/simple/claude.html"', html)
            self.assertIn('href="/simple/codex.html"', html)
            self.assertIn('href="/pro.html"', html)
            # Section cards 1..3 still emitted.
            self.assertIn("data-id=\"1\"", html)
            self.assertIn("data-id=\"2\"", html)
            self.assertIn("data-id=\"3\"", html)

    def test_section_data_ids_in_ascending_order(self) -> None:
        # 2026-05-24 IA refactor: the old data-id="0" tab-bar was removed
        # from the section flow (the new top nav has no data-id). Now the
        # body starts at data-id="1" (account card) and remains ascending.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                    positions=_default_positions(),
                    trades=_default_trades(),
                    evolution_log={"2026-05": "# 2026-05\n\n本月把 PE 权重提升 +3pp,因为低 PE 风格跑赢。"},
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            ids = [int(m) for m in re.findall(r'data-id="(\d+)"', html)]
            self.assertGreaterEqual(len(ids), 7, "expected 7+ body sections")
            self.assertEqual(ids[0], 1)
            self.assertEqual(ids, sorted(ids))
            self.assertGreaterEqual(max(ids), 7)


class AccountCardTests(unittest.TestCase):
    def test_total_assets_aggregate_across_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.01, 0.005),
                    nav_rows=_default_nav_rows(start_value=1_000_000),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("我的账户", html)
            # 两个 agent 各 1.01e6 → 总资产 ≈ 2 02 万元
            self.assertIn("万元", html)


class AgentScoreCardsTests(unittest.TestCase):
    def test_outperform_label_appears_when_excess_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("跑赢", html)
            self.assertIn("沪深300", html)

    def test_underperform_label_when_excess_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(-0.02, -0.01),
                    nav_rows=_default_nav_rows(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("跑输", html)


class NavChartTests(unittest.TestCase):
    def test_svg_emitted_when_data_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("<svg", html)
            self.assertIn("polyline", html)
            self.assertIn("viewBox", html)

    def test_missing_nav_shows_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent, perf=_default_perf(0.0, 0.0))
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("尚未有净值数据", html)


class TopHoldingsTests(unittest.TestCase):
    def test_holdings_table_renders_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.0, 0.0),
                    nav_rows=_default_nav_rows(),
                    positions=_default_positions(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("贵州茅台", html)
            self.assertIn("美的集团", html)
            self.assertIn("酒、饮料和精制茶制造业", html)

    def test_no_positions_shows_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent, perf=_default_perf(0.0, 0.0))
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("尚未开盘交易", html)


class OverlapTests(unittest.TestCase):
    def test_overlap_summary_lists_shared_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            _seed_agent(
                tmp_path,
                "claude",
                perf=_default_perf(0.0, 0.0),
                positions=[
                    {"account_id": "hs300", "code": "600519", "name": "茅台", "market_value": 100},
                    {"account_id": "hs300", "code": "000333", "name": "美的", "market_value": 100},
                ],
            )
            _seed_agent(
                tmp_path,
                "codex",
                perf=_default_perf(0.0, 0.0),
                positions=[
                    {"account_id": "hs300", "code": "000333", "name": "美的", "market_value": 100},
                    {"account_id": "hs300", "code": "601318", "name": "平安", "market_value": 100},
                ],
            )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("两位都持有", html)
            self.assertIn("000333", html)
            self.assertIn("仅 Claude 持有", html)
            self.assertIn("仅 Codex 持有", html)


class RecentTradesTests(unittest.TestCase):
    def test_recent_trades_includes_chinese_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.0, 0.0),
                    trades=_default_trades(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("买入", html)
            self.assertIn("贵州茅台", html)

    def test_no_trades_shows_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent, perf=_default_perf(0.0, 0.0))
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("尚无成交记录", html)


class EvolutionSummaryTests(unittest.TestCase):
    def test_evolution_block_appears_when_log_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            _seed_agent(
                tmp_path,
                "claude",
                perf=_default_perf(0.02, 0.01),
                evolution_log={
                    "2026-05": "# 2026-05\n\n本月把 PE 权重提升 +3pp,因为低 PE 风格跑赢。",
                },
            )
            _seed_agent(tmp_path, "codex", perf=_default_perf(0.01, 0.005))
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("本月策略调整摘要", html)
            self.assertIn("PE 权重", html)

    def test_no_evolution_omits_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent, perf=_default_perf(0.0, 0.0))
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertNotIn("本月策略调整摘要", html)


class EmptyPortfolioGracefulTests(unittest.TestCase):
    def test_empty_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            # No perf, no nav, no positions, no trades.
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent)
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("简化版", html)
            self.assertIn("尚无成交记录", html)
            self.assertLessEqual(len(html.encode("utf-8")), 80 * 1024)


class SizeBudgetTests(unittest.TestCase):
    def test_full_page_under_80kb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            # Generate 20 positions / 20 trades each.
            positions = []
            for i in range(20):
                positions.append(
                    {
                        "account_id": "hs300",
                        "code": f"{600519 + i}",
                        "name": f"测试股{i}",
                        "industry": "C15酒、饮料和精制茶制造业",
                        "shares": 100,
                        "avg_cost": 10.0 + i,
                        "last_price": 11.0 + i,
                        "market_value": 1100.0 + i * 100,
                        "unrealized_pnl": 100.0,
                    }
                )
            trades = []
            for i in range(20):
                trades.append(
                    {
                        "trade_date": "2026-05-22",
                        "account_id": "hs300",
                        "code": f"{600519 + i}",
                        "name": f"测试股{i}",
                        "side": "buy",
                        "shares": 100,
                        "price": 10.0 + i,
                        "gross_amount": 1000.0 + i * 100,
                    }
                )
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                    positions=positions,
                    trades=trades,
                    evolution_log={"2026-05": "# 2026-05\n\n本月策略调整说明。"},
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertLessEqual(
                len(html.encode("utf-8")),
                80 * 1024,
                f"simple.html exceeded 80 KB budget: {len(html.encode('utf-8'))} bytes",
            )


class ProMarkersAbsentTests(unittest.TestCase):
    def test_no_pro_only_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = {
                agent: resolve_agent_paths(agent, repo_root=tmp_path)
                for agent in ("claude", "codex")
            }
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                    positions=_default_positions(),
                    trades=_default_trades(),
                )
            html = render_beginner_competition_html(paths, today="2026-05-23")
            # Pro-only content keywords (per spec); must not appear.
            for marker in ["因子覆盖率", "前向 IC", "因子贡献明细", "运行账本", "数据源状态"]:
                self.assertNotIn(marker, html, f"unexpected pro marker {marker!r} in simple.html")


class WriteBeginnerViewsTests(unittest.TestCase):
    def test_write_creates_simple_and_per_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            for agent in ("claude", "codex"):
                _seed_agent(
                    tmp_path,
                    agent,
                    perf=_default_perf(0.02, 0.01),
                    nav_rows=_default_nav_rows(),
                    positions=_default_positions(),
                    trades=_default_trades(),
                )
            written = write_beginner_views(
                agents=["claude", "codex"],
                repo_root=tmp_path,
                today="2026-05-23",
            )
            self.assertIn("simple", written)
            self.assertIn("claude", written)
            self.assertIn("codex", written)
            self.assertTrue((tmp_path / "reports" / "competition" / "simple.html").exists())
            self.assertTrue(
                (tmp_path / "reports" / "competition" / "simple" / "claude.html").exists()
            )
            self.assertTrue(
                (tmp_path / "reports" / "competition" / "simple" / "codex.html").exists()
            )


class SingleAgentRendererTests(unittest.TestCase):
    def test_render_beginner_agent_html_claude_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            paths = resolve_agent_paths("claude", repo_root=tmp_path)
            _seed_agent(
                tmp_path,
                "claude",
                perf=_default_perf(0.02, 0.01),
                nav_rows=_default_nav_rows(),
                positions=_default_positions(),
                trades=_default_trades(),
            )
            html = render_beginner_agent_html(paths, today="2026-05-23")
            self.assertIn("Claude · 简化版", html)
            self.assertIn("贵州茅台", html)
            # Single-agent view should NOT include 持仓重叠 (cross-agent).
            self.assertNotIn("持仓重叠", html)


if __name__ == "__main__":
    unittest.main()
