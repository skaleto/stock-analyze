from __future__ import annotations

from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.capital_actions_backfill import (
    _fetch_pages,
    capital_action_partitions,
    load_capital_action_events,
    run_capital_actions_backfill,
)
from stock_analyze.research.capital_actions_study import (
    build_return_panel,
    evaluate_panel,
    load_contract,
    select_diagnostic_events,
    select_eligible_events,
)


CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "configs" / "research" / "capital_actions_study.yaml"
)


class PagedClient:
    def __init__(self) -> None:
        self.offsets: list[tuple[str, int]] = []

    def repurchase(self, **kwargs):
        self.offsets.append(("repurchase", kwargs["offset"]))
        count = 2000 if kwargs["offset"] == 0 else 1
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "ann_date": kwargs["start_date"],
            "end_date": "20200101", "proc": "完成", "exp_date": "",
            "vol": 10.0, "amount": 100.0, "high_limit": 10.0,
            "low_limit": 1.0,
        }] * count)

    def stk_holdertrade(self, **kwargs):
        self.offsets.append(("stk_holdertrade", kwargs["offset"]))
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "ann_date": kwargs["start_date"],
            "holder_name": "A", "holder_type": "G", "in_de": "IN",
            "change_vol": 10.0, "change_ratio": 0.2,
            "after_share": 20.0, "after_ratio": 0.4,
            "avg_price": 10.0, "total_share": 100.0,
        }])


class StaticClient:
    def __init__(
        self,
        repurchase: pd.DataFrame | None = None,
        holder: pd.DataFrame | None = None,
    ) -> None:
        self.repurchase_frame = repurchase
        self.holder_frame = holder

    def repurchase(self, **kwargs):
        if self.repurchase_frame is not None:
            return self.repurchase_frame.copy()
        return pd.DataFrame(columns=[
            "ts_code", "ann_date", "end_date", "proc", "exp_date",
            "vol", "amount", "high_limit", "low_limit",
        ])

    def stk_holdertrade(self, **kwargs):
        if self.holder_frame is not None:
            return self.holder_frame.copy()
        return pd.DataFrame(columns=[
            "ts_code", "ann_date", "holder_name", "holder_type",
            "in_de", "change_vol", "change_ratio", "after_share",
            "after_ratio", "avg_price", "total_share",
        ])


def relaxed_contract():
    return replace(
        load_contract(CONTRACT),
        minimum_total_events=1,
        minimum_unique_securities=1,
        minimum_event_years=1,
        minimum_scope_events=1,
        bootstrap_samples=25,
    )


def outcome_panel(
    family: str,
    values: list[float],
    *,
    scopes: tuple[str, ...] = ("hs300", "zz500"),
) -> pd.DataFrame:
    rows = []
    for scope in scopes:
        for index, value in enumerate(values):
            for horizon in (5, 20, 60):
                rows.append({
                    "event_id": f"{family}-{index}",
                    "family": family,
                    "code": f"{index + 1:06d}",
                    "event_year": str(2020 + index % 3),
                    "account_scope": scope,
                    "horizon": horizon,
                    "active_return": value + 0.0021,
                    "net_active_return": value,
                    "stress_net_active_return": value - 0.00105,
                })
    return pd.DataFrame(rows)


