"""Season-aware metrics for the two product-facing strategy slots."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from .dashboard_finance import factor_metadata
from .strategy_registry import PAIR_SLOTS, factor_weight_distance


TRADING_DAYS_PER_YEAR = 252


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strategy_factors(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in detail.get("strategy", {}).get("factors", []):
        if not isinstance(raw, dict) or not raw.get("key"):
            continue
        weight = _number(raw.get("weight"))
        output[str(raw["key"])] = {
            **raw,
            "weight": weight if weight is not None else 0.0,
        }
    return output


def _season_nav(
    detail: dict[str, Any],
    effective_date: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw in detail.get("nav", {}).get("series", []):
        if not isinstance(raw, dict):
            continue
        date = str(raw.get("date") or "")
        total = _number(raw.get("total_value"))
        if not date or total is None or total <= 0:
            continue
        rows.append({**raw, "date": date, "total_value": total})
    rows.sort(key=lambda row: row["date"])
    if not rows:
        return {
            "anchor_date": None,
            "anchor_value": None,
            "points": [],
            "returns": {},
        }
    eligible = [row for row in rows if row["date"] <= effective_date]
    anchor = eligible[-1] if eligible else rows[0]
    anchor_index = rows.index(anchor)
    season_rows = rows[anchor_index:]
    anchor_value = anchor["total_value"]
    anchor_benchmark = _number(anchor.get("benchmark_return"))
    points: list[dict[str, Any]] = []
    returns: dict[str, float] = {}
    previous_value: float | None = None
    for row in season_rows:
        total = row["total_value"]
        benchmark_return = _number(row.get("benchmark_return"))
        normalized_benchmark = None
        if anchor_benchmark is not None and benchmark_return is not None and 1.0 + anchor_benchmark != 0:
            normalized_benchmark = (1.0 + benchmark_return) / (1.0 + anchor_benchmark) - 1.0
        point = {
            "date": row["date"],
            "value": total / anchor_value - 1.0,
            "total_value": total,
            "cash": _number(row.get("cash")),
            "benchmark": normalized_benchmark,
        }
        points.append(point)
        if previous_value is not None and previous_value > 0:
            returns[row["date"]] = total / previous_value - 1.0
        previous_value = total
    return {
        "anchor_date": anchor["date"],
        "anchor_value": anchor_value,
        "points": points,
        "returns": returns,
    }


def _risk_metrics(points: list[dict[str, Any]], returns: dict[str, float]) -> tuple[float | None, float | None, float | None]:
    values = [1.0 + float(point["value"]) for point in points]
    max_drawdown = None
    if values:
        peak = values[0]
        drawdowns: list[float] = []
        for value in values:
            peak = max(peak, value)
            drawdowns.append(value / peak - 1.0)
        max_drawdown = min(drawdowns)
    daily = list(returns.values())
    if len(daily) < 2:
        return None, None, max_drawdown
    daily_std = statistics.stdev(daily)
    volatility = daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = None
    if daily_std > 0:
        sharpe = statistics.mean(daily) / daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)
    return volatility, sharpe, max_drawdown


def _season_trading_metrics(
    detail: dict[str, Any],
    effective_date: str,
    anchor_value: float | None,
) -> tuple[float | None, float, float | None]:
    gross = 0.0
    cost = 0.0
    for trade in detail.get("trades", {}).get("rows", []):
        if not isinstance(trade, dict):
            continue
        trade_date = str(trade.get("trade_date") or trade.get("date") or "")
        if trade_date and trade_date < effective_date:
            continue
        amount = _number(trade.get("gross_amount"))
        if amount is not None:
            gross += abs(amount)
        for key in ("commission", "stamp_tax", "slippage"):
            value = _number(trade.get(key))
            if value is not None:
                cost += abs(value)
    turnover = gross / anchor_value if anchor_value and anchor_value > 0 else None
    cost_bps = cost / gross * 10_000.0 if gross > 0 else None
    return turnover, cost, cost_bps


def _holding_rows(detail: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    positions = [
        row
        for row in detail.get("positions", {}).get("rows", [])
        if isinstance(row, dict) and str(row.get("code") or "")
    ]
    if positions:
        return "positions", positions
    buys = [
        row
        for row in detail.get("orders", {}).get("rows", [])
        if isinstance(row, dict)
        and str(row.get("code") or "")
        and str(row.get("side") or "").lower() == "buy"
    ]
    return "planned_orders", buys


def _row_value(row: dict[str, Any], source: str) -> float:
    keys = ("market_value",) if source == "positions" else (
        "target_value",
        "target_weight",
        "gross_amount",
    )
    for key in keys:
        value = _number(row.get(key))
        if value is not None and value > 0:
            return value
    shares = _number(row.get("shares"))
    price = _number(row.get("price") or row.get("last_price"))
    if shares is not None and price is not None:
        return max(0.0, shares * price)
    return 1.0


def _allocations(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    totals: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        label = str(
            row.get("exposure_group")
            or row.get("industry")
            or row.get("account_label")
            or "未分类"
        )
        totals[label] += _row_value(row, source)
    total = sum(totals.values())
    output = [
        {"label": label, "value": value, "weight": value / total if total > 0 else None}
        for label, value in totals.items()
    ]
    return sorted(output, key=lambda item: (-item["value"], item["label"]))


def _correlation(left: dict[str, float], right: dict[str, float]) -> float | None:
    dates = sorted(set(left) & set(right))
    if len(dates) < 2:
        return None
    left_values = [left[date] for date in dates]
    right_values = [right[date] for date in dates]
    left_std = statistics.stdev(left_values)
    right_std = statistics.stdev(right_values)
    if left_std == 0 or right_std == 0:
        return None
    left_mean = statistics.mean(left_values)
    right_mean = statistics.mean(right_values)
    covariance = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_values, right_values)
    ) / (len(dates) - 1)
    return covariance / (left_std * right_std)


def _jaccard(left: set[str], right: set[str]) -> float | None:
    union = left | right
    return len(left & right) / len(union) if union else None


def _underlying_index_set(detail: dict[str, Any]) -> set[str]:
    return {
        str(row.get("index_key"))
        for row in detail.get("lookthrough", {}).get("indexes", [])
        if isinstance(row, dict) and row.get("index_key")
    }


def _underlying_company_weights(detail: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    lookthrough = detail.get("lookthrough", {})
    for row in lookthrough.get("companies", []):
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        weight = _number(row.get("weight"))
        if weight is not None and weight >= 0:
            output[str(row["symbol"])] = weight
    for symbol in lookthrough.get("company_symbols", []):
        output.setdefault(str(symbol), 0.0)
    return output


def _weighted_overlap(left: dict[str, float], right: dict[str, float]) -> float | None:
    keys = set(left) | set(right)
    if not keys:
        return None
    numerator = sum(min(left.get(key, 0.0), right.get(key, 0.0)) for key in keys)
    denominator = sum(max(left.get(key, 0.0), right.get(key, 0.0)) for key in keys)
    return numerator / denominator if denominator > 0 else None


def _factor_rows(details: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_agent = {agent: _strategy_factors(details[agent]) for agent in PAIR_SLOTS}
    keys = set().union(*(set(factors) for factors in by_agent.values()))
    rows: list[dict[str, Any]] = []
    for key in keys:
        metadata = factor_metadata(key)
        row: dict[str, Any] = {"key": key, **metadata}
        for agent in PAIR_SLOTS:
            factor = by_agent[agent].get(key, {})
            row[agent] = {
                "weight": _number(factor.get("weight")) or 0.0,
                "direction": factor.get("direction"),
            }
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            -max(row[agent]["weight"] for agent in PAIR_SLOTS),
            row["key"],
        ),
    )


def build_strategy_comparison(
    market: str,
    details: dict[str, dict[str, Any]],
    *,
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Project two account details into one season-aware comparison payload."""

    missing = [agent for agent in PAIR_SLOTS if agent not in details]
    if missing:
        raise ValueError(f"strategy_comparison_missing:{','.join(missing)}")
    effective_date = str(registry["effective_date"])
    nav = {
        agent: _season_nav(details[agent], effective_date)
        for agent in PAIR_SLOTS
    }
    strategy_rows: dict[str, dict[str, Any]] = {}
    holding_sets: dict[str, set[str]] = {}
    for agent in PAIR_SLOTS:
        detail = details[agent]
        slot = dict(registry["slots"][agent])
        points = nav[agent]["points"]
        latest = points[-1] if points else None
        season_return = latest["value"] if latest else None
        benchmark_return = latest["benchmark"] if latest else None
        volatility, sharpe, max_drawdown = _risk_metrics(points, nav[agent]["returns"])
        turnover, trading_cost, cost_bps = _season_trading_metrics(
            detail,
            effective_date,
            nav[agent]["anchor_value"],
        )
        holdings_source, holdings = _holding_rows(detail)
        holding_sets[agent] = {str(row.get("code")) for row in holdings if row.get("code")}
        latest_total = _number(latest.get("total_value")) if latest else None
        latest_cash = _number(latest.get("cash")) if latest else None
        strategy_rows[agent] = {
            "agent": agent,
            "label": str(slot.get("label") or agent),
            "description": str(slot.get("description") or ""),
            "color": str(slot.get("color") or "#8391a3"),
            "strategy_id": detail.get("strategy", {}).get("strategy_id"),
            "strategy_name": detail.get("strategy", {}).get("name"),
            "holdings_source": holdings_source,
            "allocations": _allocations(holdings, holdings_source),
            "lookthrough": detail.get("lookthrough", {}),
            "metrics": {
                "season_return": season_return,
                "benchmark_return": benchmark_return,
                "excess_return": (
                    season_return - benchmark_return
                    if season_return is not None and benchmark_return is not None
                    else None
                ),
                "annualized_volatility": volatility,
                "sharpe": sharpe,
                "max_drawdown": max_drawdown,
                "cash_ratio": (
                    latest_cash / latest_total
                    if latest_cash is not None and latest_total and latest_total > 0
                    else None
                ),
                "turnover": turnover,
                "trading_cost": trading_cost,
                "cost_bps": cost_bps,
                "position_count": len(holdings),
                "pending_order_count": int(detail.get("orders", {}).get("summary", {}).get("total") or 0),
                "trade_count": int(detail.get("trades", {}).get("summary", {}).get("total") or 0),
            },
        }

    union = holding_sets[PAIR_SLOTS[0]] | holding_sets[PAIR_SLOTS[1]]
    overlap = (
        len(holding_sets[PAIR_SLOTS[0]] & holding_sets[PAIR_SLOTS[1]]) / len(union)
        if union
        else None
    )
    underlying_indexes = {
        agent: _underlying_index_set(details[agent]) for agent in PAIR_SLOTS
    }
    underlying_companies = {
        agent: _underlying_company_weights(details[agent]) for agent in PAIR_SLOTS
    }
    overlay_like: dict[str, dict[str, Any]] = {}
    for agent in PAIR_SLOTS:
        overlay_like[agent] = {
            "factors": {
                key: {"weight": factor["weight"]}
                for key, factor in _strategy_factors(details[agent]).items()
            }
        }
    all_dates = sorted(
        set().union(*(
            {point["date"] for point in nav[agent]["points"]}
            for agent in PAIR_SLOTS
        ))
    )
    nav_lookup = {
        agent: {point["date"]: point for point in nav[agent]["points"]}
        for agent in PAIR_SLOTS
    }
    nav_series: list[dict[str, Any]] = []
    for date in all_dates:
        benchmark_values = [
            nav_lookup[agent][date]["benchmark"]
            for agent in PAIR_SLOTS
            if date in nav_lookup[agent] and nav_lookup[agent][date]["benchmark"] is not None
        ]
        nav_series.append(
            {
                "date": date,
                **{
                    agent: nav_lookup[agent].get(date, {}).get("value")
                    for agent in PAIR_SLOTS
                },
                "benchmark": statistics.mean(benchmark_values) if benchmark_values else None,
            }
        )
    anchor_dates = [nav[agent]["anchor_date"] for agent in PAIR_SLOTS if nav[agent]["anchor_date"]]
    return {
        "market": market,
        "season": {
            "id": registry["season_id"],
            "name": registry["name"],
            "effective_date": effective_date,
            "anchor_date": min(anchor_dates) if anchor_dates else None,
        },
        "strategies": strategy_rows,
        "pair": {
            "position_overlap": overlap,
            "underlying_index_overlap": _jaccard(
                underlying_indexes[PAIR_SLOTS[0]],
                underlying_indexes[PAIR_SLOTS[1]],
            ),
            "underlying_company_overlap": _jaccard(
                set(underlying_companies[PAIR_SLOTS[0]]),
                set(underlying_companies[PAIR_SLOTS[1]]),
            ),
            "weighted_company_overlap": _weighted_overlap(
                underlying_companies[PAIR_SLOTS[0]],
                underlying_companies[PAIR_SLOTS[1]],
            ),
            "return_correlation": _correlation(
                nav[PAIR_SLOTS[0]]["returns"],
                nav[PAIR_SLOTS[1]]["returns"],
            ),
            "factor_distance": factor_weight_distance(
                overlay_like[PAIR_SLOTS[0]],
                overlay_like[PAIR_SLOTS[1]],
            ),
            "factor_distance_floor": _number(registry.get("factor_distance_floor")),
        },
        "nav_series": nav_series,
        "factor_rows": _factor_rows(details),
    }


__all__ = ["build_strategy_comparison"]
