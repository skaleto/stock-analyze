import unittest

import numpy as np
import pandas as pd

from stock_analyze.research.deep.comparison import compare_deep_predictions


class DeepComparisonTest(unittest.TestCase):
    def test_paired_comparison_uses_common_rows_and_reports_ensemble(self):
        rows = []
        for date_index, date in enumerate(("20240101", "20240102", "20240103", "20240104")):
            for code_index in range(20):
                actual = 0.01 * (code_index - 10) + 0.001 * date_index
                rows.append(
                    {
                        "code": f"{code_index:06d}",
                        "trade_date": date,
                        "excess_return": actual,
                        "predicted_excess_return": actual + 0.01 * np.sin(code_index),
                    }
                )
        d0 = pd.DataFrame(rows)
        d1 = d0[["code", "trade_date"]].copy()
        d1["predicted_excess_return_20"] = -d0["predicted_excess_return"]

        report = compare_deep_predictions(d0, d1, horizon=20)

        self.assertEqual(report["common_rows"], 80)
        self.assertEqual(report["common_dates"], 4)
        self.assertIn("ensemble", report)
        self.assertIn("daily_prediction_rank_correlation", report)
        self.assertGreater(report["models"]["dl_d0"]["rank_ic"], 0.9)
        self.assertLess(report["models"]["dl_d1"]["rank_ic"], -0.9)


if __name__ == "__main__":
    unittest.main()
