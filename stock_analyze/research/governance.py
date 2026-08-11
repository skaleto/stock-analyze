"""Research trial ledger and overfitting diagnostics."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from ..utils import now_iso, write_text_atomic


def _sharpe(values: np.ndarray) -> float:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    if len(clean) < 2:
        return 0.0
    std = float(np.std(clean, ddof=1))
    return float(np.mean(clean) / std) if std > 1e-12 else 0.0


def deflated_sharpe_probability(
    *,
    observed_sharpe: float,
    trial_sharpes: list[float] | tuple[float, ...],
    observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    periods_per_year: float = 1.0,
) -> float:
    """Probability that Sharpe exceeds the multiple-trial expectation.

    This follows the Bailey and Lopez de Prado deflated-Sharpe construction:
    estimate the expected maximum Sharpe across tried specifications, then
    standardize the observed Sharpe with its non-normal finite-sample error.
    """

    annualization = math.sqrt(max(float(periods_per_year), 1e-12))
    observed_sharpe = float(observed_sharpe) / annualization
    trials = np.asarray(
        [
            float(value) / annualization
            for value in trial_sharpes
            if math.isfinite(value)
        ],
        dtype=float,
    )
    trial_count = max(len(trials), 1)
    trial_std = float(np.std(trials, ddof=1)) if len(trials) > 1 else 0.0
    if trial_count <= 1 or trial_std <= 1e-12:
        expected_max = 0.0
    else:
        normal = NormalDist()
        gamma = 0.5772156649015329
        expected_max = trial_std * (
            (1.0 - gamma) * normal.inv_cdf(1.0 - 1.0 / trial_count)
            + gamma * normal.inv_cdf(1.0 - 1.0 / (trial_count * math.e))
        )
    sample_count = max(int(observations), 2)
    denominator_term = 1.0 - skew * observed_sharpe + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    standard_error = math.sqrt(max(denominator_term, 1e-12) / (sample_count - 1))
    z_score = (float(observed_sharpe) - expected_max) / standard_error
    return float(np.clip(NormalDist().cdf(z_score), 0.0, 1.0))


def build_aligned_trial_return_matrix(trials: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the same-date trial matrix required by PBO.

    Array offsets are not dates. Comparing trials trained on different OOS
    windows would understate selection risk, so misaligned or duplicate dates
    are rejected instead of silently outer-joining them.
    """

    if not trials:
        return pd.DataFrame()
    series: dict[str, pd.Series] = {}
    expected_dates: tuple[str, ...] | None = None
    for index, trial in enumerate(trials):
        trial_id = str(trial.get("trial_id") or f"trial_{index + 1}")
        values: dict[str, float] = {}
        for item in trial.get("oos_returns") or []:
            day = str(item.get("date") or "")
            if not day or day in values:
                raise ValueError("trial_oos_dates_invalid")
            try:
                value = float(item.get("return"))
            except (TypeError, ValueError) as exc:
                raise ValueError("trial_oos_return_invalid") from exc
            if not math.isfinite(value):
                raise ValueError("trial_oos_return_invalid")
            values[day] = value
        dates = tuple(sorted(values))
        if not dates:
            raise ValueError("trial_oos_dates_missing")
        if expected_dates is None:
            expected_dates = dates
        elif dates != expected_dates:
            raise ValueError("trial_oos_dates_misaligned")
        series[trial_id] = pd.Series(
            [values[day] for day in dates],
            index=pd.Index(dates, name="date"),
            dtype=float,
        )
    return pd.DataFrame(series, index=pd.Index(expected_dates or (), name="date"))


def probability_of_backtest_overfit(
    trial_returns: pd.DataFrame,
    *,
    block_count: int = 8,
) -> float:
    """Estimate PBO with combinatorially symmetric cross-validation.

    Rows are chronological return observations and columns are tried model
    specifications. Fewer than four trials or four blocks are not enough to
    estimate selection instability, so the result fails closed at one.
    """

    numeric = trial_returns.apply(pd.to_numeric, errors="coerce").dropna(how="all")
    if numeric.shape[1] < 4 or len(numeric) < 8:
        return 1.0
    blocks = min(max(int(block_count), 4), len(numeric))
    if blocks % 2:
        blocks -= 1
    partitions = [indices for indices in np.array_split(np.arange(len(numeric)), blocks) if len(indices)]
    if len(partitions) < 4 or len(partitions) % 2:
        return 1.0
    half = len(partitions) // 2
    negative_logits = 0
    evaluated = 0
    seen: set[tuple[int, ...]] = set()
    for train_blocks in combinations(range(len(partitions)), half):
        complement = tuple(index for index in range(len(partitions)) if index not in train_blocks)
        canonical = min(tuple(train_blocks), complement)
        if canonical in seen:
            continue
        seen.add(canonical)
        train_index = np.concatenate([partitions[index] for index in train_blocks])
        test_index = np.concatenate([partitions[index] for index in complement])
        train_scores = numeric.iloc[train_index].apply(lambda series: _sharpe(series.to_numpy()))
        winner = str(train_scores.idxmax())
        test_scores = numeric.iloc[test_index].apply(lambda series: _sharpe(series.to_numpy()))
        ranks = test_scores.rank(method="average", pct=True)
        percentile = float(ranks[winner])
        negative_logits += int(percentile <= 0.5)
        evaluated += 1
    return float(negative_logits / evaluated) if evaluated else 1.0


@dataclass(frozen=True)
class TrialRegistry:
    path: Path

    def read(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def record(self, trial: dict[str, Any]) -> dict[str, Any]:
        rows = self.read()
        trial_id = str(trial.get("trial_id") or "")
        if trial_id:
            existing = next(
                (row for row in rows if str(row.get("trial_id") or "") == trial_id),
                None,
            )
            if existing is not None:
                return existing
        protocol = str(trial.get("protocol") or "unknown")
        row = {
            **trial,
            "recorded_at": trial.get("recorded_at") or now_iso(),
            "trial_number": len(rows) + 1,
            "protocol_trial_number": 1 + sum(
                str(existing.get("protocol") or "unknown") == protocol for existing in rows
            ),
        }
        payload = "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in [*rows, row]) + "\n"
        write_text_atomic(self.path, payload, encoding="utf-8")
        return row


__all__ = [
    "TrialRegistry",
    "build_aligned_trial_return_matrix",
    "deflated_sharpe_probability",
    "probability_of_backtest_overfit",
]
