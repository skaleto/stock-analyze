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
import html
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ._dashboard_assets import BASE_CSS, NAV_CSS, render_nav_html
from .beginner_dashboard import write_beginner_views
from . import competition
from .competition import resolve_agent_paths, resolve_market_paths
from .dashboard_finance import (
    InstrumentDataError,
    build_activity,
    build_history_metrics,
    build_strategy_profile,
    enrich_rows,
    instrument_metadata,
    read_instrument_history,
    read_latest_factor_values,
)
from .markets.cn_qdii_etf.lookthrough import (
    build_portfolio_lookthrough,
    profile_for_index,
)
from .strategy_comparison import build_strategy_comparison
from .strategy_registry import PAIR_SLOTS, StrategyRegistryInvalid, load_strategy_registry
from .utils import (
    dashboard_fragment_path,
    ensure_dirs,
    format_pct,
    safe_float,
    today as _today,
)


AGENT_COLORS = {
    # Dark Bloomberg palette — must agree with _dashboard_assets BASE_CSS
    # token values (--claude / --codex).
    "claude": "#f59e0b",
    "codex": "#06b6d4",
}
DEFAULT_AGENT_ORDER = ("claude", "codex")
DEFAULT_MARKETS = tuple(competition.MARKETS)
MARKET_LABELS = {
    "a_share": "A股",
    "hk": "港股",
    "us": "美股",
    "cn_qdii_etf": "跨境ETF",
}
MARKET_CURRENCY = {
    "a_share": "¥",
    "hk": "HK$",
    "us": "$",
    "cn_qdii_etf": "¥",
}
MARKET_INITIAL_CASH = {
    "a_share": 1_000_000.0,
    "hk": 1_000_000.0,
    "us": 150_000.0,
    "cn_qdii_etf": 1_000_000.0,
}


def _strategy_labels(root: Path) -> dict[str, str]:
    try:
        registry = load_strategy_registry(root)
        return {
            agent: str(registry["slots"][agent].get("label") or agent)
            for agent in PAIR_SLOTS
        }
    except StrategyRegistryInvalid:
        return {"claude": "稳健防守", "codex": "趋势进攻"}


@dataclass
class DashboardAgentPaths:
    market: str
    agent_id: str
    repo_root: Path
    data_dir: Path
    reports_dir: Path
    config_path: Path


class DashboardDataError(RuntimeError):
    """An existing dashboard artifact could not be parsed safely."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"dashboard data source is unreadable: {source}")


def _read_selection_snapshot(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "selection_snapshot.json"
    if not path.exists():
        return {
            "schema_version": 1,
            "as_of": None,
            "universe_hash": None,
            "scopes": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DashboardDataError("selection_snapshot") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("scopes", {}), dict):
        raise DashboardDataError("selection_snapshot")
    return payload


# _today is imported from .utils as a single canonical helper.
# Tests can still patch `stock_analyze.dashboard_aggregator._today` —
# that targets the local alias name in this module's namespace.


# Expected daily / weekly pipeline tasks. Order = display order.
_DAILY_TASK_ROWS = [
    ("prepare-market-data", "ECS 17:25 Mon-Fri 拉数据 → 触发 daily agents",
     "_pipeline_market_data"),
    ("stock-analyze-claude-daily", "执行待发订单 + 更新 NAV",
     "_pipeline_agent_daily:claude"),
    ("stock-analyze-codex-daily", "执行待发订单 + 更新 NAV",
     "_pipeline_agent_daily:codex"),
    ("aggregate-dashboard (OnSuccess)", "agent daily 完成后自动刷新 competition 聚合页",
     "_pipeline_aggregate_dashboard"),
]
_WEEKLY_TASK_ROWS = [
    ("stock-analyze-weekly-trigger", "ECS Sat 10:00 触发 weekly agents（用周五 cache）",
     "_pipeline_weekly_trigger"),
    ("stock-analyze-claude-weekly", "生成下周一执行的 pending orders + 周报",
     "_pipeline_agent_weekly:claude"),
    ("stock-analyze-codex-weekly", "生成下周一执行的 pending orders + 周报",
     "_pipeline_agent_weekly:codex"),
]


def _runs_today(repo: Path, agent: str, command: str, today: "_dt.date") -> dict | None:
    """Return the latest run row for (agent, command) started today, or None."""
    csv = repo / "data" / "a_share" / agent / "runs.csv"
    if not csv.exists():
        return None
    import pandas as _pd
    try:
        # runs.csv has textually-coded fields (run_id, hash, ISO timestamps)
        df = _pd.read_csv(csv, dtype={
            "run_id": str, "command": str, "as_of": str,
            "started_at": str, "finished_at": str, "status": str,
            "error_summary": str, "config_hash": str, "code_version": str,
        })
    except Exception:  # noqa: BLE001
        return None
    if df.empty:
        return None
    today_iso = today.isoformat()
    today_rows = df[
        (df["command"] == command)
        & df["started_at"].astype(str).str.startswith(today_iso)
        & (df["status"] != "running")  # final state only
    ]
    if today_rows.empty:
        return None
    return today_rows.iloc[-1].to_dict()


def _rollup_7d(repo: Path, agent: str, command: str, today: "_dt.date") -> tuple[int, int]:
    """Return (success_count, failed_count) for (agent, command) in last 7 days."""
    csv = repo / "data" / "a_share" / agent / "runs.csv"
    if not csv.exists():
        return (0, 0)
    import pandas as _pd
    try:
        df = _pd.read_csv(csv, dtype={
            "run_id": str, "command": str, "as_of": str,
            "started_at": str, "finished_at": str, "status": str,
            "error_summary": str, "config_hash": str, "code_version": str,
        })
    except Exception:  # noqa: BLE001
        return (0, 0)
    if df.empty:
        return (0, 0)
    from datetime import timedelta as _td
    cutoff = (today - _td(days=7)).isoformat()
    df = df[(df["command"] == command) & (df["status"] != "running")]
    df = df[df["started_at"].astype(str) >= cutoff]
    success = int((df["status"] == "success").sum())
    failed = int((df["status"] == "failed").sum())
    return (success, failed)


def _status_cell(row: dict | None) -> str:
    """Render today's-status cell HTML."""
    if row is None:
        return '<span class="pending">⏸ 未跑</span>'
    if row.get("status") == "success":
        dur = row.get("duration_ms")
        dur_str = f" {float(dur)/1000:.1f}s" if dur else ""
        ts = row.get("started_at", "")[-8:]  # HH:MM:SS
        return f'<span class="ok">✓ {ts}{dur_str}</span>'
    err = row.get("error_summary", "") or ""
    err_short = (err[:60] + "…") if len(err) > 60 else err
    return f'<span class="fail">✗ {err_short}</span>'


def _market_data_today(repo: Path, today: "_dt.date") -> dict | None:
    """Read data/shared/market_snapshot_<today>.json and synthesise a row."""
    path = repo / "data" / "shared" / f"market_snapshot_{today.isoformat()}.json"
    if not path.exists():
        return None
    try:
        import json as _json
        snap = _json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    return {
        "status": snap.get("status", "unknown"),
        "duration_ms": snap.get("duration_ms"),
        "started_at": snap.get("started_at", ""),
        "error_summary": "; ".join(
            e.get("source", "?") for e in (snap.get("errors") or [])[:2]
        ),
    }


def _aggregate_dashboard_today(repo: Path, today: "_dt.date") -> dict | None:
    """Stat the competition dashboard HTML's mtime; if today, treat as ✓."""
    p = repo / "reports" / "competition" / "dashboard.html"
    if not p.exists():
        return None
    import datetime as _dt
    mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
    if mtime.date() != today:
        return None
    return {
        "status": "success",
        "duration_ms": None,
        "started_at": mtime.isoformat(timespec="seconds"),
        "error_summary": "",
    }


def _recent_failures(log_path: Path | None, today: "_dt.date", limit: int = 5) -> list[str]:
    if log_path is None:
        log_path = Path("/opt/stock-analyze/logs/PIPELINE_FAILURES.log")
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text().split("\n")
    except Exception:  # noqa: BLE001
        return []
    failures = [ln for ln in lines if "\tFAILED\t" in ln]
    return failures[-limit:]


def _is_weekday(d: "_dt.date") -> bool:
    return d.weekday() < 5  # Mon-Fri


