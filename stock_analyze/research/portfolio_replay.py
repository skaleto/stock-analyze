"""Executable daily portfolio replay for classical-model evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..execution_costs import calculate_execution_fill
from .execution_policy import estimate_execution_cost
from .strategy_ensemble import apply_cost_aware_transition, risk_adjusted_target_weights


SIMULATOR_VERSION = "paper-parity-daily-v1"


@dataclass(frozen=True)
class PortfolioReplayResult:
    metrics: dict[str, Any]
    periods: pd.DataFrame
    trades: pd.DataFrame
    nav: pd.DataFrame
    decisions: pd.DataFrame


def _annualized_return(values: pd.Series, periods_per_year: float = 252.0) -> float:
    returns = pd.to_numeric(values, errors="coerce").dropna().clip(lower=-0.99)
    if returns.empty:
        return 0.0
    cumulative = float(np.prod(1.0 + returns.to_numpy(dtype=float)))
    return float(cumulative ** (periods_per_year / len(returns)) - 1.0) if cumulative > 0 else -1.0


def _normalized_nav(values: pd.Series) -> pd.Series:
    returns = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=-0.99)
    return pd.Series(
        np.concatenate((
            np.array([1.0], dtype=float),
            np.cumprod(1.0 + returns.to_numpy(dtype=float)),
        )),
        dtype=float,
    )


def _annualized_nav_return(
    nav: pd.Series,
    *,
    periods_per_year: float = 252.0,
) -> float:
    values = pd.to_numeric(nav, errors="coerce").dropna()
    periods = len(values) - 1
    if periods <= 0 or float(values.iloc[0]) <= 0.0:
        return 0.0
    growth = float(values.iloc[-1]) / float(values.iloc[0])
    return float(growth ** (periods_per_year / periods) - 1.0) if growth > 0.0 else -1.0


def cumulative_relative_wealth(
    portfolio_nav: pd.Series,
    benchmark_nav: pd.Series,
) -> float:
    """Return portfolio wealth relative to benchmark wealth over one window."""

    portfolio = pd.to_numeric(pd.Series(portfolio_nav), errors="coerce").dropna()
    benchmark = pd.to_numeric(pd.Series(benchmark_nav), errors="coerce").dropna()
    if len(portfolio) < 2 or len(benchmark) < 2:
        return 0.0
    portfolio_start = float(portfolio.iloc[0])
    benchmark_start = float(benchmark.iloc[0])
    benchmark_end = float(benchmark.iloc[-1])
    if portfolio_start <= 0.0 or benchmark_start <= 0.0 or benchmark_end <= 0.0:
        return 0.0
    portfolio_growth = float(portfolio.iloc[-1]) / portfolio_start
    benchmark_growth = benchmark_end / benchmark_start
    return float(portfolio_growth / benchmark_growth - 1.0)


def relative_wealth_max_drawdown(
    portfolio_nav: pd.Series,
    benchmark_nav: pd.Series,
) -> float:
    portfolio = pd.to_numeric(pd.Series(portfolio_nav), errors="coerce")
    benchmark = pd.to_numeric(pd.Series(benchmark_nav), errors="coerce")
    aligned = pd.concat([portfolio, benchmark], axis=1).dropna()
    if aligned.empty or bool((aligned.iloc[:, 1] <= 0.0).any()):
        return 0.0
    relative = aligned.iloc[:, 0].to_numpy(dtype=float) / aligned.iloc[:, 1].to_numpy(
        dtype=float
    )
    peaks = np.maximum.accumulate(relative)
    return float(np.max(1.0 - relative / np.where(peaks > 0.0, peaks, 1.0)))


def annualized_relative_wealth_excess(
    portfolio_nav: pd.Series,
    benchmark_nav: pd.Series,
    *,
    periods_per_year: float = 252.0,
) -> float:
    """Annualize the ratio of portfolio wealth to benchmark wealth."""

    periods = min(len(portfolio_nav), len(benchmark_nav)) - 1
    if periods <= 0:
        return 0.0
    relative_growth = 1.0 + cumulative_relative_wealth(portfolio_nav, benchmark_nav)
    return (
        float(relative_growth ** (periods_per_year / periods) - 1.0)
        if relative_growth > 0.0 else -1.0
    )


def _performance_contract(contract: Mapping[str, Any]) -> tuple[float, float]:
    performance = contract.get("performance")
    performance = performance if isinstance(performance, Mapping) else {}
    annual_risk_free = float(
        performance.get(
            "risk_free_rate",
            contract.get("annual_risk_free_rate", contract.get("risk_free_rate", 0.02)),
        )
        or 0.0
    )
    periods_per_year = float(
        performance.get(
            "trading_days_per_year",
            contract.get("periods_per_year", 252.0),
        )
        or 252.0
    )
    return annual_risk_free, max(periods_per_year, 1.0)


def _drawdown(values: pd.Series) -> float:
    returns = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=-0.99)
    if returns.empty:
        return 1.0
    curve = np.cumprod(1.0 + returns.to_numpy(dtype=float))
    return abs(float(np.min(curve / np.maximum.accumulate(curve) - 1.0)))


def _lot_size(trading: Mapping[str, Any]) -> int:
    return max(int(trading.get("lot_size") or trading.get("lot_size_default") or 100), 1)


def _execution_date(group: pd.DataFrame) -> str:
    dates = group.get("entry_date", pd.Series(dtype=str)).dropna().astype(str)
    if dates.empty:
        return ""
    counts = dates.value_counts()
    return str(sorted(counts.loc[counts.eq(counts.max())].index)[0])


def _return_history_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "code", "return_1"}
    if required.difference(frame.columns):
        return pd.DataFrame()
    source = frame.loc[:, ["trade_date", "code", "return_1"]].copy()
    source["trade_date"] = source["trade_date"].astype(str)
    source["code"] = source["code"].astype(str).str.zfill(6)
    source["return_1"] = pd.to_numeric(source["return_1"], errors="coerce")
    return source.pivot_table(
        index="trade_date",
        columns="code",
        values="return_1",
        aggfunc="last",
    ).sort_index()


def _trailing_return_history(
    frame: pd.DataFrame,
    *,
    signal_date: str,
    lookback_sessions: int,
    minimum_sessions: int,
) -> pd.DataFrame | None:
    """Return only close-to-close observations known by the signal close."""

    history = (
        _return_history_matrix(frame)
        if {"trade_date", "code", "return_1"}.issubset(frame.columns)
        else frame.copy()
    )
    if history.empty:
        return None
    history.index = history.index.astype(str)
    eligible = history.loc[history.index <= str(signal_date)].tail(
        max(int(lookback_sessions), 1)
    )
    if len(eligible) < max(int(minimum_sessions), 1):
        return None
    return eligible


def _price_map(group: pd.DataFrame, *, entry_date: str | None = None) -> dict[str, float]:
    eligible = group
    if entry_date and "entry_date" in eligible.columns:
        eligible = eligible.loc[eligible["entry_date"].astype(str).eq(str(entry_date))]
    prices = pd.to_numeric(eligible["entry_price"], errors="coerce")
    return {
        str(code).zfill(6): float(price)
        for code, price in zip(eligible["code"].astype(str), prices)
        if pd.notna(price) and float(price) > 0.0
    }


def _allocate_residual_lots(
    targets: Mapping[str, int],
    *,
    desired_weights: Mapping[str, float],
    prices: Mapping[str, float],
    nav_before: float,
    lot: int,
    max_single_weight: float,
) -> dict[str, int]:
    """Use rounding cash without changing the strategy's target exposure."""

    result = {str(code): max(int(shares), 0) for code, shares in targets.items()}
    positive_weights = {
        str(code): max(float(weight), 0.0)
        for code, weight in desired_weights.items()
        if float(weight) > 0.0 and float(prices.get(str(code), 0.0)) > 0.0
    }
    target_exposure = min(sum(positive_weights.values()), 1.0)
    budget = max(float(nav_before), 0.0) * target_exposure
    if budget <= 0.0 or not positive_weights:
        return result
    total_target = sum(
        int(result.get(code, 0)) * float(prices.get(code, 0.0))
        for code in positive_weights
    )
    while True:
        candidates: list[tuple[float, str, float]] = []
        for code, desired_weight in positive_weights.items():
            price = float(prices[code])
            lot_value = price * lot
            current_value = int(result.get(code, 0)) * price
            shortfall = nav_before * desired_weight - current_value
            single_cap = nav_before * max_single_weight
            if shortfall <= 1e-9:
                continue
            if total_target + lot_value > budget + 1e-8:
                continue
            if current_value + lot_value > single_cap + 1e-8:
                continue
            candidates.append((shortfall / nav_before, code, lot_value))
        if not candidates:
            break
        _, code, lot_value = max(candidates, key=lambda item: (item[0], -int(item[1])))
        result[code] = int(result.get(code, 0)) + lot
        total_target += lot_value
    return result


