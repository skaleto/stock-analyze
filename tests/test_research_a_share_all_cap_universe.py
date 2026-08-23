from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_analyze.research import a_share_all_cap_universe as universe_module
from stock_analyze.research.a_share_all_cap_universe import (
    DAILY_HARD_STATUS_COLUMNS,
    MEMBERSHIP_COLUMNS,
    assign_stable_sleeve,
    build_daily_hard_status,
    build_review_membership,
    load_verified_all_cap_universe,
    materialize_all_cap_universe,
    raw_sleeve_for_rank,
)
from stock_analyze.research.a_share_all_cap_contract import (
    AllCapContract,
    SleeveContract,
)


EXPECTED_MEMBERSHIP_COLUMNS = (
    "review_date",
    "effective_date",
    "code",
    "eligible",
    "exclusion_reasons",
    "size_rank",
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
    "universe_contract_version",
)


class SleeveAssignmentTests(unittest.TestCase):
    boundaries = (300, 800, 1800, 3800)

    def test_membership_columns_are_the_exact_public_contract(self) -> None:
        self.assertEqual(MEMBERSHIP_COLUMNS, EXPECTED_MEMBERSHIP_COLUMNS)

    def test_raw_sleeves_include_unfunded_nano_watch(self) -> None:
        expected = {
            1: "large",
            300: "large",
            301: "mid",
            800: "mid",
            801: "small",
            1800: "small",
            1801: "micro",
            3800: "micro",
            3801: "nano_watch",
        }
        self.assertEqual(
            {
                rank: raw_sleeve_for_rank(rank, self.boundaries)
                for rank in expected
            },
            expected,
        )

    def test_previous_large_is_retained_only_inside_ten_percent_buffer(self) -> None:
        self.assertEqual(
            assign_stable_sleeve(
                size_rank=315,
                previous="large",
                boundaries=self.boundaries,
                buffer_fraction=0.10,
            ),
            "large",
        )
        self.assertEqual(
            assign_stable_sleeve(
                size_rank=331,
                previous="large",
                boundaries=self.boundaries,
                buffer_fraction=0.10,
            ),
            "mid",
        )

    def test_missing_or_unchanged_previous_sleeve_returns_raw(self) -> None:
        self.assertEqual(
            assign_stable_sleeve(
                size_rank=301,
                previous=None,
                boundaries=self.boundaries,
                buffer_fraction=0.10,
            ),
            "mid",
        )
        self.assertEqual(
            assign_stable_sleeve(
                size_rank=301,
                previous="mid",
                boundaries=self.boundaries,
                buffer_fraction=0.10,
            ),
            "mid",
        )

    def test_invalid_rank_boundaries_previous_or_buffer_fail_closed(self) -> None:
        cases = (
            {"size_rank": 0, "previous": None, "boundaries": self.boundaries, "buffer_fraction": 0.10},
            {"size_rank": 1.5, "previous": None, "boundaries": self.boundaries, "buffer_fraction": 0.10},
            {"size_rank": 1, "previous": None, "boundaries": (300, 300, 1800, 3800), "buffer_fraction": 0.10},
            {"size_rank": 1, "previous": "unknown", "boundaries": self.boundaries, "buffer_fraction": 0.10},
            {"size_rank": 1, "previous": None, "boundaries": self.boundaries, "buffer_fraction": -0.01},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "all_cap_universe_sleeve"):
                    assign_stable_sleeve(**kwargs)


def _contract(
    *,
    start: date = date(2024, 6, 24),
    end: date = date(2024, 7, 1),
    boundaries: tuple[int, int, int, int] = (300, 800, 1800, 3800),
    liquidity_lookback_sessions: int = 252,
) -> AllCapContract:
    raw = {
        "contract_version": 1,
        "universe": {
            "liquidity_lookback_sessions": liquidity_lookback_sessions,
            "maximum_non_trading_days": 60,
            "new_entry_minimum_amount_percentile": 0.20,
            "retention_minimum_amount_percentile": 0.10,
            "listing_age": {
                "main_board_days": 90,
                "chinext_days": 90,
                "star_days": 365,
                "bse_days": 730,
            },
        },
        "data_gates": {
            "critical_membership_coverage": 1.00,
            "daily_bar_coverage": 0.99,
            "daily_basic_coverage": 0.99,
            "adjustment_coverage": 0.98,
        },
        "storage": {"minimum_filesystem_free_fraction_after_publish": 0.15},
    }
    return AllCapContract(
        campaign_id="test_all_cap",
        development_start=start,
        development_end=end,
        holdout_start=date(2025, 1, 1),
        holdout_end=date(2025, 12, 31),
        holdout_policy="open_once_after_data_code_and_development_gates",
        size_boundaries=boundaries,
        boundary_buffer_fraction=0.10,
        sleeves=(
            SleeveContract("large", 1, boundaries[0], "L", 0.35),
            SleeveContract("mid", boundaries[0] + 1, boundaries[1], "M", 0.30),
            SleeveContract("small", boundaries[1] + 1, boundaries[2], "S", 0.25),
            SleeveContract("micro", boundaries[2] + 1, boundaries[3], "X", 0.10),
        ),
        raw=raw,
    )


def _date_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def _review_inputs(
    codes: list[str],
    *,
    review: date = date(2024, 6, 28),
    amounts: dict[str, float] | None = None,
    total_mv: dict[str, float] | None = None,
    list_dates: dict[str, date] | None = None,
    status_codes: set[str] | None = None,
    previous_codes: set[str] | None = None,
) -> dict[str, pd.DataFrame]:
    amount_values = amounts or {
        code: float((len(codes) - index) * 100)
        for index, code in enumerate(codes)
    }
    mv_values = total_mv or {
        code: float((len(codes) - index) * 1_000)
        for index, code in enumerate(codes)
    }
    listing = list_dates or {
        code: review - timedelta(days=1_000) for code in codes
    }
    status_present = set(codes) if status_codes is None else status_codes
    open_days = [review - timedelta(days=offset) for offset in (4, 3, 2, 1, 0)]
    next_open = review + timedelta(days=3)
    return {
        "trade_calendar": pd.DataFrame(
            [
                {"cal_date": _date_key(day), "is_open": "1"}
                for day in [*open_days, next_open]
            ]
        ),
        "stock_master": pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "list_date": _date_key(listing[code]),
                    "delist_date": "",
                    "list_status": "L",
                    "source_date": _date_key(review),
                }
                for code in codes
            ]
        ),
        "daily": pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": _date_key(day),
                    "amount": amount_values[code],
                }
                for day in open_days
                for code in codes
            ]
        ),
        "daily_basic": pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": _date_key(review),
                    "total_mv": mv_values[code],
                    "circ_mv": mv_values[code] * 0.8,
                }
                for code in codes
            ]
        ),
        "status": pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": _date_key(review),
                    "source_date": _date_key(review),
                    "is_st": "0",
                    "tradestatus": "1",
                    "is_delisting": "0",
                    "status_source": "baostock+suspend_d",
                }
                for code in codes
                if code in status_present
            ]
        ),
        "industry_membership": pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "l1_code": "801010.SI",
                    "l2_code": "801011.SI",
                    "l3_code": "850111.SI",
                    "in_date": "20200101",
                    "out_date": "",
                    "source_date": _date_key(review),
                }
                for code in codes
            ]
        ),
        "previous_membership": pd.DataFrame(
            [
                {
                    "review_date": "20240329",
                    "code": code,
                    "eligible": True,
                    "stable_sleeve": "large",
                }
                for code in sorted(previous_codes or set())
            ],
            columns=("review_date", "code", "eligible", "stable_sleeve"),
        ),
    }


