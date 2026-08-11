"""Beginner-friendly simplified dashboard renderer.

This module assembles ``reports/competition/simple.html`` (and per-agent
variants under ``reports/competition/simple/<agent>.html``).

The simplified view is intentionally narrow:

- account headline (total / today / month)
- two agent score cards (cumulative + vs 沪深300 / 中证500)
- NAV double-line SVG (Claude / Codex / 000300 / 000905)
- top-10 holdings for each agent
- holding overlap summary (shared / exclusive)
- last 5 simulated trades across both agents
- monthly strategy adjustment digest (from evolution_log)

It does **NOT** show factor coverage, forward IC, factor contribution
breakdowns, runs.csv, data source health, agent notes, briefings, or
factor_runs/* content — those live in the professional view.

The renderer must tolerate missing inputs (graceful placeholders) and the
combined output must stay under 80 KB.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ._dashboard_assets import BASE_CSS, NAV_CSS, render_nav_html
from .beginner_format import cn_date, cn_relative_date, cny, pct
from .competition import AgentPaths, resolve_agent_paths
from .strategy_registry import StrategyRegistryInvalid, strategy_display_name
from .utils import safe_float


__all__ = [
    "render_beginner_competition_html",
    "render_beginner_agent_html",
    "write_beginner_views",
]


AGENT_DISPLAY = {"claude": "稳健防守", "codex": "趋势进攻"}
AGENT_LINE_COLOR = {
    # Dark Bloomberg palette — must agree with _dashboard_assets.BASE_CSS
    # token values (--claude / --codex / --bench-hs300 / --bench-zz500).
    "claude": "#f59e0b",
    "codex": "#06b6d4",
    "000300": "#6b7280",
    "000905": "#8b95a7",
}
BENCHMARK_LABEL = {"000300": "沪深300", "000905": "中证500"}
DEFAULT_AGENT_ORDER = ("claude", "codex")
SECTION_LIMIT_TOP_HOLDINGS = 10
SECTION_LIMIT_RECENT_TRADES = 5


# ---------------------------------------------------------------------------
# Public entry points


def render_beginner_competition_html(
    paths_by_agent: dict[str, AgentPaths],
    today: str | None = None,
) -> str:
    """Render the competition-wide simplified HTML page."""

    today_iso = today or date.today().isoformat()
    state = _gather_state(paths_by_agent)
    sections: list[str] = []
    sections.append(_render_account_card(state, today_iso, solo_agent=None))
    sections.append(_render_agent_score_cards(state, solo_agent=None))
    sections.append(_render_nav_chart(state, today_iso))
    sections.append(_render_market_environment(state, today_iso))
    sections.append(_render_differentiation_radar(state))
    for agent_id in state["agent_order"]:
        sections.append(_render_top_holdings(state, agent_id))
    sections.append(_render_position_overlap_summary(state))
    sections.append(_render_recent_trades(state, today_iso))
    sections.append(_render_monthly_evolution_summary(state, today_iso))
    body = "\n".join(sections)
    return _shell_html(
        title="我的纸面投资 · 简化版",
        body=body,
        today_iso=today_iso,
        nav_active="simple",
        strategy_labels=state["agent_display"],
    )


def render_beginner_agent_html(
    paths: AgentPaths,
    today: str | None = None,
) -> str:
    """Render a single-agent simplified page (Claude or Codex only)."""

    today_iso = today or date.today().isoformat()
    state = _gather_state({paths.agent_id: paths})
    sections: list[str] = []
    sections.append(_render_account_card(state, today_iso, solo_agent=paths.agent_id))
    sections.append(_render_agent_score_cards(state, solo_agent=paths.agent_id))
    sections.append(_render_nav_chart(state, today_iso))
    sections.append(_render_top_holdings(state, paths.agent_id))
    sections.append(_render_recent_trades(state, today_iso))
    sections.append(_render_monthly_evolution_summary(state, today_iso))
    body = "\n".join(sections)
    display = _agent_display(state, paths.agent_id)
    return _shell_html(
        title=f"{display} · 简化版",
        body=body,
        today_iso=today_iso,
        nav_active=f"simple-{paths.agent_id}",
        strategy_labels=state["agent_display"],
    )


def write_beginner_views(
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
    today: str | None = None,
) -> dict[str, Path]:
    """Render and write simple.html plus per-agent simple/<agent>.html files.

    Returns a mapping of view name → output path (e.g.
    ``{"simple": Path(".../simple.html"), "claude": ..., "codex": ...}``).
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    agents = agents or list(DEFAULT_AGENT_ORDER)
    paths_by_agent = {agent: resolve_agent_paths(agent, repo_root=root) for agent in agents}
    out_dir = root / "reports" / "competition"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "simple").mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    html_text = render_beginner_competition_html(paths_by_agent, today=today)
    simple_path = out_dir / "simple.html"
    simple_path.write_text(html_text, encoding="utf-8")
    written["simple"] = simple_path

    for agent_id, paths in paths_by_agent.items():
        single_html = render_beginner_agent_html(paths, today=today)
        single_path = out_dir / "simple" / f"{agent_id}.html"
        single_path.write_text(single_html, encoding="utf-8")
        written[agent_id] = single_path

    return written


# ---------------------------------------------------------------------------
# Data gathering


