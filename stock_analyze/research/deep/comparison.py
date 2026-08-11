"""Paired same-row comparison for DL-D0 and DL-D1 predictions."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ...utils import write_text_atomic


def _block_interval(
    values: list[float],
    *,
    block_size: int,
    repetitions: int = 1000,
) -> list[float] | None:
    if len(values) < 2:
        return None
    array = np.asarray(values, dtype=float)
    rng = np.random.default_rng(20260729)
    means = []
    block_size = max(1, min(block_size, len(array)))
    for _ in range(repetitions):
        sampled: list[float] = []
        while len(sampled) < len(array):
            start = int(rng.integers(0, len(array)))
            sampled.extend(
                array.take((np.arange(block_size) + start) % len(array)).tolist()
            )
        means.append(float(np.mean(sampled[: len(array)])))
    return [
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
    ]


def _score_metrics(
    frame: pd.DataFrame,
    score_column: str,
    *,
    horizon: int,
) -> dict[str, Any]:
    rank_ics: list[float] = []
    spreads: list[float] = []
    for _, group in frame.groupby("trade_date", sort=True):
        rank_ic = group[score_column].rank().corr(group["excess_return"].rank())
        if pd.notna(rank_ic):
            rank_ics.append(float(rank_ic))
        tail = max(1, int(math.ceil(len(group) * 0.10)))
        ordered = group.sort_values(score_column)
        spreads.append(
            float(
                ordered.tail(tail)["excess_return"].mean()
                - ordered.head(tail)["excess_return"].mean()
            )
        )
    return {
        "rank_ic": float(np.mean(rank_ics)) if rank_ics else None,
        "rank_ic_std": float(np.std(rank_ics, ddof=1)) if len(rank_ics) > 1 else None,
        "rank_ic_block_ci": _block_interval(rank_ics, block_size=horizon),
        "top_bottom_decile_spread": float(np.mean(spreads)) if spreads else None,
        "top_bottom_spread_block_ci": _block_interval(spreads, block_size=horizon),
        "daily_observations": len(rank_ics),
    }


def compare_deep_predictions(
    dl_d0: pd.DataFrame,
    dl_d1: pd.DataFrame,
    *,
    horizon: int = 20,
) -> dict[str, Any]:
    required_d0 = {
        "code",
        "trade_date",
        "excess_return",
        "predicted_excess_return",
    }
    d1_score = f"predicted_excess_return_{horizon}"
    required_d1 = {"code", "trade_date", d1_score}
    if missing := required_d0.difference(dl_d0.columns):
        raise ValueError(f"deep_comparison_d0_columns:{','.join(sorted(missing))}")
    if missing := required_d1.difference(dl_d1.columns):
        raise ValueError(f"deep_comparison_d1_columns:{','.join(sorted(missing))}")
    frame = dl_d0[list(required_d0)].merge(
        dl_d1[list(required_d1)],
        on=["code", "trade_date"],
        how="inner",
        validate="one_to_one",
    )
    if frame.empty:
        raise ValueError("deep_comparison_empty")
    frame["dl_d0_rank"] = frame.groupby("trade_date")[
        "predicted_excess_return"
    ].rank(pct=True)
    frame["dl_d1_rank"] = frame.groupby("trade_date")[d1_score].rank(pct=True)
    frame["ensemble_rank"] = 0.5 * frame["dl_d0_rank"] + 0.5 * frame["dl_d1_rank"]
    correlations = [
        float(group["dl_d0_rank"].corr(group["dl_d1_rank"]))
        for _, group in frame.groupby("trade_date", sort=True)
    ]
    return {
        "schema_version": 1,
        "research_only": True,
        "horizon": int(horizon),
        "common_rows": len(frame),
        "common_dates": int(frame["trade_date"].nunique()),
        "daily_prediction_rank_correlation": float(np.nanmean(correlations)),
        "models": {
            "dl_d0": _score_metrics(
                frame,
                "predicted_excess_return",
                horizon=horizon,
            ),
            "dl_d1": _score_metrics(frame, d1_score, horizon=horizon),
        },
        "ensemble": _score_metrics(frame, "ensemble_rank", horizon=horizon),
    }


def write_deep_comparison(
    report: dict[str, Any],
    path: str | Path,
) -> Path:
    output = Path(path)
    write_text_atomic(
        output,
        json.dumps(report, ensure_ascii=False, indent=2),
    )
    return output
