from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml

from stock_analyze.cli import build_parser
from stock_analyze.research.paper_candidate_runtime import (
    _canonical_hash,
    build_a_share_donchian_signals,
    build_qdii_scene_signals,
    freeze_hk_candidate_artifact,
    load_hk_candidate_artifact,
    load_paper_candidate_contract,
    paper_portfolio_dir,
    run_production_paper_challengers,
)
from stock_analyze.research.scenario_model import load_scenario_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "configs/research/production_paper_challengers_v1.yaml"
SCENARIO_PATH = ROOT / "configs/research/scenario_model_v1.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PaperCandidateContractTest(unittest.TestCase):
    def test_contract_freezes_four_isolated_accounts_and_evidence_labels(self):
        contract = load_paper_candidate_contract(CONTRACT_PATH)

        self.assertTrue(contract["paper_trading_only"])
        self.assertFalse(contract["formal_strategy_activated"])
        self.assertFalse(contract["registry_mutation_allowed"])
        self.assertEqual(
            tuple(contract["markets"]["a_share"]["accounts"]),
            ("hs300", "zz500"),
        )
        self.assertEqual(
            tuple(contract["markets"]["cn_qdii_etf"]["accounts"]),
            ("hk_exposure", "us_exposure"),
        )
        self.assertEqual(
            contract["markets"]["cn_qdii_etf"]["accounts"]["hk_exposure"]["evidence_status"],
            "qualified_candidate",
        )
        for account_id in ("hs300", "zz500", "us_exposure"):
            market = "a_share" if account_id in {"hs300", "zz500"} else "cn_qdii_etf"
            self.assertEqual(
                contract["markets"][market]["accounts"][account_id]["evidence_status"],
                "transparent_challenger",
            )

    def test_portfolio_paths_are_versioned_and_not_formal_account_paths(self):
        contract = load_paper_candidate_contract(CONTRACT_PATH)
        path = paper_portfolio_dir(ROOT, contract, "a_share", "hs300")

        self.assertIn("data/research/paper_portfolios/a_share/hs300", str(path))
        self.assertNotIn("data/a_share/claude", str(path))
        self.assertNotIn("data/a_share/codex", str(path))

    def test_cli_exposes_run_and_explicit_artifact_freeze(self):
        parser = build_parser()
        run = parser.parse_args(["run-paper-candidates", "--scope", "all", "--offline"])
        freeze = parser.parse_args(["freeze-hk-paper-candidate"])

        self.assertEqual(run.command, "run-paper-candidates")
        self.assertEqual(run.scope, "all")
        self.assertEqual(freeze.command, "freeze-hk-paper-candidate")


