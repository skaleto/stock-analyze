"""Tests for backtest markdown report renderer."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from stock_analyze.markets.a_share.backtest.report import render_markdown_report
from stock_analyze.markets.a_share.backtest.types import BacktestMetrics, BacktestResult


def _sample_result(out_dir: Path = Path("/tmp/bt")) -> BacktestResult:
    return BacktestResult(
        out_dir=out_dir,
        start=date(2023, 1, 1),
        end=date(2024, 12, 31),
        metrics=BacktestMetrics(
            cum_return=0.183,
            annual_return=0.087,
            sharpe=1.4,
            max_drawdown=-0.087,
            information_ratio=0.92,
        ),
    )


class RenderMarkdownReportTests(unittest.TestCase):
    def test_renders_required_sections(self):
        md = render_markdown_report(_sample_result())
        self.assertIn("## 总结", md)
        self.assertIn("## 风险归因", md)

    def test_numbers_appear(self):
        md = render_markdown_report(_sample_result())
        self.assertIn("+18.3%", md)
        self.assertIn("1.40", md)

    def test_window_dates_appear(self):
        md = render_markdown_report(_sample_result())
        self.assertIn("2023-01-01", md)
        self.assertIn("2024-12-31", md)

    def test_handles_empty_metrics(self):
        empty = BacktestResult(
            out_dir=Path("/tmp/empty"),
            start=date(2023, 1, 1),
            end=date(2023, 1, 5),
            metrics=BacktestMetrics(0, 0, 0, 0, 0),
        )
        md = render_markdown_report(empty)
        self.assertIn("总结", md)


if __name__ == "__main__":
    unittest.main()
