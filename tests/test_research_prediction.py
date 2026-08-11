import unittest

from tests.test_research_models import model_dataset
from stock_analyze.research.models import train_model_bundle
from stock_analyze.research.prediction import _reason_text, compute_confidence, generate_predictions


class ResearchPredictionTest(unittest.TestCase):
    def test_prediction_reason_uses_chinese_feature_label(self):
        reason = _reason_text([("macd_hist_slope", 0.25), ("high_value_add_proxy", -0.12), ("operating_margin", 0.1)])
        self.assertEqual(reason[0], "MACD柱变化 正向贡献 0.250")
        self.assertEqual(reason[1], "高附加值代理 负向贡献 0.120")
        self.assertEqual(reason[2], "经营利润率 正向贡献 0.100")

    def test_low_sample_support_caps_confidence(self):
        confidence = compute_confidence(
            calibration_quality=1.0,
            sample_support=50,
            model_agreement=1.0,
            data_quality=1.0,
            regime_stability=1.0,
        )
        self.assertLessEqual(confidence, 0.49)

    def test_out_of_distribution_caps_confidence(self):
        confidence = compute_confidence(
            calibration_quality=1.0,
            sample_support=5_000,
            model_agreement=1.0,
            data_quality=1.0,
            regime_stability=1.0,
            out_of_distribution=True,
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
            self.assertIn("prediction_std", prediction.metadata)
            self.assertIn("lower_confidence_edge", prediction.metadata)
            self.assertIn("alpha_half_life_days", prediction.metadata)
            self.assertEqual(
                prediction.metadata["feature_schema_hash"],
                bundle.metrics["feature_schema_hash"],
            )
            self.assertEqual(
                prediction.metadata["calibrator_hash"],
                (
                    bundle.edge_calibrator.calibrator_hash
                    if bundle.edge_calibrator is not None else ""
                ),
            )

    def test_out_of_distribution_prediction_is_explicitly_invalidated(self):
        data = model_dataset()
        bundle = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=19)
        sample = data.iloc[-1:].copy()
        for column in bundle.feature_columns:
            sample[column] = 1_000_000.0

        prediction = generate_predictions(
            bundle,
            sample,
            as_of="2026-07-10",
            horizon=5,
            regime="risk_on",
            data_quality=1.0,
            regime_stability=1.0,
            feature_snapshot_id="snapshot-ood",
            active_status="active",
        )[0]

        self.assertTrue(prediction.invalidated)
        self.assertLessEqual(prediction.confidence, 0.49)
        self.assertGreater(prediction.metadata["out_of_distribution_ratio"], 0.20)
        self.assertTrue(any("训练分布" in reason for reason in prediction.invalidation))

    def test_prediction_exposes_independent_ranking_head(self):
        data = model_dataset()
        bundle = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=37)
        sample = data.iloc[-4:].copy()

        predictions = generate_predictions(
            bundle,
            sample,
            as_of="2026-07-10",
            horizon=5,
            regime="mixed",
            data_quality=1.0,
            regime_stability=0.8,
            feature_snapshot_id="snapshot-ranking",
        )

        expected = bundle.predict_excess_return(sample)
        self.assertEqual(
            [round(item.expected_excess_return or 0.0, 12) for item in predictions],
            [round(float(value), 12) for value in expected],
        )
        self.assertTrue(all(item.metadata["ranking_head"] == "ridge_hgbr" for item in predictions))

    def test_prediction_preserves_portfolio_risk_dimensions(self):
        data = model_dataset()
        bundle = train_model_bundle(
            data,
            feature_columns=["factor_a", "factor_b"],
            horizon=5,
            random_state=41,
        )
        sample = data.iloc[-1:].copy()
        sample["account_id"] = "us_exposure"
        sample["research_scope"] = "us_exposure"
        sample["benchmark_code"] = "513100.SH"
        sample["index_key"] = "dow_jones_industrial"
        sample["country"] = "美国"
        sample["theme"] = "道琼斯工业平均"
        sample["sector"] = "美国大盘"
        sample["asset_class"] = "equity"

        prediction = generate_predictions(
            bundle,
            sample,
            as_of="2026-07-10",
            horizon=5,
            regime="mixed",
            data_quality=1.0,
            regime_stability=0.8,
            feature_snapshot_id="snapshot-risk-metadata",
        )[0]

        self.assertEqual(prediction.metadata["index_key"], "dow_jones_industrial")
        self.assertEqual(prediction.metadata["country"], "美国")
        self.assertEqual(prediction.metadata["theme"], "道琼斯工业平均")
        self.assertEqual(prediction.metadata["sector"], "美国大盘")
        self.assertEqual(prediction.metadata["asset_class"], "equity")

    def test_scoped_model_rejects_features_from_another_account(self):
        data = model_dataset()
        data["account_id"] = "hs300"
        bundle = train_model_bundle(
            data,
            feature_columns=["factor_a", "factor_b"],
            horizon=5,
            random_state=43,
            account_scope="hs300",
        )
        sample = data.iloc[-2:].copy()
        sample["account_id"] = "zz500"

        with self.assertRaisesRegex(ValueError, "model_scope_mismatch"):
            generate_predictions(
                bundle,
                sample,
                as_of="2026-07-10",
                horizon=5,
                regime="mixed",
                data_quality=1.0,
                regime_stability=0.8,
                feature_snapshot_id="snapshot-scope-mismatch",
            )


if __name__ == "__main__":
    unittest.main()
