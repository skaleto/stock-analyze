from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest import mock

import pandas as pd

import stock_analyze.research.permanent_portfolio.paper as paper_module
from stock_analyze.research.permanent_portfolio.contract import (
    canonical_hash,
    load_contract,
)
from stock_analyze.research.permanent_portfolio.paper import (
    account_paths,
    run_paper_day,
)
from stock_analyze.research.permanent_portfolio.workflow import _code_evidence


ROLE_CODES = {
    "equity": "510300.SH",
    "bond": "511260.SH",
    "cash": "511880.SH",
    "gold": "518880.SH",
}


def _market(*forward_dates: str) -> pd.DataFrame:
    dates = list(
        pd.date_range(
            "2016-12-30",
            "2017-12-29",
            freq=pd.offsets.BusinessMonthEnd(),
        )
    )
    dates.extend(pd.Timestamp(value) for value in forward_dates)
    rows = []
    for index, day in enumerate(dates):
        for role, code in ROLE_CODES.items():
            close = 10.0 + index
            rows.append(
                {
                    "trade_date": day.strftime("%Y%m%d"),
                    "role": role,
                    "code": code,
                    "open": close,
                    "close": close,
                    "adjusted_close": close,
                    "adj_factor": 1.0,
                    "is_open": True,
                }
            )
    return pd.DataFrame(rows)


def _prepare_paper_gate(
    root: Path,
    *,
    contract_path: str = "configs/research/permanent_portfolio_v1.yaml",
) -> dict[str, object]:
    contract = load_contract(contract_path)
    version = contract.study_id.rsplit("_", 1)[-1]
    code_evidence = _code_evidence()
    market_bundle_sha256 = "c" * 64
    development_sha256 = "d" * 64
    holdout_marker_sha256 = "e" * 64
    study_root = root / f"data/research/permanent_portfolio/{version}"
    holdout_payload = {
        "status": "holdout_complete",
        "contract_sha256": canonical_hash(contract.raw),
        "market_bundle_sha256": market_bundle_sha256,
        "development_sha256": development_sha256,
        "holdout_marker_sha256": holdout_marker_sha256,
        **code_evidence,
    }
    holdout_sha256 = canonical_hash(holdout_payload)
    holdout_path = (
        study_root / "results/holdout" / holdout_sha256 / "result.json"
    )
    holdout_path.parent.mkdir(parents=True)
    holdout_path.write_text(
        json.dumps(
            {
                **holdout_payload,
                "artifact_sha256": holdout_sha256,
            }
        ),
        encoding="utf-8",
    )
    state = {
        "schema_version": int(contract.raw.get("schema_version") or 1),
        "status": "holdout_complete",
        "holdout_end": "20180101",
        "holdout_artifact": str(holdout_path.resolve()),
        "holdout_sha256": holdout_sha256,
        "development_sha256": development_sha256,
        "holdout_marker_sha256": holdout_marker_sha256,
        "contract_sha256": canonical_hash(contract.raw),
        "market_bundle_sha256": market_bundle_sha256,
        **code_evidence,
    }
    state["state_sha256"] = canonical_hash(state)
    manifests = study_root / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    dashboard = {
        "schema_version": int(contract.raw.get("schema_version") or 1),
        "study": state,
        "development": {},
        "holdout": {},
        "forward": {"status": "unavailable"},
    }
    dashboard["dashboard_sha256"] = canonical_hash(dashboard)
    report = (
        root
        / f"reports/research/permanent_portfolio/{version}/dashboard.json"
    )
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(dashboard), encoding="utf-8")
    return {
        "market_bundle_sha256": market_bundle_sha256,
        "schema_version": int(contract.raw.get("schema_version") or 1),
        "accounting_version": contract.accounting_version,
    }


