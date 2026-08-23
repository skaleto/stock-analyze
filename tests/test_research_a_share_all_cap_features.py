import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.config import load_config
from stock_analyze.research.a_share_all_cap_contract import (
    load_all_cap_contract,
    parse_all_cap_contract,
)
from stock_analyze.research.a_share_all_cap_features import (
    attach_all_cap_membership,
    build_decision_calendar,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "configs/research/a_share_all_cap_v2.yaml"


def _membership_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "review_date": "20240628",
        "effective_date": "20240701",
        "code": "000001.SZ",
        "eligible": True,
        "exclusion_reasons": "",
        "size_rank": 1,
        "raw_sleeve": "large",
        "stable_sleeve": "large",
        "total_mv": 1_000_000.0,
        "circ_mv": 900_000.0,
        "total_mv_source_date": "20240628",
        "avg_amount_252": 100_000.0,
        "avg_amount_source_date": "20240628",
        "non_trading_days_252": 0,
        "industry_l1": "801010",
        "industry_l2": "801011",
        "industry_l3": "801012",
        "industry_source_date": "20240101",
        "status_source": "baostock",
        "universe_contract_version": "a-share-all-cap-universe-v1",
    }
    row.update(overrides)
    return row


class AShareAllCapFeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_all_cap_contract(CONTRACT_PATH)

    def test_decision_dates_are_strategy_and_sleeve_specific(self) -> None:
        open_dates = pd.bdate_range("2024-01-02", periods=41).strftime("%Y%m%d")

        calendar = build_decision_calendar(open_dates, self.contract)

        expected_steps = {
            ("claude", "large"): 20,
            ("claude", "mid"): 20,
            ("claude", "small"): 20,
            ("claude", "micro"): 20,
            ("codex", "large"): 5,
            ("codex", "mid"): 5,
            ("codex", "small"): 10,
            ("codex", "micro"): 20,
        }
        positions = {value: index for index, value in enumerate(open_dates)}
        for key, step in expected_steps.items():
            agent, sleeve = key
            dates = calendar.loc[
                calendar["agent"].eq(agent)
                & calendar["stable_sleeve"].eq(sleeve),
                "trade_date",
            ].tolist()
            observed = {
                positions[right] - positions[left]
                for left, right in zip(dates, dates[1:])
            }
            self.assertEqual(observed, {step}, key)
        for column in ("trade_date", "agent", "stable_sleeve"):
            self.assertIsInstance(calendar[column].dtype, pd.StringDtype)

    def test_decision_calendar_rejects_forged_contract(self) -> None:
        open_dates = ["20240102", "20240103"]
        forged_contract = load_config(
            CONTRACT_PATH,
            apply_migrations=False,
        )
        forged_contract["candidates"]["claude"]["decision_interval_sessions"][
            "large"
        ] = 99

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_decision_calendar_contract",
        ):
            build_decision_calendar(open_dates, forged_contract)

    def test_decision_calendar_rejects_incomplete_intervals(self) -> None:
        cases = {
            "missing_agent": ("candidates", "codex"),
            "missing_interval_map": (
                "candidates",
                "claude",
                "decision_interval_sessions",
            ),
            "missing_sleeve": (
                "candidates",
                "claude",
                "decision_interval_sessions",
                "large",
            ),
        }
        for name, path in cases.items():
            with self.subTest(name=name):
                payload = load_config(
                    CONTRACT_PATH,
                    apply_migrations=False,
                )
                target = payload
                for key in path[:-1]:
                    target = target[key]
                del target[path[-1]]
                contract = parse_all_cap_contract(payload)

                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_decision_calendar_contract",
                ):
                    build_decision_calendar(["20240102"], contract)

    def test_decision_calendar_rejects_non_positive_integer_intervals(self) -> None:
        for invalid in (0, -1, True, 1.5, "20"):
            with self.subTest(invalid=invalid):
                payload = load_config(
                    CONTRACT_PATH,
                    apply_migrations=False,
                )
                payload["candidates"]["claude"]["decision_interval_sessions"][
                    "large"
                ] = invalid
                contract = parse_all_cap_contract(payload)

                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_decision_calendar_contract",
                ):
                    build_decision_calendar(["20240102"], contract)

    def test_membership_starts_on_effective_date_and_keeps_complete_evidence(self) -> None:
        features = pd.DataFrame(
            [
                {"code": "000001", "trade_date": "20240628", "signal": 1.0},
                {"code": "000001", "trade_date": "20240701", "signal": 2.0},
            ]
        )
        membership = pd.DataFrame([_membership_row()])

        attached = attach_all_cap_membership(
            features,
            membership,
            contract=self.contract,
        )

        self.assertEqual(attached.frame["trade_date"].tolist(), ["20240701"])
        row = attached.frame.iloc[0]
        self.assertEqual(row["account_id"], "large")
        self.assertEqual(row["research_scope"], "large")
        self.assertEqual(row["benchmark_code"], "000300.SH")
        self.assertEqual(row["benchmark"], "000300.SH")
        self.assertEqual(row["membership_snapshot"], "20240628")
        self.assertEqual(int(row["size_rank"]), 1)
        for column in (
            "review_date",
            "effective_date",
            "eligible",
            "exclusion_reasons",
            "raw_sleeve",
            "stable_sleeve",
            "total_mv",
            "circ_mv",
            "total_mv_source_date",
            "avg_amount_252",
            "avg_amount_source_date",
            "non_trading_days_252",
            "industry_l1",
            "industry_l2",
            "industry_l3",
            "industry_source_date",
            "status_source",
            "membership_contract_version",
        ):
            self.assertIn(column, attached.frame.columns)
        self.assertEqual(attached.metadata["membership_source"], "all_cap_quarterly")
        self.assertTrue(attached.metadata["unbiased_universe"])
        for column in (
            "code",
            "trade_date",
            "review_date",
            "effective_date",
            "membership_snapshot",
            "total_mv_source_date",
            "avg_amount_source_date",
            "industry_source_date",
        ):
            self.assertIsInstance(attached.frame[column].dtype, pd.StringDtype)

    def test_membership_uses_latest_effective_snapshot(self) -> None:
        features = pd.DataFrame(
            [
                {"code": "000001", "trade_date": "20240930"},
                {"code": "000001", "trade_date": "20241008"},
            ]
        )
        membership = pd.DataFrame(
            [
                _membership_row(),
                _membership_row(
                    review_date="20240930",
                    effective_date="20241008",
                    size_rank=350,
                    raw_sleeve="mid",
                    stable_sleeve="mid",
                    total_mv_source_date="20240930",
                    avg_amount_source_date="20240930",
                ),
            ]
        )

        result = attach_all_cap_membership(
            features,
            membership,
            contract=self.contract,
        ).frame

        self.assertEqual(
            list(zip(result["trade_date"], result["stable_sleeve"])),
            [("20240930", "large"), ("20241008", "mid")],
        )

    def test_rejects_duplicate_code_effective_date(self) -> None:
        features = pd.DataFrame([{"code": "000001", "trade_date": "20240701"}])
        membership = pd.DataFrame(
            [_membership_row(), _membership_row(code="000001")]
        )

        with self.assertRaisesRegex(
            ValueError,
            "all_cap_membership_duplicate",
        ):
            attach_all_cap_membership(
                features,
                membership,
                contract=self.contract,
            )

    def test_rejects_source_date_later_than_signal_or_trade_date(self) -> None:
        membership = pd.DataFrame(
            [_membership_row(total_mv_source_date="20240702")]
        )
        for features in (
            pd.DataFrame([{"code": "000001", "trade_date": "20240701"}]),
            pd.DataFrame(
                [{
                    "code": "000001",
                    "trade_date": "20240702",
                    "signal_date": "20240701",
                }]
            ),
        ):
            with self.subTest(columns=tuple(features.columns)):
                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_membership_future_source",
                ):
                    attach_all_cap_membership(
                        features,
                        membership,
                        contract=self.contract,
                    )

    def test_rejects_missing_feature_or_membership_fields(self) -> None:
        membership = pd.DataFrame([_membership_row()])
        with self.assertRaisesRegex(ValueError, "all_cap_feature_schema"):
            attach_all_cap_membership(
                pd.DataFrame([{"code": "000001"}]),
                membership,
                contract=self.contract,
            )

        with self.assertRaisesRegex(ValueError, "all_cap_membership_schema"):
            attach_all_cap_membership(
                pd.DataFrame([{"code": "000001", "trade_date": "20240701"}]),
                membership.drop(columns=["status_source"]),
                contract=self.contract,
            )


if __name__ == "__main__":
    unittest.main()