class CapitalActionsBackfillTest(unittest.TestCase):
    def test_two_endpoints_are_monthly_partitioned(self):
        parts = capital_action_partitions("2020-01-01", "2020-02-02")
        self.assertEqual(len(parts), 4)
        self.assertEqual({part[0] for part in parts}, {"repurchase", "stk_holdertrade"})

    def test_provider_pages_until_short_page(self):
        client = PagedClient()
        frame = _fetch_pages(client, "repurchase", "20200101", "20200131")
        self.assertEqual(len(frame), 2001)
        self.assertEqual(client.offsets, [("repurchase", 0), ("repurchase", 2000)])

    def test_backfill_resumes_after_one_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PagedClient()
            first = run_capital_actions_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-01-31",
                max_partitions=1,
            )
            second = run_capital_actions_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-01-31",
                max_partitions=1,
            )
        self.assertEqual(first["completed_partitions"], 1)
        self.assertEqual(second["completed_partitions"], 2)

    def test_partition_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_capital_actions_backfill(
                tmp, StaticClient(), start_date="2020-01-01",
                end_date="2020-01-31",
            )
            path = (
                Path(tmp) / "data/research/capital_actions_structured/v1"
                / "repurchase/20200101_20200131.parquet"
            )
            path.write_bytes(path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "partition_tampered"):
                load_capital_action_events(
                    tmp, start_date="2020-01-01", end_date="2020-01-31"
                )

    def test_repurchase_materiality_converts_total_mv_to_yuan(self):
        repurchase = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "ann_date": "20200102",
                "end_date": f"2020010{index}", "proc": "完成", "exp_date": "",
                "vol": 1.0, "amount": 2_500_000.0,
                "high_limit": 1.0, "low_limit": 1.0,
            }
            for index in (2, 3)
        ])
        with tempfile.TemporaryDirectory() as tmp:
            daily = Path(tmp) / "data/shared/backtest_cache/daily_basic"
            daily.mkdir(parents=True)
            pd.DataFrame([{
                "ts_code": "000001.SZ", "total_mv": 100_000.0,
            }]).to_csv(daily / "2020-01-02.csv", index=False)
            run_capital_actions_backfill(
                tmp, StaticClient(repurchase=repurchase),
                start_date="2020-01-01", end_date="2020-01-31",
            )
            events, _ = load_capital_action_events(
                tmp, start_date="2020-01-01", end_date="2020-01-31"
            )
        event = events.loc[events["family"].eq("repurchase_completed")].iloc[0]
        self.assertAlmostEqual(event["materiality"], 0.005)
        self.assertEqual(len(select_eligible_events(events, load_contract(CONTRACT))), 1)

    def test_holder_rows_aggregate_before_threshold_and_decrease_is_diagnostic(self):
        holder = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "ann_date": "20200102",
                "holder_name": name, "holder_type": "G", "in_de": direction,
                "change_vol": 1.0, "change_ratio": ratio,
                "after_share": 2.0, "after_ratio": 1.0,
                "avg_price": 10.0, "total_share": 100.0,
            }
            for name, direction, ratio in (
                ("A", "IN", 0.06), ("A", "IN", 0.05),
                ("C", "DE", 0.50),
            )
        ])
        with tempfile.TemporaryDirectory() as tmp:
            run_capital_actions_backfill(
                tmp, StaticClient(holder=holder),
                start_date="2020-01-01", end_date="2020-01-31",
            )
            events, _ = load_capital_action_events(
                tmp, start_date="2020-01-01", end_date="2020-01-31"
            )
        increase = events.loc[events["family"].eq("holder_management_increase")].iloc[0]
        decrease = events.loc[events["family"].eq("holder_management_decrease")].iloc[0]
        self.assertAlmostEqual(increase["materiality"], 0.0011)
        self.assertFalse(bool(decrease["eligible"]))
        selected = select_eligible_events(events, load_contract(CONTRACT))
        self.assertEqual(selected["family"].tolist(), ["holder_management_increase"])

    def test_proposal_and_approval_never_become_completed_candidates(self):
        rows = []
        for process in ("预案", "股东大会通过"):
            rows.append({
                "ts_code": "000001.SZ", "ann_date": "20200102",
                "end_date": "20200102", "proc": process, "exp_date": "",
                "vol": 1.0, "amount": 5_000_000.0,
                "high_limit": 1.0, "low_limit": 1.0,
            })
        with tempfile.TemporaryDirectory() as tmp:
            run_capital_actions_backfill(
                tmp, StaticClient(repurchase=pd.DataFrame(rows)),
                start_date="2020-01-01", end_date="2020-01-31",
            )
            events, _ = load_capital_action_events(
                tmp, start_date="2020-01-01", end_date="2020-01-31"
            )
        self.assertEqual(set(events["family"]), {"repurchase_plan"})
        self.assertTrue(select_eligible_events(events, load_contract(CONTRACT)).empty)


