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

    benchmark_returns: dict[tuple[str, int], float] = {}
    if benchmark is not None and not benchmark.empty:
        benchmark_frame = benchmark.copy()
        benchmark_frame["trade_date"] = benchmark_frame["trade_date"].astype("string").map(_date_key)
        benchmark_frame["close"] = pd.to_numeric(benchmark_frame["close"], errors="coerce")
        if label_end is not None:
            benchmark_frame = benchmark_frame.loc[benchmark_frame["trade_date"] <= _date_key(label_end)]
        benchmark_frame = benchmark_frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        for horizon in requested_horizons:
            returns = benchmark_frame["close"].shift(-horizon) / benchmark_frame["close"] - 1.0
            benchmark_returns.update(
                {(date, horizon): float(value) for date, value in zip(benchmark_frame["trade_date"], returns) if pd.notna(value)}
            )

    rows: list[dict[str, object]] = []
    for code, group in available.sort_values(["code", "trade_date"]).groupby("code", sort=False):
        group = group.reset_index(drop=True)
        daily_returns = group["close"].pct_change(fill_method=None)
        trailing_sigma = daily_returns.rolling(20, min_periods=5).std()
        for index, current in group.iterrows():
            for horizon in requested_horizons:
                future_index = index + horizon
                if future_index >= len(group):
                    continue
                future = group.iloc[future_index]
                absolute_return = float(future["close"] / current["close"] - 1.0)
                benchmark_return = benchmark_returns.get((str(current["trade_date"]), horizon))
                excess_return = absolute_return - benchmark_return if benchmark_return is not None else absolute_return
                sigma = float(trailing_sigma.iloc[index]) if pd.notna(trailing_sigma.iloc[index]) else 0.0
                threshold = max(float(round_trip_cost), 0.25 * sigma * math.sqrt(horizon))
                label = "up" if excess_return > threshold else "down" if excess_return < -threshold else "flat"
                path = group.iloc[index + 1 : future_index + 1]["close"] / float(current["close"]) - 1.0
                rows.append(
                    {
                        "code": str(code),
                        "trade_date": str(current["trade_date"]),
                        "horizon": horizon,
                        "label_end_date": str(future["trade_date"]),
                        "absolute_return": absolute_return,
                        "benchmark_return": benchmark_return,
                        "excess_return": excess_return,
                        "threshold": threshold,
                        "label": label,
                        "max_favorable_excursion": float(path.max()) if not path.empty else np.nan,
                        "max_adverse_excursion": float(path.min()) if not path.empty else np.nan,
                    }
                )
    return pd.DataFrame(rows)
