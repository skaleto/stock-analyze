from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .store import PERFORMANCE_FILE, PortfolioStore
from .utils import ensure_dirs, format_money, format_pct, safe_float, today_str, write_json


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
    path = Path(reports_dir) / "weekly_report.md"

    lines = [
        f"# Weekly Simulation Report - {today_str()}",
        "",
        "This report is generated from simulated trading data only. It is not investment advice.",
        "",
        "## Performance",
        "",
        "| Account | Latest Value | Cumulative Return | Max Drawdown | Points |",
        "|---|---:|---:|---:|---:|",
    ]
    for account_id, item in summary.get("accounts", {}).items():
        lines.append(
            f"| {account_id} | {format_money(item.get('latest_value'))} | "
            f"{format_pct(item.get('cumulative_return'))} | {format_pct(item.get('max_drawdown'))} | "
            f"{item.get('nav_points', 0)} |"
        )

    lines.extend(["", "## Current Positions", ""])
    if positions.empty:
        lines.append("No positions.")
    else:
        lines.extend(["| Account | Code | Name | Shares | Avg Cost | Last Price | Market Value | PnL |", "|---|---|---|---:|---:|---:|---:|---:|"])
        for _, row in positions.iterrows():
            lines.append(
                f"| {row.get('account_id')} | {row.get('code')} | {row.get('name')} | "
                f"{row.get('shares')} | {row.get('avg_cost')} | {row.get('last_price')} | "
                f"{format_money(row.get('market_value'))} | {format_money(row.get('unrealized_pnl'))} |"
            )

    lines.extend(["", "## Recent Trades", ""])
    if trades.empty:
        lines.append("No trades.")
    else:
        recent = trades.tail(20)
        lines.extend(["| Date | Account | Side | Code | Name | Shares | Price | Cost |", "|---|---|---|---|---|---:|---:|---:|"])
        for _, row in recent.iterrows():
            total_cost = (safe_float(row.get("commission")) or 0) + (safe_float(row.get("stamp_tax")) or 0)
            lines.append(
                f"| {row.get('trade_date')} | {row.get('account_id')} | {row.get('side')} | "
                f"{row.get('code')} | {row.get('name')} | {row.get('shares')} | "
                f"{row.get('price')} | {format_money(total_cost)} |"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_dashboard(config: dict[str, Any], store: PortfolioStore, reports_dir: str | Path) -> Path:
    ensure_dirs(reports_dir)
    summary = compute_performance(config, store)
    nav = store.read_nav()
    positions = store.read_positions()
    trades = store.read_trades()
    dashboard_path = Path(reports_dir) / "dashboard.html"

    nav_json = nav.to_json(orient="records", force_ascii=False) if not nav.empty else "[]"
    positions_html = positions.to_html(index=False, classes="table", border=0) if not positions.empty else "<p>No positions.</p>"
    trades_html = trades.tail(30).to_html(index=False, classes="table", border=0) if not trades.empty else "<p>No trades.</p>"
    cards = []
    for account_id, item in summary.get("accounts", {}).items():
        cards.append(
            f"""
            <section class="metric-card">
              <h3>{account_id}</h3>
              <div class="metric">{format_money(item.get('latest_value'))}</div>
              <p>Return {format_pct(item.get('cumulative_return'))} | Max DD {format_pct(item.get('max_drawdown'))}</p>
            </section>
            """
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock Analyze Dashboard</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #17202a; }}
    header {{ padding: 24px 32px; background: #10243e; color: white; }}
    main {{ padding: 24px 32px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin-top: 32px; font-size: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }}
    .metric-card {{ background: white; border: 1px solid #dfe4ea; border-radius: 8px; padding: 16px; }}
    .metric-card h3 {{ margin: 0 0 12px; font-size: 16px; color: #4b5563; }}
    .metric {{ font-size: 28px; font-weight: 700; }}
    .panel {{ background: white; border: 1px solid #dfe4ea; border-radius: 8px; padding: 16px; overflow: auto; }}
    .table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .table th, .table td {{ border-bottom: 1px solid #edf0f3; padding: 8px; text-align: left; white-space: nowrap; }}
    .table th {{ background: #f2f4f7; }}
    canvas {{ width: 100%; height: 320px; border: 1px solid #edf0f3; border-radius: 6px; background: #fff; }}
  </style>
</head>
<body>
  <header>
    <h1>Stock Analyze Simulation</h1>
    <div>Strategy {config.get('strategy_id')} | Generated {summary.get('generated_at')}</div>
  </header>
  <main>
    <section class="grid">{''.join(cards) or '<p>No NAV data yet.</p>'}</section>
    <h2>Net Value</h2>
    <div class="panel"><canvas id="navChart" width="1100" height="320"></canvas></div>
    <h2>Current Positions</h2>
    <div class="panel">{positions_html}</div>
    <h2>Recent Trades</h2>
    <div class="panel">{trades_html}</div>
  </main>
  <script>
    const nav = {nav_json};
    const canvas = document.getElementById('navChart');
    const ctx = canvas.getContext('2d');
    function drawChart(rows) {{
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!rows.length) {{
        ctx.fillText('No NAV data yet', 24, 40);
        return;
      }}
      const accounts = [...new Set(rows.map(r => r.account_id))];
      const colors = ['#2563eb', '#16a34a', '#f97316'];
      const values = rows.map(r => Number(r.total_value)).filter(Number.isFinite);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const pad = 36;
      ctx.strokeStyle = '#d8dee8';
      ctx.beginPath();
      ctx.moveTo(pad, pad);
      ctx.lineTo(pad, canvas.height - pad);
      ctx.lineTo(canvas.width - pad, canvas.height - pad);
      ctx.stroke();
      accounts.forEach((account, idx) => {{
        const series = rows.filter(r => r.account_id === account);
        ctx.strokeStyle = colors[idx % colors.length];
        ctx.lineWidth = 2;
        ctx.beginPath();
        series.forEach((row, i) => {{
          const x = pad + (canvas.width - pad * 2) * (i / Math.max(series.length - 1, 1));
          const y = canvas.height - pad - (canvas.height - pad * 2) * ((Number(row.total_value) - min) / Math.max(max - min, 1));
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }});
        ctx.stroke();
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillText(account, canvas.width - 120, 28 + idx * 18);
      }});
    }}
    drawChart(nav);
  </script>
</body>
</html>
"""
    dashboard_path.write_text(html, encoding="utf-8")
    return dashboard_path

