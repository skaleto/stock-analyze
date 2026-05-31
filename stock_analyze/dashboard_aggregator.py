"""Assemble a three-tab competition dashboard from per-agent fragments.

Inputs:

- ``reports/<agent>/dashboard_fragment.html`` for each participating agent
- ``data/<agent>/performance_summary.json`` (best-effort)
- ``data/<agent>/daily_nav.csv`` for the comparison NAV chart
- ``data/<agent>/positions.csv`` for the latest overlap bar
- ``data/competition/leaderboard.csv`` for the rolling strip
- ``reports/competition/monthly_review_*.md`` for the link list

Output: ``reports/competition/dashboard.html`` with three CSS-only tabs.

Renders gracefully when some inputs are missing — the spec mandates that
partial state shows placeholders, not errors.
"""

from __future__ import annotations

import json
import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ._dashboard_assets import BASE_CSS, NAV_CSS, render_nav_html
from .beginner_dashboard import write_beginner_views
from . import competition
from .competition import resolve_agent_paths, resolve_market_paths
from .utils import (
    dashboard_fragment_path,
    ensure_dirs,
    format_pct,
    safe_float,
    today as _today,
)


AGENT_COLORS = {
    # Dark Bloomberg palette — must agree with _dashboard_assets BASE_CSS
    # token values (--claude / --codex).
    "claude": "#f59e0b",
    "codex": "#06b6d4",
}
DEFAULT_AGENT_ORDER = ("claude", "codex")
DEFAULT_MARKETS = tuple(competition.MARKETS)
MARKET_LABELS = {
    "a_share": "A股",
    "hk": "港股",
    "us": "美股",
}
MARKET_CURRENCY = {
    "a_share": "¥",
    "hk": "HK$",
    "us": "$",
}
MARKET_INITIAL_CASH = {
    "a_share": 1_000_000.0,
    "hk": 1_000_000.0,
    "us": 150_000.0,
}


@dataclass
class DashboardAgentPaths:
    market: str
    agent_id: str
    repo_root: Path
    data_dir: Path
    reports_dir: Path
    config_path: Path


# _today is imported from .utils as a single canonical helper.
# Tests can still patch `stock_analyze.dashboard_aggregator._today` —
# that targets the local alias name in this module's namespace.


# Expected daily / weekly pipeline tasks. Order = display order.
_DAILY_TASK_ROWS = [
    ("prepare-market-data", "ECS 17:25 Mon-Fri 拉数据 → 触发 daily agents",
     "_pipeline_market_data"),
    ("stock-analyze-claude-daily", "执行待发订单 + 更新 NAV",
     "_pipeline_agent_daily:claude"),
    ("stock-analyze-codex-daily", "执行待发订单 + 更新 NAV",
     "_pipeline_agent_daily:codex"),
    ("aggregate-dashboard (OnSuccess)", "agent daily 完成后自动刷新 competition 聚合页",
     "_pipeline_aggregate_dashboard"),
]
_WEEKLY_TASK_ROWS = [
    ("stock-analyze-weekly-trigger", "ECS Sat 10:00 触发 weekly agents（用周五 cache）",
     "_pipeline_weekly_trigger"),
    ("stock-analyze-claude-weekly", "生成下周一执行的 pending orders + 周报",
     "_pipeline_agent_weekly:claude"),
    ("stock-analyze-codex-weekly", "生成下周一执行的 pending orders + 周报",
     "_pipeline_agent_weekly:codex"),
]


def _runs_today(repo: Path, agent: str, command: str, today: "_dt.date") -> dict | None:
    """Return the latest run row for (agent, command) started today, or None."""
    csv = repo / "data" / "a_share" / agent / "runs.csv"
    if not csv.exists():
        return None
    import pandas as _pd
    try:
        # runs.csv has textually-coded fields (run_id, hash, ISO timestamps)
        df = _pd.read_csv(csv, dtype={
            "run_id": str, "command": str, "as_of": str,
            "started_at": str, "finished_at": str, "status": str,
            "error_summary": str, "config_hash": str, "code_version": str,
        })
    except Exception:  # noqa: BLE001
        return None
    if df.empty:
        return None
    today_iso = today.isoformat()
    today_rows = df[
        (df["command"] == command)
        & df["started_at"].astype(str).str.startswith(today_iso)
        & (df["status"] != "running")  # final state only
    ]
    if today_rows.empty:
        return None
    return today_rows.iloc[-1].to_dict()


def _rollup_7d(repo: Path, agent: str, command: str, today: "_dt.date") -> tuple[int, int]:
    """Return (success_count, failed_count) for (agent, command) in last 7 days."""
    csv = repo / "data" / "a_share" / agent / "runs.csv"
    if not csv.exists():
        return (0, 0)
    import pandas as _pd
    try:
        df = _pd.read_csv(csv, dtype={
            "run_id": str, "command": str, "as_of": str,
            "started_at": str, "finished_at": str, "status": str,
            "error_summary": str, "config_hash": str, "code_version": str,
        })
    except Exception:  # noqa: BLE001
        return (0, 0)
    if df.empty:
        return (0, 0)
    from datetime import timedelta as _td
    cutoff = (today - _td(days=7)).isoformat()
    df = df[(df["command"] == command) & (df["status"] != "running")]
    df = df[df["started_at"].astype(str) >= cutoff]
    success = int((df["status"] == "success").sum())
    failed = int((df["status"] == "failed").sum())
    return (success, failed)


def _status_cell(row: dict | None) -> str:
    """Render today's-status cell HTML."""
    if row is None:
        return '<span class="pending">⏸ 未跑</span>'
    if row.get("status") == "success":
        dur = row.get("duration_ms")
        dur_str = f" {float(dur)/1000:.1f}s" if dur else ""
        ts = row.get("started_at", "")[-8:]  # HH:MM:SS
        return f'<span class="ok">✓ {ts}{dur_str}</span>'
    err = row.get("error_summary", "") or ""
    err_short = (err[:60] + "…") if len(err) > 60 else err
    return f'<span class="fail">✗ {err_short}</span>'


