from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from stock_analyze.research.activation import evaluate_activation
from stock_analyze.research.paper_candidate_gate import (
    apply_paper_candidate_gate,
    evaluate_scope_result,
    load_gate_contract,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "configs/research/paper_candidate_gate_v1.yaml"


def source_report() -> dict:
    candidate_folds = [
        {"fold": 0, "net_excess_return": 0.16, "max_drawdown": 0.10, "annual_turnover": 2.0},
        {"fold": 1, "net_excess_return": 0.09, "max_drawdown": 0.03, "annual_turnover": 1.0},
        {"fold": 2, "net_excess_return": -0.068, "max_drawdown": 0.22, "annual_turnover": 7.0},
        {"fold": 3, "net_excess_return": -0.136, "max_drawdown": 0.13, "annual_turnover": 7.0},
    ]
    router_folds = [
        {"fold": 0, "net_excess_return": 0.16, "max_drawdown": 0.10, "annual_turnover": 2.0},
        {"fold": 1, "net_excess_return": 0.09, "max_drawdown": 0.03, "annual_turnover": 1.0},
        {"fold": 2, "net_excess_return": -0.070, "max_drawdown": 0.22, "annual_turnover": 7.0},
        {"fold": 3, "net_excess_return": -0.142, "max_drawdown": 0.13, "annual_turnover": 7.0},
    ]
    common = {
        "simulator_version": "paper-parity-daily-v1",
        "attribution_status": "reconciled",
        "trade_count": 386,
    }
    result = {
        "scope": "hk_exposure",
        "market": "cn_qdii_etf",
        "status": "no_pass",
        "folds": [
            {"fold": number, "point_in_time_audit": True}
            for number in range(4)
        ],
        "gate_checks": {"all_scenes_fitted": True},
        "metrics": {
            "scenario_specialists": {
                **common,
                "annualized_excess_wealth": 0.0081,
                "cumulative_relative_wealth": 0.0315,
                "total_execution_cost": 26_664.0,
                "max_drawdown": 0.244,
                "annual_turnover": 5.63,
            },
            "router_only": {
                **common,
                "annualized_excess_wealth": 0.0061,
                "cumulative_relative_wealth": 0.0236,
                "total_execution_cost": 26_119.0,
                "max_drawdown": 0.250,
                "annual_turnover": 5.58,
            },
        },
        "fold_metrics": {
            "scenario_specialists": candidate_folds,
            "router_only": router_folds,
        },
    }
    return {
        "protocol": "scenario-specialists-v1",
        "snapshot_date": "20260814",
        "historical_test_opened": False,
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "results": [result],
    }


class PaperCandidateGateTest(unittest.TestCase):
    @staticmethod
    def _repo(root: Path) -> None:
        (root / "configs").mkdir(parents=True)
        (root / "configs/competition_cn_qdii_etf.yaml").write_text(
            yaml.safe_dump({
                "accounts": [
                    {"id": "hk_exposure", "cash": 500_000},
                    {"id": "us_exposure", "cash": 500_000},
                ]
            }),
            encoding="utf-8",
        )
        labels = root / "data/research/labels/cn_qdii_etf/20260814.parquet"
        labels.parent.mkdir(parents=True)
        pd.DataFrame({
            "label_contract_version": ["next-open-v3-adjusted"]
        }).to_parquet(labels, index=False)

    def test_hk_style_candidate_qualifies_by_cost_multiple_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = source_report()
            decision = evaluate_scope_result(
                root, report, report["results"][0],
                contract=load_gate_contract(CONTRACT),
            )

        self.assertEqual(decision["status"], "qualified")
        self.assertTrue(decision["qualified_for_isolated_paper"])
        self.assertLess(decision["metrics"]["annualized_increment"], 0.005)
        self.assertGreater(
            decision["metrics"]["cumulative_increment"],
            2.0 * decision["metrics"]["incremental_cost_return"],
        )
        self.assertGreater(decision["metrics"]["fold_delta_median"], 0.0)

    def test_disaster_fold_rejects_otherwise_positive_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = source_report()
            report["results"][0]["fold_metrics"]["scenario_specialists"][3][
                "net_excess_return"
            ] = -0.16
            decision = evaluate_scope_result(
                root, report, report["results"][0],
                contract=load_gate_contract(CONTRACT),
            )

        self.assertEqual(decision["status"], "rejected")
        self.assertIn("no_disaster_fold", decision["reasons"])

    def test_negative_fold_delta_median_rejects_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report = source_report()
            candidate = report["results"][0]["fold_metrics"][
                "scenario_specialists"
            ]
            router = report["results"][0]["fold_metrics"]["router_only"]
            candidate[0]["net_excess_return"] = router[0]["net_excess_return"] - 0.001
            candidate[1]["net_excess_return"] = router[1]["net_excess_return"] - 0.001
            candidate[2]["net_excess_return"] = router[2]["net_excess_return"] - 0.001
            decision = evaluate_scope_result(
                root, report, report["results"][0],
                contract=load_gate_contract(CONTRACT),
            )

        self.assertEqual(decision["status"], "rejected")
        self.assertIn("fold_delta_median", decision["reasons"])

    def test_gate_writes_qualification_without_registry_or_formal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(source_report()), encoding="utf-8"
            )

            result = apply_paper_candidate_gate(
                root, source_report_path=report_path,
                contract_path=CONTRACT,
            )

        self.assertTrue(result["decisions"][0]["qualified_for_isolated_paper"])
        self.assertFalse(result["formal_strategy_activated"])
        self.assertFalse(result["registry_mutated"])
        self.assertTrue(result["second_layer_active_gate_unchanged"])

    def test_second_layer_still_requires_twelve_forward_cycles(self):
        from tests.test_research_activation import passing_evidence

        report = evaluate_activation(
            passing_evidence(shadow_cycles=11, forward_cycles=11),
            current_status="shadow", target_status="active",
        )

        self.assertFalse(report.passed)
        self.assertIn("shadow_cycles", report.reasons)
        self.assertIn("forward_cycles", report.reasons)


if __name__ == "__main__":
    unittest.main()
