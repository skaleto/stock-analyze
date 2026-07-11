from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.strategy_registry import (
    StrategyPairInvalid,
    factor_weight_distance,
    load_strategy_registry,
    strategy_display_name,
    validate_strategy_pair,
)


class StrategyRegistryTests(unittest.TestCase):
    def test_repository_registry_names_the_two_strategy_slots(self) -> None:
        registry = load_strategy_registry()

        self.assertEqual(registry["season_id"], "dual_strategy_2026_s1")
        self.assertEqual(registry["effective_date"], "2026-07-11")
        self.assertEqual(registry["factor_distance_floor"], 0.45)
        self.assertEqual(strategy_display_name("claude"), "稳健防守")
        self.assertEqual(strategy_display_name("codex"), "趋势进攻")

    def test_factor_weight_distance_normalizes_each_overlay(self) -> None:
        defensive = {"factors": {"pe": {"weight": 2.0}}}
        trend = {"factors": {"momentum_20": {"weight": 4.0}}}

        self.assertEqual(factor_weight_distance(defensive, trend), 1.0)

    def test_pair_validation_returns_distance_and_strategy_ids(self) -> None:
        defensive = {
            "agent_id": "claude",
            "strategy_id": "defensive_v1",
            "name": "稳健防守 · 价值质量",
            "factors": {
                "pe": {"weight": 0.5},
                "roe": {"weight": 0.5},
            },
        }
        trend = {
            "agent_id": "codex",
            "strategy_id": "trend_v1",
            "name": "趋势进攻 · 动量成长",
            "factors": {
                "momentum_20": {"weight": 0.6},
                "roe": {"weight": 0.4},
            },
        }

        result = validate_strategy_pair(
            {"claude": defensive, "codex": trend},
            factor_distance_floor=0.45,
        )

        self.assertAlmostEqual(result["factor_distance"], 0.6)
        self.assertEqual(
            result["strategy_ids"],
            {"claude": "defensive_v1", "codex": "trend_v1"},
        )

    def test_pair_validation_rejects_weight_sum_and_near_duplicate_pair(self) -> None:
        bad_sum = {
            "agent_id": "claude",
            "strategy_id": "defensive_v1",
            "name": "稳健防守",
            "factors": {"pe": {"weight": 0.8}},
        }
        valid = {
            "agent_id": "codex",
            "strategy_id": "trend_v1",
            "name": "趋势进攻",
            "factors": {"pe": {"weight": 1.0}},
        }
        with self.assertRaisesRegex(StrategyPairInvalid, "weight_sum"):
            validate_strategy_pair(
                {"claude": bad_sum, "codex": valid},
                factor_distance_floor=0.45,
            )

        first = {
            **bad_sum,
            "factors": {
                "pe": {"weight": 0.5},
                "roe": {"weight": 0.5},
            },
        }
        second = {
            **valid,
            "factors": {
                "pe": {"weight": 0.45},
                "roe": {"weight": 0.55},
            },
        }
        with self.assertRaisesRegex(StrategyPairInvalid, "factor_distance"):
            validate_strategy_pair(
                {"claude": first, "codex": second},
                factor_distance_floor=0.45,
            )

    def test_registry_can_be_loaded_from_an_explicit_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "configs" / "strategy_competition.json").write_text(
                json.dumps(
                    {
                        "season_id": "test",
                        "name": "测试赛季",
                        "effective_date": "2026-01-02",
                        "factor_distance_floor": 0.5,
                        "slots": {
                            "claude": {"label": "甲", "description": "A", "color": "#111111"},
                            "codex": {"label": "乙", "description": "B", "color": "#222222"},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            registry = load_strategy_registry(root)

        self.assertEqual(registry["slots"]["claude"]["label"], "甲")


if __name__ == "__main__":
    unittest.main()
