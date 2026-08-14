from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.research.campaign_report import write_final_campaign_report


class ResearchCampaignReportTest(unittest.TestCase):
    def test_final_report_requires_all_four_terminal_scope_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "campaign_final_scope_count"):
                write_final_campaign_report(
                    Path(tmp),
                    campaign_id="campaign-1",
                    manifest_hash="hash-1",
                    scopes=[{
                        "market": "a_share",
                        "account_scope": "hs300",
                        "status": "falsified",
                        "reasons": ["no_candidate"],
                    }],
                )

    def test_final_report_is_machine_readable_and_never_activates_formal_strategy(self) -> None:
        scopes = [
            {"market": "a_share", "account_scope": "hs300", "status": "falsified", "reasons": ["gate_1"]},
            {"market": "a_share", "account_scope": "zz500", "status": "insufficient_data", "reasons": ["pit"]},
            {"market": "cn_qdii_etf", "account_scope": "hk_exposure", "status": "baseline_only", "reasons": ["ml_no_increment"]},
            {"market": "cn_qdii_etf", "account_scope": "us_exposure", "status": "shadow_ready", "reasons": []},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = write_final_campaign_report(
                Path(tmp),
                campaign_id="campaign-1",
                manifest_hash="hash-1",
                scopes=scopes,
            )
            payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "complete")
        self.assertFalse(payload["formal_strategy_activated"])
        self.assertIsNone(payload["champion_model_version"])
        self.assertEqual(len(payload["scopes"]), 4)

    def test_falsified_scope_reports_best_diagnostic_trial_instead_of_zeroes(self) -> None:
        diagnostic_trial = {
            "spec_id": "RULE_B",
            "oos_predictions": [{"date": "20260801", "value": 0.01}] * 100,
            "oos_returns": [{"date": "20260801", "return": 0.01}] * 100,
            "metrics": {
                "benchmark_return": 0.08,
                "net_return": 0.10,
                "net_excess_return": 0.018,
                "portfolio_sharpe": 0.62,
                "max_drawdown": 0.21,
                "target_fill_ratio": 0.97,
                "strategic_risky_exposure": 0.75,
                "annual_turnover": 6.2,
            },
            "attribution": {
                "status": "reconciled",
                "components": {
                    "selection": 0.04,
                    "timing": 0.01,
                    "beta": 0.0,
                    "active_cash": -0.005,
                    "fees": -0.02,
                    "unfilled": -0.007,
                },
            },
            "folds": [
                {"fold": 0, "net_excess_return": 0.02},
                {"fold": 1, "net_excess_return": 0.01},
                {"fold": 2, "net_excess_return": -0.012},
            ],
            "regimes": {
                "bull": {"cumulative_active_return": 0.03},
                "range": {"cumulative_active_return": 0.02},
                "down": {"cumulative_active_return": -0.04},
            },
            "bootstrap_probability": 0.79,
            "gate_one_pre_family": {
                "passed": False,
                "reasons": ["drawdown_vs_benchmark"],
            },
            "gate_two": {
                "passed": False,
                "reasons": ["stationary_bootstrap", "year_concentration"],
                "governance": {
                    "deflated_sharpe_probability": 0.72,
                    "probability_of_backtest_overfit": 0.68,
                },
            },
        }
        scopes = [
            {
                "market": "a_share",
                "account_scope": "hs300",
                "status": "falsified",
                "selected_spec_id": None,
                "reasons": ["no_transparent_candidate_passed_gates_1_2"],
                "trials": [
                    {
                        "spec_id": "RULE_A",
                        "metrics": {
                            "net_return": -0.10,
                            "net_excess_return": -0.20,
                        },
                    },
                    diagnostic_trial,
                ],
            },
            {"market": "a_share", "account_scope": "zz500", "status": "falsified"},
            {"market": "cn_qdii_etf", "account_scope": "hk_exposure", "status": "falsified"},
            {"market": "cn_qdii_etf", "account_scope": "us_exposure", "status": "falsified"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = write_final_campaign_report(
                Path(tmp),
                campaign_id="campaign-1",
                manifest_hash="hash-1",
                scopes=scopes,
            )
            payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
            markdown = Path(result["markdown_path"]).read_text(encoding="utf-8")

        hs300 = next(item for item in payload["scopes"] if item["account_scope"] == "hs300")
        self.assertEqual(hs300["best_diagnostic_spec_id"], "RULE_B")
        self.assertTrue(hs300["diagnostic_only"])
        self.assertEqual(hs300["transparent_trial_count"], 2)
        self.assertEqual(hs300["incremental_trial_count"], 0)
        self.assertEqual(hs300["display_trial"]["spec_id"], "RULE_B")
        self.assertNotIn("trials", hs300)
        self.assertNotIn("oos_predictions", hs300["display_trial"])
        self.assertNotIn("oos_returns", hs300["display_trial"])
        self.assertIn("最佳诊断候选：`RULE_B`（仅用于解释失败，不代表选中）", markdown)
        self.assertIn("基准收益：8.00%", markdown)
        self.assertIn("净收益：10.00%", markdown)
        self.assertIn("Gate 1 失败：`drawdown_vs_benchmark`", markdown)
        self.assertIn("Gate 2 失败：`stationary_bootstrap, year_concentration`", markdown)
        self.assertIn("三折净超额：2.00% / 1.00% / -1.20%", markdown)
        self.assertIn("市场状态净超额：牛市 3.00% / 震荡 2.00% / 下行 -4.00%", markdown)


if __name__ == "__main__":
    unittest.main()
