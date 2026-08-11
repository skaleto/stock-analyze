import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.tabular_forward import (
    freeze_tabular_forward_model,
    observe_tabular_forward_model,
)
from stock_analyze.research.pipeline import ResearchPipeline
from stock_analyze.research.tabular_ranker import load_tabular_ranker_config
from stock_analyze.research.tabular_ranker import _config_hash


def _config() -> dict:
    config = load_tabular_ranker_config("configs/research/classical_model.yaml")
    config["development"] = {
        "start": "20220103",
        "end": "20221104",
        "observed_final_start": "20221107",
        "observed_final_end": "20221230",
        "observed_final_status": "diagnostic_only_already_observed",
    }
    config["training"].update({
        "training_window_sessions": 180,
        "calibration_fraction": 0.20,
        "embargo_sessions": 5,
        "max_fit_rows": 100_000,
        "minimum_feature_coverage": 0.50,
    })
    config["model"]["target"] = "daily_cross_sectional_percentile_v1"
    config["model"]["parameters"].update({
        "n_estimators": 30,
        "learning_rate": 0.08,
        "num_leaves": 7,
        "max_depth": 3,
        "min_child_samples": 10,
        "early_stopping_rounds": 5,
        "num_threads": 1,
    })
    config["score_construction"] = {
        "core": "account_low_volatility_percentile",
        "core_weight": 0.80,
        "model_weight": 0.20,
        "neutralize_score": False,
    }
    return config


def _source_report(config: dict) -> dict:
    return {"config_hash": _config_hash(config)}


