from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.earnings_structured_backfill import (
    earnings_partitions,
    run_structured_earnings_backfill,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def forecast(self, **kwargs):
        self.calls.append(("forecast", kwargs))
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "ann_date": kwargs["ann_date"],
            "end_date": "20200331", "type": "预增", "p_change_min": 10.0,
            "p_change_max": 20.0, "net_profit_min": 100.0,
            "net_profit_max": 120.0, "last_parent_net": 90.0,
            "first_ann_date": kwargs["ann_date"], "summary": "增长",
            "change_reason": "主营改善", "update_flag": "0",
        }])

    def express(self, **kwargs):
        self.calls.append(("express", kwargs))
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "ann_date": kwargs["start_date"],
            "end_date": "20191231", "revenue": 1000.0,
            "operate_profit": 100.0, "total_profit": 100.0,
            "n_income": 80.0, "total_assets": 2000.0,
            "total_hldr_eqy_exc_min_int": 1000.0, "diluted_eps": 0.5,
            "diluted_roe": 8.0, "yoy_net_profit": 60.0, "bps": 5.0,
            "perf_summary": "稳定", "update_flag": "0",
        }])


class StructuredEarningsBackfillTest(unittest.TestCase):
    def test_forecast_is_daily_and_express_is_monthly(self):
        parts = earnings_partitions("2020-01-01", "2020-02-02")

        self.assertEqual(sum(p.endpoint == "forecast" for p in parts), 33)
        self.assertEqual(sum(p.endpoint == "express" for p in parts), 2)

    def test_resume_skips_committed_partitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            first = run_structured_earnings_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-01-02",
                max_partitions=1,
            )
            second = run_structured_earnings_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-01-02",
                max_partitions=1,
            )

        self.assertEqual(first["completed_partitions"], 1)
        self.assertEqual(second["completed_partitions"], 2)
        self.assertEqual(client.calls[0][0], "forecast")
        self.assertEqual(client.calls[1][0], "forecast")

    def test_manifest_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            run_structured_earnings_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-01-02",
                max_partitions=1,
            )
            with self.assertRaisesRegex(ValueError, "manifest_conflict"):
                run_structured_earnings_backfill(
                    tmp, client, start_date="2020-01-01", end_date="2020-01-03",
                    max_partitions=1,
                )


if __name__ == "__main__":
    unittest.main()
