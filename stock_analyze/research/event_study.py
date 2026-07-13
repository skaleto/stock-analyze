"""Conditional event statistics with deterministic bootstrap uncertainty."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _bootstrap_mean_interval(values: np.ndarray, samples: int, rng: np.random.Generator) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return (math.nan, math.nan)
    if len(finite) == 1 or samples <= 0:
        return (float(finite[0]), float(finite[0]))
    indices = rng.integers(0, len(finite), size=(samples, len(finite)))
    means = finite[indices].mean(axis=1)
    return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))


def build_event_study(
    events: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    min_support: int = 30,
    round_trip_cost: float = 0.0015,
    bootstrap_samples: int = 1000,
    seed: int = 20260713,
) -> pd.DataFrame:
    required_event = {"event", "market", "code", "trade_date"}
    required_label = {"code", "trade_date", "horizon", "label", "excess_return"}
    if required_event.difference(events.columns) or required_label.difference(labels.columns):
        raise ValueError("event_study_missing_columns")
    if events.empty or labels.empty:
        return pd.DataFrame()

    event_frame = events.copy()
    label_frame = labels.copy()
    for frame in (event_frame, label_frame):
        frame["code"] = frame["code"].astype("string")
        frame["trade_date"] = frame["trade_date"].astype("string")
    event_frame["regime"] = event_frame.get("regime", "unknown").fillna("unknown")
    event_frame["industry"] = event_frame.get("industry", "unclassified").fillna("unclassified")
    joined = event_frame.merge(label_frame, on=["code", "trade_date"], how="inner", suffixes=("", "_label"))
    if joined.empty:
        return pd.DataFrame()

    baseline = label_frame.groupby("horizon").agg(
        baseline_up_rate=("label", lambda values: float((values == "up").mean())),
        baseline_mean_excess=("excess_return", "mean"),
    )
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    group_columns = ["market", "event", "horizon", "regime", "industry"]
    for keys, group in joined.groupby(group_columns, dropna=False, sort=True):
        values = pd.to_numeric(group["excess_return"], errors="coerce").to_numpy(dtype=float)
        finite = values[np.isfinite(values)]
        low, high = _bootstrap_mean_interval(finite, bootstrap_samples, rng)
        observations = len(finite)
        horizon = int(keys[2])
        baseline_row = baseline.loc[horizon] if horizon in baseline.index else None
        up_rate = float((group["label"] == "up").mean())
        down_rate = float((group["label"] == "down").mean())
        mean_excess = float(np.mean(finite)) if observations else math.nan
        interval_width = high - low if np.isfinite(low) and np.isfinite(high) else math.inf
        stability = min(1.0, observations / max(min_support, 1)) * max(0.0, 1.0 - min(interval_width / 0.1, 1.0))
        rows.append(
            {
                "market": keys[0],
                "event": keys[1],
                "horizon": horizon,
                "regime": keys[3],
                "industry": keys[4],
                "observations": observations,
                "up_rate": up_rate,
                "down_rate": down_rate,
                "mean_excess": mean_excess,
                "median_excess": float(np.median(finite)) if observations else math.nan,
                "q10": float(np.quantile(finite, 0.10)) if observations else math.nan,
                "q25": float(np.quantile(finite, 0.25)) if observations else math.nan,
                "q75": float(np.quantile(finite, 0.75)) if observations else math.nan,
                "q90": float(np.quantile(finite, 0.90)) if observations else math.nan,
                "max_favorable_excursion": float(pd.to_numeric(group.get("max_favorable_excursion"), errors="coerce").mean()) if "max_favorable_excursion" in group else math.nan,
                "max_adverse_excursion": float(pd.to_numeric(group.get("max_adverse_excursion"), errors="coerce").mean()) if "max_adverse_excursion" in group else math.nan,
                "cost_adjusted_mean": mean_excess - round_trip_cost,
                "bootstrap_ci_low": low,
                "bootstrap_ci_high": high,
                "baseline_up_rate": float(baseline_row["baseline_up_rate"]) if baseline_row is not None else math.nan,
                "up_rate_lift": up_rate - float(baseline_row["baseline_up_rate"]) if baseline_row is not None else math.nan,
                "mean_excess_lift": mean_excess - float(baseline_row["baseline_mean_excess"]) if baseline_row is not None else math.nan,
                "stability_score": stability,
                "research_only": observations < min_support,
            }
        )
    return pd.DataFrame(rows)
