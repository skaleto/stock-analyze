from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_analyze.cli import main


def _trial(
    *,
    market: str,
    scope: str,
    spec_id: str,
    spec_hash: str,
    net_return: float,
    net_excess: float,
    cost_stress_excess: float,
    drawdown: float,
    fill: float,
    positive_folds: int,
    bootstrap: float,
    passed_transparent_gates: bool = False,
) -> dict:
    folds = [
        {
            "fold": index,
            "trade_count": 10,
            "net_excess_return": 0.01 if index < positive_folds else -0.01,
        }
        for index in range(3)
    ]
    return {
        "trial_id": f"{market}:{scope}:{spec_id}",
        "market": market,
        "account_scope": scope,
        "horizon": 20 if market == "a_share" else 10,
        "spec_id": spec_id,
        "spec_hash": spec_hash,
        "point_in_time_audit": True,
        "folds": folds,
        "metrics": {
            "net_return": net_return,
            "net_excess_return": net_excess,
            "max_drawdown": drawdown,
            "target_fill_ratio": fill,
            "missing_liquidity_notional_ratio": 0.0,
            "impact_capped_notional_ratio": 0.0,
            "attribution_status": "reconciled",
        },
        "cost_stress": {"net_excess_return": cost_stress_excess},
        "bootstrap_probability": bootstrap,
        "gate_zero": {"passed": True, "reasons": []},
        "passed_transparent_gates": passed_transparent_gates,
    }


