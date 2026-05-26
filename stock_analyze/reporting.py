from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import config_hash
from .performance import compute_account_performance
from .run_ledger import code_version, read_runs
from .store import (
    FACTOR_COVERAGE_COLUMNS,
    FORWARD_IC_COLUMNS,
    PENDING_FILE,
    PERFORMANCE_FILE,
    SIGNALS_FILE,
    PortfolioStore,
)
from ._dashboard_assets import BASE_CSS, NAV_CSS, render_nav_html
from .utils import (
    dashboard_fragment_path,
    ensure_dirs,
    format_money,
    format_pct,
    safe_float,
    today as _today,
    today_str,
    write_json,
)


POSITION_COLUMNS = {
    "account_id": "账户",
    "code": "代码",
    "name": "名称",
    "shares": "持股数",
    "available_shares": "可卖股数",
    "avg_cost": "平均成本",
    "last_buy_date": "最近买入日",
    "last_price": "最新价",
    "market_value": "市值",
    "unrealized_pnl": "浮动盈亏",
    "score": "入选分",
    "reason": "入选原因",
    "updated_at": "更新时间",
}

TRADE_COLUMNS = {
    "trade_date": "日期",
    "account_id": "账户",
    "code": "代码",
    "name": "名称",
    "side": "方向",
    "shares": "股数",
    "price": "成交价",
    "gross_amount": "成交金额",
    "commission": "佣金",
    "stamp_tax": "印花税",
    "slippage": "滑点成本",
    "net_amount": "净额",
    "reason": "原因",
}

HEALTH_COLUMNS = {
    "time": "时间",
    "source": "数据源",
    "status": "状态",
    "rows": "行数",
    "message": "说明",
}

SIGNAL_COLUMNS = {
    "account_id": "账户",
    "code": "代码",
    "name": "名称",
    "score": "综合分",
    "pe": "PE",
    "pb": "PB",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "debt_ratio": "资产负债率",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
    "score_detail": "因子贡献",
    "data_warnings": "数据提示",
}

PENDING_COLUMNS = {
    "signal_date": "信号日",
    "execute_after": "模拟成交日",
    "account_id": "账户",
    "status": "状态",
    "unfilled_reason": "未成交原因",
    "attempts": "尝试次数",
    "side": "方向",
    "code": "代码",
    "name": "名称",
    "delta_shares": "计划股数",
    "reference_price": "参考价",
    "score": "入选分",
    "reason": "原因",
}

SIDE_LABELS = {
    "buy": "买入",
    "sell": "卖出",
}

STATUS_LABELS = {
    "ok": "成功",
    "retry": "退避重试",
    "failed": "失败",
    "cache": "使用缓存",
    "pending": "待执行",
    "partial": "部分成交",
    "filled": "已完成",
    "running": "运行中",
    "success": "成功",
}

REASON_LABELS = {
    "not_selected": "本期未入选，调出组合",
    "history_missing": "历史行情缺失",
    "price_missing": "价格缺失",
    "financial_fetch_failed": "财务数据获取失败",
    "pe_missing": "PE 缺失",
    "pb_missing": "PB 缺失",
    "execution_quote_missing": "缺少模拟成交行情",
    "execution_quote_not_visible": "运行日尚无可见成交行情",
    "execution_price_missing": "模拟成交价缺失",
    "limit_up_buy_blocked": "涨停买入阻塞",
    "limit_down_sell_blocked": "跌停卖出阻塞",
    "paused": "停牌阻塞",
    "no_position": "无可卖持仓",
    "no_sellable_shares": "T+1 或可卖股数不足",
    "insufficient_cash": "现金不足",
    "partial_fill": "部分成交",
}

FACTOR_LABELS = {
    "pe": "PE",
    "pb": "PB",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "debt_ratio": "资产负债率",
    "market_cap_yi": "总市值",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
}

SOURCE_LABELS = {
    # Current sources (Tushare Pro primary + Baostock fallback)
    "spot_daily_basic": "Tushare daily_basic",
    "spot_daily": "Tushare daily",
    "spot_stock_basic": "Tushare stock_basic",
    "stock_basic": "Tushare 股票基础信息",
    "trade_cal": "Tushare 交易日历",
    "baostock_login": "Baostock 登录",
    "spot": "实时行情",
    "universe": "股票池",
    "basic": "个股基础信息",
    # Legacy labels kept so old data_health.json files still render
    "spot_eastmoney": "东方财富实时行情",
    "spot_sina": "新浪实时行情",
    "index_cons_csindex": "中证指数成分",
    "index_cons_weight_csindex": "中证权重成分",
    "index_cons_default": "AkShare 默认成分",
    "index_cons_baostock": "Baostock 指数成分",
    "history_eastmoney": "东方财富历史行情",
    "history_tencent": "腾讯历史行情",
    "history_sina": "新浪历史行情",
    "history_baostock": "Baostock 历史行情",
    "benchmark_eastmoney": "东方财富指数行情",
    "benchmark_tencent": "腾讯指数行情",
    "benchmark_sina": "新浪指数行情",
    "financial_abstract": "AkShare 财务摘要",
    "financial_indicator": "AkShare 财务指标",
    "financial_baostock": "Baostock 财务指标",
    "valuation_baostock": "Baostock 估值",
    "valuation_市盈率(TTM)": "百度估值 PE",
    "valuation_市净率": "百度估值 PB",
}


def compute_performance(config: dict[str, Any], store: PortfolioStore) -> dict[str, Any]:
    nav = store.read_nav()
    trades = store.read_trades()
    perf_cfg = config.get("performance", {}) or {}
    accounts = compute_account_performance(
        nav,
        trades,
        risk_free_rate=float(perf_cfg.get("risk_free_rate", 0.02) or 0.0),
        trading_days_per_year=int(perf_cfg.get("trading_days_per_year", 252) or 252),
    )
    summary: dict[str, Any] = {
        "strategy_id": config.get("strategy_id"),
        "generated_at": today_str(),
        "config_hash": config_hash(config),
        "code_version": code_version(),
        "accounts": accounts,
        "objective": config.get("objective", {}),
    }
    write_json(store.data_dir / PERFORMANCE_FILE, summary)
    return summary