def _market_data_today(repo: Path, today: "_dt.date") -> dict | None:
    """Read data/shared/market_snapshot_<today>.json and synthesise a row."""
    path = repo / "data" / "shared" / f"market_snapshot_{today.isoformat()}.json"
    if not path.exists():
        return None
    try:
        import json as _json
        snap = _json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    return {
        "status": snap.get("status", "unknown"),
        "duration_ms": snap.get("duration_ms"),
        "started_at": snap.get("started_at", ""),
        "error_summary": "; ".join(
            e.get("source", "?") for e in (snap.get("errors") or [])[:2]
        ),
    }


def _aggregate_dashboard_today(repo: Path, today: "_dt.date") -> dict | None:
    """Stat the competition dashboard HTML's mtime; if today, treat as ✓."""
    p = repo / "reports" / "competition" / "dashboard.html"
    if not p.exists():
        return None
    import datetime as _dt
    mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
    if mtime.date() != today:
        return None
    return {
        "status": "success",
        "duration_ms": None,
        "started_at": mtime.isoformat(timespec="seconds"),
        "error_summary": "",
    }


def _recent_failures(log_path: Path | None, today: "_dt.date", limit: int = 5) -> list[str]:
    if log_path is None:
        log_path = Path("/opt/stock-analyze/logs/PIPELINE_FAILURES.log")
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text().split("\n")
    except Exception:  # noqa: BLE001
        return []
    failures = [ln for ln in lines if "\tFAILED\t" in ln]
    return failures[-limit:]


def _is_weekday(d: "_dt.date") -> bool:
    return d.weekday() < 5  # Mon-Fri


def _resolve_today_task(repo: Path, kind: str, today: "_dt.date") -> dict | None:
    if kind == "_pipeline_market_data":
        if not _is_weekday(today):
            return None  # market-data timer doesn't fire on weekends
        return _market_data_today(repo, today)
    if kind.startswith("_pipeline_agent_daily:"):
        agent = kind.split(":", 1)[1]
        if not _is_weekday(today):
            return None
        return _runs_today(repo, agent, "run-daily", today)
    if kind.startswith("_pipeline_agent_weekly:"):
        agent = kind.split(":", 1)[1]
        if today.weekday() < 5:
            return None  # weekly-trigger only fires Sat
        return _runs_today(repo, agent, "run-weekly", today)
    if kind == "_pipeline_weekly_trigger":
        if today.weekday() < 5:
            return None
        # No first-class "trigger" row; check if either agent ran weekly today
        for ag in ("claude", "codex"):
            row = _runs_today(repo, ag, "run-weekly", today)
            if row is not None:
                return row
        return None
    if kind == "_pipeline_aggregate_dashboard":
        return _aggregate_dashboard_today(repo, today)
    return None


def render_pipeline_status_panel(
    repo_root: Path | str,
    *,
    pipeline_failures_log: Path | None = None,
) -> str:
    """Render the pipeline-status panel: today's task list + 7-day rollup.

    Data sources:
      - data/<agent>/runs.csv (per-agent service runs)
      - data/shared/market_snapshot_<today>.json (pipeline data fetch)
      - reports/competition/dashboard.html mtime (aggregator refresh)
      - logs/PIPELINE_FAILURES.log (recent failures from OnFailure hooks)

    Refresh frequency: as fast as dashboard regeneration (after each agent
    service via aggregate-dashboard.service OnSuccess hook).
    """
    root = Path(repo_root)
    today = _today()
    today_label = today.isoformat()
    today_dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today.weekday()]

    lines = [
        f'<div class="panel pipeline-status">',
        f'  <h3>📋 Pipeline 任务清单 · {today_label} ({today_dow})</h3>',
        '  <table class="pipeline-task-table">',
        '    <thead><tr>'
        '<th>任务</th><th>含义</th>'
        '<th>今天</th><th>近 7 日</th>'
        '</tr></thead>',
        '    <tbody>',
    ]

    for task_name, task_desc, kind in _DAILY_TASK_ROWS + _WEEKLY_TASK_ROWS:
        row = _resolve_today_task(root, kind, today)
        status_html = _status_cell(row)

        # 7-day rollup
        if kind.startswith("_pipeline_agent_daily:"):
            ag = kind.split(":", 1)[1]
            s, f = _rollup_7d(root, ag, "run-daily", today)
        elif kind.startswith("_pipeline_agent_weekly:"):
            ag = kind.split(":", 1)[1]
            s, f = _rollup_7d(root, ag, "run-weekly", today)
        else:
            s, f = (0, 0)  # not tracked in runs.csv

        rollup_html = (
            f'<span class="rollup">✓{s} ✗{f}</span>'
            if (s + f) > 0 else '<span class="rollup">—</span>'
        )

        lines.append(
            f'      <tr>'
            f'<td><code>{task_name}</code></td>'
            f'<td>{task_desc}</td>'
            f'<td>{status_html}</td>'
            f'<td>{rollup_html}</td>'
            f'</tr>'
        )

    lines.append('    </tbody>')
    lines.append('  </table>')

    # Recent failures (if any)
    failures = _recent_failures(pipeline_failures_log, today)
    if failures:
        lines.append('  <h4>⚠️ 最近 PIPELINE_FAILURES.log</h4>')
        lines.append('  <ul class="recent-failures">')
        for f in failures:
            lines.append(f'    <li><code>{f}</code></li>')
        lines.append('  </ul>')

    lines.append('</div>')
    return "\n".join(lines)


