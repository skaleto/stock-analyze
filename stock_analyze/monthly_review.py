"""Monthly comparison review across competing agents.

Reads each agent's ``performance_summary.json`` / ``daily_nav.csv`` /
``positions.csv`` / ``factor_diagnostics/forward_ic.csv`` and produces:

- ``data/competition/monthly_reviews/<month>.json`` — machine-readable
- ``reports/competition/monthly_review_<month>.md`` — human-readable
- ``data/competition/leaderboard.csv`` — rolling per-month winners (upsert)

The implementation is intentionally side-effect free except for the three
artifacts above so it can be unit-tested with handcrafted DataFrames.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .competition import AgentPaths, resolve_agent_paths
from .store import PortfolioStore
from .utils import ensure_dirs, format_pct, safe_float, write_dataframe_csv_atomic, write_json, write_text_atomic


LEADERBOARD_COLUMNS = [
    "month",
    "claude_return",
    "codex_return",
    "winner_return",
    "claude_ir",
    "codex_ir",
    "winner_ir",
    "generated_at",
]


@dataclass
class AgentMonthly:
    agent_id: str
    block: dict[str, Any]
    positions: list[str]
    daily_returns: pd.Series
    factor_ic: dict[str, float]


def compute_review(
    month: str,
    agents: list[str],
    repo_root: str | Path | None = None,
    *,
    market: str = "a_share",
) -> dict[str, Any]:
    """Compute the review payload for the given month and agent list."""

    root = Path(repo_root) if repo_root else Path.cwd()
    paths_by_agent = {agent: resolve_agent_paths(agent, repo_root=root) for agent in agents}
    monthlies = {agent: _load_agent_monthly(agent, paths_by_agent[agent], month) for agent in agents}

    agents_block = {agent: m.block for agent, m in monthlies.items()}
    comparison = _build_comparison(monthlies)

    return {
        "review_period": month,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "agents": agents_block,
        "comparison": comparison,
    }


def write_review(
    payload: dict[str, Any],
    repo_root: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    """Persist the review JSON + Markdown + leaderboard CSV.

    Returns ``(json_path, markdown_path, leaderboard_path)``.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    competition_data = root / "data" / "competition"
    competition_reports = root / "reports" / "competition"
    monthly_dir = competition_data / "monthly_reviews"
    ensure_dirs(competition_data, competition_reports, monthly_dir)

    month = payload["review_period"]
    json_path = monthly_dir / f"{month}.json"
    md_path = competition_reports / f"monthly_review_{month}.md"
    leaderboard_path = competition_data / "leaderboard.csv"

    write_json(json_path, payload)
    write_text_atomic(md_path, _render_markdown(payload), encoding="utf-8")
    _upsert_leaderboard(leaderboard_path, payload)
    return json_path, md_path, leaderboard_path


def default_month_for(today: date | None = None) -> str:
    """Return the previous calendar month string for the given anchor."""

    anchor = today or date.today()
    first_of_month = anchor.replace(day=1)
    prev = first_of_month - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


# ---------------------------------------------------------------------------
# Internals


def _load_agent_monthly(agent_id: str, paths: AgentPaths, month: str) -> AgentMonthly:
    store = PortfolioStore(paths.data_dir)
    perf_path = paths.data_dir / "performance_summary.json"
    if perf_path.exists():
        perf = json.loads(perf_path.read_text(encoding="utf-8"))
    else:
        perf = {"accounts": {}, "config_hash": None}
    accounts = perf.get("accounts") or {}
    aggregated = _aggregate_perf_accounts(accounts)
    aggregated["config_hash"] = perf.get("config_hash")
    aggregated["strategy_id"] = perf.get("strategy_id")

    nav = store.read_nav() if (paths.data_dir / "daily_nav.csv").exists() else pd.DataFrame()
    daily_returns = _monthly_daily_returns(nav, month)
    positions = _latest_positions(store)
    factor_ic = _monthly_factor_ic(store, month)
    aggregated["factor_ic_top3"] = _top_n(factor_ic, 3)
    aggregated["industry_exposure_top3"] = _industry_exposure_top(store, n=3)
    aggregated["active_factors"] = _active_factors(paths.config_path)

    return AgentMonthly(
        agent_id=agent_id,
        block=aggregated,
        positions=positions,
        daily_returns=daily_returns,
        factor_ic=factor_ic,
    )


