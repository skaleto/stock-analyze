import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.schemas import PredictionRecord
from stock_analyze.research.storage import ResearchStore


class ResearchStorageTest(unittest.TestCase):
    def test_prediction_probabilities_must_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "prediction_probability_sum"):
            PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=5,
                p_up=0.7,
                p_flat=0.2,
                p_down=0.2,
            )

    def test_prediction_horizon_must_be_supported(self):
        with self.assertRaisesRegex(ValueError, "prediction_horizon"):
            PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=7,
                p_up=0.4,
                p_flat=0.2,
                p_down=0.4,
            )

    def test_feature_snapshot_preserves_text_codes(self):
        frame = pd.DataFrame(
            [{"code": "000001", "trade_date": "20260710", "momentum_20": 0.12}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchStore(Path(tmp))
            store.write_feature_snapshot("a_share", "2026-07-10", frame)

            loaded = store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(loaded.iloc[0]["code"], "000001")
        self.assertEqual(loaded.iloc[0]["trade_date"], "20260710")


if __name__ == "__main__":
    unittest.main()