def _resolve_today_task(repo: Path, kind: str, today: "_dt.date") -> dict | None:
    if kind == "_pipeline_market_data":
        if not _is_weekday(today):
            return None  # market-data timer doesn't fire on weekends
        return _market_data_today(repo, today)
    if kind.startswith("_pipeline_agent_daily:"):
        agent = kind.split(":", 1)[1]
        if not _is_weekday(today):
            return None
        return _runs_today(repo, agent, "run-daily", today)
    if kind.startswith("_pipeline_agent_weekly:"):
        agent = kind.split(":", 1)[1]
        if today.weekday() < 5:
            return None  # weekly-trigger only fires Sat
        return _runs_today(repo, agent, "run-weekly", today)
    if kind == "_pipeline_weekly_trigger":
        if today.weekday() < 5:
            return None
        # No first-class "trigger" row; check if either agent ran weekly today
        for ag in ("claude", "codex"):
            row = _runs_today(repo, ag, "run-weekly", today)
            if row is not None:
                return row
        return None
    if kind == "_pipeline_aggregate_dashboard":
        return _aggregate_dashboard_today(repo, today)
    return None


def render_pipeline_status_panel(
    repo_root: Path | str,
    *,
    pipeline_failures_log: Path | None = None,
) -> str:
    """Render the pipeline-status panel: today's task list + 7-day rollup.

    Data sources:
      - data/<agent>/runs.csv (per-agent service runs)
      - data/shared/market_snapshot_<today>.json (pipeline data fetch)
      - reports/competition/dashboard.html mtime (aggregator refresh)
      - logs/PIPELINE_FAILURES.log (recent failures from OnFailure hooks)

    Refresh frequency: as fast as dashboard regeneration (after each agent
    service via aggregate-dashboard.service OnSuccess hook).
    """
    root = Path(repo_root)
    today = _today()
    today_label = today.isoformat()
    today_dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today.weekday()]

    lines = [
        f'<div class="panel pipeline-status">',
        f'  <h3>📋 Pipeline 任务清单 · {today_label} ({today_dow})</h3>',
        '  <table class="pipeline-task-table">',
        '    <thead><tr>'
        '<th>任务</th><th>含义</th>'
        '<th>今天</th><th>近 7 日</th>'
        '</tr></thead>',
        '    <tbody>',
    ]

    for task_name, task_desc, kind in _DAILY_TASK_ROWS + _WEEKLY_TASK_ROWS:
        row = _resolve_today_task(root, kind, today)
        status_html = _status_cell(row)

        # 7-day rollup
        if kind.startswith("_pipeline_agent_daily:"):
            ag = kind.split(":", 1)[1]
            s, f = _rollup_7d(root, ag, "run-daily", today)
        elif kind.startswith("_pipeline_agent_weekly:"):
            ag = kind.split(":", 1)[1]
            s, f = _rollup_7d(root, ag, "run-weekly", today)
        else:
            s, f = (0, 0)  # not tracked in runs.csv

        rollup_html = (
            f'<span class="rollup">✓{s} ✗{f}</span>'
            if (s + f) > 0 else '<span class="rollup">—</span>'
        )

        lines.append(
            f'      <tr>'
            f'<td><code>{task_name}</code></td>'
            f'<td>{task_desc}</td>'
            f'<td>{status_html}</td>'
            f'<td>{rollup_html}</td>'
            f'</tr>'
        )

    lines.append('    </tbody>')
    lines.append('  </table>')

    # Recent failures (if any)
    failures = _recent_failures(pipeline_failures_log, today)
    if failures:
        lines.append('  <h4>⚠️ 最近 PIPELINE_FAILURES.log</h4>')
        lines.append('  <ul class="recent-failures">')
        for f in failures:
            lines.append(f'    <li><code>{f}</code></li>')
        lines.append('  </ul>')

    lines.append('</div>')
    return "\n".join(lines)


def render_sentiment_comparison_panel(repo_root: Path | str) -> str:
    """Render the cross-strategy, multi-market sentiment comparison panel."""
    from stock_analyze.markets.a_share.alt_factors import sentiment as _alt_sent

    root = Path(repo_root)
    labels = _strategy_labels(root)
    market_rows: list[str] = []
    has_complete_pair = False
    for market in DEFAULT_MARKETS:
        label = MARKET_LABELS.get(market, market)
        claude_rows = _alt_sent.load_sentiment_history(
            "claude", root, last_n=26, market=market,
        )
        codex_rows = _alt_sent.load_sentiment_history(
            "codex", root, last_n=26, market=market,
        )
        if not claude_rows or not codex_rows:
            market_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                '<td colspan="5">尚无足够数据：至少需要两个策略都有记录。</td>'
                '</tr>'
            )
            continue

        has_complete_pair = True
        latest_c = claude_rows[-1]
        latest_x = codex_rows[-1]
        diff = latest_c.score - latest_x.score
        market_rows.append(
            '<tr>'
            f'<td>{html.escape(label)}</td>'
            f'<td>{latest_c.week_end.isoformat()}</td>'
            f'<td>{html.escape(labels["claude"])} {latest_c.score:+.2f} / {latest_c.confidence:.2f}</td>'
            f'<td>{html.escape(labels["codex"])} {latest_x.score:+.2f} / {latest_x.confidence:.2f}</td>'
            f'<td>{diff:+.2f}</td>'
            f'<td>{html.escape("; ".join(latest_c.drivers[:2] + latest_x.drivers[:2]))}</td>'
            '</tr>'
        )

    if not has_complete_pair:
        return (
            '<div class="panel">\n'
            f'  <h3>{html.escape(labels["claude"])} vs {html.escape(labels["codex"])} 市场情绪（过去 26 周）</h3>\n'
            '  <table>\n'
            f'    <tr><th>市场</th><th>week_end</th><th>{html.escape(labels["claude"])} score/conf</th>'
            f'<th>{html.escape(labels["codex"])} score/conf</th><th>差值</th><th>关键驱动</th></tr>\n'
            f'    {"".join(market_rows)}\n'
            '  </table>\n'
            '</div>'
        )

    return (
        f'<div class="panel">\n'
        f'  <h3>{html.escape(labels["claude"])} vs {html.escape(labels["codex"])} 市场情绪（过去 26 周）</h3>\n'
        f'  <table>\n'
        f'    <tr><th>市场</th><th>week_end</th><th>{html.escape(labels["claude"])} score/conf</th>'
        f'<th>{html.escape(labels["codex"])} score/conf</th><th>差值</th><th>关键驱动</th></tr>\n'
        f'    {"".join(market_rows)}\n'
        f'  </table>\n'
        f'</div>'
    )


def _normalize_markets(market: str, markets: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = list(markets) if markets is not None else ([market] if market != "all" else list(DEFAULT_MARKETS))
    if not requested:
        requested = list(DEFAULT_MARKETS)
    selected: list[str] = []
    for item in requested:
        if item == "all":
            for known in DEFAULT_MARKETS:
                if known not in selected:
                    selected.append(known)
            continue
        if item not in competition.MARKETS:
            raise competition.UnknownMarket(item)
        if item not in selected:
            selected.append(item)
    return selected


def _has_runtime_data(data_dir: Path) -> bool:
    return any(
        (data_dir / name).exists()
        for name in (
            "daily_nav.csv",
            "runs.csv",
            "pending_orders.json",
            "performance_summary.json",
            "positions.csv",
            "latest_signals.csv",
        )
    )


def _resolve_dashboard_paths(market: str, agent: str, root: Path) -> DashboardAgentPaths:
    paths = resolve_market_paths(market, agent, repo_root=root)
    data_dir = paths.data_dir
    reports_dir = paths.reports_dir
    if market == "a_share" and not _has_runtime_data(data_dir):
        legacy_data = root / "data" / agent
        legacy_reports = root / "reports" / agent
        if _has_runtime_data(legacy_data):
            data_dir = legacy_data
            if legacy_reports.exists():
                reports_dir = legacy_reports
    return DashboardAgentPaths(
        market=market,
        agent_id=agent,
        repo_root=root,
        data_dir=data_dir,
        reports_dir=reports_dir,
        config_path=paths.config_path,
    )


def _build_market_paths(
    markets: list[str],
    agents: list[str],
    root: Path,
) -> dict[str, dict[str, DashboardAgentPaths]]:
    return {
        market: {
            agent: _resolve_dashboard_paths(market, agent, root)
            for agent in agents
            if (root / "configs" / "agents" / f"{agent}_{market}.yaml").exists()
        }
        for market in markets
    }


def _agents_for_markets(markets: list[str], root: Path) -> list[str]:
    ordered: list[str] = []
    for preferred in DEFAULT_AGENT_ORDER:
        if any(preferred in competition.list_agents_for_market(market, root) for market in markets):
            ordered.append(preferred)
    for market in markets:
        for agent in competition.list_agents_for_market(market, root):
            if agent not in ordered:
                ordered.append(agent)
    return ordered


def _run_status_data(row: dict[str, Any] | None, *, missing: str = "missing") -> dict[str, Any]:
    if row is None:
        return {"status": missing, "started_at": None, "finished_at": None, "error_summary": None}
    return {
        "status": _none_if_blank(row.get("status")) or "unknown",
        "started_at": _none_if_blank(row.get("started_at")),
        "finished_at": _none_if_blank(row.get("finished_at")),
        "error_summary": _none_if_blank(row.get("error_summary")),
    }


def _none_if_blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value == "":
        return None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return value
    if isinstance(missing, bool) and missing:
        return None
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    value = _none_if_blank(value)
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError, AttributeError):
            pass
    return value


