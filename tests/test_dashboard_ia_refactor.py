"""Plumbing tests for the 2026-05-24 dashboard IA refactor.

Covers invariants introduced when:

* fragments moved out of ``reports/`` into ``data/_dashboard_build/``
  (so the user-facing reports directory stays clean and only contains
  viewable HTML)
* the unified top nav from ``_dashboard_assets.render_nav_html`` is
  injected into every renderer
* the simple combined view gained a 6-axis differentiation radar
  (compares Claude vs Codex factor tilt) and a market-environment line
  strip (沪深300 / 中证500)
* the pro view groups its 13+ ``<h2>`` sections into 4 sub-tabs
  (结果 / 洞察 / 健康 / 演化)
* ``cli.DASHBOARD_ROUTES`` gained ``/pro/{agent}.html`` aliases so the
  pro side is URL-symmetric with the simple side

See ``data/claude/notes/2026-05-24-dashboard-ia-proposal.md`` for the
full intent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze import cli
from stock_analyze.beginner_dashboard import (
    render_beginner_agent_html,
    render_beginner_competition_html,
)
from stock_analyze.competition import resolve_agent_paths
from stock_analyze.utils import dashboard_fragment_path


# ---------------------------------------------------------------------------
# Fixture helpers (lightweight; we only need data to make renderers happy)


_BASE = {
    "competition_id": "test",
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
    (tmp / "configs" / "competition_a_share.yaml").write_text(json.dumps(_BASE), encoding="utf-8")
    for agent in ("claude", "codex"):
        (tmp / "configs" / "agents" / f"{agent}_a_share.yaml").write_text(
            json.dumps({"agent_id": agent, "strategy_id": f"{agent}_v1", "factors": {"pe": {"weight": 1.0, "direction": "low"}}}),
            encoding="utf-8",
        )


def _seed_agent(tmp: Path, agent: str) -> None:
    data_dir = tmp / "data" / "a_share" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "a_share" / agent).mkdir(parents=True, exist_ok=True)
    (data_dir / "performance_summary.json").write_text(
        json.dumps({
            "accounts": {
                "hs300": {
                    "cumulative_return": 0.02,
                    "cumulative_excess_return": 0.01,
                    "information_ratio": 1.5,
                    "benchmark_code": "000300",
                    "benchmark_label": "沪深300",
                }
            }
        }),
        encoding="utf-8",
    )
    # 60 daily NAV rows so the market-environment strip has enough weekly samples
    # (it down-samples by every 5th observation, then takes the last 12).
    # benchmark_code is written with a leading zero so pandas keeps it as a
    # 6-char string when it reads the CSV (otherwise it auto-infers to int
    # and BENCHMARK_LABEL lookup misses).
    nav_rows = []
    for i in range(60):
        day = pd.Timestamp("2026-03-01") + pd.Timedelta(days=i)
        nav_rows.append({"date": str(day.date()), "account_id": "hs300", "total_value": 500000 + i * 100,
                         "benchmark_code": "000300A", "benchmark_close": 4000 + i * 5})
        nav_rows.append({"date": str(day.date()), "account_id": "hs300", "total_value": 500000 + i * 100,
                         "benchmark_code": "000905A", "benchmark_close": 6000 + i * 8})
    df = pd.DataFrame(nav_rows)
    # Strip the A suffix back off, but keep it as str dtype.
    df["benchmark_code"] = df["benchmark_code"].str.rstrip("A")
    df.to_csv(data_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([
        {"code": "600519", "name": "贵州茅台", "industry": "酒类", "shares": 100,
         "avg_cost": 1340, "last_price": 1295, "market_value": 129500},
    ]).to_csv(data_dir / "positions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([]).to_csv(data_dir / "trades.csv", index=False)
    # Latest signals — needed for the differentiation radar
    pd.DataFrame([
        {"code": "600519", "name": "贵州茅台",
         "pe": 25.0, "pb": 8.0, "roe": 0.30, "momentum_60": 0.05,
         "low_volatility_60": 0.02, "dividend_yield": 0.015},
        {"code": "000333", "name": "美的集团",
         "pe": 15.0, "pb": 3.0, "roe": 0.22, "momentum_60": 0.10,
         "low_volatility_60": 0.03, "dividend_yield": 0.025},
    ]).to_csv(data_dir / "latest_signals.csv", index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Plumbing: fragment path + route table


class FragmentPathTests(unittest.TestCase):
    def test_competition_mode_writes_to_dashboard_build(self) -> None:
        reports_dir = Path("/somewhere/repo/reports/a_share/claude")
        path = dashboard_fragment_path(reports_dir)
        self.assertEqual(path, Path("/somewhere/repo/data/_dashboard_build/a_share/claude/fragment.html"))

    def test_legacy_a_share_mode_writes_to_market_bucket(self) -> None:
        reports_dir = Path("/somewhere/repo/reports/claude")
        path = dashboard_fragment_path(reports_dir)
        self.assertEqual(path, Path("/somewhere/repo/data/_dashboard_build/a_share/claude/fragment.html"))

    def test_legacy_mode_writes_to_default_bucket(self) -> None:
        reports_dir = Path("/somewhere/repo/reports")
        path = dashboard_fragment_path(reports_dir)
        self.assertEqual(path, Path("/somewhere/repo/data/_dashboard_build/_default/fragment.html"))

    def test_fragment_path_is_never_inside_reports(self) -> None:
        # The whole point of the 2026-05-24 refactor: fragments must not
        # land in reports/ or operators will see build artifacts.
        for mode in (
            "/somewhere/repo/reports",
            "/somewhere/repo/reports/claude",
            "/somewhere/repo/reports/codex",
            "/somewhere/repo/reports/a_share/claude",
            "/somewhere/repo/reports/hk/codex",
        ):
            path = dashboard_fragment_path(Path(mode))
            self.assertNotIn("reports", path.parts, f"fragment path leaked into reports for {mode}")
            self.assertIn("_dashboard_build", path.parts)


class DashboardRoutesTests(unittest.TestCase):
    def test_simple_and_pro_url_symmetry(self) -> None:
        # Simple keeps per-agent URLs; professional views use market-aware URLs.
        for url in (
            "/",
            "/simple/claude.html",
            "/simple/codex.html",
            "/pro.html",
            "/pro/a_share/claude.html",
            "/pro/a_share/codex.html",
            "/pro/hk/claude.html",
            "/pro/us/codex.html",
        ):
            self.assertIn(url, cli.DASHBOARD_ROUTES, f"missing route alias: {url}")

    def test_pro_per_agent_aliases_point_to_agent_dashboard(self) -> None:
        self.assertEqual(cli.DASHBOARD_ROUTES["/pro/claude.html"], "/a_share/claude/dashboard.html")
        self.assertEqual(cli.DASHBOARD_ROUTES["/pro/codex.html"], "/a_share/codex/dashboard.html")

    def test_legacy_aliases_still_resolve(self) -> None:
        # Backwards-compat: outside bookmarks that point at the old paths
        # must keep working.
        self.assertEqual(cli.DASHBOARD_ROUTES["/index.html"], "/competition/simple.html")
        self.assertEqual(cli.DASHBOARD_ROUTES["/simple.html"], "/competition/simple.html")


# ---------------------------------------------------------------------------
# Visual contract: nav, sub-tabs, radar, market env all show up where expected


class NavInjectionTests(unittest.TestCase):
    def test_simple_combined_has_dashboard_nav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent)
            paths = {a: resolve_agent_paths(a, repo_root=tmp_path) for a in ("claude", "codex")}
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn('class="dashboard-nav"', html)
            # Combined link is the active one.
            self.assertIn('href="/" data-active="true"', html)
            # Professional nav is now the tri-market entry only; market/agent
            # deep links live in the decision table.
            for href in (
                "/simple/claude.html",
                "/simple/codex.html",
                "/pro.html",
            ):
                self.assertIn(f'href="{href}"', html)
            # Dual timestamp metadata.
            self.assertIn("页面生成", html)
            self.assertIn("数据截至", html)

    def test_simple_solo_agent_has_correct_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude")
            paths = resolve_agent_paths("claude", repo_root=tmp_path)
            html = render_beginner_agent_html(paths, today="2026-05-23")
            self.assertIn('href="/simple/claude.html" data-active="true"', html)
            # And the "两位 AI" copy is replaced by single-agent narration.
            self.assertNotIn("两位 AI", html)
            self.assertNotIn("两个 AI", html)
            self.assertIn("Claude", html)


class DifferentiationRadarTests(unittest.TestCase):
    def test_radar_svg_appears_in_combined_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent)
            paths = {a: resolve_agent_paths(a, repo_root=tmp_path) for a in ("claude", "codex")}
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("差异化雷达", html)
            self.assertIn('class="radar-chart"', html)
            # Both agents' polygons rendered.
            self.assertIn(":polygon", html.replace("<polygon", ":polygon"))
            # Six axis labels present.
            for label in ("PE 低估", "PB 低估", "ROE", "60 日动量", "低波 60", "股息率"):
                self.assertIn(label, html)

    def test_radar_omitted_in_solo_view(self) -> None:
        # The radar is a comparison artifact — meaningless with one agent.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude")
            paths = resolve_agent_paths("claude", repo_root=tmp_path)
            html = render_beginner_agent_html(paths, today="2026-05-23")
            self.assertNotIn("差异化雷达", html)


class MarketEnvironmentTests(unittest.TestCase):
    def test_market_env_strip_renders_when_benchmarks_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            for agent in ("claude", "codex"):
                _seed_agent(tmp_path, agent)
            paths = {a: resolve_agent_paths(a, repo_root=tmp_path) for a in ("claude", "codex")}
            html = render_beginner_competition_html(paths, today="2026-05-23")
            self.assertIn("市场环境", html)
            self.assertIn("沪深300", html)
            self.assertIn("中证500", html)
            self.assertIn('class="mini-line"', html)


# ---------------------------------------------------------------------------
# Pro view sub-tab restructure


class ProSubTabTests(unittest.TestCase):
    def test_pro_view_renders_four_sub_tabs(self) -> None:
        # Render reporting.generate_dashboard against a minimal store.
        # Use a separate tmp dir layout that matches what reporting expects.
        from stock_analyze.reporting import generate_dashboard
        from stock_analyze.store import PortfolioStore

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "data" / "a_share" / "claude"
            reports_dir = tmp_path / "reports" / "a_share" / "claude"
            data_dir.mkdir(parents=True)
            reports_dir.mkdir(parents=True)
            store = PortfolioStore(data_dir)
            store.initialize({"accounts": [{"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000}]})

            config = {
                "agent_id": "claude",
                "strategy_id": "claude_test",
                "accounts": [{"id": "hs300", "scope": "hs300", "cash": 500000, "benchmark": "000300"}],
                "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252,
                                 "forward_ic_horizon_days": 5, "low_coverage_threshold": 0.5},
            }
            path = generate_dashboard(config, store, reports_dir)
            html = path.read_text(encoding="utf-8")

            # Sub-tab nav exists with 4 buttons and JS-driven switching.
            self.assertIn('class="sub-tabs"', html)
            self.assertIn('data-target="results"', html)
            self.assertIn('data-target="insights"', html)
            self.assertIn('data-target="health"', html)
            self.assertIn('data-target="evolution"', html)
            # The 4 content sections are present.
            self.assertIn('data-tab="results"', html)
            self.assertIn('data-tab="insights"', html)
            self.assertIn('data-tab="health"', html)
            self.assertIn('data-tab="evolution"', html)
            # The unified top nav is also present in page mode.
            self.assertIn('class="dashboard-nav"', html)
            # Single-agent pages point users back to the tri-market pro entry.
            self.assertIn('href="/pro.html" data-active="true"', html)


if __name__ == "__main__":
    unittest.main()
