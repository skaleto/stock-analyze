from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.strategy import apply_hard_filters, listing_age


class ListingAgeFilterTests(unittest.TestCase):
    def test_listing_age_uses_as_of_date(self) -> None:
        self.assertEqual(listing_age("2025-05-24", "2026-05-24"), 365)

    def test_min_listing_days_filters_known_recent_ipo(self) -> None:
        df = pd.DataFrame(
            [
                {"code": "000001", "listing_age_days": 500, "paused": False},
                {"code": "000002", "listing_age_days": 20, "paused": False},
                {"code": "000003", "listing_age_days": None, "paused": False},
            ]
        )

        out = apply_hard_filters(df, {"min_listing_days": 365})

        self.assertEqual(out["code"].tolist(), ["000001", "000003"])


if __name__ == "__main__":
    unittest.main()
