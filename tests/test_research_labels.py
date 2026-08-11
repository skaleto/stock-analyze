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
                "close": np.linspace(10.0, 14.0, len(dates)),
            }
        )
        self.benchmark = pd.DataFrame(
            {"trade_date": dates, "close": np.linspace(100.0, 102.0, len(dates))}
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
        expected_absolute = self.prices.iloc[5]["close"] / self.prices.iloc[0]["close"] - 1.0
        expected_benchmark = self.benchmark.iloc[5]["close"] / self.benchmark.iloc[0]["close"] - 1.0
        self.assertAlmostEqual(row["absolute_return"], expected_absolute)
        self.assertAlmostEqual(row["excess_return"], expected_absolute - expected_benchmark)
        self.assertEqual(row["label"], "up")

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
        self.assertGreaterEqual(labels["trade_date"].min(), partial.iloc[0]["trade_date"])

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
        prices = pd.DataFrame({"code": ["000001"] * rows, "trade_date": dates, "close": np.linspace(10.0, 30.0, rows)})

        started = time.perf_counter()
        labels = build_forward_labels(prices)
        elapsed = time.perf_counter() - started

        self.assertGreater(len(labels), 9000)
        self.assertLess(elapsed, 0.25, f"label generation took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