class CapitalActionsStudyTest(unittest.TestCase):
    def test_contract_rejects_changed_primary_horizon_or_windows(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for field, value in (
            ("primary_horizon", 60),
            ("historical_diagnostic_start", "20240101"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                changed = {**payload, field: value}
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(
                    ValueError, f"capital_actions_contract_frozen:{field}"
                ):
                    load_contract(path)

    def test_next_open_never_uses_same_day_open(self):
        contract = relaxed_contract()
        events = pd.DataFrame([{
            "event_id": "e1", "family": "repurchase_completed",
            "code": "000001", "ann_date": "20200102",
            "eligible": True, "materiality": 0.01,
        }])
        dates = pd.bdate_range("2020-01-02", periods=65).strftime("%Y%m%d")
        prices = pd.DataFrame({
            "account_id": "hs300", "code": "000001",
            "trade_date": dates, "open": [1.0] + [2.0] * 64,
            "close": [2.0] * 65,
        })
        benchmark = pd.DataFrame({
            "trade_date": dates, "open": 1.0, "close": 1.0,
        })
        panel = build_return_panel(
            events, prices, {"hs300": benchmark}, contract
        )
        self.assertEqual(set(panel["entry_date"]), {dates[1]})
        self.assertTrue((panel["security_return"] == 0.0).all())

    def test_event_cannot_borrow_future_index_membership(self):
        contract = relaxed_contract()
        events = pd.DataFrame([{
            "event_id": "e1", "family": "repurchase_completed",
            "code": "000001", "ann_date": "20200102",
            "eligible": True, "materiality": 0.01,
        }])
        dates = pd.bdate_range("2020-01-02", periods=65).strftime("%Y%m%d")
        prices = pd.DataFrame({
            "account_id": [pd.NA] * 20 + ["hs300"] * 45,
            "code": "000001", "trade_date": dates,
            "open": 1.0, "close": 1.0,
        })
        benchmark = pd.DataFrame({
            "trade_date": dates, "open": 1.0, "close": 1.0,
        })
        panel = build_return_panel(
            events, prices, {"hs300": benchmark}, contract
        )
        self.assertTrue(panel.empty)

    def test_leaving_index_does_not_compress_holding_horizon(self):
        contract = relaxed_contract()
        events = pd.DataFrame([{
            "event_id": "e1", "family": "repurchase_completed",
            "code": "000001", "ann_date": "20200102",
            "eligible": True, "materiality": 0.01,
        }])
        dates = pd.bdate_range("2020-01-02", periods=65).strftime("%Y%m%d")
        prices = pd.DataFrame({
            "account_id": [pd.NA, "hs300"] + [pd.NA] * 63,
            "code": "000001", "trade_date": dates,
            "open": 1.0, "close": 1.0,
        })
        benchmark = pd.DataFrame({
            "trade_date": dates, "open": 1.0, "close": 1.0,
        })
        panel = build_return_panel(
            events, prices, {"hs300": benchmark}, contract
        )
        sixty = panel.loc[panel["horizon"].eq(60)].iloc[0]
        self.assertEqual(sixty["entry_date"], dates[1])
        self.assertEqual(sixty["exit_date"], dates[60])

    def test_family_evidence_is_independent_and_cannot_be_borrowed(self):
        contract = relaxed_contract()
        enough = outcome_panel("repurchase_completed", [0.02, 0.02, 0.02])
        result = evaluate_panel(enough, {"complete": True}, contract)
        by_family = {row["family"]: row for row in result["families"]}
        self.assertNotEqual(by_family["repurchase_completed"]["status"], "insufficient_data")
        self.assertEqual(
            by_family["holder_company_increase"]["status"],
            "insufficient_data",
        )
        self.assertEqual(by_family["holder_company_increase"]["evidence"]["events"], 0)

    def test_negative_median_fails_even_when_mean_is_positive(self):
        contract = relaxed_contract()
        panel = outcome_panel(
            "repurchase_completed", [-0.01, -0.01, 0.20]
        )
        result = evaluate_panel(panel, {"complete": True}, contract)
        family = result["families"][0]
        primary = next(row for row in family["horizons"] if row["is_primary"])
        self.assertGreater(primary["mean_net_active_return"], 0.0)
        self.assertLess(primary["median_net_active_return"], 0.0)
        self.assertFalse(primary["checks"]["median_positive"])
        self.assertEqual(family["status"], "falsified")

    def test_one_scope_only_is_insufficient(self):
        contract = relaxed_contract()
        panel = outcome_panel(
            "repurchase_completed", [0.02, 0.02, 0.02], scopes=("hs300",)
        )
        family = evaluate_panel(panel, {"complete": True}, contract)["families"][0]
        self.assertEqual(family["status"], "insufficient_data")
        self.assertFalse(family["evidence_checks"]["scopes"])

    def test_incomplete_backfill_forces_insufficient_data(self):
        contract = relaxed_contract()
        panel = outcome_panel("repurchase_completed", [0.02, 0.02, 0.02])
        result = evaluate_panel(panel, {"complete": False}, contract)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertTrue(all(
            row["status"] == "insufficient_data"
            for row in result["families"]
        ))

    def test_only_primary_horizon_controls_family_status(self):
        contract = relaxed_contract()
        panel = outcome_panel("repurchase_completed", [0.02, 0.02, 0.02])
        panel.loc[panel["horizon"].eq(5), [
            "active_return", "net_active_return", "stress_net_active_return",
        ]] = -0.02
        family = evaluate_panel(panel, {"complete": True}, contract)["families"][0]
        by_horizon = {row["horizon"]: row for row in family["horizons"]}
        self.assertFalse(by_horizon[5]["passed"])
        self.assertTrue(by_horizon[20]["passed"])
        self.assertEqual(family["status"], "transparent_baseline_candidate")

    def test_all_horizons_use_one_complete_event_cohort(self):
        contract = relaxed_contract()
        panel = outcome_panel("repurchase_completed", [0.02, 0.02, 0.02])
        panel = panel.loc[
            ~panel["event_id"].eq("repurchase_completed-2")
            | ~panel["horizon"].eq(20)
        ].copy()
        family = evaluate_panel(panel, {"complete": True}, contract)["families"][0]
        self.assertEqual(family["evidence"]["events"], 2)
        self.assertEqual(
            {row["events"] for row in family["horizons"]}, {2}
        )

    def test_diagnostic_families_cannot_change_candidate_decision(self):
        contract = relaxed_contract()
        candidate = outcome_panel(
            "repurchase_completed", [0.02, 0.02, 0.02]
        )
        diagnostic = outcome_panel(
            "holder_company_decrease", [0.50, 0.50, 0.50]
        )
        result = evaluate_panel(
            candidate,
            {"complete": True},
            contract,
            diagnostic_panel=diagnostic,
        )
        self.assertEqual(result["status"], "transparent_baseline_candidate")
        control = next(
            row for row in result["diagnostics"]
            if row["family"] == "holder_company_decrease"
        )
        self.assertEqual(control["status"], "diagnostic_only")
        self.assertFalse(control["candidate_eligible"])
        self.assertFalse(control["gate_applied"])
        self.assertNotIn("passed", control["horizons"][0])

    def test_diagnostic_selection_thresholds_decreases_but_keeps_controls(self):
        contract = load_contract(CONTRACT)
        events = pd.DataFrame([
            {
                "family": "holder_management_decrease",
                "materiality": 0.0009, "eligible": False,
            },
            {
                "family": "holder_management_decrease",
                "materiality": 0.0010, "eligible": False,
            },
            {
                "family": "repurchase_plan",
                "materiality": float("nan"), "eligible": False,
            },
            {
                "family": "repurchase_completed",
                "materiality": 0.50, "eligible": True,
            },
        ])
        selected = select_diagnostic_events(events, contract)
        self.assertEqual(
            selected["family"].tolist(),
            ["holder_management_decrease", "repurchase_plan"],
        )
        self.assertTrue(selected["eligible"].all())