def _development_panel() -> pd.DataFrame:
    rng = np.random.default_rng(20260811)
    rows: list[dict] = []
    dates = pd.date_range("2022-01-03", periods=220, freq="B")
    for date_index, trade_date in enumerate(dates):
        for code_index in range(20):
            signal = (code_index - 9.5) / 9.5
            rows.append({
                "trade_date": trade_date.strftime("%Y%m%d"),
                "label_end_date": (trade_date + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                "code": f"{code_index + 1:06d}",
                "account_id": "zz500",
                "research_scope": "zz500",
                "horizon": 20,
                "signal": signal,
                "noise": rng.normal(),
                "excess_return": 0.01 * signal + rng.normal(scale=0.002),
                "account_low_volatility_percentile": 1.0 - code_index / 20.0,
                "industry": "科技" if code_index % 2 else "制造",
                "benchmark_weight": 1.0 / 20.0,
                "realized_volatility_20": 0.18 + code_index / 1000.0,
                "avg_amount_20": 200_000_000.0,
                "return_1": rng.normal(scale=0.01),
            })
    return pd.DataFrame(rows)


def _forward_day(day: str, day_number: int) -> pd.DataFrame:
    rows = []
    for code_index in range(20):
        signal = (code_index - 9.5) / 9.5
        base = 10.0 + code_index * 0.2 + day_number * 0.03
        rows.append({
            "trade_date": day,
            "code": f"{code_index + 1:06d}",
            "name": f"样本{code_index + 1}",
            "account_id": "zz500",
            "research_scope": "zz500",
            "benchmark_code": "000905",
            "signal": signal,
            "noise": 0.0,
            "account_low_volatility_percentile": 1.0 - code_index / 20.0,
            "industry": "科技" if code_index % 2 else "制造",
            "benchmark_weight": 1.0 / 20.0,
            "realized_volatility_20": 0.18 + code_index / 1000.0,
            "avg_amount_20": 200_000_000.0,
            "return_1": 0.001 * signal,
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base * (1.0 + 0.001 * signal),
            "volume": 1_000_000.0,
        })
    return pd.DataFrame(rows)


class ResearchTabularForwardTest(unittest.TestCase):
    def test_adjustment_join_uses_latest_point_in_time_value_for_new_scope_member(self):
        features = pd.DataFrame([
            {"code": "000009", "trade_date": "20260810", "close": 12.0},
            {"code": "600000", "trade_date": "20260810", "close": 10.0},
        ])
        adjustments = pd.DataFrame([
            {"code": "000009", "trade_date": "20260807", "adj_factor": 1.25},
            {"code": "600000", "trade_date": "20260810", "adj_factor": 2.0},
        ])
        features["trade_date"] = features["trade_date"].astype("string")

        merged = ResearchPipeline._merge_forward_adjustments(
            features,
            adjustments,
        ).set_index("code")

        self.assertEqual(merged.loc["000009", "adj_factor"], 1.25)
        self.assertEqual(merged.loc["600000", "adj_factor"], 2.0)

    def test_freeze_writes_portable_research_only_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config()
            result = freeze_tabular_forward_model(
                tmp,
                dataset=_development_panel(),
                feature_columns=("signal", "noise"),
                config=config,
                observation_start="20230102",
                source_report=_source_report(config),
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text())
            current = json.loads(
                (Path(result["model_root"]).parent / "current.json").read_text()
            )

        self.assertEqual(result["status"], "frozen")
        self.assertEqual(manifest["lifecycle_status"], "forward_observation")
        self.assertFalse(manifest["formal_order_source"])
        self.assertFalse(manifest["registry_mutated"])
        self.assertEqual(manifest["formal_strategy_weight"], 0.0)
        self.assertEqual(manifest["observation_start"], "20230102")
        self.assertTrue(manifest["artifact_sha256"])
        self.assertEqual(manifest["feature_columns"], ["signal", "noise"])
        self.assertEqual(
            current["manifest"],
            f"{manifest['config_hash']}/manifest.json",
        )

    def test_freeze_rejects_source_report_for_another_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                ValueError,
                "tabular_forward_source_config_hash_mismatch",
            ):
                freeze_tabular_forward_model(
                    tmp,
                    dataset=_development_panel(),
                    feature_columns=("signal", "noise"),
                    config=_config(),
                    observation_start="20230102",
                    source_report={"config_hash": "another-candidate"},
                )

    def test_observer_is_idempotent_and_accumulates_future_only_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config()
            frozen = freeze_tabular_forward_model(
                tmp,
                dataset=_development_panel(),
                feature_columns=("signal", "noise"),
                config=config,
                observation_start="20230102",
                source_report=_source_report(config),
            )
            benchmark = pd.DataFrame([
                {"trade_date": "20230103", "open": 100.0},
                {"trade_date": "20230104", "open": 101.0},
                {"trade_date": "20230105", "open": 100.5},
                {"trade_date": "20230106", "open": 102.0},
            ])
            portfolio_contract = {
                "accounts": [{
                    "id": "zz500", "scope": "zz500", "benchmark": "000905",
                    "cash": 500_000, "top_n": 10,
                }],
                "trading": {
                    "lot_size": 100,
                    "commission_rate": 0.0003,
                    "min_commission": 5,
                    "stamp_tax_rate": 0.0005,
                    "slippage_rate": 0.0005,
                    "max_single_weight": 0.10,
                },
                "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
                "rebalance_frequency": "monthly",
                "allocation_policy": {
                    "version": "benchmark-aware-topn-v1",
                    "group_constraints": {"industry": 0.50},
                    "risk_aversion": 1.0,
                    "cost_aversion": 1.0,
                    "max_rebalance_turnover": 0.25,
                },
            }
            labels = pd.DataFrame()
            for day_number, day in enumerate(("20230102", "20230103", "20230104", "20230105")):
                result = observe_tabular_forward_model(
                    tmp,
                    model_root=Path(frozen["model_root"]),
                    featured=_forward_day(day, day_number),
                    labels=labels,
                    benchmark=benchmark,
                    config=config,
                    portfolio_contract=portfolio_contract,
                )
            repeated = observe_tabular_forward_model(
                tmp,
                model_root=Path(frozen["model_root"]),
                featured=_forward_day("20230105", 3),
                labels=labels,
                benchmark=benchmark,
                config=config,
                portfolio_contract=portfolio_contract,
            )

            status = json.loads(Path(result["status_path"]).read_text())
            prediction_files = list(
                (Path(frozen["model_root"]) / "predictions").glob("*.parquet")
            )

        self.assertEqual(result["status"], "observing")
        self.assertEqual(repeated["prediction_write"], "cached")
        self.assertEqual(len(prediction_files), 4)
        self.assertEqual(status["observation_days"], 4)
        self.assertGreaterEqual(status["portfolio"]["periods"], 1)
        self.assertFalse(status["formal_order_source"])
        self.assertEqual(status["formal_strategy_weight"], 0.0)

    def test_observer_backfills_matured_rank_ic_without_retraining(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config()
            frozen = freeze_tabular_forward_model(
                tmp,
                dataset=_development_panel(),
                feature_columns=("signal", "noise"),
                config=config,
                observation_start="20230102",
                source_report=_source_report(config),
            )
            benchmark = pd.DataFrame([
                {"trade_date": "20230103", "open": 100.0},
                {"trade_date": "20230104", "open": 100.5},
            ])
            first = _forward_day("20230102", 0)
            observe_tabular_forward_model(
                tmp,
                model_root=Path(frozen["model_root"]),
                featured=first,
                labels=pd.DataFrame(),
                benchmark=benchmark,
                config=config,
                portfolio_contract={
                    "accounts": [{"id": "zz500", "cash": 500_000, "top_n": 10}],
                    "trading": {"lot_size": 100, "max_single_weight": 0.10},
                    "rebalance_frequency": "monthly",
                },
            )
            labels = first.loc[:, ["code", "trade_date"]].copy()
            labels["horizon"] = 20
            labels["label_end_date"] = "20230131"
            labels["excess_return"] = first["signal"] * 0.02
            result = observe_tabular_forward_model(
                tmp,
                model_root=Path(frozen["model_root"]),
                featured=_forward_day("20230103", 1),
                labels=labels,
                benchmark=benchmark,
                config=config,
                portfolio_contract={
                    "accounts": [{"id": "zz500", "cash": 500_000, "top_n": 10}],
                    "trading": {"lot_size": 100, "max_single_weight": 0.10},
                    "rebalance_frequency": "monthly",
                },
            )

            manifest = json.loads(Path(frozen["manifest_path"]).read_text())

        self.assertEqual(result["matured_evidence"]["matured_days"], 1)
        self.assertEqual(result["matured_evidence"]["matured_rows"], 20)
        self.assertGreater(result["matured_evidence"]["raw_rank_ic"], 0.0)
        self.assertEqual(
            result["model_artifact_sha256"], manifest["artifact_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
