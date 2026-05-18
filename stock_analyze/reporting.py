from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .store import PERFORMANCE_FILE, PortfolioStore
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
    dashboard_path = Path(reports_dir) / "dashboard.html"

    nav_json = nav.to_json(orient="records", force_ascii=False) if not nav.empty else "[]"
    positions_html = dataframe_html(display_positions(positions), POSITION_COLUMNS, "暂无持仓。请先运行周度信号，等待下一交易日模拟成交后再观察。")
    trades_html = dataframe_html(display_trades(trades.tail(30)), TRADE_COLUMNS, "暂无交易。周度任务会先生成待执行订单，下一交易日由日度任务模拟成交。")
    health_html = dataframe_html(display_health(health.tail(40)), HEALTH_COLUMNS, "暂无数据源状态。运行周度或日度任务后会显示接口、缓存和降级情况。")
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
    .table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .table th, .table td {{ border-bottom: 1px solid #edf0f3; padding: 9px 10px; text-align: left; white-space: nowrap; }}
    .table th {{ background: #f1f4f8; color: #344054; font-weight: 650; }}
    .table td {{ max-width: 360px; overflow: hidden; text-overflow: ellipsis; }}
    .empty {{ margin: 0; color: var(--muted); }}
    canvas {{ width: 100%; height: 340px; border: 1px solid #edf0f3; border-radius: 6px; background: #fff; }}
    .hint {{ color: var(--muted); font-size: 13px; margin-top: 8px; }}
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
    <h2>当前持仓</h2>
    <div class="panel">{positions_html}</div>
    <h2>近期交易</h2>
    <div class="panel">{trades_html}</div>
    <h2>数据源状态</h2>
    <div class="panel">{health_html}</div>
  </main>
  <script>
    const nav = {nav_json};
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
