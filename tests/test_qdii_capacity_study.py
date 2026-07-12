from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.markets.cn_qdii_etf.capacity_study import (
    CapacityStudyError,
    run_capacity_study,
    write_capacity_artifacts,
)
from stock_analyze.markets.cn_qdii_etf.research_panel import ResearchPanelResult


def _panel() -> ResearchPanelResult:
    dates = pd.bdate_range("2024-01-02", periods=150)
    rows: list[dict] = []
    definitions = [
        ("510001.SH", "index_a", 0.0014, "2024-01-01"),
        ("510002.SH", "index_a", 0.0011, "2024-01-01"),
        ("510003.SH", "index_b", 0.0008, "2024-01-01"),
        ("510004.SH", "index_c", 0.0005, "2024-01-01"),
        ("510005.SH", "index_d", 0.0002, "2024-01-01"),
        ("510006.SH", "index_e", -0.0001, "2024-01-01"),
    ]
    for code_no, (code, index_key, drift, list_date) in enumerate(definitions):
        base = 1.0 + code_no * 0.05
        for day_no, trade_date in enumerate(dates):
            close = base * ((1.0 + drift) ** day_no) * (1.0 + 0.003 * np.sin(day_no / 5 + code_no))
            rows.append(
                {
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "code": code,
                    "name": code,
                    "scope": "us_exposure",
                    "index_key": index_key,
                    "theme": index_key,
                    "list_date": list_date,
                    "open": close * 0.998,
                    "close": close,
                    "adj_close": close,
                    "amount_yuan": 20_000_000 + code_no * 1_000_000,
                    "nav": close,
                    "discount_premium": 0.0,
                    "fund_size_yuan": 1_000_000_000,
                    "management_fee": 0.5,
                }
            )
    return ResearchPanelResult(
        frame=pd.DataFrame(rows),
        metadata={
            "universe_hash": "synthetic",
            "catalog_as_of": "2024-07-31",
            "survivorship_bias": True,
            "source_contract": "current-catalog historical replay",
        },
    )


def _overlay() -> dict:
    return {
        "agent_id": "codex",
        "strategy_id": "trend_test",
        "name": "趋势测试",
        "factors": {
            "momentum_20": {"weight": 0.45, "direction": "high"},
            "momentum_60": {"weight": 0.35, "direction": "high"},
            "avg_amount_20": {"weight": 0.15, "direction": "high"},
            "low_volatility_60": {"weight": 0.05, "direction": "low"},
        },
        "factor_processing": {
            "winsorize_lower": 0.0,
            "winsorize_upper": 1.0,
            "neutralize_industry": False,
            "min_factor_coverage": 0.5,
        },
        "portfolio_controls": {
            "max_etfs_per_index": 1,
            "hold_buffer_pct": 0.0,
            "max_holding_days": 45,
        },
        "filters": {
            "max_fetch_candidates": 20,
            "min_listing_days": 0,
            "min_avg_amount_20_yuan": 1_000_000,
            "max_abs_premium": 0.08,
            "min_fund_size_yuan": 100_000_000,
        },
    }


def _baseline() -> dict:
    return {
        "initial_cash": 500_000,
        "accounts": [
            {
                "id": "us_exposure",
                "scope": "us_exposure",
                "cash": 500_000,
                "top_n": 5,
                "benchmark": "510001.SH",
            }
        ],
        "trading": {
            "commission_rate": 0.0003,
            "slippage_bps": 5,
            "max_single_weight": 0.20,
            "lot_size_default": 100,
        },
    }


class QDIICapacityStudyTests(unittest.TestCase):
    def test_runs_top_n_sensitivity_with_point_in_time_execution(self) -> None:
        result = run_capacity_study(
            _panel(),
            overlays={"codex": _overlay()},
            baseline=_baseline(),
            top_ns=[2, 3],
            start="2024-01-02",
            end="2024-07-29",
            min_signal_weeks=4,
        )

        self.assertEqual(result.metrics["top_n"].tolist(), [2, 3])
        self.assertTrue(
            {
                "cumulative_return",
                "sharpe_ratio",
                "max_drawdown",
                "cumulative_excess_return",
                "weekly_turnover_avg",
                "cost_bps",
                "effective_correlation_clusters",
                "average_max_index_weight",
            }.issubset(result.metrics.columns)
        )
        first_signal = result.selections["signal_date"].min()
        first_trade = result.trades["trade_date"].min()
        self.assertGreater(first_trade, first_signal)
        self.assertTrue((result.trades["shares"] % 100 == 0).all())
        self.assertGreater(result.trades["commission"].sum(), 0)
        self.assertGreater(result.trades["slippage"].sum(), 0)
        top_two = result.selections[result.selections["top_n"] == 2]
        diversity = top_two.groupby("signal_date")["index_key"].nunique()
        self.assertTrue((diversity == 2).all())
        self.assertTrue(result.summary["limitations"]["survivorship_bias"])

    def test_rejects_short_study_window(self) -> None:
        with self.assertRaisesRegex(CapacityStudyError, "insufficient_signal_weeks"):
            run_capacity_study(
                _panel(),
                overlays={"codex": _overlay()},
                baseline=_baseline(),
                top_ns=[2],
                start="2024-05-01",
                end="2024-05-31",
                min_signal_weeks=20,
            )

    def test_writes_traceable_research_artifacts(self) -> None:
        result = run_capacity_study(
            _panel(),
            overlays={"codex": _overlay()},
            baseline=_baseline(),
            top_ns=[2],
            start="2024-01-02",
            end="2024-07-29",
            min_signal_weeks=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_capacity_artifacts(result, Path(tmp), end_date="2024-07-29")

            self.assertTrue(paths["summary"].exists())
            self.assertTrue(paths["metrics"].exists())
            self.assertTrue(paths["selections"].exists())
            self.assertTrue(paths["trades"].exists())
            self.assertTrue(paths["nav"].exists())
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(len(summary["metrics"]), 1)
            self.assertEqual(summary["metrics"][0]["top_n"], 2)
            report = paths["report"].read_text(encoding="utf-8")
            self.assertIn("当前目录历史回放", report)
            self.assertIn("幸存者偏差", report)
            self.assertIn("不自动修改", report)

            repeated = run_capacity_study(
                _panel(),
                overlays={"codex": _overlay()},
                baseline=_baseline(),
                top_ns=[2],
                start="2024-01-02",
                end="2024-07-29",
                min_signal_weeks=4,
            )
            self.assertEqual(repeated.run_id, result.run_id)


if __name__ == "__main__":
    unittest.main()
