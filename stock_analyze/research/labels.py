"""Point-in-time feature visibility and multi-horizon forward labels."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from .schemas import SUPPORTED_HORIZONS


LABEL_CONTRACT_VERSION = "next-open-v2"


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
    require_benchmark: bool = False,
    horizons: Iterable[int] = (3, 5, 10, 20),
    round_trip_cost: float = 0.0015,
    label_end: str | None = None,
) -> pd.DataFrame:
    """Build executable next-open labels without exposing prices after ``label_end``.

    A feature observed at the signal-day close enters at the next available
    security open and exits at the horizon close.  Benchmark returns use those
    exact entry and exit dates so suspensions cannot silently change the
    comparison window.
    """

    requested_horizons = tuple(int(value) for value in horizons)
    if not set(requested_horizons).issubset(SUPPORTED_HORIZONS):
        raise ValueError("label_horizon")
    required = {"code", "trade_date", "open", "close"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"label_missing_columns:{','.join(sorted(missing))}")
    if require_benchmark and (benchmark is None or benchmark.empty):
        raise ValueError("label_benchmark_missing")

    available = prices.copy()
    available["code"] = available["code"].astype("string")
    available["trade_date"] = available["trade_date"].astype("string").map(_date_key)
    for column in ("open", "high", "low", "close"):
        if column in available.columns:
            available[column] = pd.to_numeric(available[column], errors="coerce")
    if label_end is not None:
        available = available.loc[available["trade_date"] <= _date_key(label_end)]

    benchmark_open_by_date: dict[str, float] = {}
    benchmark_close_by_date: dict[str, float] = {}
    if benchmark is not None and not benchmark.empty:
        benchmark_missing = {"trade_date", "open", "close"}.difference(benchmark.columns)
        if benchmark_missing:
            raise ValueError(
                f"label_benchmark_missing_columns:{','.join(sorted(benchmark_missing))}"
            )
        benchmark_frame = benchmark.copy()
        benchmark_frame["trade_date"] = benchmark_frame["trade_date"].astype("string").map(_date_key)
        benchmark_frame["open"] = pd.to_numeric(benchmark_frame["open"], errors="coerce")
        benchmark_frame["close"] = pd.to_numeric(benchmark_frame["close"], errors="coerce")
        if label_end is not None:
            benchmark_frame = benchmark_frame.loc[benchmark_frame["trade_date"] <= _date_key(label_end)]
        benchmark_frame = (
            benchmark_frame.dropna(subset=["trade_date", "open", "close"])
            .sort_values("trade_date")
            .drop_duplicates("trade_date", keep="last")
        )
        benchmark_open_by_date = dict(zip(
            benchmark_frame["trade_date"].astype(str),
            benchmark_frame["open"].astype(float),
        ))
        benchmark_close_by_date = dict(zip(
            benchmark_frame["trade_date"].astype(str),
            benchmark_frame["close"].astype(float),
        ))

    parts: list[pd.DataFrame] = []
    for code, group in available.sort_values(["code", "trade_date"]).groupby("code", sort=False):
        group = group.reset_index(drop=True)
        daily_returns = group["close"].pct_change(fill_method=None)
        trailing_sigma = daily_returns.rolling(20, min_periods=5).std()
        open_price = group["open"]
        close = group["close"]
        high = group["high"] if "high" in group.columns else close
        low = group["low"] if "low" in group.columns else close
        volume = (
            pd.to_numeric(group["volume"], errors="coerce")
            if "volume" in group.columns
            else pd.Series(1.0, index=group.index, dtype=float)
        )
        dates = group["trade_date"].astype(str)
        for horizon in requested_horizons:
            entry_date = dates.shift(-1)
            entry_price = open_price.shift(-1)
            entry_high = high.shift(-1)
            entry_low = low.shift(-1)
            entry_close = close.shift(-1)
            entry_volume = volume.shift(-1)
            entry_return_from_prev_close = entry_price / close - 1.0
            one_price_session = (
                entry_high.notna()
                & entry_low.notna()
                & entry_high.eq(entry_low)
                & entry_high.eq(entry_price)
            )
            entry_limit_up = one_price_session & entry_return_from_prev_close.ge(0.095)
            entry_limit_down = one_price_session & entry_return_from_prev_close.le(-0.095)
            entry_tradable = entry_price.gt(0.0) & entry_volume.fillna(0.0).gt(0.0)
            future_close = close.shift(-horizon)
            future_date = dates.shift(-horizon)
            absolute_return = future_close / entry_price - 1.0
            if benchmark is not None and not benchmark.empty:
                benchmark_entry = entry_date.map(benchmark_open_by_date).astype(float)
                benchmark_exit = future_date.map(benchmark_close_by_date).astype(float)
                benchmark_return = benchmark_exit / benchmark_entry - 1.0
                excess_return = absolute_return - benchmark_return
            else:
                benchmark_entry = pd.Series(np.nan, index=group.index, dtype=float)
                benchmark_exit = pd.Series(np.nan, index=group.index, dtype=float)
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
            future_high = high.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1]
            future_low = low.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).min().iloc[::-1]
            favorable = np.maximum(future_high / entry_price - 1.0, 0.0)
            adverse = np.minimum(future_low / entry_price - 1.0, 0.0)
            part = pd.DataFrame(
                {
                    "code": str(code),
                    "trade_date": dates,
                    "horizon": horizon,
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "entry_high": entry_high,
                    "entry_low": entry_low,
                    "entry_close": entry_close,
                    "entry_volume": entry_volume,
                    "entry_return_from_prev_close": entry_return_from_prev_close,
                    "entry_one_price_limit_up": entry_limit_up,
                    "entry_one_price_limit_down": entry_limit_down,
                    "entry_buy_allowed": entry_tradable & ~entry_limit_up,
                    "entry_sell_allowed": entry_tradable & ~entry_limit_down,
                    "label_end_date": future_date,
                    "label_contract_version": LABEL_CONTRACT_VERSION,
                    "absolute_return": absolute_return,
                    "benchmark_entry_price": benchmark_entry,
                    "benchmark_exit_price": benchmark_exit,
                    "benchmark_return": benchmark_return,
                    "excess_return": excess_return,
                    "threshold": threshold,
                    "label": labels,
                    "max_favorable_excursion": favorable,
                    "max_adverse_excursion": adverse,
                }
            )
            eligible = (
                entry_date.notna()
                & future_date.notna()
                & entry_price.gt(0.0)
                & future_close.notna()
                & future_close.gt(0.0)
            )
            if benchmark is not None and not benchmark.empty:
                eligible &= benchmark_return.notna()
            parts.append(part.loc[eligible])
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["code", "trade_date", "horizon"],
        kind="stable",
    ).reset_index(drop=True)
