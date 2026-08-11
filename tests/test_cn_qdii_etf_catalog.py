from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.catalog import (
    build_membership_as_of,
    build_membership_calendar,
)


class QDIIPointInTimeCatalogTests(unittest.TestCase):
    def test_delisted_fund_is_visible_only_inside_listing_interval(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "code": "159823.SZ",
                    "name": "Example QDII ETF",
                    "scope": "us_exposure",
                    "list_date": "2020-10-23",
                    "delist_date": "2024-01-10",
                    "status": "D",
                    "first_seen_at": "2023-12-31T09:00:00+08:00",
                }
            ]
        )

        before_delist = build_membership_as_of(rows, as_of="2024-01-09")
        on_delist = build_membership_as_of(rows, as_of="2024-01-10")

        self.assertEqual(before_delist.frame["code"].tolist(), ["159823.SZ"])
        self.assertTrue(before_delist.metadata["unbiased_universe"])
        self.assertFalse(before_delist.metadata["survivorship_bias"])
        self.assertEqual(on_delist.frame["code"].tolist(), [])

    def test_future_observation_is_diagnostic_only_not_unbiased_history(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "ts_code": "159823.SZ",
                    "name": "Example QDII ETF",
                    "research_scope": "us_exposure",
                    "list_date": "2020-10-23",
                    "delist_date": "2024-01-10",
                    "status": "D",
                    "observation_date": "2026-07-24",
                }
            ]
        )

        result = build_membership_as_of(rows, as_of="2023-06-30")

        self.assertEqual(result.frame["code"].tolist(), ["159823.SZ"])
        self.assertEqual(result.metadata["quality"], "unavailable")
        self.assertFalse(result.metadata["unbiased_universe"])
        self.assertTrue(result.metadata["survivorship_bias"])
        self.assertEqual(
            result.metadata["provenance"]["fallback"],
            "post_hoc_interval_diagnostic",
        )

    def test_calendar_uses_the_latest_observation_available_each_day(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "code": "513100.SH",
                    "scope": "us_exposure",
                    "list_date": "2013-04-25",
                    "status": "L",
                    "observation_date": "2024-01-01",
                },
                {
                    "code": "513100.SH",
                    "scope": "us_exposure",
                    "list_date": "2013-04-25",
                    "delist_date": "2024-01-03",
                    "status": "D",
                    "observation_date": "2024-01-03",
                },
            ]
        )

        result = build_membership_calendar(
            rows,
            dates=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        )

        self.assertEqual(
            result.frame[["universe_date", "code"]].to_dict(orient="records"),
            [{"universe_date": "2024-01-02", "code": "513100.SH"}],
        )
        self.assertEqual(result.metadata["quality"], "available")
        self.assertEqual(result.metadata["universe_as_of"], "2024-01-04")

    def test_delisted_status_without_delist_date_fails_closed(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "code": "159823.SZ",
                    "scope": "us_exposure",
                    "list_date": "2020-10-23",
                    "status": "D",
                    "first_seen_at": "2024-01-10",
                }
            ]
        )

        result = build_membership_as_of(rows, as_of="2024-01-11")

        self.assertEqual(result.frame["code"].tolist(), [])
        self.assertEqual(result.metadata["quality"], "unavailable")
        self.assertIn(
            "delisted_status_without_delist_date",
            result.metadata["quality_reasons"],
        )

    def test_missing_list_date_fails_closed(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "code": "513100.SH",
                    "scope": "us_exposure",
                    "status": "L",
                    "observation_date": "2024-01-01",
                }
            ]
        )

        result = build_membership_as_of(rows, as_of="2024-01-02")

        self.assertEqual(result.frame["code"].tolist(), [])
        self.assertEqual(result.metadata["quality"], "unavailable")
        self.assertIn("missing_list_date", result.metadata["quality_reasons"])


if __name__ == "__main__":
    unittest.main()