def render_sentiment_comparison_panel(repo_root: Path | str) -> str:
    """Render the cross-LLM, tri-market sentiment comparison panel."""
    from stock_analyze.markets.a_share.alt_factors import sentiment as _alt_sent

    root = Path(repo_root)
    market_rows: list[str] = []
    has_complete_pair = False
    for market in DEFAULT_MARKETS:
        label = MARKET_LABELS.get(market, market)
        claude_rows = _alt_sent.load_sentiment_history(
            "claude", root, last_n=26, market=market,
        )
        codex_rows = _alt_sent.load_sentiment_history(
            "codex", root, last_n=26, market=market,
        )
        if not claude_rows or not codex_rows:
            market_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                '<td colspan="5">尚无足够数据：至少需要两个 agent 都有记录。</td>'
                '</tr>'
            )
            continue

        has_complete_pair = True
        latest_c = claude_rows[-1]
        latest_x = codex_rows[-1]
        diff = latest_c.score - latest_x.score
        market_rows.append(
            '<tr>'
            f'<td>{html.escape(label)}</td>'
            f'<td>{latest_c.week_end.isoformat()}</td>'
            f'<td>claude {latest_c.score:+.2f} / {latest_c.confidence:.2f}</td>'
            f'<td>codex {latest_x.score:+.2f} / {latest_x.confidence:.2f}</td>'
            f'<td>{diff:+.2f}</td>'
            f'<td>{html.escape("; ".join(latest_c.drivers[:2] + latest_x.drivers[:2]))}</td>'
            '</tr>'
        )

    if not has_complete_pair:
        return (
            '<div class="panel">\n'
            '  <h3>claude vs codex 三市场情绪（过去 26 周）</h3>\n'
            '  <table>\n'
            '    <tr><th>市场</th><th>week_end</th><th>claude score/conf</th>'
            '<th>codex score/conf</th><th>差值</th><th>关键驱动</th></tr>\n'
            f'    {"".join(market_rows)}\n'
            '  </table>\n'
            '</div>'
        )

    return (
        f'<div class="panel">\n'
        f'  <h3>claude vs codex 三市场情绪（过去 26 周）</h3>\n'
        f'  <table>\n'
        f'    <tr><th>市场</th><th>week_end</th><th>claude score/conf</th>'
        f'<th>codex score/conf</th><th>差值</th><th>关键驱动</th></tr>\n'
        f'    {"".join(market_rows)}\n'
        f'  </table>\n'
        f'</div>'
    )


