"""Point-in-time feature visibility and multi-horizon forward labels."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from .schemas import SUPPORTED_HORIZONS


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def observable_snapshot(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """Return only rows whose source and observation timestamps were visible."""

    visible = frame.copy()
    cutoff = _date_key(as_of)
    for column in ("observed_at", "ann_date", "source_date", "available_from"):
        if column in visible.columns:
            keys = visible[column].astype("string").str.replace("-", "", regex=False).str[:8]
            visible = visible.loc[keys.notna() & (keys <= cutoff)]
    return visible.reset_index(drop=True)


def build_forward_labels(
    prices: pd.DataFrame,
    *,
    benchmark: pd.DataFrame | None = None,
    horizons: Iterable[int] = (3, 5, 10, 20),
    round_trip_cost: float = 0.0015,
    label_end: str | None = None,
) -> pd.DataFrame:
    """Build labels without exposing any price after ``label_end``."""

    requested_horizons = tuple(int(value) for value in horizons)
    if not set(requested_horizons).issubset(SUPPORTED_HORIZONS):
        raise ValueError("label_horizon")
    required = {"code", "trade_date", "close"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"label_missing_columns:{','.join(sorted(missing))}")

    available = prices.copy()
    available["code"] = available["code"].astype("string")
    available["trade_date"] = available["trade_date"].astype("string").map(_date_key)
    available["close"] = pd.to_numeric(available["close"], errors="coerce")
    if label_end is not None:
        available = available.loc[available["trade_date"] <= _date_key(label_end)]

    benchmark_returns: dict[int, dict[str, float]] = {}
    if benchmark is not None and not benchmark.empty:
        benchmark_frame = benchmark.copy()
        benchmark_frame["trade_date"] = benchmark_frame["trade_date"].astype("string").map(_date_key)
        benchmark_frame["close"] = pd.to_numeric(benchmark_frame["close"], errors="coerce")
        if label_end is not None:
            benchmark_frame = benchmark_frame.loc[benchmark_frame["trade_date"] <= _date_key(label_end)]
        benchmark_frame = benchmark_frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        for horizon in requested_horizons:
            returns = benchmark_frame["close"].shift(-horizon) / benchmark_frame["close"] - 1.0
            benchmark_returns[horizon] = {
                str(date): float(value)
                for date, value in zip(benchmark_frame["trade_date"], returns)
                if pd.notna(value)
            }

    parts: list[pd.DataFrame] = []
    for code, group in available.sort_values(["code", "trade_date"]).groupby("code", sort=False):
        group = group.reset_index(drop=True)
        daily_returns = group["close"].pct_change(fill_method=None)
        trailing_sigma = daily_returns.rolling(20, min_periods=5).std()
        close = group["close"]
        dates = group["trade_date"].astype(str)
        for horizon in requested_horizons:
            future_close = close.shift(-horizon)
            future_date = dates.shift(-horizon)
            absolute_return = future_close / close - 1.0
            if benchmark is not None and not benchmark.empty:
                benchmark_return = dates.map(benchmark_returns.get(horizon, {})).astype(float)
                excess_return = absolute_return - benchmark_return
            else:
                benchmark_return = pd.Series(np.nan, index=group.index, dtype=float)
                excess_return = absolute_return
            threshold = pd.Series(
                np.maximum(
                    float(round_trip_cost),
                    0.25 * trailing_sigma.fillna(0.0).to_numpy(dtype=float) * math.sqrt(horizon),
                ),
                index=group.index,
            )
            labels = np.select(
                [excess_return > threshold, excess_return < -threshold],
                ["up", "down"],
                default="flat",
            )
            shifted = close.shift(-1)
            future_max = shifted.iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1]
            future_min = shifted.iloc[::-1].rolling(horizon, min_periods=horizon).min().iloc[::-1]
            part = pd.DataFrame(
                {
                    "code": str(code),
                    "trade_date": dates,
                    "horizon": horizon,
                    "label_end_date": future_date,
                    "absolute_return": absolute_return,
                    "benchmark_return": benchmark_return,
                    "excess_return": excess_return,
                    "threshold": threshold,
                    "label": labels,
                    "max_favorable_excursion": future_max / close - 1.0,
                    "max_adverse_excursion": future_min / close - 1.0,
                }
            )
            parts.append(part.loc[future_close.notna()])
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["code", "trade_date", "horizon"],
        kind="stable",
    ).reset_index(drop=True)
