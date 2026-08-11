import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.classical_specs import ClassicalModelSpec
from stock_analyze.research.cross_sectional_candidate import (
    evaluate_cross_sectional_candidate,
    evaluate_ridge_target,
)


def candidate_spec() -> ClassicalModelSpec:
    return ClassicalModelSpec(
        spec_id="cross_sectional_fixture",
        market="a_share",
        account_scope="zz500",
        horizon=5,
        estimator="ridge",
        feature_profile="fixture",
        parameters=(("alpha", "25.0"), ("ranking_linear_weight", "1.0")),
        ranking_target="daily_cross_sectional_percentile_v1",
        feature_selection_mode="fixed_profile_v1",
        rebalance_frequency="monthly",
    )


def candidate_frame() -> pd.DataFrame:
    rng = np.random.default_rng(20260810)
    dates = pd.date_range("2023-01-02", periods=180, freq="B")
    rows = []
    for date_index, trade_date in enumerate(dates):
        signal = np.linspace(-1.0, 1.0, 12) + rng.normal(scale=0.05, size=12)
        benchmark = 100.0 + date_index * 0.03
        for code_index, value in enumerate(signal):
            entry_price = 10.0 + code_index * 0.25 + date_index * 0.002
            realized = value * 0.006 + rng.normal(scale=0.001)
            rows.append({
                "code": f"{code_index + 1:06d}",
                "account_id": "zz500",
                "research_scope": "zz500",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "entry_date": (trade_date + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                "entry_price": entry_price,
                "benchmark_entry_price": benchmark,
                "label_end_date": (trade_date + pd.offsets.BDay(5)).strftime("%Y%m%d"),
                "horizon": 5,
                "signal": value,
                "noise": rng.normal(),
                "avg_amount_20": 100_000_000.0,
                "realized_volatility_20": 0.20,
                "excess_return": realized,
            })
    return pd.DataFrame(rows)


def portfolio_contract() -> dict:
    return {
        "accounts": [{"id": "zz500", "cash": 500_000.0, "top_n": 4}],
        "trading": {
            "lot_size": 100,
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_tax_rate": 0.0005,
            "slippage_rate": 0.0005,
            "max_single_weight": 0.25,
        },
    }


class CrossSectionalCandidateTest(unittest.TestCase):
    def test_ridge_target_produces_purged_oos_exact_cost_evidence(self):
        result = evaluate_ridge_target(
            candidate_frame(),
            feature_columns=("signal", "noise"),
            target_contract="daily_cross_sectional_percentile_v1",
            horizon=5,
            ridge_alpha=25.0,
            portfolio_contract=portfolio_contract(),
        )

        self.assertGreater(result["rank_ic"], 0.90)
        self.assertGreater(result["trade_count"], 0)
        self.assertGreater(result["capital_utilization"], 0.85)
        self.assertEqual(result["simulator_version"], "paper-parity-daily-v1")
        self.assertEqual(result["evidence_scope"], "development_only")
        self.assertFalse(result["formal_order_source"])
        self.assertEqual(len(result["subperiods"]), 3)
        self.assertEqual(len(result["score_bucket_returns"]), 5)
        self.assertEqual(set(result["mean_standardized_coefficients"]), {"signal", "noise"})
        self.assertNotIn("portfolio_nav", result)

    def test_candidate_evaluation_never_passes_rows_after_development_end(self):
        observed_max_dates = []

        def fake_target(frame, **kwargs):
            observed_max_dates.append(str(frame["trade_date"].max()))
            target = kwargs["target_contract"]
            return {
                "target_contract": target,
                "point_in_time_audit": True,
                "rank_ic": 0.05 if target.startswith("daily_") else -0.01,
                "icir": 0.40 if target.startswith("daily_") else -0.10,
                "net_excess_return": 0.04 if target.startswith("daily_") else -0.02,
                "cumulative_relative_wealth": 0.05 if target.startswith("daily_") else -0.03,
                "max_drawdown": 0.12,
                "annual_turnover": 4.0,
                "capital_utilization": 0.92,
                "trade_count": 20,
                "simulator_version": "paper-parity-daily-v1",
                "selected_features": ["signal", "noise"],
                "evidence_scope": "development_only",
                "formal_order_source": False,
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.cross_sectional_candidate.evaluate_ridge_target",
            side_effect=fake_target,
        ):
            result = evaluate_cross_sectional_candidate(
                tmp,
                market="a_share",
                account_scope="zz500",
                as_of="20260807",
                dataset=candidate_frame(),
                feature_columns=("signal", "noise"),
                portfolio_contract=portfolio_contract(),
                model_spec=candidate_spec(),
                development_start="20230102",
                development_end="20230531",
                observed_final_start="20230601",
                observed_final_end="20230908",
            )

            report_path = Path(result["report_path"])
            json_path = Path(result["json_path"])

            self.assertTrue(report_path.exists())
            self.assertTrue(json_path.exists())
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(observed_max_dates, ["20230531", "20230531"])
        self.assertEqual(result["status"], "development_pass")
        self.assertEqual(
            result["observed_final_status"],
            "diagnostic_only_already_observed",
        )
        self.assertFalse(result["registry_mutated"])
        self.assertIn("## 分阶段稳定性", report)
        self.assertIn("## 排名五档", report)
        self.assertIn("20230102", report)


if __name__ == "__main__":
    unittest.main()