def _gather_state(paths_by_agent: dict[str, AgentPaths]) -> dict[str, Any]:
    """Read all inputs the renderer needs into a single in-memory dict."""

    agent_order = [agent for agent in DEFAULT_AGENT_ORDER if agent in paths_by_agent]
    # Append any non-default agents (e.g. testing).
    for agent in paths_by_agent:
        if agent not in agent_order:
            agent_order.append(agent)

    state: dict[str, Any] = {
        "agent_order": agent_order,
        "agent_display": {},
        "paths": paths_by_agent,
        "perf": {},
        "nav": {},
        "positions": {},
        "trades": {},
        "signals": {},
        "evolution": {},
        "benchmarks": {},
    }
    roots = [paths.data_dir.parents[2] for paths in paths_by_agent.values()]
    repo_root = roots[0] if roots else Path.cwd()
    for agent_id in agent_order:
        try:
            state["agent_display"][agent_id] = strategy_display_name(agent_id, repo_root)
        except StrategyRegistryInvalid:
            state["agent_display"][agent_id] = AGENT_DISPLAY.get(agent_id, agent_id)
    for agent_id in agent_order:
        paths = paths_by_agent[agent_id]
        state["perf"][agent_id] = _read_performance_summary(paths.data_dir)
        state["nav"][agent_id] = _read_nav_dataframe(paths.data_dir)
        state["positions"][agent_id] = _read_positions(paths.data_dir)
        state["trades"][agent_id] = _read_trades(paths.data_dir)
        state["signals"][agent_id] = _read_signals(paths.data_dir)
        state["evolution"][agent_id] = _read_latest_evolution_log(paths.data_dir)

    state["benchmarks"] = _derive_benchmark_series(state["nav"])
    return state


def _agent_display(state: dict[str, Any], agent_id: str) -> str:
    return str(
        state.get("agent_display", {}).get(agent_id)
        or AGENT_DISPLAY.get(agent_id, agent_id)
    )


