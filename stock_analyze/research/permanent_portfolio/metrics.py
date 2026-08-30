"""Performance and risk metrics for permanent-portfolio evidence."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def _finite(value: Any) -> float | int | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _longest_drawdown(drawdown: pd.Series) -> int:
    longest = 0
    current = 0
    for value in drawdown.fillna(0.0):
        if float(value) < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def rolling_series(nav: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "total_value"}
    if nav.empty or not required.issubset(nav.columns):
        raise ValueError("permanent_portfolio_metrics_nav")
    frame = nav.loc[:, ["date", "total_value"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["total_value"] = pd.to_numeric(
        frame["total_value"],
        errors="raise",
    )
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    returns = frame["total_value"].pct_change(fill_method=None)
    running_max = frame["total_value"].cummax()
    frame["normalized_nav"] = frame["total_value"] / frame["total_value"].iloc[0]
    frame["daily_return"] = returns
    frame["drawdown"] = frame["total_value"] / running_max - 1.0
    frame["volatility_63d"] = (
        returns.rolling(63, min_periods=20).std(ddof=1) * math.sqrt(TRADING_DAYS)
    )
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    return frame


def calculate_metrics(
    nav: pd.DataFrame,
    *,
    total_turnover: float,
    total_cost: float,
    trade_count: int,
) -> dict[str, float | int | None]:
    required = {"date", "total_value", "cash_benchmark_value"}
    if nav.empty or not required.issubset(nav.columns):
        raise ValueError("permanent_portfolio_metrics_nav")
    frame = nav.loc[:, sorted(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["total_value"] = pd.to_numeric(
        frame["total_value"],
        errors="raise",
    )
    frame["cash_benchmark_value"] = pd.to_numeric(
        frame["cash_benchmark_value"],
        errors="raise",
    )
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    if (
        len(frame) < 2
        or (frame[["total_value", "cash_benchmark_value"]] <= 0).any().any()
    ):
        raise ValueError("permanent_portfolio_metrics_nav")

    daily = frame["total_value"].pct_change(fill_method=None).dropna()
    cash_daily = (
        frame["cash_benchmark_value"].pct_change(fill_method=None).dropna()
    )
    excess = daily.subtract(cash_daily, fill_value=np.nan).dropna()
    periods = max(1, len(frame) - 1)
    cumulative_return = (
        frame["total_value"].iloc[-1] / frame["total_value"].iloc[0] - 1.0
    )
    annualized_return = (
        (1.0 + cumulative_return) ** (TRADING_DAYS / periods) - 1.0
        if cumulative_return > -1.0
        else -1.0
    )
    annualized_volatility = (
        daily.std(ddof=1) * math.sqrt(TRADING_DAYS)
        if len(daily) > 1
        else None
    )
    excess_std = excess.std(ddof=1) if len(excess) > 1 else np.nan
    sharpe = (
        excess.mean() / excess_std * math.sqrt(TRADING_DAYS)
        if pd.notna(excess_std) and excess_std > 0
        else None
    )
    downside = excess.loc[excess < 0]
    downside_std = downside.std(ddof=1) if len(downside) > 1 else np.nan
    sortino = (
        excess.mean() / downside_std * math.sqrt(TRADING_DAYS)
        if pd.notna(downside_std) and downside_std > 0
        else None
    )
    drawdown = frame["total_value"] / frame["total_value"].cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = (
        annualized_return / abs(max_drawdown)
        if max_drawdown < 0
        else None
    )
    monthly = (
        frame.set_index("date")["total_value"]
        .resample(pd.offsets.MonthEnd())
        .last()
        .pct_change()
    )
    positive_month_ratio = (
        float((monthly.dropna() > 0).mean()) if monthly.notna().any() else None
    )
    average_value = float(frame["total_value"].mean())
    annualized_turnover = (
        float(total_turnover) / average_value * TRADING_DAYS / periods
        if average_value > 0
        else None
    )
    initial_value = float(frame["total_value"].iloc[0])
    return {
        "cumulative_return": _finite(cumulative_return),
        "annualized_return": _finite(annualized_return),
        "annualized_volatility": _finite(annualized_volatility),
        "sharpe_vs_cash": _finite(sharpe),
        "sortino_vs_cash": _finite(sortino),
        "max_drawdown": _finite(max_drawdown),
        "max_drawdown_duration": _longest_drawdown(drawdown),
        "calmar": _finite(calmar),
        "positive_month_ratio": _finite(positive_month_ratio),
        "annualized_turnover": _finite(annualized_turnover),
        "trade_count": int(trade_count),
        "total_cost": _finite(total_cost),
        "cost_bps": _finite(
            float(total_cost) / initial_value * 10000.0
            if initial_value > 0
            else None
        ),
    }