def generate_weekly_report(
    config: dict[str, Any],
    store: PortfolioStore,
    reports_dir: str | Path,
    run_id: str | None = None,
) -> Path:
    ensure_dirs(reports_dir)
    summary = compute_performance(config, store)
    positions = store.read_positions()
    trades = store.read_trades()
    health = display_health(read_health(store)).tail(20)
    path = Path(reports_dir) / "weekly_report.md"

    lines = [
        f"# 周度模拟交易报告 - {today_str()}",
        "",
        "本报告只来自模拟交易数据，不构成投资建议。",
        "",
        f"`run_id={run_id or '-'}` · `config_hash={summary.get('config_hash')}` · `code_version={summary.get('code_version')}`",
        "",
        "## 绩效概览",
        "",
        "| 账户 | 最新资产 | 累计收益 | 年化收益 | 年化波动 | Sharpe | Sortino | 最大回撤 | 年化超额 | 信息比率 | 换手率 | 成本(bps) | Win Rate | 净值点数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    accounts = summary.get("accounts", {})
    if not accounts:
        lines.append("| 暂无绩效数据 | - | - | - | - | - | - | - | - | - | - | - | - | 0 |")
    for account_id, item in accounts.items():
        lines.append(
            f"| {account_id} | {format_money(item.get('latest_value'))} | "
            f"{format_pct(item.get('cumulative_return'))} | {format_pct(item.get('annualized_return'))} | "
            f"{format_pct(item.get('annualized_volatility'))} | {format_ratio(item.get('sharpe_ratio'))} | "
            f"{format_ratio(item.get('sortino_ratio'))} | {format_pct(item.get('max_drawdown'))} | "
            f"{format_pct(item.get('annualized_excess_return'))} | {format_ratio(item.get('information_ratio'))} | "
            f"{format_pct(item.get('weekly_turnover_avg'))} | {format_bps(item.get('cost_bps'))} | "
            f"{format_pct(item.get('round_trip_win_rate'))} | {item.get('nav_points', 0)} |"
        )

    lines.extend(["", "## 当前持仓", ""])
    if positions.empty:
        lines.append("暂无持仓。若刚完成初始化，请等待周度信号和下一交易日模拟成交后再观察。")
    else:
        lines.extend(["| 账户 | 代码 | 名称 | 股数 | 平均成本 | 最新价 | 市值 | 浮动盈亏 |", "|---|---|---|---:|---:|---:|---:|---:|"])
        for _, row in positions.iterrows():
            lines.append(
                f"| {row.get('account_id')} | {row.get('code')} | {row.get('name')} | "
                f"{row.get('shares')} | {row.get('avg_cost')} | {row.get('last_price')} | "
                f"{format_money(row.get('market_value'))} | {format_money(row.get('unrealized_pnl'))} |"
            )

    lines.extend(["", "## 近期交易", ""])
    if trades.empty:
        lines.append("暂无交易。周度调仓会先生成待执行订单，下一交易日再按模拟成交价入账。")
    else:
        recent = trades.tail(20)
        lines.extend(["| 日期 | 账户 | 方向 | 代码 | 名称 | 股数 | 成交价 | 成本 |", "|---|---|---|---|---|---:|---:|---:|"])
        for _, row in recent.iterrows():
            total_cost = (safe_float(row.get("commission")) or 0) + (safe_float(row.get("stamp_tax")) or 0)
            side = "买入" if row.get("side") == "buy" else "卖出"
            lines.append(
                f"| {row.get('trade_date')} | {row.get('account_id')} | {side} | "
                f"{row.get('code')} | {row.get('name')} | {row.get('shares')} | "
                f"{row.get('price')} | {format_money(total_cost)} |"
            )

    lines.extend(["", "## 数据源状态", ""])
    if health.empty:
        lines.append("暂无数据源状态。请运行周度或日度任务后查看。")
    else:
        lines.extend(["| 时间 | 数据源 | 状态 | 行数 | 说明 |", "|---|---|---:|---:|---|"])
        for _, row in health.iterrows():
            lines.append(
                f"| {row.get('time')} | {row.get('source')} | {row.get('status')} | "
                f"{row.get('rows')} | {row.get('message')} |"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_dashboard(
    config: dict[str, Any],
    store: PortfolioStore,
    reports_dir: str | Path,
    mode: str = "page",
) -> Path:
    """Render the per-agent dashboard.

    ``mode="page"`` (default) writes a full ``dashboard.html`` document.
    ``mode="fragment"`` writes ``dashboard_fragment.html`` containing the
    embeddable ``<section class="agent-dashboard">`` block plus its inline
    `<style>` and `<script>` tags, so the aggregator can splice multiple
    agents into a single page without a full document boundary.
    """

    if mode not in {"page", "fragment"}:
        raise ValueError(f"unknown dashboard mode: {mode}")
    ensure_dirs(reports_dir)
    summary = compute_performance(config, store)
    nav = store.read_nav()
    positions = store.read_positions()
    trades = store.read_trades()
    health = read_health(store)
    signals = read_signals(store)
    pending_orders = read_pending_orders(store)
    price_panels = load_price_panels(store, signals)
    factor_summary = build_factor_summary(signals)
    coverage = read_coverage(store)
    forward_ic = read_forward_ic_df(store)
    runs = read_runs(store.data_dir)
    perf_cfg = config.get("performance", {}) or {}
    low_threshold = float(perf_cfg.get("low_coverage_threshold", 0.5) or 0.5)
    coverage_panel = build_coverage_panel(coverage, low_threshold=low_threshold)
    forward_ic_panel = build_forward_ic_panel(forward_ic)
    if mode == "fragment":
        # Build artifact: live in data/_dashboard_build/<agent>/, not reports/.
        # See utils.dashboard_fragment_path docstring for rationale (2026-05-24).
        dashboard_path = dashboard_fragment_path(reports_dir)
        ensure_dirs(dashboard_path.parent)
    else:
        dashboard_path = Path(reports_dir) / "dashboard.html"
    agent_id = str(config.get("agent_id") or "agent")
    notes_html = render_agent_notes_panel(store.data_dir)
    leaderboard_path = store.data_dir.parent / "competition" / "leaderboard.csv"
    strategy_evolution_html = render_strategy_evolution_panel(store.data_dir, leaderboard_path=leaderboard_path)
    latest_briefing_html = render_latest_briefing_panel(store.data_dir)

    nav_json = nav.to_json(orient="records", force_ascii=False) if not nav.empty else "[]"
    price_json = json.dumps(price_panels, ensure_ascii=False)
    factor_json = json.dumps(factor_summary, ensure_ascii=False)
    coverage_json = json.dumps(coverage_panel, ensure_ascii=False)
    forward_ic_json = json.dumps(forward_ic_panel, ensure_ascii=False)
    positions_html = dataframe_html(display_positions(positions), POSITION_COLUMNS, "暂无持仓。请先运行周度信号，等待下一交易日模拟成交后再观察。")
    trades_html = dataframe_html(display_trades(trades.tail(30)), TRADE_COLUMNS, "暂无交易。周度任务会先生成待执行订单，下一交易日由日度任务模拟成交。")
    health_html = dataframe_html(display_health(health.tail(40)), HEALTH_COLUMNS, "暂无数据源状态。运行周度或日度任务后会显示接口、缓存和降级情况。")
    signals_html = dataframe_html(display_signals(signals), SIGNAL_COLUMNS, "暂无选股信号。请先运行 run-weekly。")
    pending_html = dataframe_html(display_pending_orders(pending_orders), PENDING_COLUMNS, "暂无待执行订单。请先运行 run-weekly 生成调仓计划。")
    execution_hint = pending_execution_hint(pending_orders)
    performance_cards_html = render_performance_cards(summary)
    runs_html = render_runs_table(runs)
    cards = []
    for account_id, item in summary.get("accounts", {}).items():
        status = "模拟观察中"
        cards.append(
            f"""
            <section class="metric-card">
              <div class="card-label">{account_id}</div>
              <div class="metric">{format_money(item.get('latest_value'))}</div>
              <p>累计收益 {format_pct(item.get('cumulative_return'))} · 最大回撤 {format_pct(item.get('max_drawdown'))}</p>
              <span class="tag">{status}</span>
            </section>
            """
        )

    # Nav and active key — fragments are embedded inside competition/dashboard.html
    # by dashboard_aggregator, so they must not carry their own top nav.
    if mode == "fragment":
        nav_html = ""
    else:
        agent_key = f"pro-{agent_id}" if agent_id in ("claude", "codex") else None
        nav_html = render_nav_html(active=agent_key, data_as_of=summary.get("generated_at"))

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>模拟交易仪表盘 · {agent_id}</title>
  <style>{BASE_CSS}
{NAV_CSS}
{_PRO_CSS}</style>
</head>
<body>
  {nav_html}
  <header class="page-header">
    <h1>{agent_id.capitalize()} · 专业版</h1>
    <div class="subhead">策略 {config.get('strategy_id')} · 生成 {summary.get('generated_at')} · 仅模拟，不构成投资建议</div>
  </header>
  <main>
    <section class="grid">{''.join(cards) or '<p class="empty">暂无净值数据。请先运行初始化和周度任务。</p>'}</section>
    <div class="sub-tabs-container">
      <nav class="sub-tabs" data-active="results">
        <button type="button" class="sub-tab-btn" data-target="results">📊 结果</button>
        <button type="button" class="sub-tab-btn" data-target="insights">💡 洞察</button>
        <button type="button" class="sub-tab-btn" data-target="health">🩺 健康</button>
        <button type="button" class="sub-tab-btn" data-target="evolution">📜 演化</button>
      </nav>
      <section class="sub-content" data-tab="results">
        <h2>净值曲线</h2>
        <div class="panel">
          <canvas id="navChart" width="1200" height="340"></canvas>
          <div class="hint">曲线基于模拟账户净值生成；如果只有一个净值点，图上会显示为水平参考线。</div>
        </div>
        <h2>绩效解释</h2>
        <section class="grid">{performance_cards_html}</section>
        <h2>待执行模拟订单</h2>
        <div class="panel">
          {pending_html}
          <div class="hint warning">{execution_hint}</div>
        </div>
        <h2>当前持仓</h2>
        <div class="panel">{positions_html}</div>
        <h2>近期交易</h2>
        <div class="panel">{trades_html}</div>
      </section>
      <section class="sub-content" data-tab="insights" hidden>
        <section class="split">
          <div>
            <h2>本期选股信号</h2>
            <div class="panel">{signals_html}</div>
          </div>
          <div>
            <h2>因子贡献均值</h2>
            <div class="panel">
              <canvas id="factorChart" width="680" height="340"></canvas>
              <div class="hint">从 <code>score_detail</code> 解析各因子对入选股票的平均贡献。</div>
            </div>
          </div>
        </section>
        <h2>候选股价格走势</h2>
        <div class="price-grid" id="priceGrid"></div>
        <h2>因子诊断</h2>
        <div class="panel-row">
          <div class="panel">
            <p class="chart-title">最近 12 周因子覆盖率</p>
            <div id="coveragePanel"></div>
            <div class="hint">覆盖率低于阈值的格子高亮；阈值在 <code>performance.low_coverage_threshold</code> 控制。</div>
          </div>
          <div class="panel">
            <p class="chart-title">最近 12 周前向 5 日 RankIC</p>
            <canvas id="forwardIcChart" width="640" height="260"></canvas>
            <div class="hint">RankIC 使用 Spearman；NAV 历史不足 5 个交易日时会出现 <code>insufficient_history</code> 占位点。</div>
          </div>
        </div>
      </section>
      <section class="sub-content" data-tab="health" hidden>
        <h2>数据源状态</h2>
        <div class="panel">{health_html}</div>
        <h2>最近运行</h2>
        <div class="panel">{runs_html}</div>
        <h2>本期分析任务包</h2>
        <div class="panel">{latest_briefing_html}</div>
      </section>
      <section class="sub-content" data-tab="evolution" hidden>
        <h2>近期 agent 笔记</h2>
        <div class="panel">{notes_html}</div>
        <h2>策略演进时间线</h2>
        <div class="panel">{strategy_evolution_html}</div>
      </section>
    </div>
  </main>
  <script>
    const nav = {nav_json};
    const factorSummary = {factor_json};
    const pricePanels = {price_json};
    const coveragePanel = {coverage_json};
    const forwardIcPanel = {forward_ic_json};
    const canvas = document.getElementById('navChart');
    const ctx = canvas.getContext('2d');
    function drawChart(rows) {{
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = '13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      if (!rows.length) {{
        ctx.fillStyle = '#8b95a7';
        ctx.fillText('暂无净值数据', 24, 40);
        return;
      }}
      const accounts = [...new Set(rows.map(r => r.account_id))];
      // Dark Bloomberg palette: amber (Claude side), cyan (Codex side), warm grey for any 3rd account.
      const colors = ['#f59e0b', '#06b6d4', '#8b95a7'];
      const values = rows.map(r => Number(r.total_value)).filter(Number.isFinite);
      const min = Math.min(...values);
      const max = Math.max(...values);
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
      accounts.forEach((account, idx) => {{
        const series = rows.filter(r => r.account_id === account);
        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        series.forEach((row, i) => {{
          const x = pad + (canvas.width - pad * 2) * (i / Math.max(series.length - 1, 1));
          const y = canvas.height - pad - (canvas.height - pad * 2) * ((Number(row.total_value) - min) / Math.max(max - min, 1));
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          if (series.length === 1) {{
            ctx.arc(x, y, 3, 0, Math.PI * 2);
          }}
        }});
        ctx.stroke();
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillText(account, canvas.width - 130, 28 + idx * 20);
      }});
    }}
    drawChart(nav);

    function drawFactorChart(items) {{
      const chart = document.getElementById('factorChart');
      const c = chart.getContext('2d');
      c.clearRect(0, 0, chart.width, chart.height);
      c.font = '13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      if (!items.length) {{
        c.fillStyle = '#8b95a7';
        c.fillText('暂无因子贡献数据', 24, 40);
        return;
      }}
      const max = Math.max(...items.map(item => item.value), 1);
      const left = 96;
      const top = 24;
      const rowH = 34;
      items.slice(0, 8).forEach((item, idx) => {{
        const y = top + idx * rowH;
        const w = (chart.width - left - 70) * item.value / max;
        c.fillStyle = '#5a6478';
        c.fillText(item.label, 12, y + 18);
        c.fillStyle = '#2457a7';
        c.fillRect(left, y, w, 18);
        c.fillStyle = '#8b95a7';
        c.fillText(item.value.toFixed(1), left + w + 8, y + 15);
      }});
    }}

    function drawPricePanels(panels) {{
      const grid = document.getElementById('priceGrid');
      if (!panels.length) {{
        grid.innerHTML = '<div class="panel"><p class="empty">暂无可用历史行情缓存。运行 run-weekly 后会展示入选股票走势。</p></div>';
        return;
      }}
      panels.forEach((panel, idx) => {{
        const box = document.createElement('section');
        box.className = 'panel mini-chart';
        box.innerHTML = `<p class="chart-title">${{panel.code}} ${{panel.name}} · ${{panel.account_id}}</p><canvas width="520" height="180"></canvas>`;
        grid.appendChild(box);
        drawCandles(box.querySelector('canvas'), panel.rows);
      }});
    }}

    function drawCandles(chart, rows) {{
      const c = chart.getContext('2d');
      c.clearRect(0, 0, chart.width, chart.height);
      const prices = rows.flatMap(r => [Number(r.high), Number(r.low), Number(r.close)]).filter(Number.isFinite);
      if (!prices.length) {{
        c.fillStyle = '#8b95a7';
        c.fillText('无价格数据', 20, 40);
        return;
      }}
      const min = Math.min(...prices);
      const max = Math.max(...prices);
      const pad = 24;
      const span = Math.max(max - min, 1);
      const xStep = (chart.width - pad * 2) / Math.max(rows.length - 1, 1);
      c.strokeStyle = '#2a3145';
      c.beginPath();
      c.moveTo(pad, pad);
      c.lineTo(pad, chart.height - pad);
      c.lineTo(chart.width - pad, chart.height - pad);
      c.stroke();
      rows.forEach((r, i) => {{
        const open = Number(r.open), close = Number(r.close), high = Number(r.high), low = Number(r.low);
        if (![open, close, high, low].every(Number.isFinite)) return;
        const x = pad + i * xStep;
        const yHigh = chart.height - pad - (high - min) / span * (chart.height - pad * 2);
        const yLow = chart.height - pad - (low - min) / span * (chart.height - pad * 2);
        const yOpen = chart.height - pad - (open - min) / span * (chart.height - pad * 2);
        const yClose = chart.height - pad - (close - min) / span * (chart.height - pad * 2);
        const up = close >= open;
        // A-share convention: red = up, green = down (matches --pos/--neg).
        c.strokeStyle = up ? '#ef4444' : '#22c55e';
        c.fillStyle = up ? '#ef4444' : '#22c55e';
        c.beginPath();
        c.moveTo(x, yHigh);
        c.lineTo(x, yLow);
        c.stroke();
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(Math.abs(yClose - yOpen), 2);
        c.fillRect(x - 2, bodyTop, 4, bodyH);
      }});
      c.fillStyle = '#8b95a7';
      c.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      c.fillText(max.toFixed(2), 4, pad + 4);
      c.fillText(min.toFixed(2), 4, chart.height - pad);
    }}

    drawFactorChart(factorSummary);
    drawPricePanels(pricePanels);

    function renderCoveragePanel(rows) {{
      const root = document.getElementById('coveragePanel');
      if (!rows.length) {{
        root.innerHTML = '<p class="empty">尚无因子诊断数据，跑过至少一次 run-weekly 后再观察。</p>';
        return;
      }}
      const allDates = Array.from(new Set(rows.flatMap(row => row.items.map(item => item.signal_date)))).sort();
      let html = '<table class="heatmap"><thead><tr><th>因子</th>';
      allDates.forEach(date => {{ html += `<th>${{date.slice(5)}}</th>`; }});
      html += '</tr></thead><tbody>';
      rows.forEach(row => {{
        html += `<tr><td style="text-align:left">${{row.label}}</td>`;
        const map = new Map(row.items.map(item => [item.signal_date, item]));
        allDates.forEach(date => {{
          const cell = map.get(date);
          if (!cell) {{
            html += '<td>-</td>';
          }} else {{
            const cls = cell.low ? 'low' : '';
            html += `<td class="${{cls}}">${{(cell.coverage_pct * 100).toFixed(0)}}%</td>`;
          }}
        }});
        html += '</tr>';
      }});
      html += '</tbody></table>';
      root.innerHTML = html;
    }}

    function renderForwardIc(panels) {{
      const chart = document.getElementById('forwardIcChart');
      const c = chart.getContext('2d');
      c.clearRect(0, 0, chart.width, chart.height);
      c.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      if (!panels.length) {{
        c.fillStyle = '#8b95a7';
        c.fillText('暂无前向 RankIC 数据。等待 5 个交易日后会自动累积。', 24, 40);
        return;
      }}
      const allDates = Array.from(new Set(panels.flatMap(p => p.series.map(s => s.signal_date)))).sort();
      const xStep = (chart.width - 90) / Math.max(allDates.length - 1, 1);
      const yPad = 18;
      const usable = chart.height - yPad * 2;
      const drawAxis = () => {{
        c.strokeStyle = '#2a3145';
        c.lineWidth = 1;
        c.beginPath();
        c.moveTo(50, yPad);
        c.lineTo(50, chart.height - yPad);
        c.lineTo(chart.width - 12, chart.height - yPad);
        c.stroke();
        const zeroY = yPad + usable * 0.5;
        c.strokeStyle = '#2a3145';
        c.beginPath();
        c.moveTo(50, zeroY);
        c.lineTo(chart.width - 12, zeroY);
        c.stroke();
        c.fillStyle = '#8b95a7';
        c.fillText('+1', 28, yPad + 4);
        c.fillText('0', 36, zeroY + 4);
        c.fillText('-1', 28, chart.height - yPad);
      }};
      drawAxis();
      // Dark Bloomberg palette for the factor contribution bar chart — 8 distinct hues with good contrast on bg-overlay.
      const colors = ['#f59e0b', '#06b6d4', '#a78bfa', '#22c55e', '#ef4444', '#0ea5e9', '#fb923c', '#10b981'];
      panels.slice(0, 6).forEach((panel, idx) => {{
        c.strokeStyle = colors[idx % colors.length];
        c.fillStyle = colors[idx % colors.length];
        c.lineWidth = 2;
        c.beginPath();
        let started = false;
        panel.series.forEach((point, i) => {{
          const date = point.signal_date;
          const dateIdx = allDates.indexOf(date);
          const x = 50 + dateIdx * xStep;
          if (point.status !== 'ok' || point.ic == null) {{
            c.fillStyle = '#5a6478';
            c.fillRect(x - 1, yPad + usable * 0.5 - 1, 2, 2);
            c.fillStyle = colors[idx % colors.length];
            started = false;
            return;
          }}
          const y = yPad + usable * (1 - (Number(point.ic) + 1) / 2);
          if (!started) {{
            c.moveTo(x, y);
            started = true;
          }} else {{
            c.lineTo(x, y);
          }}
        }});
        c.stroke();
        c.fillText(panel.label, chart.width - 110, yPad + idx * 16 + 12);
      }});
    }}

    renderCoveragePanel(coveragePanel);
    renderForwardIc(forwardIcPanel);

    // Sub-tab switching: one IIFE handles every .sub-tabs nav on the page.
    // Scope: each .sub-tabs and its sibling .sub-content sections share a
    // .sub-tabs-container parent. Clicking a button toggles 'hidden' on
    // siblings and updates the bar's data-active attribute (used by CSS to
    // paint the active tab).
    (function() {{
      const containers = document.querySelectorAll('.sub-tabs-container');
      containers.forEach(container => {{
        const bar = container.querySelector('.sub-tabs');
        if (!bar) return;
        const contents = container.querySelectorAll('.sub-content');
        const activate = (target) => {{
          bar.dataset.active = target;
          contents.forEach(c => {{
            c.hidden = c.dataset.tab !== target;
          }});
        }};
        bar.querySelectorAll('.sub-tab-btn').forEach(btn => {{
          btn.addEventListener('click', () => activate(btn.dataset.target));
        }});
      }});
    }})();
  </script>
