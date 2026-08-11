from __future__ import annotations

import math
import unittest


def _detail(
    agent: str,
    totals: list[float],
    benchmark_returns: list[float],
    *,
    factors: dict[str, float],
    positions: list[dict] | None = None,
    orders: list[dict] | None = None,
    trades: list[dict] | None = None,
    cash: float = 20.0,
    lookthrough: dict | None = None,
    dates: list[str] | None = None,
) -> dict:
    dates = dates or ["2026-07-10", "2026-07-13", "2026-07-14"]
    series = [
        {
            "date": date,
            "total_value": total,
            "cash": cash if index == len(dates) - 1 else total,
            "benchmark_return": benchmark_returns[index],
        }
        for index, (date, total) in enumerate(zip(dates, totals))
    ]
    return {
        "agent": agent,
        "strategy": {
            "agent": agent,
            "strategy_id": f"{agent}_strategy_v1",
            "name": f"{agent} strategy",
            "factors": [
                {"key": key, "label": key, "weight": weight, "direction": "high"}
                for key, weight in factors.items()
            ],
        },
        "nav": {"series": series, "latest": series[-1]},
        "positions": {"rows": positions or [], "summary": {"total": len(positions or [])}},
        "orders": {"rows": orders or [], "summary": {"total": len(orders or [])}},
        "trades": {"rows": trades or [], "summary": {"total": len(trades or [])}},
        "lookthrough": lookthrough or {},
    }


class StrategyComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = {
            "season_id": "dual_strategy_2026_s1",
            "name": "双策略对抗 · 赛季1",
            "effective_date": "2026-07-11",
            "factor_distance_floor": 0.45,
            "slots": {
                "claude": {
                    "label": "稳健防守",
                    "description": "价值质量、低波与低换手",
                    "color": "#d6a84b",
                },
                "codex": {
                    "label": "趋势进攻",
                    "description": "动量成长与主动换仓",
                    "color": "#22d3ee",
                },
            },
        }

    def test_builds_season_metrics_and_pair_dimensions(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        defensive = _detail(
            "claude",
            [100.0, 102.0, 99.0],
            [0.10, 0.11, 0.105],
            factors={"pe": 0.8, "momentum_20": 0.2},
            positions=[
                {"code": "A", "exposure_group": "科技", "market_value": 60.0},
                {"code": "B", "exposure_group": "金融", "market_value": 19.0},
            ],
            trades=[
                {
                    "trade_date": "2026-07-13",
                    "gross_amount": 30.0,
                    "commission": 0.10,
                    "stamp_tax": 0.0,
                    "slippage": 0.05,
                },
                {
                    "trade_date": "2026-07-14",
                    "gross_amount": 20.0,
                    "commission": 0.10,
                    "stamp_tax": 0.02,
                    "slippage": 0.03,
                },
            ],
            cash=20.0,
        )
        trend = _detail(
            "codex",
            [100.0, 101.0, 104.0],
            [0.10, 0.115, 0.12],
            factors={"pe": 0.1, "momentum_20": 0.9},
            positions=[
                {"code": "B", "exposure_group": "金融", "market_value": 30.0},
                {"code": "C", "exposure_group": "消费", "market_value": 34.0},
            ],
            cash=40.0,
        )

        result = build_strategy_comparison(
            "a_share",
            {"claude": defensive, "codex": trend},
            registry=self.registry,
        )

        self.assertEqual(result["season"]["effective_date"], "2026-07-11")
        self.assertEqual(result["season"]["anchor_date"], "2026-07-10")
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["season_return"], -0.01)
        self.assertAlmostEqual(result["strategies"]["codex"]["metrics"]["season_return"], 0.04)
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["benchmark_return"], 0.005 / 1.1)
        self.assertAlmostEqual(
            result["strategies"]["claude"]["metrics"]["excess_return"],
            -0.01 - (0.005 / 1.1),
        )
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["cash_ratio"], 20 / 99)
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["turnover"], 0.5)
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["trading_cost"], 0.30)
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["cost_bps"], 60.0)
        self.assertIsNotNone(result["strategies"]["claude"]["metrics"]["annualized_volatility"])
        self.assertIsNotNone(result["strategies"]["claude"]["metrics"]["sharpe"])
        self.assertAlmostEqual(result["strategies"]["claude"]["metrics"]["max_drawdown"], 99 / 102 - 1)
        self.assertAlmostEqual(result["pair"]["position_overlap"], 1 / 3)
        self.assertAlmostEqual(result["pair"]["factor_distance"], 0.7)
        self.assertTrue(math.isfinite(result["pair"]["return_correlation"]))
        self.assertEqual(result["strategies"]["claude"]["holdings_source"], "positions")
        self.assertEqual(result["strategies"]["claude"]["allocations"][0]["label"], "科技")
        self.assertEqual(result["factor_rows"][0]["key"], "momentum_20")
        self.assertEqual(result["nav_series"][0]["date"], "2026-07-10")
        self.assertAlmostEqual(result["nav_series"][0]["claude"], 0.0)
        self.assertAlmostEqual(result["nav_series"][-1]["codex"], 0.04)

    def test_empty_positions_fall_back_to_planned_buys(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        defensive = _detail(
            "claude",
            [100.0, 100.0, 100.0],
            [0.0, 0.0, 0.0],
            factors={"pe": 1.0},
            orders=[
                {"side": "buy", "code": "A", "exposure_group": "科技", "target_value": 60.0},
                {"side": "sell", "code": "X", "exposure_group": "其他", "target_value": 40.0},
            ],
        )
        trend = _detail(
            "codex",
            [100.0, 100.0, 100.0],
            [0.0, 0.0, 0.0],
            factors={"momentum_20": 1.0},
            orders=[
                {"side": "buy", "code": "A", "exposure_group": "科技", "target_weight": 0.5},
                {"side": "buy", "code": "B", "exposure_group": "金融", "target_weight": 0.5},
            ],
        )

        result = build_strategy_comparison(
            "a_share",
            {"claude": defensive, "codex": trend},
            registry=self.registry,
        )

        self.assertEqual(result["strategies"]["claude"]["holdings_source"], "planned_orders")
        self.assertEqual(result["strategies"]["codex"]["holdings_source"], "planned_orders")
        self.assertAlmostEqual(result["pair"]["position_overlap"], 0.5)
        self.assertEqual(result["strategies"]["claude"]["allocations"][0]["label"], "科技")

    def test_short_series_preserves_unknown_risk_metrics_as_none(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        left = _detail("claude", [100.0], [0.0], factors={"pe": 1.0})
        right = _detail("codex", [100.0], [0.0], factors={"momentum_20": 1.0})

        result = build_strategy_comparison(
            "a_share",
            {"claude": left, "codex": right},
            registry=self.registry,
        )

        for agent in ("claude", "codex"):
            metrics = result["strategies"][agent]["metrics"]
            self.assertIsNone(metrics["annualized_volatility"])
            self.assertIsNone(metrics["sharpe"])
        self.assertIsNone(result["pair"]["return_correlation"])

    def test_qdii_comparison_reports_underlying_index_and_company_overlap(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        defensive = _detail(
            "claude",
            [100.0],
            [0.0],
            factors={"low_volatility_60": 1.0},
            lookthrough={
                "indexes": [{"index_key": "nasdaq_100"}, {"index_key": "sp_500"}],
                "companies": [{"symbol": "NVDA", "weight": 0.2}, {"symbol": "AAPL", "weight": 0.1}],
            },
        )
        trend = _detail(
            "codex",
            [100.0],
            [0.0],
            factors={"momentum_20": 1.0},
            lookthrough={
                "indexes": [{"index_key": "nasdaq_100"}, {"index_key": "hang_seng_tech"}],
                "companies": [{"symbol": "NVDA", "weight": 0.3}, {"symbol": "0700.HK", "weight": 0.2}],
            },
        )

        result = build_strategy_comparison(
            "cn_qdii_etf",
            {"claude": defensive, "codex": trend},
            registry=self.registry,
        )

        self.assertAlmostEqual(result["pair"]["underlying_index_overlap"], 1 / 3)
        self.assertAlmostEqual(result["pair"]["underlying_company_overlap"], 1 / 3)
        self.assertAlmostEqual(result["pair"]["weighted_company_overlap"], 0.2 / 0.6)
        self.assertEqual(result["strategies"]["codex"]["lookthrough"], trend["lookthrough"])

    def test_distinctness_gate_rejects_identical_strategies(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        dates = [f"2026-07-{day:02d}" for day in range(1, 26)]
        totals = [
            100.0,
            101.0,
            100.5,
            102.0,
            101.2,
            103.0,
            102.5,
            104.0,
            103.2,
            105.0,
            104.6,
            106.0,
            105.2,
            107.0,
            106.4,
            108.0,
            107.1,
            109.0,
            108.3,
            110.0,
            109.2,
            111.0,
            110.1,
            112.0,
            111.0,
        ]
        decisions = [
            {"decision_date": date, "code": "A", "side": "buy", "target_value": 100.0}
            for date in dates[5:10]
        ]
        trades = [
            {
                "trade_date": dates[5],
                "code": "A",
                "side": "buy",
                "gross_amount": 50.0,
            }
        ]
        common = {
            "totals": totals,
            "benchmark_returns": [0.0] * len(dates),
            "factors": {"pe": 0.7, "roe": 0.3},
            "positions": [
                {"code": "A", "market_value": 70.0},
                {"code": "B", "market_value": 30.0},
            ],
            "orders": decisions,
            "trades": trades,
            "dates": dates,
        }

        result = build_strategy_comparison(
            "a_share",
            {
                "claude": _detail("claude", **common),
                "codex": _detail("codex", **common),
            },
            registry={**self.registry, "effective_date": dates[0]},
        )

        distinctness = result["pair"]["distinctness"]
        self.assertEqual(distinctness["status"], "breached")
        self.assertFalse(distinctness["qualified"])
        self.assertAlmostEqual(distinctness["distinctness_score"], 0.0)
        self.assertAlmostEqual(distinctness["weighted_position_overlap"], 1.0)
        self.assertAlmostEqual(distinctness["return_correlation"], 1.0)
        self.assertAlmostEqual(distinctness["daily_decision_agreement"], 1.0)
        self.assertAlmostEqual(distinctness["factor_exposure_distance"], 0.0)
        self.assertAlmostEqual(distinctness["turnover_style_distance"], 0.0)
        self.assertEqual(
            {item["metric"] for item in distinctness["breaches"]},
            {
                "weighted_position_overlap",
                "return_correlation",
                "daily_decision_agreement",
                "factor_exposure_distance",
                "turnover_style_distance",
            },
        )

    def test_distinctness_gate_accepts_materially_different_strategies(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        dates = [f"2026-07-{day:02d}" for day in range(1, 26)]
        left_returns = [
            0.010,
            -0.004,
            0.008,
            -0.003,
            0.012,
            -0.006,
            0.007,
            -0.002,
            0.009,
            -0.005,
            0.011,
            -0.007,
            0.006,
            -0.001,
            0.013,
            -0.008,
            0.005,
            -0.004,
            0.010,
            -0.006,
            0.008,
            -0.003,
            0.012,
            -0.005,
        ]

        def totals_from_returns(returns: list[float]) -> list[float]:
            values = [100.0]
            for daily_return in returns:
                values.append(values[-1] * (1.0 + daily_return))
            return values

        left_orders = [
            {"decision_date": date, "code": "A", "side": "buy", "target_value": 100.0}
            for date in dates[5:10]
        ]
        right_orders = [
            {"decision_date": date, "code": "Z", "side": "sell", "target_value": 100.0}
            for date in dates[5:10]
        ]
        defensive = _detail(
            "claude",
            totals_from_returns(left_returns),
            [0.0] * len(dates),
            factors={"pe": 1.0},
            positions=[{"code": "A", "market_value": 100.0}],
            orders=left_orders,
            trades=[],
            dates=dates,
        )
        trend = _detail(
            "codex",
            totals_from_returns([-value for value in left_returns]),
            [0.0] * len(dates),
            factors={"momentum_20": 1.0},
            positions=[{"code": "Z", "market_value": 100.0}],
            orders=right_orders,
            trades=[
                {
                    "trade_date": dates[5],
                    "code": "Z",
                    "side": "sell",
                    "gross_amount": 200.0,
                }
            ],
            dates=dates,
        )

        result = build_strategy_comparison(
            "a_share",
            {"claude": defensive, "codex": trend},
            registry={**self.registry, "effective_date": dates[0]},
        )

        distinctness = result["pair"]["distinctness"]
        self.assertEqual(distinctness["status"], "qualified")
        self.assertTrue(distinctness["qualified"])
        self.assertAlmostEqual(distinctness["distinctness_score"], 1.0)
        self.assertEqual(distinctness["breaches"], [])
        self.assertEqual(distinctness["sample_sizes"]["return_observations"], 24)
        self.assertEqual(distinctness["sample_sizes"]["decision_days"], 5)
        self.assertEqual(
            distinctness["thresholds"]["min_factor_exposure_distance"],
            self.registry["factor_distance_floor"],
        )

    def test_distinctness_gate_fails_closed_for_short_samples(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        left = _detail(
            "claude",
            [100.0, 101.0, 102.0],
            [0.0, 0.0, 0.0],
            factors={"pe": 1.0},
            positions=[{"code": "A", "market_value": 100.0}],
            orders=[{"decision_date": "2026-07-13", "code": "A", "side": "buy"}],
        )
        right = _detail(
            "codex",
            [100.0, 99.0, 98.0],
            [0.0, 0.0, 0.0],
            factors={"momentum_20": 1.0},
            positions=[{"code": "Z", "market_value": 100.0}],
            orders=[{"decision_date": "2026-07-13", "code": "Z", "side": "sell"}],
        )

        result = build_strategy_comparison(
            "a_share",
            {"claude": left, "codex": right},
            registry=self.registry,
        )

        distinctness = result["pair"]["distinctness"]
        self.assertEqual(distinctness["status"], "insufficient_samples")
        self.assertFalse(distinctness["qualified"])
        self.assertIsNone(distinctness["distinctness_score"])
        self.assertEqual(distinctness["sample_sizes"]["return_observations"], 2)
        self.assertEqual(distinctness["sample_sizes"]["decision_days"], 1)
        self.assertIn(
            "return_observations",
            {item["metric"] for item in distinctness["breaches"]},
        )
        self.assertIn(
            "decision_days",
            {item["metric"] for item in distinctness["breaches"]},
        )

    def test_distinctness_gate_handles_missing_optional_fields(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        result = build_strategy_comparison(
            "a_share",
            {
                "claude": {"nav": {"series": []}},
                "codex": {"nav": {"series": []}},
            },
            registry=self.registry,
        )

        distinctness = result["pair"]["distinctness"]
        self.assertEqual(distinctness["status"], "insufficient_samples")
        self.assertFalse(distinctness["qualified"])
        self.assertIsNone(distinctness["distinctness_score"])
        self.assertIsNone(distinctness["weighted_position_overlap"])
        self.assertIsNone(distinctness["factor_exposure_distance"])
        self.assertIn(
            "weighted_position_overlap",
            {item["metric"] for item in distinctness["breaches"]},
        )
        self.assertIn(
            "factor_exposure_distance",
            {item["metric"] for item in distinctness["breaches"]},
        )

    def test_distinctness_payload_is_deterministic(self) -> None:
        from stock_analyze.strategy_comparison import build_strategy_comparison

        details = {
            "claude": _detail(
                "claude",
                [100.0, 101.0, 102.0],
                [0.0, 0.0, 0.0],
                factors={"pe": 1.0},
                positions=[{"code": "A", "market_value": 100.0}],
            ),
            "codex": _detail(
                "codex",
                [100.0, 99.0, 98.0],
                [0.0, 0.0, 0.0],
                factors={"momentum_20": 1.0},
                positions=[{"code": "Z", "market_value": 100.0}],
            ),
        }

        first = build_strategy_comparison("a_share", details, registry=self.registry)
        second = build_strategy_comparison("a_share", details, registry=self.registry)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