class PermanentPortfolioPaperTests(unittest.TestCase):
    def test_accounts_are_isolated_under_research(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = account_paths(Path(tmp))

            self.assertTrue(
                str(paths["fixed"]).endswith(
                    "data/research/paper_portfolios/permanent_fixed_v1"
                )
            )
            self.assertTrue(
                str(paths["dynamic"]).endswith(
                    "data/research/paper_portfolios/permanent_dynamic_v1"
                )
            )

    def test_v2_accounts_do_not_reuse_v1_ledgers(self) -> None:
        with TemporaryDirectory() as tmp:
            v1 = account_paths(Path(tmp))
            v2 = account_paths(
                Path(tmp), study_id="permanent_portfolio_v2"
            )

            self.assertNotEqual(v2, v1)
            self.assertTrue(str(v2["fixed"]).endswith("permanent_fixed_v2"))
            self.assertTrue(str(v2["dynamic"]).endswith("permanent_dynamic_v2"))

    def test_v2_paper_rejects_legacy_market_accounting_before_mutation(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = _prepare_paper_gate(
                root,
                contract_path="configs/research/permanent_portfolio_v2.yaml",
            )
            evidence["schema_version"] = 1
            evidence["accounting_version"] = None

            with (
                mock.patch(
                    "stock_analyze.research.permanent_portfolio.paper._latest_market_with_evidence",
                    return_value=(
                        _market("2018-01-02", "2018-01-03"),
                        evidence,
                    ),
                ),
                self.assertRaisesRegex(ValueError, "market_accounting"),
            ):
                run_paper_day(
                    root,
                    as_of="2018-01-03",
                    contract_path=(
                        "configs/research/permanent_portfolio_v2.yaml"
                    ),
                )

            for path in account_paths(
                root,
                study_id="permanent_portfolio_v2",
            ).values():
                self.assertFalse((path / "runs.csv").exists())

    def test_same_day_run_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            first = run_paper_day(
                root,
                as_of="2026-09-01",
                fixture_mode=True,
            )
            second = run_paper_day(
                root,
                as_of="2026-09-01",
                fixture_mode=True,
            )

            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(second["status"], "already_complete")
            for path in account_paths(root).values():
                self.assertTrue((path / "runs.csv").is_file())

    def test_later_day_continues_account_and_appends_ledgers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = _prepare_paper_gate(root)

            with mock.patch(
                "stock_analyze.research.permanent_portfolio.paper._latest_market_with_evidence",
                return_value=(
                    _market("2018-01-02", "2018-01-03"),
                    evidence,
                ),
            ):
                run_paper_day(root, as_of="2018-01-03")

            fixed_path = account_paths(root)["fixed"]
            first_trades = pd.read_csv(fixed_path / "trades.csv", dtype=str)
            first_state = json.loads(
                (fixed_path / "state.json").read_text(encoding="utf-8")
            )

            with mock.patch(
                "stock_analyze.research.permanent_portfolio.paper._latest_market_with_evidence",
                return_value=(_market("2018-01-04"), evidence),
            ):
                run_paper_day(root, as_of="2018-01-04")

            second_trades = pd.read_csv(fixed_path / "trades.csv", dtype=str)
            nav = pd.read_csv(fixed_path / "daily_nav.csv", dtype=str)
            second_state = json.loads(
                (fixed_path / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                second_trades.to_dict(orient="records"),
                first_trades.to_dict(orient="records"),
            )
            self.assertEqual(
                nav["date"].tolist(),
                ["20180102", "20180103", "20180104"],
            )
            self.assertEqual(
                second_state["accounts"]["fixed"]["cash"],
                first_state["accounts"]["fixed"]["cash"],
            )

    def test_strategy_failure_does_not_partially_commit_other_account(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = _prepare_paper_gate(root)
            original = paper_module._paper_result

            def fail_dynamic(**kwargs):
                if kwargs["strategy"] == "dynamic":
                    raise ValueError("injected_dynamic_failure")
                return original(**kwargs)

            with (
                mock.patch(
                    "stock_analyze.research.permanent_portfolio.paper._latest_market_with_evidence",
                    return_value=(
                        _market("2018-01-02", "2018-01-03"),
                        evidence,
                    ),
                ),
                mock.patch(
                    "stock_analyze.research.permanent_portfolio.paper._paper_result",
                    side_effect=fail_dynamic,
                ),
                self.assertRaisesRegex(ValueError, "injected_dynamic_failure"),
            ):
                run_paper_day(root, as_of="2018-01-03")

            for path in account_paths(root).values():
                self.assertFalse((path / "runs.csv").exists())
                self.assertFalse((path / "trades.csv").exists())

    def test_tampered_study_state_is_rejected_before_account_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = _prepare_paper_gate(root)
            state_path = (
                root
                / "data/research/permanent_portfolio/v1/manifests/state.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["holdout_end"] = "20180102"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with (
                mock.patch(
                    "stock_analyze.research.permanent_portfolio.paper._latest_market_with_evidence",
                    return_value=(
                        _market("2018-01-02", "2018-01-03"),
                        evidence,
                    ),
                ),
                self.assertRaisesRegex(ValueError, "paper_binding"),
            ):
                run_paper_day(root, as_of="2018-01-03")

            for path in account_paths(root).values():
                self.assertFalse((path / "runs.csv").exists())
                self.assertFalse((path / "trades.csv").exists())


if __name__ == "__main__":
    unittest.main()
