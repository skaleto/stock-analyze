from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .store import PERFORMANCE_FILE, PENDING_FILE, SIGNALS_FILE, PortfolioStore
from .utils import ensure_dirs, format_money, format_pct, safe_float, today_str, write_json


POSITION_COLUMNS = {
    "account_id": "账户",
    "code": "代码",
    "name": "名称",
    "shares": "持股数",
    "avg_cost": "平均成本",
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
}

REASON_LABELS = {
    "not_selected": "本期未入选，调出组合",
    "history_missing": "历史行情缺失",
    "price_missing": "价格缺失",
    "financial_fetch_failed": "财务数据获取失败",
    "pe_missing": "PE 缺失",
    "pb_missing": "PB 缺失",
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
    "spot_eastmoney": "东方财富实时行情",
    "spot_sina": "新浪实时行情",
    "spot": "实时行情",
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
    "baostock_login": "Baostock 登录",
    "universe": "股票池",
    "basic": "个股基础信息",
}


def compute_performance(config: dict[str, Any], store: PortfolioStore) -> dict[str, Any]:
    nav = store.read_nav()
    summary: dict[str, Any] = {
        "strategy_id": config.get("strategy_id"),
        "generated_at": today_str(),
        "accounts": {},
        "objective": config.get("objective", {}),
    }
    if nav.empty:
        write_json(store.data_dir / PERFORMANCE_FILE, summary)
        return summary

    for account_id, group in nav.groupby("account_id"):
        group = group.sort_values("date")
        initial = safe_float(group.iloc[0]["total_value"]) or 0
        latest = safe_float(group.iloc[-1]["total_value"]) or 0
        cumulative = (latest / initial - 1) if initial else None
        rolling_peak = group["total_value"].cummax()
        drawdowns = group["total_value"] / rolling_peak - 1
        max_drawdown = float(drawdowns.min()) if not drawdowns.empty else None
        summary["accounts"][account_id] = {
            "start_date": str(group.iloc[0]["date"]),
            "latest_date": str(group.iloc[-1]["date"]),
            "initial_value": round(initial, 2),
            "latest_value": round(latest, 2),
            "cumulative_return": cumulative,
            "max_drawdown": max_drawdown,
            "nav_points": int(len(group)),
        }
    write_json(store.data_dir / PERFORMANCE_FILE, summary)
    return summary


