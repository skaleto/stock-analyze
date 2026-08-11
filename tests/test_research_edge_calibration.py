from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.edge_calibration import EdgeCalibrator, fit_edge_calibrator


class ResearchEdgeCalibrationTest(unittest.TestCase):
    def test_non_finite_scores_fail_closed(self) -> None:
        calibrator = EdgeCalibrator(
            available=True,
            boundaries=(0.0,),
            expected_returns=(-0.01, 0.01),
            prediction_std=(0.002, 0.003),
            bucket_date_support=(30, 30),
            fit_max_date="20260131",
            alpha_half_life_days=5.0,
            calibration_version="clustered-date-mean-se-v2",
        )

        expected, uncertainty = calibrator.predict_distribution(
            np.array([np.nan, np.inf, -np.inf, 0.5])
        )

        np.testing.assert_allclose(expected[:3], np.zeros(3))
        np.testing.assert_allclose(uncertainty[:3], np.ones(3))
        self.assertAlmostEqual(expected[3], 0.01)
        self.assertAlmostEqual(uncertainty[3], 0.003)

    def test_legacy_uncertainty_contract_fails_closed(self) -> None:
        calibrator = EdgeCalibrator(
            available=True,
            boundaries=(0.0,),
            expected_returns=(-0.01, 0.01),
            prediction_std=(0.20, 0.20),
            bucket_date_support=(30, 30),
            fit_max_date="20260131",
            alpha_half_life_days=5.0,
        )

        expected, uncertainty = calibrator.predict_distribution(
            np.array([-0.5, 0.5])
        )

        np.testing.assert_allclose(expected, np.zeros(2))
        np.testing.assert_allclose(uncertainty, np.ones(2))

    def test_calibration_records_only_training_date_support(self) -> None:
        dates = pd.date_range("2025-01-02", periods=90, freq="B")
        scores = np.tile(np.linspace(-1.0, 1.0, 15), 6)
        predictions = pd.DataFrame({
            "trade_date": np.repeat(dates[:6].strftime("%Y%m%d"), 15),
            "score": scores,
        })
        returns = pd.Series(scores * 0.01)

        calibrator = fit_edge_calibrator(
            predictions,
            returns,
            minimum_dates_per_bucket=4,
        )

        self.assertTrue(calibrator.available)
        self.assertEqual(calibrator.fit_max_date, predictions["trade_date"].max())
        expected, uncertainty = calibrator.predict_distribution(np.array([-0.8, 0.8]))
        self.assertLess(expected[0], expected[1])
        self.assertTrue((uncertainty >= 0.0).all())

    def test_non_monotonic_buckets_fail_closed(self) -> None:
        dates = pd.date_range("2025-01-02", periods=6, freq="B")
        scores = np.tile(np.arange(15, dtype=float), 6)
        returns = np.tile(
            np.concatenate((np.full(5, 0.02), np.full(5, -0.02), np.full(5, 0.01))),
            6,
        )
        predictions = pd.DataFrame({
            "trade_date": np.repeat(dates.strftime("%Y%m%d"), 15),
            "score": scores,
        })

        calibrator = fit_edge_calibrator(
            predictions,
            pd.Series(returns),
            minimum_dates_per_bucket=4,
        )

        self.assertFalse(calibrator.available)
        self.assertEqual(calibrator.reason, "bucket_returns_not_monotonic")

    def test_uncertainty_uses_clustered_mean_error_not_outcome_dispersion(self) -> None:
        dates = pd.date_range("2024-01-02", periods=80, freq="B")
        rows = []
        realized = []
        for trade_date in dates.strftime("%Y%m%d"):
            for bucket, score in enumerate((-2.0, -1.0, 0.0, 1.0, 2.0)):
                edge = (bucket - 2) * 0.005
                for repeat in range(10):
                    rows.append({"trade_date": trade_date, "score": score})
                    realized.append(edge + (-0.10 if repeat % 2 == 0 else 0.10))

        calibrator = fit_edge_calibrator(
            pd.DataFrame(rows),
            pd.Series(realized),
            minimum_dates_per_bucket=40,
        )

        self.assertTrue(calibrator.available)
        self.assertEqual(calibrator.calibration_version, "clustered-date-mean-se-v2")
        self.assertGreater(min(calibrator.outcome_dispersion), 0.09)
        self.assertLess(max(calibrator.mean_standard_error), 1e-10)
        _, uncertainty = calibrator.predict_distribution(np.array([-2.0, 2.0]))
        self.assertLess(float(uncertainty.max()), 1e-10)

    def test_calibrator_hash_is_content_addressed(self) -> None:
        left = EdgeCalibrator(
            available=True,
            boundaries=(0.0,),
            expected_returns=(-0.01, 0.01),
            prediction_std=(0.002, 0.003),
            bucket_date_support=(30, 30),
            fit_max_date="20260131",
            alpha_half_life_days=5.0,
        )
        same = EdgeCalibrator(**left.__dict__)
        changed = EdgeCalibrator(
            **{**left.__dict__, "expected_returns": (-0.01, 0.02)}
        )

        self.assertEqual(left.calibrator_hash, same.calibrator_hash)
        self.assertNotEqual(left.calibrator_hash, changed.calibrator_hash)


if __name__ == "__main__":
    unittest.main()