class ReviewMembershipTests(unittest.TestCase):
    review = date(2024, 6, 28)
    contract = _contract()

    def test_excludes_future_listing_st_and_missing_status(self) -> None:
        codes = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]
        inputs = _review_inputs(
            codes,
            list_dates={
                codes[0]: self.review - timedelta(days=1_000),
                codes[1]: self.review + timedelta(days=1),
                codes[2]: self.review - timedelta(days=1_000),
                codes[3]: self.review - timedelta(days=1_000),
            },
            status_codes=set(codes[:3]),
        )
        inputs["status"].loc[
            inputs["status"]["ts_code"].eq("000003.SZ"), "is_st"
        ] = "1"

        result = build_review_membership(
            inputs,
            review_date=_date_key(self.review),
            contract=self.contract,
        ).set_index("code")

        self.assertIn("not_listed", result.loc["000002.SZ", "exclusion_reasons"])
        self.assertIn("st", result.loc["000003.SZ", "exclusion_reasons"])
        self.assertIn("status_missing", result.loc["000004.SZ", "exclusion_reasons"])
        self.assertFalse(result.loc[["000002.SZ", "000003.SZ", "000004.SZ"], "eligible"].any())

    def test_listing_age_uses_explicit_board_specific_calendar_thresholds(self) -> None:
        thresholds = {
            "600001.SH": 90,
            "600002.SH": 89,
            "300001.SZ": 90,
            "688001.SH": 365,
            "688002.SH": 364,
            "430001.BJ": 730,
            "430002.BJ": 729,
            "900001.SH": 1_000,
        }
        for code, age in thresholds.items():
            with self.subTest(code=code, age=age):
                inputs = _review_inputs(
                    [code],
                    list_dates={code: self.review - timedelta(days=age)},
                )
                row = build_review_membership(
                    inputs,
                    review_date=self.review,
                    contract=self.contract,
                ).iloc[0]
                if code in {"600002.SH", "688002.SH", "430002.BJ"}:
                    self.assertIn("listing_age", row["exclusion_reasons"])
                elif code == "900001.SH":
                    self.assertIn("unsupported_board", row["exclusion_reasons"])
                else:
                    self.assertTrue(row["eligible"], row["exclusion_reasons"])

    def test_liquidity_hysteresis_uses_top_80_and_top_90_with_code_ties(self) -> None:
        codes = [f"0000{index:02d}.SZ" for index in range(1, 11)]
        amounts = {
            **{code: float(110 - index * 10) for index, code in enumerate(codes[:7], 1)},
            codes[7]: 20.0,
            codes[8]: 20.0,
            codes[9]: 10.0,
        }
        new_result = build_review_membership(
            _review_inputs(codes, amounts=amounts),
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")
        self.assertTrue(new_result.loc[codes[7], "eligible"])
        self.assertIn("liquidity", new_result.loc[codes[8], "exclusion_reasons"])

        retained_result = build_review_membership(
            _review_inputs(codes, amounts=amounts, previous_codes={codes[8]}),
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")
        self.assertTrue(retained_result.loc[codes[8], "eligible"])
        self.assertIn("liquidity", retained_result.loc[codes[9], "exclusion_reasons"])

    def test_liquidity_denominator_excludes_non_liquidity_failures(self) -> None:
        eligible_codes = [f"0000{index:02d}.SZ" for index in range(1, 11)]
        invalid_cap = "001001.SZ"
        ambiguous_industry = "001002.SZ"
        excessive_non_trading = "001003.SZ"
        future_listing = "001004.SZ"
        missing_status = "001005.SZ"
        codes = [
            *eligible_codes,
            invalid_cap,
            ambiguous_industry,
            excessive_non_trading,
            future_listing,
            missing_status,
        ]
        amounts = {
            code: float(100 - index * 10)
            for index, code in enumerate(eligible_codes)
        }
        amounts.update(
            {
                invalid_cap: 1.0,
                ambiguous_industry: 1.0,
                excessive_non_trading: 1.0,
                future_listing: 1.0,
                missing_status: 1.0,
            }
        )
        inputs = _review_inputs(
            codes,
            amounts=amounts,
            total_mv={
                code: 0.0 if code == invalid_cap else 1_000.0
                for code in codes
            },
            list_dates={
                code: (
                    self.review + timedelta(days=1)
                    if code == future_listing
                    else self.review - timedelta(days=1_000)
                )
                for code in codes
            },
            status_codes=set(codes).difference({missing_status}),
        )
        overlap = inputs["industry_membership"].loc[
            inputs["industry_membership"]["ts_code"].eq(ambiguous_industry)
        ].copy()
        overlap["l1_code"] = "801020.SI"
        inputs["industry_membership"] = pd.concat(
            [inputs["industry_membership"], overlap],
            ignore_index=True,
        )
        non_trading_dates = sorted(
            inputs["daily"].loc[
                inputs["daily"]["ts_code"].eq(excessive_non_trading),
                "trade_date",
            ].astype(str)
        )[:2]
        inputs["daily"] = inputs["daily"].loc[
            ~(
                inputs["daily"]["ts_code"].eq(excessive_non_trading)
                & inputs["daily"]["trade_date"].isin(non_trading_dates)
            )
        ].copy()

        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")

        self.assertTrue(result.loc[eligible_codes[:8], "eligible"].all())
        self.assertIn("liquidity", result.loc[eligible_codes[8], "exclusion_reasons"])
        self.assertIn("liquidity", result.loc[eligible_codes[9], "exclusion_reasons"])
        self.assertIn("total_mv_invalid", result.loc[invalid_cap, "exclusion_reasons"])
        self.assertIn(
            "industry_ambiguous",
            result.loc[ambiguous_industry, "exclusion_reasons"],
        )
        self.assertIn(
            "non_trading_days",
            result.loc[excessive_non_trading, "exclusion_reasons"],
        )
        self.assertIn("not_listed", result.loc[future_listing, "exclusion_reasons"])
        self.assertIn("status_missing", result.loc[missing_status, "exclusion_reasons"])

    def test_latest_ineligible_quarter_does_not_reuse_older_eligible_retention(self) -> None:
        codes = [f"0000{index:02d}.SZ" for index in range(1, 11)]
        amounts = {
            **{code: float(110 - index * 10) for index, code in enumerate(codes[:7], 1)},
            codes[7]: 20.0,
            codes[8]: 20.0,
            codes[9]: 10.0,
        }
        inputs = _review_inputs(codes, amounts=amounts)
        inputs["previous_membership"] = pd.DataFrame(
            [
                {
                    "review_date": "20231229",
                    "code": codes[8],
                    "eligible": True,
                    "stable_sleeve": "large",
                },
                {
                    "review_date": "20240329",
                    "code": codes[8],
                    "eligible": False,
                    "stable_sleeve": pd.NA,
                },
            ]
        )

        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")

        self.assertFalse(result.loc[codes[8], "eligible"])
        self.assertIn("liquidity", result.loc[codes[8], "exclusion_reasons"])

    def test_future_amount_market_cap_industry_and_status_rows_are_ignored(self) -> None:
        code = "000001.SZ"
        inputs = _review_inputs([code], amounts={code: 100.0}, total_mv={code: 1_000.0})
        future = _date_key(self.review + timedelta(days=3))
        inputs["daily"] = pd.concat(
            [inputs["daily"], pd.DataFrame([{"ts_code": code, "trade_date": future, "amount": 999_999.0}])],
            ignore_index=True,
        )
        inputs["daily_basic"] = pd.concat(
            [inputs["daily_basic"], pd.DataFrame([{"ts_code": code, "trade_date": future, "total_mv": 9_999.0, "circ_mv": 8_888.0}])],
            ignore_index=True,
        )
        inputs["status"] = pd.concat(
            [
                inputs["status"],
                pd.DataFrame(
                    [{
                        "ts_code": code,
                        "trade_date": future,
                        "source_date": future,
                        "is_st": "1",
                        "tradestatus": "0",
                        "is_delisting": "1",
                        "status_source": "future",
                    }]
                ),
            ],
            ignore_index=True,
        )
        inputs["industry_membership"] = pd.concat(
            [
                inputs["industry_membership"],
                pd.DataFrame(
                    [{
                        "ts_code": code,
                        "l1_code": "FUTURE",
                        "l2_code": "FUTURE",
                        "l3_code": "FUTURE",
                        "in_date": "20200101",
                        "out_date": "",
                        "source_date": future,
                    }]
                ),
            ],
            ignore_index=True,
        )

        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        )
        row = result.iloc[0]

        self.assertTrue(row["eligible"], row["exclusion_reasons"])
        self.assertEqual(row["avg_amount_252"], 100.0)
        self.assertEqual(row["avg_amount_source_date"], "20240628")
        self.assertEqual(row["total_mv"], 1_000.0)
        self.assertEqual(row["total_mv_source_date"], "20240628")
        self.assertLessEqual(row["avg_amount_source_date"], row["review_date"])
        self.assertLessEqual(row["total_mv_source_date"], row["review_date"])
        self.assertTrue(str(result["avg_amount_source_date"].dtype).startswith("string"))
        self.assertTrue(str(result["total_mv_source_date"].dtype).startswith("string"))
        self.assertEqual(row["industry_l1"], "801010.SI")
        self.assertEqual(row["industry_source_date"], "20200101")
        self.assertEqual(row["status_source"], "baostock+suspend_d")

    def test_missing_industry_is_unclassified_but_overlaps_are_ambiguous(self) -> None:
        codes = ["000001.SZ", "000002.SZ"]
        inputs = _review_inputs(codes)
        inputs["industry_membership"].loc[
            inputs["industry_membership"]["ts_code"].eq(codes[0]), "out_date"
        ] = _date_key(self.review)
        overlap = inputs["industry_membership"].loc[
            inputs["industry_membership"]["ts_code"].eq(codes[1])
        ].copy()
        overlap["l1_code"] = "801020.SI"
        inputs["industry_membership"] = pd.concat(
            [inputs["industry_membership"], overlap], ignore_index=True
        )

        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")

        self.assertTrue(result.loc[codes[0], "eligible"])
        self.assertEqual(result.loc[codes[0], "exclusion_reasons"], "")
        self.assertEqual(result.loc[codes[0], "industry_l1"], "unclassified")
        self.assertEqual(result.loc[codes[0], "industry_l2"], "unclassified")
        self.assertEqual(result.loc[codes[0], "industry_l3"], "unclassified")
        self.assertTrue(pd.isna(result.loc[codes[0], "industry_source_date"]))
        self.assertIn("industry_ambiguous", result.loc[codes[1], "exclusion_reasons"])
        self.assertFalse(result.loc[codes[1], "eligible"])

    def test_identical_active_industry_or_status_duplicates_fail_closed(self) -> None:
        codes = ["000001.SZ", "000002.SZ"]
        inputs = _review_inputs(codes)
        inputs["industry_membership"] = pd.concat(
            [
                inputs["industry_membership"],
                inputs["industry_membership"].loc[
                    inputs["industry_membership"]["ts_code"].eq(codes[0])
                ],
            ],
            ignore_index=True,
        )
        inputs["status"] = pd.concat(
            [
                inputs["status"],
                inputs["status"].loc[inputs["status"]["ts_code"].eq(codes[1])],
            ],
            ignore_index=True,
        )

        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")

        self.assertIn("industry_ambiguous", result.loc[codes[0], "exclusion_reasons"])
        self.assertIn("status_missing", result.loc[codes[1], "exclusion_reasons"])

    def test_market_cap_ties_rank_by_code_and_previous_applies_after_eligibility(self) -> None:
        codes = ["000002.SZ", "000001.SZ", "000003.SZ"]
        inputs = _review_inputs(
            codes,
            total_mv={code: 1_000.0 for code in codes},
            previous_codes={"000003.SZ"},
            status_codes={"000001.SZ", "000002.SZ"},
        )
        result = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).set_index("code")

        self.assertEqual(result.loc["000001.SZ", "size_rank"], 1)
        self.assertEqual(result.loc["000002.SZ", "size_rank"], 2)
        self.assertTrue(pd.isna(result.loc["000003.SZ", "size_rank"]))
        self.assertTrue(pd.isna(result.loc["000003.SZ", "stable_sleeve"]))

    def test_invalid_amount_and_market_caps_have_sorted_explicit_reasons(self) -> None:
        code = "000001.SZ"
        inputs = _review_inputs([code])
        inputs["daily"].loc[:, "amount"] = -1.0
        inputs["daily_basic"].loc[:, "total_mv"] = 0.0
        inputs["daily_basic"].loc[:, "circ_mv"] = float("nan")

        row = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).iloc[0]

        self.assertEqual(
            row["exclusion_reasons"],
            "amount_invalid;circ_mv_invalid;total_mv_invalid",
        )

    def test_pre_listing_open_sessions_do_not_reduce_liquidity_or_count_non_trading(self) -> None:
        code = "000001.SZ"
        list_date = self.review - timedelta(days=90)
        inputs = _review_inputs([code], list_dates={code: list_date})
        inputs["trade_calendar"] = pd.concat(
            [
                pd.DataFrame(
                    [{
                        "cal_date": _date_key(list_date - timedelta(days=1)),
                        "is_open": "1",
                    }]
                ),
                inputs["trade_calendar"],
            ],
            ignore_index=True,
        )

        row = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).iloc[0]

        self.assertEqual(row["avg_amount_252"], 100.0)
        self.assertEqual(row["non_trading_days_252"], 0)
        self.assertTrue(row["eligible"], row["exclusion_reasons"])

    def test_effective_date_is_the_next_open_session(self) -> None:
        inputs = _review_inputs(["000001.SZ"])
        row = build_review_membership(
            inputs,
            review_date=self.review,
            contract=self.contract,
        ).iloc[0]
        self.assertEqual(row["effective_date"], "20240701")

        inputs["trade_calendar"] = inputs["trade_calendar"].loc[
            inputs["trade_calendar"]["cal_date"].le(_date_key(self.review))
        ]
        with self.assertRaisesRegex(ValueError, "all_cap_universe_next_open"):
            build_review_membership(
                inputs,
                review_date=self.review,
                contract=self.contract,
            )


EXPECTED_DAILY_HARD_STATUS_COLUMNS = (
    "trade_date",
    "code",
    "listed",
    "st",
    "delisting",
    "suspended",
    "limit_up",
    "limit_down",
    "at_limit_up",
    "at_limit_down",
    "status_complete",
    "status_conflict",
    "buy_executable",
    "sell_executable",
    "prohibit_new_position",
    "status_source",
    "hard_status_version",
)