class PersonalQuantShadowAdmissionTest(unittest.TestCase):
    def test_cli_admits_sealed_campaign_through_one_idempotent_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "sealed.json"
            report.write_text("{}", encoding="utf-8")
            with patch(
                "stock_analyze.research.shadow_admission.admit_campaign_shadows",
                return_value={"status": "complete", "admitted": []},
            ) as admit:
                exit_code = main([
                    "admit-personal-quant-shadow",
                    "--repo-root",
                    tmp,
                    "--campaign-report",
                    str(report),
                ])

        self.assertEqual(exit_code, 0)
        admit.assert_called_once_with(Path(tmp), report)

    def test_cli_audits_shadow_quality_read_only_or_applied(self):
        for extra_args, expected_apply in (([], False), (["--apply"], True)):
            with self.subTest(apply=expected_apply), tempfile.TemporaryDirectory() as tmp:
                with patch(
                    "stock_analyze.research.shadow_admission.audit_shadow_quality",
                    return_value={
                        "status": "complete",
                        "flagged": 0,
                        "changed": 0,
                        "entries": [],
                    },
                ) as audit:
                    exit_code = main([
                        "audit-model-shadow-quality",
                        "--repo-root",
                        tmp,
                        *extra_args,
                    ])

                self.assertEqual(exit_code, 0)
                audit.assert_called_once_with(Path(tmp), apply=expected_apply)

    def test_grades_promising_and_exploratory_without_weakening_active_gate(self):
        from stock_analyze.research.shadow_admission import (
            evaluate_transparent_shadow_trial,
        )

        promising = _trial(
            market="cn_qdii_etf",
            scope="hk_exposure",
            spec_id="Q_TREND_01",
            spec_hash="q-hash",
            net_return=0.21,
            net_excess=0.10,
            cost_stress_excess=0.08,
            drawdown=0.236,
            fill=0.955,
            positive_folds=3,
            bootstrap=0.96,
            passed_transparent_gates=True,
        )
        exploratory = _trial(
            market="a_share",
            scope="zz500",
            spec_id="A_MOM_02",
            spec_hash="a-hash",
            net_return=0.316,
            net_excess=-0.0035,
            cost_stress_excess=-0.0227,
            drawdown=0.199,
            fill=0.994,
            positive_folds=1,
            bootstrap=0.557,
            passed_transparent_gates=True,
        )
        low_confidence = json.loads(json.dumps(promising))
        low_confidence["bootstrap_probability"] = 0.80

        qdii = evaluate_transparent_shadow_trial(promising)
        a_share = evaluate_transparent_shadow_trial(exploratory)

        low_confidence_decision = evaluate_transparent_shadow_trial(low_confidence)
        self.assertTrue(qdii["passed"])
        self.assertEqual(qdii["grade"], "promising")
        self.assertFalse(a_share["passed"])
        self.assertEqual(a_share["grade"], "exploratory")
        self.assertTrue(a_share["active_evidence_passed"])
        self.assertIn("positive_net_excess_return", a_share["reasons"])

        self.assertFalse(low_confidence_decision["passed"])
        self.assertIn("bootstrap_probability", low_confidence_decision["reasons"])
    def test_hard_safety_checks_fail_closed(self):
        from stock_analyze.research.shadow_admission import (
            evaluate_transparent_shadow_trial,
        )

        unsafe = _trial(
            market="cn_qdii_etf",
            scope="us_exposure",
            spec_id="Q_TREND_01",
            spec_hash="q-us-hash",
            net_return=0.08,
            net_excess=-0.12,
            cost_stress_excess=-0.14,
            drawdown=0.19,
            fill=0.932,
            positive_folds=0,
            bootstrap=0.14,
        )
        unsafe["point_in_time_audit"] = False

        decision = evaluate_transparent_shadow_trial(unsafe)

        self.assertFalse(decision["passed"])
        self.assertEqual(decision["grade"], "rejected")
        self.assertIn("point_in_time_audit", decision["reasons"])
        self.assertIn("target_fill_ratio", decision["reasons"])

    def test_selects_one_best_safe_trial_for_each_account_scope(self):
        from stock_analyze.research.shadow_admission import select_account_shadow_trials

        a_hs300 = _trial(
            market="a_share", scope="hs300", spec_id="A_MOM_02", spec_hash="hs",
            net_return=0.10, net_excess=-0.04, cost_stress_excess=-0.06,
            drawdown=0.23, fill=0.98, positive_folds=1, bootstrap=0.35,
        )
        a_zz500 = _trial(
            market="a_share", scope="zz500", spec_id="A_MOM_02", spec_hash="zz",
            net_return=0.32, net_excess=-0.003, cost_stress_excess=-0.02,
            drawdown=0.20, fill=0.99, positive_folds=1, bootstrap=0.56,
        )
        q_hk = _trial(
            market="cn_qdii_etf", scope="hk_exposure", spec_id="Q_TREND_01", spec_hash="hk",
            net_return=0.21, net_excess=0.10, cost_stress_excess=0.08,
            drawdown=0.24, fill=0.96, positive_folds=3, bootstrap=0.96,
            passed_transparent_gates=True,
        )
        q_us = _trial(
            market="cn_qdii_etf", scope="us_exposure", spec_id="Q_TREND_01", spec_hash="us",
            net_return=0.08, net_excess=-0.12, cost_stress_excess=-0.14,
            drawdown=0.19, fill=0.96, positive_folds=0, bootstrap=0.14,
        )
        q_us_display = _trial(
            market="cn_qdii_etf", scope="us_exposure", spec_id="Q_TRACK_01", spec_hash="us-bad",
            net_return=-0.02, net_excess=-0.20, cost_stress_excess=-0.22,
            drawdown=0.21, fill=0.92, positive_folds=0, bootstrap=0.05,
        )
        report = {
            "status": "transparent_complete",
            "campaign_id": "campaign-v1",
            "manifest_hash": "manifest-v1",
            "formal_strategy_activated": False,
            "scopes": [
                {"market": row["market"], "account_scope": row["account_scope"], "display_trial": row, "trials": [row]}
                for row in (a_hs300, a_zz500, q_hk)
            ] + [{
                "market": "cn_qdii_etf",
                "account_scope": "us_exposure",
                "display_trial": q_us_display,
                "trials": [q_us_display, q_us],
            }],
        }

        selected = select_account_shadow_trials(report)

        self.assertEqual(
            [(row["market"], row["account_scope"], row["grade"]) for row in selected],
            [("cn_qdii_etf", "hk_exposure", "promising")],
        )
        self.assertEqual(selected[0]["spec_id"], "Q_TREND_01")

    def test_blocked_scope_keeps_each_trial_failure_reason(self):
        from stock_analyze.research.shadow_admission import decide_account_shadow_trials

        low_fill = _trial(
            market="cn_qdii_etf", scope="us_exposure", spec_id="Q_TREND_01", spec_hash="trend",
            net_return=0.08, net_excess=-0.12, cost_stress_excess=-0.14,
            drawdown=0.19, fill=0.93, positive_folds=0, bootstrap=0.14,
        )
        negative = _trial(
            market="cn_qdii_etf", scope="us_exposure", spec_id="Q_TRACK_01", spec_hash="track",
            net_return=-0.02, net_excess=-0.20, cost_stress_excess=-0.22,
            drawdown=0.21, fill=0.98, positive_folds=0, bootstrap=0.05,
        )
        report = {
            "status": "transparent_complete",
            "campaign_id": "campaign-v1",
            "manifest_hash": "manifest-v1",
            "formal_strategy_activated": False,
            "scopes": [{
                "market": "cn_qdii_etf",
                "account_scope": "us_exposure",
                "display_trial": negative,
                "trials": [negative, low_fill],
            }],
        }

        decisions = decide_account_shadow_trials(report)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["status"], "blocked")
        self.assertEqual(decisions[0]["reasons"], ["no_safe_trial"])
        failures = {
            row["spec_id"]: row["reasons"]
            for row in decisions[0]["trial_decisions"]
        }
        self.assertIn("positive_net_return", failures["Q_TRACK_01"])
        self.assertIn("target_fill_ratio", failures["Q_TREND_01"])

    def test_admission_freezes_artifact_and_is_idempotent(self):
        from stock_analyze.model_iteration import model_registry_path
        from stock_analyze.research.activation import ModelRegistry
        from stock_analyze.research.classical_specs import transparent_strategy_specs
        from stock_analyze.research.shadow_admission import admit_campaign_shadows

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            campaign_id = "campaign-v1"
            manifest_hash = "manifest-v1"
            specs = {
                (market, scope, spec.spec_id): spec
                for market, scope in (
                    ("a_share", "zz500"),
                    ("cn_qdii_etf", "hk_exposure"),
                )
                for spec in transparent_strategy_specs(market, scope)
            }
            a_spec = specs[("a_share", "zz500", "A_MOM_02")]
            q_spec = specs[("cn_qdii_etf", "hk_exposure", "Q_TREND_01")]
            report = {
                "status": "transparent_complete",
                "campaign_id": campaign_id,
                "manifest_hash": manifest_hash,
                "formal_strategy_activated": False,
                "scopes": [
                    {
                        "market": "a_share",
                        "account_scope": "zz500",
                        "display_trial": _trial(
                            market="a_share", scope="zz500", spec_id="A_MOM_02",
                            spec_hash=a_spec.spec_hash, net_return=0.32,
                            net_excess=-0.003, cost_stress_excess=-0.02,
                            drawdown=0.20, fill=0.99, positive_folds=1,
                            bootstrap=0.56,
                        ),
                    },
                    {
                        "market": "cn_qdii_etf",
                        "account_scope": "hk_exposure",
                        "display_trial": _trial(
                            market="cn_qdii_etf", scope="hk_exposure",
                            spec_id="Q_TREND_01", spec_hash=q_spec.spec_hash,
                            net_return=0.21, net_excess=0.10,
                            cost_stress_excess=0.08, drawdown=0.24, fill=0.96,
                            positive_folds=3, bootstrap=0.96,
                            passed_transparent_gates=True,
                        ),
                    },
                ],
            }
            full_trials = [
                json.loads(json.dumps(scope["display_trial"]))
                for scope in report["scopes"]
            ]
            for trial in full_trials:
                trial["manifest_hash"] = manifest_hash
            for scope in report["scopes"]:
                metrics = scope["display_trial"]["metrics"]
                metrics.pop("missing_liquidity_notional_ratio")
                metrics.pop("impact_capped_notional_ratio")
            report_path = root / "reports/research/campaign-v1-transparent.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            trials_path = root / f"data/research/campaigns/{campaign_id}/trials.jsonl"
            trials_path.parent.mkdir(parents=True)
            trials_path.write_text(
                "".join(json.dumps(trial) + "\n" for trial in full_trials),
                encoding="utf-8",
            )
            (trials_path.parent / "manifest.json").write_text(
                json.dumps({
                    "campaign_id": campaign_id,
                    "manifest_hash": manifest_hash,
                }),
                encoding="utf-8",
            )
            for market, accounts, trading in (
                (
                    "a_share",
                    [{"id": "zz500", "scope": "zz500", "benchmark": "000905", "cash": 500000, "top_n": 50}],
                    {"lot_size": 100, "max_single_weight": 0.05},
                ),
                (
                    "cn_qdii_etf",
                    [{"id": "hk_exposure", "scope": "hk_exposure", "benchmark": "159920.SZ", "cash": 500000, "top_n": 5}],
                    {"lot_size_default": 100, "max_single_weight": 0.20},
                ),
            ):
                config = root / f"data/research/campaigns/{campaign_id}/input/{market}/payload/configs/competition_{market}.yaml"
                config.parent.mkdir(parents=True)
                config.write_text(json.dumps({"accounts": accounts, "trading": trading}), encoding="utf-8")

            old_registry_path = model_registry_path(
                root,
                "a_share",
                20,
                account_scope="zz500",
            )
            ModelRegistry(old_registry_path).admit_development_shadow(
                "old-transparent-rule",
                metadata={"candidate_kind": "transparent_rule"},
                admission={"contract": "personal-quant-shadow-v1"},
            )

            first = admit_campaign_shadows(root, report_path)
            second = admit_campaign_shadows(root, report_path)

            self.assertEqual(first["admitted"], second["admitted"])
            self.assertEqual(len(first["admitted"]), 1)
            for admitted in first["admitted"]:
                registry_path = root / admitted["registry_path"]
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                model = registry["models"][admitted["model_version"]]
                artifact = json.loads((root / model["artifact"]).read_text(encoding="utf-8"))
                self.assertEqual(model["status"], "shadow")
                self.assertFalse(model["formal_strategy_activated"])
                self.assertFalse(registry["formal_strategy_activated"])
                self.assertIsNone(registry["champion_model_version"])
                self.assertEqual(model["candidate_kind"], "transparent_rule")
                self.assertEqual(artifact["manifest_hash"], manifest_hash)
                event_ids = [event["event_id"] for event in registry["lifecycle_events"]]
                self.assertEqual(len(event_ids), len(set(event_ids)))
                active_rules = [
                    candidate
                    for candidate in registry["models"].values()
                    if candidate.get("candidate_kind") == "transparent_rule"
                    and candidate.get("status") == "shadow"
                ]
                self.assertEqual(len(active_rules), 1)

            old_registry = json.loads(old_registry_path.read_text(encoding="utf-8"))
            self.assertEqual(
                old_registry["models"]["old-transparent-rule"]["status"],
                "shadow",
            )

    def test_audit_rejects_only_unqualified_legacy_shadow_idempotently(self):
        from stock_analyze.model_iteration import (
            SHADOW_ADMISSION_CONTRACT,
            model_registry_path,
        )
        from stock_analyze.research.activation import ModelRegistry
        from stock_analyze.research.shadow_admission import audit_shadow_quality

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = model_registry_path(
                root,
                "a_share",
                20,
                account_scope="hs300",
            )
            qualified_path = model_registry_path(
                root,
                "cn_qdii_etf",
                10,
                account_scope="hk_exposure",
            )
            ModelRegistry(legacy_path).admit_development_shadow(
                "legacy-rule",
                metadata={"candidate_kind": "transparent_rule"},
                admission={
                    "contract": "personal-quant-shadow-v1",
                    "active_evidence_passed": False,
                },
            )
            ModelRegistry(qualified_path).admit_development_shadow(
                "qualified-rule",
                metadata={"candidate_kind": "transparent_rule"},
                admission={
                    "contract": SHADOW_ADMISSION_CONTRACT,
                    "active_evidence_passed": True,
                },
            )
            legacy_before = legacy_path.read_bytes()
            qualified_before = qualified_path.read_bytes()

            report = audit_shadow_quality(root)

            self.assertEqual(report["status"], "audit_required")
            self.assertFalse(report["apply"])
            self.assertEqual(report["flagged"], 1)
            self.assertEqual(report["changed"], 0)
            self.assertEqual(report["entries"][0]["model_version"], "legacy-rule")
            self.assertEqual(report["entries"][0]["action"], "reject")
            self.assertEqual(legacy_path.read_bytes(), legacy_before)
            self.assertEqual(qualified_path.read_bytes(), qualified_before)

            applied = audit_shadow_quality(root, apply=True)

            self.assertEqual(applied["status"], "complete")
            self.assertEqual(applied["flagged"], 1)
            self.assertEqual(applied["changed"], 1)
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            qualified = json.loads(qualified_path.read_text(encoding="utf-8"))
            self.assertEqual(legacy["models"]["legacy-rule"]["status"], "rejected")
            self.assertEqual(
                qualified["models"]["qualified-rule"]["status"],
                "shadow",
            )
            self.assertFalse(legacy["formal_strategy_activated"])
            self.assertIsNone(legacy["champion_model_version"])

            repeated = audit_shadow_quality(root, apply=True)

            self.assertEqual(repeated["flagged"], 0)
            self.assertEqual(repeated["changed"], 0)


