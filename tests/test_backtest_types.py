"""Tests for backtest result dataclasses."""
import unittest
from datetime import date
from pathlib import Path

from stock_analyze.markets.a_share.backtest.types import (
    BacktestMetrics,
    BacktestResult,
    CoverageReport,
)


class BacktestMetricsTests(unittest.TestCase):
    def test_construction(self):
        m = BacktestMetrics(
            cum_return=0.183,
            annual_return=0.087,
            sharpe=1.4,
            max_drawdown=-0.087,
            information_ratio=0.92,
        )
        self.assertAlmostEqual(m.cum_return, 0.183)
        self.assertAlmostEqual(m.annual_return, 0.087)
        self.assertAlmostEqual(m.sharpe, 1.4)
        self.assertAlmostEqual(m.max_drawdown, -0.087)
        self.assertAlmostEqual(m.information_ratio, 0.92)


class BacktestResultTests(unittest.TestCase):
    def test_construction(self):
        result = BacktestResult(
            out_dir=Path('/tmp/bt'),
            start=date(2021, 1, 1),
            end=date(2024, 12, 31),
            metrics=BacktestMetrics(
                cum_return=0.183,
                annual_return=0.087,
                sharpe=1.4,
                max_drawdown=-0.087,
                information_ratio=0.92,
            ),
        )
        self.assertEqual(result.out_dir, Path('/tmp/bt'))
        self.assertEqual(result.start, date(2021, 1, 1))
        self.assertEqual(result.end, date(2024, 12, 31))
        self.assertAlmostEqual(result.metrics.sharpe, 1.4)


class CoverageReportTests(unittest.TestCase):
    def test_complete(self):
        r = CoverageReport(complete=True)
        self.assertTrue(r.complete)
        self.assertEqual(r.missing_weeks, [])
        self.assertAlmostEqual(r.missing_pct, 0.0)

    def test_incomplete(self):
        r = CoverageReport(
            complete=False,
            missing_weeks=['2021-W01', '2021-W02'],
            missing_pct=0.05,
        )
        self.assertFalse(r.complete)
        self.assertEqual(r.missing_weeks, ['2021-W01', '2021-W02'])
        self.assertAlmostEqual(r.missing_pct, 0.05)


if __name__ == '__main__':
    unittest.main()
