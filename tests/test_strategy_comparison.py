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
) -> dict:
    dates = ["2026-07-10", "2026-07-13", "2026-07-14"]
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


if __name__ == "__main__":
    unittest.main()
