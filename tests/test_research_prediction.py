import unittest

from tests.test_research_models import model_dataset
from stock_analyze.research.models import train_model_bundle
from stock_analyze.research.prediction import compute_confidence, generate_predictions


class ResearchPredictionTest(unittest.TestCase):
    def test_low_sample_support_caps_confidence(self):
        confidence = compute_confidence(
            calibration_quality=1.0,
            sample_support=50,
            model_agreement=1.0,
            data_quality=1.0,
            regime_stability=1.0,
        )
        self.assertLessEqual(confidence, 0.49)

    def test_prediction_probabilities_confidence_and_reasons_are_distinct(self):
        data = model_dataset()
        bundle = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=13)
        sample = data.iloc[-3:].copy()

        predictions = generate_predictions(
            bundle,
            sample,
            as_of="2026-07-10",
            horizon=5,
            regime="risk_on",
            data_quality=0.92,
            regime_stability=0.8,
            feature_snapshot_id="snapshot-1",
        )

        self.assertEqual(len(predictions), 3)
        for prediction in predictions:
            self.assertAlmostEqual(prediction.p_up + prediction.p_flat + prediction.p_down, 1.0)
            self.assertNotAlmostEqual(prediction.confidence, prediction.p_up)
            self.assertTrue(prediction.reasons)
            self.assertEqual(prediction.feature_snapshot_id, "snapshot-1")


if __name__ == "__main__":
    unittest.main()