def _normalize_markets(market: str, markets: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = list(markets) if markets is not None else ([market] if market != "all" else list(DEFAULT_MARKETS))
    if not requested:
        requested = list(DEFAULT_MARKETS)
    selected: list[str] = []
    for item in requested:
        if item == "all":
            for known in DEFAULT_MARKETS:
                if known not in selected:
                    selected.append(known)
            continue
        if item not in competition.MARKETS:
            raise competition.UnknownMarket(item)
        if item not in selected:
            selected.append(item)
    return selected


def _has_runtime_data(data_dir: Path) -> bool:
    return any(
        (data_dir / name).exists()
        for name in (
            "daily_nav.csv",
            "runs.csv",
            "pending_orders.json",
            "performance_summary.json",
            "positions.csv",
            "latest_signals.csv",
        )
    )


def _resolve_dashboard_paths(market: str, agent: str, root: Path) -> DashboardAgentPaths:
    paths = resolve_market_paths(market, agent, repo_root=root)
    data_dir = paths.data_dir
    reports_dir = paths.reports_dir
    if market == "a_share" and not _has_runtime_data(data_dir):
        legacy_data = root / "data" / agent
        legacy_reports = root / "reports" / agent
        if _has_runtime_data(legacy_data):
            data_dir = legacy_data
            if legacy_reports.exists():
                reports_dir = legacy_reports
    return DashboardAgentPaths(
        market=market,
        agent_id=agent,
        repo_root=root,
        data_dir=data_dir,
        reports_dir=reports_dir,
        config_path=paths.config_path,
    )


def _build_market_paths(
    markets: list[str],
    agents: list[str],
    root: Path,
) -> dict[str, dict[str, DashboardAgentPaths]]:
    return {
        market: {
            agent: _resolve_dashboard_paths(market, agent, root)
            for agent in agents
            if (root / "configs" / "agents" / f"{agent}_{market}.yaml").exists()
        }
        for market in markets
    }


def _agents_for_markets(markets: list[str], root: Path) -> list[str]:
    ordered: list[str] = []
    for preferred in DEFAULT_AGENT_ORDER:
        if any(preferred in competition.list_agents_for_market(market, root) for market in markets):
            ordered.append(preferred)
    for market in markets:
        for agent in competition.list_agents_for_market(market, root):
            if agent not in ordered:
                ordered.append(agent)
    return ordered


def generate_competition_dashboard(
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
    *,
    market: str = "a_share",
    markets: list[str] | None = None,
) -> Path:
    """Render and persist ``reports/competition/dashboard.html``."""

    root = Path(repo_root) if repo_root else Path.cwd()
    selected_markets = _normalize_markets(market, markets)
    agents = agents or _agents_for_markets(selected_markets, root)
    paths_by_market = _build_market_paths(selected_markets, agents, root)
    primary_market = "a_share" if "a_share" in paths_by_market else selected_markets[0]
    paths_by_agent = paths_by_market.get(primary_market) or {
        agent: resolve_agent_paths(agent, repo_root=root) for agent in agents
    }
    primary_agents = [agent for agent in agents if agent in paths_by_agent]

    fragments = {agent: _read_fragment(paths_by_agent[agent].reports_dir) for agent in primary_agents}
    perf = {agent: _read_performance_summary(paths_by_agent[agent].data_dir) for agent in primary_agents}
    nav_panel = _build_nav_panel(paths_by_agent)
    leaderboard = _read_leaderboard(root / "data" / "competition" / "leaderboard.csv")
    monthly_links = _list_monthly_reviews(root / "reports" / "competition")
    positions_overlap = _compute_position_overlap(paths_by_agent)
    comparison_table = _build_comparison_table(perf)
    summary_cards = _render_summary_cards(perf, leaderboard)
    nav_json = json.dumps(nav_panel, ensure_ascii=False)
    leaderboard_json = json.dumps(leaderboard, ensure_ascii=False)
    all_market_html = _render_all_market_observer(selected_markets, agents, paths_by_market, root)

    out_dir = root / "reports" / "competition"
    ensure_dirs(out_dir)
    out_path = out_dir / "dashboard.html"

    tabs_nav = _render_tabs_nav(primary_agents)
    tab_sections = _render_tab_sections(
        primary_agents,
        fragments,
        summary_cards,
        comparison_table,
        positions_overlap,
        leaderboard,
        monthly_links,
        paths_by_agent,
        all_market_html=all_market_html,
    )

    html = _render_page(tabs_nav, tab_sections, nav_json, leaderboard_json)
    out_path.write_text(html, encoding="utf-8")

    # Also render the beginner simplified view alongside the professional dashboard.
    # Best-effort: if beginner rendering fails (e.g. unexpected data shape), the
    # professional view must still be produced. We re-raise the error after a
    # warning so the failure surfaces in the run ledger.
    try:
        write_beginner_views(agents=primary_agents, repo_root=root)
    except Exception as exc:  # noqa: BLE001
        # Don't let beginner-view crashes break the professional dashboard write
        # that the caller already depends on. The error message is logged but
        # the pro file is already on disk.
        import sys

        print(
            f"warning: beginner dashboard render failed: {exc}",
            file=sys.stderr,
        )
    return out_path


# ---------------------------------------------------------------------------
# Inputs


def _read_fragment(reports_dir: Path) -> str | None:
    # Fragments live under data/_dashboard_build/<agent>/, NOT in reports/.
    # See utils.dashboard_fragment_path docstring for rationale (2026-05-24).
    path = dashboard_fragment_path(reports_dir)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_performance_summary(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "performance_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_leaderboard(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        # month "YYYY-MM" string — defensive against int coercion
        df = pd.read_csv(path, dtype={"month": str})
    except Exception:  # noqa: BLE001
        return []
    return df.to_dict(orient="records")


def _list_monthly_reviews(reports_dir: Path) -> list[dict[str, str]]:
    if not reports_dir.exists():
        return []
    files = sorted(reports_dir.glob("monthly_review_*.md"))
    return [
        {"month": path.stem.replace("monthly_review_", ""), "href": path.name}
        for path in reversed(files)
    ]


# ---------------------------------------------------------------------------
# Aggregations


def _build_nav_panel(paths_by_agent: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    panel: dict[str, list[dict[str, Any]]] = {}
    for agent, paths in paths_by_agent.items():
        nav_path = paths.data_dir / "daily_nav.csv"
        if not nav_path.exists():
            panel[agent] = []
            continue
        try:
            # Keep benchmark_code as str so '000300' isn't coerced to int 300
            df = pd.read_csv(nav_path, dtype={"benchmark_code": str})
        except Exception:  # noqa: BLE001
            panel[agent] = []
            continue
        if df.empty:
            panel[agent] = []
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
        df = df.dropna(subset=["date"])
        grouped = df.groupby("date")["total_value"].sum().sort_index().reset_index()
        panel[agent] = grouped.to_dict(orient="records")
    return panel


def _compute_position_overlap(paths_by_agent: dict[str, Any]) -> dict[str, Any]:
    sets: dict[str, set[str]] = {}
    for agent, paths in paths_by_agent.items():
        path = paths.data_dir / "positions.csv"
        if not path.exists():
            sets[agent] = set()
            continue
        try:
            df = pd.read_csv(path, dtype={"code": str})
        except Exception:  # noqa: BLE001
            sets[agent] = set()
            continue
        if df.empty or "code" not in df.columns:
            sets[agent] = set()
            continue
        sets[agent] = {str(value).zfill(6) for value in df["code"].dropna().tolist()}
    agents = list(sets.keys())
    if len(agents) != 2:
        return {"shared": [], "exclusives": {}}
    a, b = agents
    shared = sorted(sets[a] & sets[b])
    return {
        "shared": shared,
        "exclusives": {
            a: sorted(sets[a] - sets[b]),
            b: sorted(sets[b] - sets[a]),
        },
        "agents": agents,
    }


def _build_comparison_table(perf: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = [
        ("累计收益", "cumulative_return", "pct"),
        ("年化收益", "annualized_return", "pct"),
        ("Sharpe", "sharpe_ratio", "ratio"),
        ("信息比率", "information_ratio", "ratio"),
        ("跟踪误差", "tracking_error", "pct"),
        ("最大回撤", "max_drawdown", "pct"),
        ("周换手率", "weekly_turnover_avg", "pct"),
        ("成本(bps)", "cost_bps", "bps"),
        ("Win Rate", "round_trip_win_rate", "pct"),
    ]
    rows: list[dict[str, Any]] = []
    for label, key, kind in metrics:
        per_agent: dict[str, str] = {}
        raw: dict[str, float | None] = {}
        for agent, summary in perf.items():
            accounts = (summary or {}).get("accounts") or {}
            values = [safe_float(account.get(key)) for account in accounts.values()]
            values = [value for value in values if value is not None]
            value = sum(values) / len(values) if values else None
            raw[agent] = value
            per_agent[agent] = _format_metric(value, kind)
        winner = _pick_winner(raw, prefer_higher=key not in {"max_drawdown", "tracking_error", "cost_bps", "weekly_turnover_avg"})
        rows.append({"label": label, "values": per_agent, "winner": winner})
    return rows


def _format_metric(value: float | None, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return format_pct(value)
    if kind == "bps":
        return f"{value:.1f}"
    return f"{value:.2f}"


def _pick_winner(raw: dict[str, float | None], prefer_higher: bool) -> str | None:
    valid = {agent: value for agent, value in raw.items() if value is not None}
    if not valid:
        return None
    if prefer_higher:
        return max(valid, key=valid.get)
    return min(valid, key=valid.get)


def _read_latest_nav(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "daily_nav.csv"
    if not path.exists():
        return {"latest": None, "change_1d": None, "date": None}
    try:
        df = pd.read_csv(path, dtype={"date": str, "account_id": str})
    except Exception:  # noqa: BLE001
        return {"latest": None, "change_1d": None, "date": None}
    if df.empty or "total_value" not in df.columns or "date" not in df.columns:
        return {"latest": None, "change_1d": None, "date": None}
    grouped = df.groupby("date")["total_value"].sum().sort_index()
    if grouped.empty:
        return {"latest": None, "change_1d": None, "date": None}
    latest = safe_float(grouped.iloc[-1])
    prev = safe_float(grouped.iloc[-2]) if len(grouped) >= 2 else None
    change = (latest / prev - 1.0) if latest is not None and prev not in (None, 0) else None
    return {"latest": latest, "change_1d": change, "date": str(grouped.index[-1])}


def _format_market_money(value: float | None, market: str) -> str:
    if value is None:
        return "-"
    currency = MARKET_CURRENCY.get(market, "")
    if abs(value) >= 1_000_000:
        return f"{currency}{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{currency}{value / 1_000:.0f}K"
    return f"{currency}{value:.0f}"


def _read_latest_run(data_dir: Path, command: str) -> dict[str, Any] | None:
    path = data_dir / "runs.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:  # noqa: BLE001
        return None
    if df.empty or "command" not in df.columns:
        return None
    rows = df[(df["command"] == command) & (df.get("status", "") != "running")]
    if rows.empty:
        return None
    return rows.iloc[-1].to_dict()


def _status_badge(row: dict[str, Any] | None, *, missing: str = "未运行") -> str:
    if row is None:
        return f'<span class="pending">{html.escape(missing)}</span>'
    status = row.get("status") or "unknown"
    started = str(row.get("started_at") or "")[:19].replace("T", " ")
    if status == "success":
        return f'<span class="ok">OK {html.escape(started)}</span>'
    if status == "failed":
        err = str(row.get("error_summary") or "")
        return f'<span class="fail">失败 {html.escape(err[:48])}</span>'
    return f'<span class="pending">{html.escape(status)} {html.escape(started)}</span>'


def _read_pending_summary(data_dir: Path) -> dict[str, int]:
    path = data_dir / "pending_orders.json"
    if not path.exists():
        return {"total": 0, "buy": 0, "sell": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"total": 0, "buy": 0, "sell": 0}
    if isinstance(payload, dict):
        raw_orders = payload.get("orders") or []
    elif isinstance(payload, list):
        raw_orders = payload
    else:
        raw_orders = []
    orders: list[dict[str, Any]] = []
    for item in raw_orders:
        if isinstance(item, dict) and isinstance(item.get("orders"), list):
            orders.extend(order for order in item["orders"] if isinstance(order, dict))
        elif isinstance(item, dict):
            orders.append(item)
    buy = sum(1 for order in orders if str(order.get("side", "")).lower() == "buy")
    sell = sum(1 for order in orders if str(order.get("side", "")).lower() == "sell")
    return {"total": len(orders), "buy": buy, "sell": sell}


def _latest_weekly_report_link(market: str, agent: str, reports_dir: Path) -> str:
    path = reports_dir / "weekly_report.md"
    if not path.exists():
        return '<span class="pending">无周报</span>'
    if reports_dir.parent.name == "reports":
        href = f"/{agent}/weekly_report.md"
    else:
        href = f"/{market}/{agent}/weekly_report.md"
    return (
        f'<a href="{html.escape(href)}">'
        f'{html.escape(path.name)}</a>'
    )


def _latest_monthly_status(root: Path, market: str) -> str:
    if market != "a_share":
        return '<span class="pending">未配置</span>'
    reports_dir = root / "reports" / "competition"
    files = sorted(reports_dir.glob("monthly_review_*.md"))
    if not files:
        return '<span class="pending">无月报</span>'
    latest = files[-1]
    return f'<a href="/competition/{html.escape(latest.name)}">{html.escape(latest.stem.replace("monthly_review_", ""))}</a>'


def _render_all_market_observer(
    markets: list[str],
    agents: list[str],
    paths_by_market: dict[str, dict[str, DashboardAgentPaths]],
    root: Path,
) -> str:
    market_cards: list[str] = []
    decision_rows: list[str] = []
    task_rows: list[str] = []
    for market in markets:
        label = MARKET_LABELS.get(market, market)
        agent_paths = paths_by_market.get(market, {})
        nav_bits: list[str] = []
        for agent in agents:
            paths = agent_paths.get(agent)
            if paths is None:
                continue
            nav = _read_latest_nav(paths.data_dir)
            baseline = MARKET_INITIAL_CASH.get(market, 1.0)
            latest = nav["latest"]
            ret = (latest / baseline - 1.0) if latest is not None and baseline else None
            nav_bits.append(
                f'<div><strong>{html.escape(agent)}</strong> '
                f'<span class="num">{_format_market_money(latest, market)}</span> '
                f'<span class="{"pos" if (ret or 0) >= 0 else "neg"}">{format_pct(ret)}</span></div>'
            )
            pending = _read_pending_summary(paths.data_dir)
            decision_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(agent)}</td>'
                f'<td><a href="/pro/{html.escape(market)}/{html.escape(agent)}.html">专业页</a></td>'
                f'<td class="num">目标订单 {pending["total"]} '
                f'(买 {pending["buy"]} / 卖 {pending["sell"]})</td>'
                f'<td>{_latest_weekly_report_link(market, agent, paths.reports_dir)}</td>'
                '</tr>'
            )
            task_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(agent)}</td>'
                '<td>日任务 <code>run-daily</code></td>'
                f'<td>{_status_badge(_read_latest_run(paths.data_dir, "run-daily"))}</td>'
                '</tr>'
            )
            task_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(agent)}</td>'
                '<td>周任务 <code>run-weekly</code></td>'
                f'<td>{_status_badge(_read_latest_run(paths.data_dir, "run-weekly"))}</td>'
                '</tr>'
            )
        task_rows.append(
            '<tr>'
            f'<td>{html.escape(label)}</td>'
            '<td>market</td>'
            '<td>月任务 <code>competition-monthly-review</code></td>'
            f'<td>{_latest_monthly_status(root, market)}</td>'
            '</tr>'
        )
        nav_html = "".join(nav_bits) or '<p class="empty">暂无 NAV</p>'
        market_cards.append(
            '<section class="metric-card market-card">'
            f'<div class="card-label">{html.escape(label)}</div>'
            f'<div class="market-nav-lines">{nav_html}</div>'
            '</section>'
        )

    decisions = (
        '<table class="comparison market-decisions"><thead>'
        '<tr><th>市场</th><th>Agent</th><th>决策入口</th><th>最新决策</th><th>周报</th></tr>'
        '</thead><tbody>'
        + "".join(decision_rows)
        + '</tbody></table>'
    )
    tasks = (
        '<table class="comparison market-task-matrix"><thead>'
        '<tr><th>市场</th><th>主体</th><th>任务</th><th>最近状态</th></tr>'
        '</thead><tbody>'
        + "".join(task_rows)
        + '</tbody></table>'
    )
    return (
        '<section class="all-market-observer">'
        '<h2>三市场总览</h2>'
        f'<section class="grid market-overview-grid">{"".join(market_cards)}</section>'
        '<h2>三市场具体决策</h2>'
        f'<div class="panel">{decisions}</div>'
        '<h2>日/周/月任务运行情况</h2>'
        f'<div class="panel">{tasks}</div>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Rendering


def _render_summary_cards(perf: dict[str, dict[str, Any]], leaderboard: list[dict[str, Any]]) -> list[dict[str, str]]:
    cumulative: dict[str, float | None] = {}
    for agent, summary in perf.items():
        accounts = (summary or {}).get("accounts") or {}
        values = [safe_float(account.get("cumulative_return")) for account in accounts.values()]
        values = [value for value in values if value is not None]
        cumulative[agent] = sum(values) / len(values) if values else None

    spread = None
    if all(value is not None for value in cumulative.values()) and len(cumulative) >= 2:
        agents = list(cumulative.keys())
        spread = cumulative[agents[0]] - cumulative[agents[1]]

    if leaderboard:
        latest = leaderboard[-1]
        winner = latest.get("winner_return") or "-"
        month = latest.get("month") or "-"
    else:
        winner = "-"
        month = "-"

    cards = []
    for agent in DEFAULT_AGENT_ORDER:
        cards.append(
            {
                "label": f"{agent.capitalize()} 累计收益",
                "value": format_pct(cumulative.get(agent)),
                "tone": "primary",
            }
        )
    cards.append(
        {
            "label": "累计差(Claude − Codex)",
            "value": format_pct(spread) if spread is not None else "-",
            "tone": "primary",
        }
    )
    cards.append(
        {
            "label": f"最近一月胜方 ({month})",
            "value": str(winner),
            "tone": "primary",
        }
    )
    return cards


def _render_tabs_nav(agents: list[str]) -> str:
    items = []
    for agent in agents:
        items.append(f'<a href="#tab-{agent}" class="tab-link">{agent.capitalize()}</a>')
    items.append('<a href="#tab-compare" class="tab-link">对比</a>')
    return '<nav class="tabs">' + "".join(items) + "</nav>"


def _render_tab_sections(
    agents: list[str],
    fragments: dict[str, str | None],
    summary_cards: list[dict[str, str]],
    comparison_table: list[dict[str, Any]],
    positions_overlap: dict[str, Any],
    leaderboard: list[dict[str, Any]],
    monthly_links: list[dict[str, str]],
    paths_by_agent: dict[str, Any] | None = None,
    *,
    all_market_html: str = "",
) -> str:
    sections: list[str] = []
    for agent in agents:
        fragment = fragments.get(agent)
        if fragment:
            body = fragment
        else:
            body = (
                f'<p class="empty">尚未生成 {agent.capitalize()} 仪表盘；'
                f'请先跑 <code>python3 -m stock_analyze --agent {agent} run-weekly</code>。</p>'
            )
        sections.append(
            f'<section id="tab-{agent}" class="tab-section">\n<h1 class="tab-title">{agent.capitalize()}</h1>\n{body}\n</section>'
        )

    cards_html = "".join(
        f'<section class="metric-card metric-{card["tone"]}"><div class="card-label">{card["label"]}</div>'
        f'<div class="metric">{card["value"]}</div></section>'
        for card in summary_cards
    )

    table_rows = []
    agent_names = [agent for agent in agents]
    header_cells = "".join(f"<th>{agent.capitalize()}</th>" for agent in agent_names)
    table_rows.append(f'<tr><th>指标</th>{header_cells}<th>胜方</th></tr>')
    for row in comparison_table:
        cells = []
        for agent in agent_names:
            cells.append(f'<td>{row["values"].get(agent, "-")}</td>')
        winner = row.get("winner") or "-"
        table_rows.append(
            f'<tr><th class="metric-label">{row["label"]}</th>{"".join(cells)}<td><strong>{winner}</strong></td></tr>'
        )
    table_html = '<table class="comparison"><thead>' + table_rows[0] + "</thead><tbody>" + "".join(table_rows[1:]) + "</tbody></table>"

    overlap_html = _render_overlap_bar(positions_overlap)
    leaderboard_html = _render_leaderboard_strip(leaderboard)
    monthly_html = _render_monthly_links(monthly_links)
    if paths_by_agent:
        observation_html = _render_observation_pairing(agents, {agent: paths_by_agent[agent].data_dir for agent in agents if agent in paths_by_agent})
    else:
        observation_html = _render_observation_pairing(agents, {})

    # Pipeline status (refreshes with the aggregate dashboard via OnSuccess)
    try:
        pipeline_status_html = render_pipeline_status_panel(repo_root=Path.cwd())
    except Exception as exc:  # noqa: BLE001
        pipeline_status_html = (
            f'<div class="panel"><p>pipeline-status panel render error: {exc}</p></div>'
        )
    try:
        sentiment_status_html = render_sentiment_comparison_panel(repo_root=Path.cwd())
    except Exception as exc:  # noqa: BLE001
        sentiment_status_html = (
            f'<div class="panel"><p>sentiment panel render error: {exc}</p></div>'
        )

    compare_section = (
        '<section id="tab-compare" class="tab-section">\n'
        '<h1 class="tab-title">三市场观察台</h1>\n'
        f'{all_market_html}\n'
        '<h2>三市场情绪反馈</h2>\n'
        f'{sentiment_status_html}\n'
        '<h2>A股双 Agent 对比</h2>\n'
        f'<section class="grid summary-grid">{cards_html}</section>\n'
        '<h2>📋 Pipeline 任务清单</h2>\n'
        f'{pipeline_status_html}\n'
        '<h2>累计净值曲线</h2>\n'
        '<div class="panel"><canvas id="comparisonNav" width="1200" height="320"></canvas>'
        '<div class="hint">两条曲线分别代表两个 agent 的总资产；颜色与 tab 颜色一致。</div></div>\n'
        '<h2>关键指标横向对比</h2>\n'
        f'<div class="panel">{table_html}</div>\n'
        '<h2>持仓重叠度</h2>\n'
        f'<div class="panel">{overlap_html}</div>\n'
        '<h2>滚动战绩</h2>\n'
        f'<div class="panel"><section class="leaderboard-strip">{leaderboard_html}</section></div>\n'
        '<h2>月度报告</h2>\n'
        f'<div class="panel">{monthly_html}</div>\n'
        '<h2>本周双方观察对照</h2>\n'
        f'<div class="panel observation-pairing">{observation_html}</div>\n'
        '</section>'
    )
    sections.append(compare_section)
    return "\n".join(sections)


MAX_OBSERVATION_BYTES = 12 * 1024


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate_text(text: str, limit: int = MAX_OBSERVATION_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n…(truncated)"


def _latest_weekly_note(data_dir: Path) -> Path | None:
    notes_dir = data_dir / "notes"
    if not notes_dir.exists():
        return None
    candidates = sorted(
        [path for path in notes_dir.glob("*-weekly-review.md") if path.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _render_observation_pairing(agents: list[str], data_dirs: dict[str, Path]) -> str:
    """Render side-by-side latest weekly observations for the given agents."""

    if len(agents) < 2 or not data_dirs:
        return '<p class="empty">尚未生成 agent 周笔记。运行 <code>/weekly-review claude</code> / <code>do weekly review for codex</code> 后会出现。</p>'

    panels: list[str] = []
    have_any = False
    for agent in agents:
        path = _latest_weekly_note(data_dirs.get(agent, Path("/dev/null/missing")))
        label = agent.capitalize()
        if path is None:
            panels.append(
                f'<div class="observation-cell"><h3>{label}</h3>'
                f'<p class="empty">{label} 本周无笔记</p></div>'
            )
            continue
        have_any = True
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            panels.append(
                f'<div class="observation-cell"><h3>{label}</h3>'
                f'<p class="empty">{label}: 读取失败</p></div>'
            )
            continue
        safe = _escape_html(_truncate_text(text))
        panels.append(
            f'<div class="observation-cell"><h3>{label}</h3>'
            f'<details open><summary>{_escape_html(path.name)}</summary>'
            f'<pre style="white-space:pre-wrap;font-family:inherit">{safe}</pre>'
            '</details></div>'
        )

    if not have_any:
        return '<p class="empty">尚未生成 agent 周笔记。运行 <code>/weekly-review claude</code> / <code>do weekly review for codex</code> 后会出现。</p>'

    return (
        '<div class="observation-grid">' + "".join(panels) + "</div>"
    )


def _render_overlap_bar(overlap: dict[str, Any]) -> str:
    if not overlap.get("agents"):
        return '<p class="empty">尚无持仓数据。</p>'
    a, b = overlap["agents"]
    shared = overlap.get("shared", [])
    ex_a = overlap["exclusives"].get(a, [])
    ex_b = overlap["exclusives"].get(b, [])
    total = max(len(shared) + len(ex_a) + len(ex_b), 1)
    seg_shared = len(shared) / total * 100
    seg_a = len(ex_a) / total * 100
    seg_b = len(ex_b) / total * 100
    return (
        '<div class="overlap-bar">'
        f'<span class="seg seg-a" style="width:{seg_a:.1f}%" title="仅 {a}: {len(ex_a)} 只">{a} 独占 {len(ex_a)}</span>'
        f'<span class="seg seg-shared" style="width:{seg_shared:.1f}%" title="共有: {len(shared)} 只">共有 {len(shared)}</span>'
        f'<span class="seg seg-b" style="width:{seg_b:.1f}%" title="仅 {b}: {len(ex_b)} 只">{b} 独占 {len(ex_b)}</span>'
        '</div>'
        f'<div class="hint">Jaccard 重叠度 = {len(shared) / max(len(shared) + len(ex_a) + len(ex_b), 1):.2%}</div>'
    )


def _render_leaderboard_strip(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">尚未生成月度对比。运行 <code>competition-monthly-review</code> 后会出现。</p>'
    blocks = []
    for row in rows[-24:]:
        month = row.get("month") or "-"
        winner = row.get("winner_return") or "-"
        cls = "win-claude" if winner == "claude" else "win-codex" if winner == "codex" else "win-tie"
        blocks.append(f'<span class="month-block {cls}" title="{month}: {winner}">{month}</span>')
    return "".join(blocks)


def _render_monthly_links(links: list[dict[str, str]]) -> str:
    if not links:
        return '<p class="empty">暂无月度报告。运行 <code>competition-monthly-review</code> 后会出现。</p>'
    items = "".join(f'<li><a href="{link["href"]}">{link["month"]}</a></li>' for link in links)
    return f'<ul class="monthly-review-links">{items}</ul>'


def _render_page(tabs_nav: str, tab_sections: str, nav_json: str, leaderboard_json: str) -> str:
    generated = datetime.now()
    generated_at = generated.strftime("%Y-%m-%d %H:%M:%S")
    color_claude = AGENT_COLORS.get("claude", "#f59e0b")
    color_codex = AGENT_COLORS.get("codex", "#06b6d4")
    top_nav = render_nav_html(active="pro", generated_at=generated)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Claude vs Codex · Competition Dashboard</title>
  <style>{BASE_CSS}
{NAV_CSS}
{_COMPETITION_CSS}
:root {{
  --claude: {color_claude};
  --codex: {color_codex};
}}</style>
</head>
<body>
  {top_nav}
  <header class="page-header">
    <h1>Claude <span class="vs">vs</span> Codex · Paper Trading Competition</h1>
    <div class="subhead">生成时间 {generated_at} · 仅模拟交易，不构成任何投资建议</div>
  </header>
  {tabs_nav}
  <main>
    {tab_sections}
  </main>
  <script>
    const navPanel = {nav_json};
    const leaderboardData = {leaderboard_json};
    const colors = {{ claude: "{color_claude}", codex: "{color_codex}" }};

    function drawComparisonNav() {{
      const canvas = document.getElementById('comparisonNav');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = '13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      const agents = Object.keys(navPanel);
      const allValues = agents.flatMap(a => navPanel[a].map(r => Number(r.total_value))).filter(Number.isFinite);
      if (!allValues.length) {{
        ctx.fillStyle = '#8b95a7';
        ctx.fillText('暂无对比净值数据，等两侧都跑过至少 2 个 NAV 日。', 24, 40);
        return;
      }}
      const min = Math.min(...allValues);
      const max = Math.max(...allValues);
      const pad = 42;
      ctx.strokeStyle = '#2a3145';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad, pad);
      ctx.lineTo(pad, canvas.height - pad);
      ctx.lineTo(canvas.width - pad, canvas.height - pad);
      ctx.stroke();
      ctx.fillStyle = '#8b95a7';
      ctx.fillText(max.toLocaleString('zh-CN', {{ maximumFractionDigits: 0 }}), 8, pad + 4);
      ctx.fillText(min.toLocaleString('zh-CN', {{ maximumFractionDigits: 0 }}), 8, canvas.height - pad);
      agents.forEach((agent, idx) => {{
        const series = navPanel[agent];
        if (!series.length) return;
        ctx.strokeStyle = colors[agent] || '#5a6478';
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        series.forEach((row, i) => {{
          const x = pad + (canvas.width - pad * 2) * (i / Math.max(series.length - 1, 1));
          const y = canvas.height - pad - (canvas.height - pad * 2) * ((Number(row.total_value) - min) / Math.max(max - min, 1));
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }});
        ctx.stroke();
        ctx.fillStyle = colors[agent] || '#5a6478';
        ctx.fillText(agent, canvas.width - 130, 28 + idx * 20);
      }});
    }}

    drawComparisonNav();
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Dark Bloomberg theme for the competition page. Class names preserved.
# Color/spacing tokens pull from _dashboard_assets.BASE_CSS.

_COMPETITION_CSS = """
.page-header { padding: var(--space-md) var(--space-xl) var(--space-sm); background: var(--bg-elevated); border-bottom: 1px solid var(--border-subtle); }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 600; color: var(--text-primary); letter-spacing: 0.02em; }
.page-header h1 .vs { color: var(--text-tertiary); font-weight: 400; padding: 0 6px; font-size: 18px; }
.page-header .subhead { margin-top: 4px; color: var(--text-tertiary); font-size: 12px; font-family: var(--font-mono); }

/* Competition tab bar (per-agent + compare) — separate from the global top nav */
.tabs { display: flex; gap: var(--space-xs); padding: var(--space-sm) var(--space-xl); background: var(--bg-elevated); border-bottom: 1px solid var(--border-subtle); }
.tab-link { color: var(--text-secondary); text-decoration: none; padding: 6px 14px; border-radius: var(--radius-sm); background: var(--bg-overlay); font-size: 13px; font-weight: 500; letter-spacing: 0.04em; transition: color 0.12s, background 0.12s; }
.tab-link:hover { background: var(--bg-base); color: var(--text-primary); text-decoration: none; }

/* Tab sections (CSS-only :target switching) */
main { padding: var(--space-lg) var(--space-xl) var(--space-xl); max-width: 1600px; margin: 0 auto; }
.tab-section { display: none; }
.tab-section:target { display: block; }
main > .tab-section:nth-of-type(3) { display: block; }
main:has(:target) > .tab-section:nth-of-type(3) { display: none; }
main:has(:target) > .tab-section:target { display: block; }
.tab-title { margin: 0 0 var(--space-md); font-size: 18px; font-weight: 600; color: var(--text-primary); text-transform: uppercase; letter-spacing: 0.06em; }

h2 { margin: var(--space-xl) 0 var(--space-md); font-size: 13px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em; }

/* KPI / metric cards */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-md); }
.metric-card { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-md); }
.metric-card .card-label { color: var(--text-tertiary); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: var(--space-sm); }
.metric-card .metric { font-size: 24px; font-weight: 600; font-family: var(--font-mono); color: var(--text-primary); font-variant-numeric: tabular-nums; }

/* Panels */
.panel { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-md); }
canvas { width: 100%; height: 320px; background: var(--bg-overlay); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); }

/* Comparison table */
table.comparison { width: 100%; border-collapse: collapse; font-size: 12px; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
table.comparison th, table.comparison td { padding: var(--space-sm) var(--space-md); border-bottom: 1px solid var(--border-subtle); text-align: right; color: var(--text-primary); }
table.comparison thead th { background: var(--bg-overlay); color: var(--text-tertiary); font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; }
table.comparison th.metric-label, table.comparison th:first-child { text-align: left; background: var(--bg-overlay); }
table.comparison strong { color: var(--accent); font-weight: 600; }

/* Overlap bar */
.overlap-bar { display: flex; height: 28px; border-radius: var(--radius-sm); overflow: hidden; border: 1px solid var(--border-subtle); }
.overlap-bar .seg { display: flex; align-items: center; justify-content: center; color: var(--bg-base); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.seg-a { background: var(--claude); }
.seg-b { background: var(--codex); }
.seg-shared { background: var(--text-secondary); }

/* Leaderboard strip */
.leaderboard-strip { display: flex; flex-wrap: wrap; gap: var(--space-xs); }
.month-block { display: inline-block; padding: 4px 10px; border-radius: var(--radius-sm); color: var(--bg-base); font-size: 11px; font-weight: 600; font-family: var(--font-mono); }
.win-claude { background: var(--claude); }
.win-codex { background: var(--codex); }
.win-tie { background: var(--tie); }

/* Monthly review links */
.monthly-review-links { margin: 0; padding-left: var(--space-lg); }
.monthly-review-links li { color: var(--text-secondary); margin: 4px 0; }

/* Empty / hint */
.empty { color: var(--text-tertiary); font-size: 13px; }
.hint { color: var(--text-tertiary); font-size: 11px; margin-top: var(--space-xs); font-family: var(--font-mono); }
.ok { color: var(--pos); font-family: var(--font-mono); font-size: 12px; }
.pending { color: var(--text-tertiary); font-family: var(--font-mono); font-size: 12px; }
.fail { color: var(--neg); font-family: var(--font-mono); font-size: 12px; }
.all-market-observer { margin-bottom: var(--space-xl); }
.market-overview-grid { margin-bottom: var(--space-lg); }
.market-nav-lines { display: grid; gap: var(--space-xs); font-family: var(--font-mono); font-size: 12px; }
.market-nav-lines strong { color: var(--text-secondary); font-family: var(--font-sans); margin-right: var(--space-xs); }
.market-decisions td, .market-task-matrix td { vertical-align: top; }

/* Observation pairing (side-by-side weekly notes) */
.observation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
.observation-cell { border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); padding: var(--space-md); background: var(--bg-overlay); }
.observation-cell h3 { margin: 0 0 var(--space-sm); font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
.observation-cell pre { margin: var(--space-xs) 0 0; font-size: 11px; color: var(--text-primary); white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
.observation-cell details summary { color: var(--text-tertiary); font-size: 11px; cursor: pointer; padding: 4px 0; }

/* Code spans for command names */
code { background: var(--bg-overlay); color: var(--accent); padding: 1px 5px; border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 11px; }

/* Responsive */
@media (max-width: 900px) {
  .observation-grid { grid-template-columns: 1fr; }
  main { padding: var(--space-md); }
}
"""
