from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from stock_analyze.research.scenario_model import (
    ABLATIONS,
    SCENES,
    classify_scenes,
    evaluate_scope,
    load_scenario_contract,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "configs/research/scenario_model_v1.yaml"


class ScenarioModelContractTest(unittest.TestCase):
    def test_frozen_contract_has_four_scopes_and_forbids_event_features(self):
        contract = load_scenario_contract(CONTRACT_PATH)

        self.assertEqual(
            contract["contract_sha256"],
            "f7cb2d64bba38b50caf4cbd99ead0ba8ac5b08370b40920d921dc96d3c4c4b64",
        )
        self.assertEqual(
            tuple(contract["scopes"]),
            ("hs300", "zz500", "hk_exposure", "us_exposure"),
        )
        self.assertEqual(tuple(contract["router"]["scenes"]), SCENES)
        self.assertEqual(tuple(contract["model"]["ablations"]), ABLATIONS)
        self.assertFalse(contract["open_historical_test"])

        forbidden = tuple(contract["forbidden_feature_prefixes"])
        for scope in contract["scopes"].values():
            self.assertEqual(len(scope["features"]), 8)
            self.assertFalse(
                any(feature.startswith(forbidden) for feature in scope["features"])
            )

    def test_contract_rejects_an_event_feature_before_training(self):
        payload = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        payload["scopes"]["hs300"]["features"][-1] = (
            "event_positive_decay_5d"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.yaml"
            path.write_text(
                yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError, "scenario_model_forbidden_feature"
            ):
                load_scenario_contract(path)


class ScenarioRouterTest(unittest.TestCase):
    @staticmethod
    def _frame(last_volatility: float) -> pd.DataFrame:
        rows = []
        dates = pd.date_range("2020-01-01", periods=80, freq="B")
        for index, day in enumerate(dates):
            for code_index in range(4):
                rows.append({
                    "trade_date": day.strftime("%Y%m%d"),
                    "code": f"{code_index + 1:06d}",
                    "momentum_60": 0.10,
                    "sma_distance_200": 0.10,
                    "realized_volatility_20": (
                        last_volatility if index == len(dates) - 1 else 0.10
                    ),
                })
        return pd.DataFrame(rows)

    def test_scene_boundary_uses_prior_dates_only(self):
        router = {
            "trailing_sessions": 252,
            "minimum_history_sessions": 60,
            "high_volatility_quantile": 0.70,
            "expansion_breadth_floor": 0.55,
            "stress_breadth_ceiling": 0.45,
        }
        normal = classify_scenes(self._frame(0.10), router)
        shocked = classify_scenes(self._frame(10.0), router)
        last = normal["trade_date"].max()

        normal_boundary = normal.loc[
            normal["trade_date"].eq(last), "scene_volatility_boundary"
        ].iloc[0]
        shocked_boundary = shocked.loc[
            shocked["trade_date"].eq(last), "scene_volatility_boundary"
        ].iloc[0]
        self.assertAlmostEqual(normal_boundary, shocked_boundary)
        self.assertAlmostEqual(normal_boundary, 0.10)
        self.assertEqual(
            set(normal.loc[normal["trade_date"].eq(last), "scene"]),
            {"expansion"},
        )
        self.assertEqual(
            set(shocked.loc[shocked["trade_date"].eq(last), "scene"]),
            {"range"},
        )


class ScenarioModelEvaluationTest(unittest.TestCase):
    @staticmethod
    def _dataset() -> pd.DataFrame:
        rows = []
        rng = np.random.default_rng(19)
        for year in range(2018, 2025):
            dates = pd.date_range(f"{year}-01-02", periods=180, freq="B")
            for date_index, day in enumerate(dates):
                phase = (date_index // 20) % 3
                if phase == 0:
                    scene_momentum, scene_distance, volatility = 0.12, 0.10, 0.10
                elif phase == 1:
                    scene_momentum, scene_distance, volatility = 0.08, -0.04, 0.12
                else:
                    scene_momentum, scene_distance, volatility = -0.12, -0.10, 0.24
                for code_index in range(6):
                    cross = (code_index - 2.5) / 2.5
                    momentum_20 = scene_momentum + cross * 0.02
                    momentum_60 = scene_momentum + cross * 0.03
                    momentum_120 = scene_momentum + cross * 0.04
                    rows.append({
                        "code": f"{code_index + 1:06d}",
                        "account_id": "hs300",
                        "research_scope": "hs300",
                        "trade_date": day.strftime("%Y%m%d"),
                        "entry_date": (day + pd.offsets.BDay(1)).strftime(
                            "%Y%m%d"
                        ),
                        "label_end_date": (day + pd.offsets.BDay(20)).strftime(
                            "%Y%m%d"
                        ),
                        "entry_price": (
                            10.0 + code_index + (year - 2018) * 0.2
                            + date_index * 0.001
                        ),
                        "benchmark_entry_price": (
                            100.0 + (year - 2018) + date_index * 0.001
                        ),
                        "horizon": 20,
                        "momentum_20": momentum_20,
                        "momentum_60": momentum_60,
                        "momentum_120": momentum_120,
                        "sma_distance_200": scene_distance + cross * 0.01,
                        "account_low_volatility_percentile": (
                            1.0 - code_index / 6.0
                        ),
                        "account_liquidity_percentile": (code_index + 1) / 6.0,
                        "account_quality_percentile": 0.5 + cross * 0.10,
                        "realized_volatility_20": volatility + code_index * 0.001,
                        "excess_return": (
                            cross
                            * (
                                0.004 if phase == 0
                                else -0.002 if phase == 2 else 0.001
                            )
                            + rng.normal(0.0, 0.0001)
                        ),
                    })
        return pd.DataFrame(rows)

    def test_four_ablations_use_same_folds_and_never_mutate_registry(self):
        contract = copy.deepcopy(load_scenario_contract(CONTRACT_PATH))
        contract["minimum_scene_training_dates"] = 40
        result = evaluate_scope(
            self._dataset(),
            contract=contract,
            scope="hs300",
            portfolio_contract={
                "accounts": [
                    {"id": "hs300", "cash": 500_000.0, "top_n": 3}
                ],
                "trading": {
                    "lot_size": 100,
                    "commission_rate": 0.0003,
                    "min_commission": 5.0,
                    "stamp_tax_rate": 0.0005,
                    "slippage_rate": 0.0005,
                    "max_single_weight": 0.34,
                },
            },
        )

        self.assertEqual(set(result["metrics"]), set(ABLATIONS))
        self.assertEqual(len(result["folds"]), 4)
        self.assertTrue(
            all(fold["point_in_time_audit"] for fold in result["folds"])
        )
        self.assertEqual(set(result["scene_shares"]), set(SCENES))
        self.assertFalse(result["formal_strategy_activated"])
        self.assertFalse(result["registry_mutated"])
        for ablation in ABLATIONS:
            self.assertEqual(len(result["fold_metrics"][ablation]), 4)


if __name__ == "__main__":
    unittest.main()