</body>
</html>
"""
    if mode == "fragment":
        html = _to_fragment(html, agent_id)
    dashboard_path.write_text(html, encoding="utf-8")
    return dashboard_path


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


def _to_fragment(page_html: str, agent_id: str) -> str:
    """Strip the outer HTML shell and rename element IDs so multiple fragments
    can be inlined into one container page without collisions.
    """

    import re as _re

    body_match = _re.search(r"<body>(.*)</body>", page_html, _re.S)
    inner = body_match.group(1) if body_match else page_html
    style_match = _re.search(r"<style>(.*?)</style>", page_html, _re.S)
    style_block = f"<style>{style_match.group(1)}</style>" if style_match else ""
    fragment_ids = [
        "navChart",
        "factorChart",
        "priceGrid",
        "coveragePanel",
        "forwardIcChart",
    ]
    for token in fragment_ids:
        scoped = f"{token}-{agent_id}"
        inner = inner.replace(f"id=\"{token}\"", f"id=\"{scoped}\"")
        inner = inner.replace(f"getElementById('{token}')", f"getElementById('{scoped}')")
    return (
        f"<section class=\"agent-dashboard\" data-agent=\"{agent_id}\">\n"
        f"{style_block}\n{inner}\n</section>\n"
    )


def dataframe_html(df: pd.DataFrame, columns: dict[str, str], empty_text: str) -> str:
    if df.empty:
        return f'<p class="empty">{empty_text}</p>'
    available = [col for col in columns if col in df.columns]
    display = df[available].rename(columns=columns)
    return display.to_html(index=False, classes="table", border=0)


def display_positions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "reason" in out.columns:
        out["reason"] = out["reason"].map(localize_reason)
    return out


def display_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "side" in out.columns:
        out["side"] = out["side"].map(lambda value: SIDE_LABELS.get(str(value), str(value)))
    if "reason" in out.columns:
        out["reason"] = out["reason"].map(localize_reason)
    return out


def display_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    numeric_cols = ["score", "pe", "pb", "roe", "gross_margin", "debt_ratio", "momentum_20", "momentum_60"]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(3)
    return out.sort_values(["account_id", "score"], ascending=[True, False])


def display_pending_orders(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "side" in out.columns:
        out["side"] = out["side"].map(lambda value: SIDE_LABELS.get(str(value), str(value)))
    if "status" in out.columns:
        out["status"] = out["status"].map(lambda value: STATUS_LABELS.get(str(value), str(value)))
    if "unfilled_reason" in out.columns:
        out["unfilled_reason"] = out["unfilled_reason"].map(localize_reason)
    for col in ["reference_price", "score"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(3)
    return out.sort_values(["account_id", "side", "score"], ascending=[True, True, False])


def read_signals(store: PortfolioStore) -> pd.DataFrame:
    path = store.data_dir / SIGNALS_FILE
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str})


def read_pending_orders(store: PortfolioStore) -> pd.DataFrame:
    path = store.data_dir / PENDING_FILE
    if not path.exists():
        return pd.DataFrame()
    batches = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for batch in batches:
        for order in batch.get("orders", []):
            rows.append(
                {
                    "signal_date": batch.get("signal_date"),
                    "execute_after": batch.get("execute_after"),
                    "account_id": batch.get("account_id"),
                    "scope": batch.get("scope"),
                    "status": order.get("status", "pending"),
                    "unfilled_reason": order.get("unfilled_reason", ""),
                    "attempts": order.get("attempts", 0),
                    **order,
                }
            )
    return pd.DataFrame(rows)


def pending_execution_hint(df: pd.DataFrame) -> str:
    if df.empty or "execute_after" not in df.columns:
        return "当前没有待执行订单。"
    dates = sorted({str(value) for value in df["execute_after"].dropna().tolist()})
    if not dates:
        return "当前待执行订单缺少执行日期。"
    return f"模拟成交将在 {', '.join(dates)} 或之后的日度任务执行；若该日期尚未有行情数据，订单会继续等待。"


def build_factor_summary(signals: pd.DataFrame) -> list[dict[str, Any]]:
    if signals.empty or "score_detail" not in signals.columns:
        return []
    totals: dict[str, list[float]] = {}
    for detail in signals["score_detail"].dropna().astype(str):
        for part in detail.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            number = safe_float(value)
            if number is not None:
                totals.setdefault(key.strip(), []).append(number)
    labels = {key: value for key, value in FACTOR_LABELS.items()}
    return sorted(
        [
            {
                "factor": factor,
                "label": labels.get(factor, factor),
                "value": sum(values) / len(values),
            }
            for factor, values in totals.items()
            if values
        ],
        key=lambda item: item["value"],
        reverse=True,
    )


def load_price_panels(store: PortfolioStore, signals: pd.DataFrame, limit: int = 6) -> list[dict[str, Any]]:
    if signals.empty:
        return []
    out: list[dict[str, Any]] = []
    selected = signals.copy()
    selected["score"] = pd.to_numeric(selected.get("score"), errors="coerce")
    selected = selected.sort_values(["account_id", "score"], ascending=[True, False]).head(limit)
    for _, row in selected.iterrows():
        code = str(row.get("code", "")).zfill(6)
        matches = sorted((store.data_dir / "cache").glob(f"history_{code}_*.csv"))
        if not matches:
            continue
        # Date columns can be YYYYMMDD ints; preserve as str so downstream
        # str(item.get("日期", ...)) doesn't render '20260525' as '20260525.0'.
        history = pd.read_csv(
            matches[-1],
            dtype={"日期": str, "date": str, "trade_date": str, "source": str},
        )
        if history.empty:
            continue
        rows = []
        for _, item in history.tail(60).iterrows():
            rows.append(
                {
                    "date": str(item.get("日期", item.get("date", ""))),
                    "open": safe_float(item.get("开盘", item.get("open"))),
                    "close": safe_float(item.get("收盘", item.get("close"))),
                    "high": safe_float(item.get("最高", item.get("high"))),
                    "low": safe_float(item.get("最低", item.get("low"))),
                }
            )
        out.append({"code": code, "name": str(row.get("name", "")), "account_id": str(row.get("account_id", "")), "rows": rows})
    return out


def display_health(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "source" in out.columns:
        out["source"] = out["source"].map(localize_source)
    if "status" in out.columns:
        out["status"] = out["status"].map(lambda value: STATUS_LABELS.get(str(value), str(value)))
    if "message" in out.columns:
        out["message"] = out["message"].map(localize_message)
    if "rows" in out.columns:
        out["rows"] = out["rows"].map(format_rows)
    return out


def localize_reason(value: Any) -> str:
    text = str(value or "")
    if text in REASON_LABELS:
        return REASON_LABELS[text]
    parts = []
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        if item in REASON_LABELS:
            parts.append(REASON_LABELS[item])
            continue
        if ":" in item:
            key, score = item.split(":", 1)
            parts.append(f"{FACTOR_LABELS.get(key, key)} 加分 {score}")
            continue
        parts.append(item)
    return "；".join(parts)


def localize_source(value: Any) -> str:
    text = str(value or "")
    for prefix, label in sorted(SOURCE_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        if text.startswith(prefix):
            suffix = text[len(prefix) :].strip("_")
            return f"{label} {suffix}".strip()
    return text


def localize_message(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("attempt="):
        attempt, _, detail = text.partition(":")
        prefix = attempt.replace("attempt=", "第 ", 1) + " 次尝试"
        return f"{prefix}：{simplify_error(detail.strip())}" if detail else prefix
    if text.startswith("using cache"):
        return text.replace("using cache", "使用本地缓存", 1)
    if text == "all realtime spot sources failed":
        return "全部实时行情源失败，已尝试缓存或降级数据"
    if text == "no constituents":
        return "股票池成分为空"
    simplified = simplify_error(text)
    if simplified != text:
        return simplified
    return text


def simplify_error(text: str) -> str:
    if not text:
        return ""
    if "RemoteDisconnected" in text:
        return "远端主动断开连接，已触发重试或降级"
    if "ProxyError" in text or "HTTPSConnectionPool" in text or "Max retries exceeded" in text:
        return "网络连接失败，已触发重试或降级"
    if "JSONDecodeError" in text:
        return "数据源返回内容无法解析，已触发重试或降级"
    return text


def format_rows(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return str(int(number))


def format_ratio(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.2f}"


def format_bps(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.1f}"


def format_duration_ms(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    if number < 1000:
        return f"{int(number)} ms"
    return f"{number / 1000:.1f} s"


def read_coverage(store: PortfolioStore) -> pd.DataFrame:
    df = store.read_factor_coverage()
    if df.empty:
        return pd.DataFrame(columns=FACTOR_COVERAGE_COLUMNS)
    return df


def read_forward_ic_df(store: PortfolioStore) -> pd.DataFrame:
    df = store.read_forward_ic()
    if df.empty:
        return pd.DataFrame(columns=FORWARD_IC_COLUMNS)
    return df


def build_coverage_panel(coverage: pd.DataFrame, low_threshold: float = 0.5, max_weeks: int = 12) -> list[dict[str, Any]]:
    if coverage.empty:
        return []
    coverage = coverage.copy()
    coverage["signal_date"] = pd.to_datetime(coverage["signal_date"], errors="coerce")
    coverage = coverage.dropna(subset=["signal_date"]).sort_values("signal_date")
    recent_dates = sorted(coverage["signal_date"].unique())[-max_weeks:]
    rows = coverage[coverage["signal_date"].isin(recent_dates)]
    panel: list[dict[str, Any]] = []
    for (factor,), group in rows.groupby(["factor"]):
        items = []
        for signal_date, week_rows in group.groupby("signal_date"):
            mean_cov = float(week_rows["coverage_pct"].mean())
            items.append(
                {
                    "signal_date": pd.to_datetime(signal_date).date().isoformat(),
                    "coverage_pct": round(mean_cov, 4),
                    "low": mean_cov < low_threshold,
                }
            )
        panel.append({"factor": str(factor), "label": FACTOR_LABELS.get(str(factor), str(factor)), "items": items})
    panel.sort(key=lambda item: item["label"])
    return panel


def build_forward_ic_panel(forward_ic: pd.DataFrame, max_weeks: int = 12) -> list[dict[str, Any]]:
    if forward_ic.empty:
        return []
    forward_ic = forward_ic.copy()
    forward_ic["signal_date"] = pd.to_datetime(forward_ic["signal_date"], errors="coerce")
    forward_ic = forward_ic.dropna(subset=["signal_date"]).sort_values("signal_date")
    recent_dates = sorted(forward_ic["signal_date"].unique())[-max_weeks:]
    rows = forward_ic[forward_ic["signal_date"].isin(recent_dates)]
    panel: list[dict[str, Any]] = []
    for factor, group in rows.groupby("factor"):
        series = []
        for signal_date, sub in group.groupby("signal_date"):
            status_values = sub["ic_status"].astype(str).tolist()
            if "ok" in status_values:
                ok_row = sub[sub["ic_status"] == "ok"].iloc[0]
                series.append(
                    {
                        "signal_date": pd.to_datetime(signal_date).date().isoformat(),
                        "ic": round(float(ok_row["ic"]), 4) if pd.notna(ok_row["ic"]) else None,
                        "status": "ok",
                    }
                )
            else:
                series.append(
                    {
                        "signal_date": pd.to_datetime(signal_date).date().isoformat(),
                        "ic": None,
                        "status": "insufficient_history",
                    }
                )
        panel.append({"factor": str(factor), "label": FACTOR_LABELS.get(str(factor), str(factor)), "series": series})
    panel.sort(key=lambda item: item["label"])
    return panel


def render_performance_cards(summary: dict[str, Any]) -> str:
    accounts = summary.get("accounts") or {}
    if not accounts:
        return '<p class="empty">绩效数据不足。请等待至少 2 个净值日。</p>'
    cards: list[str] = []
    for account_id, item in accounts.items():
        cards.append(
            f"""
            <section class="metric-card metric-deep">
              <div class="card-label">{account_id}</div>
              <p class="metric-row" title="日收益年化均值 × 252">年化收益 <strong>{format_pct(item.get('annualized_return'))}</strong></p>
              <p class="metric-row" title="日收益样本标准差 × √252">年化波动 <strong>{format_pct(item.get('annualized_volatility'))}</strong></p>
              <p class="metric-row" title="(年化收益 − rf) / 年化波动">Sharpe <strong>{format_ratio(item.get('sharpe_ratio'))}</strong></p>
              <p class="metric-row" title="(年化收益 − rf) / 年化下行波动">Sortino <strong>{format_ratio(item.get('sortino_ratio'))}</strong></p>
              <p class="metric-row" title="(组合 − 基准) 复利差">累计超额 <strong>{format_pct(item.get('cumulative_excess_return'))}</strong></p>
              <p class="metric-row" title="日超额收益均值 × 252">年化超额 <strong>{format_pct(item.get('annualized_excess_return'))}</strong></p>
              <p class="metric-row" title="日超额样本标准差 × √252">跟踪误差 <strong>{format_pct(item.get('tracking_error'))}</strong></p>
              <p class="metric-row" title="年化超额 / 跟踪误差">信息比率 <strong>{format_ratio(item.get('information_ratio'))}</strong></p>
              <p class="metric-row" title="单周双边换手 = (买额 + 卖额) / 期初组合市值">换手率(周) <strong>{format_pct(item.get('weekly_turnover_avg'))}</strong></p>
              <p class="metric-row" title="累计成本 / 累计成交金额 × 10000">成本 <strong>{format_bps(item.get('cost_bps'))} bps</strong></p>
              <p class="metric-row" title="FIFO 配对完成 round-trip 中收益为正的比例">Win Rate <strong>{format_pct(item.get('round_trip_win_rate'))}</strong></p>
              <p class="metric-row" title="最大回撤持续日数">最大回撤天数 <strong>{format_days(item.get('max_drawdown_days'))}</strong></p>
            </section>
            """
        )
    return "".join(cards)


def render_runs_table(runs: list[dict[str, Any]], limit: int = 10) -> str:
    if not runs:
        return '<p class="empty">尚无运行账本。第一次跑 init 或 run-weekly 后会出现。</p>'
    rows = runs[:limit]
    parts = [
        '<table class="table"><thead><tr><th>run_id</th><th>命令</th><th>状态</th><th>耗时</th><th>config_hash</th><th>code_version</th><th>开始</th></tr></thead><tbody>'
    ]
    for row in rows:
        status = str(row.get("status") or "")
        tag_class = "tag-success" if status == "success" else "tag-failed" if status == "failed" else "tag-running"
        parts.append(
            f"<tr><td>{row.get('run_id', '-') }</td><td>{row.get('command', '-')}</td>"
            f"<td><span class=\"{tag_class}\">{STATUS_LABELS.get(status, status) or '-'}</span></td>"
            f"<td>{format_duration_ms(row.get('duration_ms'))}</td><td>{row.get('config_hash') or '-'}</td>"
            f"<td>{row.get('code_version') or '-'}</td><td>{row.get('started_at') or '-'}</td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def format_days(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{int(number)} d"


MAX_PANEL_CONTENT_BYTES = 16 * 1024


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _truncate(text: str, limit: int = MAX_PANEL_CONTENT_BYTES) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n…(truncated)"


def read_agent_evolutions(data_dir: str | Path) -> list[dict[str, Any]]:
    """Return monthly evolutions from the new LLM-direct flow.

    Reads ``data/<agent>/evolution_diff/<YYYY-MM>.json`` (machine-readable
    diff) and stitches in the matching ``evolution_log/<YYYY-MM>.md`` text
    if present. Sorted by month descending.

    The new sources, written by :mod:`evolution_writer`, replace the old
    ``data/<agent>/proposals/*-strategy.json`` source.
    """

    data_root = Path(data_dir)
    diff_dir = data_root / "evolution_diff"
    log_dir = data_root / "evolution_log"
    if not diff_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(diff_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        month = path.stem
        payload.setdefault("month", month)
        payload.setdefault("_source_path", str(path))
        log_path = log_dir / f"{month}.md"
        if log_path.exists():
            try:
                payload["_log_text"] = log_path.read_text(encoding="utf-8")
            except OSError:
                payload["_log_text"] = ""
            payload["_log_path"] = str(log_path)
        results.append(payload)
    return results


# Backwards-compat alias for callers that still use the old name (e.g.
# tests that pre-date this rewrite). New code should call
# :func:`read_agent_evolutions`.
def read_agent_proposals(data_dir: str | Path) -> list[dict[str, Any]]:
    return read_agent_evolutions(data_dir)


def _load_leaderboard_by_month(leaderboard_path: Path) -> dict[str, dict[str, Any]]:
    if not leaderboard_path.exists():
        return {}
    try:
        # month is "YYYY-MM" string — defensive to ensure no int coercion
        df = pd.read_csv(leaderboard_path, dtype={"month": str})
    except Exception:  # noqa: BLE001
        return {}
    if df.empty or "month" not in df.columns:
        return {}
    return {str(row["month"]): row.to_dict() for _, row in df.iterrows()}


def _next_month(month: str) -> str | None:
    try:
        year, mo = month.split("-")
        year_i = int(year)
        mo_i = int(mo)
    except (ValueError, AttributeError):
        return None
    if mo_i == 12:
        year_i += 1
        mo_i = 1
    else:
        mo_i += 1
    return f"{year_i:04d}-{mo_i:02d}"


def _agent_from_data_dir(data_dir: Path) -> str:
    return data_dir.name or "agent"


def _leaderboard_return_cell(row: dict[str, Any] | None, agent: str) -> str:
    if not row:
        return "-"
    column = f"{agent}_return"
    value = safe_float(row.get(column))
    if value is None:
        return "-"
    return format_pct(value)


def _hash_drift_flag(agent: str, month: str, data_dir: Path, to_hash: str) -> bool:
    """Return True when the live overlay hash diverges from the recorded ``to_hash``.

    Replaces the old proposal-hash drift check. The new contract is: if the
    `config_evolution.csv` row for this month says we evolved to hash X,
    but the live `configs/agents/<agent>.yaml` now hashes to Y ≠ X, the
    timeline shows a red highlight because either someone touched the
    overlay outside the slash command, or a later evolution overwrote
    this row without removing it.
    """

    if not to_hash:
        return False
    repo_root = data_dir.parent.parent  # data/<agent> → repo_root
    try:
        from . import competition
        from .config import config_hash
        actual = config_hash(competition.load(agent, repo_root=repo_root))
    except Exception:  # noqa: BLE001
        return False
    return actual != to_hash


def render_strategy_evolution_panel(
    data_dir: str | Path,
    leaderboard_path: str | Path | None = None,
) -> str:
    """Render the per-agent strategy-evolution timeline panel.

    Reads the new ``evolution_diff/*.json`` + ``evolution_log/*.md`` sources
    written by :mod:`evolution_writer`, replacing the deleted
    ``proposals/*-strategy.json`` + ``decisions/*.json`` chain.
    """

    evolutions = read_agent_evolutions(data_dir)
    agent = _agent_from_data_dir(Path(data_dir))
    if not evolutions:
        return (
            '<p class="empty">尚未生成策略演化记录。月度 <code>/monthly-strategy '
            f"{agent}</code> 跑完后会出现。</p>"
        )

    leaderboard_lookup: dict[str, dict[str, Any]] = {}
    if leaderboard_path is not None:
        leaderboard_lookup = _load_leaderboard_by_month(Path(leaderboard_path))

    rows_html: list[str] = []
    for evolution in evolutions:
        month = str(evolution.get("month", "-"))
        diff = evolution.get("diff") if isinstance(evolution.get("diff"), dict) else {}
        no_change = not diff
        row_class = "proposal-no-change" if no_change else "proposal-change"
        status_text = "本月维持" if no_change else "已演化"
        from_hash = str(evolution.get("from_config_hash") or "-")
        to_hash = str(evolution.get("to_config_hash") or "-")
        drift = _hash_drift_flag(agent, month, Path(data_dir), to_hash)
        hash_text = f"{from_hash[:12]} → {to_hash[:12]}"
        if drift:
            hash_text += " · overlay 已变"
        hash_class = " proposal-drift" if drift else ""
        diff_summary = _summarise_diff_keys(diff)
        diff_summary_html = (
            ", ".join(_escape_html(item) for item in diff_summary) if diff_summary else "（无变化）"
        )
        log_text = str(evolution.get("_log_text") or "").strip()
        log_excerpt = _truncate(log_text, limit=400) if log_text else "-"
        log_excerpt_html = _escape_html(log_excerpt) if log_excerpt != "-" else "-"
        log_path_rel = evolution.get("_log_path")
        log_link_html = (
            f'<a href="{_escape_html(str(log_path_rel))}">阅读</a>'
            if log_path_rel
            else "-"
        )
        current_row = leaderboard_lookup.get(month)
        next_row = leaderboard_lookup.get(_next_month(month) or "")
        rows_html.append(
            f'<tr class="{row_class}{hash_class}">'
            f"<td>{_escape_html(month)}</td>"
            f"<td>{status_text}</td>"
            f'<td class="hash-cell">{_escape_html(hash_text)}</td>'
            f"<td>{diff_summary_html}</td>"
            f"<td>{log_excerpt_html}</td>"
            f"<td>{log_link_html}</td>"
            f"<td>{_leaderboard_return_cell(current_row, agent)}</td>"
            f"<td>{_leaderboard_return_cell(next_row, agent)}</td>"
            "</tr>"
        )
    return (
        '<table class="table strategy-evolution"><thead>'
        "<tr><th>月份</th><th>状态</th><th>from → to hash</th><th>diff 摘要</th>"
        "<th>思考摘要</th><th>evolution_log</th><th>当月收益</th><th>次月收益</th></tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )


def _summarise_diff_keys(diff: Any, limit: int = 6) -> list[str]:
    """Return at most ``limit`` short labels summarising changed keys."""

    if not isinstance(diff, dict):
        return []
    keys = list(diff.keys())
    if not keys:
        return []
    if len(keys) <= limit:
        return keys
    return keys[: limit - 1] + [f"…+{len(keys) - limit + 1}"]


def render_latest_briefing_panel(data_dir: str | Path) -> str:
    """Render the latest weekly and (if present) latest monthly briefing as
    collapsible details blocks.
    """

    briefings_dir = Path(data_dir) / "notes" / "briefings"
    if not briefings_dir.exists():
        return (
            '<p class="empty">ECS 还没生成 briefing。下次 <code>run-weekly --agent '
            f"{_agent_from_data_dir(Path(data_dir))}</code> 跑完会出现。</p>"
        )
    weekly = sorted(briefings_dir.glob("*-weekly.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    monthly = sorted(briefings_dir.glob("*-monthly.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    blocks: list[str] = []
    for path, label in ((weekly[:1], "周度"), (monthly[:1], "月度")):
        for entry in path:
            try:
                text = entry.read_text(encoding="utf-8")
            except OSError:
                continue
            safe = _escape_html(_truncate(text))
            blocks.append(
                f"<details><summary>{label} · {entry.name}</summary>"
                f"<pre style=\"white-space:pre-wrap;font-family:inherit\">{safe}</pre>"
                "</details>"
            )
    if not blocks:
        return (
            '<p class="empty">briefings 目录存在但暂无内容。运行 '
            f"<code>agent-prepare-weekly --agent {_agent_from_data_dir(Path(data_dir))}</code> 后再看。</p>"
        )
    return "\n".join(blocks)


def render_agent_notes_panel(data_dir: str | Path, limit: int = 5) -> str:
    """Render the most recent ``data/<agent>/notes/*.md`` files (excluding
    ``notes/briefings/`` and ``proposals/``) as collapsible ``<details>``.

    Returns an empty-state placeholder when the directory is missing or
    contains no eligible files.
    """

    notes_dir = Path(data_dir) / "notes"
    if not notes_dir.exists():
        return '<p class="empty">尚无 agent 笔记。跑过 /weekly-review 后会出现。</p>'
    candidates = sorted(
        [path for path in notes_dir.glob("*.md") if path.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    if not candidates:
        return '<p class="empty">尚无 agent 笔记。跑过 /weekly-review 后会出现。</p>'
    parts = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        size_kb = max(1, path.stat().st_size // 1024)
        safe_text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        parts.append(
            f"<details><summary>{path.name} · {size_kb} KB</summary>"
            f"<pre style=\"white-space:pre-wrap;font-family:inherit\">{safe_text}</pre>"
            f"</details>"
        )
    return "\n".join(parts)


def read_health(store: PortfolioStore) -> pd.DataFrame:
    path = store.data_dir / "data_health.json"
    if not path.exists():
        return pd.DataFrame(columns=list(HEALTH_COLUMNS))
    try:
        return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return pd.DataFrame(columns=list(HEALTH_COLUMNS))


# ---------------------------------------------------------------------------
# Dark Bloomberg theme CSS for the pro view.
# Class names preserved so renderers (above) emit unchanged markup.
# Color/spacing/typography pull tokens from _dashboard_assets.BASE_CSS.

_PRO_CSS = """
main { padding: var(--space-lg) var(--space-xl) var(--space-xl); max-width: 1600px; margin: 0 auto; }
.page-header { padding: var(--space-lg) var(--space-xl); background: var(--bg-elevated); border-bottom: 1px solid var(--border-subtle); }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 600; color: var(--text-primary); letter-spacing: 0.02em; }
.page-header .subhead { color: var(--text-tertiary); font-size: 12px; margin-top: 4px; font-family: var(--font-mono); }
h2 { margin: var(--space-xl) 0 var(--space-md); font-size: 14px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em; }

/* KPI grids */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: var(--space-md); }
.metric-card { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-md) var(--space-lg); }
.card-label { margin-bottom: 6px; font-size: 11px; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.08em; }
.metric { font-size: 28px; font-weight: 600; font-family: var(--font-mono); color: var(--text-primary); font-variant-numeric: tabular-nums; line-height: 1.15; }
.metric-card p { margin: var(--space-sm) 0; color: var(--text-secondary); font-size: 12px; font-family: var(--font-mono); }
.tag { display: inline-flex; align-items: center; height: 22px; padding: 0 var(--space-sm); border-radius: var(--radius-sm); background: var(--bg-overlay); color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }

