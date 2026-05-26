"""Dashboard panel renderers extracted from ``reporting/__init__.py``.

This module is part of the I1 split (per the 2026-05-26 project audit) to
keep ``reporting/__init__.py`` from growing unbounded. The functions here
are re-exported from ``stock_analyze.reporting`` so any external caller
that imports ``from stock_analyze.reporting import render_market_sentiment_panel``
continues to work byte-equivalently.

Panels currently lifted here:
  - :func:`render_market_sentiment_panel` — per-agent LLM sentiment timeline
  - :func:`render_backtest_vs_live_panel` — historical backtest vs live NAV
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..utils import today as _today


def render_market_sentiment_panel(agent_id: str, repo_root: Path | str) -> str:
    """Render the per-agent market sentiment timeline panel (professional view).

    Reads ``data/<agent>/alt_factors/market_sentiment.csv`` and shows:
    - Latest week's score + confidence + key_drivers
    - 4-week and 8-week rolling means
    - A "未更新 N 周" warning when the latest row is > 2 weeks old
    """
    from stock_analyze.alt_factors import sentiment as _alt_sent

    rows = _alt_sent.load_sentiment_history(agent_id, Path(repo_root), last_n=26)
    if not rows:
        return (
            f'<div class="panel"><h3>{agent_id} 市场情感</h3>'
            f'<p>尚无记录。请跑 <code>record-sentiment --agent {agent_id} ...</code>'
            f' 把每周 LLM 客户端的情感判断落盘。</p></div>'
        )

    latest = rows[-1]
    last_4 = rows[-4:] if len(rows) >= 4 else rows
    last_8 = rows[-8:] if len(rows) >= 8 else rows
    avg_4 = sum(r.score for r in last_4) / len(last_4)
    avg_8 = sum(r.score for r in last_8) / len(last_8)

    today_d = _today()
    days_since = (today_d - latest.week_end).days
    stale_html = ""
    if days_since > 14:
        weeks_stale = days_since // 7
        stale_html = (
            f'<p class="warn">⚠️ {agent_id} 已 {weeks_stale} 周未更新市场情感'
            f'（最近 {latest.week_end.isoformat()}）</p>'
        )

    drivers_html = "".join(f"<li>{d}</li>" for d in latest.drivers)
    sources_html = "".join(
        f'<li><a href="{s}">{s}</a></li>' for s in latest.sources
    )

    return (
        f'<div class="panel">\n'
        f'  <h3>{agent_id} 市场情感（过去 {len(rows)} 周）</h3>\n'
        f'  {stale_html}\n'
        f'  <ul class="metrics">\n'
        f'    <li>最新 ({latest.week_end.isoformat()}): '
        f'{latest.score:+.2f} (信心 {latest.confidence:.2f})</li>\n'
        f'    <li>4 周均值: {avg_4:+.2f}</li>\n'
        f'    <li>8 周均值: {avg_8:+.2f}</li>\n'
        f'  </ul>\n'
        f'  <details><summary>本周关键驱动</summary>'
        f'<ul>{drivers_html}</ul></details>\n'
        f'  <details><summary>参考新闻来源</summary>'
        f'<ul>{sources_html}</ul></details>\n'
        f'</div>'
    )


def render_backtest_vs_live_panel(agent_id: str, repo_root: Path | str) -> str:
    """Render the historical-backtest-vs-live-NAV comparison panel.

    Reads:
      - data/<agent>/backtest/training/<latest>/daily_nav.csv (historical)
      - data/<agent>/daily_nav.csv (live)

    Returns an HTML fragment; emitted into the professional dashboard's
    Claude / Codex tabs. New-beginner dashboard does NOT include this panel.
    """
    root = Path(repo_root)
    train_root = root / "data" / agent_id / "backtest" / "training"
    if not train_root.exists() or not any(train_root.iterdir()):
        return (
            '<div class="panel"><h3>历史回测 vs 真实运行</h3>'
            '<p>尚无训练窗口回测数据。请先跑 <code>prepare-backtest-data</code> '
            '+ 自动月度训练回测。</p></div>'
        )
    runs = sorted(p for p in train_root.iterdir() if p.is_dir())
    if not runs:
        return (
            '<div class="panel"><h3>历史回测 vs 真实运行</h3>'
            '<p>尚无训练窗口回测数据。</p></div>'
        )
    bt_nav_path = runs[-1] / "daily_nav.csv"
    live_nav_path = root / "data" / agent_id / "daily_nav.csv"

    # Mirror store.py daily_nav dtype invariant — benchmark_code must stay str
    # so '000300' isn't coerced to int 300 mid-merge.
    _nav_dtype = {
        "date": str,
        "account_id": str,
        "benchmark_code": str,
        "benchmark_date": str,
    }
    bt_df = pd.read_csv(bt_nav_path, dtype=_nav_dtype) if bt_nav_path.exists() else pd.DataFrame()
    live_df = pd.read_csv(live_nav_path, dtype=_nav_dtype) if live_nav_path.exists() else pd.DataFrame()

    def _cum_return(df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        p = df.groupby("date")["total_value"].sum().sort_index()
        if len(p) < 2:
            return 0.0
        return float(p.iloc[-1] / p.iloc[0] - 1)

    bt_cum = _cum_return(bt_df)
    live_cum = _cum_return(live_df)
    diff = bt_cum - live_cum
    warn_cls = ' class="warn"' if abs(diff) > 0.05 else ""

    return (
        f'<div class="panel">\n'
        f'  <h3>历史回测 vs 真实运行</h3>\n'
        f'  <p>(浅色 = 历史回测；深色 = live 真实运行；灰色虚线 = 基准)</p>\n'
        f'  <table>\n'
        f'    <tr><th></th><th>累计收益</th></tr>\n'
        f'    <tr><td>历史回测</td><td>{bt_cum:+.1%}</td></tr>\n'
        f'    <tr><td>真实运行</td><td>{live_cum:+.1%}</td></tr>\n'
        f'    <tr{warn_cls}><td>差异</td><td>{diff:+.1%}</td></tr>\n'
        f'  </table>\n'
        f'</div>'
    )
