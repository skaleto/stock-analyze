"""Point-in-time QDII top-N capacity research."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ...factor_pipeline import process_factors
from .research_panel import ResearchPanelResult
from .strategy import _apply_risk_gates


class CapacityStudyError(ValueError):
    """The requested capacity study cannot produce meaningful evidence."""


@dataclass(frozen=True)
class CapacityStudyResult:
    run_id: str
    metrics: pd.DataFrame
    selections: pd.DataFrame
    trades: pd.DataFrame
    nav: pd.DataFrame
    summary: dict[str, Any]


def _prepare_panel(raw: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    frame = raw.copy()
    required = {
        "trade_date",
        "code",
        "scope",
        "index_key",
        "open",
        "close",
        "adj_close",
        "amount_yuan",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CapacityStudyError(f"panel_columns_missing:{','.join(missing)}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "code", "close"])
    frame = frame.loc[frame["trade_date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    if frame.empty:
        raise CapacityStudyError("empty_study_panel")
    for column in (
        "open",
        "close",
        "adj_close",
        "amount_yuan",
        "discount_premium",
        "fund_size_yuan",
        "management_fee",
    ):
        if column not in frame.columns:
            frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values(["code", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("code", sort=False)
    frame["momentum_20"] = grouped["adj_close"].transform(
        lambda values: values / values.shift(20) - 1.0
    )
    frame["momentum_60"] = grouped["adj_close"].transform(
        lambda values: values / values.shift(60) - 1.0
    )
    frame["low_volatility_60"] = grouped["adj_close"].transform(
        lambda values: values.pct_change().rolling(60, min_periods=20).std(ddof=1)
        * math.sqrt(252)
    )
    frame["avg_amount_20"] = grouped["amount_yuan"].transform(
        lambda values: values.rolling(20, min_periods=5).mean()
    )
    listed = pd.to_datetime(frame.get("list_date"), errors="coerce")
    frame["listing_age_days"] = (frame["trade_date"] - listed).dt.days
    frame["paused"] = False
    frame["history_complete"] = frame["momentum_60"].notna()
    return frame.sort_values(["trade_date", "scope", "code"]).reset_index(drop=True)


def _signal_dates(scope_frame: pd.DataFrame, benchmark: str) -> list[pd.Timestamp]:
    benchmark_rows = scope_frame.loc[scope_frame["code"].astype(str).eq(str(benchmark))]
    if benchmark_rows.empty:
        raise CapacityStudyError(f"benchmark_unavailable:{benchmark}")
    dates = benchmark_rows["trade_date"].drop_duplicates().sort_values()
    weekly = pd.DataFrame({"trade_date": dates})
    weekly["week"] = weekly["trade_date"].dt.to_period("W-FRI")
    return weekly.groupby("week", sort=True)["trade_date"].max().tolist()


def _score_snapshot(
    frame: pd.DataFrame,
    signal_date: pd.Timestamp,
    overlay: dict[str, Any],
) -> tuple[pd.DataFrame, int]:
    visible = frame.loc[frame["trade_date"].le(signal_date)].copy()
    if visible.empty:
        return pd.DataFrame(), 0
    snapshot = visible.groupby("code", sort=False).tail(1).copy()
    age = (signal_date - snapshot["trade_date"]).dt.days
    snapshot["paused"] = age.gt(7)
    eligible, _rejected = _apply_risk_gates(snapshot, dict(overlay.get("filters") or {}))
    max_candidates = int((overlay.get("filters") or {}).get("max_fetch_candidates", 0) or 0)
    if max_candidates > 0 and len(eligible) > max_candidates:
        eligible = eligible.nlargest(max_candidates, "avg_amount_20")
    eligible_count = len(eligible)
    factors = {
        name: dict(spec) if isinstance(spec, dict) else {"weight": float(spec)}
        for name, spec in (overlay.get("factors") or {}).items()
        if name in eligible.columns
    }
    if eligible.empty or not factors:
        return pd.DataFrame(), eligible_count
    columns = ["code", *factors]
    scored, _table = process_factors(
        eligible[columns].copy(),
        factors=factors,
        factor_processing=dict(overlay.get("factor_processing") or {}),
    )
    if "insufficient_factor_coverage" in scored.columns:
        scored = scored.loc[~scored["insufficient_factor_coverage"].fillna(False)]
    metadata_columns = [
        column
        for column in (
            "code",
            "name",
            "scope",
            "index_key",
            "theme",
            "close",
            "avg_amount_20",
            "discount_premium",
            "fund_size_yuan",
        )
        if column in eligible.columns
    ]
    scored = scored.merge(eligible[metadata_columns], on="code", how="left")
    return scored.sort_values(["score", "code"], ascending=[False, True]), eligible_count


def _select_distinct_indexes(
    ranked: pd.DataFrame,
    top_n: int,
    max_per_index: int,
) -> pd.DataFrame:
    if ranked.empty:
        return ranked.copy()
    kept: list[int] = []
    deferred: list[int] = []
    counts: dict[str, int] = {}
    for idx, row in ranked.iterrows():
        key = str(row.get("index_key") or f"code:{row['code']}")
        if counts.get(key, 0) < max(max_per_index, 1):
            kept.append(idx)
            counts[key] = counts.get(key, 0) + 1
        else:
            deferred.append(idx)
    chosen = [*kept[:top_n]]
    if len(chosen) < top_n:
        chosen.extend(deferred[: top_n - len(chosen)])
    return ranked.loc[chosen].head(top_n).copy()


def _next_date(calendar: list[pd.Timestamp], current: pd.Timestamp) -> pd.Timestamp | None:
    for value in calendar:
        if value > current:
            return value
    return None


def _generate_orders(
    *,
    ranked: pd.DataFrame,
    selected: pd.DataFrame,
    signal_date: pd.Timestamp,
    execute_date: pd.Timestamp,
    cash: float,
    positions: dict[str, dict[str, Any]],
    pending: list[dict[str, Any]],
    close_prices: pd.Series,
    top_n: int,
    max_single_weight: float,
    lot_size: int,
    commission_rate: float,
    hold_buffer_pct: float,
    max_holding_days: int | None,
    cash_reserve_pct: float,
) -> list[dict[str, Any]]:
    account_value = cash + sum(
        int(position["shares"]) * float(close_prices.get(code, position.get("avg_cost", 0.0)))
        for code, position in positions.items()
    )
    investable = account_value * (1.0 - cash_reserve_pct)
    per_target = min(investable / max(top_n, 1), investable * max_single_weight)
    selected_codes = set(selected["code"].astype(str))
    retention_count = max(top_n, math.ceil(top_n * (1.0 + max(hold_buffer_pct, 0.0))))
    retention_codes = set(ranked.head(retention_count)["code"].astype(str))
    pending_codes = {str(order["code"]) for order in pending}
    output: list[dict[str, Any]] = []

    for code, position in positions.items():
        hold_since = pd.Timestamp(position.get("hold_since", signal_date))
        expired = (
            max_holding_days is not None
            and (signal_date - hold_since).days >= max_holding_days
        )
        if code not in retention_codes or (expired and code not in selected_codes):
            if code not in pending_codes and int(position["shares"]) > 0:
                output.append(
                    {
                        "code": code,
                        "side": "sell",
                        "shares": int(position["shares"]),
                        "execute_date": execute_date,
                        "signal_date": signal_date,
                    }
                )

    buying_power = max(cash * (1.0 - cash_reserve_pct), 0.0)
    for _, row in selected.iterrows():
        code = str(row["code"])
        price = float(close_prices.get(code, float("nan")))
        if not math.isfinite(price) or price <= 0 or code in pending_codes:
            continue
        current_shares = int(positions.get(code, {}).get("shares", 0))
        target_shares = max(int(per_target / (price * lot_size)), 0) * lot_size
        desired = max(target_shares - current_shares, 0)
        estimated = price * (1.0 + commission_rate)
        affordable = max(int(buying_power / (estimated * lot_size)), 0) * lot_size
        shares = min(desired, affordable)
        if shares > 0:
            output.append(
                {
                    "code": code,
                    "side": "buy",
                    "shares": shares,
                    "execute_date": execute_date,
                    "signal_date": signal_date,
                    "score": float(row["score"]),
                }
            )
            buying_power -= shares * estimated
    return output


def _simulate(
    *,
    panel: pd.DataFrame,
    ranked_by_signal: dict[pd.Timestamp, pd.DataFrame],
    selected_by_signal: dict[pd.Timestamp, pd.DataFrame],
    baseline_account: dict[str, Any],
    trading: dict[str, Any],
    overlay: dict[str, Any],
    strategy: str,
    scope: str,
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    benchmark = str(baseline_account["benchmark"])
    calendar = sorted(
        panel.loc[panel["code"].astype(str).eq(benchmark), "trade_date"].unique()
    )
    calendar = [pd.Timestamp(value) for value in calendar]
    close_table = panel.pivot(index="trade_date", columns="code", values="close").sort_index().ffill()
    open_table = panel.pivot(index="trade_date", columns="code", values="open").sort_index()
    cash = float(baseline_account.get("cash", 0.0))
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    settlement: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    commission_rate = float(trading.get("commission_rate", 0.0003))
    slippage_rate = float(trading.get("slippage_bps", 5.0)) / 10_000.0
    lot_size = int(trading.get("lot_size_default", 100))
    max_single_weight = float(trading.get("max_single_weight", 0.20))
    controls = dict(overlay.get("portfolio_controls") or {})

    for trade_date in calendar:
        matured = [item for item in settlement if item["date"] <= trade_date]
        cash += sum(float(item["amount"]) for item in matured)
        settlement = [item for item in settlement if item["date"] > trade_date]

        remaining: list[dict[str, Any]] = []
        due = sorted(
            [order for order in pending if order["execute_date"] <= trade_date],
            key=lambda order: 0 if order["side"] == "sell" else 1,
        )
        remaining.extend(order for order in pending if order["execute_date"] > trade_date)
        for order in due:
            code = str(order["code"])
            if trade_date not in open_table.index or code not in open_table.columns:
                remaining.append(order)
                continue
            raw_open = float(open_table.at[trade_date, code])
            if not math.isfinite(raw_open) or raw_open <= 0:
                remaining.append(order)
                continue
            side = str(order["side"])
            price = raw_open * (1.0 + slippage_rate if side == "buy" else 1.0 - slippage_rate)
            shares = int(order["shares"])
            gross = shares * price
            commission = gross * commission_rate
            slippage = abs(price - raw_open) * shares
            if side == "sell":
                held = int(positions.get(code, {}).get("shares", 0))
                if held < shares:
                    continue
                new_shares = held - shares
                if new_shares:
                    positions[code]["shares"] = new_shares
                else:
                    positions.pop(code, None)
                settle_date = _next_date(calendar, trade_date)
                if settle_date is not None:
                    settlement.append({"date": settle_date, "amount": gross - commission})
            else:
                debit = gross + commission
                if debit > cash:
                    remaining.append(order)
                    continue
                cash -= debit
                current = positions.get(code, {"shares": 0, "avg_cost": 0.0})
                prior_shares = int(current["shares"])
                total_shares = prior_shares + shares
                positions[code] = {
                    "shares": total_shares,
                    "avg_cost": (
                        prior_shares * float(current["avg_cost"]) + gross
                    )
                    / total_shares,
                    "hold_since": current.get("hold_since", trade_date),
                }
            trade_rows.append(
                {
                    "strategy": strategy,
                    "scope": scope,
                    "top_n": top_n,
                    "signal_date": order["signal_date"].strftime("%Y-%m-%d"),
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "code": code,
                    "side": side,
                    "shares": shares,
                    "price": price,
                    "gross_amount": gross,
                    "commission": commission,
                    "stamp_tax": 0.0,
                    "slippage": slippage,
                }
            )
        pending = remaining

        closes = close_table.loc[trade_date] if trade_date in close_table.index else pd.Series(dtype=float)
        market_value = sum(
            int(position["shares"]) * float(closes.get(code, position["avg_cost"]))
            for code, position in positions.items()
        )
        settlement_receivable = sum(float(item["amount"]) for item in settlement)
        nav_rows.append(
            {
                "strategy": strategy,
                "scope": scope,
                "top_n": top_n,
                "date": trade_date.strftime("%Y-%m-%d"),
                "cash": cash,
                "market_value": market_value,
                "settlement_receivable": settlement_receivable,
                "total_value": cash + market_value + settlement_receivable,
                "benchmark_code": benchmark,
                "benchmark_close": float(closes.get(benchmark, float("nan"))),
            }
        )

        if trade_date in selected_by_signal:
            execute_date = _next_date(calendar, trade_date)
            if execute_date is not None:
                pending.extend(
                    _generate_orders(
                        ranked=ranked_by_signal[trade_date],
                        selected=selected_by_signal[trade_date],
                        signal_date=trade_date,
                        execute_date=execute_date,
                        cash=cash,
                        positions=positions,
                        pending=pending,
                        close_prices=closes,
                        top_n=top_n,
                        max_single_weight=max_single_weight,
                        lot_size=lot_size,
                        commission_rate=commission_rate,
                        hold_buffer_pct=float(controls.get("hold_buffer_pct", 0.0)),
                        max_holding_days=(
                            int(controls["max_holding_days"])
                            if controls.get("max_holding_days") is not None
                            else None
                        ),
                        cash_reserve_pct=0.02,
                    )
                )
    return pd.DataFrame(nav_rows), pd.DataFrame(trade_rows)


def _correlation_clusters(panel: pd.DataFrame, codes: list[str]) -> int:
    selected = panel.loc[panel["code"].isin(codes), ["trade_date", "code", "adj_close"]]
    prices = selected.pivot(index="trade_date", columns="code", values="adj_close").sort_index()
    corr = prices.pct_change().corr(min_periods=20)
    if corr.empty:
        return len(codes)
    parent = {code: code for code in corr.columns}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for i, left in enumerate(corr.columns):
        for right in corr.columns[i + 1 :]:
            value = corr.at[left, right]
            if pd.notna(value) and float(value) >= 0.80:
                union(str(left), str(right))
    return len({find(str(code)) for code in corr.columns})


def _metrics(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    selections: pd.DataFrame,
    panel: pd.DataFrame,
) -> dict[str, Any]:
    values = nav.sort_values("date").copy()
    returns = values["total_value"].pct_change().dropna()
    benchmark_returns = values["benchmark_close"].pct_change().dropna()
    aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
    cumulative = float(values.iloc[-1]["total_value"] / values.iloc[0]["total_value"] - 1.0)
    benchmark_cumulative = float(
        values.iloc[-1]["benchmark_close"] / values.iloc[0]["benchmark_close"] - 1.0
    )
    annualized = float(returns.mean() * 252) if not returns.empty else None
    volatility = float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) >= 2 else None
    sharpe = (
        (annualized - 0.02) / volatility
        if annualized is not None and volatility not in (None, 0)
        else None
    )
    drawdown = values["total_value"] / values["total_value"].cummax() - 1.0
    excess = returns - benchmark_returns
    tracking_error = float(excess.dropna().std(ddof=1) * math.sqrt(252)) if len(excess.dropna()) >= 2 else None
    annualized_excess = float(excess.dropna().mean() * 252) if not excess.dropna().empty else None
    information_ratio = (
        annualized_excess / tracking_error
        if annualized_excess is not None and tracking_error not in (None, 0)
        else None
    )
    gross = float(trades["gross_amount"].sum()) if not trades.empty else 0.0
    costs = (
        float(trades[["commission", "stamp_tax", "slippage"]].sum().sum())
        if not trades.empty
        else 0.0
    )
    weekly_turnover: list[float] = []
    if not trades.empty:
        work = trades.copy()
        work["week"] = pd.to_datetime(work["trade_date"]).dt.to_period("W")
        nav_lookup = values.assign(date=pd.to_datetime(values["date"])).set_index("date")
        for week, group in work.groupby("week"):
            anchors = nav_lookup.loc[nav_lookup.index <= week.start_time, "total_value"]
            if anchors.empty:
                anchors = nav_lookup["total_value"].head(1)
            if not anchors.empty and float(anchors.iloc[-1]) > 0:
                weekly_turnover.append(float(group["gross_amount"].sum()) / float(anchors.iloc[-1]))
    selected_codes = sorted(selections["code"].astype(str).unique()) if not selections.empty else []
    concentration = []
    if not selections.empty:
        for _, group in selections.groupby("signal_date"):
            weights = group.groupby("index_key")["target_weight"].sum()
            concentration.append(float(weights.max()))
    eligible_counts = (
        selections.groupby("signal_date")["eligible_count"].max()
        if not selections.empty
        else pd.Series(dtype=float)
    )
    return {
        "start_date": str(values.iloc[0]["date"]),
        "end_date": str(values.iloc[-1]["date"]),
        "nav_points": int(len(values)),
        "signal_weeks": int(selections["signal_date"].nunique()) if not selections.empty else 0,
        "cumulative_return": cumulative,
        "annualized_return": annualized,
        "annualized_volatility": volatility,
        "sharpe_ratio": sharpe,
        "max_drawdown": float(drawdown.min()),
        "benchmark_cumulative_return": benchmark_cumulative,
        "cumulative_excess_return": cumulative - benchmark_cumulative,
        "information_ratio": information_ratio,
        "weekly_turnover_avg": float(np.mean(weekly_turnover)) if weekly_turnover else None,
        "cost_bps": costs / gross * 10_000.0 if gross > 0 else None,
        "average_eligible_count": float(eligible_counts.mean()) if not eligible_counts.empty else 0.0,
        "average_selected_count": (
            float(selections.groupby("signal_date")["code"].count().mean())
            if not selections.empty
            else 0.0
        ),
        "average_max_index_weight": float(np.mean(concentration)) if concentration else None,
        "effective_correlation_clusters": _correlation_clusters(panel, selected_codes),
    }


def _run_id(
    panel: ResearchPanelResult,
    overlays: dict[str, dict[str, Any]],
    top_ns: list[int],
    start: str,
    end: str,
) -> str:
    payload = {
        "universe_hash": panel.metadata.get("universe_hash"),
        "strategies": {
            key: value.get("strategy_id") for key, value in sorted(overlays.items())
        },
        "top_ns": top_ns,
        "start": start,
        "end": end,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    return f"{end}-{digest}"


def run_capacity_study(
    panel: ResearchPanelResult,
    *,
    overlays: dict[str, dict[str, Any]],
    baseline: dict[str, Any],
    top_ns: list[int],
    start: str,
    end: str,
    min_signal_weeks: int = 20,
) -> CapacityStudyResult:
    """Run both QDII strategies through deterministic top-N sensitivities."""

    if pd.Timestamp(start) > pd.Timestamp(end):
        raise CapacityStudyError("invalid_date_range")
    normalized_top_ns = sorted({int(value) for value in top_ns if int(value) > 0})
    if not normalized_top_ns:
        raise CapacityStudyError("empty_top_n")
    prepared = _prepare_panel(panel.frame, start, end)
    account_by_scope = {
        str(account["scope"]): dict(account)
        for account in baseline.get("accounts", [])
    }
    metric_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    trade_frames: list[pd.DataFrame] = []
    nav_frames: list[pd.DataFrame] = []

    for strategy, overlay in sorted(overlays.items()):
        for scope, account in sorted(account_by_scope.items()):
            scope_frame = prepared.loc[prepared["scope"].astype(str).eq(scope)].copy()
            if scope_frame.empty:
                raise CapacityStudyError(f"scope_unavailable:{scope}")
            signals = _signal_dates(scope_frame, str(account["benchmark"]))
            ranked_by_signal: dict[pd.Timestamp, pd.DataFrame] = {}
            eligible_by_signal: dict[pd.Timestamp, int] = {}
            for signal_date in signals:
                ranked, eligible_count = _score_snapshot(scope_frame, signal_date, overlay)
                if not ranked.empty:
                    ranked_by_signal[signal_date] = ranked
                    eligible_by_signal[signal_date] = eligible_count
            if len(ranked_by_signal) < min_signal_weeks:
                raise CapacityStudyError(
                    f"insufficient_signal_weeks:{strategy}:{scope}:{len(ranked_by_signal)}"
                )

            for top_n in normalized_top_ns:
                selected_by_signal: dict[pd.Timestamp, pd.DataFrame] = {}
                combo_selections: list[dict[str, Any]] = []
                controls = dict(overlay.get("portfolio_controls") or {})
                max_per_index = int(controls.get("max_etfs_per_index", 1))
                target_weight = min(0.98 / top_n, float((baseline.get("trading") or {}).get("max_single_weight", 0.20)))
                for signal_date, ranked in ranked_by_signal.items():
                    selected = _select_distinct_indexes(ranked, top_n, max_per_index)
                    if selected.empty:
                        continue
                    selected_by_signal[signal_date] = selected
                    for rank, (_, row) in enumerate(selected.iterrows(), start=1):
                        combo_selections.append(
                            {
                                "strategy": strategy,
                                "strategy_id": overlay.get("strategy_id"),
                                "scope": scope,
                                "top_n": top_n,
                                "signal_date": signal_date.strftime("%Y-%m-%d"),
                                "rank": rank,
                                "code": str(row["code"]),
                                "name": row.get("name"),
                                "index_key": row.get("index_key"),
                                "theme": row.get("theme"),
                                "score": float(row["score"]),
                                "target_weight": target_weight,
                                "eligible_count": eligible_by_signal[signal_date],
                            }
                        )
                combo_selection_df = pd.DataFrame(combo_selections)
                nav, trades = _simulate(
                    panel=scope_frame,
                    ranked_by_signal=ranked_by_signal,
                    selected_by_signal=selected_by_signal,
                    baseline_account=account,
                    trading=dict(baseline.get("trading") or {}),
                    overlay=overlay,
                    strategy=strategy,
                    scope=scope,
                    top_n=top_n,
                )
                metric_rows.append(
                    {
                        "strategy": strategy,
                        "strategy_id": overlay.get("strategy_id"),
                        "scope": scope,
                        "top_n": top_n,
                        **_metrics(nav, trades, combo_selection_df, scope_frame),
                    }
                )
                selection_rows.extend(combo_selections)
                trade_frames.append(trades)
                nav_frames.append(nav)

    metrics = pd.DataFrame(metric_rows).sort_values(["strategy", "scope", "top_n"]).reset_index(drop=True)
    selections = pd.DataFrame(selection_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    nav = pd.concat(nav_frames, ignore_index=True) if nav_frames else pd.DataFrame()
    run_id = _run_id(panel, overlays, normalized_top_ns, start, end)
    recommendations: list[dict[str, Any]] = []
    for (strategy, scope), group in metrics.groupby(["strategy", "scope"]):
        eligible = group.loc[
            group["average_eligible_count"].ge(group["top_n"] + 1)
            & group["max_drawdown"].ge(-0.25)
        ].copy()
        if eligible.empty:
            recommendations.append(
                {"strategy": strategy, "scope": scope, "recommended_top_n": None, "reason": "no_candidate_passed_capacity_gates"}
            )
            continue
        eligible["_sharpe"] = pd.to_numeric(eligible["sharpe_ratio"], errors="coerce").fillna(-999.0)
        winner = eligible.sort_values(
            ["_sharpe", "effective_correlation_clusters", "weekly_turnover_avg", "top_n"],
            ascending=[False, False, True, True],
        ).iloc[0]
        recommendations.append(
            {
                "strategy": strategy,
                "scope": scope,
                "recommended_top_n": int(winner["top_n"]),
                "reason": "best_risk_adjusted_candidate_with_capacity_buffer",
            }
        )
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "start": start,
        "end": end,
        "top_ns": normalized_top_ns,
        "universe_hash": panel.metadata.get("universe_hash"),
        "source_contract": panel.metadata.get("source_contract"),
        "limitations": {
            "survivorship_bias": bool(panel.metadata.get("survivorship_bias", True)),
            "catalog_membership": "current_catalog_only",
            "automatic_baseline_change": False,
        },
        "recommendations": recommendations,
        "metrics": metrics.to_dict(orient="records"),
    }
    return CapacityStudyResult(run_id, metrics, selections, trades, nav, summary)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    return value


def _render_report(result: CapacityStudyResult) -> str:
    lines = [
        f"# 跨境ETF容量研究 · {result.summary['end']}",
        "",
        "> 研究类型：当前目录历史回放。存在幸存者偏差，结果不自动修改竞赛基线或活动策略。",
        "",
        f"`run_id={result.run_id}` · `universe_hash={result.summary.get('universe_hash')}`",
        "",
        "## top_n 敏感度",
        "",
        "| 策略 | 范围 | top_n | 累计 | 超额 | Sharpe | 最大回撤 | 周换手 | 成本bps | 有效簇 | 最大指数权重 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result.metrics.to_dict(orient="records"):
        def pct(key: str) -> str:
            value = row.get(key)
            return "-" if value is None or pd.isna(value) else f"{float(value):+.2%}"

        def num(key: str, digits: int = 2) -> str:
            value = row.get(key)
            return "-" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"

        lines.append(
            f"| {row['strategy']} | {row['scope']} | {int(row['top_n'])} | "
            f"{pct('cumulative_return')} | {pct('cumulative_excess_return')} | "
            f"{num('sharpe_ratio')} | {pct('max_drawdown')} | "
            f"{pct('weekly_turnover_avg')} | {num('cost_bps', 1)} | "
            f"{int(row['effective_correlation_clusters'])} | {pct('average_max_index_weight')} |"
        )
    lines += ["", "## 研究建议", ""]
    for item in result.summary["recommendations"]:
        value = item.get("recommended_top_n")
        recommendation = f"top_n={value}" if value is not None else "暂无候选"
        lines.append(f"- {item['strategy']} / {item['scope']}: {recommendation}；{item['reason']}。")
    lines += [
        "",
        "## 限制与晋级边界",
        "",
        "- 当前只有现存目录快照，无法还原已退市或历史上曾符合条件的基金，存在幸存者偏差。",
        "- 缺失 NAV/份额时不填零，溢价和规模覆盖率应与机器摘要一起审阅。",
        "- 本报告只提供容量证据，不自动修改 `top_n`、账户资金、持仓或策略配置。",
        "- 晋级仍需历史目录、公告事件链路和至少四周影子运行。",
        "",
    ]
    return "\n".join(lines)


def write_capacity_artifacts(
    result: CapacityStudyResult,
    repo_root: str | Path,
    *,
    end_date: str,
) -> dict[str, Path]:
    root = Path(repo_root)
    data_dir = root / "data" / "cn_qdii_etf" / "research" / "capacity" / result.run_id
    report_dir = root / "reports" / "competition" / "research"
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": data_dir / "summary.json",
        "metrics": data_dir / "metrics.csv",
        "selections": data_dir / "selections.csv",
        "trades": data_dir / "trades.csv",
        "nav": data_dir / "nav.csv",
        "report": report_dir / f"qdii_capacity_{end_date}.md",
    }
    paths["summary"].write_text(
        json.dumps(_json_safe(result.summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result.metrics.to_csv(paths["metrics"], index=False)
    result.selections.to_csv(paths["selections"], index=False)
    result.trades.to_csv(paths["trades"], index=False)
    result.nav.to_csv(paths["nav"], index=False)
    paths["report"].write_text(_render_report(result), encoding="utf-8")
    return paths


__all__ = [
    "CapacityStudyError",
    "CapacityStudyResult",
    "run_capacity_study",
    "write_capacity_artifacts",
]
