"""Generate weekly/monthly briefing markdown files that direct Claude Code /
Codex CLI in their local-development analysis loop.

The briefings are pure functions over what's already on disk in the agent's
``data/<agent>/`` namespace plus competition outputs. They never call any
LLM API. Output is markdown with five fixed sections so agents can parse
them deterministically.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .competition import (
    BASELINE_LOCKED_PATHS,
    AgentPaths,
    load_baseline,
    resolve_agent_paths,
)
from .monthly_review import default_month_for


BRIEFINGS_SUBDIR = "notes/briefings"


def build_weekly_briefing(
    agent_id: str,
    as_of: str | None = None,
    repo_root: str | Path | None = None,
) -> str:
    """Return the weekly briefing markdown for the given agent."""

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = resolve_agent_paths(agent_id, repo_root=root)
    as_of = as_of or date.today().isoformat()
    overlay = _load_overlay(paths.config_path)

    lines: list[str] = []
    lines += _role_section(agent_id, paths, overlay, target_kind="weekly", as_of=as_of)
    lines += _weekly_data_snapshot(paths, as_of)
    lines += _weekly_task_section(agent_id, as_of)
    lines += _weekly_output_contract(agent_id, as_of)
    lines += _weekly_references_section(paths)
    return "\n".join(lines).rstrip() + "\n"


def build_monthly_briefing(
    agent_id: str,
    month: str | None = None,
    repo_root: str | Path | None = None,
) -> str:
    """Return the monthly briefing markdown for the given agent."""

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = resolve_agent_paths(agent_id, repo_root=root)
    month = month or default_month_for()
    overlay = _load_overlay(paths.config_path)
    baseline = _try_load_baseline(root)

    lines: list[str] = []
    lines += _role_section(agent_id, paths, overlay, target_kind="monthly", as_of=month)
    lines += _monthly_data_snapshot(paths, month, root)
    lines += _monthly_task_section(agent_id, month)
    lines += _monthly_output_contract(agent_id, month, baseline)
    lines += _monthly_references_section(paths)
    return "\n".join(lines).rstrip() + "\n"


def write_briefing(text: str, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")
    return target_path


def weekly_briefing_path(paths: AgentPaths, as_of: str | None = None) -> Path:
    as_of = as_of or date.today().isoformat()
    return paths.data_dir / BRIEFINGS_SUBDIR / f"{as_of}-weekly.md"


def monthly_briefing_path(paths: AgentPaths, month: str | None = None) -> Path:
    month = month or default_month_for()
    return paths.data_dir / BRIEFINGS_SUBDIR / f"{month}-monthly.md"


# ---------------------------------------------------------------------------
# Sections


def _role_section(
    agent_id: str,
    paths: AgentPaths,
    overlay: dict[str, Any],
    target_kind: str,
    as_of: str,
) -> list[str]:
    strategy_id = str(overlay.get("strategy_id") or agent_id)
    name = str(overlay.get("name") or strategy_id)
    return [
        "# 角色",
        "",
        f"你正在作为 **{agent_id}** agent 工作（策略：`{strategy_id}` · {name}）。",
        f"任务类型：**{target_kind}**。数据截止：`{as_of}`。",
        "",
        f"你的工作目录：`{paths.data_dir}`、`{paths.reports_dir}`。",
        f"你的策略 overlay：`{paths.config_path}`。",
        "",
        "**绝对不要**修改：`configs/competition.yaml`、`configs/agents/<其它 agent>.yaml`、",
        "`stock_analyze/*.py`、`tests/*.py`、`openspec/specs/*` 以及 `CLAUDE.md` / `AGENTS.md`。",
        "",
    ]


def _weekly_data_snapshot(paths: AgentPaths, as_of: str) -> list[str]:
    lines = ["# 数据快照", ""]
    lines += _render_recent_runs(paths)
    lines += _render_recent_nav(paths)
    lines += _render_latest_signals(paths)
    lines += _render_recent_trades(paths)
    lines += _render_current_positions(paths)
    lines += _render_pending_orders(paths)
    lines += _render_factor_coverage(paths, last_n_weeks=2)
    lines += _render_forward_ic(paths, last_n_weeks=2)
    return lines


def _monthly_data_snapshot(paths: AgentPaths, month: str, root: Path) -> list[str]:
    lines = ["# 数据快照", ""]
    lines += _render_monthly_review_excerpt(root, month)
    lines += _render_recent_runs(paths, limit=10)
    lines += _render_monthly_nav(paths, month)
    lines += _render_current_positions(paths)
    lines += _render_factor_coverage(paths, last_n_weeks=6)
    lines += _render_forward_ic(paths, last_n_weeks=6)
    lines += _render_recent_weekly_notes(paths, limit=4)
    return lines


def _weekly_task_section(agent_id: str, as_of: str) -> list[str]:
    return [
        "# 任务",
        "",
        "请基于上方数据快照写一份 **≤800 字** 的中文 markdown 笔记，覆盖：",
        "",
        "1. **数据合理性检查**：本周数据是否有异常（成交失败激增 / 因子覆盖突降 / NAV 离群 / 行业暴露漂移 / 数据源失败）。",
        "2. **本周表现归因**：净值变化主要来自哪些因子贡献、哪些个股、是否被基准带动。",
        '3. **观察点**：下周需要重点关注的信号或风险。**不要**写"应该买/卖"。',
        "4. **下一步计划草稿**：如果月底要调整策略，初步会考虑往哪个方向调（一句话）。",
        "",
        "**本周不要修改任何 `configs/` 下的内容。**仅产出笔记。",
        "",
    ]


def _weekly_output_contract(agent_id: str, as_of: str) -> list[str]:
    target = f"data/{agent_id}/notes/{as_of}-weekly-review.md"
    return [
        "# 输出契约",
        "",
        f"把笔记 markdown 写到 **`{target}`**。",
        "如果该文件已存在，覆盖之（同一天可以重写，但请保留同等结构）。",
        "",
        "其它路径一律不要写入。**不要**创建 `configs/`、`stock_analyze/`、`tests/`、`reports/` 下的文件。",
        "",
    ]


def _weekly_references_section(paths: AgentPaths) -> list[str]:
    notes_dir = paths.data_dir / "notes"
    if not notes_dir.exists():
        return ["# 可选参考", "", "本周首次跑，无历史笔记。", ""]
    candidates = sorted(
        [
            path
            for path in notes_dir.glob("*.md")
            if path.is_file()
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:3]
    if not candidates:
        return ["# 可选参考", "", "无历史笔记。", ""]
    lines = ["# 可选参考", "", "最近 3 篇你写过的笔记（可以读进来对照）：", ""]
    for path in candidates:
        lines.append(f"- `{path.relative_to(paths.data_dir.parents[1])}`")
    lines.append("")
    return lines


def _monthly_task_section(agent_id: str, month: str) -> list[str]:
    return [
        "# 任务",
        "",
        f"基于上方 `{month}` 月度对比与近期周笔记，做两件事：",
        "",
        "1. **写一份月度策略思考笔记**（中文 markdown，≤1500 字）。结构：",
        "   - 月度表现复盘（数据驱动，不情绪化）。",
        "   - 与对手的差异化分析（持仓重叠 / 风格相关性 / 共同与分歧因子驱动）。",
        "   - 策略调整方向与理由（1-3 条，每条一句话）。",
        "",
        "2. **产出一份月度策略提案 JSON**。",
        "   - 这是结构化输出，用户审核后才会被自动应用（Phase 2）。",
        "   - 若决定本月不调，仍要输出 JSON 但 `no_change=true`、`patch={}`。",
        "",
    ]


def _monthly_output_contract(agent_id: str, month: str, baseline: dict[str, Any] | None) -> list[str]:
    notes_target = f"data/{agent_id}/notes/{month}-monthly-review.md"
    proposal_target = f"data/{agent_id}/proposals/{month}-strategy.json"
    locked_lines = [f"- `{path}`" for path in BASELINE_LOCKED_PATHS]
    baseline_excerpt: list[str] = []
    if baseline:
        accounts = baseline.get("accounts") or []
        baseline_excerpt = [
            "",
            "Baseline 关键值（不可改）：",
            f"- `competition_id`: `{baseline.get('competition_id')}`",
            f"- `start_date`: `{baseline.get('start_date')}`",
            f"- `initial_cash`: `{baseline.get('initial_cash')}`",
        ]
        for account in accounts:
            baseline_excerpt.append(
                f"- account `{account.get('id')}`: scope=`{account.get('scope')}`, "
                f"benchmark=`{account.get('benchmark')}`, cash=`{account.get('cash')}`, top_n=`{account.get('top_n')}`"
            )
        trading = baseline.get("trading") or {}
        if trading:
            baseline_excerpt.append("- `trading`:")
            for key, value in trading.items():
                baseline_excerpt.append(f"  - `{key}`: `{value}`")
    return [
        "# 输出契约",
        "",
        f"1. 月度笔记写到 **`{notes_target}`**（中文 markdown）。",
        f"2. 月度提案写到 **`{proposal_target}`**，严格 JSON。Schema 如下：",
        "",
        "```json",
        json.dumps(
            {
                "agent_id": agent_id,
                "based_on_config_hash": "<当前 overlay 的 config_hash，可在 data/<agent>/runs.csv 最近一行找到>",
                "proposed_at": "<YYYY-MM-DD>",
                "rationale": "<300 字内中文>",
                "expected_effect": "<一句话>",
                "risks": ["<风险 1>", "<风险 2>"],
                "no_change": False,
                "patch": {
                    "factors": {"<factor_name>": {"weight": 0.15, "direction": "low"}},
                    "factor_processing": {"<field>": "<value>"},
                    "portfolio_controls": {"<field>": "<value>"},
                    "filters": {"<field>": "<value>"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "**`patch` 中包含以下锁字段或路径会被拒绝**：",
        "",
        *locked_lines,
        *baseline_excerpt,
        "",
        "其它路径不要写入。**不要**修改 `configs/`、`stock_analyze/`、`tests/`、`reports/`。",
        "",
    ]


def _monthly_references_section(paths: AgentPaths) -> list[str]:
    proposals_dir = paths.data_dir / "proposals"
    notes_dir = paths.data_dir / "notes"
    lines = ["# 可选参考", ""]
    notes_recent: list[Path] = []
    if notes_dir.exists():
        notes_recent = sorted(
            [path for path in notes_dir.glob("*.md") if path.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:4]
    if notes_recent:
        lines.append("最近 4 篇你写过的笔记：")
        lines.append("")
        for path in notes_recent:
            lines.append(f"- `{path.relative_to(paths.data_dir.parents[1])}`")
        lines.append("")
    proposals_recent: list[Path] = []
    if proposals_dir.exists():
        proposals_recent = sorted(
            [path for path in proposals_dir.glob("*-strategy.json") if path.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:3]
    if proposals_recent:
        lines.append("最近 3 份历史提案：")
        lines.append("")
        for path in proposals_recent:
            lines.append(f"- `{path.relative_to(paths.data_dir.parents[1])}`")
        lines.append("")
    if not notes_recent and not proposals_recent:
        lines.append("无历史笔记或提案。本月首次策略提案。")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Data rendering helpers


def _render_recent_runs(paths: AgentPaths, limit: int = 5) -> list[str]:
    path = paths.data_dir / "runs.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 最近运行", "", "尚无 `runs.csv`，可能是第一次跑。", ""]
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return ["## 最近运行", "", "`runs.csv` 解析失败，跳过。", ""]
    if df.empty:
        return ["## 最近运行", "", "`runs.csv` 为空。", ""]
    df = df.copy()
    df = df.sort_values("started_at", ascending=False).drop_duplicates("run_id", keep="first").head(limit)
    columns = ["run_id", "command", "status", "duration_ms", "config_hash", "code_version", "started_at"]
    available = [col for col in columns if col in df.columns]
    return ["## 最近运行", "", _df_to_markdown_table(df[available]), ""]


def _render_recent_nav(paths: AgentPaths, limit: int = 7) -> list[str]:
    path = paths.data_dir / "daily_nav.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 最近净值", "", "尚无 `daily_nav.csv`。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 最近净值", "", "`daily_nav.csv` 为空。", ""]
    df = df.sort_values(["date", "account_id"], ascending=[False, True]).head(limit * 2)
    df["total_value"] = pd.to_numeric(df.get("total_value"), errors="coerce").round(2)
    df["benchmark_close"] = pd.to_numeric(df.get("benchmark_close"), errors="coerce").round(2)
    cols = [col for col in ["date", "account_id", "cash", "market_value", "total_value", "benchmark_code", "benchmark_close", "notes"] if col in df.columns]
    return ["## 最近净值", "", _df_to_markdown_table(df[cols]), ""]


def _render_monthly_nav(paths: AgentPaths, month: str) -> list[str]:
    path = paths.data_dir / "daily_nav.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 本月净值序列", "", "尚无 `daily_nav.csv`。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 本月净值序列", "", "`daily_nav.csv` 为空。", ""]
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    target = pd.to_datetime(month)
    df = df[(df["date"].dt.year == target.year) & (df["date"].dt.month == target.month)]
    if df.empty:
        return ["## 本月净值序列", "", f"`{month}` 月内没有 NAV 数据。", ""]
    df = df.sort_values(["date", "account_id"])
    df["date"] = df["date"].dt.date.astype(str)
    df["total_value"] = pd.to_numeric(df.get("total_value"), errors="coerce").round(2)
    cols = [col for col in ["date", "account_id", "total_value", "benchmark_close"] if col in df.columns]
    return ["## 本月净值序列", "", _df_to_markdown_table(df[cols]), ""]


def _render_latest_signals(paths: AgentPaths) -> list[str]:
    path = paths.data_dir / "latest_signals.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 本期入选信号", "", "尚无 `latest_signals.csv`。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 本期入选信号", "", "`latest_signals.csv` 为空。", ""]
    cols = [col for col in ["account_id", "code", "name", "industry", "score", "score_detail"] if col in df.columns]
    return ["## 本期入选信号", "", _df_to_markdown_table(df[cols].head(40)), ""]


def _render_recent_trades(paths: AgentPaths, limit: int = 20) -> list[str]:
    path = paths.data_dir / "trades.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 最近模拟成交", "", "尚无 `trades.csv`。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 最近模拟成交", "", "`trades.csv` 为空。", ""]
    df = df.sort_values("trade_date", ascending=False).head(limit)
    cols = [col for col in ["trade_date", "account_id", "code", "name", "side", "shares", "price", "gross_amount", "commission", "stamp_tax", "slippage", "reason"] if col in df.columns]
    return ["## 最近模拟成交", "", _df_to_markdown_table(df[cols]), ""]


def _render_current_positions(paths: AgentPaths) -> list[str]:
    path = paths.data_dir / "positions.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 当前持仓", "", "尚无 `positions.csv`。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 当前持仓", "", "`positions.csv` 为空。", ""]
    cols = [col for col in ["account_id", "code", "name", "industry", "shares", "available_shares", "avg_cost", "last_price", "market_value", "unrealized_pnl", "hold_since"] if col in df.columns]
    return ["## 当前持仓", "", _df_to_markdown_table(df[cols]), ""]


def _render_pending_orders(paths: AgentPaths) -> list[str]:
    path = paths.data_dir / "pending_orders.json"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 待执行订单", "", "`pending_orders.json` 不存在或为空。", ""]
    try:
        batches = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["## 待执行订单", "", "`pending_orders.json` 解析失败。", ""]
    rows: list[dict[str, Any]] = []
    for batch in batches:
        for order in batch.get("orders", []):
            rows.append(
                {
                    "signal_date": batch.get("signal_date"),
                    "execute_after": batch.get("execute_after"),
                    "account_id": batch.get("account_id"),
                    "code": order.get("code"),
                    "side": order.get("side"),
                    "delta_shares": order.get("delta_shares"),
                    "status": order.get("status"),
                    "unfilled_reason": order.get("unfilled_reason"),
                    "attempts": order.get("attempts"),
                }
            )
    if not rows:
        return ["## 待执行订单", "", "无待执行订单。", ""]
    df = pd.DataFrame(rows)
    return ["## 待执行订单", "", _df_to_markdown_table(df), ""]


def _render_factor_coverage(paths: AgentPaths, last_n_weeks: int) -> list[str]:
    path = paths.data_dir / "factor_diagnostics" / "coverage.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 因子覆盖率", "", "尚无覆盖率数据。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 因子覆盖率", "", "覆盖率文件为空。", ""]
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df.get("signal_date"), errors="coerce")
    df = df.dropna(subset=["signal_date"])
    if df.empty:
        return ["## 因子覆盖率", "", "覆盖率数据日期无法解析。", ""]
    recent_dates = sorted(df["signal_date"].unique())[-last_n_weeks:]
    rows = df[df["signal_date"].isin(recent_dates)]
    pivot = rows.pivot_table(values="coverage_pct", index="factor", columns="signal_date", aggfunc="mean")
    pivot = pivot.reset_index()
    pivot.columns = [str(col) if isinstance(col, str) else pd.to_datetime(col).date().isoformat() for col in pivot.columns]
    return ["## 因子覆盖率（最近 " + str(last_n_weeks) + " 周）", "", _df_to_markdown_table(pivot, precision=3), ""]


def _render_forward_ic(paths: AgentPaths, last_n_weeks: int) -> list[str]:
    path = paths.data_dir / "factor_diagnostics" / "forward_ic.csv"
    if not path.exists() or path.stat().st_size == 0:
        return ["## 前向 RankIC", "", "尚无前向 IC 数据。", ""]
    df = _safe_read_csv(path)
    if df.empty:
        return ["## 前向 RankIC", "", "前向 IC 文件为空。", ""]
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df.get("signal_date"), errors="coerce")
    df = df.dropna(subset=["signal_date"])
    ok = df[df["ic_status"] == "ok"]
    if ok.empty:
        return ["## 前向 RankIC", "", "尚无满足 5 个交易日前向窗口的 IC。", ""]
    recent_dates = sorted(ok["signal_date"].unique())[-last_n_weeks:]
    rows = ok[ok["signal_date"].isin(recent_dates)]
    pivot = rows.pivot_table(values="ic", index="factor", columns="signal_date", aggfunc="mean")
    pivot = pivot.reset_index()
    pivot.columns = [str(col) if isinstance(col, str) else pd.to_datetime(col).date().isoformat() for col in pivot.columns]
    return ["## 前向 RankIC（最近 " + str(last_n_weeks) + " 周）", "", _df_to_markdown_table(pivot, precision=3), ""]


def _render_monthly_review_excerpt(root: Path, month: str) -> list[str]:
    path = root / "data" / "competition" / "monthly_reviews" / f"{month}.json"
    if not path.exists():
        return ["## 月度对比报告", "", f"尚未生成 `{path.relative_to(root)}`，先跑 `competition-monthly-review --month {month}`。", ""]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["## 月度对比报告", "", "JSON 解析失败。", ""]
    lines = ["## 月度对比报告", "", f"`{path.relative_to(root)}`", ""]
    comp = payload.get("comparison") or {}
    agents_block = payload.get("agents") or {}
    lines.append("- 累计收益胜方：`" + str(comp.get("winner_cumulative_return") or "-") + "`")
    lines.append("- 信息比率胜方：`" + str(comp.get("winner_information_ratio") or "-") + "`")
    spread = comp.get("spread_cumulative_return")
    lines.append("- 累计收益差：`" + (f"{spread:+.4f}" if isinstance(spread, (int, float)) else "-") + "`")
    lines.append("- 持仓重叠度：`" + (f"{comp.get('position_overlap_ratio'):.2%}" if isinstance(comp.get("position_overlap_ratio"), (int, float)) else "-") + "`")
    lines.append("- 日收益相关性：`" + (f"{comp.get('daily_return_correlation'):.3f}" if isinstance(comp.get("daily_return_correlation"), (int, float)) else "-") + "`")
    lines.append("- 共同因子：`" + ", ".join(comp.get("shared_factor_drivers") or []) + "`")
    diff = comp.get("divergent_factor_drivers") or {}
    if diff:
        lines.append("- 分歧因子：" + " · ".join(f"`{key}=[{', '.join(values)}]`" for key, values in diff.items()))
    lines.append("")
    lines.append("各 agent 关键指标：")
    lines.append("")
    table_rows = []
    for agent_id, block in agents_block.items():
        table_rows.append(
            {
                "agent": agent_id,
                "cumulative_return": block.get("cumulative_return"),
                "annualized_return": block.get("annualized_return"),
                "sharpe_ratio": block.get("sharpe_ratio"),
                "information_ratio": block.get("information_ratio"),
                "max_drawdown": block.get("max_drawdown"),
                "weekly_turnover_avg": block.get("weekly_turnover_avg"),
                "cost_bps": block.get("cost_bps"),
                "round_trip_win_rate": block.get("round_trip_win_rate"),
            }
        )
    if table_rows:
        lines.append(_df_to_markdown_table(pd.DataFrame(table_rows), precision=4))
    lines.append("")
    return lines


def _render_recent_weekly_notes(paths: AgentPaths, limit: int) -> list[str]:
    notes_dir = paths.data_dir / "notes"
    if not notes_dir.exists():
        return ["## 近期周笔记摘要", "", "尚无周笔记。", ""]
    notes = sorted(
        [path for path in notes_dir.glob("*-weekly-review.md") if path.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]
    if not notes:
        return ["## 近期周笔记摘要", "", "尚无周笔记。", ""]
    lines = [f"## 近期周笔记摘要（最近 {len(notes)} 篇）", ""]
    for path in notes:
        text = path.read_text(encoding="utf-8")
        excerpt = text.strip().splitlines()
        head = "\n".join(excerpt[:30])
        lines.append(f"### `{path.relative_to(paths.data_dir.parents[1])}`")
        lines.append("")
        lines.append("```markdown")
        lines.append(head)
        lines.append("```")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Misc


def _load_overlay(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _try_load_baseline(root: Path) -> dict[str, Any] | None:
    try:
        return load_baseline(root)
    except Exception:  # noqa: BLE001
        return None


def _safe_read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def _df_to_markdown_table(df: pd.DataFrame, precision: int = 4) -> str:
    if df.empty:
        return "_（空）_"
    rounded = df.copy()
    for col in rounded.columns:
        if pd.api.types.is_float_dtype(rounded[col]):
            rounded[col] = rounded[col].round(precision)
    header = "| " + " | ".join(str(col) for col in rounded.columns) + " |"
    divider = "| " + " | ".join("---" for _ in rounded.columns) + " |"
    rows = []
    for _, row in rounded.iterrows():
        rows.append("| " + " | ".join(_format_cell(value) for value in row.tolist()) + " |")
    return "\n".join([header, divider, *rows])


def _format_cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if pd.isna(value):
            return "-"
        return f"{value:.4f}".rstrip("0").rstrip(".") if "." in f"{value:.4f}" else f"{value:.4f}"
    text = str(value)
    text = text.replace("|", "/")
    if len(text) > 80:
        text = text[:77] + "…"
    return text
