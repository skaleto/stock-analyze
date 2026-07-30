"""Season-aware metrics for the two product-facing strategy slots."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from .dashboard_finance import factor_metadata
from .strategy_registry import PAIR_SLOTS, factor_weight_distance


TRADING_DAYS_PER_YEAR = 252
DEFAULT_DISTINCTNESS_THRESHOLDS = {
    "max_weighted_position_overlap": 0.70,
    "max_return_correlation": 0.85,
    "max_daily_decision_agreement": 0.80,
    "min_factor_exposure_distance": 0.45,
    "min_turnover_style_distance": 0.20,
    "min_return_observations": 20,
    "min_decision_days": 5,
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strategy_factors(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    strategy = detail.get("strategy")
    if not isinstance(strategy, dict):
        return output
    for raw in strategy.get("factors", []):
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


def _holding_weights(rows: list[dict[str, Any]], source: str) -> dict[str, float]:
    values: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        code = str(row.get("code") or row.get("ts_code") or "")
        if not code:
            continue
        values[code] += _row_value(row, source)
    total = sum(values.values())
    if total <= 0:
        return {}
    return {code: value / total for code, value in sorted(values.items())}


def _position_weight_overlap(
    left: dict[str, float],
    right: dict[str, float],
) -> float | None:
    if not left or not right:
        return None
    return sum(
        min(left.get(code, 0.0), right.get(code, 0.0))
        for code in set(left) | set(right)
    )


def _safe_factor_distance(
    left: dict[str, Any],
    right: dict[str, Any],
) -> float | None:
    try:
        return factor_weight_distance(left, right)
    except ValueError:
        return None


def _decision_date(row: dict[str, Any]) -> str:
    for key in (
        "decision_date",
        "signal_date",
        "created_at",
        "submitted_at",
        "trade_date",
        "date",
    ):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
        digits = "".join(character for character in raw if character.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def _decision_side(row: dict[str, Any]) -> str:
    raw = str(row.get("side") or row.get("action") or "").strip().lower()
    if raw in {"buy", "b", "买", "买入"}:
        return "buy"
    if raw in {"sell", "s", "卖", "卖出"}:
        return "sell"
    return ""


def _daily_decisions(
    detail: dict[str, Any],
    effective_date: str,
) -> dict[str, set[tuple[str, str]]]:
    selected: list[dict[str, Any]] = []
    for key in ("decisions", "orders", "trades"):
        container = detail.get(key)
        rows = container.get("rows", []) if isinstance(container, dict) else []
        selected = [row for row in rows if isinstance(row, dict)]
        if selected:
            break
    output: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in selected:
        date = _decision_date(row)
        code = str(row.get("code") or row.get("ts_code") or "").strip()
        side = _decision_side(row)
        if not date or date < effective_date or not code or not side:
            continue
        output[date].add((code, side))
    return dict(sorted(output.items()))


def _decision_agreement(
    left: dict[str, set[tuple[str, str]]],
    right: dict[str, set[tuple[str, str]]],
) -> tuple[float | None, int]:
    dates = sorted(set(left) | set(right))
    if not dates:
        return None, 0
    daily_scores: list[float] = []
    for date in dates:
        left_actions = left.get(date, set())
        right_actions = right.get(date, set())
        union = left_actions | right_actions
        daily_scores.append(
            len(left_actions & right_actions) / len(union) if union else 1.0
        )
    return statistics.mean(daily_scores), len(dates)


def _turnover_style_distance(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    scale = max(abs(left), abs(right))
    if scale == 0:
        return 0.0
    return min(1.0, abs(left - right) / scale)


def _distinctness_thresholds(registry: dict[str, Any]) -> dict[str, float | int]:
    thresholds: dict[str, float | int] = dict(DEFAULT_DISTINCTNESS_THRESHOLDS)
    factor_floor = _number(registry.get("factor_distance_floor"))
    if factor_floor is not None:
        thresholds["min_factor_exposure_distance"] = factor_floor
    overrides = registry.get("distinctness_thresholds")
    if not isinstance(overrides, dict):
        return thresholds
    for key, default in tuple(thresholds.items()):
        value = _number(overrides.get(key))
        if value is None or value < 0:
            continue
        thresholds[key] = int(value) if isinstance(default, int) else value
    return thresholds


def _distinctness_payload(
    *,
    weighted_position_overlap: float | None,
    return_correlation: float | None,
    return_observations: int,
    daily_decision_agreement: float | None,
    decision_days: int,
    factor_exposure_distance: float | None,
    turnover_style_distance: float | None,
    position_counts: dict[str, int],
    factor_counts: dict[str, int],
    thresholds: dict[str, float | int],
) -> dict[str, Any]:
    metrics = {
        "weighted_position_overlap": weighted_position_overlap,
        "return_correlation": return_correlation,
        "daily_decision_agreement": daily_decision_agreement,
        "factor_exposure_distance": factor_exposure_distance,
        "turnover_style_distance": turnover_style_distance,
    }
    sample_sizes = {
        "return_observations": return_observations,
        "decision_days": decision_days,
        "position_counts": position_counts,
        "factor_counts": factor_counts,
    }
    breaches: list[dict[str, Any]] = []
    insufficient = False

    minimum_samples = (
        ("return_observations", return_observations, int(thresholds["min_return_observations"])),
        ("decision_days", decision_days, int(thresholds["min_decision_days"])),
    )
    for metric, actual, required in minimum_samples:
        if actual >= required:
            continue
        insufficient = True
        breaches.append(
            {
                "metric": metric,
                "reason": "insufficient_samples",
                "actual": actual,
                "operator": ">=",
                "threshold": required,
            }
        )

    for metric, actual in metrics.items():
        if actual is not None:
            continue
        insufficient = True
        breaches.append(
            {
                "metric": metric,
                "reason": "missing_evidence",
                "actual": None,
                "operator": "available",
                "threshold": True,
            }
        )

    checks = (
        (
            "weighted_position_overlap",
            weighted_position_overlap,
            "<=",
            float(thresholds["max_weighted_position_overlap"]),
        ),
        (
            "return_correlation",
            return_correlation,
            "<=",
            float(thresholds["max_return_correlation"]),
        ),
        (
            "daily_decision_agreement",
            daily_decision_agreement,
            "<=",
            float(thresholds["max_daily_decision_agreement"]),
        ),
        (
            "factor_exposure_distance",
            factor_exposure_distance,
            ">=",
            float(thresholds["min_factor_exposure_distance"]),
        ),
        (
            "turnover_style_distance",
            turnover_style_distance,
            ">=",
            float(thresholds["min_turnover_style_distance"]),
        ),
    )
    if not insufficient:
        for metric, actual, operator, threshold in checks:
            assert actual is not None
            passed = actual <= threshold if operator == "<=" else actual >= threshold
            if passed:
                continue
            breaches.append(
                {
                    "metric": metric,
                    "reason": "threshold_breach",
                    "actual": actual,
                    "operator": operator,
                    "threshold": threshold,
                }
            )

    score = None
    if not insufficient:
        assert all(value is not None for value in metrics.values())
        score = statistics.mean(
            (
                1.0 - float(weighted_position_overlap),
                (1.0 - float(return_correlation)) / 2.0,
                1.0 - float(daily_decision_agreement),
                float(factor_exposure_distance),
                float(turnover_style_distance),
            )
        )
        score = min(1.0, max(0.0, score))

    status = "insufficient_samples" if insufficient else ("breached" if breaches else "qualified")
    return {
        **metrics,
        "distinctness_score": score,
        "thresholds": thresholds,
        "sample_sizes": sample_sizes,
        "breaches": breaches,
        "qualified": status == "qualified",
        "status": status,
    }


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
    holding_weights: dict[str, dict[str, float]] = {}
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
        holding_weights[agent] = _holding_weights(holdings, holdings_source)
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
    factor_distance = _safe_factor_distance(
        overlay_like[PAIR_SLOTS[0]],
        overlay_like[PAIR_SLOTS[1]],
    )
    return_correlation = _correlation(
        nav[PAIR_SLOTS[0]]["returns"],
        nav[PAIR_SLOTS[1]]["returns"],
    )
    return_observations = len(
        set(nav[PAIR_SLOTS[0]]["returns"]) & set(nav[PAIR_SLOTS[1]]["returns"])
    )
    decisions = {
        agent: _daily_decisions(details[agent], effective_date)
        for agent in PAIR_SLOTS
    }
    decision_agreement, decision_days = _decision_agreement(
        decisions[PAIR_SLOTS[0]],
        decisions[PAIR_SLOTS[1]],
    )
    weighted_position_overlap = _position_weight_overlap(
        holding_weights[PAIR_SLOTS[0]],
        holding_weights[PAIR_SLOTS[1]],
    )
    turnover_style_distance = _turnover_style_distance(
        strategy_rows[PAIR_SLOTS[0]]["metrics"]["turnover"],
        strategy_rows[PAIR_SLOTS[1]]["metrics"]["turnover"],
    )
    distinctness = _distinctness_payload(
        weighted_position_overlap=weighted_position_overlap,
        return_correlation=return_correlation,
        return_observations=return_observations,
        daily_decision_agreement=decision_agreement,
        decision_days=decision_days,
        factor_exposure_distance=factor_distance,
        turnover_style_distance=turnover_style_distance,
        position_counts={
            agent: len(holding_weights[agent])
            for agent in PAIR_SLOTS
        },
        factor_counts={
            agent: len(overlay_like[agent]["factors"])
            for agent in PAIR_SLOTS
        },
        thresholds=_distinctness_thresholds(registry),
    )
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
            "return_correlation": return_correlation,
            "factor_distance": factor_distance,
            "factor_distance_floor": _number(registry.get("factor_distance_floor")),
            "distinctness": distinctness,
        },
        "nav_series": nav_series,
        "factor_rows": _factor_rows(details),
    }


__all__ = ["build_strategy_comparison"]