def _weekly_report_href(market: str, agent: str, reports_dir: Path) -> str | None:
    if not (reports_dir / "weekly_report.md").exists():
        return None
    return f"/{market}/{agent}/weekly_report.md"


def _coerce_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _limited_csv_rows(
    path: Path,
    *,
    source: str,
    required_columns: list[str],
    text_columns: list[str],
    numeric_columns: list[str],
    limit: int,
    sort_by: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        df = pd.read_csv(
            path,
            dtype={column: str for column in text_columns},
            keep_default_na=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise DashboardDataError(source) from exc
    if any(column not in df.columns for column in required_columns):
        raise DashboardDataError(source)
    if df.empty:
        return []
    df = _coerce_numeric_columns(df, numeric_columns)
    if sort_by:
        existing = [column for column in sort_by if column in df.columns]
        if existing:
            df = df.sort_values(existing)
    if limit > 0:
        df = df.tail(limit)
    return _json_safe(df.to_dict(orient="records"))


def _collapse_run_transitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one logical run per run_id, preferring its terminal transition."""
    selected: dict[str, tuple[tuple[int, str, int], dict[str, Any]]] = {}
    order: list[str] = []
    for index, row in enumerate(rows):
        run_id = str(row.get("run_id") or "").strip()
        key = run_id or f"__missing_run_id_{index}"
        status = str(row.get("status") or "").strip().lower()
        finished_at = str(row.get("finished_at") or "")
        is_terminal = int(bool(finished_at) or status not in {"", "running"})
        rank = (is_terminal, finished_at, index)
        if key not in selected:
            order.append(key)
        if key not in selected or rank >= selected[key][0]:
            selected[key] = (rank, row)
    return [selected[key][1] for key in order]


def _read_fund_name_lookup(root: Path, market: str) -> dict[str, str]:
    cache_dir = root / "data" / market / "shared" / "cache"
    path = next(
        (
            candidate
            for candidate in (
                cache_dir / "fund_basic_E_v2.csv",
                cache_dir / "fund_basic_E.csv",
            )
            if candidate.exists() and candidate.stat().st_size > 0
        ),
        None,
    )
    if path is None:
        return {}
    try:
        df = pd.read_csv(path, dtype={"ts_code": str, "name": str}, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001
        raise DashboardDataError("fund_basic") from exc
    if df.empty or "ts_code" not in df.columns or "name" not in df.columns:
        return {}
    return {
        str(row["ts_code"]): str(row["name"])
        for row in df[["ts_code", "name"]].to_dict(orient="records")
        if str(row.get("ts_code") or "") and str(row.get("name") or "")
    }


def _flatten_pending_orders(data_dir: Path, *, name_lookup: dict[str, str] | None = None) -> list[dict[str, Any]]:
    path = data_dir / "pending_orders.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise DashboardDataError("pending_orders") from exc
    if isinstance(payload, dict):
        raw_orders = payload.get("orders") or []
    elif isinstance(payload, list):
        raw_orders = payload
    else:
        raise DashboardDataError("pending_orders")

    orders: list[dict[str, Any]] = []
    for item in raw_orders:
        if not isinstance(item, dict):
            continue
        nested = item.get("orders")
        if isinstance(nested, list):
            parent = {key: value for key, value in item.items() if key != "orders"}
            for order in nested:
                if isinstance(order, dict):
                    merged = dict(parent)
                    merged.update(order)
                    orders.append(merged)
        else:
            orders.append(item)
    return [_normalise_order_row(order, name_lookup=name_lookup or {}) for order in orders]


def _normalise_order_row(order: dict[str, Any], *, name_lookup: dict[str, str]) -> dict[str, Any]:
    numeric_fields = {
        "shares",
        "price",
        "target_weight",
        "target_value",
        "current_weight",
        "score",
        "gross_amount",
        "commission",
        "stamp_tax",
        "slippage",
        "net_amount",
    }
    row: dict[str, Any] = {}
    for key, value in order.items():
        if key in numeric_fields:
            row[key] = safe_float(value)
        else:
            row[key] = _none_if_blank(value)
    row["side"] = str(row.get("side") or "").lower() or None
    code = str(row.get("code") or "")
    if code and not row.get("name"):
        row["name"] = name_lookup.get(code)
    if not row.get("execute_after"):
        row["execute_after"] = row.get("trade_date")
    return _json_safe(row)


def _read_nav_detail(data_dir: Path, market: str) -> dict[str, Any]:
    path = data_dir / "daily_nav.csv"
    empty = {
        "latest": None,
        "series": [],
        "accounts": [],
        "benchmark_codes": [],
        "benchmark_label": "基准",
    }
    if not path.exists() or path.stat().st_size == 0:
        return empty
    try:
        df = pd.read_csv(
            path,
            dtype={
                "date": str,
                "account_id": str,
                "benchmark_code": str,
                "benchmark_date": str,
                "notes": str,
            },
            keep_default_na=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise DashboardDataError("daily_nav") from exc
    if df.empty or "date" not in df.columns or "total_value" not in df.columns:
        return empty

    numeric_columns = ["cash", "market_value", "total_value", "benchmark_close"]
    for column in ("cash", "market_value"):
        if column not in df.columns:
            df[column] = 0.0
    df = _coerce_numeric_columns(df, numeric_columns)
    baseline = MARKET_INITIAL_CASH.get(market, 1.0)
    grouped = (
        df.groupby("date", as_index=False)[["cash", "market_value", "total_value"]]
        .sum(numeric_only=True)
        .sort_values("date")
    )
    benchmark_by_date, benchmark_codes_all = _composite_benchmark_series(df)
    series: list[dict[str, Any]] = []
    for row in grouped.to_dict(orient="records"):
        total_value = safe_float(row.get("total_value"))
        date_key = str(row.get("date"))
        series.append(
            {
                "date": date_key,
                "cash": safe_float(row.get("cash")),
                "market_value": safe_float(row.get("market_value")),
                "total_value": total_value,
                "total_value_display": _format_market_money(total_value, market),
                "return": (total_value / baseline - 1.0) if total_value is not None and baseline else None,
                "return_display": format_pct(
                    (total_value / baseline - 1.0) if total_value is not None and baseline else None
                ),
                **benchmark_by_date.get(
                    date_key,
                    {"benchmark_return": None, "benchmark_coverage": 0.0},
                ),
            }
        )
    latest_date = series[-1]["date"] if series else None
    latest_row = None
    if latest_date is not None:
        latest_rows = df[df["date"] == latest_date]
        benchmark_codes = sorted(
            {
                str(value)
                for value in latest_rows.get("benchmark_code", pd.Series(dtype=str)).tolist()
                if _none_if_blank(value) is not None
            }
        )
        benchmark_code = None
        benchmark_close = None
        benchmark_date = None
        if len(benchmark_codes) == 1:
            benchmark_code = benchmark_codes[0]
            benchmark_rows = latest_rows[
                latest_rows["benchmark_code"].astype(str) == benchmark_code
            ]
            first = benchmark_rows.iloc[0].to_dict()
            benchmark_code = _none_if_blank(first.get("benchmark_code"))
            benchmark_close = safe_float(first.get("benchmark_close"))
            benchmark_date = _none_if_blank(first.get("benchmark_date"))
        latest_row = {
            **series[-1],
            "benchmark_codes": benchmark_codes,
            "benchmark_code": benchmark_code,
            "benchmark_close": benchmark_close,
            "benchmark_date": benchmark_date,
        }
    else:
        benchmark_codes = []
    accounts = _json_safe(df.tail(100).to_dict(orient="records"))
    return {
        "latest": latest_row,
        "series": _json_safe(series[-260:]),
        "accounts": accounts,
        "benchmark_codes": benchmark_codes,
        "benchmark_label": "组合基准" if len(benchmark_codes_all) > 1 else (
            benchmark_codes_all[0] if benchmark_codes_all else "基准"
        ),
    }


def _composite_benchmark_series(
    frame: pd.DataFrame,
) -> tuple[dict[str, dict[str, float | None]], list[str]]:
    required = {"date", "account_id", "total_value", "benchmark_code", "benchmark_close"}
    if not required.issubset(frame.columns):
        return {}, []
    benchmark = frame[list(required)].copy()
    benchmark["benchmark_close"] = pd.to_numeric(
        benchmark["benchmark_close"], errors="coerce"
    )
    benchmark["total_value"] = pd.to_numeric(benchmark["total_value"], errors="coerce")
    benchmark = benchmark[
        benchmark["benchmark_code"].astype(str).str.len().gt(0)
        & benchmark["benchmark_close"].notna()
        & benchmark["benchmark_close"].gt(0)
    ].sort_values(["account_id", "date"])
    if benchmark.empty:
        return {}, []
    bases: dict[str, tuple[float, float]] = {}
    for account_id, rows in benchmark.groupby("account_id", sort=False):
        first = rows.iloc[0]
        close = safe_float(first.get("benchmark_close"))
        weight = safe_float(first.get("total_value"))
        if close and weight and weight > 0:
            bases[str(account_id)] = (close, weight)
    total_weight = sum(weight for _, weight in bases.values())
    if not bases or total_weight <= 0:
        return {}, []
    output: dict[str, dict[str, float | None]] = {}
    for date_value, rows in benchmark.groupby("date", sort=True):
        weighted_return = 0.0
        available_weight = 0.0
        for row in rows.to_dict(orient="records"):
            base = bases.get(str(row.get("account_id") or ""))
            close = safe_float(row.get("benchmark_close"))
            if base is None or close is None:
                continue
            base_close, weight = base
            weighted_return += (close / base_close - 1.0) * weight
            available_weight += weight
        output[str(date_value)] = {
            "benchmark_return": weighted_return / available_weight if available_weight else None,
            "benchmark_coverage": available_weight / total_weight if total_weight else 0.0,
        }
    codes = sorted(
        {
            str(value)
            for value in benchmark["benchmark_code"].tolist()
            if str(value)
        }
    )
    return output, codes


def build_dashboard_detail_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = 200,
) -> dict[str, Any]:
    """Return runtime detail data for one dashboard market/agent selection."""

    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    if agent not in competition.list_agents_for_market(market, root):
        raise competition.UnknownAgent(f"unknown_agent:{agent}; market={market}")
    paths = _resolve_dashboard_paths(market, agent, root)

    orders = enrich_rows(
        market,
        _flatten_pending_orders(
            paths.data_dir,
            name_lookup=_read_fund_name_lookup(root, market),
        ),
        repo_root=root,
    )
    positions_all = enrich_rows(
        market,
        _limited_csv_rows(
            paths.data_dir / "positions.csv",
            source="positions",
            required_columns=["account_id", "code", "shares"],
            text_columns=[
                "account_id",
                "code",
                "name",
                "industry",
                "last_buy_date",
                "hold_since",
                "reason",
                "updated_at",
            ],
            numeric_columns=[
                "shares",
                "available_shares",
                "avg_cost",
                "last_price",
                "market_value",
                "unrealized_pnl",
                "score",
            ],
            limit=0,
            sort_by=["account_id", "code"],
        ),
        repo_root=root,
    )
    trades_all = enrich_rows(
        market,
        _limited_csv_rows(
            paths.data_dir / "trades.csv",
            source="trades",
            required_columns=["trade_date", "account_id", "code", "side"],
            text_columns=["trade_date", "account_id", "code", "name", "side", "reason"],
            numeric_columns=[
                "shares",
                "price",
                "gross_amount",
                "commission",
                "stamp_tax",
                "slippage",
                "net_amount",
                "cash_after",
            ],
            limit=0,
            sort_by=["trade_date"],
        ),
        repo_root=root,
    )
    runs_all = _limited_csv_rows(
        paths.data_dir / "runs.csv",
        source="runs",
        required_columns=["run_id", "command", "started_at", "status"],
        text_columns=[
            "run_id",
            "command",
            "as_of",
            "started_at",
            "finished_at",
            "status",
            "error_summary",
            "config_hash",
            "code_version",
        ],
        numeric_columns=["duration_ms"],
        limit=0,
        sort_by=["started_at"],
    )
    runs_all = _collapse_run_transitions(runs_all)
    try:
        strategy = build_strategy_profile(paths.config_path, repo_root=root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DashboardDataError("strategy_overlay") from exc
    activity = build_activity(trades_all, orders)
    positions = positions_all[-limit:] if limit > 0 else positions_all
    trades = trades_all[-limit:] if limit > 0 else trades_all
    runs = runs_all[-limit:] if limit > 0 else runs_all
    report_path = paths.reports_dir / "weekly_report.md"
    report_markdown = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    position_value = sum(
        safe_float(row.get("market_value")) or 0.0
        for row in positions_all
    )
    selection = _read_selection_snapshot(paths.data_dir)
    if market == "cn_qdii_etf":
        lookthrough_source = "positions" if positions_all else "planned_orders"
        lookthrough_rows = positions_all or [
            row for row in orders if str(row.get("side") or "").lower() == "buy"
        ]
        lookthrough = build_portfolio_lookthrough(
            lookthrough_rows,
            source=lookthrough_source,
        )
    else:
        lookthrough = {}
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": market,
        "market_label": MARKET_LABELS.get(market, market),
        "currency": MARKET_CURRENCY.get(market, ""),
        "agent": agent,
        "strategy": strategy,
        "selection": selection,
        "lookthrough": lookthrough,
        "nav": _read_nav_detail(paths.data_dir, market),
        "activity": {"summary": {"total": len(activity)}, "rows": activity[:limit]},
        "orders": {
            "summary": {
                "total": len(orders),
                "buy": sum(1 for order in orders if order.get("side") == "buy"),
                "sell": sum(1 for order in orders if order.get("side") == "sell"),
            },
            "rows": orders[:limit],
        },
        "positions": {
            "summary": {
                "total": len(positions_all),
                "market_value": position_value,
                "market_value_display": _format_market_money(position_value, market),
            },
            "rows": positions,
        },
        "trades": {"summary": {"total": len(trades_all)}, "rows": trades},
        "runs": {"summary": {"total": len(runs_all)}, "rows": list(reversed(runs))},
        "weekly_report": {
            "exists": report_path.exists(),
            "href": _weekly_report_href(market, agent, paths.reports_dir),
            "markdown": report_markdown[:12000],
        },
    }
    return _json_safe(payload)


def build_dashboard_instrument_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    code: str,
) -> dict[str, Any]:
    """Return cached OHLCV and explanatory metrics for one security."""

    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    if agent not in competition.list_agents_for_market(market, root):
        raise competition.UnknownAgent(f"unknown_agent:{agent}; market={market}")
    paths = _resolve_dashboard_paths(market, agent, root)
    normalized, candles, warning = read_instrument_history(root, market, code)
    name = _instrument_name(root, market, normalized)
    factor_values = (
        read_latest_factor_values(root, agent, normalized)
        if market == "a_share"
        else {}
    )
    metrics = build_history_metrics(candles, factor_values)
    related = _limited_csv_rows(
        paths.data_dir / "trades.csv",
        source="trades",
        required_columns=["trade_date", "account_id", "code", "side"],
        text_columns=["trade_date", "account_id", "code", "name", "side", "reason"],
        numeric_columns=[
            "shares",
            "price",
            "gross_amount",
            "commission",
            "stamp_tax",
            "slippage",
            "net_amount",
            "cash_after",
        ],
        limit=0,
        sort_by=["trade_date"],
    )
    digits = normalized.split(".", 1)[0]
    related = enrich_rows(
        market,
        [row for row in related if str(row.get("code") or "").split(".", 1)[0].zfill(6) == digits],
        repo_root=root,
    )
    latest = None
    if candles:
        latest = dict(candles[-1])
        previous_close = safe_float(candles[-2].get("close")) if len(candles) > 1 else None
        close = safe_float(latest.get("close"))
        latest["change_pct"] = (
            close / previous_close - 1.0
            if close is not None and previous_close not in {None, 0}
            else None
        )
    metadata = instrument_metadata(market, normalized, name, repo_root=root)
    underlying = (
        profile_for_index(str(metadata.get("index_key") or ""))
        if market == "cn_qdii_etf"
        else None
    )
    return _json_safe(
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "market": market,
            "agent": agent,
            "instrument": metadata,
            "underlying": underlying,
            "latest": latest,
            "candles": candles,
            "metrics": metrics,
            "related_trades": list(reversed(related[-50:])),
            "warning": warning,
        }
    )


def _instrument_name(root: Path, market: str, code: str) -> str:
    if market == "cn_qdii_etf":
        return _read_fund_name_lookup(root, market).get(code, code)
    cache = root / "data" / "shared" / "cache"
    candidates = sorted(cache.glob("spot_*.csv"))
    if not candidates:
        return code
    try:
        frame = pd.read_csv(candidates[-1], dtype=str, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001
        raise InstrumentDataError("instrument_name") from exc
    code_column = next((item for item in ("code", "代码", "ts_code") if item in frame.columns), None)
    name_column = next((item for item in ("name", "名称", "股票简称") if item in frame.columns), None)
    if code_column is None or name_column is None:
        return code
    digits = code.split(".", 1)[0]
    rows = frame[frame[code_column].astype(str).str.split(".").str[0].str.zfill(6) == digits]
    return str(rows.iloc[0][name_column]) if not rows.empty else code


def _monthly_status_data(root: Path, market: str) -> dict[str, Any]:
    if market != "a_share":
        return {"status": "not_configured", "href": None, "label": None}
    reports_dir = root / "reports" / "competition"
    files = sorted(reports_dir.glob("monthly_review_*.md"))
    if not files:
        return {"status": "missing", "href": None, "label": None}
    latest = files[-1]
    month = latest.stem.replace("monthly_review_", "")
    return {"status": "success", "href": f"/competition/{latest.name}", "label": month}


def _latest_sentiment_rows(root: Path) -> list[dict[str, Any]]:
    from stock_analyze.markets.a_share.alt_factors import sentiment as _alt_sent

    rows: list[dict[str, Any]] = []
    for market in DEFAULT_MARKETS:
        item: dict[str, Any] = {
            "market": market,
            "market_label": MARKET_LABELS.get(market, market),
            "agents": {},
            "score_diff": None,
            "drivers": [],
        }
        latest_by_agent = {}
        for agent in DEFAULT_AGENT_ORDER:
            history = _alt_sent.load_sentiment_history(agent, root, last_n=26, market=market)
            if not history:
                continue
            latest = history[-1]
            latest_by_agent[agent] = latest
            item["agents"][agent] = {
                "week_end": latest.week_end.isoformat(),
                "score": latest.score,
                "confidence": latest.confidence,
                "drivers": list(latest.drivers),
                "sources": list(latest.sources),
            }
        if "claude" in latest_by_agent and "codex" in latest_by_agent:
            item["score_diff"] = latest_by_agent["claude"].score - latest_by_agent["codex"].score
            item["drivers"] = (
                list(latest_by_agent["claude"].drivers[:2])
                + list(latest_by_agent["codex"].drivers[:2])
            )
        rows.append(item)
    return rows


def build_dashboard_summary_data(
    *,
    repo_root: str | Path | None = None,
    markets: list[str] | None = None,
    agents: list[str] | None = None,
) -> dict[str, Any]:
    """Return the structured tri-market dashboard data used by the UI.

    The HTML page remains a static shell/fallback, while ``serve-dashboard``
    can expose this payload at ``/api/dashboard/summary.json`` so the page can
    fetch fresh data from disk without regenerating HTML.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    selected_markets = _normalize_markets("all", markets)
    selected_agents = agents or _agents_for_markets(selected_markets, root)
    paths_by_market = _build_market_paths(selected_markets, selected_agents, root)
    try:
        strategy_registry = load_strategy_registry(root)
    except StrategyRegistryInvalid:
        strategy_registry = {
            "season_id": "legacy",
            "name": "双策略对抗",
            "effective_date": "1970-01-01",
            "factor_distance_floor": 0.0,
            "slots": {
                "claude": {
                    "label": "稳健防守",
                    "description": "价值质量、低波与低换手",
                    "color": "#d6a84b",
                },
                "codex": {
                    "label": "趋势进攻",
                    "description": "动量成长与主动换仓",
                    "color": "#22d3ee",
                },
            },
        }
    market_payloads: list[dict[str, Any]] = []
    for market in selected_markets:
        agent_paths = paths_by_market.get(market, {})
        details = {
            agent: build_dashboard_detail_data(
                repo_root=root,
                market=market,
                agent=agent,
                limit=100_000,
            )
            for agent in selected_agents
            if agent in agent_paths
        }
        comparison = (
            build_strategy_comparison(market, details, registry=strategy_registry)
            if all(agent in details for agent in PAIR_SLOTS)
            else None
        )
        market_agents: list[dict[str, Any]] = []
        for agent in selected_agents:
            paths = agent_paths.get(agent)
            if paths is None:
                continue
            detail = details[agent]
            nav_latest = detail.get("nav", {}).get("latest") or {}
            latest = safe_float(nav_latest.get("total_value"))
            baseline = MARKET_INITIAL_CASH.get(market, 1.0)
            pending = _read_pending_summary(paths.data_dir)
            strategy = (
                comparison.get("strategies", {}).get(agent)
                if comparison is not None
                else None
            ) or {
                "agent": agent,
                "label": detail.get("strategy", {}).get("name") or agent,
                "strategy_id": detail.get("strategy", {}).get("strategy_id"),
                "strategy_name": detail.get("strategy", {}).get("name"),
            }
            market_agents.append(
                {
                    "agent": agent,
                    "strategy": strategy,
                    "nav": {
                        "latest": latest,
                        "latest_display": _format_market_money(latest, market),
                        "date": nav_latest.get("date"),
                        "return": (latest / baseline - 1.0) if latest is not None and baseline else None,
                        "return_display": format_pct(
                            (latest / baseline - 1.0) if latest is not None and baseline else None
                        ),
                    },
                    "decision": {
                        "href": f"/pro/{market}/{agent}.html",
                        "pending_orders": pending,
                        "weekly_report_href": _weekly_report_href(market, agent, paths.reports_dir),
                    },
                    "tasks": {
                        "daily": _run_status_data(_read_latest_run(paths.data_dir, "run-daily")),
                        "weekly": _run_status_data(_read_latest_run(paths.data_dir, "run-weekly")),
                    },
                }
            )
        market_payloads.append(
            {
                "market": market,
                "label": MARKET_LABELS.get(market, market),
                "currency": MARKET_CURRENCY.get(market, ""),
                "agents": market_agents,
                "comparison": comparison,
                "monthly": _monthly_status_data(root, market),
            }
        )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "markets": market_payloads,
        "sentiment": _latest_sentiment_rows(root),
    }
    return _json_safe(payload)


def generate_competition_dashboard(
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
    *,
    market: str = "a_share",
    markets: list[str] | None = None,
) -> Path:
    """Render and persist ``reports/competition/dashboard.html``."""

    root = Path(repo_root) if repo_root else Path.cwd()
    strategy_labels = _strategy_labels(root)
    selected_markets = _normalize_markets(market, markets)
    agents = agents or _agents_for_markets(selected_markets, root)
    paths_by_market = _build_market_paths(selected_markets, agents, root)
    primary_market = "a_share" if "a_share" in paths_by_market else selected_markets[0]
    paths_by_agent = paths_by_market.get(primary_market) or {
        agent: resolve_agent_paths(agent, repo_root=root) for agent in agents
    }
    primary_agents = [agent for agent in agents if agent in paths_by_agent]

    fragments = {agent: _read_fragment(paths_by_agent[agent].reports_dir) for agent in primary_agents}
    perf = {agent: _read_performance_summary(paths_by_agent[agent].data_dir) for agent in primary_agents}
    nav_panel = _build_nav_panel(paths_by_agent)
    leaderboard = _read_leaderboard(root / "data" / "competition" / "leaderboard.csv")
    monthly_links = _list_monthly_reviews(root / "reports" / "competition")
    positions_overlap = _compute_position_overlap(paths_by_agent)
    comparison_table = _build_comparison_table(perf)
    summary_cards = _render_summary_cards(perf, leaderboard, strategy_labels)
    nav_json = json.dumps(nav_panel, ensure_ascii=False)
    leaderboard_json = json.dumps(leaderboard, ensure_ascii=False)
    all_market_html = _render_all_market_observer(
        selected_markets,
        agents,
        paths_by_market,
        root,
        strategy_labels,
    )

    out_dir = root / "reports" / "competition"
    ensure_dirs(out_dir)
    out_path = out_dir / "dashboard.html"

    tabs_nav = _render_tabs_nav(primary_agents, strategy_labels)
    tab_sections = _render_tab_sections(
        primary_agents,
        fragments,
        summary_cards,
        comparison_table,
        positions_overlap,
        leaderboard,
        monthly_links,
        paths_by_agent,
        all_market_html=all_market_html,
        strategy_labels=strategy_labels,
    )

    html = _render_page(
        tabs_nav,
        tab_sections,
        nav_json,
        leaderboard_json,
        strategy_labels,
    )
    out_path.write_text(html, encoding="utf-8")
    summary_payload = build_dashboard_summary_data(
        repo_root=root,
        markets=selected_markets,
        agents=agents,
    )
    (out_dir / "dashboard-data.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    # Also render the beginner simplified view alongside the professional dashboard.
    # Best-effort: if beginner rendering fails (e.g. unexpected data shape), the
    # professional view must still be produced. We re-raise the error after a
    # warning so the failure surfaces in the run ledger.
    try:
        write_beginner_views(agents=primary_agents, repo_root=root)
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
        # month "YYYY-MM" string — defensive against int coercion
        df = pd.read_csv(path, dtype={"month": str})
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
            # Keep benchmark_code as str so '000300' isn't coerced to int 300
            df = pd.read_csv(nav_path, dtype={"benchmark_code": str})
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


def _read_latest_nav(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "daily_nav.csv"
    if not path.exists():
        return {"latest": None, "change_1d": None, "date": None}
    try:
        df = pd.read_csv(path, dtype={"date": str, "account_id": str})
    except Exception:  # noqa: BLE001
        return {"latest": None, "change_1d": None, "date": None}
    if df.empty or "total_value" not in df.columns or "date" not in df.columns:
        return {"latest": None, "change_1d": None, "date": None}
    grouped = df.groupby("date")["total_value"].sum().sort_index()
    if grouped.empty:
        return {"latest": None, "change_1d": None, "date": None}
    latest = safe_float(grouped.iloc[-1])
    prev = safe_float(grouped.iloc[-2]) if len(grouped) >= 2 else None
    change = (latest / prev - 1.0) if latest is not None and prev not in (None, 0) else None
    return {"latest": latest, "change_1d": change, "date": str(grouped.index[-1])}


def _format_market_money(value: float | None, market: str) -> str:
    if value is None:
        return "-"
    currency = MARKET_CURRENCY.get(market, "")
    if abs(value) >= 1_000_000:
        return f"{currency}{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{currency}{value / 1_000:.0f}K"
    return f"{currency}{value:.0f}"


def _read_latest_run(data_dir: Path, command: str) -> dict[str, Any] | None:
    path = data_dir / "runs.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:  # noqa: BLE001
        return None
    if df.empty or "command" not in df.columns:
        return None
    rows = df[(df["command"] == command) & (df.get("status", "") != "running")]
    if rows.empty:
        return None
    return rows.iloc[-1].to_dict()


def _status_badge(row: dict[str, Any] | None, *, missing: str = "未运行") -> str:
    if row is None:
        return f'<span class="pending">{html.escape(missing)}</span>'
    status = row.get("status") or "unknown"
    started = str(row.get("started_at") or "")[:19].replace("T", " ")
    if status == "success":
        return f'<span class="ok">OK {html.escape(started)}</span>'
    if status == "failed":
        err = str(row.get("error_summary") or "")
        return f'<span class="fail">失败 {html.escape(err[:48])}</span>'
    return f'<span class="pending">{html.escape(status)} {html.escape(started)}</span>'


def _read_pending_summary(data_dir: Path) -> dict[str, int]:
    path = data_dir / "pending_orders.json"
    if not path.exists():
        return {"total": 0, "buy": 0, "sell": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"total": 0, "buy": 0, "sell": 0}
    if isinstance(payload, dict):
        raw_orders = payload.get("orders") or []
    elif isinstance(payload, list):
        raw_orders = payload
    else:
        raw_orders = []
    orders: list[dict[str, Any]] = []
    for item in raw_orders:
        if isinstance(item, dict) and isinstance(item.get("orders"), list):
            orders.extend(order for order in item["orders"] if isinstance(order, dict))
        elif isinstance(item, dict):
            orders.append(item)
    buy = sum(1 for order in orders if str(order.get("side", "")).lower() == "buy")
    sell = sum(1 for order in orders if str(order.get("side", "")).lower() == "sell")
    return {"total": len(orders), "buy": buy, "sell": sell}


def _latest_weekly_report_link(market: str, agent: str, reports_dir: Path) -> str:
    path = reports_dir / "weekly_report.md"
    if not path.exists():
        return '<span class="pending">无周报</span>'
    if reports_dir.parent.name == "reports":
        href = f"/{agent}/weekly_report.md"
    else:
        href = f"/{market}/{agent}/weekly_report.md"
    return (
        f'<a href="{html.escape(href)}">'
        f'{html.escape(path.name)}</a>'
    )


def _latest_monthly_status(root: Path, market: str) -> str:
    if market != "a_share":
        return '<span class="pending">未配置</span>'
    reports_dir = root / "reports" / "competition"
    files = sorted(reports_dir.glob("monthly_review_*.md"))
    if not files:
        return '<span class="pending">无月报</span>'
    latest = files[-1]
    return f'<a href="/competition/{html.escape(latest.name)}">{html.escape(latest.stem.replace("monthly_review_", ""))}</a>'


def _render_all_market_observer(
    markets: list[str],
    agents: list[str],
    paths_by_market: dict[str, dict[str, DashboardAgentPaths]],
    root: Path,
    strategy_labels: dict[str, str],
) -> str:
    market_cards: list[str] = []
    decision_rows: list[str] = []
    task_rows: list[str] = []
    for market in markets:
        label = MARKET_LABELS.get(market, market)
        agent_paths = paths_by_market.get(market, {})
        nav_bits: list[str] = []
        for agent in agents:
            paths = agent_paths.get(agent)
            if paths is None:
                continue
            nav = _read_latest_nav(paths.data_dir)
            baseline = MARKET_INITIAL_CASH.get(market, 1.0)
            latest = nav["latest"]
            ret = (latest / baseline - 1.0) if latest is not None and baseline else None
            nav_bits.append(
                f'<div><strong>{html.escape(strategy_labels.get(agent, agent))}</strong> '
                f'<span class="num">{_format_market_money(latest, market)}</span> '
                f'<span class="{"pos" if (ret or 0) >= 0 else "neg"}">{format_pct(ret)}</span></div>'
            )
            pending = _read_pending_summary(paths.data_dir)
            decision_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(strategy_labels.get(agent, agent))}</td>'
                f'<td><a href="/pro/{html.escape(market)}/{html.escape(agent)}.html">专业页</a></td>'
                f'<td class="num">目标订单 {pending["total"]} '
                f'(买 {pending["buy"]} / 卖 {pending["sell"]})</td>'
                f'<td>{_latest_weekly_report_link(market, agent, paths.reports_dir)}</td>'
                '</tr>'
            )
            task_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(strategy_labels.get(agent, agent))}</td>'
                '<td>日任务 <code>run-daily</code></td>'
                f'<td>{_status_badge(_read_latest_run(paths.data_dir, "run-daily"))}</td>'
                '</tr>'
            )
            task_rows.append(
                '<tr>'
                f'<td>{html.escape(label)}</td>'
                f'<td>{html.escape(strategy_labels.get(agent, agent))}</td>'
                '<td>周任务 <code>run-weekly</code></td>'
                f'<td>{_status_badge(_read_latest_run(paths.data_dir, "run-weekly"))}</td>'
                '</tr>'
            )
        task_rows.append(
            '<tr>'
            f'<td>{html.escape(label)}</td>'
            '<td>市场级</td>'
            '<td>月任务 <code>competition-monthly-review</code></td>'
            f'<td>{_latest_monthly_status(root, market)}</td>'
            '</tr>'
        )
        nav_html = "".join(nav_bits) or '<p class="empty">暂无 NAV</p>'
        market_cards.append(
            '<section class="metric-card market-card">'
            f'<div class="card-label">{html.escape(label)}</div>'
            f'<div class="market-nav-lines">{nav_html}</div>'
            '</section>'
        )

    decisions = (
        '<table class="comparison market-decisions"><thead>'
        '<tr><th>市场</th><th>策略</th><th>决策入口</th><th>最新决策</th><th>周报</th></tr>'
        '</thead><tbody>'
        + "".join(decision_rows)
        + '</tbody></table>'
    )
    tasks = (
        '<table class="comparison market-task-matrix"><thead>'
        '<tr><th>市场</th><th>主体</th><th>任务</th><th>最近状态</th></tr>'
        '</thead><tbody>'
        + "".join(task_rows)
        + '</tbody></table>'
    )
    return (
        '<section id="all-market-observer" class="all-market-observer" data-source="/api/dashboard/summary.json">'
        '<h2>投资账户总览</h2>'
        f'<section class="grid market-overview-grid">{"".join(market_cards)}</section>'
        '<h2>投资账户具体决策</h2>'
        f'<div class="panel">{decisions}</div>'
        '<h2>日/周/月任务运行情况</h2>'
        f'<div class="panel">{tasks}</div>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Rendering


def _render_summary_cards(
    perf: dict[str, dict[str, Any]],
    leaderboard: list[dict[str, Any]],
    strategy_labels: dict[str, str],
) -> list[dict[str, str]]:
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
        winner_id = str(latest.get("winner_return") or "")
        winner = strategy_labels.get(winner_id, winner_id) or "-"
        month = latest.get("month") or "-"
    else:
        winner = "-"
        month = "-"

    cards = []
    for agent in DEFAULT_AGENT_ORDER:
        cards.append(
            {
                "label": f"{strategy_labels.get(agent, agent)} 累计收益",
                "value": format_pct(cumulative.get(agent)),
                "tone": "primary",
            }
        )
    cards.append(
        {
            "label": (
                f"累计差({strategy_labels.get('claude', 'claude')} − "
                f"{strategy_labels.get('codex', 'codex')})"
            ),
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


def _render_tabs_nav(agents: list[str], strategy_labels: dict[str, str]) -> str:
    items = []
    for agent in agents:
        items.append(
            f'<a href="#tab-{agent}" class="tab-link">'
            f'{html.escape(strategy_labels.get(agent, agent))}</a>'
        )
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
    *,
    all_market_html: str = "",
    strategy_labels: dict[str, str],
) -> str:
    sections: list[str] = []
    for agent in agents:
        fragment = fragments.get(agent)
        if fragment:
            body = fragment
        else:
            label = strategy_labels.get(agent, agent)
            body = (
                f'<p class="empty">尚未生成 {html.escape(label)} 仪表盘；'
                f'请先跑 <code>python3 -m stock_analyze --agent {agent} run-weekly</code>。</p>'
            )
        sections.append(
            f'<section id="tab-{agent}" class="tab-section">\n<h1 class="tab-title">'
            f'{html.escape(strategy_labels.get(agent, agent))}</h1>\n{body}\n</section>'
        )

    cards_html = "".join(
        f'<section class="metric-card metric-{card["tone"]}"><div class="card-label">{card["label"]}</div>'
        f'<div class="metric">{card["value"]}</div></section>'
        for card in summary_cards
    )

    table_rows = []
    agent_names = [agent for agent in agents]
    header_cells = "".join(
        f"<th>{html.escape(strategy_labels.get(agent, agent))}</th>"
        for agent in agent_names
    )
    table_rows.append(f'<tr><th>指标</th>{header_cells}<th>胜方</th></tr>')
    for row in comparison_table:
        cells = []
        for agent in agent_names:
            cells.append(f'<td>{row["values"].get(agent, "-")}</td>')
        winner_id = row.get("winner")
        winner = strategy_labels.get(winner_id, winner_id) if winner_id else "-"
        table_rows.append(
            f'<tr><th class="metric-label">{row["label"]}</th>{"".join(cells)}<td><strong>{winner}</strong></td></tr>'
        )
    table_html = '<table class="comparison"><thead>' + table_rows[0] + "</thead><tbody>" + "".join(table_rows[1:]) + "</tbody></table>"

    overlap_html = _render_overlap_bar(positions_overlap, strategy_labels)
    leaderboard_html = _render_leaderboard_strip(leaderboard, strategy_labels)
    monthly_html = _render_monthly_links(monthly_links)
    if paths_by_agent:
        observation_html = _render_observation_pairing(
            agents,
            {agent: paths_by_agent[agent].data_dir for agent in agents if agent in paths_by_agent},
            strategy_labels,
        )
    else:
        observation_html = _render_observation_pairing(agents, {}, strategy_labels)

    # Pipeline status (refreshes with the aggregate dashboard via OnSuccess)
    try:
        pipeline_status_html = render_pipeline_status_panel(repo_root=Path.cwd())
    except Exception as exc:  # noqa: BLE001
        pipeline_status_html = (
            f'<div class="panel"><p>pipeline-status panel render error: {exc}</p></div>'
        )
    try:
        sentiment_status_html = render_sentiment_comparison_panel(repo_root=Path.cwd())
    except Exception as exc:  # noqa: BLE001
        sentiment_status_html = (
            f'<div class="panel"><p>sentiment panel render error: {exc}</p></div>'
        )

    compare_section = (
        '<section id="tab-compare" class="tab-section">\n'
        '<h1 class="tab-title">投资账户观察台</h1>\n'
        f'{all_market_html}\n'
        '<h2>投资账户情绪反馈</h2>\n'
        f'{sentiment_status_html}\n'
        '<h2>A股双策略对比</h2>\n'
        f'<section class="grid summary-grid">{cards_html}</section>\n'
        '<h2>📋 Pipeline 任务清单</h2>\n'
        f'{pipeline_status_html}\n'
        '<h2>累计净值曲线</h2>\n'
        '<div class="panel"><canvas id="comparisonNav" width="1200" height="320"></canvas>'
        '<div class="hint">两条曲线分别代表两个策略的总资产；颜色与 tab 颜色一致。</div></div>\n'
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


def _render_observation_pairing(
    agents: list[str],
    data_dirs: dict[str, Path],
    strategy_labels: dict[str, str],
) -> str:
    """Render side-by-side latest weekly observations for the given agents."""

    if len(agents) < 2 or not data_dirs:
        return '<p class="empty">尚未生成 agent 周笔记。运行 <code>/weekly-review claude</code> / <code>do weekly review for codex</code> 后会出现。</p>'

    panels: list[str] = []
    have_any = False
    for agent in agents:
        path = _latest_weekly_note(data_dirs.get(agent, Path("/dev/null/missing")))
        label = strategy_labels.get(agent, agent)
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


def _render_overlap_bar(
    overlap: dict[str, Any],
    strategy_labels: dict[str, str],
) -> str:
    if not overlap.get("agents"):
        return '<p class="empty">尚无持仓数据。</p>'
    a, b = overlap["agents"]
    label_a = strategy_labels.get(a, a)
    label_b = strategy_labels.get(b, b)
    shared = overlap.get("shared", [])
    ex_a = overlap["exclusives"].get(a, [])
    ex_b = overlap["exclusives"].get(b, [])
    total = max(len(shared) + len(ex_a) + len(ex_b), 1)
    seg_shared = len(shared) / total * 100
    seg_a = len(ex_a) / total * 100
    seg_b = len(ex_b) / total * 100
    return (
        '<div class="overlap-bar">'
        f'<span class="seg seg-a" style="width:{seg_a:.1f}%" title="仅 {html.escape(label_a)}: {len(ex_a)} 只">{html.escape(label_a)} 独占 {len(ex_a)}</span>'
        f'<span class="seg seg-shared" style="width:{seg_shared:.1f}%" title="共有: {len(shared)} 只">共有 {len(shared)}</span>'
        f'<span class="seg seg-b" style="width:{seg_b:.1f}%" title="仅 {html.escape(label_b)}: {len(ex_b)} 只">{html.escape(label_b)} 独占 {len(ex_b)}</span>'
        '</div>'
        f'<div class="hint">Jaccard 重叠度 = {len(shared) / max(len(shared) + len(ex_a) + len(ex_b), 1):.2%}</div>'
    )


def _render_leaderboard_strip(
    rows: list[dict[str, Any]],
    strategy_labels: dict[str, str],
) -> str:
    if not rows:
        return '<p class="empty">尚未生成月度对比。运行 <code>competition-monthly-review</code> 后会出现。</p>'
    blocks = []
    for row in rows[-24:]:
        month = row.get("month") or "-"
        winner = row.get("winner_return") or "-"
        cls = "win-claude" if winner == "claude" else "win-codex" if winner == "codex" else "win-tie"
        winner_label = strategy_labels.get(str(winner), str(winner))
        blocks.append(
            f'<span class="month-block {cls}" title="{month}: '
            f'{html.escape(winner_label)}">{month}</span>'
        )
    return "".join(blocks)


def _render_monthly_links(links: list[dict[str, str]]) -> str:
    if not links:
        return '<p class="empty">暂无月度报告。运行 <code>competition-monthly-review</code> 后会出现。</p>'
    items = "".join(f'<li><a href="{link["href"]}">{link["month"]}</a></li>' for link in links)
    return f'<ul class="monthly-review-links">{items}</ul>'


def _render_page(
    tabs_nav: str,
    tab_sections: str,
    nav_json: str,
    leaderboard_json: str,
    strategy_labels: dict[str, str],
) -> str:
    generated = datetime.now()
    generated_at = generated.strftime("%Y-%m-%d %H:%M:%S")
    color_claude = AGENT_COLORS.get("claude", "#f59e0b")
    color_codex = AGENT_COLORS.get("codex", "#06b6d4")
    top_nav = render_nav_html(
        active="pro",
        generated_at=generated,
        strategy_labels=strategy_labels,
    )
    defensive_label = html.escape(strategy_labels.get("claude", "稳健防守"))
    trend_label = html.escape(strategy_labels.get("codex", "趋势进攻"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{defensive_label} vs {trend_label} · 策略竞技场</title>
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
    <h1>{defensive_label} <span class="vs">vs</span> {trend_label} · 双策略模拟竞技场</h1>
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
    const strategyLabels = {json.dumps(strategy_labels, ensure_ascii=False)};

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
        ctx.fillText(strategyLabels[agent] || agent, canvas.width - 130, 28 + idx * 20);
      }});
    }}

    drawComparisonNav();
    {_DASHBOARD_DYNAMIC_JS}
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Dark Bloomberg theme for the competition page. Class names preserved.
# Color/spacing tokens pull from _dashboard_assets.BASE_CSS.