def _read_performance_summary(data_dir: Path) -> dict[str, Any]:
    import json

    path = data_dir / "performance_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_nav_dataframe(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "daily_nav.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        # Keep benchmark_code as str so '000300' isn't coerced to int 300
        df = pd.read_csv(path, dtype={"benchmark_code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date.astype(str)
    df = df.dropna(subset=["date"])
    return df


def _read_positions(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "positions.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return df


def _read_trades(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "trades.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return df


def _read_signals(data_dir: Path) -> pd.DataFrame:
    """Read the agent's latest weekly stock-pick signals.

    The radar chart on the simplified view averages factor values across
    each agent's pick set to surface style differences (Claude tilts
    value/momentum vs Codex tilts quality/dividend, etc.).
    """

    path = data_dir / "latest_signals.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return df


def _read_latest_evolution_log(data_dir: Path) -> dict[str, str] | None:
    """Return the latest monthly evolution log summary, or ``None``."""

    log_dir = data_dir / "evolution_log"
    if not log_dir.exists():
        return None
    candidates = sorted(log_dir.glob("*.md"))
    if not candidates:
        return None
    path = candidates[-1]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # Pull the first prose paragraph after the title (skip blank lines).
    lines = [line.rstrip() for line in text.splitlines()]
    summary_lines: list[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        if not line and not summary_lines:
            continue
        if not line:
            break
        summary_lines.append(line)
        if len(summary_lines) >= 3:
            break
    return {
        "month": path.stem,
        "summary": " ".join(summary_lines).strip(),
        "path": path.name,
    }


def _derive_benchmark_series(nav_by_agent: dict[str, pd.DataFrame]) -> dict[str, list[dict[str, Any]]]:
    """Pull benchmark close series from any agent's daily_nav.csv.

    The fairness baseline guarantees both agents use the same benchmarks, so
    reading either one is enough. Series are indexed by date.
    """

    result: dict[str, list[dict[str, Any]]] = {}
    for df in nav_by_agent.values():
        if df.empty or "benchmark_code" not in df.columns:
            continue
        for code, group in df.groupby("benchmark_code"):
            # benchmark_code may be read by pandas as int (300) instead of
            # string ("000300"); zfill ensures the dict key matches the
            # BENCHMARK_LABEL convention so downstream lookups don't miss.
            key = str(code).zfill(6) if str(code).isdigit() else str(code)
            if key in result:
                continue
            sub = group[["date", "benchmark_close"]].dropna().drop_duplicates("date")
            sub = sub.sort_values("date")
            result[key] = [
                {"date": row["date"], "close": safe_float(row["benchmark_close"])}
                for _, row in sub.iterrows()
                if safe_float(row["benchmark_close"]) is not None
            ]
    return result


# ---------------------------------------------------------------------------
# Section renderers


def _render_account_card(
    state: dict[str, Any],
    today_iso: str,
    solo_agent: str | None = None,
) -> str:
    """Section 1: aggregated 总资产 / 今日 / 本月.

    When ``solo_agent`` is given, the card narrates a single agent's
    account (not "two AI combined"). The KPI math is unchanged because
    ``_compute_aggregate_account`` already collapses across whatever
    agents were loaded into state.
    """

    totals = _compute_aggregate_account(state)
    items = []
    items.append(
        f'<div class="kpi"><div class="kpi-label">总资产</div>'
        f'<div class="kpi-value big">{html.escape(cny(totals["total"]))}</div></div>'
    )
    items.append(
        f'<div class="kpi"><div class="kpi-label">今日变动</div>'
        f'<div class="kpi-value big">{pct(totals["today_ratio"], color=True)}</div>'
        f'<div class="kpi-sub">{html.escape(cny(totals["today_delta"]))}</div></div>'
    )
    items.append(
        f'<div class="kpi"><div class="kpi-label">本月变动</div>'
        f'<div class="kpi-value big">{pct(totals["month_ratio"], color=True)}</div>'
        f'<div class="kpi-sub">{html.escape(cny(totals["month_delta"]))}</div></div>'
    )
    if solo_agent:
        display = _agent_display(state, solo_agent)
        scope_hint = f"{display} 当前资产"
    else:
        scope_hint = "两个 AI 合计资产"
    return (
        '<section class="card account-card" data-id="1">'
        '<h2>👤 我的账户</h2>'
        f'<div class="kpi-row">{"".join(items)}</div>'
        f'<p class="hint">截至 {html.escape(cn_date(today_iso))}，{html.escape(scope_hint)}</p>'
        '</section>'
    )


def _render_agent_score_cards(
    state: dict[str, Any],
    solo_agent: str | None = None,
) -> str:
    """Section 2: agent score cards.

    When ``solo_agent`` is given, only one card is shown and the section
    title narrates that one agent (not "两位 AI").
    """

    cards: list[str] = []
    for agent_id in state["agent_order"]:
        cards.append(_render_single_agent_score(state, agent_id))
    if solo_agent:
        display = _agent_display(state, solo_agent)
        title = f"📊 {display} 的成绩"
    else:
        title = "📊 两位 AI 的成绩"
    return (
        '<section class="card" data-id="2">'
        f'<h2>{title}</h2>'
        f'<div class="agent-grid">{"".join(cards)}</div>'
        '</section>'
    )


def _render_single_agent_score(state: dict[str, Any], agent_id: str) -> str:
    perf = state["perf"].get(agent_id, {})
    accounts = (perf.get("accounts") or {}) if perf else {}
    if not accounts:
        return (
            f'<div class="agent-score {agent_id}">'
            f'<div class="agent-name">{html.escape(_agent_display(state, agent_id))}</div>'
            '<div class="empty">尚未开盘交易</div>'
            '</div>'
        )
    cumulative = _mean_metric(accounts, "cumulative_return")
    excess = _mean_metric(accounts, "cumulative_excess_return")
    info_ratio = _mean_metric(accounts, "information_ratio")
    benchmark_label = _format_benchmark_summary(accounts)

    lines: list[str] = []
    lines.append(
        f'<div class="agent-name">{html.escape(_agent_display(state, agent_id))}</div>'
    )
    lines.append(
        f'<div class="agent-cumulative big">{pct(cumulative, color=True)}</div>'
        '<div class="agent-cumulative-label">累计收益</div>'
    )
    if excess is not None and benchmark_label:
        verb = "跑赢" if excess >= 0 else "跑输"
        lines.append(
            f'<div class="agent-vs-benchmark">{verb} {html.escape(benchmark_label)} '
            f'{pct(abs(excess), signed=False, color=True)}</div>'
        )
    if info_ratio is not None:
        lines.append(
            f'<div class="agent-ir">信息比率 {info_ratio:.2f}</div>'
        )
    return f'<div class="agent-score {agent_id}">{"".join(lines)}</div>'


def _render_nav_chart(state: dict[str, Any], today_iso: str) -> str:
    """Section 3: SVG line chart of Claude vs Codex vs benchmarks."""

    series: list[tuple[str, str, list[tuple[str, float]]]] = []
    for agent_id in state["agent_order"]:
        points = _normalize_agent_nav(state["nav"].get(agent_id, pd.DataFrame()))
        if points:
            series.append((agent_id, _agent_display(state, agent_id), points))

    benchmarks = state.get("benchmarks") or {}
    for code, label in BENCHMARK_LABEL.items():
        bench = benchmarks.get(code) or []
        if bench:
            base = bench[0].get("close")
            if not base:
                continue
            points = [
                (row["date"], row["close"] / base)
                for row in bench
                if row.get("close")
            ]
            if points:
                series.append((code, label, points))

    if not series:
        return (
            '<section class="card" data-id="3">'
            '<h2>📈 净值曲线</h2>'
            '<p class="empty">尚未有净值数据。等首个交易日 NAV 落库后会出现。</p>'
            '</section>'
        )

    svg = _render_nav_svg(series)
    legend = "".join(
        f'<span class="legend-item"><span class="dot" style="background:{AGENT_LINE_COLOR.get(key, "#444")}"></span>'
        f'{html.escape(label)}</span>'
        for key, label, _ in series
    )
    return (
        '<section class="card" data-id="3">'
        '<h2>📈 净值曲线(初始 1.00 为基准)</h2>'
        f'{svg}'
        f'<div class="legend">{legend}</div>'
        f'<p class="hint">最后更新 {html.escape(cn_relative_date(today_iso, today_iso))}</p>'
        '</section>'
    )


def _render_top_holdings(state: dict[str, Any], agent_id: str) -> str:
    """Section 4 / 5: top-10 holdings for one agent."""

    positions = state["positions"].get(agent_id, pd.DataFrame())
    display_name = _agent_display(state, agent_id)
    data_id = 4 if agent_id == state["agent_order"][0] else 5
    if positions.empty:
        return (
            f'<section class="card" data-id="{data_id}">'
            f'<h2>📦 {html.escape(display_name)} 持仓</h2>'
            '<p class="empty">尚未开盘交易</p>'
            '</section>'
        )

    df = positions.copy()
    if "market_value" in df.columns:
        df = df.sort_values("market_value", ascending=False)
    df = df.head(SECTION_LIMIT_TOP_HOLDINGS)
    rows: list[str] = []
    for _, row in df.iterrows():
        rows.append(_render_position_row(row))
    industry_total = positions["market_value"].sum() if "market_value" in positions.columns else 0
    return (
        f'<section class="card" data-id="{data_id}">'
        f'<h2>📦 {html.escape(display_name)} 持仓 Top {len(rows)}</h2>'
        '<table class="holdings">'
        '<thead><tr><th>股票</th><th>行业</th><th>买入价</th><th>现价</th><th>市值</th><th>盈亏</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        f'<p class="hint">合计持仓市值 {html.escape(cny(industry_total))}</p>'
        '</section>'
    )


def _render_position_row(row: pd.Series) -> str:
    code = str(row.get("code") or "").zfill(6)
    name = str(row.get("name") or "-")
    industry = _shorten_industry(str(row.get("industry") or "-"))
    avg_cost = safe_float(row.get("avg_cost"))
    last_price = safe_float(row.get("last_price"))
    market_value = safe_float(row.get("market_value"))
    pnl_ratio: float | None = None
    if avg_cost and last_price:
        pnl_ratio = (last_price / avg_cost) - 1
    return (
        '<tr>'
        f'<td><span class="stock-name">{html.escape(name)}</span>'
        f'<span class="stock-code">{html.escape(code)}</span></td>'
        f'<td>{html.escape(industry)}</td>'
        f'<td>{_format_price(avg_cost)}</td>'
        f'<td>{_format_price(last_price)}</td>'
        f'<td>{html.escape(cny(market_value))}</td>'
        f'<td>{pct(pnl_ratio, color=True)}</td>'
        '</tr>'
    )


def _render_position_overlap_summary(state: dict[str, Any]) -> str:
    """Section 7: holdings overlap summary across the two agents."""

    if len(state["agent_order"]) < 2:
        return ""  # No overlap to compute for a single agent.
    a, b = state["agent_order"][:2]
    set_a = _position_codes(state["positions"].get(a, pd.DataFrame()))
    set_b = _position_codes(state["positions"].get(b, pd.DataFrame()))
    shared = sorted(set_a & set_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)

    parts: list[str] = []
    parts.append(_overlap_row("两位都持有", shared))
    parts.append(_overlap_row(f"仅 {_agent_display(state, a)} 持有", only_a))
    parts.append(_overlap_row(f"仅 {_agent_display(state, b)} 持有", only_b))
    return (
        '<section class="card" data-id="6">'
        '<h2>🔍 持仓重叠</h2>'
        f'<div class="overlap-grid">{"".join(parts)}</div>'
        '</section>'
    )


def _overlap_row(label: str, codes: list[str]) -> str:
    if not codes:
        return (
            '<div class="overlap-cell">'
            f'<div class="overlap-label">{html.escape(label)}</div>'
            '<div class="overlap-value empty">无</div>'
            '</div>'
        )
    preview = "、".join(codes[:6])
    suffix = f"…(共 {len(codes)} 只)" if len(codes) > 6 else f"(共 {len(codes)} 只)"
    return (
        '<div class="overlap-cell">'
        f'<div class="overlap-label">{html.escape(label)}</div>'
        f'<div class="overlap-value">{html.escape(preview)} {html.escape(suffix)}</div>'
        '</div>'
    )


def _render_recent_trades(state: dict[str, Any], today_iso: str) -> str:
    """Section 8: latest N trades across both agents."""

    combined: list[dict[str, Any]] = []
    for agent_id in state["agent_order"]:
        df = state["trades"].get(agent_id, pd.DataFrame())
        if df.empty:
            continue
        local = df.copy()
        local["agent_id"] = agent_id
        combined.append(local)
    if not combined:
        return (
            '<section class="card" data-id="7">'
            '<h2>🔄 最近模拟成交</h2>'
            '<p class="empty">尚无成交记录</p>'
            '</section>'
        )
    merged = pd.concat(combined, ignore_index=True)
    if "trade_date" in merged.columns:
        merged = merged.sort_values("trade_date", ascending=False)
    merged = merged.head(SECTION_LIMIT_RECENT_TRADES)
    rows: list[str] = []
    for _, row in merged.iterrows():
        rows.append(_render_trade_row(row, today_iso, state["agent_display"]))
    return (
        '<section class="card" data-id="7">'
        '<h2>🔄 最近 5 笔模拟成交</h2>'
        '<table class="trades">'
        '<thead><tr><th>日期</th><th>AI</th><th>股票</th><th>方向</th><th>股数</th><th>价格</th><th>成交额</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</section>'
    )


def _render_trade_row(
    row: pd.Series,
    today_iso: str,
    strategy_labels: dict[str, str],
) -> str:
    trade_date = str(row.get("trade_date") or "")
    agent = str(row.get("agent_id") or "")
    code = str(row.get("code") or "").zfill(6)
    name = str(row.get("name") or "-")
    side_raw = str(row.get("side") or "").lower()
    side_display = "买入" if side_raw == "buy" else ("卖出" if side_raw == "sell" else side_raw)
    side_class = "buy" if side_raw == "buy" else "sell"
    shares = safe_float(row.get("shares")) or 0
    price = safe_float(row.get("price"))
    gross = safe_float(row.get("gross_amount"))
    return (
        '<tr>'
        f'<td>{html.escape(cn_relative_date(trade_date, today_iso))}</td>'
        f'<td>{html.escape(strategy_labels.get(agent, AGENT_DISPLAY.get(agent, agent)))}</td>'
        f'<td><span class="stock-name">{html.escape(name)}</span>'
        f'<span class="stock-code">{html.escape(code)}</span></td>'
        f'<td><span class="trade-side {side_class}">{html.escape(side_display)}</span></td>'
        f'<td>{int(shares):,}</td>'
        f'<td>{_format_price(price)}</td>'
        f'<td>{html.escape(cny(gross))}</td>'
        '</tr>'
    )


def _render_monthly_evolution_summary(state: dict[str, Any], today_iso: str) -> str:
    """Section 9 (optional): current-month evolution log digests."""

    blocks: list[str] = []
    for agent_id in state["agent_order"]:
        record = state["evolution"].get(agent_id)
        if not record:
            continue
        summary = record.get("summary") or ""
        if not summary:
            continue
        blocks.append(
            '<div class="evolution-cell">'
            f'<h3>{html.escape(_agent_display(state, agent_id))} · {html.escape(record.get("month", ""))}</h3>'
            f'<p>{html.escape(summary[:280])}</p>'
            '</div>'
        )
    if not blocks:
        return ""
    return (
        '<section class="card" data-id="8">'
        '<h2>🧭 本月策略调整摘要</h2>'
        f'<div class="evolution-grid">{"".join(blocks)}</div>'
        '<p class="hint">点击 <a href="/pro.html">专业版</a> → 策略演进 tab 查看完整 evolution_log。</p>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Market environment + differentiation radar (added 2026-05-24 IA refactor)


# Six factors used by both agents' overlays. Each tuple is
# (factor_column, display_label, lower_is_better).
_RADAR_FACTORS: tuple[tuple[str, str, bool], ...] = (
    ("pe", "PE 低估", True),
    ("pb", "PB 低估", True),
    ("roe", "ROE", False),
    ("momentum_60", "60 日动量", False),
    ("low_volatility_60", "低波 60", True),
    ("dividend_yield", "股息率", False),
)


def _render_market_environment(state: dict[str, Any], today_iso: str) -> str:
    """Section 3.5: 沪深300 / 中证500 weekly close trend strip.

    Helps the user place the agents' returns in market context — if both
    indices are down, "Codex 跑赢沪深300 0.5%" still means a loss in
    absolute terms.
    """

    benchmarks = state.get("benchmarks") or {}
    cards: list[str] = []
    for code, label in BENCHMARK_LABEL.items():
        series = benchmarks.get(code) or []
        if not series:
            continue
        # Last ~12 weekly closes (we approximate by taking every 5th daily
        # observation, oldest first). This is a sparkline of market trend,
        # NOT a true K-line — daily OHLC is not surfaced by the data layer
        # to the dashboard yet. See data/claude/notes/2026-05-24-... for
        # the K-line upgrade plan.
        weekly = series[::5][-12:]
        if len(weekly) < 2:
            continue
        first = weekly[0].get("close")
        last = weekly[-1].get("close")
        if not first or not last:
            continue
        change = last / first - 1
        change_html = f'<span class="{"pos" if change >= 0 else "neg"}">{pct(change, color=False)}</span>'
        svg = _render_mini_line_svg(
            [(row["date"], row["close"]) for row in weekly],
            stroke=AGENT_LINE_COLOR.get(code, "#8b95a7"),
        )
        cards.append(
            '<div class="market-card">'
            f'<div class="market-label">{html.escape(label)} <span class="stock-code">{html.escape(code)}</span></div>'
            f'<div class="market-change">近 12 周 {change_html}</div>'
            f'{svg}'
            '</div>'
        )
    if not cards:
        return ""
    # No data-id attribute on this new section — the legacy 1..N data-id
    # sequence belongs to the original 8 sections; new IA additions stay
    # outside that numbering to keep the existing ordering test stable.
    return (
        '<section class="card">'
        '<h2>🌐 市场环境（沪深300 / 中证500 近 12 周）</h2>'
        f'<div class="market-grid">{"".join(cards)}</div>'
        '<p class="hint">把两位 AI 的累计收益放到市场背景里看：基准下跌时，跑赢指数不代表绝对收益为正。</p>'
        '</section>'
    )


def _render_mini_line_svg(points: list[tuple[str, float]], stroke: str) -> str:
    """Tiny sparkline-style SVG for the market-environment cards.

    Width 100% (viewBox), height 60px. No axes, no labels — just the line
    and a faint baseline at the first value.
    """

    if not points:
        return ''
    width = 300
    height = 60
    pad_x = 4
    pad_y = 6
    values = [v for _, v in points]
    v_min = min(values)
    v_max = max(values)
    if v_max == v_min:
        v_max = v_min + 0.001
    inner_w = width - 2 * pad_x
    inner_h = height - 2 * pad_y

    def x_of(i: int) -> float:
        return pad_x + (i / max(len(points) - 1, 1)) * inner_w

    def y_of(v: float) -> float:
        return pad_y + (1 - (v - v_min) / (v_max - v_min)) * inner_h

    line_points = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, (_, v) in enumerate(points))
    first_y = y_of(points[0][1])
    return (
        f'<svg class="mini-line" viewBox="0 0 {width} {height}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
        f'<line x1="{pad_x}" y1="{first_y:.1f}" x2="{width - pad_x}" y2="{first_y:.1f}" stroke="#2a3145" stroke-dasharray="2 4" stroke-opacity="0.6" />'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" points="{line_points}" />'
        '</svg>'
    )


def _render_differentiation_radar(state: dict[str, Any]) -> str:
    """Section 4 (NEW 2026-05-24): six-axis radar comparing factor tilt.

    For each agent we average the 6 factor columns across the latest
    weekly signals and project the two polygons over the same axis set.
    Normalization is max-of-both per axis so the polygon visually
    answers "which agent leans more {value, quality, momentum, low-vol,
    dividend}". For inverted-direction factors (PE, PB, low-vol) we
    flip the axis so that a polygon pushing outward means "more X".
    """

    if len(state["agent_order"]) < 2:
        return ""  # Single-agent view does not show the comparison radar.

    # Compute factor means per agent.
    means_by_agent: dict[str, dict[str, float | None]] = {}
    for agent_id in state["agent_order"]:
        signals = state["signals"].get(agent_id, pd.DataFrame())
        means_by_agent[agent_id] = {}
        if signals.empty:
            for col, _, _ in _RADAR_FACTORS:
                means_by_agent[agent_id][col] = None
            continue
        for col, _, _ in _RADAR_FACTORS:
            if col not in signals.columns:
                means_by_agent[agent_id][col] = None
                continue
            series = pd.to_numeric(signals[col], errors="coerce").dropna()
            means_by_agent[agent_id][col] = float(series.mean()) if not series.empty else None

    # If neither agent has any factor data, skip the card.
    has_any = any(any(v is not None for v in d.values()) for d in means_by_agent.values())
    if not has_any:
        return ""

    axes = [(col, label, lower_better) for col, label, lower_better in _RADAR_FACTORS]
    n_axes = len(axes)
    width = 460
    height = 360
    cx = width / 2
    cy = height / 2 + 6
    radius = 130
    import math

    # Per-axis normalization: for lower-is-better factors invert so that
    # a larger plotted value means "more of this style".
    normalized: dict[str, list[float | None]] = {agent: [] for agent in state["agent_order"]}
    for col, _, lower_better in axes:
        raw = {agent: means_by_agent[agent].get(col) for agent in state["agent_order"]}
        valid = [v for v in raw.values() if v is not None]
        if not valid:
            for agent in state["agent_order"]:
                normalized[agent].append(None)
            continue
        if lower_better:
            # Invert: smaller value → larger normalized score.
            v_max = max(valid)
            transformed = {agent: (v_max - v) if v is not None else None for agent, v in raw.items()}
        else:
            transformed = dict(raw)
        valid_t = [v for v in transformed.values() if v is not None]
        peak = max(valid_t) if valid_t else 1.0
        if peak <= 0:
            peak = 1.0
        for agent in state["agent_order"]:
            v = transformed.get(agent)
            normalized[agent].append((v / peak) if v is not None else None)

    # SVG: axis lines + grid rings + labels + polygons.
    svg_parts: list[str] = []
    # Grid rings
    for r_ratio in (0.25, 0.5, 0.75, 1.0):
        r = radius * r_ratio
        svg_parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="#2a3145" stroke-opacity="0.6" stroke-width="1" />'
        )
    # Axis spokes + labels
    angles: list[float] = []
    for i, (_, label, _) in enumerate(axes):
        angle = -math.pi / 2 + i * (2 * math.pi / n_axes)
        angles.append(angle)
        x_end = cx + radius * math.cos(angle)
        y_end = cy + radius * math.sin(angle)
        svg_parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{x_end:.1f}" y2="{y_end:.1f}" stroke="#2a3145" stroke-opacity="0.6" stroke-width="1" />'
        )
        label_x = cx + (radius + 18) * math.cos(angle)
        label_y = cy + (radius + 18) * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.3:
            anchor = "start"
        elif math.cos(angle) < -0.3:
            anchor = "end"
        svg_parts.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}" dominant-baseline="middle" '
            f'font-size="11" fill="#8b95a7" font-family="-apple-system, PingFang SC, sans-serif">{html.escape(label)}</text>'
        )

    # Polygons per agent
    legend_items: list[str] = []
    for agent in state["agent_order"]:
        color = AGENT_LINE_COLOR.get(agent, "#8b95a7")
        coords: list[str] = []
        for i, value in enumerate(normalized[agent]):
            v = value if value is not None else 0.0
            r = radius * max(0.0, min(1.0, v))
            x = cx + r * math.cos(angles[i])
            y = cy + r * math.sin(angles[i])
            coords.append(f"{x:.1f},{y:.1f}")
        polygon_points = " ".join(coords)
        svg_parts.append(
            f'<polygon points="{polygon_points}" fill="{color}" fill-opacity="0.15" '
            f'stroke="{color}" stroke-width="2" />'
        )
        # Dots at each axis point
        for i, value in enumerate(normalized[agent]):
            v = value if value is not None else 0.0
            r = radius * max(0.0, min(1.0, v))
            x = cx + r * math.cos(angles[i])
            y = cy + r * math.sin(angles[i])
            svg_parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}" />'
            )
        legend_items.append(
            f'<span class="legend-item"><span class="dot" style="background:{color}"></span>'
            f'{html.escape(_agent_display(state, agent))}</span>'
        )

    svg_html = (
        f'<svg class="radar-chart" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" '
        'xmlns="http://www.w3.org/2000/svg">'
        + "".join(svg_parts)
        + "</svg>"
    )

    # No data-id (see note on _render_market_environment).
    return (
        '<section class="card">'
        '<h2>🎯 差异化雷达（持仓因子均值，每轴归一）</h2>'
        f'{svg_html}'
        f'<div class="legend">{"".join(legend_items)}</div>'
        '<p class="hint">每轴方向：向外 = 更偏向该风格。PE / PB / 低波三轴已反向，'
        '所以向外 = 更便宜 / 更低波。轴长按两位 agent 均值的较大者归一。</p>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Helpers


def _shell_html(
    title: str,
    body: str,
    today_iso: str,
    nav_active: str | None = None,
    strategy_labels: dict[str, str] | None = None,
) -> str:
    """Wrap section body HTML in the full <html> document.

    Includes the global top nav (from ``_dashboard_assets``) and dark
    Bloomberg-styled CSS scoped to the simplified view's class names.
    """

    generated = datetime.now()
    safe_title = html.escape(title)
    today_display = html.escape(cn_date(today_iso))
    nav = render_nav_html(
        active=nav_active,
        generated_at=generated,
        data_as_of=today_iso,
        strategy_labels=strategy_labels,
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>{BASE_CSS}
{NAV_CSS}
{_CSS}</style>
</head>
<body>
  {nav}
  <header class="page-header">
    <h1>{safe_title}</h1>
    <p class="page-sub">数据截至 {today_display}</p>
  </header>
  <main>
{body}
  </main>
  <footer class="footer">
    <p class="hint">仅模拟交易 · 不构成任何投资建议</p>
  </footer>
</body>
</html>
"""


# Simplified-view CSS: dark Bloomberg theme that consumes the BASE_CSS
# tokens defined in ``_dashboard_assets``. Class names are preserved so
# the section renderers do not need to be rewritten.
_CSS = """
main { max-width: 1200px; margin: 0 auto; padding: var(--space-lg) var(--space-xl); }
.page-header { padding: var(--space-lg) var(--space-xl); background: var(--bg-elevated); border-bottom: 1px solid var(--border-subtle); }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 600; color: var(--text-primary); letter-spacing: 0.02em; }
.page-sub { margin: 6px 0 0; font-size: 12px; color: var(--text-tertiary); font-family: var(--font-mono); }

/* Cards — flat panels, no rounded "fluffy" feel */
.card { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-md) var(--space-lg); margin: var(--space-md) 0; }
.card h2 { margin: 0 0 var(--space-md); font-size: 14px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }

/* KPI row */
.kpi-row { display: flex; gap: var(--space-xl); flex-wrap: wrap; }
.kpi { flex: 1 1 200px; min-width: 0; }
.kpi-label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }
.kpi-value { font-size: 22px; font-weight: 600; font-family: var(--font-mono); color: var(--text-primary); font-variant-numeric: tabular-nums; }
.kpi-value.big { font-size: 30px; line-height: 1.1; }
.kpi-sub { font-size: 12px; color: var(--text-tertiary); font-family: var(--font-mono); margin-top: 4px; }

/* P&L color tokens override (from BASE_CSS) */
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.zero { color: var(--text-tertiary); }

/* Agent score panels — paint Claude amber, Codex cyan via left accent stripe */
.agent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-md); }
.agent-score { padding: var(--space-md); background: var(--bg-overlay); border-radius: var(--radius-sm); border-left: 3px solid var(--text-tertiary); }
.agent-score.claude { border-left-color: var(--claude); }
.agent-score.codex { border-left-color: var(--codex); }
.agent-name { font-weight: 600; font-size: 13px; color: var(--text-primary); text-transform: uppercase; letter-spacing: 0.06em; }
.agent-score.claude .agent-name { color: var(--claude); }
.agent-score.codex .agent-name { color: var(--codex); }
.agent-cumulative { font-size: 28px; font-weight: 700; font-family: var(--font-mono); margin: 4px 0; font-variant-numeric: tabular-nums; }
.agent-cumulative-label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: var(--space-sm); }
.agent-vs-benchmark { font-size: 13px; color: var(--text-secondary); margin-bottom: 4px; }
.agent-ir { font-size: 12px; color: var(--text-tertiary); font-family: var(--font-mono); }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
table.holdings th, table.holdings td,
table.trades th, table.trades td { padding: var(--space-sm) var(--space-md); text-align: right; border-bottom: 1px solid var(--border-subtle); font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
table.holdings th:first-child, table.holdings td:first-child,
table.trades th:first-child, table.trades td:first-child,
table.trades td:nth-child(2), table.trades td:nth-child(3),
table.trades td:nth-child(4), table.trades th:nth-child(2),
table.trades th:nth-child(3), table.trades th:nth-child(4) { text-align: left; font-family: var(--font-sans); }
table.holdings td:nth-child(2), table.holdings th:nth-child(2) { text-align: left; font-family: var(--font-sans); }
table.holdings th, table.trades th { background: var(--bg-overlay); color: var(--text-tertiary); font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; }
.stock-name { font-weight: 500; margin-right: 6px; color: var(--text-primary); }
.stock-code { color: var(--text-tertiary); font-size: 11px; font-family: var(--font-mono); }
.trade-side.buy { color: var(--pos); font-weight: 600; }
.trade-side.sell { color: var(--neg); font-weight: 600; }

/* Position overlap */
.overlap-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-sm); }
.overlap-cell { background: var(--bg-overlay); border-radius: var(--radius-sm); padding: var(--space-sm) var(--space-md); }
.overlap-label { font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
.overlap-value { font-size: 13px; color: var(--text-primary); font-family: var(--font-mono); }
.overlap-value.empty { color: var(--text-tertiary); }

/* Chart legend */
.legend { display: flex; gap: var(--space-md); margin-top: var(--space-sm); font-size: 12px; color: var(--text-secondary); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

/* Empty / hint */
.empty { color: var(--text-tertiary); font-size: 13px; }
.hint { color: var(--text-tertiary); font-size: 12px; margin: var(--space-xs) 0 0; font-family: var(--font-mono); }

/* Evolution summary */
.evolution-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--space-sm); }
.evolution-cell { background: var(--bg-overlay); border-radius: var(--radius-sm); padding: var(--space-sm) var(--space-md); border-left: 3px solid var(--accent-dim); }
.evolution-cell h3 { margin: 0 0 6px; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
.evolution-cell p { margin: 0; font-size: 13px; color: var(--text-primary); line-height: 1.5; }

/* Footer */
.footer { text-align: center; color: var(--text-tertiary); font-size: 11px; padding: var(--space-lg) 0 var(--space-xl); border-top: 1px solid var(--border-subtle); margin-top: var(--space-xl); }
.footer a { color: var(--accent); margin: 0 6px; }

/* NAV chart — dark canvas, legible axes */
svg.nav-chart { width: 100%; height: 280px; background: var(--bg-overlay); border-radius: var(--radius-sm); border: 1px solid var(--border-subtle); }

/* Market environment cards (沪深300 / 中证500 mini line) */
.market-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: var(--space-md); }
.market-card { background: var(--bg-overlay); border-radius: var(--radius-sm); padding: var(--space-sm) var(--space-md); border-left: 3px solid var(--text-tertiary); }
.market-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.04em; }
.market-change { font-size: 18px; font-weight: 600; font-family: var(--font-mono); color: var(--text-primary); margin-bottom: var(--space-sm); }
svg.mini-line { width: 100%; height: 60px; display: block; }

