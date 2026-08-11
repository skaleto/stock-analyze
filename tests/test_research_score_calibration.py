import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.score_calibration import fit_predict_score_calibration


def _calibration_frame(*, dates: int = 100, duplicate_rows: int = 1) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(20260810)
    for date_index, trade_date in enumerate(pd.date_range("2020-01-02", periods=dates, freq="B")):
        market_noise = rng.normal(scale=0.001)
        for score in np.linspace(-0.5, 0.5, 20):
            for _ in range(duplicate_rows):
                rows.append({
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "score": score,
                    "excess_return": 0.012 * score + market_noise,
                })
    return pd.DataFrame(rows)


class ResearchScoreCalibrationTest(unittest.TestCase):
    def test_predictions_are_monotone_bounded_deterministic_and_finite(self):
        calibration = _calibration_frame()
        validation = pd.DataFrame({"score": [-0.60, -0.25, 0.0, 0.25, 0.60]})

        first = fit_predict_score_calibration(
            calibration,
            validation,
            score_column="score",
            return_column="excess_return",
            horizon=20,
            bins=10,
            minimum_dates=60,
        )
        second = fit_predict_score_calibration(
            calibration,
            validation,
            score_column="score",
            return_column="excess_return",
            horizon=20,
            bins=10,
            minimum_dates=60,
        )

        self.assertTrue(np.isfinite(first.expected_excess_return).all())
        self.assertTrue(np.isfinite(first.uncertainty_bps).all())
        self.assertTrue(np.isfinite(first.confidence).all())
        self.assertTrue((np.diff(first.expected_excess_return) >= -1e-12).all())
        self.assertTrue((first.uncertainty_bps >= 0.0).all())
        self.assertTrue(((first.confidence >= 0.0) & (first.confidence <= 1.0)).all())
        self.assertAlmostEqual(first.effective_date_count, 5.0)
        self.assertEqual(first.calibrator_hash, second.calibrator_hash)
        np.testing.assert_allclose(
            first.expected_excess_return,
            second.expected_excess_return,
        )

    def test_uncertainty_uses_dates_not_duplicate_stock_rows(self):
        validation = pd.DataFrame({"score": [-0.40, 0.0, 0.40]})

        base = fit_predict_score_calibration(
            _calibration_frame(duplicate_rows=1),
            validation,
            score_column="score",
            return_column="excess_return",
            horizon=20,
            bins=10,
            minimum_dates=60,
        )
        duplicated = fit_predict_score_calibration(
            _calibration_frame(duplicate_rows=8),
            validation,
            score_column="score",
            return_column="excess_return",
            horizon=20,
            bins=10,
            minimum_dates=60,
        )

        np.testing.assert_allclose(
            base.uncertainty_bps,
            duplicated.uncertainty_bps,
            rtol=0.0,
            atol=1e-10,
        )

    def test_insufficient_independent_dates_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "score_calibration_insufficient_dates"):
            fit_predict_score_calibration(
                _calibration_frame(dates=20),
                pd.DataFrame({"score": [0.1]}),
                score_column="score",
                return_column="excess_return",
                horizon=20,
                bins=10,
                minimum_dates=60,
            )


if __name__ == "__main__":
    unittest.main()