_DASHBOARD_DYNAMIC_JS = r"""
    function escapeText(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[ch]));
    }

    function statusLabel(row, missingText) {
      if (!row || row.status === 'missing') return '<span class="pending">' + escapeText(missingText) + '</span>';
      const started = row.started_at ? String(row.started_at).slice(0, 19).replace('T', ' ') : '';
      if (row.status === 'success') return '<span class="ok">OK ' + escapeText(started) + '</span>';
      if (row.status === 'failed') return '<span class="fail">失败 ' + escapeText(row.error_summary || '') + '</span>';
      return '<span class="pending">' + escapeText(row.status) + ' ' + escapeText(started) + '</span>';
    }

    function renderDashboardSummary(payload) {
      const marketCards = [];
      const decisionRows = [];
      const taskRows = [];
      for (const market of payload.markets || []) {
        const navBits = [];
        for (const item of market.agents || []) {
          const displayName = item.strategy && item.strategy.label || item.agent;
          const ret = Number(item.nav && item.nav.return);
          const retClass = Number.isFinite(ret) && ret < 0 ? 'neg' : 'pos';
          navBits.push(
            '<div><strong>' + escapeText(displayName) + '</strong> ' +
            '<span class="num">' + escapeText(item.nav && item.nav.latest_display || '-') + '</span> ' +
            '<span class="' + retClass + '">' + escapeText(item.nav && item.nav.return_display || '-') + '</span></div>'
          );
          const pending = (item.decision && item.decision.pending_orders) || {};
          const reportHref = item.decision && item.decision.weekly_report_href;
          decisionRows.push(
            '<tr><td>' + escapeText(market.label) + '</td>' +
            '<td>' + escapeText(displayName) + '</td>' +
            '<td><a href="' + escapeText(item.decision.href) + '">专业页</a></td>' +
            '<td class="num">目标订单 ' + escapeText(pending.total ?? 0) +
            ' (买 ' + escapeText(pending.buy ?? 0) + ' / 卖 ' + escapeText(pending.sell ?? 0) + ')</td>' +
            '<td>' + (reportHref ? '<a href="' + escapeText(reportHref) + '">weekly_report.md</a>' : '<span class="pending">无周报</span>') + '</td></tr>'
          );
          taskRows.push(
            '<tr><td>' + escapeText(market.label) + '</td><td>' + escapeText(displayName) +
            '</td><td>日任务 <code>run-daily</code></td><td>' + statusLabel(item.tasks && item.tasks.daily, '未运行') + '</td></tr>'
          );
          taskRows.push(
            '<tr><td>' + escapeText(market.label) + '</td><td>' + escapeText(displayName) +
            '</td><td>周任务 <code>run-weekly</code></td><td>' + statusLabel(item.tasks && item.tasks.weekly, '未运行') + '</td></tr>'
          );
        }
        const monthly = market.monthly || {};
        let monthlyCell = '<span class="pending">' + (monthly.status === 'not_configured' ? '未配置' : '无月报') + '</span>';
        if (monthly.href) monthlyCell = '<a href="' + escapeText(monthly.href) + '">' + escapeText(monthly.label || '月报') + '</a>';
        taskRows.push(
          '<tr><td>' + escapeText(market.label) + '</td><td>市场级</td>' +
          '<td>月任务 <code>competition-monthly-review</code></td><td>' + monthlyCell + '</td></tr>'
        );
        marketCards.push(
          '<section class="metric-card market-card"><div class="card-label">' + escapeText(market.label) +
          '</div><div class="market-nav-lines">' + (navBits.join('') || '<p class="empty">暂无 NAV</p>') + '</div></section>'
        );
      }
      const updatedDisplay = (payload && payload.generated_at) ? String(payload.generated_at).replace('T', ' ') : new Date().toLocaleTimeString();
      return (
        '<div class="live-badge">🟢 实时 · 更新于 ' + escapeText(updatedDisplay) + ' · 每 30 秒自动刷新</div>' +
        '<h2>投资账户总览</h2><section class="grid market-overview-grid">' + marketCards.join('') + '</section>' +
        '<h2>投资账户具体决策</h2><div class="panel"><table class="comparison market-decisions"><thead>' +
        '<tr><th>市场</th><th>策略</th><th>决策入口</th><th>最新决策</th><th>周报</th></tr></thead><tbody>' +
        decisionRows.join('') + '</tbody></table></div>' +
        '<h2>日/周/月任务运行情况</h2><div class="panel"><table class="comparison market-task-matrix"><thead>' +
        '<tr><th>市场</th><th>主体</th><th>任务</th><th>最近状态</th></tr></thead><tbody>' +
        taskRows.join('') + '</tbody></table>' +
        '<div class="hint">数据来自 <code>/api/dashboard/summary.json</code>；静态 HTML 仅作为首屏兜底。</div></div>'
      );
    }

    async function hydrateDashboardSummary() {
      const root = document.getElementById('all-market-observer');
      if (!root || !window.fetch) return;
      try {
        const response = await fetch('/api/dashboard/summary.json', { cache: 'no-store' });
        if (!response.ok) return;
        const payload = await response.json();
        root.innerHTML = renderDashboardSummary(payload);
      } catch (err) {
        root.dataset.apiStatus = 'fallback';
      }
    }

    hydrateDashboardSummary();
    // 动态刷新:页面打开期间每 30 秒重新拉一次 /api/dashboard/summary.json(live 重读磁盘)
    if (window.fetch) { setInterval(hydrateDashboardSummary, 30000); }
"""

