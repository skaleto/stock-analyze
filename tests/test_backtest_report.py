"""Tests for backtest markdown report renderer."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from stock_analyze.markets.a_share.backtest.report import (
    render_compare_panel_markdown,
    render_markdown_report,
)
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


class CompareMvpPanelTests(unittest.TestCase):
    """--compare-mvp panel (bridge-factor-pipeline §6)."""

    def _result(self, cum, dd, sharpe, ir):
        return BacktestResult(
            out_dir=Path("/tmp/x"), start=date(2025, 1, 1), end=date(2026, 4, 30),
            metrics=BacktestMetrics(cum_return=cum, annual_return=cum,
                                     sharpe=sharpe, max_drawdown=dd,
                                     information_ratio=ir),
        )

    def test_panel_has_all_four_metric_rows_with_both_columns(self):
        full = self._result(0.123, -0.072, 1.4, 0.6)
        mvp = self._result(0.081, -0.114, 0.8, 0.3)
        md = render_compare_panel_markdown(full, mvp)
        self.assertIn("与 MVP PE-only 信号对比", md)
        for label in ("累计收益", "最大回撤", "Sharpe", "信息比率"):
            self.assertIn(label, md)
        # both runs' numbers appear
        self.assertIn("+12.3%", md)   # full cum
        self.assertIn("+8.1%", md)    # mvp cum
        self.assertIn("1.40", md)     # full sharpe
        self.assertIn("0.80", md)     # mvp sharpe


if __name__ == "__main__":
    unittest.main()
