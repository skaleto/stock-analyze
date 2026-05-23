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

from .beginner_format import cn_date, cn_relative_date, cny, pct
from .competition import AgentPaths, resolve_agent_paths
from .utils import safe_float


__all__ = [
    "render_beginner_competition_html",
    "render_beginner_agent_html",
    "write_beginner_views",
]


AGENT_DISPLAY = {"claude": "Claude", "codex": "Codex"}
AGENT_LINE_COLOR = {
    "claude": "#c4520d",
    "codex": "#2a6c9c",
    "000300": "#7a7a7a",
    "000905": "#b59a4a",
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
    sections.append(_render_tab_bar(active="simple"))
    sections.append(_render_account_card(state, today_iso))
    sections.append(_render_agent_score_cards(state))
    sections.append(_render_nav_chart(state, today_iso))
    for agent_id in state["agent_order"]:
        sections.append(_render_top_holdings(state, agent_id))
    sections.append(_render_position_overlap_summary(state))
    sections.append(_render_recent_trades(state, today_iso))
    sections.append(_render_monthly_evolution_summary(state, today_iso))
    sections.append(_render_footer_links())
    body = "\n".join(sections)
    return _shell_html("我的纸面投资 · 简化版", body, today_iso)


def render_beginner_agent_html(
    paths: AgentPaths,
    today: str | None = None,
) -> str:
    """Render a single-agent simplified page (Claude or Codex only)."""

    today_iso = today or date.today().isoformat()
    state = _gather_state({paths.agent_id: paths})
    sections: list[str] = []
    sections.append(_render_tab_bar(active="simple"))
    sections.append(_render_account_card(state, today_iso))
    sections.append(_render_agent_score_cards(state))
    sections.append(_render_nav_chart(state, today_iso))
    sections.append(_render_top_holdings(state, paths.agent_id))
    sections.append(_render_recent_trades(state, today_iso))
    sections.append(_render_monthly_evolution_summary(state, today_iso))
    sections.append(_render_footer_links())
    body = "\n".join(sections)
    display = AGENT_DISPLAY.get(paths.agent_id, paths.agent_id)
    return _shell_html(f"{display} · 简化版", body, today_iso)


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
        "paths": paths_by_agent,
        "perf": {},
        "nav": {},
        "positions": {},
        "trades": {},
        "evolution": {},
        "benchmarks": {},
    }
    for agent_id in agent_order:
        paths = paths_by_agent[agent_id]
        state["perf"][agent_id] = _read_performance_summary(paths.data_dir)
        state["nav"][agent_id] = _read_nav_dataframe(paths.data_dir)
        state["positions"][agent_id] = _read_positions(paths.data_dir)
        state["trades"][agent_id] = _read_trades(paths.data_dir)
        state["evolution"][agent_id] = _read_latest_evolution_log(paths.data_dir)

    state["benchmarks"] = _derive_benchmark_series(state["nav"])
    return state


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
        df = pd.read_csv(path)
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
            if code in result:
                continue
            sub = group[["date", "benchmark_close"]].dropna().drop_duplicates("date")
            sub = sub.sort_values("date")
            result[str(code)] = [
                {"date": row["date"], "close": safe_float(row["benchmark_close"])}
                for _, row in sub.iterrows()
                if safe_float(row["benchmark_close"]) is not None
            ]
    return result


# ---------------------------------------------------------------------------
# Section renderers


def _render_tab_bar(active: str) -> str:
    def cls(name: str) -> str:
        return "tab active" if name == active else "tab"

    return (
        '<nav class="tab-bar" data-id="0">'
        f'<a class="{cls("simple")}" href="/simple.html">简化版</a>'
        f'<a class="{cls("pro")}" href="/pro.html">专业版</a>'
        f'<a class="{cls("evolution")}" href="/competition/dashboard.html#tab-compare">策略演进</a>'
        '</nav>'
    )


def _render_account_card(state: dict[str, Any], today_iso: str) -> str:
    """Section 1: aggregated 总资产 / 今日 / 本月."""

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
    return (
        '<section class="card account-card" data-id="1">'
        '<h2>👤 我的账户</h2>'
        f'<div class="kpi-row">{"".join(items)}</div>'
        f'<p class="hint">截至 {html.escape(cn_date(today_iso))}，两个 AI 合计资产</p>'
        '</section>'
    )