if __name__ == "__main__":
    unittest.main()


class FullHistoryShadowAdmissionTest(unittest.TestCase):
    def _trial(self) -> dict:
        return {
            "trial_id": "a_share:hs300:new-model",
            "market": "a_share",
            "account_scope": "hs300",
            "horizon": 20,
            "point_in_time_audit": True,
            "expected_outer_folds": 4,
            "folds": [
                {"fold": index, "trade_count": 10, "net_excess_return": 0.01}
                for index in range(4)
            ],
            "metrics": {
                "net_return": 0.05,
                "net_excess_return": 0.03,
                "max_drawdown": 0.10,
                "target_fill_ratio": 0.98,
                "missing_liquidity_notional_ratio": 0.0,
                "impact_capped_notional_ratio": 0.0,
                "attribution_status": "reconciled",
            },
            "cost_stress": {"net_excess_return": 0.01},
            "bootstrap_probability": 0.97,
            "deflated_sharpe_probability": 0.96,
            "probability_of_backtest_overfit": 0.20,
            "gate_zero": {"passed": True},
            "passed_transparent_gates": True,
        }

    def test_accepts_four_fold_full_history_evidence(self) -> None:
        from stock_analyze.research.shadow_admission import evaluate_transparent_shadow_trial

        result = evaluate_transparent_shadow_trial(self._trial())
        self.assertTrue(result["passed"])
        self.assertEqual(result["positive_excess_folds"], 4)

    def test_rejects_missing_dsr_and_excessive_pbo(self) -> None:
        from stock_analyze.research.shadow_admission import evaluate_transparent_shadow_trial

        trial = self._trial()
        trial.pop("deflated_sharpe_probability")
        trial["probability_of_backtest_overfit"] = 0.75
        result = evaluate_transparent_shadow_trial(trial)
        self.assertFalse(result["passed"])
        self.assertIn("deflated_sharpe_probability", result["reasons"])
        self.assertIn("probability_of_backtest_overfit", result["reasons"])
