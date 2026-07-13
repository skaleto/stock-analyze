import unittest
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.models import (
    load_model_bundle,
    make_purged_walk_forward_splits,
    save_model_bundle,
    train_model_bundle,
)


def model_dataset(rows: int = 360) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    dates = pd.date_range("2023-01-02", periods=rows, freq="B")
    factor_a = rng.normal(size=rows)
    factor_b = rng.normal(size=rows)
    score = factor_a - 0.65 * factor_b + rng.normal(scale=0.5, size=rows)
    labels = np.where(score > 0.55, "up", np.where(score < -0.55, "down", "flat"))
    returns = 0.018 * score + rng.normal(scale=0.01, size=rows)
    return pd.DataFrame(
        {
            "code": [f"{index % 80:06d}" for index in range(rows)],
            "trade_date": dates.strftime("%Y%m%d"),
            "label_end_date": (dates + pd.offsets.BDay(5)).strftime("%Y%m%d"),
            "horizon": [5] * rows,
            "factor_a": factor_a,
            "factor_b": factor_b,
            "label": labels,
            "excess_return": returns,
        }
    )


class ResearchModelsTest(unittest.TestCase):
    def test_walk_forward_split_purges_overlap_and_embargo(self):
        data = model_dataset(180)

        splits = make_purged_walk_forward_splits(data, n_splits=3, embargo=5)

        self.assertEqual(len(splits), 3)
        for split in splits:
            train = data.loc[split.train_indices]
            validation = data.loc[split.validation_indices]
            self.assertLess(train["label_end_date"].max(), validation["trade_date"].min())
            self.assertLess(split.train_indices.max(), split.validation_indices.min() - 5)

    def test_trains_calibrated_deterministic_ensemble(self):
        data = model_dataset()

        first = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=11)
        second = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5, random_state=11)
        sample = data.iloc[-12:]
        first_probabilities = first.predict_proba(sample)
        second_probabilities = second.predict_proba(sample)

        np.testing.assert_allclose(first_probabilities.sum(axis=1), np.ones(len(sample)), atol=1e-9)
        np.testing.assert_allclose(first_probabilities, second_probabilities, atol=1e-12)
        self.assertEqual(first.class_order, ("down", "flat", "up"))
        self.assertLess(first.split_dates["train_end"], first.split_dates["calibration_start"])
        self.assertLess(first.split_dates["calibration_end"], first.split_dates["validation_start"])
        self.assertIn("log_loss", first.metrics)
        self.assertTrue({
            "feature_coverage",
            "point_in_time_audit",
            "oos_predictions",
            "rank_ic",
            "icir",
            "brier_improvement",
            "hit_rate_uplift",
            "auc",
            "net_excess_return",
            "max_drawdown",
            "annual_turnover",
            "ablation_stability",
        }.issubset(first.metrics))
        self.assertIn(first.calibration_method, {"sigmoid", "isotonic"})

    def test_model_artifact_has_auditable_metadata(self):
        data = model_dataset()
        bundle = train_model_bundle(data, feature_columns=["factor_a", "factor_b"], horizon=5)
        with tempfile.TemporaryDirectory() as tmp:
            artifact = save_model_bundle(bundle, Path(tmp) / "model.joblib")
            restored = load_model_bundle(artifact)
            metadata = json.loads((Path(tmp) / "model.metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(restored.model_version, bundle.model_version)
        self.assertEqual(metadata["feature_columns"], ["factor_a", "factor_b"])
        self.assertIn("scikit_learn", metadata["dependency_versions"])


if __name__ == "__main__":
    unittest.main()
