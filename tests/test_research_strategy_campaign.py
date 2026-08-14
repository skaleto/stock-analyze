from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_analyze.research.strategy_campaign import (
    CAMPAIGN_THRESHOLDS,
    evaluate_incremental_gate,
    evaluate_incremental_residual,
    incremental_specs_for_scope,
    resolve_transparent_scope,
    evaluate_transparent_spec,
    load_campaign_inputs,
    run_strategy_campaign,
)
from stock_analyze.research.classical_specs import transparent_strategy_specs

import numpy as np
import pandas as pd


class ResearchStrategyCampaignTest(unittest.TestCase):
    @staticmethod
    def _trial(spec_id: str, *, net_excess: float = 0.04) -> dict:
        return {
            "trial_id": f"a_share:hs300:{spec_id}",
            "spec_id": spec_id,
            "market": "a_share",
            "account_scope": "hs300",
            "gate_zero": {"passed": True, "reasons": []},
            "gate_one_pre_family": {"passed": net_excess > 0.0, "reasons": []},
            "bootstrap_probability": 0.98,
            "year_concentration": {"passed": True},
            "security_concentration": {"passed": True},
            "regimes": {
                "bull": {"max_drawdown": 0.04},
                "range": {"max_drawdown": 0.05},
                "down": {"max_drawdown": 0.08},
            },
            "metrics": {
                "net_return": 0.08,
                "net_excess_return": net_excess,
                "max_drawdown": 0.10,
            },
            "oos_returns": [
                {"date": f"202401{day:02d}", "return": 0.001 + index * 0.00001}
                for index, day in enumerate(range(2, 14))
            ],
        }

    def test_campaign_input_loader_verifies_payload_hashes_and_markets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests = []
            for market, fingerprint in (("a_share", "a"), ("cn_qdii_etf", "q")):
                bundle = root / market
                payload = bundle / "payload" / f"{market}.txt"
                payload.parent.mkdir(parents=True)
                payload.write_bytes(market.encode("utf-8"))
                manifest = {
                    "schema_version": 1,
                    "kind": "research_training_input",
                    "market": market,
                    "snapshot_date": "20260813",
                    "source_fingerprint": fingerprint,
                    "read_only_input": True,
                    "files": [{
                        "path": f"{market}.txt",
                        "sha256": hashlib.sha256(market.encode("utf-8")).hexdigest(),
                        "size": len(market),
                    }],
                }
                manifest_path = bundle / "manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                manifests.append(manifest_path)

            loaded = load_campaign_inputs(tuple(manifests))

        self.assertEqual(set(loaded), {"a_share", "cn_qdii_etf"})
        self.assertEqual(loaded["a_share"]["snapshot_date"], "20260813")

    def test_campaign_thresholds_are_frozen_to_plan(self) -> None:
        self.assertEqual(CAMPAIGN_THRESHOLDS["maximum_drawdown"], 0.25)
        self.assertEqual(CAMPAIGN_THRESHOLDS["minimum_target_fill_ratio"], 0.95)
        self.assertEqual(CAMPAIGN_THRESHOLDS["cost_stress_multiplier"], 2.0)
        self.assertEqual(CAMPAIGN_THRESHOLDS["bootstrap_samples"], 10_000)
        self.assertEqual(CAMPAIGN_THRESHOLDS["bootstrap_seed"], 20260814)

    def test_transparent_trial_uses_three_purged_folds_and_double_cost_replay(self) -> None:
        dates = pd.date_range("2024-01-02", periods=130, freq="B")
        rows = []
        for day_index, day in enumerate(dates):
            for code_index in range(4):
                entry = 10.0 + code_index + day_index * 0.01
                rows.append({
                    "account_id": "hs300",
                    "research_scope": "hs300",
                    "trade_date": day.strftime("%Y%m%d"),
                    "entry_date": (day + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                    "label_end_date": (day + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                    "code": f"{code_index + 1:06d}",
                    "momentum_20": 0.01 * code_index,
                    "momentum_60": 0.02 * code_index,
                    "momentum_120": 0.03 * code_index,
                    "entry_price": entry,
                    "benchmark_entry_price": 100.0 + day_index * 0.02,
                    "avg_amount_20": 100_000_000.0,
                    "realized_volatility_20": 0.20,
                    "entry_buy_allowed": True,
                    "entry_sell_allowed": True,
                    "excess_return": 0.001 * code_index,
                })
        contract = {
            "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.50,
            },
            "rule_execution_policy": {
                "version": "campaign-transparent-v1",
                "minimum_target_change": 0.0,
                "max_daily_turnover": 1.0,
                "max_industry_weight": 1.0,
            },
        }
        spec = transparent_strategy_specs("a_share", "hs300")[0]

        result = evaluate_transparent_spec(
            pd.DataFrame(rows),
            spec=spec,
            portfolio_contract=contract,
        )

        self.assertEqual(result["walk_forward_splits"], 3)
        self.assertEqual(len(result["folds"]), 3)
        self.assertTrue(all(item["trade_count"] > 0 for item in result["folds"]))
        self.assertEqual(result["cost_stress"]["execution_cost_multiplier"], 2.0)
        self.assertLessEqual(
            result["cost_stress"]["net_return"],
            result["metrics"]["net_return"],
        )
        self.assertTrue(result["point_in_time_audit"])

    def test_scope_gate_requires_both_variants_in_a_family_before_selecting(self) -> None:
        trials = [
            self._trial("A_MOM_01", net_excess=0.08),
            self._trial("A_MOM_02", net_excess=-0.01),
            self._trial("A_QMLV_01", net_excess=0.05),
            self._trial("A_QMLV_02", net_excess=0.04),
            self._trial("A_REGIME_01", net_excess=0.03),
            self._trial("A_REGIME_02", net_excess=0.02),
        ]
        with patch(
            "stock_analyze.research.strategy_campaign.evaluate_campaign_governance",
            return_value={
                "deflated_sharpe_probability": 0.99,
                "probability_of_backtest_overfit": 0.25,
                "legacy_trial_count": 0,
                "valid_trial_count": 6,
            },
        ):
            result = resolve_transparent_scope(trials)

        self.assertEqual(result["status"], "transparent_survivor")
        self.assertEqual(result["selected_spec_id"], "A_QMLV_01")
        by_id = {row["spec_id"]: row for row in result["trials"]}
        self.assertFalse(by_id["A_MOM_01"]["gate_two"]["checks"]["family_variants_positive"])
        self.assertTrue(by_id["A_QMLV_01"]["passed_transparent_gates"])

    def test_scope_gate_fails_closed_when_point_in_time_gate_fails(self) -> None:
        trials = [self._trial(spec.spec_id) for spec in transparent_strategy_specs("a_share", "hs300")]
        trials[0]["gate_zero"] = {"passed": False, "reasons": ["point_in_time_audit"]}

        result = resolve_transparent_scope(trials)

        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["selected_spec_id"])

    def test_incremental_specs_are_zero_when_transparent_scope_has_no_survivor(self) -> None:
        specs = incremental_specs_for_scope({
            "market": "a_share",
            "account_scope": "hs300",
            "status": "falsified",
            "selected_spec_id": None,
        })

        self.assertEqual(specs, ())

    def test_incremental_gate_requires_paired_net_benefit(self) -> None:
        baseline = {
            "metrics": {"net_excess_return": 0.05, "max_drawdown": 0.10, "annual_turnover": 2.0},
            "cost_stress": {"net_excess_return": 0.03},
            "folds": [{"fold": fold, "net_excess_return": 0.01} for fold in range(3)],
            "oos_returns": [
                {"date": f"202401{day:02d}", "return": 0.001}
                for day in range(2, 14)
            ],
        }
        candidate = {
            "metrics": {"net_excess_return": 0.04, "max_drawdown": 0.09, "annual_turnover": 2.1},
            "cost_stress": {"net_excess_return": 0.02},
            "folds": [{"fold": fold, "net_excess_return": 0.009} for fold in range(3)],
            "oos_returns": [
                {"date": f"202401{day:02d}", "return": 0.0009}
                for day in range(2, 14)
            ],
            "feature_direction_stability": {"passed": True},
        }

        result = evaluate_incremental_gate(baseline, candidate, horizon=20)

        self.assertFalse(result["passed"])
        self.assertIn("positive_net_increment", result["reasons"])
        self.assertIn("paired_block_bootstrap", result["reasons"])

    def test_incremental_residual_is_fixed_and_uses_three_purged_folds(self) -> None:
        dates = pd.date_range("2023-01-02", periods=150, freq="B")
        rows = []
        for day_index, day in enumerate(dates):
            for code_index in range(6):
                entry = 10.0 + code_index + day_index * 0.01
                rows.append({
                    "account_id": "hs300",
                    "research_scope": "hs300",
                    "trade_date": day.strftime("%Y%m%d"),
                    "entry_date": (day + pd.offsets.BDay(1)).strftime("%Y%m%d"),
                    "label_end_date": (day + pd.offsets.BDay(20)).strftime("%Y%m%d"),
                    "horizon": 20,
                    "code": f"{code_index + 1:06d}",
                    "momentum_20": 0.01 * code_index,
                    "momentum_60": 0.02 * code_index,
                    "momentum_120": 0.03 * code_index,
                    "account_low_volatility_percentile": 1.0 - code_index / 10.0,
                    "account_liquidity_percentile": code_index / 10.0,
                    "account_quality_percentile": code_index / 12.0,
                    "entry_price": entry,
                    "benchmark_entry_price": 100.0 + day_index * 0.02,
                    "avg_amount_20": 100_000_000.0,
                    "realized_volatility_20": 0.20,
                    "entry_buy_allowed": True,
                    "entry_sell_allowed": True,
                    "excess_return": 0.002 * code_index - 0.004,
                })
        transparent = transparent_strategy_specs("a_share", "hs300")[0]
        incremental = incremental_specs_for_scope({
            "market": "a_share",
            "account_scope": "hs300",
            "status": "transparent_survivor",
            "selected_spec_id": transparent.spec_id,
        })[0]
        contract = {
            "accounts": [{"id": "hs300", "cash": 500_000.0, "top_n": 2}],
            "trading": {
                "lot_size": 100,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.0005,
                "slippage_rate": 0.0005,
                "max_single_weight": 0.50,
            },
            "rule_execution_policy": {
                "version": "campaign-transparent-v1",
                "minimum_target_change": 0.0,
                "max_daily_turnover": 1.0,
                "max_industry_weight": 1.0,
            },
        }

        result = evaluate_incremental_residual(
            pd.DataFrame(rows),
            baseline_spec=transparent,
            incremental_spec=incremental,
            portfolio_contract=contract,
        )

        self.assertEqual(result["walk_forward_splits"], 3)
        self.assertEqual(result["bound_baseline_spec_id"], "A_MOM_01")
        self.assertEqual(result["residual_tilt_weight"], 0.05)
        self.assertEqual(result["cost_stress"]["execution_cost_multiplier"], 2.0)

    def test_transparent_stage_records_all_24_trials_before_returning(self) -> None:
        fake_inputs = {
            market: {
                "market": market,
                "snapshot_date": "20260813",
                "source_fingerprint": f"fingerprint-{market}",
                "_manifest_path": f"/frozen/{market}/manifest.json",
            }
            for market in ("a_share", "cn_qdii_etf")
        }

        def fake_evaluate(_dataset, *, spec, portfolio_contract):
            return {
                "trial_id": f"{spec.market}:{spec.account_scope}:{spec.spec_id}",
                "spec_id": spec.spec_id,
                "spec_hash": spec.spec_hash,
                "market": spec.market,
                "account_scope": spec.account_scope,
                "metrics": {"net_excess_return": -0.01},
                "gate_zero": {"passed": True, "reasons": []},
                "gate_one_pre_family": {"passed": False, "reasons": ["net_excess_return"]},
                "oos_returns": [{"date": "20240102", "return": -0.001}],
            }

        def fake_resolve(trials, *, legacy_trials=()):
            return {
                "market": trials[0]["market"],
                "account_scope": trials[0]["account_scope"],
                "status": "falsified",
                "selected_spec_id": None,
                "reasons": ["no_transparent_candidate_passed_gates_1_2"],
                "trials": list(trials),
                "legacy_trial_count": len(legacy_trials),
            }

        with tempfile.TemporaryDirectory() as tmp, patch(
            "stock_analyze.research.strategy_campaign._freeze_campaign_inputs",
            return_value=(fake_inputs, []),
        ), patch(
            "stock_analyze.research.strategy_campaign._load_scope_dataset",
            return_value=(pd.DataFrame(), {}, {"rows": 1}),
        ), patch(
            "stock_analyze.research.strategy_campaign.evaluate_transparent_spec",
            side_effect=fake_evaluate,
        ) as evaluate, patch(
            "stock_analyze.research.strategy_campaign.resolve_transparent_scope",
            side_effect=fake_resolve,
        ):
            result = run_strategy_campaign(
                repo_root=Path(tmp),
                campaign_id="campaign-1",
                as_of="2026-08-14",
                stage="transparent",
                input_manifests=(Path("a.json"), Path("q.json")),
            )
            trial_lines = (
                Path(tmp) / "data/research/campaigns/campaign-1/trials.jsonl"
            ).read_text(encoding="utf-8").splitlines()

        self.assertEqual(result["status"], "transparent_complete")
        self.assertEqual(evaluate.call_count, 24)
        self.assertEqual(len(trial_lines), 24)


if __name__ == "__main__":
    unittest.main()