def _flag(value: object, *, default: bool = False) -> bool:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _mechanical_rule_transition(
    group: pd.DataFrame,
    *,
    state: Mapping[str, Any],
    prices: Mapping[str, float],
    nav_before: float,
    account: Mapping[str, Any],
    trading: Mapping[str, Any],
    policy: Mapping[str, Any],
    signal_date: str,
) -> tuple[dict[str, int], set[str], list[dict[str, Any]]]:
    """Build rule targets from ranks without using a forecast-edge gate."""

    top_n = max(int(account.get("top_n") or 1), 1)
    reserve = min(max(float(account.get("cash_reserve_pct") or 0.0), 0.0), 0.95)
    max_single_weight = min(max(float(trading.get("max_single_weight") or 1.0), 0.0), 1.0)
    lot = _lot_size(trading)
    hold_buffer = max(
        float(policy.get("rank_buffer_pct", account.get("hold_buffer_pct") or 0.0)),
        0.0,
    )
    buffer_count = max(top_n, int(np.ceil(top_n * (1.0 + hold_buffer))))
    industry_column = str(policy.get("industry_column") or "industry")
    unclassified = str(policy.get("industry_unclassified_label") or "unclassified")
    max_industry_weight = min(max(float(policy.get("max_industry_weight") or 1.0), 0.0), 1.0)
    minimum_change = max(float(policy.get("minimum_target_change") or 0.0), 0.0)
    partial_rate = min(max(float(policy.get("partial_adjustment_rate") or 1.0), 0.0), 1.0)
    max_turnover = min(max(float(policy.get("max_daily_turnover") or 1.0), 0.0), 1.0)
    max_holding_days = max(int(policy.get("max_holding_days") or 0), 0)

    ranked = group.copy()
    if "_is_cash_placeholder" in ranked.columns:
        ranked = ranked.loc[~ranked["_is_cash_placeholder"].fillna(False).astype(bool)].copy()
    if "_eligible_for_selection" in ranked.columns:
        ranked = ranked.loc[
            ranked["_eligible_for_selection"].fillna(False).astype(bool)
        ].copy()
    ranked["_code"] = ranked["code"].astype(str).str.zfill(6)
    ranked["_score"] = pd.to_numeric(ranked["score"], errors="coerce").fillna(-np.inf)
    ranked = (
        ranked.sort_values(["_score", "_code"], ascending=[False, True], kind="stable")
        .drop_duplicates("_code", keep="first")
        .reset_index(drop=True)
    )
    ranked["_rank"] = np.arange(1, len(ranked) + 1)
    by_code = ranked.set_index("_code", drop=False)
    current_weights = {
        str(code): (
            int(position.get("shares") or 0)
            * float(prices.get(code, position.get("last_price") or 0.0))
            / nav_before
        )
        for code, position in (state.get("positions") or {}).items()
    }
    expected_weight = min((1.0 - reserve) / top_n, max_single_weight)
    max_industry_names = (
        max(int(np.floor(max_industry_weight / max(expected_weight, 1e-12) + 1e-9)), 1)
        if max_industry_weight < 1.0 else top_n
    )

    hard_exits: set[str] = set()
    for code, position in (state.get("positions") or {}).items():
        row = by_code.loc[code] if code in by_code.index else pd.Series(dtype=object)
        if _flag(row.get("hard_risk_exit", row.get("risk_exit")), default=False):
            hard_exits.add(code)
            continue
        opened = pd.to_datetime(position.get("opened_date"), errors="coerce")
        current_day = pd.to_datetime(signal_date, errors="coerce")
        if (
            max_holding_days > 0
            and pd.notna(opened)
            and pd.notna(current_day)
            and int((current_day - opened).days) >= max_holding_days
        ):
            hard_exits.add(code)

    industry_counts: dict[str, int] = {}
    selected: list[str] = []

    def try_select(code: str) -> None:
        if code in selected or code in hard_exits or code not in by_code.index:
            return
        industry = str(by_code.loc[code].get(industry_column) or unclassified)
        if industry_counts.get(industry, 0) >= max_industry_names:
            return
        selected.append(code)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1

    retained = sorted(
        (
            code for code in current_weights
            if code in by_code.index
            and int(by_code.loc[code]["_rank"]) <= buffer_count
        ),
        key=lambda code: (int(by_code.loc[code]["_rank"]), code),
    )
    for code in retained:
        try_select(code)
        if len(selected) >= top_n:
            break
    if len(selected) < top_n:
        for code in ranked["_code"].astype(str):
            try_select(code)
            if len(selected) >= top_n:
                break

    target_weight = min((1.0 - reserve) / max(len(selected), 1), max_single_weight)
    desired = {code: target_weight for code in selected}
    all_codes = sorted(set(current_weights).union(desired))
    target_weights: dict[str, float] = {}
    decisions: list[dict[str, Any]] = []
    ordinary_deltas: dict[str, float] = {}
    for code in all_codes:
        current = max(float(current_weights.get(code, 0.0)), 0.0)
        aim = 0.0 if code in hard_exits else float(desired.get(code, 0.0))
        delta = aim - current
        reason = ""
        allowed = False
        if code in hard_exits and current > 0.0:
            target = 0.0
            allowed = True
            reason = "hard_risk_exit"
        elif abs(delta) < minimum_change:
            target = current
            reason = "target_change_below_band"
        elif partial_rate <= 0.0:
            target = current
            reason = "partial_adjustment_disabled"
        else:
            target = current + delta * partial_rate
            ordinary_deltas[code] = target - current
            allowed = True
        target_weights[code] = max(float(target), 0.0)
        decisions.append({
            "code": code,
            "rank": int(by_code.loc[code]["_rank"]) if code in by_code.index else None,
            "current_weight": current,
            "aim_weight": aim,
            "target_weight": max(float(target), 0.0),
            "trade_allowed": allowed,
            "no_trade_reason": reason,
            "partial_adjustment_rate": 1.0 if code in hard_exits else partial_rate,
        })

    buy_turnover = sum(max(delta, 0.0) for delta in ordinary_deltas.values())
    sell_turnover = sum(max(-delta, 0.0) for delta in ordinary_deltas.values())
    ordinary_turnover = max(buy_turnover, sell_turnover)
    scale = min(max_turnover / ordinary_turnover, 1.0) if ordinary_turnover > 0.0 else 1.0
    if scale < 1.0:
        decision_by_code = {str(item["code"]): item for item in decisions}
        for code, delta in ordinary_deltas.items():
            current = current_weights.get(code, 0.0)
            target_weights[code] = max(float(current + delta * scale), 0.0)
            decision_by_code[code]["target_weight"] = target_weights[code]
            decision_by_code[code]["partial_adjustment_rate"] = partial_rate * scale

    targets: dict[str, int] = {}
    for code, weight in target_weights.items():
        price = prices.get(code)
        if price is None or price <= 0.0:
            continue
        targets[code] = int((nav_before * weight / price) // lot) * lot
    return targets, {code for code, weight in target_weights.items() if weight > 0.0}, decisions


def _state_value(state: dict[str, Any], prices: Mapping[str, float]) -> float:
    positions = state.get("positions") or {}
    market_value = sum(
        int(position.get("shares") or 0)
        * float(prices.get(code, position.get("last_price") or 0.0))
        for code, position in positions.items()
    )
    unsettled = sum(float(item.get("amount") or 0.0) for item in state.get("settlement_queue") or [])
    return float(state.get("cash") or 0.0) + unsettled + market_value


def _release_settlement(state: dict[str, Any], entry_date: str) -> None:
    retained: list[dict[str, Any]] = []
    released = 0.0
    for item in state.get("settlement_queue") or []:
        if str(item.get("settle_date") or "") <= entry_date:
            released += float(item.get("amount") or 0.0)
        else:
            retained.append(item)
    state["cash"] = float(state.get("cash") or 0.0) + released
    state["settlement_queue"] = retained


def _next_settlement_date(entry_dates: list[str], index: int, days: int) -> str:
    target = min(index + max(days, 0), len(entry_dates) - 1)
    return entry_dates[target]


def _scheduled_rebalance_due(
    signal_dates: list[str],
    index: int,
    frequency: str,
) -> bool:
    normalized = str(frequency or "daily").strip().lower()
    if normalized == "daily":
        return True
    if normalized != "monthly":
        raise ValueError(f"portfolio_replay_rebalance_frequency:{normalized}")
    if index == 0:
        return True
    current = pd.to_datetime(signal_dates[index], errors="raise")
    previous = pd.to_datetime(signal_dates[index - 1], errors="raise")
    return current.to_period("M") != previous.to_period("M")


def _benchmark_aware_aim_weights(
    group: pd.DataFrame,
    *,
    top_n: int,
    max_single_weight: float,
    current_weights: Mapping[str, float],
    gross_exposure: float,
    allocation_policy: Mapping[str, Any],
    return_history_matrix: pd.DataFrame,
    signal_date: str,
    use_point_in_time_covariance: bool,
) -> tuple[dict[str, float], dict[str, object]]:
    benchmark_weights = {
        str(code).zfill(6): float(weight)
        for code, weight in zip(
            group["code"].astype(str),
            pd.to_numeric(group.get("benchmark_weight"), errors="coerce"),
        )
        if pd.notna(weight) and float(weight) > 0.0
    }
    if not benchmark_weights:
        raise ValueError("portfolio_replay_benchmark_weights_missing")
    candidates = group.copy()
    candidates["expected_volatility"] = pd.to_numeric(
        candidates.get("realized_volatility_20"),
        errors="coerce",
    )
    return_history = (
        _trailing_return_history(
            return_history_matrix,
            signal_date=signal_date,
            lookback_sessions=int(
                allocation_policy.get("covariance_lookback_sessions") or 90
            ),
            minimum_sessions=int(
                allocation_policy.get("covariance_min_history_sessions") or 60
            ),
        )
        if use_point_in_time_covariance else None
    )
    diagnostics: dict[str, object] = {}
    raw_tracking_error = allocation_policy.get("max_tracking_error")
    desired_weights = risk_adjusted_target_weights(
        candidates,
        top_n=top_n,
        max_single_weight=max_single_weight,
        current_weights=current_weights,
        return_history=return_history,
        gross_exposure=gross_exposure,
        group_constraints=dict(allocation_policy.get("group_constraints") or {}),
        exposure_constraints=dict(
            allocation_policy.get("exposure_constraints") or {}
        ),
        risk_aversion=float(allocation_policy.get("risk_aversion") or 1.0),
        active_risk_aversion=float(
            allocation_policy.get("active_risk_aversion") or 0.35
        ),
        cost_aversion=float(allocation_policy.get("cost_aversion") or 1.0),
        max_turnover=(
            1.0
            if not current_weights
            else float(allocation_policy.get("max_rebalance_turnover") or 1.0)
        ),
        max_tracking_error=(
            float(raw_tracking_error)
            if raw_tracking_error is not None else None
        ),
        benchmark_weights=benchmark_weights,
        diagnostics=diagnostics,
    )
    return desired_weights, diagnostics


def _account_path(
    frame: pd.DataFrame,
    *,
    account: Mapping[str, Any],
    trading: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None,
    rule_execution_policy: Mapping[str, Any] | None,
    allocation_policy: Mapping[str, Any] | None,
    rebalance_frequency: str,
    fold: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    account_id = str(account.get("id") or "account")
    initial_cash = float(account.get("cash") or 0.0)
    top_n = max(int(account.get("top_n") or 1), 1)
    max_single_weight = min(max(float(trading.get("max_single_weight") or 1.0), 0.0), 1.0)
    reserve = min(max(float(account.get("cash_reserve_pct") or 0.0), 0.0), 0.95)
    hold_buffer = max(float(account.get("hold_buffer_pct") or 0.20), 0.0)
    lot = _lot_size(trading)
    settlement_days = max(int(trading.get("settlement_days") or 0), 0)
    dates = sorted(frame["trade_date"].astype(str).unique())
    use_point_in_time_covariance = bool(
        allocation_policy
        and _flag(allocation_policy.get("use_point_in_time_covariance"))
    )
    return_history_matrix = (
        _return_history_matrix(frame)
        if use_point_in_time_covariance else pd.DataFrame()
    )
    groups = {
        str(day): group.sort_values(["score", "code"], ascending=[False, True], kind="stable")
        for day, group in frame.groupby("trade_date", sort=True)
    }
    entry_dates = [_execution_date(groups[day]) for day in dates]
    state: dict[str, Any] = {"cash": initial_cash, "positions": {}, "settlement_queue": []}
    selected_codes: set[str] = set()
    period_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for date_index, signal_date in enumerate(dates[:-1]):
        group = groups[signal_date]
        next_group = groups[dates[date_index + 1]]
        entry_date = entry_dates[date_index]
        next_entry_date = entry_dates[date_index + 1]
        _release_settlement(state, entry_date)
        prices = _price_map(group, entry_date=entry_date)
        next_prices = _price_map(next_group, entry_date=next_entry_date)
        nav_before = _state_value(state, prices)
        if nav_before <= 0.0:
            continue
        rebalance_due = _scheduled_rebalance_due(
            dates,
            date_index,
            rebalance_frequency,
        )
        group_by_code = group.assign(
            _code=group["code"].astype(str).str.zfill(6)
        ).set_index("_code")
        target_weight = min((1.0 - reserve) / max(len(selected_codes), 1), max_single_weight)
        targets: dict[str, int]
        desired_weights: dict[str, float] = {}
        decisions_by_code: dict[str, dict[str, Any]] = {}
        if not rebalance_due:
            targets = {}
            selected_codes = set(state["positions"])
            for code, position in sorted(state["positions"].items()):
                shares = int(position.get("shares") or 0)
                price = float(prices.get(code, position.get("last_price") or 0.0))
                current_weight = shares * price / nav_before if nav_before > 0.0 else 0.0
                row = (
                    group_by_code.loc[code]
                    if code in group_by_code.index
                    else pd.Series(dtype=object)
                )
                hard_risk_exit = _flag(
                    row.get("hard_risk_exit", row.get("risk_exit")),
                    default=False,
                )
                target_shares = 0 if hard_risk_exit else shares
                targets[code] = target_shares
                desired_weights[code] = 0.0 if hard_risk_exit else current_weight
                decision = {
                    "code": code,
                    "rank": None,
                    "current_weight": current_weight,
                    "aim_weight": desired_weights[code],
                    "target_weight": desired_weights[code],
                    "gross_expected_edge_bps": 0.0,
                    "round_trip_cost_bps": 0.0,
                    "uncertainty_bps": 0.0,
                    "net_expected_edge_bps": 0.0,
                    "trade_allowed": hard_risk_exit,
                    "no_trade_reason": (
                        "hard_risk_exit"
                        if hard_risk_exit
                        else "scheduled_rebalance_not_due"
                    ),
                    "partial_adjustment_rate": 1.0 if hard_risk_exit else 0.0,
                    "scheduled_rebalance": False,
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                }
                decisions_by_code[code] = decision
                decision_rows.append(decision)
        elif execution_policy:
            current_weights = {
                str(code): (
                    int(position.get("shares") or 0)
                    * float(prices.get(code, position.get("last_price") or 0.0))
                    / nav_before
                )
                for code, position in state["positions"].items()
            }
            allocation_diagnostics: dict[str, object] = {}
            if allocation_policy:
                aim_weights, allocation_diagnostics = _benchmark_aware_aim_weights(
                    group,
                    top_n=top_n,
                    max_single_weight=max_single_weight,
                    current_weights=current_weights,
                    gross_exposure=1.0 - reserve,
                    allocation_policy=allocation_policy,
                    return_history_matrix=return_history_matrix,
                    signal_date=signal_date,
                    use_point_in_time_covariance=use_point_in_time_covariance,
                )
            else:
                frictionless_codes = [
                    str(code).zfill(6)
                    for code in group.head(top_n)["code"].astype(str)
                ]
                target_weight = min(
                    (1.0 - reserve) / max(len(frictionless_codes), 1),
                    max_single_weight,
                )
                aim_weights = {
                    code: target_weight for code in frictionless_codes
                }
            target_weight = min(
                (1.0 - reserve) / max(len(aim_weights), 1),
                max_single_weight,
            )
            policy_candidates = group.copy()
            baseline_bps = (
                float(trading.get("slippage_bps") or 0.0)
                if trading.get("slippage_bps") is not None
                else float(trading.get("slippage_rate") or 0.0) * 10_000.0
            )
            commission_bps = float(trading.get("commission_rate") or 0.0) * 10_000.0
            stamp_bps = float(trading.get("stamp_tax_rate") or 0.0) * 10_000.0
            if "round_trip_cost_bps" not in policy_candidates.columns:
                policy_candidates["round_trip_cost_bps"] = [
                    2.0 * estimate_execution_cost(
                        order_value=nav_before * target_weight,
                        avg_daily_amount=pd.to_numeric(row.get("avg_amount_20"), errors="coerce"),
                        volatility=pd.to_numeric(row.get("realized_volatility_20"), errors="coerce"),
                        baseline_bps=baseline_bps,
                    ).total_bps
                    + 2.0 * commission_bps
                    + stamp_bps
                    for _, row in policy_candidates.iterrows()
                ]
            transition = apply_cost_aware_transition(
                policy_candidates,
                aim_weights=aim_weights,
                current_weights=current_weights,
                top_n=top_n,
                rank_buffer_pct=float(execution_policy.get("rank_buffer_pct", hold_buffer)),
                minimum_target_change=float(execution_policy.get("minimum_target_change", 0.0)),
                partial_adjustment_rate=float(execution_policy.get("partial_adjustment_rate", 1.0)),
                max_daily_turnover=float(execution_policy.get("max_daily_turnover", 1.0)),
                cost_safety_multiple=float(execution_policy.get("cost_safety_multiple", 1.0)),
                alpha_persistence=float(execution_policy.get("alpha_persistence", 1.0)),
                gross_exposure=1.0 - reserve,
            )
            selected_codes = set(transition.weights)
            desired_weights = {
                str(code): max(float(weight), 0.0)
                for code, weight in transition.weights.items()
            }
            targets = {}
            for code, weight in transition.weights.items():
                price = prices.get(code)
                if price is None:
                    continue
                targets[code] = int((nav_before * weight / price) // lot) * lot
            for raw in transition.decisions.to_dict(orient="records"):
                decision = {
                    **raw,
                    "allocation_policy_version": (
                        str(
                            allocation_policy.get("version")
                            or "benchmark-aware-topn-v1"
                        )
                        if allocation_policy else ""
                    ),
                    "optimizer_tracking_error": allocation_diagnostics.get(
                        "tracking_error"
                    ),
                    "optimizer_turnover": allocation_diagnostics.get("turnover"),
                    "optimizer_market_beta_source": allocation_diagnostics.get(
                        "market_beta_source"
                    ),
                    "scheduled_rebalance": True,
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                }
                decisions_by_code[str(raw["code"])] = decision
                decision_rows.append(decision)
        elif rule_execution_policy:
            targets, selected_codes, rule_decisions = _mechanical_rule_transition(
                group,
                state=state,
                prices=prices,
                nav_before=nav_before,
                account=account,
                trading=trading,
                policy=rule_execution_policy,
                signal_date=signal_date,
            )
            desired_weights = {
                str(item["code"]): max(float(item["target_weight"]), 0.0)
                for item in rule_decisions
            }
            for raw in rule_decisions:
                decision = {
                    **raw,
                    "scheduled_rebalance": True,
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                }
                decisions_by_code[str(raw["code"])] = decision
                decision_rows.append(decision)
        elif allocation_policy:
            current_weights = {
                str(code): (
                    int(position.get("shares") or 0)
                    * float(prices.get(code, position.get("last_price") or 0.0))
                    / nav_before
                )
                for code, position in state["positions"].items()
            }
            desired_weights, diagnostics = _benchmark_aware_aim_weights(
                group,
                top_n=top_n,
                max_single_weight=max_single_weight,
                current_weights=current_weights,
                gross_exposure=1.0 - reserve,
                allocation_policy=allocation_policy,
                return_history_matrix=return_history_matrix,
                signal_date=signal_date,
                use_point_in_time_covariance=use_point_in_time_covariance,
            )
            selected_codes = set(desired_weights)
            targets = {}
            rank_by_code = {
                str(code).zfill(6): rank
                for rank, code in enumerate(group["code"].astype(str), start=1)
            }
            for code, weight in desired_weights.items():
                price = prices.get(code)
                if price is None:
                    continue
                targets[code] = int((nav_before * weight / price) // lot) * lot
                current_weight = float(current_weights.get(code, 0.0))
                decision = {
                    "code": code,
                    "rank": rank_by_code.get(code),
                    "current_weight": current_weight,
                    "aim_weight": float(weight),
                    "target_weight": float(weight),
                    "trade_allowed": abs(float(weight) - current_weight) > 1e-12,
                    "no_trade_reason": "benchmark_aware_allocation",
                    "allocation_policy_version": str(
                        allocation_policy.get("version") or "benchmark-aware-topn-v1"
                    ),
                    "optimizer_tracking_error": diagnostics.get("tracking_error"),
                    "optimizer_turnover": diagnostics.get("turnover"),
                    "optimizer_market_beta_source": diagnostics.get(
                        "market_beta_source"
                    ),
                    "scheduled_rebalance": True,
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                }
                decisions_by_code[code] = decision
                decision_rows.append(decision)
        else:
            buffer_count = max(top_n, int(np.ceil(top_n * (1.0 + hold_buffer))))
            buffer_codes = set(group.head(buffer_count)["code"].astype(str).str.zfill(6))
            retained = selected_codes.intersection(buffer_codes)
            additions = [
                str(code).zfill(6)
                for code in group["code"].astype(str)
                if str(code).zfill(6) not in retained
            ][: max(top_n - len(retained), 0)]
            selected_codes = set([*retained, *additions])
            target_weight = min((1.0 - reserve) / max(len(selected_codes), 1), max_single_weight)
            desired_weights = {code: target_weight for code in selected_codes}
            targets = {}
            for code in selected_codes:
                price = prices.get(code)
                if price is None:
                    continue
                targets[code] = int((nav_before * target_weight / price) // lot) * lot
        targets = _allocate_residual_lots(
            targets,
            desired_weights=desired_weights,
            prices=prices,
            nav_before=nav_before,
            lot=lot,
            max_single_weight=max_single_weight,
        )
        for code in state["positions"]:
            targets.setdefault(code, 0)

        traded_gross = commission = stamp_tax = slippage = 0.0
        for side in ("sell", "buy"):
            for code, target_shares in sorted(targets.items()):
                current = int((state["positions"].get(code) or {}).get("shares") or 0)
                delta = int(target_shares) - current
                if (side == "sell" and delta >= 0) or (side == "buy" and delta <= 0):
                    continue
                reference_price = prices.get(code)
                if reference_price is None or reference_price <= 0.0:
                    continue
                shares = abs(delta)
                row = group_by_code.loc[code] if code in group_by_code.index else pd.Series(dtype=object)
                allowed_column = "entry_buy_allowed" if side == "buy" else "entry_sell_allowed"
                if allowed_column in row.index and not _flag(row.get(allowed_column), default=False):
                    if code in decisions_by_code:
                        decisions_by_code[code]["trade_allowed"] = False
                        decisions_by_code[code]["no_trade_reason"] = f"{side}_blocked_at_entry"
                    continue
                cost_estimate = estimate_execution_cost(
                    order_value=shares * reference_price,
                    avg_daily_amount=pd.to_numeric(row.get("avg_amount_20"), errors="coerce"),
                    volatility=pd.to_numeric(row.get("realized_volatility_20"), errors="coerce"),
                    baseline_bps=(
                        float(trading.get("slippage_bps") or 0.0)
                        if trading.get("slippage_bps") is not None
                        else float(trading.get("slippage_rate") or 0.0) * 10_000.0
                    ),
                )
                fill = calculate_execution_fill(
                    reference_price=reference_price,
                    shares=shares,
                    side=side,
                    trading=trading,
                    impact_bps=cost_estimate.total_bps,
                )
                if side == "buy":
                    while shares > 0 and float(state["cash"]) + fill.cash_delta < -1e-8:
                        shares -= lot
                        if shares <= 0:
                            break
                        fill = calculate_execution_fill(
                            reference_price=reference_price,
                            shares=shares,
                            side=side,
                            trading=trading,
                            impact_bps=cost_estimate.total_bps,
                        )
                    if shares <= 0:
                        continue
                    state["cash"] = float(state["cash"]) + fill.cash_delta
                    state["positions"][code] = {
                        "shares": current + shares,
                        "last_price": reference_price,
                        "opened_date": (
                            (state["positions"].get(code) or {}).get("opened_date")
                            or entry_date
                        ),
                    }
                else:
                    shares = min(shares, current)
                    if shares <= 0:
                        continue
                    if shares != fill.shares:
                        fill = calculate_execution_fill(
                            reference_price=reference_price,
                            shares=shares,
                            side=side,
                            trading=trading,
                            impact_bps=cost_estimate.total_bps,
                        )
                    if settlement_days:
                        state["settlement_queue"].append({
                            "settle_date": _next_settlement_date(entry_dates, date_index, settlement_days),
                            "amount": fill.cash_delta,
                        })
                    else:
                        state["cash"] = float(state["cash"]) + fill.cash_delta
                    remaining = current - shares
                    if remaining:
                        state["positions"][code] = {
                            "shares": remaining,
                            "last_price": reference_price,
                            "opened_date": (
                                (state["positions"].get(code) or {}).get("opened_date")
                                or entry_date
                            ),
                        }
                    else:
                        state["positions"].pop(code, None)
                traded_gross += fill.gross_amount
                commission += fill.commission
                stamp_tax += fill.stamp_tax
                slippage += fill.slippage
                trade_rows.append({
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                    "code": code,
                    "side": side,
                    "shares": shares,
                    "reference_price": reference_price,
                    "execution_price": fill.execution_price,
                    "gross_amount": fill.gross_amount,
                    "commission": fill.commission,
                    "stamp_tax": fill.stamp_tax,
                    "slippage": fill.slippage,
                    "impact_bps": fill.impact_bps,
                    "avg_daily_amount": pd.to_numeric(
                        row.get("avg_amount_20"), errors="coerce"
                    ),
                    "participation_rate": cost_estimate.participation_rate,
                    "liquidity_status": cost_estimate.liquidity_status,
                    "impact_capped": cost_estimate.capped,
                    **{
                        key: decisions_by_code.get(code, {}).get(key)
                        for key in (
                            "gross_expected_edge_bps",
                            "round_trip_cost_bps",
                            "uncertainty_bps",
                            "net_expected_edge_bps",
                            "partial_adjustment_rate",
                        )
                    },
                })
        beginning_market_value = sum(
            int(position.get("shares") or 0)
            * float(prices.get(code, position.get("last_price") or 0.0))
            for code, position in state["positions"].items()
        )
        beginning_capital_utilization = (
            beginning_market_value / nav_before if nav_before > 0.0 else 0.0
        )
        for code, position in state["positions"].items():
            position["last_price"] = next_prices.get(code, prices.get(code, position.get("last_price", 0.0)))
        nav_next = _state_value(state, next_prices)
        cash = float(state["cash"])
        unsettled_cash = sum(float(item["amount"]) for item in state["settlement_queue"])
        market_value = max(nav_next - cash - unsettled_cash, 0.0)
        cash_ratio = (cash + unsettled_cash) / nav_next if nav_next > 0.0 else 1.0
        capital_utilization = market_value / nav_next if nav_next > 0.0 else 0.0
        target_risky_exposure = 1.0 - reserve
        passive_cash_ratio = max(cash_ratio - reserve, 0.0)
        net_return = nav_next / nav_before - 1.0
        period_cost = commission + stamp_tax + slippage
        gross_return = (nav_next + period_cost) / nav_before - 1.0
        benchmark_now = float(pd.to_numeric(group["benchmark_entry_price"], errors="coerce").dropna().median())
        benchmark_next = float(pd.to_numeric(next_group["benchmark_entry_price"], errors="coerce").dropna().median())
        benchmark_return = benchmark_next / benchmark_now - 1.0 if benchmark_now > 0 else 0.0
        cost_return = period_cost / nav_before
        cash_position_effect = (
            beginning_capital_utilization - 1.0
        ) * benchmark_return
        security_selection_return = (
            gross_return
            - beginning_capital_utilization * benchmark_return
        )
        execution_cost_effect = -cost_return
        attribution_reconciliation_error = (
            net_return
            - benchmark_return
            - cash_position_effect
            - security_selection_return
            - execution_cost_effect
        )
        period_rows.append({
            "fold": fold,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "next_entry_date": next_entry_date,
            "account_id": account_id,
            "account_weight": initial_cash,
            "scheduled_rebalance": rebalance_due,
            "gross_return": gross_return,
            "cost_return": cost_return,
            "net_return": net_return,
            "benchmark_return": benchmark_return,
            "active_return": net_return - benchmark_return,
            "beginning_capital_utilization": beginning_capital_utilization,
            "cash_position_effect": cash_position_effect,
            "security_selection_return": security_selection_return,
            "execution_cost_effect": execution_cost_effect,
            "attribution_reconciliation_error": attribution_reconciliation_error,
            "turnover": traded_gross / nav_before,
            "cash_ratio": cash_ratio,
            "capital_utilization": capital_utilization,
            "target_risky_exposure": target_risky_exposure,
            "passive_cash_ratio": passive_cash_ratio,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "slippage": slippage,
            "traded_gross": traded_gross,
            "trade_count": sum(
                row["fold"] == fold and row["signal_date"] == signal_date and row["account_id"] == account_id
                for row in trade_rows
            ),
        })
        nav_rows.append({
            "fold": fold,
            "date": next_entry_date,
            "account_id": account_id,
            "cash": cash,
            "unsettled_cash": unsettled_cash,
            "market_value": market_value,
            "cash_ratio": cash_ratio,
            "capital_utilization": capital_utilization,
            "beginning_capital_utilization": beginning_capital_utilization,
            "target_risky_exposure": target_risky_exposure,
            "passive_cash_ratio": passive_cash_ratio,
            "nav": nav_next,
        })
    return period_rows, trade_rows, nav_rows, decision_rows


def replay_executable_portfolio(
    evaluation: pd.DataFrame,
    *,
    contract: Mapping[str, Any],
) -> PortfolioReplayResult:
    """Replay model scores under the same account and cost contract as paper trading."""

    required = {
        "account_id", "trade_date", "entry_date", "code", "score",
        "entry_price", "benchmark_entry_price",
    }
    missing = required.difference(evaluation.columns)
    if missing:
        raise ValueError(f"portfolio_replay_missing_columns:{','.join(sorted(missing))}")
    accounts = {
        str(account.get("id")): dict(account)
        for account in contract.get("accounts") or []
        if account.get("id")
    }
    if not accounts:
        raise ValueError("portfolio_replay_accounts_missing")
    trading = dict(contract.get("trading") or {})
    execution_policy = contract.get("execution_policy")
    if execution_policy is not None and not isinstance(execution_policy, Mapping):
        raise ValueError("portfolio_replay_execution_policy")
    rule_execution_policy = contract.get("rule_execution_policy")
    if rule_execution_policy is not None and not isinstance(rule_execution_policy, Mapping):
        raise ValueError("portfolio_replay_rule_execution_policy")
    allocation_policy = contract.get("allocation_policy")
    if allocation_policy is not None and not isinstance(allocation_policy, Mapping):
        raise ValueError("portfolio_replay_allocation_policy")
    rebalance_frequency = str(contract.get("rebalance_frequency") or "daily").strip().lower()
    if rebalance_frequency not in {"daily", "monthly"}:
        raise ValueError(f"portfolio_replay_rebalance_frequency:{rebalance_frequency}")
    frame = evaluation.copy()
    frame["account_id"] = frame["account_id"].astype(str)
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame["entry_date"] = frame["entry_date"].astype(str)
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["fold"] = frame.get("fold", 0).astype(str) if isinstance(frame.get("fold"), pd.Series) else "0"
    period_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for (account_id, fold), part in frame.groupby(["account_id", "fold"], sort=True):
        account = accounts.get(str(account_id))
        if account is None:
            continue
        periods, trades, nav, decisions = _account_path(
            part,
            account=account,
            trading=trading,
            execution_policy=execution_policy,
            rule_execution_policy=rule_execution_policy,
            allocation_policy=allocation_policy,
            rebalance_frequency=rebalance_frequency,
            fold=str(fold),
        )
        period_rows.extend(periods)
        trade_rows.extend(trades)
        nav_rows.extend(nav)
        decision_rows.extend(decisions)
    periods = pd.DataFrame(period_rows)
    trades = pd.DataFrame(trade_rows)
    nav = pd.DataFrame(nav_rows)
    decisions = pd.DataFrame(decision_rows)
    if periods.empty:
        raise ValueError("portfolio_replay_no_periods")
    weighted = periods.copy()
    aggregated_rows: list[dict[str, Any]] = []
    for signal_date, group in weighted.groupby("signal_date", sort=True):
        weights = pd.to_numeric(group["account_weight"], errors="coerce").fillna(0.0)
        weights = weights / weights.sum() if weights.sum() > 0 else pd.Series(1.0 / len(group), index=group.index)
        aggregated_rows.append({
            "signal_date": str(signal_date),
            **{
                column: float(np.sum(pd.to_numeric(group[column], errors="coerce").fillna(0.0) * weights))
                for column in (
                    "gross_return", "net_return", "benchmark_return", "active_return",
                    "turnover", "cash_ratio", "capital_utilization",
                    "beginning_capital_utilization", "cash_position_effect",
                    "security_selection_return", "execution_cost_effect",
                    "attribution_reconciliation_error",
                    "target_risky_exposure", "passive_cash_ratio",
                )
            },
        })
    aggregate = pd.DataFrame(aggregated_rows)
    annual_risk_free, periods_per_year = _performance_contract(contract)
    portfolio_nav = _normalized_nav(aggregate["net_return"])
    benchmark_nav = _normalized_nav(aggregate["benchmark_return"])
    gross_nav = _normalized_nav(aggregate["gross_return"])
    relative_wealth = cumulative_relative_wealth(portfolio_nav, benchmark_nav)
    annualized_excess = annualized_relative_wealth_excess(
        portfolio_nav,
        benchmark_nav,
        periods_per_year=periods_per_year,
    )
    active_std = float(aggregate["active_return"].std(ddof=1)) if len(aggregate) > 1 else 0.0
    portfolio_std = float(aggregate["net_return"].std(ddof=1)) if len(aggregate) > 1 else 0.0
    daily_risk_free = (1.0 + annual_risk_free) ** (1.0 / periods_per_year) - 1.0
    account_metrics: dict[str, dict[str, Any]] = {}
    for account_id, group in periods.groupby("account_id", sort=True):
        account_portfolio_nav = _normalized_nav(group["net_return"])
        account_benchmark_nav = _normalized_nav(group["benchmark_return"])
        account_relative_wealth = cumulative_relative_wealth(
            account_portfolio_nav,
            account_benchmark_nav,
        )
        account_metrics[str(account_id)] = {
            "net_return": float(account_portfolio_nav.iloc[-1] - 1.0),
            "benchmark_return": float(account_benchmark_nav.iloc[-1] - 1.0),
            "active_return": account_relative_wealth,
            "portfolio_cagr": _annualized_nav_return(
                account_portfolio_nav,
                periods_per_year=periods_per_year,
            ),
            "benchmark_cagr": _annualized_nav_return(
                account_benchmark_nav,
                periods_per_year=periods_per_year,
            ),
            "annualized_excess_wealth": annualized_relative_wealth_excess(
                account_portfolio_nav,
                account_benchmark_nav,
                periods_per_year=periods_per_year,
            ),
            "portfolio_nav": account_portfolio_nav.astype(float).tolist(),
            "benchmark_nav": account_benchmark_nav.astype(float).tolist(),
            "periods": int(len(group)),
            "turnover": float(group["turnover"].mean()),
            "cash_ratio": float(group["cash_ratio"].mean()),
            "capital_utilization": float(group["capital_utilization"].mean()),
            "beginning_capital_utilization": float(
                group["beginning_capital_utilization"].mean()
            ),
            "cash_position_effect_total": float(group["cash_position_effect"].sum()),
            "security_selection_return_total": float(
                group["security_selection_return"].sum()
            ),
            "execution_cost_effect_total": float(
                group["execution_cost_effect"].sum()
            ),
            "target_risky_exposure": float(group["target_risky_exposure"].mean()),
            "passive_cash_ratio": float(group["passive_cash_ratio"].mean()),
        }
    total_traded = float(periods["traded_gross"].sum())
    total_commission = float(periods["commission"].sum())
    total_stamp = float(periods["stamp_tax"].sum())
    total_slippage = float(periods["slippage"].sum())
    gross_net_error = (
        pd.to_numeric(periods["gross_return"], errors="coerce")
        - pd.to_numeric(periods["cost_return"], errors="coerce")
        - pd.to_numeric(periods["net_return"], errors="coerce")
    ).abs()
    active_error = (
        pd.to_numeric(periods["net_return"], errors="coerce")
        - pd.to_numeric(periods["benchmark_return"], errors="coerce")
        - pd.to_numeric(periods["active_return"], errors="coerce")
    ).abs()
    component_error = pd.to_numeric(
        periods["attribution_reconciliation_error"],
        errors="coerce",
    ).abs()
    attribution_max_error = float(max(
        gross_net_error.max(skipna=True),
        active_error.max(skipna=True),
        component_error.max(skipna=True),
    ))
    attribution_status = (
        "reconciled" if attribution_max_error <= 1e-10 else "mismatch"
    )
    if trades.empty:
        impact_p50 = impact_p90 = 0.0
        capped_notional_ratio = missing_notional_ratio = 0.0
        execution_evidence_status = "not_applicable"
    else:
        impact_values = pd.to_numeric(trades["impact_bps"], errors="coerce").dropna()
        impact_p50 = float(impact_values.quantile(0.50)) if not impact_values.empty else 0.0
        impact_p90 = float(impact_values.quantile(0.90)) if not impact_values.empty else 0.0
        trade_notional = pd.to_numeric(trades["gross_amount"], errors="coerce").fillna(0.0)
        notional_total = float(trade_notional.sum())
        capped_notional_ratio = (
            float(trade_notional.loc[trades["impact_capped"].fillna(False).astype(bool)].sum())
            / notional_total
            if notional_total > 0.0 else 0.0
        )
        missing_notional_ratio = (
            float(trade_notional.loc[trades["liquidity_status"].ne("available")].sum())
            / notional_total
            if notional_total > 0.0 else 0.0
        )
        execution_evidence_status = (
            "available"
            if missing_notional_ratio <= 0.05 and capped_notional_ratio <= 0.10
            else "unavailable"
        )
    metrics: dict[str, Any] = {
        "simulator_version": SIMULATOR_VERSION,
        "gross_return": _annualized_nav_return(
            gross_nav,
            periods_per_year=periods_per_year,
        ),
        "net_return": _annualized_nav_return(
            portfolio_nav,
            periods_per_year=periods_per_year,
        ),
        "benchmark_return": _annualized_nav_return(
            benchmark_nav,
            periods_per_year=periods_per_year,
        ),
        "net_excess_return": annualized_excess,
        "portfolio_nav": portfolio_nav.astype(float).tolist(),
        "benchmark_nav": benchmark_nav.astype(float).tolist(),
        "portfolio_cagr": _annualized_nav_return(
            portfolio_nav,
            periods_per_year=periods_per_year,
        ),
        "benchmark_cagr": _annualized_nav_return(
            benchmark_nav,
            periods_per_year=periods_per_year,
        ),
        "cumulative_relative_wealth": relative_wealth,
        "annualized_excess_wealth": annualized_excess,
        "max_drawdown": _drawdown(aggregate["net_return"]),
        "active_max_drawdown": relative_wealth_max_drawdown(
            portfolio_nav,
            benchmark_nav,
        ),
        "annual_turnover": float(aggregate["turnover"].mean() * periods_per_year),
        "cash_ratio": float(aggregate["cash_ratio"].mean()),
        "capital_utilization": float(aggregate["capital_utilization"].mean()),
        "beginning_capital_utilization": float(
            aggregate["beginning_capital_utilization"].mean()
        ),
        "cash_position_effect_total": float(
            aggregate["cash_position_effect"].sum()
        ),
        "security_selection_return_total": float(
            aggregate["security_selection_return"].sum()
        ),
        "execution_cost_effect_total": float(
            aggregate["execution_cost_effect"].sum()
        ),
        "active_attribution_total": float(aggregate["active_return"].sum()),
        "target_risky_exposure": float(aggregate["target_risky_exposure"].mean()),
        "passive_cash_ratio": float(aggregate["passive_cash_ratio"].mean()),
        "portfolio_sharpe": (
            float(
                (aggregate["net_return"].mean() - daily_risk_free)
                / portfolio_std
                * np.sqrt(periods_per_year)
            )
            if portfolio_std > 1e-12 else 0.0
        ),
        "information_ratio": (
            float(aggregate["active_return"].mean() / active_std * np.sqrt(periods_per_year))
            if active_std > 1e-12 else 0.0
        ),
        "portfolio_period_returns": aggregate["active_return"].astype(float).tolist(),
        "portfolio_daily_returns": aggregate["net_return"].astype(float).tolist(),
        "benchmark_period_returns": aggregate["benchmark_return"].astype(float).tolist(),
        "portfolio_period_return_dates": aggregate["signal_date"].astype(str).tolist(),
        "portfolio_rebalance_periods": int(len(aggregate)),
        "portfolio_horizon": 1,
        "trade_count": int(len(trades)),
        "total_traded_gross": total_traded,
        "total_commission": total_commission,
        "total_stamp_tax": total_stamp,
        "total_slippage": total_slippage,
        "total_execution_cost": total_commission + total_stamp + total_slippage,
        "attribution_status": attribution_status,
        "attribution_max_error": attribution_max_error,
        "execution_cost_bps": (
            (total_commission + total_stamp + total_slippage) / total_traded * 10_000.0
            if total_traded > 0 else 0.0
        ),
        "impact_bps_p50": impact_p50,
        "impact_bps_p90": impact_p90,
        "impact_capped_notional_ratio": capped_notional_ratio,
        "missing_liquidity_notional_ratio": missing_notional_ratio,
        "execution_evidence_status": execution_evidence_status,
        "account_metrics": account_metrics,
        "all_accounts_profitable": bool(
            account_metrics and all(item["net_return"] > 0.0 for item in account_metrics.values())
        ),
        "all_accounts_positive_active": bool(
            account_metrics and all(item["active_return"] > 0.0 for item in account_metrics.values())
        ),
        "execution_policy_version": (
            str(execution_policy.get("version") or "cost-aware-aim-v1")
            if execution_policy
            else str(rule_execution_policy.get("version") or "mechanical-rule-v1")
            if rule_execution_policy
            else str(allocation_policy.get("version") or "benchmark-aware-topn-v1")
            if allocation_policy
            else "legacy-full-rank-v1"
        ),
        "rebalance_frequency": rebalance_frequency,
        "scheduled_rebalance_periods": int(
            periods.loc[periods["scheduled_rebalance"].astype(bool), "signal_date"].nunique()
        ),
        "replay_contract": "legacy",
        "decision_count": int(len(decisions)),
        "trade_allowed_count": (
            int(decisions["trade_allowed"].fillna(False).astype(bool).sum())
            if not decisions.empty else 0
        ),
        "no_trade_count": (
            int((~decisions["trade_allowed"].fillna(False).astype(bool)).sum())
            if not decisions.empty else 0
        ),
        "no_trade_reason_counts": (
            {
                str(reason): int(count)
                for reason, count in decisions.loc[
                    ~decisions["trade_allowed"].fillna(False).astype(bool),
                    "no_trade_reason",
                ].fillna("unknown").replace("", "unknown").value_counts().items()
            }
            if not decisions.empty else {}
        ),
    }
    return PortfolioReplayResult(
        metrics=metrics,
        periods=periods,
        trades=trades,
        nav=nav,
        decisions=decisions,
    )


def replay_rule_portfolio(
    evaluation: pd.DataFrame,
    *,
    contract: Mapping[str, Any],
) -> PortfolioReplayResult:
    """Replay ranked rules without requiring a calibrated return forecast."""

    rule_contract = dict(contract)
    rule_contract.pop("execution_policy", None)
    rule_frame = evaluation.drop(
        columns=["expected_excess_return", "prediction_uncertainty_bps"],
        errors="ignore",
    )
    result = replay_executable_portfolio(rule_frame, contract=rule_contract)
    result.metrics["replay_contract"] = "rule"
    return result


def replay_fixed_top_n_diagnostic_portfolio(
    evaluation: pd.DataFrame,
    *,
    contract: Mapping[str, Any],
) -> PortfolioReplayResult:
    """Replay raw ranks as an equal-weight Top-N diagnostic portfolio.

    The diagnostic deliberately ignores deployable allocation, edge, buffer,
    partial-adjustment, and turnover policies. It retains the shared date,
    quote, lot-size, settlement, and execution-cost mechanics.
    """

    diagnostic_contract = dict(contract)
    diagnostic_contract.pop("execution_policy", None)
    diagnostic_contract.pop("allocation_policy", None)
    diagnostic_contract["accounts"] = [
        {**dict(account), "hold_buffer_pct": 0.0}
        for account in contract.get("accounts") or []
    ]
    diagnostic_contract["rule_execution_policy"] = {
        "version": "fixed-topn-diagnostic-v1",
        "rank_buffer_pct": 0.0,
        "minimum_target_change": 0.0,
        "partial_adjustment_rate": 1.0,
        "max_daily_turnover": 1.0,
        "max_industry_weight": 1.0,
        "max_holding_days": 0,
    }
    diagnostic_frame = evaluation.drop(
        columns=["expected_excess_return", "prediction_uncertainty_bps"],
        errors="ignore",
    )
    result = replay_executable_portfolio(
        diagnostic_frame,
        contract=diagnostic_contract,
    )
    result.metrics["replay_contract"] = "diagnostic_fixed_topn"
    return result


def replay_model_portfolio(
    evaluation: pd.DataFrame,
    *,
    contract: Mapping[str, Any],
) -> PortfolioReplayResult:
    """Replay models only when finite economic predictions are available."""

    required = {"expected_excess_return", "prediction_uncertainty_bps"}
    if not required.issubset(evaluation.columns):
        raise ValueError("model_replay_missing_economic_prediction")
    predictions = evaluation.loc[:, sorted(required)].apply(
        pd.to_numeric,
        errors="coerce",
    )
    if (
        predictions.isna().any(axis=None)
        or not np.isfinite(predictions.to_numpy(dtype=float)).all()
        or bool(predictions["prediction_uncertainty_bps"].lt(0.0).any())
    ):
        raise ValueError("model_replay_missing_economic_prediction")
    result = replay_executable_portfolio(evaluation, contract=contract)
    result.metrics["replay_contract"] = "model"
    return result


__all__ = [
    "PortfolioReplayResult",
    "SIMULATOR_VERSION",
    "annualized_relative_wealth_excess",
    "cumulative_relative_wealth",
    "replay_executable_portfolio",
    "replay_fixed_top_n_diagnostic_portfolio",
    "replay_model_portfolio",
    "replay_rule_portfolio",
]
