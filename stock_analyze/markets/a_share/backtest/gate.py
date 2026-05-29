"""Backtest floor gate.

Called by ``evolution_writer.write_evolution`` after ``overlay_guard.validate``
passes: runs a validation-window backtest of the new overlay and rejects it
when the result trips any of three floor thresholds. The gate's role is to
catch catastrophes, not to evaluate quality — the thresholds are intentionally
loose (max DD ≤ 25%, Sharpe ≥ −0.5, cumulative ≥ −15%).

Thresholds live in ``configs/competition.yaml::backtest.floor.*`` and are
operator-tunable (not in ``BASELINE_LOCKED_PATHS``).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .... import competition
from . import engine
from .exceptions import BacktestFloorBreach, BacktestStructuralBreach
from .types import BacktestMetrics


VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2026, 4, 30)

# Structural-equivalence guard: a healthy signal day's scores must have at
# least this fraction of distinct values, else the scoring is degenerate
# (nearly all candidates tied). See bridge-factor-pipeline-into-backtest §5.
MIN_UNIQUE_SCORE_RATIO = 0.5


def check_structural_equivalence(
    samples: list[dict[str, Any]],
    *,
    min_unique_ratio: float = MIN_UNIQUE_SCORE_RATIO,
) -> None:
    """Pure structural-equivalence check over sampled score distributions.

    Each sample is ``{"date": iso, "scores": [float, ...]}``. For each
    sample with >= 2 scores, the fraction of distinct (rounded) score values
    must be >= ``min_unique_ratio``; otherwise the scoring is degenerate and
    ``BacktestStructuralBreach`` is raised. Empty / single-score samples are
    skipped, so an all-empty ``samples`` list is a no-op (thin cache — never
    false-positive).
    """
    for s in samples:
        scores = s.get("scores") or []
        if len(scores) < 2:
            continue
        n_unique = len({round(float(v), 6) for v in scores})
        ratio = n_unique / len(scores)
        if ratio < min_unique_ratio:
            raise BacktestStructuralBreach({
                "type": "degenerate_scores",
                "date": s.get("date"),
                "n_scores": len(scores),
                "n_unique": n_unique,
                "unique_ratio": round(ratio, 4),
                "min_unique_ratio": min_unique_ratio,
            })


def _sample_signal_scores(
    overlay: dict[str, Any],
    cache_root: Path,
    universe: list[str],
    *,
    n_samples: int = 3,
) -> list[dict[str, Any]]:
    """Re-score up to ``n_samples`` evenly-spaced validation-window days.

    Returns ``[{"date": iso, "scores": [...]}]`` for days that have data.
    Days with no ``daily_basic`` snapshot are skipped, so on a thin cache
    this returns an empty list and the structural check no-ops.
    """
    from .data_view import PointInTimeView

    span_days = (VALIDATION_END - VALIDATION_START).days
    offsets = [int(span_days * f) for f in (0.25, 0.5, 0.75)][:n_samples]
    samples: list[dict[str, Any]] = []
    for off in offsets:
        d = VALIDATION_START + timedelta(days=off)
        view = PointInTimeView(as_of=d, cache_root=cache_root)
        if view.daily_basic(as_of=d).empty:
            continue
        rows = engine._compute_signals(view, overlay, d, universe)
        if rows:
            samples.append({"date": d.isoformat(),
                             "scores": [r["score"] for r in rows]})
    return samples


def validate_overlay_via_backtest(
    overlay: dict[str, Any],
    *,
    agent_id: str,
    cache_root: Path = Path("data/shared/backtest_cache"),
    out_dir: Path | None = None,
) -> BacktestMetrics:
    """Run the validation-window backtest of ``overlay`` and check floors.

    Raises ``BacktestFloorBreach`` if any floor is tripped. Otherwise
    returns the ``BacktestMetrics`` for the run (cumulative, annual, Sharpe,
    max drawdown, information ratio).

    The three floors are read from ``configs/competition.yaml::backtest.floor``.
    Order of evaluation: max_drawdown, sharpe, cum_return (first breach wins,
    per the spec).
    """
    cfg = competition.load(agent_id)
    floor_cfg = cfg.get("backtest", {}).get("floor", {})
    max_dd_floor = float(floor_cfg.get("max_drawdown", 0.25))
    sharpe_floor = float(floor_cfg.get("sharpe_floor", -0.5))
    cum_floor = float(floor_cfg.get("cum_return_floor", -0.15))

    target_out = out_dir or Path("data") / "_temp" / "backtest_validation"
    target_out.mkdir(parents=True, exist_ok=True)

    result = engine.run_backtest(
        overlay=overlay,
        start=VALIDATION_START,
        end=VALIDATION_END,
        universe=["hs300", "zz500"],
        market_data_root=cache_root,
        out_dir=target_out,
        in_memory=True,
    )

    m = result.metrics

    # Evaluate floors in priority order: max_drawdown > sharpe > cum_return.
    # abs(max_drawdown) is the magnitude (max_drawdown itself is negative).
    if abs(m.max_drawdown) > max_dd_floor:
        raise BacktestFloorBreach("max_drawdown_exceeded", m)
    if m.sharpe < sharpe_floor:
        raise BacktestFloorBreach("sharpe_below_floor", m)
    if m.cum_return < cum_floor:
        raise BacktestFloorBreach("cum_return_below_floor", m)

    # Structural-equivalence guard (bridge-factor-pipeline-into-backtest §5):
    # re-score a few validation-window days and reject if the scoring is
    # degenerate (all-tied) — a failure the return-based floors can't catch
    # because a tied universe still earns ordinary returns. No-ops on a thin
    # cache (sample days with no data are skipped).
    samples = _sample_signal_scores(overlay, cache_root, ["hs300", "zz500"])
    check_structural_equivalence(samples)

    return m
