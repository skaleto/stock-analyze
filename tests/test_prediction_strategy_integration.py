import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.strategy_ensemble import load_and_attach_predictions


class PredictionStrategyIntegrationTest(unittest.TestCase):
    def test_missing_predictions_leave_existing_factor_score_unchanged(self):
        candidates = pd.DataFrame([{"code": "000001", "score": 1.2}])
        with tempfile.TemporaryDirectory() as tmp:
            result = load_and_attach_predictions(
                candidates,
                repo_root=Path(tmp),
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                profile="trend",
            )
        self.assertEqual(result.iloc[0]["score"], 1.2)
        self.assertFalse(bool(result.iloc[0]["prediction_applied"]))

    def test_active_prediction_file_adjusts_score_without_mutating_input(self):
        candidates = pd.DataFrame([
            {"code": "000001", "score": 1.0, "low_volatility_60": 0.2},
            {"code": "000002", "score": 0.9, "low_volatility_60": 0.2},
        ])
        original = candidates.copy(deep=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "a_share" / "codex" / "predictions" / "20260710.parquet"
            path.parent.mkdir(parents=True)
            pd.DataFrame([
                {"code": "000002", "p_up": 0.8, "p_down": 0.1, "confidence": 0.9, "expected_excess_return": 0.1, "active_status": "active"}
            ]).to_parquet(path, index=False)
            result = load_and_attach_predictions(
                candidates,
                repo_root=Path(tmp),
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                profile="trend",
            )

        pd.testing.assert_frame_equal(candidates, original)
        self.assertGreater(result.loc[result["code"] == "000002", "score"].iloc[0], 0.9)


if __name__ == "__main__":
    unittest.main()
