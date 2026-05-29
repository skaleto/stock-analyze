"""Render a ``BacktestResult`` as a human-readable markdown report.

Intended consumer: operators running the research CLI
(``python3 -m stock_analyze backtest ...``). Report is saved as
``report.md`` alongside the engine's CSV outputs.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pandas as pd

from .types import BacktestResult


def render_markdown_report(result: BacktestResult) -> str:
    """Return a markdown report for ``result``."""
    m = result.metrics
    lines: List[str] = [
        f"# 回测报告 · {result.start.isoformat()} → {result.end.isoformat()}",
        "",
        "## 总结",
        "",
        f"- 累计收益: {m.cum_return:+.1%}",
        f"- 年化收益: {m.annual_return:+.1%}",
        f"- Sharpe: {m.sharpe:.2f}",
        f"- 最大回撤: {m.max_drawdown:+.1%}",
        f"- 信息比率: {m.information_ratio:.2f}",
        "",
    ]

    # Trades summary if available
    trades_path = result.out_dir / "trades.csv"
    if trades_path.exists():
        try:
            # code / account_id / side / date are textual identifiers
            tdf = pd.read_csv(
                trades_path,
                dtype={"code": str, "account_id": str, "side": str, "date": str},
            )
            n_trades = len(tdf)
            n_buys = int((tdf["side"] == "BUY").sum()) if not tdf.empty else 0
            n_sells = int((tdf["side"] == "SELL").sum()) if not tdf.empty else 0
            lines.extend([
                "## 交易统计",
                "",
                f"- 总成交笔数: {n_trades}",
                f"- 买入: {n_buys}，卖出: {n_sells}",
                "",
            ])
        except (pd.errors.EmptyDataError, KeyError):
            pass

    # NAV summary if available
    nav_path = result.out_dir / "daily_nav.csv"
    if nav_path.exists():
        try:
            # Mirror store.py dtype invariant for daily_nav.
            ndf = pd.read_csv(
                nav_path,
                dtype={
                    "date": str,
                    "account_id": str,
                    "benchmark_code": str,
                    "benchmark_date": str,
                },
            )
            if not ndf.empty:
                portfolio = ndf.groupby("date")["total_value"].sum()
                lines.extend([
                    "## NAV 路径",
                    "",
                    f"- 起始总资产: ¥{portfolio.iloc[0]:,.0f}",
                    f"- 终值总资产: ¥{portfolio.iloc[-1]:,.0f}",
                    f"- 净值天数: {len(portfolio)}",
                    "",
                ])
        except (pd.errors.EmptyDataError, KeyError):
            pass

    lines.extend([
        "## 风险归因",
        "",
        f"- 最大回撤 ({m.max_drawdown:+.1%}) 是本期最大资金波动",
        f"- Sharpe ({m.sharpe:.2f}) 反映风险调整后收益",
        "",
        "## 备注",
        "",
        "本 MVP 回测引擎使用简化的信号生成（low PE top-N，等权目标），"
        "未走完整 factor_pipeline。完整 overlay 驱动的回测是后续工作；"
        "见 `openspec/changes/add-historical-backtest-engine/design.md` §12。",
        "",
    ])

    return "\n".join(lines)


def write_report(result: BacktestResult) -> Path:
    """Write report.md into ``result.out_dir`` and return the path."""
    md = render_markdown_report(result)
    out = result.out_dir / "report.md"
    out.write_text(md)
    return out


def render_compare_panel_markdown(
    result_full: BacktestResult,
    result_mvp: BacktestResult,
) -> str:
    """Render a "full-pipeline vs MVP PE-only" comparison panel.

    Emitted when ``backtest --compare-mvp`` runs the same overlay/window
    twice (``use_full_pipeline`` True then False) so a human can see whether
    the overlay's actual factor mix beats the naive low-PE proxy. Per
    OpenSpec change bridge-factor-pipeline-into-backtest §6.

    Compares the four BacktestMetrics that exist on both runs:
    cumulative return, max drawdown, Sharpe, and information ratio.
    (The design's "mean IC" row is represented by IR — the realised
    information ratio — since per-factor IC is not on BacktestMetrics.)
    """
    f = result_full.metrics
    v = result_mvp.metrics
    rows = [
        ("累计收益", f"{f.cum_return:+.1%}", f"{v.cum_return:+.1%}"),
        ("最大回撤", f"{f.max_drawdown:+.1%}", f"{v.max_drawdown:+.1%}"),
        ("Sharpe", f"{f.sharpe:.2f}", f"{v.sharpe:.2f}"),
        ("信息比率", f"{f.information_ratio:.2f}", f"{v.information_ratio:.2f}"),
    ]
    lines = [
        "## 与 MVP PE-only 信号对比",
        "",
        "| 指标 | 当前 overlay (full pipeline) | MVP PE-only |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {label} | {full} | {mvp} |" for label, full, mvp in rows)
    lines.append("")
    return "\n".join(lines)
