import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.labels import build_forward_labels, observable_snapshot
from stock_analyze.research.storage import ResearchStore


class ResearchLabelsTest(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2026-05-01", periods=35, freq="B").strftime("%Y%m%d")
        self.prices = pd.DataFrame(
            {
                "code": ["000001"] * len(dates),
                "trade_date": dates,
                "open": np.linspace(10.1, 14.1, len(dates)),
                "close": np.linspace(10.0, 14.0, len(dates)),
            }
        )
        self.benchmark = pd.DataFrame(
            {
                "trade_date": dates,
                "open": np.linspace(100.1, 102.1, len(dates)),
                "close": np.linspace(100.0, 102.0, len(dates)),
            }
        )

    def test_builds_absolute_and_excess_returns_for_all_horizons(self):
        labels = build_forward_labels(
            self.prices,
            benchmark=self.benchmark,
            round_trip_cost=0.001,
        )

        first = labels.loc[labels["trade_date"] == self.prices.iloc[0]["trade_date"]]
        self.assertEqual(set(first["horizon"]), {3, 5, 10, 20})
        row = first.loc[first["horizon"] == 5].iloc[0]
        expected_absolute = self.prices.iloc[5]["close"] / self.prices.iloc[1]["open"] - 1.0
        expected_benchmark = self.benchmark.iloc[5]["close"] / self.benchmark.iloc[1]["open"] - 1.0
        self.assertAlmostEqual(row["absolute_return"], expected_absolute)
        self.assertAlmostEqual(row["excess_return"], expected_absolute - expected_benchmark)
        self.assertEqual(row["entry_date"], self.prices.iloc[1]["trade_date"])
        self.assertEqual(row["label_end_date"], self.prices.iloc[5]["trade_date"])
        self.assertAlmostEqual(row["entry_price"], self.prices.iloc[1]["open"])
        self.assertEqual(row["label_contract_version"], "next-open-v2")
        self.assertEqual(row["label"], "up")

    def test_overnight_gap_before_entry_is_not_counted_as_model_return(self):
        prices = self.prices.iloc[:8].copy()
        prices.loc[prices.index[0], "close"] = 10.0
        prices.loc[prices.index[1], "open"] = 20.0
        prices.loc[prices.index[3], "close"] = 22.0
        benchmark = self.benchmark.iloc[:8].copy()

        labels = build_forward_labels(
            prices,
            benchmark=benchmark,
            horizons=(3,),
            round_trip_cost=0.0,
        )

        row = labels.loc[labels["trade_date"].eq(prices.iloc[0]["trade_date"])].iloc[0]
        self.assertAlmostEqual(row["absolute_return"], 0.10)
        self.assertNotAlmostEqual(row["absolute_return"], 1.20)

    def test_benchmark_uses_security_entry_and_exit_dates_when_sessions_are_missing(self):
        prices = self.prices.iloc[:10].drop(index=[2]).reset_index(drop=True)
        labels = build_forward_labels(
            prices,
            benchmark=self.benchmark.iloc[:10],
            horizons=(3,),
        )

        row = labels.loc[labels["trade_date"].eq(prices.iloc[0]["trade_date"])].iloc[0]
        self.assertEqual(row["entry_date"], prices.iloc[1]["trade_date"])
        self.assertEqual(row["label_end_date"], prices.iloc[3]["trade_date"])
        benchmark_by_date = self.benchmark.set_index("trade_date")
        expected = (
            benchmark_by_date.loc[row["label_end_date"], "close"]
            / benchmark_by_date.loc[row["entry_date"], "open"]
            - 1.0
        )
        self.assertAlmostEqual(row["benchmark_return"], expected)

    def test_labels_persist_next_open_execution_constraints(self):
        prices = self.prices.iloc[:8].copy()
        prices["high"] = prices["open"] + 0.1
        prices["low"] = prices["open"] - 0.1
        prices["volume"] = 1_000.0
        prices.loc[prices.index[1], ["open", "high", "low"]] = 11.0
        prices.loc[prices.index[0], "close"] = 10.0

        labels = build_forward_labels(prices, horizons=(3,))

        row = labels.loc[labels["trade_date"].eq(prices.iloc[0]["trade_date"])].iloc[0]
        self.assertEqual(row["entry_high"], 11.0)
        self.assertEqual(row["entry_low"], 11.0)
        self.assertFalse(bool(row["entry_buy_allowed"]))
        self.assertTrue(bool(row["entry_sell_allowed"]))

    def test_required_benchmark_rejects_missing_or_partial_history(self):
        with self.assertRaisesRegex(ValueError, "label_benchmark_missing"):
            build_forward_labels(self.prices, require_benchmark=True)

        partial = self.benchmark.iloc[10:].copy()
        labels = build_forward_labels(
            self.prices,
            benchmark=partial,
            require_benchmark=True,
        )

        self.assertTrue(labels["benchmark_return"].notna().all())
        self.assertGreaterEqual(labels["entry_date"].min(), partial.iloc[0]["trade_date"])

    def test_label_endpoint_prevents_future_prices_from_affecting_results(self):
        endpoint = self.prices.iloc[20]["trade_date"]
        baseline = build_forward_labels(self.prices, benchmark=self.benchmark, label_end=endpoint)
        changed = self.prices.copy()
        changed.loc[changed.index > 20, "close"] = 10_000.0

        repeated = build_forward_labels(changed, benchmark=self.benchmark, label_end=endpoint)

        pd.testing.assert_frame_equal(baseline, repeated)
        self.assertLessEqual(baseline["label_end_date"].max(), endpoint)

    def test_observable_snapshot_filters_late_announcements(self):
        frame = pd.DataFrame(
            [
                {"code": "000001", "ann_date": "20260709", "observed_at": "2026-07-09T18:00:00+08:00", "roe": 0.1},
                {"code": "000002", "ann_date": "20260711", "observed_at": "2026-07-11T18:00:00+08:00", "roe": 0.2},
            ]
        )

        snapshot = observable_snapshot(frame, "2026-07-10")

        self.assertEqual(snapshot["code"].tolist(), ["000001"])

    def test_label_store_preserves_codes(self):
        labels = build_forward_labels(self.prices, benchmark=self.benchmark)
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            store.write_label_snapshot("a_share", "2026-07-10", labels)
            loaded = store.read_label_snapshot("a_share", "2026-07-10")
        self.assertEqual(loaded.iloc[0]["code"], "000001")

    def test_large_label_frame_is_vectorized(self):
        rows = 2500
        dates = pd.date_range("2016-01-01", periods=rows, freq="B").strftime("%Y%m%d")
        close = np.linspace(10.0, 30.0, rows)
        prices = pd.DataFrame({"code": ["000001"] * rows, "trade_date": dates, "open": close + 0.01, "close": close})

        started = time.perf_counter()
        labels = build_forward_labels(prices)
        elapsed = time.perf_counter() - started

        self.assertGreater(len(labels), 9000)
        self.assertLess(elapsed, 0.25, f"label generation took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
