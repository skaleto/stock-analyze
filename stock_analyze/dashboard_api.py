"""Bounded domain resources for the interactive dashboard API.

The runtime files remain the source of truth.  This module controls how much of
that state each HTTP resource reads and returns so one slow research artifact
cannot block the entire dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_finance import build_activity, build_strategy_profile, enrich_rows
from .markets.cn_qdii_etf.lookthrough import build_portfolio_lookthrough
from .utils import safe_float


DEFAULT_ROW_LIMIT = 200
DEFAULT_PREDICTION_LIMIT_PER_HORIZON = 12
MAX_PREDICTION_LIMIT_PER_HORIZON = 50

_COMMON_ROW_FIELDS = {
    "account_id",
    "account_label",
    "code",
    "name",
    "side",
    "side_label",
    "reason",
    "score",
    "exposure_group",
    "theme",
    "industry",
    "index_key",
    "country",
    "sector",
}
_ORDER_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "target_value",
    "target_weight",
    "execute_after",
    "trade_date",
    "signal_date",
    "status",
}
_POSITION_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "available_shares",
    "avg_cost",
    "last_price",
    "market_value",
    "unrealized_pnl",
    "last_buy_date",
    "hold_since",
    "updated_at",
}
_TRADE_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "price",
    "gross_amount",
    "commission",
    "stamp_tax",
    "slippage",
    "net_amount",
    "cash_after",
    "trade_date",
}


def _generated_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _context(
    repo_root: str | Path | None,
    market: str,
    agent: str,
) -> tuple[Path, agg.DashboardAgentPaths]:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    if agent not in competition.list_agents_for_market(market, root):
        raise competition.UnknownAgent(f"unknown_agent:{agent}; market={market}")
    return root, agg._resolve_dashboard_paths(market, agent, root)


def _base(market: str, agent: str) -> dict[str, Any]:
    return {"generated_at": _generated_at(), "market": market, "agent": agent}


def _project_rows(
    rows: list[dict[str, Any]],
    fields: set[str],
    *,
    order: bool = False,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in rows:
        row = {key: value for key, value in raw.items() if key in fields}
        if order and row.get("shares") is None and raw.get("delta_shares") is not None:
            row["shares"] = raw["delta_shares"]
        projected.append(row)
    return projected


def build_dashboard_overview_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return identity, strategy configuration, and the latest account NAV."""

    root, paths = _context(repo_root, market, agent)
    try:
        strategy = build_strategy_profile(paths.config_path, repo_root=root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise agg.DashboardDataError("strategy_overlay") from exc
    latest_nav = agg._read_nav_detail(paths.data_dir, market).get("latest")
    return agg._json_safe(
        {
            **_base(market, agent),
            "market_label": agg.MARKET_LABELS.get(market, market),
            "currency": agg.MARKET_CURRENCY.get(market, ""),
            "strategy": strategy,
            "latest_nav": latest_nav,
        }
    )


def build_dashboard_performance_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return NAV and benchmark history for charting."""

    _, paths = _context(repo_root, market, agent)
    return agg._json_safe(
        {**_base(market, agent), "nav": agg._read_nav_detail(paths.data_dir, market)}
    )


def _read_portfolio_exposure(
    root: Path,
    paths: agg.DashboardAgentPaths,
    market: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    names = agg._read_fund_name_lookup(root, market)
    orders = enrich_rows(
        market,
        agg._flatten_pending_orders(paths.data_dir, name_lookup=names),
        repo_root=root,
    )
    positions = enrich_rows(
        market,
        agg._limited_csv_rows(
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
    return (
        _project_rows(orders, _ORDER_ROW_FIELDS, order=True),
        _project_rows(positions, _POSITION_ROW_FIELDS),
    )


def build_dashboard_portfolio_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = DEFAULT_ROW_LIMIT,
) -> dict[str, Any]:
    """Return current holdings, pending orders, trades, and their timeline."""

    root, paths = _context(repo_root, market, agent)
    orders_all, positions_all = _read_portfolio_exposure(root, paths, market)
    trades_all = enrich_rows(
        market,
        agg._limited_csv_rows(
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
    trades_all = _project_rows(trades_all, _TRADE_ROW_FIELDS)
    activity_all = build_activity(trades_all, orders_all)
    positions = positions_all[-limit:] if limit > 0 else positions_all
    trades = trades_all[-limit:] if limit > 0 else trades_all
    orders = orders_all[:limit] if limit > 0 else orders_all
    activity = activity_all[:limit] if limit > 0 else activity_all
    position_value = sum(
        safe_float(row.get("market_value")) or 0.0 for row in positions_all
    )
    return agg._json_safe(
        {
            **_base(market, agent),
            "activity": {"summary": {"total": len(activity_all)}, "rows": activity},
            "orders": {
                "summary": {
                    "total": len(orders_all),
                    "buy": sum(1 for row in orders_all if row.get("side") == "buy"),
                    "sell": sum(1 for row in orders_all if row.get("side") == "sell"),
                },
                "rows": orders,
            },
            "positions": {
                "summary": {
                    "total": len(positions_all),
                    "market_value": position_value,
                    "market_value_display": agg._format_market_money(position_value, market),
                },
                "rows": positions,
            },
            "trades": {"summary": {"total": len(trades_all)}, "rows": trades},
        }
    )


def build_dashboard_predictions_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit_per_horizon: int | None = DEFAULT_PREDICTION_LIMIT_PER_HORIZON,
) -> dict[str, Any]:
    """Return bounded prediction rows and the evidence needed to interpret them."""

    root, _ = _context(repo_root, market, agent)
    bounded_limit = None
    if limit_per_horizon is not None:
        bounded_limit = min(
            MAX_PREDICTION_LIMIT_PER_HORIZON,
            max(1, int(limit_per_horizon)),
        )
    summary = agg._read_prediction_summary(
        root,
        market,
        agent,
        limit_per_horizon=bounded_limit,
    )
    return agg._json_safe(
        {
            **_base(market, agent),
            "prediction_summary": summary,
            "alerts": agg._prediction_alerts(summary),
            "regimes": agg._read_regime_summary(root, market),
            "model_health": agg._read_model_health(root, market),
            "source_health": agg._read_research_source_health(root, market),
        }
    )


def _lookthrough(
    market: str,
    positions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    if market != "cn_qdii_etf":
        return {}
    source = "positions" if positions else "planned_orders"
    rows = positions or [row for row in orders if str(row.get("side") or "").lower() == "buy"]
    return build_portfolio_lookthrough(rows, source=source)


def build_dashboard_research_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return optional ETF selection, look-through, and research artifacts."""

    root, paths = _context(repo_root, market, agent)
    if market != "cn_qdii_etf":
        return agg._json_safe(
            {
                **_base(market, agent),
                "selection": {},
                "lookthrough": {},
                "research": {},
            }
        )
    orders, positions = _read_portfolio_exposure(root, paths, market)
    return agg._json_safe(
        {
            **_base(market, agent),
            "selection": agg._read_selection_snapshot(paths.data_dir),
            "lookthrough": _lookthrough(market, positions, orders),
            "research": agg._read_qdii_research(root, agent),
        }
    )


def build_dashboard_operations_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = DEFAULT_ROW_LIMIT,
) -> dict[str, Any]:
    """Return bounded execution history and weekly-report metadata."""

    _, paths = _context(repo_root, market, agent)
    rows_all = agg._limited_csv_rows(
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
    rows_all = agg._collapse_run_transitions(rows_all)
    rows = rows_all[-limit:] if limit > 0 else rows_all
    report_path = paths.reports_dir / "weekly_report.md"
    markdown = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    return agg._json_safe(
        {
            **_base(market, agent),
            "runs": {"summary": {"total": len(rows_all)}, "rows": list(reversed(rows))},
            "weekly_report": {
                "exists": report_path.exists(),
                "href": agg._weekly_report_href(market, agent, paths.reports_dir),
                "markdown": markdown[:12000],
            },
        }
    )


def build_dashboard_comparison_input_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Build only fields consumed by ``build_strategy_comparison``."""

    root, paths = _context(repo_root, market, agent)
    try:
        strategy = build_strategy_profile(paths.config_path, repo_root=root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise agg.DashboardDataError("strategy_overlay") from exc
    nav = agg._read_nav_detail(paths.data_dir, market)
    portfolio = build_dashboard_portfolio_data(
        repo_root=root,
        market=market,
        agent=agent,
        limit=0,
    )
    return {
        "strategy": strategy,
        "nav": nav,
        "positions": portfolio["positions"],
        "orders": portfolio["orders"],
        "trades": portfolio["trades"],
        "lookthrough": _lookthrough(
            market,
            portfolio["positions"]["rows"],
            portfolio["orders"]["rows"],
        ),
    }