/* Differentiation radar */
svg.radar-chart { width: 100%; max-width: 460px; height: 360px; display: block; margin: 0 auto; }
"""


def _compute_aggregate_account(state: dict[str, Any]) -> dict[str, Any]:
    """Compute aggregate total value and today / month deltas."""

    nav_frames = [df for df in state["nav"].values() if not df.empty]
    if not nav_frames:
        return {
            "total": None,
            "today_delta": None,
            "today_ratio": None,
            "month_delta": None,
            "month_ratio": None,
        }
    merged = pd.concat(nav_frames, ignore_index=True)
    merged = merged.copy()
    merged["total_value"] = merged["total_value"].apply(safe_float)
    merged = merged.dropna(subset=["total_value"])
    if merged.empty:
        return {
            "total": None,
            "today_delta": None,
            "today_ratio": None,
            "month_delta": None,
            "month_ratio": None,
        }
    grouped = merged.groupby("date")["total_value"].sum().sort_index()
    latest_date = grouped.index[-1]
    latest_value = float(grouped.iloc[-1])
    prev_value = float(grouped.iloc[-2]) if len(grouped) >= 2 else latest_value
    today_delta = latest_value - prev_value
    today_ratio = (latest_value / prev_value - 1) if prev_value else None

    latest_month_prefix = latest_date[:7]
    month_grouped = grouped[grouped.index.str.startswith(latest_month_prefix)]
    month_start_value = float(month_grouped.iloc[0]) if not month_grouped.empty else latest_value
    month_delta = latest_value - month_start_value
    month_ratio = (latest_value / month_start_value - 1) if month_start_value else None

    return {
        "total": latest_value,
        "today_delta": today_delta,
        "today_ratio": today_ratio,
        "month_delta": month_delta,
        "month_ratio": month_ratio,
    }


def _mean_metric(accounts: dict[str, Any], key: str) -> float | None:
    values = [safe_float(account.get(key)) for account in accounts.values()]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _format_benchmark_summary(accounts: dict[str, Any]) -> str:
    labels: list[str] = []
    for account_id in accounts:
        if account_id == "hs300":
            labels.append("沪深300")
        elif account_id == "zz500":
            labels.append("中证500")
    if not labels:
        return "基准"
    if len(labels) == 1:
        return labels[0]
    return "/".join(labels)


def _normalize_agent_nav(df: pd.DataFrame) -> list[tuple[str, float]]:
    """Aggregate per-date total_value across an agent's accounts, then index to 1."""

    if df.empty:
        return []
    local = df.copy()
    local["total_value"] = local["total_value"].apply(safe_float)
    local = local.dropna(subset=["total_value"])
    if local.empty:
        return []
    grouped = local.groupby("date")["total_value"].sum().sort_index()
    base = float(grouped.iloc[0])
    if not base:
        return []
    return [(str(d), float(v) / base) for d, v in grouped.items()]


