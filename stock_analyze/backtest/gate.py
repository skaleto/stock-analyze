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

from datetime import date
from pathlib import Path
from typing import Any

from stock_analyze import competition
from stock_analyze.backtest import engine
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics


VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2026, 4, 30)


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

    return m
