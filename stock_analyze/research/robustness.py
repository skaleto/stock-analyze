"""Deterministic robustness diagnostics for bounded strategy campaigns."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd


def _clean_returns(values: Sequence[float] | pd.Series | np.ndarray) -> np.ndarray:
    clean = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(
        dtype=float
    )
    if not len(clean) or not np.isfinite(clean).all():
        raise ValueError("robustness_returns_missing")
    return clean


def stationary_block_bootstrap_probability(
    values: Sequence[float] | pd.Series | np.ndarray,
    *,
    block_length: int,
    samples: int = 10_000,
    seed: int = 20260814,
    threshold: float = 0.0,
) -> float:
    """Estimate P(mean return > threshold) with a stationary bootstrap."""

    clean = _clean_returns(values)
    sample_count = max(int(samples), 1)
    average_block = max(int(block_length), 1)
    restart_probability = 1.0 / average_block
    rng = np.random.default_rng(int(seed))
    wins = 0
    completed = 0
    chunk_size = min(sample_count, 1_000)
    while completed < sample_count:
        size = min(chunk_size, sample_count - completed)
        indices = np.empty((size, len(clean)), dtype=np.int32)
        indices[:, 0] = rng.integers(0, len(clean), size=size)
        for position in range(1, len(clean)):
            restart = rng.random(size) < restart_probability
            fresh = rng.integers(0, len(clean), size=size)
            indices[:, position] = np.where(
                restart,
                fresh,
                (indices[:, position - 1] + 1) % len(clean),
            )
        means = clean[indices].mean(axis=1)
        wins += int(np.sum(means > float(threshold)))
        completed += size
    return float(wins / sample_count)


def paired_block_bootstrap_probability(
    challenger: Sequence[float] | pd.Series | np.ndarray,
    baseline: Sequence[float] | pd.Series | np.ndarray,
    *,
    block_length: int,
    samples: int = 10_000,
    seed: int = 20260814,
) -> float:
    challenger_values = _clean_returns(challenger)
    baseline_values = _clean_returns(baseline)
    if len(challenger_values) != len(baseline_values):
        raise ValueError("robustness_paired_returns_misaligned")
    return stationary_block_bootstrap_probability(
        challenger_values - baseline_values,
        block_length=block_length,
        samples=samples,
        seed=seed,
    )


def classify_market_regimes(frame: pd.DataFrame) -> pd.Series:
    """Apply the campaign's immutable bull/range/down market definitions."""

    if "benchmark_close" not in frame.columns:
        raise ValueError("robustness_benchmark_close_missing")
    ordered = frame.copy()
    close = pd.to_numeric(ordered["benchmark_close"], errors="coerce")
    sma = (
        pd.to_numeric(ordered["benchmark_sma_200"], errors="coerce")
        if "benchmark_sma_200" in ordered.columns
        else close.rolling(200, min_periods=200).mean()
    )
    momentum = (
        pd.to_numeric(ordered["benchmark_momentum_60"], errors="coerce")
        if "benchmark_momentum_60" in ordered.columns
        else close.pct_change(60, fill_method=None)
    )
    available = close.notna() & sma.notna() & momentum.notna()
    result = pd.Series("unavailable", index=frame.index, dtype="string")
    result.loc[available] = "range"
    result.loc[available & close.gt(sma) & momentum.gt(0.05)] = "bull"
    result.loc[available & close.lt(sma) & momentum.lt(-0.05)] = "down"
    return result


def _return_drawdown(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=-0.99)
    if clean.empty:
        return 0.0
    nav = np.cumprod(1.0 + clean.to_numpy(dtype=float))
    return abs(float(np.min(nav / np.maximum.accumulate(nav) - 1.0)))


def summarize_regime_performance(frame: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    if not {"regime", "active_return"}.issubset(frame.columns):
        raise ValueError("robustness_regime_columns_missing")
    result: dict[str, dict[str, float | int]] = {}
    for regime in ("bull", "range", "down"):
        values = pd.to_numeric(
            frame.loc[frame["regime"].astype(str).eq(regime), "active_return"],
            errors="coerce",
        ).dropna()
        result[regime] = {
            "observations": int(len(values)),
            "mean_active_return": float(values.mean()) if len(values) else 0.0,
            "cumulative_active_return": (
                float(np.prod(1.0 + values.to_numpy(dtype=float)) - 1.0)
                if len(values) else 0.0
            ),
            "max_drawdown": _return_drawdown(values),
        }
    return result


def contribution_concentration(
    contributions: Mapping[str, float],
    *,
    maximum_share: float = 0.50,
) -> dict[str, object]:
    numeric = {
        str(key): float(value)
        for key, value in contributions.items()
        if np.isfinite(float(value))
    }
    total = float(sum(numeric.values()))
    largest_key = max(numeric, key=numeric.get) if numeric else ""
    largest = max(float(numeric.get(largest_key, 0.0)), 0.0)
    largest_share = largest / total if total > 0.0 else 1.0
    return {
        "passed": bool(total > 0.0 and largest_share <= float(maximum_share)),
        "total_contribution": total,
        "largest_key": largest_key,
        "largest_contribution": largest,
        "largest_share": float(largest_share),
        "maximum_share": float(maximum_share),
    }


__all__ = [
    "classify_market_regimes",
    "contribution_concentration",
    "paired_block_bootstrap_probability",
    "stationary_block_bootstrap_probability",
    "summarize_regime_performance",
]
