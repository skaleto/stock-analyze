"""Executable daily portfolio replay for classical-model evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..execution_costs import calculate_execution_fill
from .execution_policy import estimate_execution_cost
from .strategy_ensemble import apply_cost_aware_transition


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


def _drawdown(values: pd.Series) -> float:
    returns = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=-0.99)
    if returns.empty:
        return 1.0
    curve = np.cumprod(1.0 + returns.to_numpy(dtype=float))
    return abs(float(np.min(curve / np.maximum.accumulate(curve) - 1.0)))


def _lot_size(trading: Mapping[str, Any]) -> int:
    return max(int(trading.get("lot_size") or trading.get("lot_size_default") or 100), 1)


def _price_map(group: pd.DataFrame) -> dict[str, float]:
    prices = pd.to_numeric(group["entry_price"], errors="coerce")
    return {
        str(code).zfill(6): float(price)
        for code, price in zip(group["code"].astype(str), prices)
        if pd.notna(price) and float(price) > 0.0
    }


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


def _account_path(
    frame: pd.DataFrame,
    *,
    account: Mapping[str, Any],
    trading: Mapping[str, Any],
    execution_policy: Mapping[str, Any] | None,
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
    groups = {
        str(day): group.sort_values(["score", "code"], ascending=[False, True], kind="stable")
        for day, group in frame.groupby("trade_date", sort=True)
    }
    entry_dates = [str(groups[day]["entry_date"].dropna().astype(str).min()) for day in dates]
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
        prices = _price_map(group)
        next_prices = _price_map(next_group)
        nav_before = _state_value(state, prices)
        if nav_before <= 0.0:
            continue
        target_weight = min((1.0 - reserve) / max(len(selected_codes), 1), max_single_weight)
        targets: dict[str, int]
        decisions_by_code: dict[str, dict[str, Any]] = {}
        if execution_policy:
            frictionless_codes = [
                str(code).zfill(6)
                for code in group.head(top_n)["code"].astype(str)
            ]
            target_weight = min(
                (1.0 - reserve) / max(len(frictionless_codes), 1),
                max_single_weight,
            )
            aim_weights = {code: target_weight for code in frictionless_codes}
            current_weights = {
                str(code): (
                    int(position.get("shares") or 0)
                    * float(prices.get(code, position.get("last_price") or 0.0))
                    / nav_before
                )
                for code, position in state["positions"].items()
            }
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
            targets = {}
            for code, weight in transition.weights.items():
                price = prices.get(code)
                if price is None:
                    continue
                targets[code] = int((nav_before * weight / price) // lot) * lot
            for raw in transition.decisions.to_dict(orient="records"):
                decision = {
                    **raw,
                    "fold": fold,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "account_id": account_id,
                }
                decisions_by_code[str(raw["code"])] = decision
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
            targets = {}
            for code in selected_codes:
                price = prices.get(code)
                if price is None:
                    continue
                targets[code] = int((nav_before * target_weight / price) // lot) * lot
        for code in state["positions"]:
            targets.setdefault(code, 0)

        group_by_code = group.assign(_code=group["code"].astype(str).str.zfill(6)).set_index("_code")
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
        for code, position in state["positions"].items():
            position["last_price"] = next_prices.get(code, prices.get(code, position.get("last_price", 0.0)))
        nav_next = _state_value(state, next_prices)
        net_return = nav_next / nav_before - 1.0
        period_cost = commission + stamp_tax + slippage
        gross_return = (nav_next + period_cost) / nav_before - 1.0
        benchmark_now = float(pd.to_numeric(group["benchmark_entry_price"], errors="coerce").dropna().median())
        benchmark_next = float(pd.to_numeric(next_group["benchmark_entry_price"], errors="coerce").dropna().median())
        benchmark_return = benchmark_next / benchmark_now - 1.0 if benchmark_now > 0 else 0.0
        period_rows.append({
            "fold": fold,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "next_entry_date": next_entry_date,
            "account_id": account_id,
            "account_weight": initial_cash,
            "gross_return": gross_return,
            "net_return": net_return,
            "benchmark_return": benchmark_return,
            "active_return": net_return - benchmark_return,
            "turnover": traded_gross / nav_before,
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
            "cash": float(state["cash"]),
            "unsettled_cash": sum(float(item["amount"]) for item in state["settlement_queue"]),
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
                for column in ("gross_return", "net_return", "benchmark_return", "active_return", "turnover")
            },
        })
    aggregate = pd.DataFrame(aggregated_rows)
    active_std = float(aggregate["active_return"].std(ddof=1)) if len(aggregate) > 1 else 0.0
    account_metrics = {
        str(account_id): {
            "net_return": float(np.prod(1.0 + group["net_return"].clip(lower=-0.99)) - 1.0),
            "benchmark_return": float(np.prod(1.0 + group["benchmark_return"].clip(lower=-0.99)) - 1.0),
            "active_return": float(np.prod(1.0 + group["active_return"].clip(lower=-0.99)) - 1.0),
            "periods": int(len(group)),
            "turnover": float(group["turnover"].mean()),
        }
        for account_id, group in periods.groupby("account_id", sort=True)
    }
    total_traded = float(periods["traded_gross"].sum())
    total_commission = float(periods["commission"].sum())
    total_stamp = float(periods["stamp_tax"].sum())
    total_slippage = float(periods["slippage"].sum())
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
        "gross_return": _annualized_return(aggregate["gross_return"]),
        "net_return": _annualized_return(aggregate["net_return"]),
        "benchmark_return": _annualized_return(aggregate["benchmark_return"]),
        "net_excess_return": _annualized_return(aggregate["active_return"]),
        "max_drawdown": _drawdown(aggregate["net_return"]),
        "annual_turnover": float(aggregate["turnover"].mean() * 252.0),
        "portfolio_sharpe": (
            float(aggregate["active_return"].mean() / active_std * np.sqrt(252.0))
            if active_std > 1e-12 else 0.0
        ),
        "portfolio_period_returns": aggregate["active_return"].astype(float).tolist(),
        "portfolio_period_return_dates": aggregate["signal_date"].astype(str).tolist(),
        "portfolio_rebalance_periods": int(len(aggregate)),
        "portfolio_horizon": 1,
        "trade_count": int(len(trades)),
        "total_traded_gross": total_traded,
        "total_commission": total_commission,
        "total_stamp_tax": total_stamp,
        "total_slippage": total_slippage,
        "total_execution_cost": total_commission + total_stamp + total_slippage,
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
            if execution_policy else "legacy-full-rank-v1"
        ),
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


__all__ = ["PortfolioReplayResult", "SIMULATOR_VERSION", "replay_executable_portfolio"]