def _daily_status_inputs() -> dict[str, pd.DataFrame]:
    trade_date = "20240628"
    codes = [f"0000{index:02d}.SZ" for index in range(1, 10)]
    stock_master = pd.DataFrame(
        [
            {
                "ts_code": code,
                "list_date": "20200101" if code != codes[7] else "20240701",
                "delist_date": trade_date if code == codes[8] else "",
                "list_status": "D" if code == codes[8] else "L",
            }
            for code in codes
        ]
    )
    daily = pd.DataFrame(
        [
            {
                "ts_code": code,
                "trade_date": trade_date,
                "open": 11.0 if code == codes[3] else 9.0 if code == codes[4] else 10.0,
            }
            for code in codes
        ]
    )
    stk_limit = pd.DataFrame(
        [
            {
                "ts_code": code,
                "trade_date": trade_date,
                "up_limit": 11.0,
                "down_limit": 9.0,
            }
            for code in codes
        ]
    )
    baostock_status = pd.DataFrame(
        [
            {
                "ts_code": code,
                "trade_date": trade_date,
                "tradestatus": "0" if code == codes[2] else "1",
                "is_st": "1" if code == codes[1] else "0",
                "st_source": "baostock_history_isST_v1",
            }
            for code in codes
            if code != codes[5]
        ]
    )
    suspend_d = pd.DataFrame(
        [
            {
                "ts_code": code,
                "trade_date": trade_date,
                "suspend_timing": "09:30-15:00",
                "suspend_type": "S",
            }
            for code in (codes[2], codes[6])
        ],
        columns=("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    )
    namechange = pd.DataFrame(
        [
            {
                "ts_code": code,
                "name": "*ST测试" if code == codes[1] else "测试股份",
                "start_date": "20200101",
                "end_date": "",
                "ann_date": "20200101",
                "change_reason": "更名",
            }
            for code in codes
        ]
    )
    return {
        "stock_master": stock_master,
        "daily": daily,
        "stk_limit": stk_limit,
        "baostock_status": baostock_status,
        "namechange": namechange,
        "suspend_d": suspend_d,
    }


class DailyHardStatusTests(unittest.TestCase):
    trade_date = "20240628"

    def test_schema_and_st_suspension_limits_missing_conflict_and_lifecycle(self) -> None:
        result = build_daily_hard_status(
            trade_date=self.trade_date,
            **_daily_status_inputs(),
        ).set_index("code")

        self.assertEqual(DAILY_HARD_STATUS_COLUMNS, EXPECTED_DAILY_HARD_STATUS_COLUMNS)
        self.assertTrue(result.loc["000001.SZ", "buy_executable"])
        self.assertTrue(result.loc["000001.SZ", "sell_executable"])

        self.assertTrue(result.loc["000002.SZ", "st"])
        self.assertFalse(result.loc["000002.SZ", "buy_executable"])
        self.assertTrue(result.loc["000002.SZ", "sell_executable"])

        self.assertTrue(result.loc["000003.SZ", "suspended"])
        self.assertFalse(result.loc["000003.SZ", "buy_executable"])
        self.assertFalse(result.loc["000003.SZ", "sell_executable"])

        self.assertTrue(result.loc["000004.SZ", "at_limit_up"])
        self.assertFalse(result.loc["000004.SZ", "buy_executable"])
        self.assertTrue(result.loc["000004.SZ", "sell_executable"])
        self.assertTrue(result.loc["000005.SZ", "at_limit_down"])
        self.assertTrue(result.loc["000005.SZ", "buy_executable"])
        self.assertFalse(result.loc["000005.SZ", "sell_executable"])

        self.assertFalse(result.loc["000006.SZ", "status_complete"])
        self.assertTrue(result.loc["000006.SZ", "prohibit_new_position"])
        self.assertFalse(result.loc["000006.SZ", "buy_executable"])
        self.assertFalse(result.loc["000006.SZ", "sell_executable"])

        self.assertTrue(result.loc["000007.SZ", "status_conflict"])
        self.assertFalse(result.loc["000007.SZ", "buy_executable"])
        self.assertFalse(result.loc["000007.SZ", "sell_executable"])

        self.assertFalse(result.loc["000008.SZ", "listed"])
        self.assertFalse(result.loc["000008.SZ", "buy_executable"])
        self.assertTrue(result.loc["000009.SZ", "delisting"])
        self.assertFalse(result.loc["000009.SZ", "buy_executable"])

    def test_namechange_st_conflict_fails_closed_with_baostock_primary(self) -> None:
        inputs = _daily_status_inputs()
        inputs["namechange"].loc[
            inputs["namechange"]["ts_code"].eq("000001.SZ"), "name"
        ] = "*ST测试"

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc["000001.SZ"]

        self.assertFalse(row["st"])
        self.assertTrue(row["status_conflict"])
        self.assertFalse(row["status_complete"])
        self.assertFalse(row["buy_executable"])

    def test_missing_active_namechange_interval_does_not_default_to_normal(self) -> None:
        inputs = _daily_status_inputs()
        inputs["namechange"] = inputs["namechange"].loc[
            ~inputs["namechange"]["ts_code"].eq("000001.SZ")
        ].copy()

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc["000001.SZ"]

        self.assertFalse(row["st"])
        self.assertTrue(row["status_conflict"])
        self.assertFalse(row["status_complete"])
        self.assertFalse(row["buy_executable"])

    def test_null_active_namechange_does_not_default_to_normal(self) -> None:
        inputs = _daily_status_inputs()
        inputs["namechange"].loc[
            inputs["namechange"]["ts_code"].eq("000001.SZ"),
            "name",
        ] = pd.NA

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc["000001.SZ"]

        self.assertTrue(row["status_conflict"])
        self.assertFalse(row["status_complete"])
        self.assertFalse(row["buy_executable"])

    def test_missing_namechange_announcement_date_is_not_pit_visible(self) -> None:
        for missing in ("", pd.NA):
            with self.subTest(missing=missing):
                inputs = _daily_status_inputs()
                inputs["namechange"].loc[
                    inputs["namechange"]["ts_code"].eq("000001.SZ"),
                    "ann_date",
                ] = missing

                row = build_daily_hard_status(
                    trade_date=self.trade_date,
                    **inputs,
                ).set_index("code").loc["000001.SZ"]

                self.assertFalse(row["status_complete"])
                self.assertTrue(row["status_conflict"])
                self.assertFalse(row["buy_executable"])
                self.assertFalse(row["sell_executable"])

    def test_duplicate_keys_in_each_single_row_daily_source_are_rejected(self) -> None:
        for source in ("daily", "stk_limit", "baostock_status", "namechange"):
            with self.subTest(source=source):
                inputs = _daily_status_inputs()
                inputs[source] = pd.concat(
                    [inputs[source], inputs[source].iloc[[0]]], ignore_index=True
                )
                with self.assertRaisesRegex(ValueError, "all_cap_hard_status_duplicate"):
                    build_daily_hard_status(
                        trade_date=self.trade_date,
                        **inputs,
                    )

    def test_empty_suspend_timing_s_is_full_day(self) -> None:
        inputs = _daily_status_inputs()
        code = "000001.SZ"
        inputs["suspend_d"] = pd.DataFrame(
            [{
                "ts_code": code,
                "trade_date": self.trade_date,
                "suspend_timing": "",
                "suspend_type": "S",
            }]
        )
        inputs["baostock_status"].loc[
            inputs["baostock_status"]["ts_code"].eq(code),
            "tradestatus",
        ] = "0"
        inputs["daily"] = inputs["daily"].loc[
            ~inputs["daily"]["ts_code"].eq(code)
        ].copy()

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc[code]

        self.assertTrue(row["status_complete"])
        self.assertTrue(row["suspended"])
        self.assertFalse(row["buy_executable"])
        self.assertFalse(row["sell_executable"])

    def test_partial_suspension_covering_open_blocks_buy(self) -> None:
        inputs = _daily_status_inputs()
        code = "000001.SZ"
        inputs["suspend_d"] = pd.DataFrame(
            [{
                "ts_code": code,
                "trade_date": self.trade_date,
                "suspend_timing": "09:30-10:30",
                "suspend_type": "S",
            }]
        )

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc[code]

        self.assertTrue(row["status_complete"])
        self.assertFalse(row["status_conflict"])
        self.assertFalse(row["buy_executable"])
        self.assertTrue(row["prohibit_new_position"])

    def test_future_status_timestamps_are_rejected(self) -> None:
        inputs = _daily_status_inputs()
        future = inputs["baostock_status"].iloc[[0]].copy()
        future["trade_date"] = "20240701"
        inputs["baostock_status"] = pd.concat(
            [inputs["baostock_status"], future], ignore_index=True
        )
        with self.assertRaisesRegex(ValueError, "all_cap_hard_status_future"):
            build_daily_hard_status(
                trade_date=self.trade_date,
                **inputs,
            )

    def test_agreed_full_day_suspension_is_complete_without_a_daily_bar(self) -> None:
        inputs = _daily_status_inputs()
        inputs["daily"] = inputs["daily"].loc[
            ~inputs["daily"]["ts_code"].eq("000003.SZ")
        ].copy()

        row = build_daily_hard_status(
            trade_date=self.trade_date,
            **inputs,
        ).set_index("code").loc["000003.SZ"]

        self.assertTrue(row["status_complete"])
        self.assertTrue(row["suspended"])
        self.assertFalse(row["buy_executable"])
        self.assertFalse(row["sell_executable"])


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _FakeSources:
    def __init__(self, codes: list[str], open_dates: list[str]) -> None:
        self.metadata = {
            "manifest_sha256": "a" * 64,
            "start_date": min(open_dates),
            "end_date": max(open_dates),
        }
        self.industry_membership = pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "l1_code": "801010.SI",
                    "l2_code": "801011.SI",
                    "l3_code": "850111.SI",
                    "in_date": "20200101",
                    "out_date": "",
                    "is_new": "Y",
                }
                for code in codes
            ]
        )
        self._limits = pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": trade_date,
                    "pre_close": 10.0,
                    "up_limit": 11.0,
                    "down_limit": 9.0,
                }
                for trade_date in open_dates
                for code in codes
            ]
        )

    def load_stk_limit_year(self, year: str | int) -> pd.DataFrame:
        return self._limits.loc[
            self._limits["trade_date"].str.startswith(str(year))
        ].copy()


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _write_complete_cache(repo_root: Path) -> tuple[_FakeSources, AllCapContract]:
    cache = repo_root / "data/shared/backtest_cache"
    open_dates = ["20240624", "20240625", "20240626", "20240627", "20240628"]
    code = "000001.SZ"
    _write_csv(
        cache / "trade_cal.csv",
        pd.DataFrame(
            [
                {"exchange": "SSE", "cal_date": value, "is_open": "1"}
                for value in [*open_dates, "20240701"]
            ]
        ),
    )
    _write_csv(
        cache / "stock_basic.csv",
        pd.DataFrame(
            [{
                "ts_code": code,
                "list_date": "20200101",
                "delist_date": "",
                "list_status": "L",
            }]
        ),
    )
    (cache / "_meta.json").write_text(
        json.dumps(
            {
                "stock_basic_done": True,
                "stock_basic_statuses_done": ["L", "D", "P"],
                "namechange_codes_done": [code],
                "daily_dates_done": [f"{value[:4]}-{value[4:6]}-{value[6:]}" for value in open_dates],
                "daily_basic_dates_done": [f"{value[:4]}-{value[4:6]}-{value[6:]}" for value in open_dates],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for value in open_dates:
        dashed = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        _write_csv(
            cache / "daily" / f"{dashed}.csv",
            pd.DataFrame(
                [{
                    "ts_code": code,
                    "trade_date": value,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "amount": 100.0,
                }]
            ),
        )
        _write_csv(
            cache / "daily_basic" / f"{dashed}.csv",
            pd.DataFrame(
                [{
                    "ts_code": code,
                    "trade_date": value,
                    "total_mv": 1_000.0,
                    "circ_mv": 800.0,
                }]
            ),
        )
        _write_csv(
            cache / "suspend_d" / f"{dashed}.csv",
            pd.DataFrame(
                columns=("ts_code", "trade_date", "suspend_timing", "suspend_type")
            ),
        )
    _write_csv(
        cache / "namechange" / f"{code}.csv",
        pd.DataFrame(
            [{
                "ts_code": code,
                "name": "测试股份",
                "start_date": "20200101",
                "end_date": "",
                "ann_date": "20200101",
                "change_reason": "更名",
            }]
        ),
    )
    _write_csv(
        cache / "adj_factor" / f"{code}.csv",
        pd.DataFrame(
            [
                {"ts_code": code, "trade_date": value, "adj_factor": 1.0}
                for value in open_dates
            ]
        ),
    )
    _write_csv(
        cache / "baostock_status" / f"{code}.csv",
        pd.DataFrame(
            [
                {
                    "ts_code": code,
                    "trade_date": value,
                    "tradestatus": "1",
                    "is_st": "0",
                    "st_source": "baostock_history_isST_v1",
                }
                for value in open_dates
            ]
        ),
    )
    sources = _FakeSources([code], open_dates)
    sources.metadata["stock_master_sha256"] = hashlib.sha256(
        (cache / "stock_basic.csv").read_bytes()
    ).hexdigest()
    sources.metadata["open_trade_dates"] = list(open_dates)
    return (
        sources,
        _contract(
            start=date(2024, 6, 24),
            end=date(2024, 6, 28),
            liquidity_lookback_sessions=5,
        ),
    )


def _resign_manifest_and_latest(repo_root: Path, mutate) -> None:
    latest_path = repo_root / "data/research/a_share_all_cap/v1/universe/latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    publication = repo_root / "data/research/a_share_all_cap/v1/universe" / latest["publication"]
    manifest_path = publication / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest, publication)
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    latest["manifest_sha256"] = manifest["manifest_sha256"]
    latest.pop("marker_sha256", None)
    latest["marker_sha256"] = _canonical_hash(latest)
    latest_path.write_text(
        json.dumps(latest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _resign_partition(
    repo_root: Path,
    dataset: str,
    mutate,
) -> None:
    def rewrite(manifest: dict[str, object], publication: Path) -> None:
        record = manifest["partitions"][dataset][0]
        path = publication / record["path"]
        schema = pq.ParquetFile(path).schema_arrow
        frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
        mutate(frame)
        pq.write_table(
            pa.Table.from_pandas(
                frame,
                schema=schema,
                preserve_index=False,
                safe=True,
            ),
            path,
            compression="snappy",
        )
        record["bytes"] = path.stat().st_size
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    _resign_manifest_and_latest(repo_root, rewrite)


def _expand_complete_cache(
    repo_root: Path,
    sources: _FakeSources,
    additional_codes: list[str],
) -> None:
    cache = repo_root / "data/shared/backtest_cache"
    master_path = cache / "stock_basic.csv"
    master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
    _write_csv(
        master_path,
        pd.concat(
            [
                master,
                pd.DataFrame(
                    [
                        {
                            "ts_code": code,
                            "list_date": "20200101",
                            "delist_date": "",
                            "list_status": "L",
                        }
                        for code in additional_codes
                    ]
                ),
            ],
            ignore_index=True,
        ),
    )
    meta_path = cache / "_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["namechange_codes_done"] = sorted(
        set(meta["namechange_codes_done"]).union(additional_codes)
    )
    meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    open_dates = list(sources.metadata["open_trade_dates"])
    for trade_key in open_dates:
        dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
        daily_path = cache / "daily" / f"{dashed}.csv"
        daily = pd.read_csv(daily_path, dtype=str, keep_default_na=False)
        _write_csv(
            daily_path,
            pd.concat(
                [
                    daily,
                    pd.DataFrame(
                        [
                            {
                                "ts_code": code,
                                "trade_date": trade_key,
                                "open": 10.0,
                                "amount": 100.0,
                            }
                            for code in additional_codes
                        ]
                    ),
                ],
                ignore_index=True,
            ),
        )
        basic_path = cache / "daily_basic" / f"{dashed}.csv"
        daily_basic = pd.read_csv(
            basic_path,
            dtype=str,
            keep_default_na=False,
        )
        _write_csv(
            basic_path,
            pd.concat(
                [
                    daily_basic,
                    pd.DataFrame(
                        [
                            {
                                "ts_code": code,
                                "trade_date": trade_key,
                                "total_mv": 1_000.0,
                                "circ_mv": 800.0,
                            }
                            for code in additional_codes
                        ]
                    ),
                ],
                ignore_index=True,
            ),
        )
    for code in additional_codes:
        _write_csv(
            cache / "namechange" / f"{code}.csv",
            pd.DataFrame(
                [{
                    "ts_code": code,
                    "name": "测试股份",
                    "start_date": "20200101",
                    "end_date": "",
                    "ann_date": "20200101",
                    "change_reason": "更名",
                }]
            ),
        )
        _write_csv(
            cache / "baostock_status" / f"{code}.csv",
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "trade_date": trade_key,
                        "tradestatus": "1",
                        "is_st": "0",
                        "st_source": "baostock_history_isST_v1",
                    }
                    for trade_key in open_dates
                ]
            ),
        )
        _write_csv(
            cache / "adj_factor" / f"{code}.csv",
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "trade_date": trade_key,
                        "adj_factor": 1.0,
                    }
                    for trade_key in open_dates
                ]
            ),
        )
    sources._limits = pd.concat(
        [
            sources._limits,
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "trade_date": trade_key,
                        "pre_close": 10.0,
                        "up_limit": 11.0,
                        "down_limit": 9.0,
                    }
                    for trade_key in open_dates
                    for code in additional_codes
                ]
            ),
        ],
        ignore_index=True,
    )
    sources.industry_membership = pd.concat(
        [
            sources.industry_membership,
            pd.DataFrame(
                [
                    {
                        "ts_code": code,
                        "l1_code": "801010.SI",
                        "l2_code": "801011.SI",
                        "l3_code": "850111.SI",
                        "in_date": "20200101",
                        "out_date": "",
                        "is_new": "Y",
                    }
                    for code in additional_codes
                ]
            ),
        ],
        ignore_index=True,
    )
    sources.metadata["stock_master_sha256"] = hashlib.sha256(
        master_path.read_bytes()
    ).hexdigest()


class UniverseMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        self.sources, self.contract = _write_complete_cache(self.repo_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _materialize(self) -> dict[str, object]:
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ) as loader:
            result = materialize_all_cap_universe(
                repo_root=self.repo_root,
                contract=self.contract,
            )
        loader.assert_called_once_with(self.repo_root)
        return result

    def test_materializes_quarter_review_and_daily_year_partitions_offline(self) -> None:
        result = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        membership = verified.load_membership_year("2024")
        hard_status = verified.load_hard_status_year("2024")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(membership["review_date"].astype(str).tolist(), ["20240628"])
        self.assertEqual(membership["effective_date"].astype(str).tolist(), ["20240701"])
        self.assertEqual(hard_status["trade_date"].nunique(), 5)
        self.assertTrue(membership.iloc[0]["eligible"])
        self.assertTrue(hard_status["buy_executable"].all())
        self.assertTrue(str(membership["code"].dtype).startswith("string"))
        self.assertTrue(str(hard_status["trade_date"].dtype).startswith("string"))

        universe_root = self.repo_root / "data/research/a_share_all_cap/v1/universe"
        published_files = {
            path.relative_to(universe_root).as_posix()
            for path in universe_root.rglob("*")
            if path.is_file()
        }
        self.assertTrue(any(path.endswith("membership/year=2024.parquet") for path in published_files))
        self.assertTrue(any(path.endswith("daily_hard_status/year=2024.parquet") for path in published_files))
        self.assertFalse(any("daily_basic" in path or "/daily/" in path for path in published_files))
        source = inspect.getsource(universe_module)
        self.assertNotIn("collect_all_cap_sources", source)
        self.assertNotIn("pro_client", source)

    def test_namechange_intervals_are_indexed_once_for_the_full_batch(self) -> None:
        indexer = universe_module._index_namechange_intervals
        with patch.object(
            universe_module,
            "_index_namechange_intervals",
            wraps=indexer,
        ) as indexed:
            self._materialize()

        indexed.assert_called_once()

    def test_quarterly_membership_reads_only_the_trailing_252_sessions(self) -> None:
        review_key = "20241231"
        contract = _contract()
        all_dates = pd.bdate_range("2023-12-01", "2024-12-31").strftime("%Y%m%d").tolist()
        inputs = _review_inputs(["000001.SZ"], review=date(2024, 12, 31))
        template = build_review_membership(
            inputs,
            review_date=review_key,
            contract=contract,
        )
        daily_by_date = {
            trade_key: pd.DataFrame(
                [{
                    "ts_code": "000001.SZ",
                    "trade_date": trade_key,
                    "amount": 100.0,
                }]
            )
            for trade_key in all_dates
        }
        daily_basic_by_date = {
            trade_key: pd.DataFrame(
                [{
                    "ts_code": "000001.SZ",
                    "trade_date": trade_key,
                    "total_mv": 1_000.0,
                    "circ_mv": 800.0,
                }]
            )
            for trade_key in all_dates
        }
        cache = SimpleNamespace(
            calendar=pd.DataFrame(
                [
                    {"cal_date": trade_key, "is_open": "1"}
                    for trade_key in [*all_dates, "20250102"]
                ]
            ),
            open_dates=tuple(all_dates),
            stock_master=inputs["stock_master"],
            daily_by_date=daily_by_date,
            daily_basic_by_date=daily_basic_by_date,
        )
        hard_status = pd.DataFrame(
            [{
                "trade_date": review_key,
                "code": "000001.SZ",
                "status_complete": True,
                "status_conflict": False,
                "st": False,
                "suspended": False,
                "delisting": False,
                "status_source": "verified",
            }]
        )
        observed_dates: list[tuple[list[str], list[str]]] = []

        def capture(inputs_arg, **_kwargs):
            observed_dates.append(
                (
                    sorted(inputs_arg["daily"]["trade_date"].astype(str).unique()),
                    sorted(
                        inputs_arg["daily_basic"]["trade_date"].astype(str).unique()
                    ),
                )
            )
            return template

        with (
            patch.object(
                universe_module,
                "_quarter_review_dates",
                return_value=[review_key],
            ),
            patch.object(
                universe_module,
                "build_review_membership",
                side_effect=capture,
            ),
        ):
            universe_module._membership_from_cache(
                cache,
                hard_status,
                inputs["industry_membership"],
                contract,
            )

        expected = all_dates[-252:]
        self.assertEqual(observed_dates, [(expected, expected)])

    def test_first_review_requires_full_warmup_for_an_old_stock(self) -> None:
        review_key = "20240628"
        contract = _contract()
        inputs = _review_inputs(["000001.SZ"], review=date(2024, 6, 28))
        development_dates = [
            "20240624",
            "20240625",
            "20240626",
            "20240627",
            review_key,
        ]
        cache = SimpleNamespace(
            calendar=inputs["trade_calendar"],
            open_dates=tuple(development_dates),
            stock_master=inputs["stock_master"],
            daily_by_date={
                trade_key: inputs["daily"].loc[
                    inputs["daily"]["trade_date"].eq(trade_key)
                ].copy()
                for trade_key in development_dates
            },
            daily_basic_by_date={
                trade_key: inputs["daily_basic"].copy()
                for trade_key in development_dates
            },
        )
        hard_status = pd.DataFrame(
            [{
                "trade_date": review_key,
                "code": "000001.SZ",
                "status_complete": True,
                "status_conflict": False,
                "st": False,
                "suspended": False,
                "delisting": False,
                "status_source": "verified",
            }]
        )

        with patch.object(
            universe_module,
            "_quarter_review_dates",
            return_value=[review_key],
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_insufficient_data:liquidity_warmup",
            ):
                universe_module._membership_from_cache(
                    cache,
                    hard_status,
                    inputs["industry_membership"],
                    contract,
                )

    def test_first_review_scales_short_history_for_a_new_listing(self) -> None:
        review_key = "20240628"
        contract = _contract()
        inputs = _review_inputs(
            ["000001.SZ"],
            review=date(2024, 6, 28),
            list_dates={"000001.SZ": date(2024, 6, 24)},
        )
        development_dates = [
            "20240624",
            "20240625",
            "20240626",
            "20240627",
            review_key,
        ]
        cache = SimpleNamespace(
            calendar=inputs["trade_calendar"],
            open_dates=tuple(development_dates),
            stock_master=inputs["stock_master"],
            daily_by_date={
                trade_key: inputs["daily"].loc[
                    inputs["daily"]["trade_date"].eq(trade_key)
                ].copy()
                for trade_key in development_dates
            },
            daily_basic_by_date={
                trade_key: inputs["daily_basic"].copy()
                for trade_key in development_dates
            },
        )
        hard_status = pd.DataFrame(
            [{
                "trade_date": review_key,
                "code": "000001.SZ",
                "status_complete": True,
                "status_conflict": False,
                "st": False,
                "suspended": False,
                "delisting": False,
                "status_source": "verified",
            }]
        )

        with patch.object(
            universe_module,
            "_quarter_review_dates",
            return_value=[review_key],
        ):
            membership = universe_module._membership_from_cache(
                cache,
                hard_status,
                inputs["industry_membership"],
                contract,
            )

        self.assertEqual(float(membership.iloc[0]["avg_amount_252"]), 100.0)
        self.assertNotIn(
            "amount_invalid",
            str(membership.iloc[0]["exclusion_reasons"]),
        )

    def test_predevelopment_daily_cache_supplies_exact_252_session_warmup(self) -> None:
        cache_root = self.repo_root / "data/shared/backtest_cache"
        self.contract = _contract(
            start=date(2024, 6, 24),
            end=date(2024, 6, 28),
            liquidity_lookback_sessions=252,
        )
        warmup_dates = (
            pd.bdate_range(end="2024-06-21", periods=248)
            .strftime("%Y%m%d")
            .tolist()
        )
        calendar_path = cache_root / "trade_cal.csv"
        calendar = pd.read_csv(calendar_path, dtype=str, keep_default_na=False)
        _write_csv(
            calendar_path,
            pd.concat(
                [
                    pd.DataFrame(
                        [
                            {
                                "exchange": "SSE",
                                "cal_date": trade_key,
                                "is_open": "1",
                            }
                            for trade_key in warmup_dates
                        ]
                    ),
                    calendar,
                ],
                ignore_index=True,
            ),
        )
        for index, trade_key in enumerate(warmup_dates):
            dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
            _write_csv(
                cache_root / "daily" / f"{dashed}.csv",
                pd.DataFrame(
                    [{
                        "ts_code": "000001.SZ",
                        "trade_date": trade_key,
                        "open": 10.0,
                        "amount": 1_000_000.0 if index == 0 else 100.0,
                    }]
                ),
            )
        meta_path = cache_root / "_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["daily_dates_done"] = sorted(
            set(meta["daily_dates_done"]).union(
                f"{value[:4]}-{value[4:6]}-{value[6:]}"
                for value in warmup_dates
            )
        )
        meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

        result = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        membership = verified.load_membership_year("2024")
        identity_paths = {
            record["path"] for record in verified.metadata["cache_identity"]["files"]
        }
        oldest = warmup_dates[0]
        oldest_path = (
            f"daily/{oldest[:4]}-{oldest[4:6]}-{oldest[6:]}.csv"
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(float(membership.iloc[0]["avg_amount_252"]), 100.0)
        self.assertNotIn(oldest_path, identity_paths)

    def test_new_listing_does_not_require_prelisting_warmup_files(self) -> None:
        cache_root = self.repo_root / "data/shared/backtest_cache"
        warmup_dates = (
            pd.bdate_range(end="2024-06-21", periods=248)
            .strftime("%Y%m%d")
            .tolist()
        )
        calendar_path = cache_root / "trade_cal.csv"
        calendar = pd.read_csv(calendar_path, dtype=str, keep_default_na=False)
        _write_csv(
            calendar_path,
            pd.concat(
                [
                    pd.DataFrame(
                        [
                            {
                                "exchange": "SSE",
                                "cal_date": trade_key,
                                "is_open": "1",
                            }
                            for trade_key in warmup_dates
                        ]
                    ),
                    calendar,
                ],
                ignore_index=True,
            ),
        )
        master_path = cache_root / "stock_basic.csv"
        master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
        master.loc[0, "list_date"] = "20240624"
        _write_csv(master_path, master)
        self.sources.metadata["stock_master_sha256"] = hashlib.sha256(
            master_path.read_bytes()
        ).hexdigest()
        self.contract = _contract(
            start=date(2024, 6, 24),
            end=date(2024, 6, 28),
            liquidity_lookback_sessions=252,
        )

        result = self._materialize()
        membership = load_verified_all_cap_universe(
            self.repo_root
        ).load_membership_year("2024")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(float(membership.iloc[0]["avg_amount_252"]), 100.0)

    def test_daily_hard_status_is_built_and_written_one_year_at_a_time(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        code = "000001.SZ"
        earlier_dates = ["20231229", "20240102"]
        calendar_path = cache / "trade_cal.csv"
        calendar = pd.read_csv(calendar_path, dtype=str, keep_default_na=False)
        _write_csv(
            calendar_path,
            pd.concat(
                [
                    pd.DataFrame(
                        [
                            {"exchange": "SSE", "cal_date": value, "is_open": "1"}
                            for value in earlier_dates
                        ]
                    ),
                    calendar,
                ],
                ignore_index=True,
            ),
        )
        for trade_date in earlier_dates:
            dashed = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            _write_csv(
                cache / "daily" / f"{dashed}.csv",
                pd.DataFrame(
                    [{
                        "ts_code": code,
                        "trade_date": trade_date,
                        "open": 10.0,
                        "amount": 100.0,
                    }]
                ),
            )
            _write_csv(
                cache / "daily_basic" / f"{dashed}.csv",
                pd.DataFrame(
                    [{
                        "ts_code": code,
                        "trade_date": trade_date,
                        "total_mv": 1_000.0,
                        "circ_mv": 800.0,
                    }]
                ),
            )
            _write_csv(
                cache / "suspend_d" / f"{dashed}.csv",
                pd.DataFrame(
                    columns=(
                        "ts_code",
                        "trade_date",
                        "suspend_timing",
                        "suspend_type",
                    )
                ),
            )
        for dataset, values in (
            (
                "baostock_status",
                {
                    "tradestatus": "1",
                    "is_st": "0",
                    "st_source": "baostock_history_isST_v1",
                },
            ),
            ("adj_factor", {"adj_factor": 1.0}),
        ):
            path = cache / dataset / f"{code}.csv"
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            _write_csv(
                path,
                pd.concat(
                    [
                        frame,
                        pd.DataFrame(
                            [
                                {
                                    "ts_code": code,
                                    "trade_date": trade_date,
                                    **values,
                                }
                                for trade_date in earlier_dates
                            ]
                        ),
                    ],
                    ignore_index=True,
                ),
            )
        meta_path = cache / "_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for field in ("daily_dates_done", "daily_basic_dates_done"):
            meta[field] = sorted(
                set(meta[field]).union({"2023-12-29", "2024-01-02"})
            )
        meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        self.sources._limits = pd.concat(
            [
                self.sources._limits,
                pd.DataFrame(
                    [
                        {
                            "ts_code": code,
                            "trade_date": trade_date,
                            "pre_close": 10.0,
                            "up_limit": 11.0,
                            "down_limit": 9.0,
                        }
                        for trade_date in earlier_dates
                    ]
                ),
            ],
            ignore_index=True,
        )
        open_dates = [
            "20231229",
            "20240102",
            "20240624",
            "20240625",
            "20240626",
            "20240627",
            "20240628",
        ]
        self.sources.metadata["start_date"] = open_dates[0]
        self.sources.metadata["open_trade_dates"] = open_dates
        self.contract = _contract(
            start=date(2023, 12, 29),
            end=date(2024, 6, 28),
            liquidity_lookback_sessions=1,
        )
        events: list[tuple[str, str]] = []
        original_build = universe_module.build_daily_hard_status
        original_write = universe_module.universe_store.write_partition

        def track_build(*args, **kwargs):
            trade_date = str(kwargs["trade_date"])
            events.append(("build", trade_date[:4]))
            return original_build(*args, **kwargs)

        def track_write(staging, dataset, year, frame):
            events.append((f"write:{dataset}", year))
            return original_write(staging, dataset, year, frame)

        with (
            patch.object(
                universe_module,
                "build_daily_hard_status",
                side_effect=track_build,
            ),
            patch.object(
                universe_module.universe_store,
                "write_partition",
                side_effect=track_write,
            ),
        ):
            self._materialize()

        write_2023 = events.index(("write:daily_hard_status", "2023"))
        build_2024 = events.index(("build", "2024"))
        self.assertLess(write_2023, build_2024)

    def test_rerun_reuses_verified_publication_without_recomputing(self) -> None:
        first = self._materialize()
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        original_latest = latest_path.read_bytes()

        with patch.object(
            universe_module,
            "build_daily_hard_status",
            side_effect=AssertionError("daily status must be reused"),
        ):
            second = self._materialize()

        self.assertEqual(second["publication_id"], first["publication_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(latest_path.read_bytes(), original_latest)

    def test_changed_contract_does_not_reuse_verified_publication(self) -> None:
        first = self._materialize()
        self.contract = _contract(
            start=date(2024, 6, 24),
            end=date(2024, 6, 28),
            boundaries=(1, 2, 3, 4),
            liquidity_lookback_sessions=5,
        )

        second = self._materialize()

        self.assertNotEqual(second["publication_id"], first["publication_id"])
        self.assertFalse(second["reused"])

    def test_previous_schema_publication_is_rebuilt(self) -> None:
        first = self._materialize()
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        publication = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe"
            / latest["publication"]
        )
        manifest_path = publication / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["schema_version"] = 2
        manifest.pop("manifest_sha256", None)
        manifest["manifest_sha256"] = _canonical_hash(manifest)
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        latest["schema_version"] = 2
        latest["manifest_sha256"] = manifest["manifest_sha256"]
        latest.pop("marker_sha256", None)
        latest["marker_sha256"] = _canonical_hash(latest)
        latest_path.write_text(
            json.dumps(latest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        second = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)

        self.assertNotEqual(second["publication_id"], first["publication_id"])
        self.assertFalse(second["reused"])
        self.assertEqual(verified.metadata["schema_version"], 4)

    def test_manifest_binds_normalized_identity_of_every_consumed_cache_input(self) -> None:
        self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        identity = verified.metadata["cache_identity"]
        paths = {record["path"] for record in identity["files"]}

        self.assertEqual(verified.metadata["schema_version"], 4)
        self.assertEqual(identity["version"], "normalized-cache-v1")
        self.assertRegex(identity["sha256"], r"^[a-f0-9]{64}$")
        for expected in (
            "_meta.json",
            "trade_cal.csv",
            "stock_basic.csv",
            "daily/2024-06-24.csv",
            "daily_basic/2024-06-24.csv",
            "suspend_d/2024-06-24.csv",
            "baostock_status/000001.SZ.csv",
            "namechange/000001.SZ.csv",
            "adj_factor/000001.SZ.csv",
        ):
            self.assertIn(expected, paths)

        calendar_path = self.repo_root / "data/shared/backtest_cache/trade_cal.csv"
        calendar = pd.read_csv(calendar_path, dtype=str, keep_default_na=False)
        _write_csv(calendar_path, calendar.iloc[::-1].reset_index(drop=True))
        load_verified_all_cap_universe(self.repo_root)

        daily_path = self.repo_root / "data/shared/backtest_cache/daily/2024-06-24.csv"
        daily = pd.read_csv(daily_path, dtype=str, keep_default_na=False)
        daily.loc[0, "amount"] = "101.0"
        _write_csv(daily_path, daily)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_cache_identity"):
            load_verified_all_cap_universe(self.repo_root)

    def test_resigned_cache_identity_cannot_omit_a_consumed_input(self) -> None:
        self._materialize()

        def omit_daily(manifest: dict[str, object], _publication: Path) -> None:
            identity = manifest["cache_identity"]
            identity["files"] = [
                record
                for record in identity["files"]
                if record["path"] != "daily/2024-06-24.csv"
            ]
            identity.pop("sha256", None)
            identity["sha256"] = _canonical_hash(identity)

        _resign_manifest_and_latest(self.repo_root, omit_daily)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_cache_identity"):
            load_verified_all_cap_universe(self.repo_root)
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        tampered_latest = latest_path.read_bytes()
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_identity_manifest",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )
        self.assertEqual(latest_path.read_bytes(), tampered_latest)

    def test_resigned_readiness_must_match_missing_cache_inputs(self) -> None:
        self._materialize()

        def falsify_readiness(
            manifest: dict[str, object],
            _publication: Path,
        ) -> None:
            manifest["readiness"]["missing_baostock_status_codes"] = [
                "000001.SZ"
            ]

        _resign_manifest_and_latest(self.repo_root, falsify_readiness)
        with self.assertRaisesRegex(
            ValueError,
            "all_cap_universe_manifest_readiness",
        ):
            load_verified_all_cap_universe(self.repo_root)

    def test_cache_change_during_build_aborts_before_latest_publish(self) -> None:
        original_build = universe_module.build_daily_hard_status
        changed = False

        def mutate_cache_after_first_day(*args, **kwargs):
            nonlocal changed
            result = original_build(*args, **kwargs)
            if not changed:
                changed = True
                path = (
                    self.repo_root
                    / "data/shared/backtest_cache/daily/2024-06-25.csv"
                )
                frame = pd.read_csv(path, dtype=str, keep_default_na=False)
                frame.loc[0, "amount"] = "101.0"
                _write_csv(path, frame)
            return result

        with (
            patch.object(
                universe_module,
                "load_verified_all_cap_sources",
                return_value=self.sources,
            ),
            patch.object(
                universe_module,
                "build_daily_hard_status",
                side_effect=mutate_cache_after_first_day,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_identity_mismatch",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )
        self.assertFalse(
            (
                self.repo_root
                / "data/research/a_share_all_cap/v1/universe/latest.json"
            ).exists()
        )

    def test_cache_identity_uses_the_same_bytes_that_were_parsed(self) -> None:
        original_hash = universe_module.universe_store._normalized_csv_hash
        changed = False

        def mutate_master_before_late_hash(path: Path):
            nonlocal changed
            if path.name == "stock_basic.csv" and not changed:
                changed = True
                master = pd.read_csv(path, dtype=str, keep_default_na=False)
                master.loc[0, "list_date"] = "20240628"
                _write_csv(path, master)
                self.sources.metadata["stock_master_sha256"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            return original_hash(path)

        with (
            patch.object(
                universe_module,
                "load_verified_all_cap_sources",
                return_value=self.sources,
            ),
            patch.object(
                universe_module.universe_store,
                "_normalized_csv_hash",
                side_effect=mutate_master_before_late_hash,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_identity_mismatch",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

        self.assertFalse(
            (
                self.repo_root
                / "data/research/a_share_all_cap/v1/universe/latest.json"
            ).exists()
        )

    def test_lazy_partition_read_rejects_aba_bytes(self) -> None:
        target = (
            self.repo_root
            / "data/shared/backtest_cache/daily/2024-06-25.csv"
        )
        original_payload = target.read_bytes()
        original_verify = universe_module.verify_shared_backtest_cache
        original_load = universe_module._load_cache_partition
        armed = False
        swapped = False

        def verify_then_arm(*args, **kwargs):
            nonlocal armed
            cache = original_verify(*args, **kwargs)
            armed = True
            return cache

        def read_b_then_restore_a(value, dataset, *args, **kwargs):
            nonlocal swapped
            path = Path(value) if not isinstance(value, pd.DataFrame) else None
            if armed and path == target and dataset == "daily" and not swapped:
                swapped = True
                changed = pd.read_csv(target, dtype=str, keep_default_na=False)
                changed.loc[0, "amount"] = "999.0"
                _write_csv(target, changed)
                try:
                    return original_load(value, dataset, *args, **kwargs)
                finally:
                    target.write_bytes(original_payload)
            return original_load(value, dataset, *args, **kwargs)

        with (
            patch.object(
                universe_module,
                "load_verified_all_cap_sources",
                return_value=self.sources,
            ),
            patch.object(
                universe_module,
                "verify_shared_backtest_cache",
                side_effect=verify_then_arm,
            ),
            patch.object(
                universe_module,
                "_load_cache_partition",
                side_effect=read_b_then_restore_a,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_identity_mismatch",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

        self.assertTrue(swapped)
        self.assertEqual(target.read_bytes(), original_payload)

    def test_cache_change_at_latest_publish_preserves_previous_latest(self) -> None:
        self._materialize()
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        previous_latest = latest_path.read_bytes()
        publications = latest_path.parent / "publications"
        previous_publications = {path.name for path in publications.iterdir()}
        self.contract = _contract(
            start=date(2024, 6, 24),
            end=date(2024, 6, 28),
            boundaries=(1, 2, 3, 4),
            liquidity_lookback_sessions=5,
        )
        original_write_latest = universe_module.universe_store.write_latest

        def mutate_cache_then_write(repo_root, manifest):
            path = (
                self.repo_root
                / "data/shared/backtest_cache/daily/2024-06-25.csv"
            )
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            frame.loc[0, "amount"] = "101.0"
            _write_csv(path, frame)
            return original_write_latest(repo_root, manifest)

        with (
            patch.object(
                universe_module,
                "load_verified_all_cap_sources",
                return_value=self.sources,
            ),
            patch.object(
                universe_module.universe_store,
                "write_latest",
                side_effect=mutate_cache_then_write,
            ),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_identity_mismatch",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

        self.assertEqual(latest_path.read_bytes(), previous_latest)
        self.assertEqual(
            {path.name for path in publications.iterdir()},
            previous_publications,
        )

    def test_same_day_suspend_and_resume_events_do_not_abort_batch(self) -> None:
        suspend_path = (
            self.repo_root
            / "data/shared/backtest_cache/suspend_d/2024-06-25.csv"
        )
        _write_csv(
            suspend_path,
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240625",
                        "suspend_timing": "09:30",
                        "suspend_type": "S",
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20240625",
                        "suspend_timing": "10:30",
                        "suspend_type": "R",
                    },
                ]
            ),
        )

        self._materialize()
        row = (
            load_verified_all_cap_universe(self.repo_root)
            .load_hard_status_year("2024")
            .set_index(["trade_date", "code"])
            .loc[("20240625", "000001.SZ")]
        )

        self.assertFalse(row["status_conflict"])
        self.assertFalse(row["buy_executable"])

    def test_verified_cache_keeps_large_inputs_as_paths(self) -> None:
        cache = universe_module.verify_shared_backtest_cache(
            self.repo_root,
            self.contract.development_start,
            self.contract.development_end,
            minimum_daily_basic_coverage=0.99,
        )

        self.assertTrue(
            all(isinstance(path, Path) for path in cache.daily_by_date.values())
        )
        self.assertTrue(
            all(
                isinstance(path, Path)
                for path in cache.daily_basic_by_date.values()
            )
        )
        self.assertTrue(
            all(isinstance(path, Path) for path in cache.suspend_by_date.values())
        )
        self.assertTrue(
            all(
                path is None or isinstance(path, Path)
                for path in cache.baostock_status_by_code.values()
            )
        )

    def test_verified_loader_metadata_is_immutable_and_lazy_load_rechecks_checksum(self) -> None:
        self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        with self.assertRaises(TypeError):
            verified.metadata["status"] = "tampered"
        with self.assertRaises(TypeError):
            verified.membership["2099"] = verified.membership["2024"]
        with self.assertRaises(TypeError):
            verified.membership["2024"].record["path"] = "elsewhere.parquet"

        partition = next((verified.publication_dir / "membership").glob("*.parquet"))
        partition.write_bytes(partition.read_bytes() + b"corrupt-after-load")
        with self.assertRaisesRegex(ValueError, "all_cap_universe_checksum"):
            verified.load_membership_year("2024")

    def test_lazy_publication_load_parses_the_verified_payload_after_aba_swap(self) -> None:
        self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        expected = verified.load_membership_year("2024")
        partition = next(
            (verified.publication_dir / "membership").glob("*.parquet")
        )
        schema = pq.ParquetFile(partition).schema_arrow
        replacement = expected.copy()
        replacement.loc[0, "total_mv"] = float(replacement.loc[0, "total_mv"]) + 1.0
        with TemporaryDirectory() as temporary:
            replacement_path = Path(temporary) / "replacement.parquet"
            pq.write_table(
                pa.Table.from_pandas(
                    replacement,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                replacement_path,
                compression="snappy",
            )
            replacement_payload = replacement_path.read_bytes()

        original_parquet_file = universe_module.universe_store.pq.ParquetFile
        swapped = False

        def swap_before_metadata(source, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                partition.write_bytes(replacement_payload)
            return original_parquet_file(source, *args, **kwargs)

        with patch.object(
            universe_module.universe_store.pq,
            "ParquetFile",
            side_effect=swap_before_metadata,
        ):
            loaded = verified.load_membership_year("2024")

        self.assertTrue(swapped)
        self.assertEqual(
            float(loaded.loc[0, "total_mv"]),
            float(expected.loc[0, "total_mv"]),
        )

    def test_missing_or_partial_cache_fails_closed(self) -> None:
        missing = self.repo_root / "data/shared/backtest_cache/daily_basic/2024-06-26.csv"
        missing.unlink()
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(ValueError, "all_cap_universe_insufficient_data"):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )
        self.assertFalse(
            (self.repo_root / "data/research/a_share_all_cap/v1/universe/latest.json").exists()
        )

    def test_partial_daily_partition_fails_before_materialization(self) -> None:
        path = self.repo_root / "data/shared/backtest_cache/daily/2024-06-26.csv"
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        _write_csv(path, frame.iloc[0:0])

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "coverage:daily:20240626",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_partial_daily_basic_partition_fails_before_materialization(self) -> None:
        path = self.repo_root / "data/shared/backtest_cache/daily_basic/2024-06-26.csv"
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        _write_csv(path, frame.iloc[0:0])

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "coverage:daily_basic:20240626",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_daily_coverage_rejects_complete_codes_with_too_many_invalid_values(self) -> None:
        additional_codes = [
            f"{index:06d}.SZ" for index in range(2, 101)
        ]
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            additional_codes,
        )
        path = (
            self.repo_root
            / "data/shared/backtest_cache/daily/2024-06-26.csv"
        )
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame.loc[frame["ts_code"].eq("000099.SZ"), "open"] = "0"
        frame.loc[frame["ts_code"].eq("000100.SZ"), "amount"] = "0"
        _write_csv(path, frame)

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "coverage:daily:20240626",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_daily_basic_coverage_rejects_complete_codes_with_too_many_invalid_values(self) -> None:
        additional_codes = [
            f"{index:06d}.SZ" for index in range(2, 101)
        ]
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            additional_codes,
        )
        path = (
            self.repo_root
            / "data/shared/backtest_cache/daily_basic/2024-06-26.csv"
        )
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame.loc[
            frame["ts_code"].eq("000099.SZ"),
            "total_mv",
        ] = "inf"
        frame.loc[
            frame["ts_code"].eq("000100.SZ"),
            "circ_mv",
        ] = "0"
        _write_csv(path, frame)

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "coverage:daily_basic:20240626",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_frozen_daily_and_adjustment_coverage_boundaries_are_accepted(self) -> None:
        additional_codes = [
            f"{index:06d}.SZ" for index in range(2, 101)
        ]
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            additional_codes,
        )
        cache = self.repo_root / "data/shared/backtest_cache"
        for trade_key in self.sources.metadata["open_trade_dates"]:
            dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
            for dataset, missing_code in (
                ("daily", "000100.SZ"),
                ("daily_basic", "000099.SZ"),
            ):
                path = cache / dataset / f"{dashed}.csv"
                frame = pd.read_csv(path, dtype=str, keep_default_na=False)
                _write_csv(
                    path,
                    frame.loc[~frame["ts_code"].eq(missing_code)].copy(),
                )
        for code in ("000099.SZ", "000100.SZ"):
            (cache / "adj_factor" / f"{code}.csv").unlink()

        result = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        coverage = verified.metadata["coverage"]
        membership = verified.load_membership_year("2024").set_index("code")
        hard_status = verified.load_hard_status_year("2024")

        self.assertEqual(result["status"], "complete")
        self.assertEqual(float(coverage["daily"]["minimum"]), 0.99)
        self.assertEqual(float(coverage["daily_basic"]["minimum"]), 0.99)
        self.assertEqual(float(coverage["adjustment"]["minimum"]), 0.98)
        self.assertIn(
            "daily_basic_missing",
            membership.loc["000099.SZ", "exclusion_reasons"],
        )
        self.assertIn(
            "adjustment_missing",
            membership.loc["000100.SZ", "exclusion_reasons"],
        )
        self.assertIn(
            "daily_missing",
            membership.loc["000100.SZ", "exclusion_reasons"],
        )
        missing_daily = hard_status.loc[
            hard_status["code"].eq("000100.SZ")
        ]
        self.assertFalse(missing_daily["status_complete"].any())

    def test_adjustment_coverage_below_frozen_threshold_fails_closed(self) -> None:
        additional_codes = [
            f"{index:06d}.SZ" for index in range(2, 101)
        ]
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            additional_codes,
        )
        cache = self.repo_root / "data/shared/backtest_cache"
        for code in ("000098.SZ", "000099.SZ", "000100.SZ"):
            (cache / "adj_factor" / f"{code}.csv").unlink()

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_insufficient_data:coverage:adjustment",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_confirmed_full_day_suspension_allows_missing_daily_rows(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        for dataset in ("daily", "daily_basic"):
            path = cache / dataset / "2024-06-26.csv"
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            _write_csv(path, frame.iloc[0:0])
        status_path = cache / "baostock_status/000001.SZ.csv"
        status = pd.read_csv(status_path, dtype=str, keep_default_na=False)
        status.loc[status["trade_date"].eq("20240626"), "tradestatus"] = "0"
        _write_csv(status_path, status)
        _write_csv(
            cache / "suspend_d/2024-06-26.csv",
            pd.DataFrame([{
                "ts_code": "000001.SZ",
                "trade_date": "20240626",
                "suspend_timing": "09:30-15:00",
                "suspend_type": "S",
            }]),
        )

        self._materialize()
        status = load_verified_all_cap_universe(
            self.repo_root
        ).load_hard_status_year("2024")
        row = status.loc[status["trade_date"].eq("20240626")].iloc[0]
        self.assertTrue(row["status_complete"])
        self.assertTrue(row["suspended"])

    def test_namechange_completion_metadata_and_file_are_required(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        meta_path = cache / "_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["namechange_codes_done"] = []
        meta_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(ValueError, "namechange"):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_stock_master_meta_requires_complete_l_d_p_collection(self) -> None:
        meta = self.repo_root / "data/shared/backtest_cache/_meta.json"
        meta.write_text(
            json.dumps(
                {
                    "stock_basic_done": True,
                    "stock_basic_statuses_done": ["L"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError, "all_cap_universe_insufficient_data"
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_missing_verified_source_fails_closed_without_fallback(self) -> None:
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            side_effect=ValueError("all_cap_source_manifest_missing"),
        ) as loader:
            with self.assertRaisesRegex(
                ValueError, "all_cap_universe_insufficient_data:sources"
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )
        loader.assert_called_once_with(self.repo_root)

    def test_source_identity_and_daily_limit_completeness_are_required(self) -> None:
        self.sources.metadata["stock_master_sha256"] = "b" * 64
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError, "all_cap_universe_insufficient_data:sources"
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

        self.sources, self.contract = _write_complete_cache(self.repo_root)
        self.sources._limits = self.sources._limits.loc[
            ~self.sources._limits["trade_date"].eq("20240626")
        ].copy()
        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError, "all_cap_universe_insufficient_data:sources"
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_b_share_is_rejected_by_board_without_requiring_source_limit_coverage(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        b_code = "900901.SH"
        master_path = cache / "stock_basic.csv"
        master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
        master = pd.concat(
            [
                master,
                pd.DataFrame(
                    [{
                        "ts_code": b_code,
                        "list_date": "20200101",
                        "delist_date": "",
                        "list_status": "L",
                    }]
                ),
            ],
            ignore_index=True,
        )
        _write_csv(master_path, master)
        open_dates = ["20240624", "20240625", "20240626", "20240627", "20240628"]
        for trade_date in open_dates:
            dashed = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            daily_path = cache / "daily" / f"{dashed}.csv"
            daily = pd.read_csv(daily_path, dtype=str, keep_default_na=False)
            _write_csv(
                daily_path,
                pd.concat(
                    [
                        daily,
                        pd.DataFrame(
                            [{
                                "ts_code": b_code,
                                "trade_date": trade_date,
                                "open": 10.0,
                                "high": 10.5,
                                "low": 9.5,
                                "close": 10.0,
                                "amount": 100.0,
                            }]
                        ),
                    ],
                    ignore_index=True,
                ),
            )
            basic_path = cache / "daily_basic" / f"{dashed}.csv"
            basic = pd.read_csv(basic_path, dtype=str, keep_default_na=False)
            _write_csv(
                basic_path,
                pd.concat(
                    [
                        basic,
                        pd.DataFrame(
                            [{
                                "ts_code": b_code,
                                "trade_date": trade_date,
                                "total_mv": 500.0,
                                "circ_mv": 400.0,
                            }]
                        ),
                    ],
                    ignore_index=True,
                ),
            )
        self.sources.metadata["stock_master_sha256"] = hashlib.sha256(
            master_path.read_bytes()
        ).hexdigest()

        result = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        membership = verified.load_membership_year("2024").set_index("code")
        self.assertFalse(membership.loc[b_code, "eligible"])
        self.assertIn("unsupported_board", membership.loc[b_code, "exclusion_reasons"])
        self.assertNotIn(b_code, result["missing_baostock_status_codes"])
        self.assertNotIn(
            b_code,
            verified.metadata["readiness"]["missing_baostock_status_codes"],
        )

    def test_bj_missing_producer_status_fails_closed_per_row_without_aborting(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            [f"{index:06d}.SZ" for index in range(2, 50)],
        )
        bj_code = "430001.BJ"
        master_path = cache / "stock_basic.csv"
        master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
        _write_csv(
            master_path,
            pd.concat(
                [
                    master,
                    pd.DataFrame(
                        [{
                            "ts_code": bj_code,
                            "list_date": "20200101",
                            "delist_date": "",
                            "list_status": "L",
                        }]
                    ),
                ],
                ignore_index=True,
            ),
        )
        open_dates = ["20240624", "20240625", "20240626", "20240627", "20240628"]
        for trade_date in open_dates:
            dashed = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            for dataset, values in (
                ("daily", {"open": 10.0, "amount": 100.0}),
                ("daily_basic", {"total_mv": 500.0, "circ_mv": 400.0}),
            ):
                path = cache / dataset / f"{dashed}.csv"
                frame = pd.read_csv(path, dtype=str, keep_default_na=False)
                _write_csv(
                    path,
                    pd.concat(
                        [
                            frame,
                            pd.DataFrame(
                                [{
                                    "ts_code": bj_code,
                                    "trade_date": trade_date,
                                    **values,
                                }]
                            ),
                        ],
                        ignore_index=True,
                    ),
                )
        self.sources._limits = pd.concat(
            [
                self.sources._limits,
                pd.DataFrame(
                    [
                        {
                            "ts_code": bj_code,
                            "trade_date": trade_date,
                            "pre_close": 10.0,
                            "up_limit": 13.0,
                            "down_limit": 7.0,
                        }
                        for trade_date in open_dates
                    ]
                ),
            ],
            ignore_index=True,
        )
        self.sources.industry_membership = pd.concat(
            [
                self.sources.industry_membership,
                pd.DataFrame(
                    [{
                        "ts_code": bj_code,
                        "l1_code": "801010.SI",
                        "l2_code": "801011.SI",
                        "l3_code": "850111.SI",
                        "in_date": "20200101",
                        "out_date": "",
                        "is_new": "Y",
                    }]
                ),
            ],
            ignore_index=True,
        )
        self.sources.metadata["stock_master_sha256"] = hashlib.sha256(
            master_path.read_bytes()
        ).hexdigest()

        result = self._materialize()
        verified = load_verified_all_cap_universe(self.repo_root)
        hard_status = verified.load_hard_status_year("2024")
        bj_status = hard_status.loc[hard_status["code"].eq(bj_code)]
        membership = verified.load_membership_year("2024").set_index("code")

        self.assertEqual(len(bj_status), len(open_dates))
        self.assertFalse(bj_status["status_complete"].any())
        self.assertTrue(
            bj_status["status_source"].str.contains("missing:baostock_status").all()
        )
        self.assertIn("status_missing", membership.loc[bj_code, "exclusion_reasons"])
        self.assertEqual(result["missing_baostock_status_codes"], [bj_code])
        self.assertEqual(result["missing_namechange_codes"], [bj_code])
        self.assertEqual(result["missing_adj_factor_codes"], [bj_code])

    def test_resigned_membership_semantic_contradictions_are_rejected(self) -> None:
        self._materialize()
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        publication = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe"
            / latest["publication"]
        )
        manifest_path = publication / "manifest.json"
        partition = next((publication / "membership").glob("*.parquet"))
        original_latest = latest_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        original_partition = partition.read_bytes()

        def eligible_with_reason(frame: pd.DataFrame) -> None:
            frame.loc[0, "exclusion_reasons"] = "status_missing"

        def raw_sleeve_outside_rank(frame: pd.DataFrame) -> None:
            frame.loc[0, "raw_sleeve"] = "nano_watch"

        def stable_sleeve_without_previous_assignment(frame: pd.DataFrame) -> None:
            frame.loc[0, "stable_sleeve"] = "mid"

        def invalid_eligible_market_cap(frame: pd.DataFrame) -> None:
            frame.loc[0, "total_mv"] = 0.0

        mutations = (
            eligible_with_reason,
            raw_sleeve_outside_rank,
            stable_sleeve_without_previous_assignment,
            invalid_eligible_market_cap,
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate.__name__):
                latest_path.write_bytes(original_latest)
                manifest_path.write_bytes(original_manifest)
                partition.write_bytes(original_partition)
                _resign_partition(self.repo_root, "membership", mutate)
                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_universe_manifest_semantics:membership",
                ):
                    load_verified_all_cap_universe(self.repo_root)

    def test_resigned_hard_status_semantic_contradictions_are_rejected(self) -> None:
        self._materialize()
        latest_path = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe/latest.json"
        )
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        publication = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe"
            / latest["publication"]
        )
        manifest_path = publication / "manifest.json"
        partition = next(
            (publication / "daily_hard_status").glob("*.parquet")
        )
        original_latest = latest_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        original_partition = partition.read_bytes()

        def suspended_but_executable(frame: pd.DataFrame) -> None:
            frame.loc[0, "suspended"] = True

        def missing_but_complete(frame: pd.DataFrame) -> None:
            frame.loc[0, "status_source"] = (
                str(frame.loc[0, "status_source"]) + ";missing:daily"
            )

        def conflict_but_complete(frame: pd.DataFrame) -> None:
            frame.loc[0, "status_conflict"] = True

        def limit_locked_but_buyable(frame: pd.DataFrame) -> None:
            frame.loc[0, "at_limit_up"] = True

        def prohibited_flag_disagrees_with_buy_gate(frame: pd.DataFrame) -> None:
            frame.loc[0, "prohibit_new_position"] = True

        mutations = (
            suspended_but_executable,
            missing_but_complete,
            conflict_but_complete,
            limit_locked_but_buyable,
            prohibited_flag_disagrees_with_buy_gate,
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate.__name__):
                latest_path.write_bytes(original_latest)
                manifest_path.write_bytes(original_manifest)
                partition.write_bytes(original_partition)
                _resign_partition(self.repo_root, "daily_hard_status", mutate)
                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_universe_manifest_semantics:daily_hard_status",
                ):
                    load_verified_all_cap_universe(self.repo_root)

    def test_corrupt_partition_and_resigned_semantic_manifest_are_rejected(self) -> None:
        self._materialize()
        latest = json.loads(
            (self.repo_root / "data/research/a_share_all_cap/v1/universe/latest.json").read_text()
        )
        publication = (
            self.repo_root
            / "data/research/a_share_all_cap/v1/universe"
            / latest["publication"]
        )
        partition = next((publication / "membership").glob("*.parquet"))
        original = partition.read_bytes()
        partition.write_bytes(original + b"corrupt")
        with self.assertRaisesRegex(ValueError, "all_cap_universe_checksum"):
            load_verified_all_cap_universe(self.repo_root)
        partition.write_bytes(original)

        def false_bounds(manifest: dict[str, object], _publication: Path) -> None:
            manifest["partitions"]["membership"][0]["min_date"] = "20240101"

        _resign_manifest_and_latest(self.repo_root, false_bounds)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_manifest_dates"):
            load_verified_all_cap_universe(self.repo_root)

    def test_resigned_invalid_classified_industry_source_date_is_rejected(self) -> None:
        self._materialize()

        def invalid_industry_source_date(
            manifest: dict[str, object],
            publication: Path,
        ) -> None:
            record = manifest["partitions"]["membership"][0]
            path = publication / record["path"]
            schema = pq.ParquetFile(path).schema_arrow
            frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
            frame.loc[0, "industry_source_date"] = "20240230"
            pq.write_table(
                pa.Table.from_pandas(
                    frame,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                path,
                compression="snappy",
            )
            record["bytes"] = path.stat().st_size
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

        _resign_manifest_and_latest(
            self.repo_root,
            invalid_industry_source_date,
        )
        with self.assertRaisesRegex(ValueError, "all_cap_universe_manifest_dates"):
            load_verified_all_cap_universe(self.repo_root)

    def test_resigned_future_classified_industry_source_date_is_rejected(self) -> None:
        self._materialize()

        def future_industry_source_date(
            manifest: dict[str, object],
            publication: Path,
        ) -> None:
            record = manifest["partitions"]["membership"][0]
            path = publication / record["path"]
            schema = pq.ParquetFile(path).schema_arrow
            frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
            frame.loc[0, "industry_source_date"] = "20240701"
            pq.write_table(
                pa.Table.from_pandas(
                    frame,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                path,
                compression="snappy",
            )
            record["bytes"] = path.stat().st_size
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

        _resign_manifest_and_latest(
            self.repo_root,
            future_industry_source_date,
        )
        with self.assertRaisesRegex(ValueError, "all_cap_universe_manifest_dates"):
            load_verified_all_cap_universe(self.repo_root)

    def test_resigned_future_membership_source_date_is_rejected(self) -> None:
        self._materialize()

        def future_source_date(manifest: dict[str, object], publication: Path) -> None:
            record = manifest["partitions"]["membership"][0]
            path = publication / record["path"]
            schema = pq.ParquetFile(path).schema_arrow
            frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
            frame.loc[0, "total_mv_source_date"] = "20240701"
            pq.write_table(
                pa.Table.from_pandas(
                    frame,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                path,
                compression="snappy",
            )
            record["bytes"] = path.stat().st_size
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

        _resign_manifest_and_latest(self.repo_root, future_source_date)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_manifest_dates"):
            load_verified_all_cap_universe(self.repo_root)


    def test_duplicate_keys_are_rejected_even_when_partition_and_manifest_are_resigned(self) -> None:
        self._materialize()

        def duplicate_membership(manifest: dict[str, object], publication: Path) -> None:
            record = manifest["partitions"]["membership"][0]
            path = publication / record["path"]
            schema = pq.ParquetFile(path).schema_arrow
            frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
            duplicated = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
            pq.write_table(
                pa.Table.from_pandas(
                    duplicated,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                path,
                compression="snappy",
            )
            record["rows"] = len(frame) + 1
            record["bytes"] = path.stat().st_size
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest["row_counts"]["membership"] = len(frame) + 1

        _resign_manifest_and_latest(self.repo_root, duplicate_membership)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_duplicate"):
            load_verified_all_cap_universe(self.repo_root)

    def test_missing_cross_section_key_is_rejected_after_resigning(self) -> None:
        _expand_complete_cache(
            self.repo_root,
            self.sources,
            ["000002.SZ"],
        )
        self._materialize()

        def remove_membership_row(
            manifest: dict[str, object],
            publication: Path,
        ) -> None:
            record = manifest["partitions"]["membership"][0]
            path = publication / record["path"]
            schema = pq.ParquetFile(path).schema_arrow
            frame = pq.read_table(path).to_pandas(types_mapper=pd.ArrowDtype)
            shortened = frame.iloc[1:].reset_index(drop=True)
            pq.write_table(
                pa.Table.from_pandas(
                    shortened,
                    schema=schema,
                    preserve_index=False,
                    safe=True,
                ),
                path,
                compression="snappy",
            )
            record["rows"] = len(shortened)
            record["bytes"] = path.stat().st_size
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            record["min_date"] = shortened["review_date"].astype(str).min()
            record["max_date"] = shortened["review_date"].astype(str).max()
            manifest["row_counts"]["membership"] = len(shortened)

        _resign_manifest_and_latest(self.repo_root, remove_membership_row)
        with self.assertRaisesRegex(
            ValueError,
            "all_cap_universe_cross_section",
        ):
            load_verified_all_cap_universe(self.repo_root)

    def test_low_projected_disk_preserves_old_verified_latest(self) -> None:
        self._materialize()
        latest_path = self.repo_root / "data/research/a_share_all_cap/v1/universe/latest.json"
        original_latest = latest_path.read_bytes()
        usage = shutil._ntuple_diskusage(total=1_000_000, used=900_000, free=100_000)
        with (
            patch.object(
                universe_module,
                "load_verified_all_cap_sources",
                return_value=self.sources,
            ),
            patch.object(universe_module.shutil, "disk_usage", return_value=usage),
        ):
            with self.assertRaisesRegex(ValueError, "all_cap_universe_free_space"):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )
        self.assertEqual(latest_path.read_bytes(), original_latest)
        verified = load_verified_all_cap_universe(self.repo_root)
        self.assertEqual(len(verified.load_membership_year("2024")), 1)

    def test_latest_symlink_is_rejected(self) -> None:
        self._materialize()
        latest_path = self.repo_root / "data/research/a_share_all_cap/v1/universe/latest.json"
        external = self.repo_root / "outside-latest.json"
        latest_path.replace(external)
        latest_path.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "all_cap_universe_symlink"):
            load_verified_all_cap_universe(self.repo_root)

    def test_invalid_stock_code_cannot_escape_cache_root(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        master_path = cache / "stock_basic.csv"
        master = pd.read_csv(master_path, dtype=str, keep_default_na=False)
        master.loc[0, "ts_code"] = "../outside"
        _write_csv(master_path, master)
        self.sources.metadata["stock_master_sha256"] = hashlib.sha256(
            master_path.read_bytes()
        ).hexdigest()

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_code",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_invalid_partition_stock_code_is_rejected(self) -> None:
        daily_path = (
            self.repo_root
            / "data/shared/backtest_cache/daily/2024-06-24.csv"
        )
        daily = pd.read_csv(daily_path, dtype=str, keep_default_na=False)
        daily.loc[0, "ts_code"] = "../outside"
        _write_csv(daily_path, daily)

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "all_cap_universe_cache_code",
            ):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_cache_subdirectory_symlink_is_rejected(self) -> None:
        cache = self.repo_root / "data/shared/backtest_cache"
        daily = cache / "daily"
        external = self.repo_root / "outside-daily"
        daily.replace(external)
        daily.symlink_to(external, target_is_directory=True)

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(ValueError, "all_cap_universe_symlink"):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_cache_root_ancestor_symlink_is_rejected(self) -> None:
        data = self.repo_root / "data"
        external = self.repo_root / "outside-data"
        data.replace(external)
        data.symlink_to(external, target_is_directory=True)

        with patch.object(
            universe_module,
            "load_verified_all_cap_sources",
            return_value=self.sources,
        ):
            with self.assertRaisesRegex(ValueError, "all_cap_universe_symlink"):
                materialize_all_cap_universe(
                    repo_root=self.repo_root,
                    contract=self.contract,
                )

    def test_loader_rejects_cache_root_ancestor_symlink(self) -> None:
        self._materialize()
        data = self.repo_root / "data"
        external = self.repo_root / "outside-data"
        data.replace(external)
        data.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "all_cap_universe_symlink"):
            load_verified_all_cap_universe(self.repo_root)

    def test_writer_rejects_each_universe_output_symlink_ancestor(self) -> None:
        original_writer = universe_module.universe_store.write_partition
        ancestors = (
            Path("data/research"),
            Path("data/research/a_share_all_cap"),
            Path("data/research/a_share_all_cap/v1"),
            Path("data/research/a_share_all_cap/v1/universe"),
            Path("data/research/a_share_all_cap/v1/universe/publications"),
        )
        for relative in ancestors:
            with self.subTest(ancestor=relative.as_posix()):
                with TemporaryDirectory() as repo_name, TemporaryDirectory() as outside:
                    repo_root = Path(repo_name)
                    sources, contract = _write_complete_cache(repo_root)
                    ancestor = repo_root / relative
                    ancestor.parent.mkdir(parents=True, exist_ok=True)
                    ancestor.symlink_to(Path(outside), target_is_directory=True)
                    with (
                        patch.object(
                            universe_module,
                            "load_verified_all_cap_sources",
                            return_value=sources,
                        ),
                        patch.object(
                            universe_module.universe_store,
                            "write_partition",
                            wraps=original_writer,
                        ) as writer,
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "all_cap_universe_symlink",
                        ):
                            materialize_all_cap_universe(
                                repo_root=repo_root,
                                contract=contract,
                            )
                    writer.assert_not_called()
                    self.assertEqual(list(Path(outside).iterdir()), [])

    def test_loader_rejects_each_universe_output_symlink_ancestor(self) -> None:
        ancestors = (
            Path("data/research"),
            Path("data/research/a_share_all_cap"),
            Path("data/research/a_share_all_cap/v1"),
        )
        for relative in ancestors:
            with self.subTest(ancestor=relative.as_posix()):
                with TemporaryDirectory() as repo_name, TemporaryDirectory() as outside:
                    repo_root = Path(repo_name)
                    sources, contract = _write_complete_cache(repo_root)
                    with patch.object(
                        universe_module,
                        "load_verified_all_cap_sources",
                        return_value=sources,
                    ):
                        materialize_all_cap_universe(
                            repo_root=repo_root,
                            contract=contract,
                        )
                    ancestor = repo_root / relative
                    moved = Path(outside) / ancestor.name
                    ancestor.replace(moved)
                    ancestor.symlink_to(moved, target_is_directory=True)

                    with self.assertRaisesRegex(
                        ValueError,
                        "all_cap_universe_symlink",
                    ):
                        load_verified_all_cap_universe(repo_root)


if __name__ == "__main__":
    unittest.main()