class AShareDonchianSignalTest(unittest.TestCase):
    @staticmethod
    def _frame(*, final_close: float = 40.0) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        dates = pd.date_range("2026-06-01", periods=30, freq="B")
        for index, day in enumerate(dates):
            close = 10.0 + index * 0.25
            low = close - 0.5
            if index == len(dates) - 1:
                close = final_close
                low = final_close - 0.5
            rows.append({
                "code": "000001",
                "trade_date": day.strftime("%Y%m%d"),
                "account_id": "hs300",
                "research_scope": "hs300",
                "close": close,
                "adjusted_close": close,
                "adjusted_low": low,
                "breakout_20": 0.05 if index == len(dates) - 1 else -0.01,
                "momentum_20": 0.20,
                "natr_14": 2.0,
                "industry": "银行",
                "is_st": 0,
                "is_suspended": 0,
                "is_tradable": True,
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _account() -> dict[str, object]:
        return {
            "id": "hs300", "scope": "hs300", "top_n": 10,
            "strategy_id": "A_DONCHIAN_20_10_V1",
            "minimum_current_members": 1,
        }

    def test_fresh_breakout_is_a_transparent_next_open_candidate(self):
        signals = build_a_share_donchian_signals(
            self._frame(), account=self._account(), state={"accounts": {}},
            snapshot_date="20260710", minimum_coverage=0.8,
        )

        self.assertEqual(signals["code"].tolist(), ["000001"])
        self.assertEqual(set(signals["signal_kind"]), {"transparent_rule"})
        self.assertEqual(set(signals["reason"]), {"donchian_entry_20"})
        self.assertNotIn("p_up", signals.columns)
        self.assertNotIn("expected_excess_return", signals.columns)

    def test_held_position_is_retained_until_ten_day_exit(self):
        state = {
            "accounts": {
                "hs300": {"positions": {"000001": {"shares": 100, "avg_cost": 10.0}}}
            }
        }
        held = build_a_share_donchian_signals(
            self._frame(), account=self._account(), state=state,
            snapshot_date="20260710", minimum_coverage=0.8,
        )
        exited = build_a_share_donchian_signals(
            self._frame(final_close=1.0), account=self._account(), state=state,
            snapshot_date="20260710", minimum_coverage=0.8,
        )

        self.assertEqual(held.iloc[0]["reason"], "donchian_hold_until_exit")
        self.assertFalse(bool(held.iloc[0]["hard_risk_exit"]))
        self.assertEqual(exited.iloc[0]["reason"], "donchian_exit_10")
        self.assertTrue(bool(exited.iloc[0]["hard_risk_exit"]))

    def test_incomplete_current_index_cross_section_fails_closed(self):
        account = {**self._account(), "minimum_current_members": 2}
        with self.assertRaisesRegex(ValueError, "paper_candidate_scope_incomplete"):
            build_a_share_donchian_signals(
                self._frame(), account=account, state={"accounts": {}},
                snapshot_date="20260710", minimum_coverage=0.8,
            )


class QDIISceneSignalTest(unittest.TestCase):
    @staticmethod
    def _frame(scope: str = "us_exposure") -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        dates = pd.date_range("2025-09-01", periods=220, freq="B")
        for date_index, day in enumerate(dates):
            for code_index, code in enumerate(("513100", "513500")):
                close = 1.0 + date_index * 0.003 + code_index * 0.02
                rows.append({
                    "code": code,
                    "trade_date": day.strftime("%Y%m%d"),
                    "account_id": scope,
                    "research_scope": scope,
                    "close": close,
                    "amount": 10_000_000 + code_index * 1_000_000,
                    "avg_amount_20": 10_000_000 + code_index * 1_000_000,
                    "momentum_20": 0.08 + code_index * 0.01,
                    "momentum_60": 0.15 + code_index * 0.01,
                    "momentum_120": 0.20 + code_index * 0.01,
                    "sma_distance_200": 0.10 + code_index * 0.01,
                    "realized_volatility_20": 0.10,
                    "natr_14": 2.0,
                    "nav_momentum_20": 0.07,
                    "discount_premium": 0.001 + code_index * 0.001,
                    "tracking_error_20": 0.02 + code_index * 0.001,
                    "global_index_momentum": 0.10,
                    "industry": "QDII ETF",
                })
        return pd.DataFrame(rows)

    def test_us_router_is_transparent_and_carries_no_fake_model_probabilities(self):
        scenario = load_scenario_contract(SCENARIO_PATH)
        frame = self._frame()
        latest = frame["trade_date"].max()
        account = {
            "id": "us_exposure", "scope": "us_exposure", "top_n": 5,
            "minimum_current_members": 2, "baseline_spec_id": "Q_TRACK_01",
            "strategy_id": "US_Q_TRACK_01_ROUTER_V1",
        }

        signals = build_qdii_scene_signals(
            frame, account=account, scenario_contract=scenario,
            snapshot_date=latest, minimum_coverage=0.8,
        )

        self.assertEqual(set(signals["signal_kind"]), {"transparent_rule"})
        self.assertEqual(set(signals["model_version"]), {"none-transparent-router"})
        self.assertTrue(signals["reason"].str.startswith("transparent_scene_router:").all())
        self.assertNotIn("p_up", signals.columns)

    def test_hk_artifact_changes_rank_only_by_frozen_ten_percent_residual(self):
        scenario = load_scenario_contract(SCENARIO_PATH)
        frame = self._frame("hk_exposure")
        latest = frame["trade_date"].max()
        account = {
            "id": "hk_exposure", "scope": "hk_exposure", "top_n": 5,
            "minimum_current_members": 2, "baseline_spec_id": "Q_TRACK_02",
            "strategy_id": "HK_SCENARIO_SPECIALISTS_V1",
        }
        features = scenario["scopes"]["hk_exposure"]["features"]
        expert = {
            "feature_medians": {name: 0.0 for name in features},
            "scaler_mean": [0.0] * len(features),
            "scaler_scale": [1.0] * len(features),
            "coefficients": [1.0] + [0.0] * (len(features) - 1),
            "intercept": 0.0,
        }
        artifact = {
            "feature_columns": features,
            "experts": {scene: copy.deepcopy(expert) for scene in ("expansion", "range", "stress")},
            "residual_weight": 0.10,
            "artifact_sha256": "a" * 64,
            "version": "test-v1",
        }

        signals = build_qdii_scene_signals(
            frame, account=account, scenario_contract=scenario,
            snapshot_date=latest, minimum_coverage=0.8, artifact=artifact,
        )

        self.assertEqual(set(signals["model_version"]), {"test-v1"})
        self.assertIn("model_residual", signals.columns)
        self.assertTrue(signals["reason"].str.startswith("qualified_scenario_specialist:").all())


class ArtifactFreezeTest(unittest.TestCase):
    def test_freeze_serializes_three_experts_and_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "configs/research"
            config_dir.mkdir(parents=True)
            scenario_path = config_dir / "scenario.yaml"
            scenario_path.write_bytes(SCENARIO_PATH.read_bytes())
            report = root / "evidence/report.json"
            ledger = root / "evidence/ledger.json"
            features = root / "evidence/features.parquet"
            labels = root / "evidence/labels.parquet"
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({"historical_test_opened": False}), encoding="utf-8")
            ledger.write_text(json.dumps({"decisions": [{
                "scope": "hk_exposure", "status": "qualified",
                "qualified_for_isolated_paper": True,
            }]}), encoding="utf-8")
            features.write_bytes(b"features")
            labels.write_bytes(b"labels")
            source = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
            source["data_root"] = "data/paper"
            source["artifact_root"] = "data/artifacts"
            source["scenario_contract"] = "configs/research/scenario.yaml"
            source["scenario_contract_sha256"] = _sha(scenario_path)
            source["source_evidence"] = {
                "scenario_report": "evidence/report.json",
                "scenario_report_sha256": _sha(report),
                "qualification_ledger": "evidence/ledger.json",
                "qualification_ledger_sha256": _sha(ledger),
                "development_features": "evidence/features.parquet",
                "development_features_sha256": _sha(features),
                "development_labels": "evidence/labels.parquet",
                "development_labels_sha256": _sha(labels),
            }
            contract_path = config_dir / "runtime.yaml"
            contract_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
            scenario = load_scenario_contract(scenario_path)
            feature_names = scenario["scopes"]["hk_exposure"]["features"]
            rows = []
            for scene_index, scene in enumerate(("expansion", "range", "stress")):
                for index, day in enumerate(pd.date_range("2018-01-02", periods=120, freq="B")):
                    rows.append({
                        "trade_date": (day + pd.DateOffset(years=scene_index * 2)).strftime("%Y%m%d"),
                        "label_end_date": (day + pd.DateOffset(years=scene_index * 2) + pd.offsets.BDay(10)).strftime("%Y%m%d"),
                        "excess_return": (index - 60) / 100_000,
                        "scene": scene,
                        **{name: 0.1 + index / 10_000 for name in feature_names},
                    })
            dataset = pd.DataFrame(rows)

            with patch(
                "stock_analyze.research.paper_candidate_runtime._scope_dataset",
                return_value=dataset,
            ), patch(
                "stock_analyze.research.paper_candidate_runtime.classify_scenes",
                side_effect=lambda frame, _router: frame,
            ):
                artifact = freeze_hk_candidate_artifact(
                    root, contract_path=contract_path
                )
            loaded = load_hk_candidate_artifact(root, contract_path=contract_path)

            self.assertEqual(set(artifact["experts"]), {"expansion", "range", "stress"})
            self.assertEqual(artifact["development_end"], "20241231")
            self.assertEqual(loaded["artifact_sha256"], artifact["artifact_sha256"])
            path = Path(loaded["artifact_path"])
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["residual_weight"] = 0.50
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "paper_candidate_artifact_hash"):
                load_hk_candidate_artifact(root, contract_path=contract_path)


class FailClosedOrchestrationTest(unittest.TestCase):
    def test_missing_exact_date_snapshots_fail_all_four_without_formal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_dir = root / "configs/research"
            contract_dir.mkdir(parents=True)
            scenario = contract_dir / "scenario_model_v1.yaml"
            scenario.write_bytes(SCENARIO_PATH.read_bytes())
            source = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
            source["scenario_contract"] = "configs/research/scenario_model_v1.yaml"
            source["scenario_contract_sha256"] = _sha(scenario)
            contract = contract_dir / "runtime.yaml"
            contract.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

            result = run_production_paper_challengers(
                root, contract_path=contract, markets="all",
                as_of="2026-08-19", offline=True,
            )

            self.assertEqual(result["status"], "failed_closed")
            self.assertEqual(result["accounts_complete"], 0)
            self.assertEqual(len(result["accounts"]), 4)
            self.assertTrue(all(row["status"] == "failed_closed" for row in result["accounts"]))
            self.assertFalse((root / "data/a_share/claude/state.json").exists())
            self.assertFalse((root / "data/a_share/codex/state.json").exists())
            self.assertFalse((root / "data/research/models/registry.json").exists())


if __name__ == "__main__":
    unittest.main()