def _render_nav_svg(series: list[tuple[str, str, list[tuple[str, float]]]]) -> str:
    """Render the four-line NAV chart as inline SVG, with a date x-axis."""

    width = 900
    height = 260
    pad_left = 50
    pad_right = 20
    pad_top = 20
    pad_bottom = 36

    all_values = [value for _, _, points in series for _, value in points]
    if not all_values:
        return '<p class="empty">尚无净值数据</p>'

    all_dates = sorted({d for _, _, points in series for d, _ in points})
    if not all_dates:
        return '<p class="empty">尚无净值数据</p>'
    date_index = {d: i for i, d in enumerate(all_dates)}

    y_min = min(all_values)
    y_max = max(all_values)
    if y_max == y_min:
        y_max = y_min + 0.001  # avoid div-by-zero

    inner_w = width - pad_left - pad_right
    inner_h = height - pad_top - pad_bottom

    def x_of(d: str) -> float:
        idx = date_index[d]
        denom = max(len(all_dates) - 1, 1)
        return pad_left + (idx / denom) * inner_w

    def y_of(v: float) -> float:
        return pad_top + (1 - (v - y_min) / (y_max - y_min)) * inner_h

    lines: list[str] = []
    # Dark-theme axis frame (matches --bg-overlay / --border-subtle).
    lines.append(
        f'<rect x="{pad_left}" y="{pad_top}" width="{inner_w}" height="{inner_h}" '
        'fill="#1a1f2e" stroke="#2a3145" />'
    )
    # baseline at 1.0 — dashed amber line
    if y_min <= 1.0 <= y_max:
        y1 = y_of(1.0)
        lines.append(
            f'<line x1="{pad_left}" y1="{y1:.1f}" x2="{pad_left + inner_w}" y2="{y1:.1f}" '
            'stroke="#b87333" stroke-dasharray="4 4" stroke-opacity="0.55" />'
        )
        lines.append(
            f'<text x="{pad_left - 8}" y="{y1 + 4:.1f}" text-anchor="end" font-size="10" fill="#8b95a7" font-family="JetBrains Mono, monospace">1.00</text>'
        )
    # y labels min/max — secondary text color, mono font
    lines.append(
        f'<text x="{pad_left - 8}" y="{pad_top + 4}" text-anchor="end" font-size="10" fill="#8b95a7" font-family="JetBrains Mono, monospace">{y_max:.3f}</text>'
    )
    lines.append(
        f'<text x="{pad_left - 8}" y="{pad_top + inner_h:.1f}" text-anchor="end" font-size="10" fill="#8b95a7" font-family="JetBrains Mono, monospace">{y_min:.3f}</text>'
    )
    # x labels: first and last
    lines.append(
        f'<text x="{pad_left}" y="{height - 12}" font-size="10" fill="#5a6478" font-family="JetBrains Mono, monospace">{html.escape(cn_date(all_dates[0]))}</text>'
    )
    lines.append(
        f'<text x="{pad_left + inner_w}" y="{height - 12}" text-anchor="end" font-size="10" fill="#5a6478" font-family="JetBrains Mono, monospace">'
        f'{html.escape(cn_date(all_dates[-1]))}</text>'
    )
    for key, label, points in series:
        color = AGENT_LINE_COLOR.get(key, "#444")
        polyline = " ".join(f"{x_of(d):.1f},{y_of(v):.1f}" for d, v in points)
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{polyline}" />'
        )
        # Tiny end-of-line label
        last_d, last_v = points[-1]
        lines.append(
            f'<text x="{x_of(last_d) - 4:.1f}" y="{y_of(last_v) - 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="{color}">{html.escape(label)}</text>'
        )
    return (
        f'<svg class="nav-chart" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
        + "".join(lines)
        + '</svg>'
    )


_INDUSTRY_PREFIX_RE = re.compile(r"^[A-Z]\d{2}")


def _shorten_industry(name: str) -> str:
    """Drop ISIC-style "C36" prefix → "汽车制造业"."""

    if not name:
        return "-"
    return _INDUSTRY_PREFIX_RE.sub("", name).strip() or name


def _format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"¥{value:,.2f}"


def _position_codes(df: pd.DataFrame) -> set[str]:
    if df.empty or "code" not in df.columns:
        return set()
    return {str(value).zfill(6) for value in df["code"].dropna().tolist()}
