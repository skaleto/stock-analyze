"""Performance + benchmark + cost metrics derived from CSV ledgers.

Pure pandas; no plotting. Intended to be cheap to call on every dashboard
generation. All metrics return ``None`` when data is insufficient so the
dashboard can show ``-`` placeholders without raising.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_account_performance(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    risk_free_rate: float = 0.02,
    trading_days_per_year: int = 252,
) -> dict[str, dict[str, Any]]:
    """Compute per-account performance metrics keyed by ``account_id``."""

    accounts: dict[str, dict[str, Any]] = {}
    if nav.empty:
        return accounts

    nav = nav.copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce").dt.date.astype(str)
    nav["total_value"] = pd.to_numeric(nav["total_value"], errors="coerce")
    nav["benchmark_close"] = pd.to_numeric(nav.get("benchmark_close"), errors="coerce")

    for account_id, group in nav.groupby("account_id"):
        group = group.sort_values("date").reset_index(drop=True)
        if group.empty:
            continue
        initial = float(group.iloc[0]["total_value"]) if not pd.isna(group.iloc[0]["total_value"]) else None
        latest = float(group.iloc[-1]["total_value"]) if not pd.isna(group.iloc[-1]["total_value"]) else None
        daily_return = group["total_value"].pct_change()
        benchmark_return = group["benchmark_close"].pct_change()
        excess_return = daily_return - benchmark_return

        annualized_return = _annualized_return(daily_return, trading_days_per_year)
        annualized_volatility = _annualized_std(daily_return, trading_days_per_year)
        downside_volatility = _annualized_downside(daily_return, trading_days_per_year)
        sharpe = _safe_ratio(annualized_return, risk_free_rate, annualized_volatility)
        sortino = _safe_ratio(annualized_return, risk_free_rate, downside_volatility)

        cumulative_return = (latest / initial - 1.0) if initial and latest else None
        rolling_peak = group["total_value"].cummax()
        drawdowns = group["total_value"] / rolling_peak - 1.0
        max_dd_value = float(drawdowns.min()) if not drawdowns.empty else None
        max_dd_days = _max_drawdown_days(group, drawdowns)

        cumulative_excess = _cumulative_excess(daily_return.dropna(), benchmark_return.dropna())
        annualized_excess = _annualized_return(excess_return, trading_days_per_year)
        tracking_error = _annualized_std(excess_return, trading_days_per_year)
        information_ratio = _safe_ratio(annualized_excess, 0.0, tracking_error)

        account_trades = trades[trades["account_id"] == account_id] if not trades.empty else pd.DataFrame()
        cost_summary = _trade_costs(account_trades)
        turnover_summary = _turnover_summary(account_trades, group)
        round_trip_summary = _round_trip_summary(account_trades)

        accounts[str(account_id)] = {
            "start_date": str(group.iloc[0]["date"]),
            "latest_date": str(group.iloc[-1]["date"]),
            "initial_value": round(initial, 2) if initial else None,
            "latest_value": round(latest, 2) if latest else None,
            "cumulative_return": _round(cumulative_return),
            "annualized_return": _round(annualized_return),
            "annualized_volatility": _round(annualized_volatility),
            "sharpe_ratio": _round(sharpe),
            "sortino_ratio": _round(sortino),
            "max_drawdown": _round(max_dd_value),
            "max_drawdown_days": max_dd_days,
            "cumulative_excess_return": _round(cumulative_excess),
            "annualized_excess_return": _round(annualized_excess),
            "tracking_error": _round(tracking_error),
            "information_ratio": _round(information_ratio),
            "nav_points": int(len(group)),
            **cost_summary,
            **turnover_summary,
            **round_trip_summary,
        }
    return accounts


def _annualized_return(daily_return: pd.Series, trading_days_per_year: int) -> float | None:
    valid = daily_return.dropna()
    if valid.empty:
        return None
    mean = float(valid.mean())
    return mean * trading_days_per_year


def _annualized_std(daily_return: pd.Series, trading_days_per_year: int) -> float | None:
    valid = daily_return.dropna()
    if len(valid) < 2:
        return None
    return float(valid.std(ddof=1)) * (trading_days_per_year ** 0.5)


def _annualized_downside(daily_return: pd.Series, trading_days_per_year: int) -> float | None:
    valid = daily_return.dropna()
    if valid.empty:
        return None
    downside = valid[valid < 0]
    if downside.empty:
        return None
    return float(downside.std(ddof=0)) * (trading_days_per_year ** 0.5)


def _safe_ratio(numerator: float | None, baseline: float, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return (numerator - baseline) / denominator


def _cumulative_excess(account_returns: pd.Series, benchmark_returns: pd.Series) -> float | None:
    if account_returns.empty or benchmark_returns.empty:
        return None
    aligned = pd.concat([account_returns, benchmark_returns], axis=1).dropna()
    if aligned.empty:
        return None
    account_cum = (1 + aligned.iloc[:, 0]).prod() - 1
    bench_cum = (1 + aligned.iloc[:, 1]).prod() - 1
    return float(account_cum) - float(bench_cum)


def _max_drawdown_days(group: pd.DataFrame, drawdowns: pd.Series) -> int | None:
    if drawdowns.empty:
        return None
    trough_idx = drawdowns.idxmin()
    if pd.isna(trough_idx):
        return None
    trough_date = pd.to_datetime(group.loc[trough_idx, "date"]).date()
    peak_value = float(group.iloc[: trough_idx + 1]["total_value"].max())
    peak_window = group.iloc[: trough_idx + 1]
    peak_match = peak_window[peak_window["total_value"] >= peak_value]
    if peak_match.empty:
        return None
    peak_date = pd.to_datetime(peak_match.iloc[0]["date"]).date()
    return (trough_date - peak_date).days


def _trade_costs(trades: pd.DataFrame) -> dict[str, float | None]:
    if trades.empty:
        return {
            "total_commission": 0.0,
            "total_stamp_tax": 0.0,
            "total_slippage": 0.0,
            "total_traded_value": 0.0,
            "cost_bps": None,
        }
    commission = float(pd.to_numeric(trades.get("commission", 0), errors="coerce").fillna(0).sum())
    stamp_tax = float(pd.to_numeric(trades.get("stamp_tax", 0), errors="coerce").fillna(0).sum())
    slippage = float(pd.to_numeric(trades.get("slippage", 0), errors="coerce").fillna(0).sum())
    gross = float(pd.to_numeric(trades.get("gross_amount", 0), errors="coerce").fillna(0).abs().sum())
    cost_bps = ((commission + stamp_tax + slippage) / gross * 10000.0) if gross > 0 else None
    return {
        "total_commission": round(commission, 2),
        "total_stamp_tax": round(stamp_tax, 2),
        "total_slippage": round(slippage, 2),
        "total_traded_value": round(gross, 2),
        "cost_bps": _round(cost_bps),
    }


def _turnover_summary(trades: pd.DataFrame, nav_group: pd.DataFrame) -> dict[str, float | None]:
    if trades.empty or nav_group.empty:
        return {"weekly_turnover_avg": None, "weekly_turnover_count": 0}
    trades = trades.copy()
    trades["trade_date"] = pd.to_datetime(trades["trade_date"], errors="coerce")
    trades = trades.dropna(subset=["trade_date"])
    if trades.empty:
        return {"weekly_turnover_avg": None, "weekly_turnover_count": 0}
    trades["week"] = trades["trade_date"].dt.to_period("W").astype(str)
    weekly_gross = trades.groupby("week")["gross_amount"].apply(
        lambda series: pd.to_numeric(series, errors="coerce").fillna(0).abs().sum()
    )
    nav_lookup = nav_group.set_index(pd.to_datetime(nav_group["date"], errors="coerce"))
    turnovers: list[float] = []
    for week_label, gross in weekly_gross.items():
        start = pd.to_datetime(week_label.split("/")[0])
        anchor = nav_lookup.index[nav_lookup.index <= start]
        if anchor.empty:
            anchor = nav_lookup.index[:1]
        if anchor.empty:
            continue
        baseline = float(nav_lookup.loc[anchor[-1], "total_value"])
        if baseline <= 0:
            continue
        turnovers.append(gross / baseline)
    if not turnovers:
        return {"weekly_turnover_avg": None, "weekly_turnover_count": 0}
    return {
        "weekly_turnover_avg": _round(float(np.mean(turnovers))),
        "weekly_turnover_count": int(len(turnovers)),
    }


def _round_trip_summary(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "round_trip_count": 0,
            "round_trip_win_rate": None,
            "avg_holding_days": None,
            "avg_round_trip_pnl": None,
        }
    pairs: list[dict[str, float]] = []
    for code, group in trades.groupby("code"):
        ordered = group.sort_values("trade_date")
        queue: list[tuple[pd.Timestamp, float, float, float]] = []
        for _, row in ordered.iterrows():
            shares = float(pd.to_numeric(row.get("shares"), errors="coerce") or 0)
            if shares == 0:
                continue
            price = float(pd.to_numeric(row.get("price"), errors="coerce") or 0)
            commission = float(pd.to_numeric(row.get("commission"), errors="coerce") or 0)
            stamp_tax = float(pd.to_numeric(row.get("stamp_tax"), errors="coerce") or 0)
            trade_date = pd.to_datetime(row.get("trade_date"), errors="coerce")
            if pd.isna(trade_date):
                continue
            cost_per_share = (commission + stamp_tax) / shares if shares else 0.0
            if row.get("side") == "buy":
                queue.append((trade_date, shares, price + cost_per_share, 0.0))
            else:
                remaining = shares
                proceeds_per_share = price - cost_per_share
                while remaining > 0 and queue:
                    entry_date, qty, basis, _ = queue[0]
                    matched = min(qty, remaining)
                    pnl = (proceeds_per_share - basis) * matched
                    pairs.append(
                        {
                            "entry_date": entry_date,
                            "exit_date": trade_date,
                            "holding_days": float((trade_date - entry_date).days),
                            "pnl": pnl,
                        }
                    )
                    remaining -= matched
                    if matched >= qty:
                        queue.pop(0)
                    else:
                        queue[0] = (entry_date, qty - matched, basis, 0.0)
    if not pairs:
        return {
            "round_trip_count": 0,
            "round_trip_win_rate": None,
            "avg_holding_days": None,
            "avg_round_trip_pnl": None,
        }
    wins = sum(1 for pair in pairs if pair["pnl"] > 0)
    return {
        "round_trip_count": len(pairs),
        "round_trip_win_rate": _round(wins / len(pairs)),
        "avg_holding_days": _round(float(np.mean([pair["holding_days"] for pair in pairs]))),
        "avg_round_trip_pnl": _round(float(np.mean([pair["pnl"] for pair in pairs]))),
    }


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return None
    return round(float(value), digits)
