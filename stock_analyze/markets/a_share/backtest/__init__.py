"""Historical backtest engine.

Reuses ``stock_analyze.markets.a_share.simulator`` to drive a day-by-day loop
over a historical window, reading point-in-time market data from a separate
``data/shared/backtest_cache/``. Forward simulation behavior is preserved
(default simulator parameters maintain current behavior).

See ``openspec/changes/add-historical-backtest-engine/design.md`` for the
full design rationale.
"""
from .types import (
    BacktestMetrics,
    BacktestResult,
    CoverageReport,
)

__all__ = ['BacktestMetrics', 'BacktestResult', 'CoverageReport']
