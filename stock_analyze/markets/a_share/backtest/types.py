"""Dataclasses describing the inputs and outputs of the backtest engine.

These types are intentionally lightweight; the engine produces files on disk
(daily_nav.csv / trades.csv / signals.csv / performance_summary.json) that
match the forward simulator schema. The dataclasses here are convenience
containers used by ``stock_analyze.backtest.engine`` and ``...gate`` to pass
metrics around in-process.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List


@dataclass
class BacktestMetrics:
    """Five aggregate metrics describing a backtest's performance.

    These are the only metrics the gate and the briefing's validation-window
    section are allowed to surface. Per-month / per-factor breakdowns must
    NOT be derived from this dataclass — they belong in the training-window
    full report or in factor_runs/.
    """

    cum_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float  # negative number (e.g. -0.087 for -8.7%)
    information_ratio: float


@dataclass
class BacktestResult:
    """Container summarising a backtest run.

    ``out_dir`` is the directory on disk that holds the canonical artefacts
    (daily_nav.csv etc.); ``metrics`` is the aggregate summary.
    """

    out_dir: Path
    start: date
    end: date
    metrics: BacktestMetrics


@dataclass
class CoverageReport:
    """Report whether prepared market-data covers the requested window.

    Used by the gate to detect missing alt-factor history before running a
    backtest that needs it. ``missing_pct`` is in [0.0, 1.0].
    """

    complete: bool
    missing_weeks: List[str] = field(default_factory=list)
    missing_pct: float = 0.0