/* Panels (containers for charts + tables) */
.panel { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: var(--space-md); overflow: auto; }
.split { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr); gap: var(--space-md); }
.panel-row { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: var(--space-md); }
.price-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: var(--space-sm); }
.mini-chart { min-height: 220px; }
.chart-title { margin: 0 0 var(--space-sm); font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }

/* Tables */
.table { width: 100%; border-collapse: collapse; font-size: 12px; }
.table th, .table td { border-bottom: 1px solid var(--border-subtle); padding: var(--space-sm) var(--space-md); text-align: left; white-space: nowrap; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.table th { background: var(--bg-overlay); color: var(--text-tertiary); font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; }
.table td { color: var(--text-primary); max-width: 360px; overflow: hidden; text-overflow: ellipsis; }

/* Empty / hint / warning */
.empty { margin: 0; color: var(--text-tertiary); font-size: 13px; }
.hint { color: var(--text-tertiary); font-size: 11px; margin-top: var(--space-sm); font-family: var(--font-mono); }
.warning { color: var(--accent); }

/* Canvas charts on dark backgrounds */
canvas { width: 100%; height: 340px; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: var(--bg-overlay); }
.mini-chart canvas { height: 180px; }

/* Deep metric breakdown */
.metric-deep { font-size: 12px; }
.metric-deep .metric-row { margin: var(--space-xs) 0; color: var(--text-secondary); display: flex; justify-content: space-between; font-family: var(--font-mono); }
.metric-deep .metric-row strong { color: var(--text-primary); font-weight: 600; }

/* Heatmap (factor coverage) */
.heatmap { width: 100%; border-collapse: collapse; font-size: 11px; font-family: var(--font-mono); }
.heatmap th, .heatmap td { border: 1px solid var(--border-subtle); padding: 4px 6px; text-align: center; color: var(--text-secondary); }
.heatmap th { background: var(--bg-overlay); color: var(--text-tertiary); font-weight: 500; text-transform: uppercase; letter-spacing: 0.06em; }
.heatmap td.low { background: var(--pos-bg); color: var(--pos); font-weight: 600; }

/* Status tags */
.tag-success { display: inline-flex; align-items: center; height: 20px; padding: 0 var(--space-sm); border-radius: var(--radius-sm); background: rgba(34, 197, 94, 0.12); color: var(--neg); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono); }
.tag-failed { display: inline-flex; align-items: center; height: 20px; padding: 0 var(--space-sm); border-radius: var(--radius-sm); background: rgba(239, 68, 68, 0.12); color: var(--pos); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono); }
.tag-running { display: inline-flex; align-items: center; height: 20px; padding: 0 var(--space-sm); border-radius: var(--radius-sm); background: rgba(245, 158, 11, 0.12); color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono); }