_COMPETITION_CSS = """
.page-header { padding: var(--space-md) var(--space-xl) var(--space-sm); background: var(--bg-elevated); border-bottom: 1px solid var(--border-subtle); }
.page-header h1 { margin: 0; font-size: 22px; font-weight: 600; color: var(--text-primary); letter-spacing: 0.02em; }
.page-header h1 .vs { color: var(--text-tertiary); font-weight: 400; padding: 0 6px; font-size: 18px; }
.page-header .subhead { margin-top: 4px; color: var(--text-tertiary); font-size: 12px; font-family: var(--font-mono); }
.live-badge { display: inline-block; margin: 0 0 var(--space-sm); padding: 3px 10px; border-radius: var(--radius-sm); background: var(--bg-overlay); border: 1px solid var(--border-subtle); color: var(--text-secondary); font-size: 11px; font-family: var(--font-mono); letter-spacing: 0.04em; }

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
.ok { color: var(--pos); font-family: var(--font-mono); font-size: 12px; }
.pending { color: var(--text-tertiary); font-family: var(--font-mono); font-size: 12px; }
.fail { color: var(--neg); font-family: var(--font-mono); font-size: 12px; }
.all-market-observer { margin-bottom: var(--space-xl); }
.market-overview-grid { margin-bottom: var(--space-lg); }
.market-nav-lines { display: grid; gap: var(--space-xs); font-family: var(--font-mono); font-size: 12px; }
.market-nav-lines strong { color: var(--text-secondary); font-family: var(--font-sans); margin-right: var(--space-xs); }
.market-decisions td, .market-task-matrix td { vertical-align: top; }

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
