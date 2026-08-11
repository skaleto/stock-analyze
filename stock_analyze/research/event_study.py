"""Conditional event statistics with deterministic bootstrap uncertainty."""

from __future__ import annotations

import gc
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def _bootstrap_mean_interval(values: np.ndarray, samples: int, rng: np.random.Generator) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        return (math.nan, math.nan)
    if len(finite) == 1 or samples <= 0:
        return (float(finite[0]), float(finite[0]))
    if len(finite) > 500:
        standard_error = float(np.std(finite, ddof=1) / math.sqrt(len(finite)))
        mean = float(np.mean(finite))
        if not np.isfinite(standard_error) or standard_error <= 0:
            return (mean, mean)
        # The sampling distribution of the mean is asymptotically normal. This
        # keeps large event groups O(samples) instead of O(samples * rows).
        means = rng.normal(loc=mean, scale=standard_error, size=samples)
        return (float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975)))
    max_draws_per_batch = 1_000_000
    batch_samples = max(1, min(samples, max_draws_per_batch // len(finite)))
    means_parts: list[np.ndarray] = []
    remaining = samples
    while remaining > 0:
        current = min(batch_samples, remaining)
        indices = rng.integers(0, len(finite), size=(current, len(finite)))
        means_parts.append(finite[indices].mean(axis=1))
        remaining -= current
    means = np.concatenate(means_parts)
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

    event_columns = [
        column
        for column in ("market", "event", "code", "trade_date", "regime", "industry")
        if column in events
    ]
    label_columns = [
        column
        for column in (
            "code", "trade_date", "horizon", "label", "excess_return",
            "max_favorable_excursion", "max_adverse_excursion",
        )
        if column in labels
    ]
    event_frame = events.loc[:, event_columns].copy()
    label_frame = labels.loc[:, label_columns].copy()
    for frame in (event_frame, label_frame):
        frame["code"] = frame["code"].astype("string[pyarrow]")
        frame["trade_date"] = frame["trade_date"].astype("string[pyarrow]")
    event_frame["regime"] = event_frame.get("regime", "unknown").fillna("unknown")
    event_frame["industry"] = event_frame.get("industry", "unclassified").fillna("unclassified")

    baseline = label_frame.groupby("horizon").agg(
        baseline_up_rate=("label", lambda values: float((values == "up").mean())),
        baseline_mean_excess=("excess_return", "mean"),
    )
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    group_columns = ["market", "event", "regime", "industry"]
    for horizon in sorted(pd.to_numeric(label_frame["horizon"], errors="coerce").dropna().astype(int).unique()):
        horizon_labels = label_frame.loc[label_frame["horizon"].eq(horizon)]
        joined = event_frame.merge(
            horizon_labels,
            on=["code", "trade_date"],
            how="inner",
        )
        if joined.empty:
            continue
        baseline_row = baseline.loc[horizon] if horizon in baseline.index else None
        for keys, group in joined.groupby(group_columns, dropna=False, sort=True):
            values = pd.to_numeric(group["excess_return"], errors="coerce").to_numpy(dtype=float)
            finite = values[np.isfinite(values)]
            low, high = _bootstrap_mean_interval(finite, bootstrap_samples, rng)
            observations = len(finite)
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
                    "regime": keys[2],
                    "industry": keys[3],
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
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["market", "event", "horizon", "regime", "industry"]
    ).reset_index(drop=True)


def build_event_study_from_parquet(
    events_path: str | Path,
    labels_path: str | Path,
    *,
    min_support: int = 30,
    round_trip_cost: float = 0.0015,
    bootstrap_samples: int = 1000,
    seed: int = 20260713,
) -> pd.DataFrame:
    """Build statistics one horizon at a time from immutable snapshots."""

    event_columns = ["market", "event", "code", "trade_date", "regime", "industry"]
    label_columns = [
        "code", "trade_date", "horizon", "label", "excess_return",
        "max_favorable_excursion", "max_adverse_excursion",
    ]
    event_schema = set(pq.read_schema(events_path).names)
    label_schema = set(pq.read_schema(labels_path).names)
    if {"market", "event", "code", "trade_date"}.difference(event_schema):
        raise ValueError("event_study_missing_columns")
    if {"code", "trade_date", "horizon", "label", "excess_return"}.difference(label_schema):
        raise ValueError("event_study_missing_columns")

    events = pd.read_parquet(
        events_path,
        columns=[column for column in event_columns if column in event_schema],
    )
    horizon_frame = pd.read_parquet(labels_path, columns=["horizon"])
    horizons = sorted(
        pd.to_numeric(horizon_frame["horizon"], errors="coerce").dropna().astype(int).unique()
    )
    del horizon_frame
    parts: list[pd.DataFrame] = []
    available_label_columns = [column for column in label_columns if column in label_schema]
    for index, horizon in enumerate(horizons):
        labels = pd.read_parquet(
            labels_path,
            columns=available_label_columns,
            filters=[("horizon", "=", int(horizon))],
        )
        part = build_event_study(
            events,
            labels,
            min_support=min_support,
            round_trip_cost=round_trip_cost,
            bootstrap_samples=bootstrap_samples,
            seed=seed + index,
        )
        if not part.empty:
            parts.append(part)
        del labels, part
        gc.collect()
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values(
        ["market", "event", "horizon", "regime", "industry"]
    ).reset_index(drop=True)