/* Strategy evolution table */
table.strategy-evolution { font-size: 11px; }
table.strategy-evolution td { vertical-align: top; max-width: 320px; white-space: normal; color: var(--text-primary); }
table.strategy-evolution tr.proposal-no-change td { color: var(--text-tertiary); }
table.strategy-evolution tr.proposal-drift td.decision-cell { color: var(--pos); font-weight: 600; }

/* Code spans for command names */
code { background: var(--bg-overlay); color: var(--accent); padding: 1px 5px; border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 11px; }

/* Sub-tab navigation (二级 Tab — 结果 / 洞察 / 健康 / 演化) */
.sub-tabs-container { margin: var(--space-lg) 0 0; }
.sub-tabs { display: flex; gap: var(--space-xs); border-bottom: 1px solid var(--border-strong); padding-bottom: 0; margin-bottom: 0; }
.sub-tab-btn {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  padding: var(--space-sm) var(--space-md) calc(var(--space-sm) + 1px);
  font-size: 12px;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 500;
  font-family: var(--font-sans);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.12s, border-color 0.12s;
}
.sub-tab-btn:hover { color: var(--text-primary); }
.sub-tabs[data-active="results"] .sub-tab-btn[data-target="results"],
.sub-tabs[data-active="insights"] .sub-tab-btn[data-target="insights"],
.sub-tabs[data-active="health"] .sub-tab-btn[data-target="health"],
.sub-tabs[data-active="evolution"] .sub-tab-btn[data-target="evolution"] {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
.sub-content { padding-top: var(--space-md); }
.sub-content[hidden] { display: none; }

/* Responsive */
@media (max-width: 900px) {
  .split { grid-template-columns: 1fr; }
  .panel-row { grid-template-columns: 1fr; }
  .sub-tabs { flex-wrap: wrap; }
  main { padding: var(--space-md); }
}
"""
