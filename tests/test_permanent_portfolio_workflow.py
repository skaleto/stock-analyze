from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import pandas as pd

import stock_analyze.permanent_portfolio_history as history_module
from stock_analyze.permanent_portfolio_history import (
    merge_historical_market,
    rebuild_historical_dashboard,
)
from stock_analyze.research.permanent_portfolio.contract import (
    canonical_hash,
    load_contract,
)
from stock_analyze.research.permanent_portfolio.workflow import (
    _assert_market_accounting,
    _momentum_observations,
    _study_root,
    evaluate_window,
    open_holdout_once,
    run_development,
)


ROLE_CODES = {
    "equity": "510300.SH",
    "bond": "511260.SH",
    "cash": "511880.SH",
    "gold": "518880.SH",
}


def _write_bound_development(root: Path) -> tuple[Path, dict[str, str]]:
    evidence = {
        "contract_sha256": "b" * 64,
        "market_bundle_sha256": "c" * 64,
        "development_data_sha256": "d" * 64,
        "code_sha256": "e" * 64,
        "git_revision": "f" * 40,
    }
    payload = {
        "status": "development_complete",
        **evidence,
        "evidence": "frozen",
    }
    artifact_sha256 = canonical_hash(payload)
    development = root / "development.json"
    development.write_text(
        json.dumps({**payload, "artifact_sha256": artifact_sha256}),
        encoding="utf-8",
    )
    state = {
        "schema_version": 1,
        "status": "development_complete",
        "development_artifact": str(development.resolve()),
        "development_sha256": artifact_sha256,
        **evidence,
    }
    state["state_sha256"] = canonical_hash(state)
    manifests = root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    return development, {**evidence, "artifact_sha256": artifact_sha256}


def _market_history() -> pd.DataFrame:
    dates = list(
        pd.date_range(
            "2016-12-30",
            "2017-12-29",
            freq=pd.offsets.BusinessMonthEnd(),
        )
    ) + list(pd.date_range("2018-01-02", "2018-01-05", freq="B"))
    rows: list[dict[str, object]] = []
    role_growth = {
        "equity": 0.012,
        "bond": 0.004,
        "cash": 0.001,
        "gold": 0.008,
    }
    for index, day in enumerate(dates):
        for role, code in ROLE_CODES.items():
            close = 10.0 * ((1.0 + role_growth[role]) ** index)
            rows.append(
                {
                    "trade_date": day.strftime("%Y%m%d"),
                    "role": role,
                    "code": code,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "vol": 100000.0,
                    "amount": close * 100000.0,
                    "adj_factor": 1.0,
                    "adjusted_close": close,
                    "is_open": True,
                }
            )
    return pd.DataFrame(rows)


def _v2_market_history() -> pd.DataFrame:
    dates = list(
        pd.date_range(
            "2017-08-31",
            "2018-08-31",
            freq=pd.offsets.BusinessMonthEnd(),
        )
    ) + list(pd.date_range("2018-09-03", "2018-09-05", freq="B"))
    rows: list[dict[str, object]] = []
    for index, day in enumerate(dates):
        for role, code in ROLE_CODES.items():
            close = 10.0 + index * 0.1
            rows.append(
                {
                    "trade_date": day.strftime("%Y%m%d"),
                    "role": role,
                    "code": code,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "vol": 100000.0,
                    "amount": close * 100000.0,
                    "adj_factor": 1.0,
                    "adjusted_close": close,
                    "distribution_cash_per_share": 0.0,
                    "is_open": True,
                }
            )
    return pd.DataFrame(rows)


