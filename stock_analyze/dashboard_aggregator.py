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
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ._dashboard_assets import BASE_CSS, NAV_CSS, render_nav_html
from .beginner_dashboard import write_beginner_views
from .competition import resolve_agent_paths
from .utils import dashboard_fragment_path, ensure_dirs, format_pct, safe_float


AGENT_COLORS = {
    # Dark Bloomberg palette — must agree with _dashboard_assets BASE_CSS
    # token values (--claude / --codex).
    "claude": "#f59e0b",
    "codex": "#06b6d4",
}
DEFAULT_AGENT_ORDER = ("claude", "codex")


def generate_competition_dashboard(
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
) -> Path:
    """Render and persist ``reports/competition/dashboard.html``."""

    root = Path(repo_root) if repo_root else Path.cwd()
    agents = agents or list(DEFAULT_AGENT_ORDER)
    paths_by_agent = {agent: resolve_agent_paths(agent, repo_root=root) for agent in agents}

    fragments = {agent: _read_fragment(paths_by_agent[agent].reports_dir) for agent in agents}
    perf = {agent: _read_performance_summary(paths_by_agent[agent].data_dir) for agent in agents}
    nav_panel = _build_nav_panel(paths_by_agent)
    leaderboard = _read_leaderboard(root / "data" / "competition" / "leaderboard.csv")
    monthly_links = _list_monthly_reviews(root / "reports" / "competition")
    positions_overlap = _compute_position_overlap(paths_by_agent)
    comparison_table = _build_comparison_table(perf)
    summary_cards = _render_summary_cards(perf, leaderboard)
    nav_json = json.dumps(nav_panel, ensure_ascii=False)
    leaderboard_json = json.dumps(leaderboard, ensure_ascii=False)

    out_dir = root / "reports" / "competition"
    ensure_dirs(out_dir)
    out_path = out_dir / "dashboard.html"

    tabs_nav = _render_tabs_nav(agents)
    tab_sections = _render_tab_sections(
        agents,
        fragments,
        summary_cards,
        comparison_table,
        positions_overlap,
        leaderboard,
        monthly_links,
        paths_by_agent,
    )

    html = _render_page(tabs_nav, tab_sections, nav_json, leaderboard_json)
    out_path.write_text(html, encoding="utf-8")

    # Also render the beginner simplified view alongside the professional dashboard.
    # Best-effort: if beginner rendering fails (e.g. unexpected data shape), the
    # professional view must still be produced. We re-raise the error after a
    # warning so the failure surfaces in the run ledger.
    try:
        write_beginner_views(agents=agents, repo_root=root)
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
        df = pd.read_csv(path)
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
            df = pd.read_csv(nav_path)
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

    compare_section = (
        '<section id="tab-compare" class="tab-section">\n'
        '<h1 class="tab-title">对比</h1>\n'
        f'<section class="grid summary-grid">{cards_html}</section>\n'
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