def _aggregate_perf_accounts(accounts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-account performance into a single agent-level block.

    Aggregation rule: average across accounts for ratio-like metrics; sum
    across accounts for absolute metrics. NAVs share the same start, so a
    simple mean is faithful enough for a competition summary.
    """

    if not accounts:
        return {
            "cumulative_return": None,
            "annualized_return": None,
            "annualized_volatility": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": None,
            "information_ratio": None,
            "tracking_error": None,
            "weekly_turnover_avg": None,
            "cost_bps": None,
            "round_trip_win_rate": None,
            "nav_points": 0,
        }

    def _avg(field: str) -> float | None:
        values = [safe_float(account.get(field)) for account in accounts.values()]
        values = [value for value in values if value is not None]
        return float(np.mean(values)) if values else None

    nav_points = max((int(account.get("nav_points") or 0) for account in accounts.values()), default=0)
    return {
        "cumulative_return": _avg("cumulative_return"),
        "annualized_return": _avg("annualized_return"),
        "annualized_volatility": _avg("annualized_volatility"),
        "sharpe_ratio": _avg("sharpe_ratio"),
        "sortino_ratio": _avg("sortino_ratio"),
        "max_drawdown": _avg("max_drawdown"),
        "information_ratio": _avg("information_ratio"),
        "tracking_error": _avg("tracking_error"),
        "weekly_turnover_avg": _avg("weekly_turnover_avg"),
        "cost_bps": _avg("cost_bps"),
        "round_trip_win_rate": _avg("round_trip_win_rate"),
        "nav_points": nav_points,
    }


def _monthly_daily_returns(nav: pd.DataFrame, month: str) -> pd.Series:
    if nav.empty:
        return pd.Series(dtype=float)
    nav = nav.copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce")
    nav = nav.dropna(subset=["date"])
    grouped = nav.groupby("date")["total_value"].sum().sort_index()
    daily_return = grouped.pct_change()
    target = pd.to_datetime(month)
    mask = (daily_return.index.year == target.year) & (daily_return.index.month == target.month)
    return daily_return[mask].dropna()


def _latest_positions(store: PortfolioStore) -> list[str]:
    if not (store.data_dir / "positions.csv").exists():
        return []
    df = store.read_positions()
    if df.empty:
        return []
    return sorted({str(code).zfill(6) for code in df["code"].astype(str).tolist()})


def _monthly_factor_ic(store: PortfolioStore, month: str) -> dict[str, float]:
    ic = store.read_forward_ic()
    if ic.empty:
        return {}
    ic = ic.copy()
    ic["signal_date"] = pd.to_datetime(ic["signal_date"], errors="coerce")
    ic = ic.dropna(subset=["signal_date"])
    target = pd.to_datetime(month)
    mask = (ic["signal_date"].dt.year == target.year) & (ic["signal_date"].dt.month == target.month) & (ic["ic_status"] == "ok")
    rows = ic[mask]
    if rows.empty:
        return {}
    return rows.groupby("factor")["ic"].mean().to_dict()


def _top_n(mapping: dict[str, float], n: int) -> list[list[Any]]:
    if not mapping:
        return []
    items = sorted(((str(key), float(value)) for key, value in mapping.items()), key=lambda item: abs(item[1]), reverse=True)
    return [[key, round(value, 6)] for key, value in items[:n]]


def _industry_exposure_top(store: PortfolioStore, n: int) -> list[list[Any]]:
    if not (store.data_dir / "positions.csv").exists():
        return []
    df = store.read_positions()
    if df.empty or "industry" not in df.columns:
        return []
    df = df.copy()
    df["market_value"] = pd.to_numeric(df.get("market_value"), errors="coerce").fillna(0.0)
    if df["market_value"].sum() == 0:
        counts = df.groupby("industry")["code"].count()
        weights = counts / counts.sum() if counts.sum() else counts
    else:
        weights = df.groupby("industry")["market_value"].sum() / df["market_value"].sum()
    weights = weights.sort_values(ascending=False).head(n)
    return [[str(industry), round(float(value), 4)] for industry, value in weights.items()]


def _active_factors(config_path: Path) -> list[str]:
    if not config_path.exists():
        return []
    try:
        overlay = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    factors = overlay.get("factors") or {}
    return sorted(name for name, spec in factors.items() if float(spec.get("weight", 0)) > 0)


def _build_comparison(monthlies: dict[str, AgentMonthly]) -> dict[str, Any]:
    agents = list(monthlies.keys())
    if len(agents) != 2:
        # Generic N-way comparison would be nice but for MVP we focus on the 2-agent case.
        return {"agents_compared": agents, "note": "comparison_requires_exactly_two_agents"}

    a, b = agents
    block_a = monthlies[a].block
    block_b = monthlies[b].block
    winner_cum = _winner(a, b, block_a.get("cumulative_return"), block_b.get("cumulative_return"))
    winner_ir = _winner(a, b, block_a.get("information_ratio"), block_b.get("information_ratio"))
    spread = _safe_subtract(block_a.get("cumulative_return"), block_b.get("cumulative_return"))

    pos_a = set(monthlies[a].positions)
    pos_b = set(monthlies[b].positions)
    union = pos_a | pos_b
    overlap = (len(pos_a & pos_b) / len(union)) if union else None

    aligned = pd.concat([monthlies[a].daily_returns, monthlies[b].daily_returns], axis=1).dropna()
    if aligned.shape[0] >= 2 and aligned.iloc[:, 0].std(ddof=0) > 0 and aligned.iloc[:, 1].std(ddof=0) > 0:
        correlation = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    else:
        correlation = None

    factor_a = {row[0] for row in block_a.get("factor_ic_top3") or []}
    factor_b = {row[0] for row in block_b.get("factor_ic_top3") or []}
    shared = sorted(factor_a & factor_b)
    divergent = {
        f"{a}_only": sorted(factor_a - factor_b),
        f"{b}_only": sorted(factor_b - factor_a),
    }

    return {
        "agents_compared": [a, b],
        "winner_cumulative_return": winner_cum,
        "winner_information_ratio": winner_ir,
        "spread_cumulative_return": round(spread, 6) if spread is not None else None,
        "position_overlap_ratio": round(overlap, 4) if overlap is not None else None,
        "daily_return_correlation": round(correlation, 4) if correlation is not None else None,
        "shared_factor_drivers": shared,
        "divergent_factor_drivers": divergent,
    }


def _winner(a: str, b: str, value_a: Any, value_b: Any) -> str | None:
    va = safe_float(value_a)
    vb = safe_float(value_b)
    if va is None and vb is None:
        return None
    if va is None:
        return b
    if vb is None:
        return a
    if va > vb:
        return a
    if vb > va:
        return b
    return "tie"


def _safe_subtract(left: Any, right: Any) -> float | None:
    a = safe_float(left)
    b = safe_float(right)
    if a is None or b is None:
        return None
    return float(a) - float(b)


def _render_markdown(payload: dict[str, Any]) -> str:
    month = payload["review_period"]
    comp = payload.get("comparison") or {}
    agents_block = payload.get("agents") or {}
    agents = comp.get("agents_compared") or list(agents_block.keys())
    if len(agents) != 2:
        return _render_markdown_generic(payload)
    a, b = agents
    block_a = agents_block.get(a, {})
    block_b = agents_block.get(b, {})

    def _row(label: str, key: str, fmt) -> str:
        return (
            f"| {label} | {fmt(block_a.get(key))} | {fmt(block_b.get(key))} "
            f"| {_winner_cell(_winner(a, b, block_a.get(key), block_b.get(key)), a, b)} |"
        )

    fmt_pct = format_pct

    def fmt_ratio(value: Any) -> str:
        number = safe_float(value)
        return "-" if number is None else f"{number:.2f}"

    def fmt_bps(value: Any) -> str:
        number = safe_float(value)
        return "-" if number is None else f"{number:.1f} bps"

    lines = [
        f"# 月度对比报告 · {month}",
        "",
        f"competition_id: `{payload.get('competition_id', '-')}` · generated_at: `{payload.get('generated_at')}`",
        f"配置快照: `{a}={block_a.get('config_hash') or '-'}` `{b}={block_b.get('config_hash') or '-'}`",
        "",
        "> 本报告仅基于模拟交易数据，不构成投资建议。",
        "",
        "## 指标对比",
        "",
        f"| 指标 | {a} | {b} | 胜方 |",
        "|---|---:|---:|:---:|",
        _row("累计收益", "cumulative_return", fmt_pct),
        _row("年化收益", "annualized_return", fmt_pct),
        _row("年化波动", "annualized_volatility", fmt_pct),
        _row("Sharpe", "sharpe_ratio", fmt_ratio),
        _row("Sortino", "sortino_ratio", fmt_ratio),
        _row("最大回撤", "max_drawdown", fmt_pct),
        _row("信息比率", "information_ratio", fmt_ratio),
        _row("跟踪误差", "tracking_error", fmt_pct),
        _row("周换手率", "weekly_turnover_avg", fmt_pct),
        _row("成本(bps)", "cost_bps", fmt_bps),
        _row("Win Rate", "round_trip_win_rate", fmt_pct),
        "",
        "## 比较结果",
        "",
        f"- 累计收益胜方：**{comp.get('winner_cumulative_return') or '-'}**",
        f"- 信息比率胜方：**{comp.get('winner_information_ratio') or '-'}**",
        f"- 累计收益差（{a} − {b}）：{fmt_pct(comp.get('spread_cumulative_return'))}",
        f"- 持仓重叠度（Jaccard）：{fmt_pct(comp.get('position_overlap_ratio'))}",
        f"- 日收益相关性：{fmt_ratio(comp.get('daily_return_correlation'))}",
        "",
        "## 因子有效性",
        "",
        f"- 共同驱动因子：{', '.join(comp.get('shared_factor_drivers') or []) or '-'}",
        f"- 仅 {a} 驱动：{', '.join(comp.get('divergent_factor_drivers', {}).get(f'{a}_only', [])) or '-'}",
        f"- 仅 {b} 驱动：{', '.join(comp.get('divergent_factor_drivers', {}).get(f'{b}_only', [])) or '-'}",
        "",
        "## 差异化建议（基于对比数据自动生成）",
        "",
        _auto_recommendation(a, b, comp),
        "",
        "## 行业暴露",
        "",
        f"- {a}：{_format_exposure_list(block_a.get('industry_exposure_top3'))}",
        f"- {b}：{_format_exposure_list(block_b.get('industry_exposure_top3'))}",
        "",
        "## 持仓快照",
        "",
        f"- {a} 持仓数：{len(payload.get('agents', {}).get(a, {}).get('positions', [])) or '由 positions.csv 决定'}（详见 `data/{a}/positions.csv`）",
        f"- {b} 持仓数：详见 `data/{b}/positions.csv`",
    ]
    return "\n".join(lines) + "\n"


def _render_markdown_generic(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _winner_cell(winner: str | None, a: str, b: str) -> str:
    if winner == a:
        return f"**{a}**"
    if winner == b:
        return f"**{b}**"
    if winner == "tie":
        return "tie"
    return "-"


def _format_exposure_list(items: Any) -> str:
    if not items:
        return "-"
    return "; ".join(f"{row[0]} {float(row[1]) * 100:.1f}%" for row in items)


def _auto_recommendation(a: str, b: str, comp: dict[str, Any]) -> str:
    correlation = safe_float(comp.get("daily_return_correlation"))
    overlap = safe_float(comp.get("position_overlap_ratio"))
    if correlation is not None and correlation > 0.85:
        return "两侧日收益相关性偏高，建议至少有一方调整因子组合以保持策略可分辨度（仅建议，非投资指令）。"
    if overlap is not None and overlap > 0.7:
        return "持仓重叠度较高，落后方可考虑放大与对手不一致的因子权重，避免变成事实上的同一策略。"
    spread = safe_float(comp.get("spread_cumulative_return"))
    if spread is not None and abs(spread) > 0.03:
        leader = a if spread > 0 else b
        return f"`{leader}` 本月领先 {abs(spread)*100:.1f}%。落后方可参考其因子组合与行业暴露，并在自身风格内做有限调整。"
    return "本月双方差距与重叠均在合理范围；保持各自风格观察下一周期。"


def _upsert_leaderboard(path: Path, payload: dict[str, Any]) -> None:
    month = payload["review_period"]
    comp = payload.get("comparison") or {}
    agents_block = payload.get("agents") or {}

    claude_block = agents_block.get("claude") or {}
    codex_block = agents_block.get("codex") or {}
    row = {
        "month": month,
        "claude_return": _round(claude_block.get("cumulative_return")),
        "codex_return": _round(codex_block.get("cumulative_return")),
        "winner_return": comp.get("winner_cumulative_return"),
        "claude_ir": _round(claude_block.get("information_ratio")),
        "codex_ir": _round(codex_block.get("information_ratio")),
        "winner_ir": comp.get("winner_information_ratio"),
        "generated_at": payload.get("generated_at"),
    }

    if path.exists():
        # month must be str ("YYYY-MM") for the != filter below to match
        df = pd.read_csv(path, dtype={"month": str})
        df = df[df["month"] != month]
        df = pd.concat([df, pd.DataFrame([row], columns=LEADERBOARD_COLUMNS)], ignore_index=True)
    else:
        df = pd.DataFrame([row], columns=LEADERBOARD_COLUMNS)
    df = df.sort_values("month")
    write_dataframe_csv_atomic(df, path, index=False)


def _round(value: Any, digits: int = 6) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return round(float(number), digits)