class PermanentPortfolioWorkflowTests(unittest.TestCase):
    def test_v2_history_rebuild_stays_in_v2_and_labels_corrected_retest(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = load_contract(
                "configs/research/permanent_portfolio_v2.yaml"
            )
            study_root = (
                root / "data/research/permanent_portfolio/v2"
            )
            state = {
                "schema_version": 2,
                "study_id": contract.study_id,
                "status": "holdout_complete",
                "holdout_end": "20180905",
                "market_bundle_sha256": "b" * 64,
            }
            state["state_sha256"] = canonical_hash(state)
            manifests = study_root / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            report = (
                root
                / "reports/research/permanent_portfolio/v2/dashboard.json"
            )
            report.parent.mkdir(parents=True)
            existing = {
                "schema_version": 2,
                "study": state,
                "forward": {"status": "unavailable"},
            }
            existing["dashboard_sha256"] = canonical_hash(existing)
            report.write_text(json.dumps(existing), encoding="utf-8")
            market = _v2_market_history()
            evidence = {
                "market_bundle_sha256": "b" * 64,
                "partition_data_sha256": "d" * 64,
                "schema_version": 2,
                "accounting_version": "cash_distributions_v2",
            }

            with (
                unittest.mock.patch.object(
                    history_module,
                    "_market_index",
                    return_value=({}, "b" * 64),
                ),
                unittest.mock.patch.object(
                    history_module,
                    "_latest_market_with_evidence",
                    side_effect=[(market, evidence), (market, evidence)],
                ),
            ):
                result = rebuild_historical_dashboard(
                    repo_root=root,
                    contract_path=(
                        "configs/research/permanent_portfolio_v2.yaml"
                    ),
                )

            rebuilt = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                Path(result["dashboard"]),
                report.resolve(),
            )
            self.assertEqual(rebuilt["schema_version"], 2)
            self.assertEqual(
                rebuilt["historical"]["stage_boundaries"][0][
                    "after_label"
                ],
                "纠错封存复测期",
            )

    def test_v2_development_artifact_declares_corrected_accounting(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = run_development(
                repo_root=root,
                contract_path="configs/research/permanent_portfolio_v2.yaml",
                market_frame_fixture=_v2_market_history(),
            )
            artifact = json.loads(
                Path(state["development_artifact"]).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["study_id"], "permanent_portfolio_v2")
            self.assertEqual(
                state["accounting_version"], "cash_distributions_v2"
            )
            self.assertEqual(
                artifact["evidence_class"],
                "bug_corrected_sealed_retest",
            )
            self.assertIn(
                "data/research/permanent_portfolio/v2/results/development",
                state["development_artifact"],
            )
    def test_v2_rejects_legacy_market_accounting(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v2.yaml")

        with self.assertRaisesRegex(ValueError, "market_accounting"):
            _assert_market_accounting(
                contract,
                {"schema_version": 1, "accounting_version": None},
            )
    def test_v2_uses_a_separate_study_root(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v2.yaml")
        with TemporaryDirectory() as tmp:
            root = _study_root(Path(tmp), contract=contract)

            self.assertEqual(
                root,
                Path(tmp).resolve() / "data/research/permanent_portfolio/v2",
            )

    def test_momentum_ignores_non_open_prelisting_placeholders(self) -> None:
        rows: list[dict[str, object]] = []
        for role, code in ROLE_CODES.items():
            rows.extend(
                [
                    {
                        "trade_date": "20170103",
                        "role": role,
                        "code": code,
                        "adjusted_close": 10.0,
                        "is_open": role != "bond",
                    },
                    {
                        "trade_date": "20180103",
                        "role": role,
                        "code": code,
                        "adjusted_close": 11.0,
                        "is_open": True,
                    },
                ]
            )

        observations = _momentum_observations(
            pd.DataFrame(rows),
            as_of="20180103",
        )

        bond_lookbacks = set(
            observations.loc[
                observations["role"].eq("bond"), "months_ago"
            ]
        )
        self.assertNotIn(12, bond_lookbacks)
    def test_history_merge_uses_development_before_boundary_and_holdout_after(
        self,
    ) -> None:
        development = pd.DataFrame(
            [
                {"trade_date": "20241231", "role": "equity", "close": 1.0},
                {"trade_date": "20250102", "role": "equity", "close": 2.0},
            ]
        )
        holdout = pd.DataFrame(
            [
                {"trade_date": "20241231", "role": "equity", "close": 9.0},
                {"trade_date": "20250102", "role": "equity", "close": 3.0},
            ]
        )

        result = merge_historical_market(
            development,
            holdout,
            holdout_start="20250101",
        )

        self.assertEqual(result["trade_date"].tolist(), ["20241231", "20250102"])
        self.assertEqual(result["close"].tolist(), [1.0, 3.0])

    def test_development_rejects_rows_from_holdout(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "development_window"):
                run_development(
                    repo_root=Path(tmp),
                    contract_path=Path(
                        "configs/research/permanent_portfolio_v1.yaml"
                    ),
                    market_frame_fixture=pd.DataFrame(
                        [{"trade_date": "20250102"}]
                    ),
                )

    def test_evaluation_contains_two_strategies_and_three_baselines(self) -> None:
        contract = load_contract(
            "configs/research/permanent_portfolio_v1.yaml"
        )

        result = evaluate_window(
            _market_history(),
            contract=contract,
            start_date="20180102",
            end_date="20180105",
        )

        self.assertEqual(
            set(result["portfolios"]),
            {
                "fixed",
                "dynamic",
                "equity_buy_hold",
                "equal_weight_buy_hold",
                "cash_buy_hold",
            },
        )
        for portfolio in result["portfolios"].values():
            self.assertIn("metrics", portfolio)
            self.assertGreater(len(portfolio["series"]), 0)
            self.assertGreater(len(portfolio["nav"]), 0)

    def test_holdout_open_is_exclusive(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            development, evidence = _write_bound_development(root)

            marker = open_holdout_once(
                root,
                development,
                expected_sha256=evidence["artifact_sha256"],
                expected_contract_sha256=evidence["contract_sha256"],
                expected_market_bundle_sha256=evidence[
                    "market_bundle_sha256"
                ],
                expected_code_sha256=evidence["code_sha256"],
                expected_git_revision=evidence["git_revision"],
            )

            self.assertEqual(marker["status"], "holdout_opened")
            with self.assertRaisesRegex(ValueError, "holdout_already_opened"):
                open_holdout_once(
                    root,
                    development,
                    expected_sha256=evidence["artifact_sha256"],
                    expected_contract_sha256=evidence["contract_sha256"],
                    expected_market_bundle_sha256=evidence[
                        "market_bundle_sha256"
                    ],
                    expected_code_sha256=evidence["code_sha256"],
                    expected_git_revision=evidence["git_revision"],
                )

    def test_holdout_rejects_tampered_development_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            development, evidence = _write_bound_development(root)
            payload = json.loads(development.read_text(encoding="utf-8"))
            development.write_text(
                json.dumps(
                    {
                        **payload,
                        "evidence": "tampered",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "development_binding"):
                open_holdout_once(
                    root,
                    development,
                    expected_sha256=evidence["artifact_sha256"],
                    expected_contract_sha256=evidence["contract_sha256"],
                    expected_market_bundle_sha256=evidence[
                        "market_bundle_sha256"
                    ],
                    expected_code_sha256=evidence["code_sha256"],
                    expected_git_revision=evidence["git_revision"],
                )

    def test_holdout_rejects_artifact_not_recorded_in_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            development, evidence = _write_bound_development(root)
            state_path = root / "manifests/state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["development_artifact"] = str(root / "other.json")
            unsigned = dict(state)
            unsigned.pop("state_sha256")
            state["state_sha256"] = canonical_hash(unsigned)
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "development_state_binding"):
                open_holdout_once(
                    root,
                    development,
                    expected_sha256=evidence["artifact_sha256"],
                    expected_contract_sha256=evidence["contract_sha256"],
                    expected_market_bundle_sha256=evidence[
                        "market_bundle_sha256"
                    ],
                    expected_code_sha256=evidence["code_sha256"],
                    expected_git_revision=evidence["git_revision"],
                )

    def test_holdout_rejects_changed_contract_or_code(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            development, evidence = _write_bound_development(root)

            with self.assertRaisesRegex(ValueError, "development_state_binding"):
                open_holdout_once(
                    root,
                    development,
                    expected_sha256=evidence["artifact_sha256"],
                    expected_contract_sha256="0" * 64,
                    expected_market_bundle_sha256=evidence[
                        "market_bundle_sha256"
                    ],
                    expected_code_sha256="1" * 64,
                    expected_git_revision=evidence["git_revision"],
                )


if __name__ == "__main__":
    unittest.main()
