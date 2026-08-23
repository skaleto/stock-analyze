from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.a_share_all_cap_contract import load_all_cap_contract
from stock_analyze.research.a_share_all_cap_evaluation import (
    aggregate_all_cap_metrics,
    capacity_metrics,
    evaluate_all_cap_gate,
    registered_governance_metrics,
    summarize_sleeve_evidence,
)
from stock_analyze.research.governance import TrialRegistry
from stock_analyze.research.portfolio_replay import PortfolioReplayResult


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "configs/research/a_share_all_cap_v2.yaml"


def _passing_evidence(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "evidence_scope": "development_only",
        "oos_start": "20180102",
        "oos_end": "20241231",
        "critical_membership_coverage": 1.0,
        "daily_bar_coverage": 0.995,
        "daily_basic_coverage": 0.995,
        "adjustment_coverage": 0.99,
        "core_factor_daily_coverage": 0.90,
        "checksum_valid": True,
        "unbiased_universe": True,
        "point_in_time_audit": True,
        "simulator_version": "paper-parity-daily-v1",
        "gross_return": 0.08,
        "net_return": 0.06,
        "benchmark_return": 0.03,
        "net_excess_return": 0.03,
        "single_cost_net_excess_return": 0.03,
        "double_cost_net_excess_return": 0.01,
        "rank_ic": 0.03,
        "icir": 0.40,
        "oos_folds": 4,
        "positive_oos_folds": 3,
        "oos_dates": 300,
        "completed_trades": 120,
        "max_drawdown": 0.12,
        "benchmark_max_drawdown": 0.12,
        "benchmark_drawdown_multiple": 1.0,
        "annual_turnover": 3.0,
        "target_fill_rate": 0.98,
        "cost_attribution": {
            "commission": 10.0,
            "stamp_tax": 5.0,
            "slippage": 8.0,
            "impact": 2.0,
            "total": 25.0,
        },
        "attribution_status": "reconciled",
        "deflated_sharpe_probability": 0.97,
        "probability_of_backtest_overfit": 0.30,
        "pbo_trial_count": 6,
        "positive_calendar_years": 5,
        "single_year_positive_excess_share": 0.40,
        "orders_within_base_adv": 0.995,
        "orders_within_hard_adv": 1.0,
        "participation_rate_p50": 0.005,
        "participation_rate_p90": 0.01,
        "participation_rate_p95": 0.015,
        "participation_rate_p99": 0.019,
        "maximum_order_adv_fraction": 0.02,
        "maximum_liquidation_days": 4.0,
        "liquidation_days": {
            "normal": 2.0,
            "half_volume": 4.0,
            "consecutive_limit_down": 4.0,
        },
    }
    values.update(overrides)
    return values


def _passing_by_sleeve() -> dict[str, dict[str, object]]:
    return {
        sleeve: _passing_evidence()
        for sleeve in ("large", "mid", "small", "micro")
    }


def _passing_data_evidence() -> dict[str, object]:
    evidence = _passing_evidence()
    keys = {
        "critical_membership_coverage",
        "daily_bar_coverage",
        "daily_basic_coverage",
        "adjustment_coverage",
        "core_factor_daily_coverage",
        "checksum_valid",
        "unbiased_universe",
        "point_in_time_audit",
    }
    return {key: evidence[key] for key in keys}


class AShareAllCapEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_all_cap_contract(CONTRACT_PATH)

    def test_aggregate_cannot_hide_failed_micro_sleeve(self) -> None:
        evidence = _passing_by_sleeve()
        evidence["large"]["net_excess_return"] = 0.50
        evidence["micro"]["double_cost_net_excess_return"] = -0.001

        report = evaluate_all_cap_gate(evidence, self.contract)

        self.assertFalse(report.passed)
        self.assertTrue(report.sleeves["large"].passed)
        self.assertEqual(
            report.sleeves["micro"].reasons,
            ("double_cost_net_excess_return",),
        )
        self.assertEqual(report.aggregate["sleeve_weights"]["micro"], 0.10)

    def test_all_passing_sleeves_require_passing_csi_aggregate(self) -> None:
        evidence = _passing_by_sleeve()

        missing = evaluate_all_cap_gate(evidence, self.contract)
        passing = evaluate_all_cap_gate(
            evidence,
            self.contract,
            aggregate={
                "benchmark": "000985.CSI",
                "annualized_net_excess_return": 0.021,
            },
        )

        self.assertFalse(missing.passed)
        self.assertEqual(
            missing.reasons,
            ("aggregate_annualized_net_excess_return",),
        )
        self.assertTrue(passing.passed)
        self.assertTrue(all(item.passed for item in passing.sleeves.values()))

    def test_aggregate_benchmark_identity_is_fail_closed(self) -> None:
        report = evaluate_all_cap_gate(
            _passing_by_sleeve(),
            self.contract,
            aggregate={
                "benchmark": "000300.SH",
                "annualized_net_excess_return": 0.50,
            },
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.reasons, ("aggregate_benchmark",))

    def test_aggregate_cannot_override_frozen_sleeve_weights(self) -> None:
        report = evaluate_all_cap_gate(
            _passing_by_sleeve(),
            self.contract,
            aggregate={
                "benchmark": "000985.CSI",
                "annualized_net_excess_return": 0.50,
                "sleeve_weights": {
                    "large": 1.0,
                    "mid": 0.0,
                    "small": 0.0,
                    "micro": 0.0,
                },
            },
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.reasons, ("aggregate_sleeve_weights",))

    def test_missing_nan_and_insufficient_sample_fail_closed_in_stable_order(
        self,
    ) -> None:
        evidence = _passing_by_sleeve()
        evidence["small"].pop("rank_ic")
        evidence["small"]["icir"] = math.nan
        evidence["small"]["oos_dates"] = 251

        first = evaluate_all_cap_gate(evidence, self.contract)
        second = evaluate_all_cap_gate(evidence, self.contract)

        self.assertEqual(
            first.sleeves["small"].reasons,
            ("oos_dates", "rank_ic", "icir"),
        )
        self.assertEqual(
            first.sleeves["small"].reasons,
            second.sleeves["small"].reasons,
        )

    def test_pbo_requires_at_least_four_registered_trials(self) -> None:
        evidence = _passing_by_sleeve()
        evidence["small"].pop("pbo_trial_count")

        report = evaluate_all_cap_gate(
            evidence,
            self.contract,
            aggregate={
                "benchmark": "000985.CSI",
                "annualized_net_excess_return": 0.03,
            },
        )

        self.assertEqual(
            report.sleeves["small"].reasons,
            ("pbo_trial_count",),
        )

    def test_rejects_non_development_evidence(self) -> None:
        evidence = _passing_by_sleeve()
        evidence["large"]["oos_end"] = "20250102"

        report = evaluate_all_cap_gate(evidence, self.contract)

        self.assertEqual(
            report.sleeves["large"].reasons,
            ("development_window",),
        )

    def test_capacity_gate_counts_adv_percentiles_and_liquidation_days(
        self,
    ) -> None:
        orders = pd.DataFrame(
            {
                "participation_rate": [0.005, 0.010, 0.020, 0.049],
                "position_notional": [1_000.0, 2_000.0, 4_000.0, 4_000.0],
                "avg_daily_amount": [100_000.0] * 4,
                "consecutive_limit_down_days": [0, 0, 0, 1],
            }
        )

        metrics = capacity_metrics(
            orders,
            base_adv_fraction=0.02,
            hard_adv_fraction=0.05,
        )

        self.assertEqual(metrics["orders_within_base_adv"], 0.75)
        self.assertEqual(metrics["orders_within_hard_adv"], 1.0)
        self.assertAlmostEqual(metrics["participation_rate_p99"], 0.04813)
        self.assertLessEqual(metrics["maximum_liquidation_days"], 5)
        self.assertEqual(
            set(metrics["liquidation_days"]),
            {"normal", "half_volume", "consecutive_limit_down"},
        )
        self.assertEqual(set(metrics["aum_scenarios"]), {"1", "5", "10", "20"})

    def test_missing_liquidation_scenario_fails_closed(self) -> None:
        evidence = _passing_by_sleeve()
        evidence["micro"]["liquidation_days"] = {
            "normal": 2.0,
            "half_volume": 4.0,
        }

        report = evaluate_all_cap_gate(
            evidence,
            self.contract,
            aggregate={
                "benchmark": "000985.CSI",
                "annualized_net_excess_return": 0.03,
            },
        )

        self.assertEqual(report.sleeves["micro"].reasons, ("liquidation_days",))

    def test_incomplete_cost_attribution_fails_closed(self) -> None:
        evidence = _passing_by_sleeve()
        evidence["mid"]["cost_attribution"] = {
            "commission": 10.0,
            "stamp_tax": 5.0,
            "slippage": 8.0,
            "total": 23.0,
        }

        report = evaluate_all_cap_gate(
            evidence,
            self.contract,
            aggregate={
                "benchmark": "000985.CSI",
                "annualized_net_excess_return": 0.03,
            },
        )

        self.assertEqual(report.sleeves["mid"].reasons, ("cost_attribution",))

    def test_capacity_metrics_fail_closed_on_missing_or_invalid_inputs(self) -> None:
        metrics = capacity_metrics(
            pd.DataFrame(
                {
                    "participation_rate": [0.01, math.nan],
                    "position_notional": [1_000.0, 1_000.0],
                    "avg_daily_amount": [100_000.0, 0.0],
                }
            )
        )

        self.assertTrue(math.isnan(metrics["orders_within_hard_adv"]))
        self.assertTrue(math.isnan(metrics["maximum_liquidation_days"]))

    def test_aggregate_uses_frozen_weights_and_csi_all_share(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=252).strftime("%Y%m%d")
        sleeve_returns = {
            sleeve: pd.DataFrame(
                {"signal_date": dates, "net_return": [daily_return] * len(dates)}
            )
            for sleeve, daily_return in {
                "large": 0.0004,
                "mid": 0.0003,
                "small": 0.0002,
                "micro": 0.0001,
            }.items()
        }
        benchmark = pd.DataFrame(
            {"signal_date": dates, "benchmark_return": [0.0001] * len(dates)}
        )

        result = aggregate_all_cap_metrics(
            sleeve_returns,
            benchmark,
            self.contract,
        )

        self.assertEqual(result["benchmark"], "000985.CSI")
        self.assertEqual(
            result["sleeve_weights"],
            {"large": 0.35, "mid": 0.30, "small": 0.25, "micro": 0.10},
        )
        self.assertGreater(result["annualized_net_excess_return"], 0.02)
        self.assertEqual(result["observations"], 252)

    def test_governance_uses_registry_history_and_records_current_trials(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=40).strftime("%Y%m%d")

        def trial(trial_id: str, offset: float) -> dict[str, object]:
            return {
                "trial_id": trial_id,
                "protocol": "all-cap-v2",
                "oos_returns": [
                    {"date": day, "return": float(value + offset)}
                    for day, value in zip(dates, np.linspace(-0.001, 0.001, 40))
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            registry = TrialRegistry(Path(tmp) / "trials.jsonl")
            registry.record(trial("prior", -0.0002))

            metrics = registered_governance_metrics(
                [
                    trial("router", 0.0),
                    trial("equal", 0.0001),
                    trial("all_cap_v2", 0.0003),
                ],
                selected_trial_id="all_cap_v2",
                registry=registry,
            )

            recorded = registry.read()

        self.assertEqual(metrics["valid_trial_count"], 4)
        self.assertEqual(metrics["pbo_trial_count"], 4)
        self.assertEqual(
            [item["trial_id"] for item in recorded],
            ["prior", "router", "equal", "all_cap_v2"],
        )

    def test_governance_records_failed_misaligned_trial_before_raising(self) -> None:
        dates = pd.bdate_range("2024-01-02", periods=40).strftime("%Y%m%d")
        aligned = {
            "trial_id": "aligned",
            "protocol": "all-cap-v2",
            "oos_returns": [
                {"date": day, "return": 0.001}
                for day in dates
            ],
        }
        misaligned = {
            "trial_id": "misaligned",
            "protocol": "all-cap-v2",
            "oos_returns": [
                {"date": day, "return": -0.001}
                for day in dates[1:]
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            registry = TrialRegistry(Path(tmp) / "trials.jsonl")
            with self.assertRaisesRegex(
                ValueError,
                "trial_oos_dates_misaligned",
            ):
                registered_governance_metrics(
                    [aligned, misaligned],
                    selected_trial_id="aligned",
                    registry=registry,
                )

            recorded_ids = [item["trial_id"] for item in registry.read()]

        self.assertEqual(recorded_ids, ["aligned", "misaligned"])

    def test_summarize_sleeve_evidence_composes_existing_metrics(self) -> None:
        dates = pd.bdate_range("2018-01-02", periods=300)
        periods = pd.DataFrame(
            {
                "signal_date": dates.strftime("%Y%m%d"),
                "fold": [str(min(index // 75, 3)) for index in range(300)],
                "gross_return": [0.0012] * 300,
                "net_return": [0.0010] * 300,
                "benchmark_return": [0.0002] * 300,
                "active_return": [0.0008] * 300,
                "turnover": [0.01] * 300,
                "target_fill_ratio": [0.99] * 300,
            }
        )
        evaluation_rows: list[dict[str, object]] = []
        for day in dates:
            for rank in range(3):
                evaluation_rows.append(
                    {
                        "trade_date": day.strftime("%Y%m%d"),
                        "score": float(rank),
                        "excess_return": float(rank) * 0.001,
                    }
                )
        trades = pd.DataFrame(
            {
                "participation_rate": [0.01] * 120,
                "position_notional": [1_000.0] * 120,
                "avg_daily_amount": [100_000.0] * 120,
                "commission": [1.0] * 120,
                "stamp_tax": [0.5] * 120,
                "slippage": [0.25] * 120,
                "impact_cost": [0.25] * 120,
            }
        )
        replay = PortfolioReplayResult(
            metrics={
                "simulator_version": "paper-parity-daily-v1",
                "gross_return": 0.10,
                "net_return": 0.08,
                "benchmark_return": 0.03,
                "net_excess_return": 0.05,
                "max_drawdown": 0.10,
                "annual_turnover": 2.52,
                "target_fill_ratio": 0.99,
                "trade_count": 120,
                "attribution_status": "reconciled",
                "total_commission": 120.0,
                "total_stamp_tax": 60.0,
                "total_slippage": 30.0,
                "total_execution_cost": 240.0,
            },
            periods=periods,
            trades=trades,
            nav=pd.DataFrame(),
            decisions=pd.DataFrame(),
        )
        double_cost = PortfolioReplayResult(
            metrics={"net_excess_return": 0.02},
            periods=periods,
            trades=trades,
            nav=pd.DataFrame(),
            decisions=pd.DataFrame(),
        )

        metrics = summarize_sleeve_evidence(
            pd.DataFrame(evaluation_rows),
            replay,
            double_cost_replay=double_cost,
            governance={
                "deflated_sharpe_probability": 0.97,
                "probability_of_backtest_overfit": 0.20,
                "pbo_trial_count": 6,
            },
            data_evidence=_passing_data_evidence(),
            benchmark_max_drawdown=0.10,
        )

        self.assertAlmostEqual(metrics["rank_ic"], 1.0)
        self.assertEqual(metrics["oos_folds"], 4)
        self.assertEqual(metrics["positive_oos_folds"], 4)
        self.assertGreaterEqual(metrics["positive_calendar_years"], 1)
        self.assertEqual(metrics["completed_trades"], 120)
        self.assertEqual(metrics["double_cost_net_excess_return"], 0.02)
        self.assertEqual(metrics["pbo_trial_count"], 6)
        self.assertEqual(metrics["cost_attribution"]["total"], 240.0)


if __name__ == "__main__":
    unittest.main()
