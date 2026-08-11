"""Realized forward evidence from version-pinned model iteration accounts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def _empty(gaps: list[str], lookthrough: dict | None = None) -> dict:
    return {
        "forward_evidence_status": "insufficient_evidence",
        "forward_evidence_gaps": gaps,
        "forward_cycles": 0,
        "forward_net_excess_return": 0.0,
        "forward_max_drawdown": 1.0,
        "forward_all_accounts_positive_active": False,
        "forward_execution_cost_bps": 0.0,
        "forward_account_metrics": {},
        **(lookthrough or {}),
    }


def _lookthrough_evidence(
    root: Path,
    expected_account_ids: tuple[str, ...],
    *,
    required: bool,
) -> dict:
    if not required:
        return {
            "lookthrough_required": False,
            "lookthrough_evidence_status": "not_required",
            "lookthrough_evidence_gaps": [],
            "underlying_profile_coverage": 1.0,
            "underlying_company_weight_coverage": 1.0,
            "lookthrough_account_metrics": {},
        }
    status_path = root / "shadow_status.json"
    if not status_path.exists():
        return {
            "lookthrough_required": True,
            "lookthrough_evidence_status": "insufficient_evidence",
            "lookthrough_evidence_gaps": ["shadow_status_missing"],
            "underlying_profile_coverage": 0.0,
            "underlying_company_weight_coverage": 0.0,
            "lookthrough_account_metrics": {},
        }
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        status = {}
    accounts = {
        str(row.get("account_id") or ""): row
        for row in status.get("accounts") or []
        if isinstance(row, dict) and str(row.get("account_id") or "")
    }
    gaps: list[str] = []
    metrics: dict[str, dict[str, float]] = {}
    for account_id in expected_account_ids:
        diagnostics = (accounts.get(account_id) or {}).get("optimizer_diagnostics")
        if not isinstance(diagnostics, dict):
            gaps.append(f"lookthrough_account_missing:{account_id}")
            continue
        values: dict[str, float] = {}
        for key in (
            "underlying_profile_coverage",
            "underlying_company_weight_coverage",
        ):
            try:
                value = float(diagnostics[key])
            except (KeyError, TypeError, ValueError):
                gaps.append(f"lookthrough_metric_missing:{account_id}:{key}")
                continue
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                gaps.append(f"lookthrough_metric_invalid:{account_id}:{key}")
                continue
            values[key] = value
        if len(values) == 2:
            metrics[account_id] = values
    available = not gaps and len(metrics) == len(expected_account_ids)
    return {
        "lookthrough_required": True,
        "lookthrough_evidence_status": (
            "available" if available else "insufficient_evidence"
        ),
        "lookthrough_evidence_gaps": gaps,
        "underlying_profile_coverage": min(
            (row["underlying_profile_coverage"] for row in metrics.values()),
            default=0.0,
        ),
        "underlying_company_weight_coverage": min(
            (
                row["underlying_company_weight_coverage"]
                for row in metrics.values()
            ),
            default=0.0,
        ),
        "lookthrough_account_metrics": metrics,
    }


def _account_metrics(frame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    ordered = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    ordered["total_value"] = pd.to_numeric(ordered["total_value"], errors="coerce")
    ordered["benchmark_close"] = pd.to_numeric(ordered["benchmark_close"], errors="coerce")
    ordered = ordered.dropna(subset=["total_value", "benchmark_close"])
    if len(ordered) < 2 or float(ordered.iloc[0]["total_value"]) <= 0 or float(ordered.iloc[0]["benchmark_close"]) <= 0:
        return {}, pd.DataFrame()
    ordered["portfolio_return"] = ordered["total_value"].pct_change(fill_method=None)
    ordered["benchmark_return"] = ordered["benchmark_close"].pct_change(fill_method=None)
    ordered["active_return"] = ordered["portfolio_return"] - ordered["benchmark_return"]
    portfolio_cumulative = float(ordered.iloc[-1]["total_value"] / ordered.iloc[0]["total_value"] - 1.0)
    benchmark_cumulative = float(ordered.iloc[-1]["benchmark_close"] / ordered.iloc[0]["benchmark_close"] - 1.0)
    active_cumulative = (
        (1.0 + portfolio_cumulative) / (1.0 + benchmark_cumulative) - 1.0
        if benchmark_cumulative > -1.0 else -1.0
    )
    iso_weeks = pd.to_datetime(ordered["date"], errors="coerce").dt.strftime("%G-W%V")
    return {
        "net_return": portfolio_cumulative,
        "benchmark_return": benchmark_cumulative,
        "active_return": active_cumulative,
        "cycles": int(iso_weeks.dropna().nunique()),
        "observations": int(len(ordered)),
    }, ordered


def load_forward_portfolio_evidence(
    portfolio_dir: str | Path,
    *,
    expected_account_ids: Iterable[str],
    require_lookthrough: bool = False,
) -> dict:
    """Summarize realized NAV/cost evidence without using research metrics."""

    root = Path(portfolio_dir)
    expected = tuple(dict.fromkeys(str(value) for value in expected_account_ids))
    lookthrough = _lookthrough_evidence(
        root,
        expected,
        required=require_lookthrough,
    )
    nav_path = root / "daily_nav.csv"
    if not nav_path.exists():
        return _empty(["daily_nav_missing"], lookthrough)
    try:
        nav = pd.read_csv(
            nav_path,
            dtype={"date": str, "account_id": str, "benchmark_code": str},
        )
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return _empty(["daily_nav_unreadable"], lookthrough)
    required = {"date", "account_id", "total_value", "benchmark_close"}
    if nav.empty or required.difference(nav.columns):
        return _empty(["daily_nav_schema"], lookthrough)
    gaps: list[str] = []
    account_metrics: dict[str, dict] = {}
    account_paths: list[pd.DataFrame] = []
    for account_id in expected:
        metrics, path = _account_metrics(nav.loc[nav["account_id"].astype(str).eq(account_id)])
        if not metrics:
            gaps.append(f"missing_account:{account_id}")
            continue
        account_metrics[account_id] = metrics
        path = path.copy()
        path["account_id"] = account_id
        path["account_weight"] = float(path.iloc[0]["total_value"])
        account_paths.append(path)
    if gaps or len(account_metrics) != len(expected):
        result = _empty(gaps or ["account_evidence_incomplete"], lookthrough)
        result["forward_account_metrics"] = account_metrics
        return result
    returns = pd.concat(account_paths, ignore_index=True, sort=False).dropna(
        subset=["portfolio_return", "benchmark_return"]
    )
    aggregate_rows: list[dict] = []
    for day, group in returns.groupby("date", sort=True):
        weights = pd.to_numeric(group["account_weight"], errors="coerce").fillna(0.0)
        weights = weights / weights.sum()
        portfolio_return = float(np.sum(group["portfolio_return"] * weights))
        benchmark_return = float(np.sum(group["benchmark_return"] * weights))
        aggregate_rows.append({
            "date": str(day),
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "active_return": portfolio_return - benchmark_return,
        })
    aggregate = pd.DataFrame(aggregate_rows)
    net_curve = np.cumprod(1.0 + aggregate["portfolio_return"].fillna(0.0).to_numpy(dtype=float))
    drawdown = abs(float(np.min(net_curve / np.maximum.accumulate(net_curve) - 1.0))) if len(net_curve) else 1.0
    net_excess = float(np.prod(1.0 + aggregate["active_return"].fillna(0.0)) - 1.0)
    total_cost = total_gross = 0.0
    trade_path = root / "trades.csv"
    if trade_path.exists():
        try:
            trades = pd.read_csv(
                trade_path,
                dtype={"trade_date": str, "account_id": str, "code": str},
            )
        except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
            trades = pd.DataFrame()
        if not trades.empty:
            total_gross = float(pd.to_numeric(trades.get("gross_amount"), errors="coerce").fillna(0.0).sum())
            total_cost = sum(
                float(pd.to_numeric(trades.get(column), errors="coerce").fillna(0.0).sum())
                for column in ("commission", "stamp_tax", "slippage")
                if column in trades.columns
            )
    return {
        "forward_evidence_status": "available",
        "forward_evidence_gaps": [],
        "forward_cycles": min(int(item["cycles"]) for item in account_metrics.values()),
        "forward_net_excess_return": net_excess,
        "forward_max_drawdown": drawdown,
        "forward_all_accounts_positive_active": all(
            float(item["active_return"]) > 0.0 for item in account_metrics.values()
        ),
        "forward_execution_cost_bps": total_cost / total_gross * 10_000.0 if total_gross > 0 else 0.0,
        "forward_account_metrics": account_metrics,
        **lookthrough,
    }


__all__ = ["load_forward_portfolio_evidence"]