def generate_weekly_report(config: dict[str, Any], store: PortfolioStore, reports_dir: str | Path) -> Path:
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
        "## 绩效概览",
        "",
        "| 账户 | 最新资产 | 累计收益 | 最大回撤 | 净值点数 |",
        "|---|---:|---:|---:|---:|",
    ]
    accounts = summary.get("accounts", {})
    if not accounts:
        lines.append("| 暂无绩效数据 | - | - | - | 0 |")
    for account_id, item in accounts.items():
        lines.append(
            f"| {account_id} | {format_money(item.get('latest_value'))} | "
            f"{format_pct(item.get('cumulative_return'))} | {format_pct(item.get('max_drawdown'))} | "
            f"{item.get('nav_points', 0)} |"
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


def generate_dashboard(config: dict[str, Any], store: PortfolioStore, reports_dir: str | Path) -> Path:
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
    dashboard_path = Path(reports_dir) / "dashboard.html"

    nav_json = nav.to_json(orient="records", force_ascii=False) if not nav.empty else "[]"
    price_json = json.dumps(price_panels, ensure_ascii=False)
    factor_json = json.dumps(factor_summary, ensure_ascii=False)
    positions_html = dataframe_html(display_positions(positions), POSITION_COLUMNS, "暂无持仓。请先运行周度信号，等待下一交易日模拟成交后再观察。")
    trades_html = dataframe_html(display_trades(trades.tail(30)), TRADE_COLUMNS, "暂无交易。周度任务会先生成待执行订单，下一交易日由日度任务模拟成交。")
    health_html = dataframe_html(display_health(health.tail(40)), HEALTH_COLUMNS, "暂无数据源状态。运行周度或日度任务后会显示接口、缓存和降级情况。")
    signals_html = dataframe_html(display_signals(signals), SIGNAL_COLUMNS, "暂无选股信号。请先运行 run-weekly。")
    pending_html = dataframe_html(display_pending_orders(pending_orders), PENDING_COLUMNS, "暂无待执行订单。请先运行 run-weekly 生成调仓计划。")
    execution_hint = pending_execution_hint(pending_orders)
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

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>模拟交易仪表盘</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #667085;
      --line: #d9e0e8;
      --panel: #ffffff;
      --bg: #f4f6f8;
      --blue: #2457a7;
      --green: #147d64;
      --amber: #b76e00;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--ink); }}
    header {{ padding: 24px 32px 20px; background: #0f253f; color: white; border-bottom: 4px solid #2b7bbb; }}
    main {{ padding: 24px 32px 48px; max-width: 1440px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; letter-spacing: 0; }}
    .subhead {{ color: #c9d7e8; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }}
    .metric-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }}
    .card-label {{ margin-bottom: 10px; font-size: 14px; color: var(--muted); }}
    .metric {{ font-size: 30px; font-weight: 750; }}
    .metric-card p {{ margin: 10px 0 12px; color: var(--muted); }}
    .tag {{ display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 6px; background: #e8f3ef; color: var(--green); font-size: 12px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; overflow: auto; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }}
    .split {{ display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr); gap: 16px; }}
    .price-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }}
    .mini-chart {{ min-height: 230px; }}
    .chart-title {{ margin: 0 0 8px; font-size: 14px; font-weight: 650; color: #344054; }}
    .table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .table th, .table td {{ border-bottom: 1px solid #edf0f3; padding: 9px 10px; text-align: left; white-space: nowrap; }}
    .table th {{ background: #f1f4f8; color: #344054; font-weight: 650; }}
    .table td {{ max-width: 360px; overflow: hidden; text-overflow: ellipsis; }}
    .empty {{ margin: 0; color: var(--muted); }}
    canvas {{ width: 100%; height: 340px; border: 1px solid #edf0f3; border-radius: 6px; background: #fff; }}
    .mini-chart canvas {{ height: 180px; }}
    .hint {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
    .warning {{ color: var(--amber); }}
    @media (max-width: 900px) {{ .split {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Stock Analyze Simulation</h1>
    <div class="subhead">策略版本 {config.get('strategy_id')} · 生成日期 {summary.get('generated_at')} · 仅模拟交易，不构成投资建议</div>
  </header>
  <main>
    <section class="grid">{''.join(cards) or '<p class="empty">暂无净值数据。请先运行初始化和周度任务。</p>'}</section>
    <h2>净值曲线</h2>
    <div class="panel">
      <canvas id="navChart" width="1200" height="340"></canvas>
      <div class="hint">曲线基于模拟账户净值生成；如果只有一个净值点，图上会显示为水平参考线。</div>
    </div>
    <section class="split">
      <div>
        <h2>本期选股信号</h2>
        <div class="panel">{signals_html}</div>
      </div>
      <div>
        <h2>因子贡献均值</h2>
        <div class="panel">
          <canvas id="factorChart" width="680" height="340"></canvas>
          <div class="hint">从 `score_detail` 解析各因子对入选股票的平均贡献。</div>
        </div>
      </div>
    </section>
    <h2>待执行模拟订单</h2>
    <div class="panel">
      {pending_html}
      <div class="hint warning">{execution_hint}</div>
    </div>
    <h2>候选股价格走势</h2>
    <div class="price-grid" id="priceGrid"></div>
    <h2>当前持仓</h2>
    <div class="panel">{positions_html}</div>
    <h2>近期交易</h2>
    <div class="panel">{trades_html}</div>
    <h2>数据源状态</h2>
    <div class="panel">{health_html}</div>
  </main>
  <script>
    const nav = {nav_json};
    const factorSummary = {factor_json};
    const pricePanels = {price_json};
    const canvas = document.getElementById('navChart');
    const ctx = canvas.getContext('2d');
    function drawChart(rows) {{
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = '13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      if (!rows.length) {{
        ctx.fillStyle = '#667085';
        ctx.fillText('暂无净值数据', 24, 40);
        return;
      }}
      const accounts = [...new Set(rows.map(r => r.account_id))];
      const colors = ['#2457a7', '#147d64', '#b76e00'];
      const values = rows.map(r => Number(r.total_value)).filter(Number.isFinite);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const pad = 42;
      ctx.strokeStyle = '#d8dee8';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad, pad);
      ctx.lineTo(pad, canvas.height - pad);
      ctx.lineTo(canvas.width - pad, canvas.height - pad);
      ctx.stroke();
      ctx.fillStyle = '#667085';
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
        c.fillStyle = '#667085';
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
        c.fillStyle = '#344054';
        c.fillText(item.label, 12, y + 18);
        c.fillStyle = '#2457a7';
        c.fillRect(left, y, w, 18);
        c.fillStyle = '#667085';
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
        c.fillStyle = '#667085';
        c.fillText('无价格数据', 20, 40);
        return;
      }}
      const min = Math.min(...prices);
      const max = Math.max(...prices);
      const pad = 24;
      const span = Math.max(max - min, 1);
      const xStep = (chart.width - pad * 2) / Math.max(rows.length - 1, 1);
      c.strokeStyle = '#e1e7ef';
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
        c.strokeStyle = up ? '#b42318' : '#147d64';
        c.fillStyle = up ? '#f04438' : '#12b76a';
        c.beginPath();
        c.moveTo(x, yHigh);
        c.lineTo(x, yLow);
        c.stroke();
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(Math.abs(yClose - yOpen), 2);
        c.fillRect(x - 2, bodyTop, 4, bodyH);
      }});
      c.fillStyle = '#667085';
      c.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      c.fillText(max.toFixed(2), 4, pad + 4);
      c.fillText(min.toFixed(2), 4, chart.height - pad);
    }}

    drawFactorChart(factorSummary);
    drawPricePanels(pricePanels);
  </script>
</body>
</html>
"""
    dashboard_path.write_text(html, encoding="utf-8")
    return dashboard_path


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
        history = pd.read_csv(matches[-1])
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


def read_health(store: PortfolioStore) -> pd.DataFrame:
    path = store.data_dir / "data_health.json"
    if not path.exists():
        return pd.DataFrame(columns=list(HEALTH_COLUMNS))
    try:
        return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return pd.DataFrame(columns=list(HEALTH_COLUMNS))