def _render_agent_score_cards(state: dict[str, Any]) -> str:
    """Section 2: two agent score cards side-by-side."""

    cards: list[str] = []
    for agent_id in state["agent_order"]:
        cards.append(_render_single_agent_score(state, agent_id))
    return (
        '<section class="card" data-id="2">'
        '<h2>📊 两位 AI 的成绩</h2>'
        f'<div class="agent-grid">{"".join(cards)}</div>'
        '</section>'
    )


def _render_single_agent_score(state: dict[str, Any], agent_id: str) -> str:
    perf = state["perf"].get(agent_id, {})
    accounts = (perf.get("accounts") or {}) if perf else {}
    if not accounts:
        return (
            f'<div class="agent-score {agent_id}">'
            f'<div class="agent-name">{html.escape(AGENT_DISPLAY.get(agent_id, agent_id))}</div>'
            '<div class="empty">尚未开盘交易</div>'
            '</div>'
        )
    cumulative = _mean_metric(accounts, "cumulative_return")
    excess = _mean_metric(accounts, "cumulative_excess_return")
    info_ratio = _mean_metric(accounts, "information_ratio")
    benchmark_label = _format_benchmark_summary(accounts)

    lines: list[str] = []
    lines.append(
        f'<div class="agent-name">{html.escape(AGENT_DISPLAY.get(agent_id, agent_id))}</div>'
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
            series.append((agent_id, AGENT_DISPLAY.get(agent_id, agent_id), points))

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
    display_name = AGENT_DISPLAY.get(agent_id, agent_id)
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
    parts.append(_overlap_row(f"仅 {AGENT_DISPLAY.get(a, a)} 持有", only_a))
    parts.append(_overlap_row(f"仅 {AGENT_DISPLAY.get(b, b)} 持有", only_b))
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
        rows.append(_render_trade_row(row, today_iso))
    return (
        '<section class="card" data-id="7">'
        '<h2>🔄 最近 5 笔模拟成交</h2>'
        '<table class="trades">'
        '<thead><tr><th>日期</th><th>AI</th><th>股票</th><th>方向</th><th>股数</th><th>价格</th><th>成交额</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</section>'
    )


def _render_trade_row(row: pd.Series, today_iso: str) -> str:
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
        f'<td>{html.escape(AGENT_DISPLAY.get(agent, agent))}</td>'
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
            f'<h3>{html.escape(AGENT_DISPLAY.get(agent_id, agent_id))} · {html.escape(record.get("month", ""))}</h3>'
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


def _render_footer_links() -> str:
    return (
        '<footer class="footer">'
        '<a href="/pro.html">专业版</a> · '
        '<a href="/competition/dashboard.html">完整对比 dashboard</a> · '
        '<a href="/claude/dashboard.html">Claude 详情</a> · '
        '<a href="/codex/dashboard.html">Codex 详情</a>'
        '<p class="hint">本视图仅展示模拟成交,不构成任何投资建议。</p>'
        '</footer>'
    )


# ---------------------------------------------------------------------------
# Helpers


def _shell_html(title: str, body: str, today_iso: str) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_title = html.escape(title)
    today_display = html.escape(cn_date(today_iso))
    style = _CSS
    return f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{safe_title}</title>
  <style>{style}</style>
</head>
<body>
  <header class=\"page-header\">
    <h1>{safe_title}</h1>
    <p class=\"page-sub\">数据截至 {today_display} · 生成于 {generated_at}</p>
  </header>
  <main>
{body}
  </main>
</body>
</html>
"""


_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; background: #fffaf3; color: #2c1a00; }
main { max-width: 960px; margin: 0 auto; padding: 16px 24px 48px; }
.page-header { padding: 20px 24px; background: linear-gradient(135deg, #c4520d, #e88a3a); color: #fff; }
.page-header h1 { margin: 0; font-size: 22px; }
.page-sub { margin: 6px 0 0; font-size: 13px; opacity: 0.92; }
.tab-bar { display: flex; gap: 12px; margin: 14px 0 18px; }
.tab { padding: 8px 18px; border-radius: 999px; background: #f1e4cc; color: #2c1a00; text-decoration: none; font-size: 14px; }
.tab.active { background: #c4520d; color: #fff; box-shadow: 0 2px 6px rgba(196,82,13,0.25); }
.card { background: #fff; border: 1px solid #e6d6b8; border-radius: 14px; padding: 18px 22px; margin: 16px 0; box-shadow: 0 2px 8px rgba(70,40,0,0.05); }
.card h2 { margin: 0 0 12px; font-size: 18px; color: #5a3a18; }
.kpi-row { display: flex; gap: 24px; flex-wrap: wrap; }
.kpi { flex: 1 1 220px; }
.kpi-label { font-size: 13px; color: #8c7350; }
.kpi-value { font-size: 22px; font-weight: 600; }
.kpi-value.big { font-size: 30px; }
.kpi-sub { font-size: 13px; color: #8c7350; margin-top: 2px; }
.pos { color: #c4520d; }
.neg { color: #1a7340; }
.zero { color: #8c7350; }
.agent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.agent-score { padding: 14px 16px; background: #fcf3e0; border-radius: 12px; }
.agent-score.codex { background: #ecf3f9; }
.agent-name { font-weight: 600; font-size: 15px; color: #5a3a18; }
.agent-score.codex .agent-name { color: #1f4c70; }
.agent-cumulative { font-size: 26px; font-weight: 700; margin: 4px 0; }
.agent-cumulative-label { font-size: 12px; color: #8c7350; margin-bottom: 8px; }
.agent-vs-benchmark { font-size: 14px; margin-bottom: 4px; }
.agent-ir { font-size: 12px; color: #8c7350; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
table.holdings th, table.holdings td, table.trades th, table.trades td { padding: 8px 10px; text-align: right; border-bottom: 1px solid #f3e4c4; }
table.holdings th:first-child, table.holdings td:first-child, table.trades th:first-child, table.trades td:first-child { text-align: left; }
table.holdings th, table.trades th { background: #fcf3e0; color: #6b4820; font-weight: 600; }
.stock-name { font-weight: 600; margin-right: 6px; }
.stock-code { color: #8c7350; font-size: 12px; }
.trade-side.buy { color: #c4520d; font-weight: 600; }
.trade-side.sell { color: #1a7340; font-weight: 600; }
.overlap-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.overlap-cell { background: #fcf3e0; border-radius: 10px; padding: 12px 14px; }
.overlap-label { font-size: 13px; color: #8c7350; margin-bottom: 4px; }
.overlap-value { font-size: 14px; }
.overlap-value.empty { color: #8c7350; }
.legend { display: flex; gap: 16px; margin-top: 10px; font-size: 13px; color: #6b4820; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.empty { color: #8c7350; font-size: 14px; }
.hint { color: #8c7350; font-size: 12px; margin: 6px 0 0; }
.evolution-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.evolution-cell { background: #fcf3e0; border-radius: 10px; padding: 12px 14px; }
.evolution-cell h3 { margin: 0 0 6px; font-size: 14px; color: #5a3a18; }
.evolution-cell p { margin: 0; font-size: 13px; color: #4a3008; }
.footer { text-align: center; color: #8c7350; font-size: 13px; margin: 24px 0; }
.footer a { color: #c4520d; margin: 0 6px; text-decoration: none; }
svg.nav-chart { width: 100%; height: 260px; background: #fff7e8; border-radius: 8px; }
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
    # axis frame
    lines.append(
        f'<rect x="{pad_left}" y="{pad_top}" width="{inner_w}" height="{inner_h}" '
        'fill="#fff7e8" stroke="#e6d6b8" />'
    )
    # baseline at 1.0
    if y_min <= 1.0 <= y_max:
        y1 = y_of(1.0)
        lines.append(
            f'<line x1="{pad_left}" y1="{y1:.1f}" x2="{pad_left + inner_w}" y2="{y1:.1f}" '
            'stroke="#cdb98a" stroke-dasharray="4 4" />'
        )
        lines.append(
            f'<text x="{pad_left - 8}" y="{y1 + 4:.1f}" text-anchor="end" font-size="10" fill="#8c7350">1.00</text>'
        )
    # y labels min/max
    lines.append(
        f'<text x="{pad_left - 8}" y="{pad_top + 4}" text-anchor="end" font-size="10" fill="#8c7350">{y_max:.3f}</text>'
    )
    lines.append(
        f'<text x="{pad_left - 8}" y="{pad_top + inner_h:.1f}" text-anchor="end" font-size="10" fill="#8c7350">{y_min:.3f}</text>'
    )
    # x labels: first and last
    lines.append(
        f'<text x="{pad_left}" y="{height - 12}" font-size="10" fill="#8c7350">{html.escape(cn_date(all_dates[0]))}</text>'
    )
    lines.append(
        f'<text x="{pad_left + inner_w}" y="{height - 12}" text-anchor="end" font-size="10" fill="#8c7350">'
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
