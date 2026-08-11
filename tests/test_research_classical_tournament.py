from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.classical_specs import a_share_h3_specs
from stock_analyze.research.classical_tournament import (
    _baseline_trials,
    run_classical_tournament,
)


class _FakeBundle:
    def __init__(self, spec) -> None:
        self.model_version = f"{spec.spec_id}-v1"
        self.feature_columns = ("signal",)
        self.edge_calibrator = SimpleNamespace(available=True)
        self.metrics = {
            "feature_coverage": 1.0,
            "point_in_time_audit": True,
            "brier_improvement": 0.06,
            "hit_rate_uplift": 0.06,
            "auc": 0.58,
            "ablation_stability": 0.8,
            "seed_rank_ic_std": 0.01,
            "subperiod_stability": 0.8,
            "feature_selection_stability": 0.8,
            "unbiased_universe": True,
            "selected_features": ["signal"],
            "model_spec_id": spec.spec_id,
            "model_spec_hash": spec.spec_hash,
            "edge_calibration_available": True,
        }

    def predict_excess_return(self, frame: pd.DataFrame) -> np.ndarray:
        return -pd.to_numeric(frame["signal"], errors="coerce").to_numpy() * 0.01

    def predict_ranking_score(self, frame: pd.DataFrame) -> np.ndarray:
        return pd.to_numeric(frame["signal"], errors="coerce").to_numpy()

    def predict_excess_uncertainty(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), 0.0001)


def _dataset() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2025-01-02", periods=150, freq="B")
    for day_index, trade_date in enumerate(dates):
        for code_index in range(6):
            signal = float(6 - code_index)
            rows.append({
                "code": f"{code_index + 1:06d}",
                "account_id": "hs300",
                "research_scope": "hs300",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                "entry_price": 10.0 + code_index + day_index * 0.01,
                "benchmark_entry_price": 100.0 + day_index * 0.02,
                "label_end_date": (trade_date + pd.offsets.BDay(3)).strftime("%Y%m%d"),
                "horizon": 3,
                "label": "up" if code_index < 2 else "flat" if code_index < 4 else "down",
                "excess_return": 0.002 * signal,
                "signal": signal,
                "avg_amount_20": 50_000_000.0,
                "realized_volatility_20": 0.20,
            })
    return pd.DataFrame(rows)


class ResearchClassicalTournamentTest(unittest.TestCase):
    def test_cash_baseline_earns_declared_rate_and_is_measured_against_benchmark(self) -> None:
        dates = ("20260102", "20260105")
        final_frame = pd.DataFrame({
            "trade_date": [*dates, "20260106"],
            "benchmark_entry_price": [100.0, 100.1, 100.2001],
        })

        baselines = _baseline_trials(
            final_frame,
            portfolio_contract={
                "performance": {
                    "risk_free_rate": 0.05,
                    "trading_days_per_year": 252,
                },
            },
            reference_dates=dates,
        )
        cash = next(item for item in baselines if item["spec_id"] == "baseline_cash")
        daily_rf = (1.05 ** (1.0 / 252.0)) - 1.0
        expected_relative = ((1.0 + daily_rf) ** 2 / (1.001 ** 2)) - 1.0
        expected_annualized = (1.0 + expected_relative) ** (252.0 / 2.0) - 1.0

        self.assertNotEqual(cash["net_excess_return"], 0.0)
        self.assertAlmostEqual(cash["oos_returns"][0]["return"], daily_rf - 0.001)
        self.assertAlmostEqual(cash["net_excess_return"], expected_annualized)

    def test_tournament_opens_final_gate_once_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trained = []
            specs = (a_share_h3_specs("hs300")[0],)

            def trainer(*_args, model_spec, **_kwargs):
                trained.append(model_spec.spec_id)
                return _FakeBundle(model_spec)

            def writer(_bundle, path):
                destination = Path(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("fixture", encoding="utf-8")
                return destination

            with (
                patch("stock_analyze.research.classical_tournament.train_model_bundle", side_effect=trainer),
                patch("stock_analyze.research.classical_tournament.save_model_bundle", side_effect=writer),
            ):
                first = run_classical_tournament(
                    root,
                    market="a_share",
                    account_scope="hs300",
                    horizon=3,
                    as_of="2026-08-07",
                    dataset=_dataset(),
                    feature_columns=("signal",),
                    portfolio_contract={
                        "accounts": [{"id": "hs300", "cash": 200_000.0, "top_n": 3}],
                        "trading": {
                            "lot_size": 100,
                            "commission_rate": 0.0003,
                            "min_commission": 5.0,
                            "stamp_tax_rate": 0.0005,
                            "slippage_rate": 0.0005,
                            "max_single_weight": 0.20,
                        },
                        "execution_policy": {
                            "rank_buffer_pct": 0.50,
                            "minimum_target_change": 0.01,
                            "partial_adjustment_rate": 0.35,
                            "max_daily_turnover": 0.10,
                            "cost_safety_multiple": 1.50,
                        },
                    },
                    specs=specs,
                )
                second = run_classical_tournament(
                    root,
                    market="a_share",
                    account_scope="hs300",
                    horizon=3,
                    as_of="2026-08-07",
                    dataset=_dataset(),
                    feature_columns=("signal",),
                    portfolio_contract={},
                    specs=specs,
                )

            manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual(len(trained), 1)
        self.assertEqual(manifest["final_gate_open_count"], 1)
        self.assertEqual(first["report_path"], second["report_path"])
        self.assertEqual(
            {item["status"] for item in first["candidates"]}.difference({"shadow", "rejected"}),
            set(),
        )
        evidenced_candidates = [
            item for item in first["candidates"] if "development_selection" in item
        ]
        self.assertTrue(evidenced_candidates)
        candidate = evidenced_candidates[0]
        self.assertEqual(first["evidence_contract_version"], "windowed-evidence-v1")
        self.assertEqual(
            candidate["development_selection"]["window"],
            first["development_window"],
        )
        self.assertEqual(
            candidate["sealed_final_evaluation"]["window"],
            first["final_window"],
        )
        self.assertEqual(
            candidate["activation_evidence"]["metric_sources"]["rank_ic"],
            "sealed_final_evaluation",
        )
        self.assertEqual(
            candidate["activation_evidence"]["metric_sources"]["brier_improvement"],
            "development_selection",
        )
        self.assertEqual(
            candidate["diagnostic_rank_evaluation"]["window"],
            first["final_window"],
        )
        self.assertEqual(
            candidate["diagnostic_rank_evaluation"]["metrics"]["evaluation_contract"],
            "diagnostic_rank_only-v1",
        )
        self.assertGreater(
            candidate["diagnostic_rank_evaluation"]["metrics"]["trade_count"],
            0,
        )
        self.assertEqual(
            candidate["activation_evidence"]["metric_sources"][
                "diagnostic_net_excess_return"
            ],
            "diagnostic_rank_evaluation",
        )
        self.assertLess(
            candidate["development_selection"]["window"][1],
            candidate["sealed_final_evaluation"]["window"][0],
        )


if __name__ == "__main__":
    unittest.main()
