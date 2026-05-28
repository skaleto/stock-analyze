"""Tests for backtest floor gate."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_analyze.markets.a_share.backtest import gate
from stock_analyze.markets.a_share.backtest.exceptions import BacktestFloorBreach
from stock_analyze.markets.a_share.backtest.types import BacktestMetrics, BacktestResult


def _result(cum: float, annual: float, sharpe: float, max_dd: float,
              ir: float) -> BacktestResult:
    return BacktestResult(
        out_dir=Path("/tmp"),
        start=date(2025, 1, 1),
        end=date(2026, 4, 30),
        metrics=BacktestMetrics(
            cum_return=cum, annual_return=annual, sharpe=sharpe,
            max_drawdown=max_dd, information_ratio=ir,
        ),
    )


class ValidateOverlayViaBacktestTests(unittest.TestCase):
    def test_passing_overlay_returns_metrics(self):
        good = _result(cum=0.05, annual=0.04, sharpe=0.8,
                        max_dd=-0.10, ir=0.6)
        with patch("stock_analyze.markets.a_share.backtest.engine.run_backtest",
                    return_value=good), \
             patch("stock_analyze.competition.load",
                    return_value={"backtest": {"floor": {
                        "max_drawdown": 0.25,
                        "sharpe_floor": -0.5,
                        "cum_return_floor": -0.15,
                    }}}):
            metrics = gate.validate_overlay_via_backtest(
                {"agent_id": "claude", "factors": {}}, agent_id="claude",
            )
            self.assertAlmostEqual(metrics.sharpe, 0.8)

    def test_breach_on_max_drawdown(self):
        bad = _result(cum=-0.20, annual=-0.15, sharpe=-0.8,
                       max_dd=-0.32, ir=-1.4)
        with patch("stock_analyze.markets.a_share.backtest.engine.run_backtest",
                    return_value=bad), \
             patch("stock_analyze.competition.load",
                    return_value={"backtest": {"floor": {
                        "max_drawdown": 0.25,
                        "sharpe_floor": -0.5,
                        "cum_return_floor": -0.15,
                    }}}):
            with self.assertRaises(BacktestFloorBreach) as ctx:
                gate.validate_overlay_via_backtest(
                    {"agent_id": "claude", "factors": {}}, agent_id="claude",
                )
            self.assertEqual(ctx.exception.breach_type, "max_drawdown_exceeded")
            self.assertAlmostEqual(ctx.exception.metrics.max_drawdown, -0.32)

    def test_breach_on_sharpe_floor(self):
        bad = _result(cum=-0.05, annual=-0.04, sharpe=-0.8,
                       max_dd=-0.10, ir=-1.0)
        with patch("stock_analyze.markets.a_share.backtest.engine.run_backtest",
                    return_value=bad), \
             patch("stock_analyze.competition.load",
                    return_value={"backtest": {"floor": {
                        "max_drawdown": 0.25,
                        "sharpe_floor": -0.5,
                        "cum_return_floor": -0.15,
                    }}}):
            with self.assertRaises(BacktestFloorBreach) as ctx:
                gate.validate_overlay_via_backtest(
                    {"agent_id": "claude", "factors": {}}, agent_id="claude",
                )
            self.assertEqual(ctx.exception.breach_type, "sharpe_below_floor")

    def test_breach_on_cum_return_floor(self):
        bad = _result(cum=-0.20, annual=-0.15, sharpe=0.0,
                       max_dd=-0.10, ir=0.0)
        with patch("stock_analyze.markets.a_share.backtest.engine.run_backtest",
                    return_value=bad), \
             patch("stock_analyze.competition.load",
                    return_value={"backtest": {"floor": {
                        "max_drawdown": 0.25,
                        "sharpe_floor": -0.5,
                        "cum_return_floor": -0.15,
                    }}}):
            with self.assertRaises(BacktestFloorBreach) as ctx:
                gate.validate_overlay_via_backtest(
                    {"agent_id": "claude", "factors": {}}, agent_id="claude",
                )
            self.assertEqual(ctx.exception.breach_type, "cum_return_below_floor")

    def test_max_drawdown_takes_priority(self):
        """When multiple floors breached, max_drawdown is reported first."""
        bad = _result(cum=-0.50, annual=-0.30, sharpe=-1.5,
                       max_dd=-0.40, ir=-2.0)
        with patch("stock_analyze.markets.a_share.backtest.engine.run_backtest",
                    return_value=bad), \
             patch("stock_analyze.competition.load",
                    return_value={"backtest": {"floor": {
                        "max_drawdown": 0.25,
                        "sharpe_floor": -0.5,
                        "cum_return_floor": -0.15,
                    }}}):
            with self.assertRaises(BacktestFloorBreach) as ctx:
                gate.validate_overlay_via_backtest(
                    {"agent_id": "claude", "factors": {}}, agent_id="claude",
                )
            self.assertEqual(ctx.exception.breach_type, "max_drawdown_exceeded")


if __name__ == "__main__":
    unittest.main()
