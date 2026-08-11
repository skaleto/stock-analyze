import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.deep.dataset import prepare_tabular_dataset
from stock_analyze.research.deep.dataset import DatasetSplit

try:
    import torch
except ModuleNotFoundError:
    torch = None

if torch is not None:
    from stock_analyze.research.deep.inference import load_deep_artifact
    from stock_analyze.research.deep.training import (
        DeepTrainingConfig,
        _bounded_training_indices,
        save_training_artifact,
        train_deep_model,
    )


@unittest.skipIf(torch is None, "optional deep-learning dependencies not installed")
class DeepTrainingTest(unittest.TestCase):
    @staticmethod
    def _prepared():
        rng = np.random.default_rng(23)
        dates = pd.bdate_range("2024-01-02", periods=48)
        features = []
        labels = []
        for date_index, date in enumerate(dates):
            market_state = np.sin(date_index / 6.0)
            for code_index in range(24):
                code = f"{code_index + 1:06d}"
                signal = (code_index - 11.5) / 8.0 + 0.35 * market_state
                noise = float(rng.normal(0.0, 0.15))
                features.append(
                    {
                        "code": code,
                        "trade_date": date.strftime("%Y%m%d"),
                        "signal": signal + noise,
                        "market_state": market_state,
                        "noise": float(rng.normal()),
                    }
                )
                end_index = date_index + 3
                if end_index >= len(dates):
                    continue
                excess_return = 0.012 * signal + float(rng.normal(0.0, 0.002))
                label = "up" if excess_return > 0.004 else "down" if excess_return < -0.004 else "flat"
                labels.append(
                    {
                        "code": code,
                        "trade_date": date.strftime("%Y%m%d"),
                        "horizon": 3,
                        "label_end_date": dates[end_index].strftime("%Y%m%d"),
                        "label": label,
                        "excess_return": excess_return,
                    }
                )
        return prepare_tabular_dataset(
            pd.DataFrame(features),
            pd.DataFrame(labels),
            horizon=3,
            min_nonzero_rows=10,
            min_nonzero_ratio=0.01,
        )

    def test_training_is_reproducible_and_writes_auditable_artifact(self):
        prepared = self._prepared()
        config = DeepTrainingConfig(
            epochs=8,
            patience=3,
            batch_size=256,
            hidden_dim=32,
            bottleneck_dim=16,
            dropout=0.0,
            device="cpu",
            seed=17,
        )

        first = train_deep_model(prepared, config)
        second = train_deep_model(prepared, config)

        np.testing.assert_allclose(
            first.validation_predictions["predicted_excess_return"],
            second.validation_predictions["predicted_excess_return"],
            rtol=1e-5,
            atol=1e-6,
        )
        self.assertTrue(
            np.isfinite([value for value in first.metrics.values() if value is not None]).all()
        )
        self.assertGreaterEqual(first.metrics["rank_ic"], 0.0)
        self.assertAlmostEqual(
            first.metrics["training_prior_up"],
            float(np.mean(prepared.train.y_class == 2)),
            places=6,
        )
        probability_columns = ["prob_down", "prob_flat", "prob_up"]
        np.testing.assert_allclose(
            first.validation_predictions[probability_columns].sum(axis=1),
            1.0,
            atol=1e-6,
        )

        with tempfile.TemporaryDirectory() as directory:
            artifact = save_training_artifact(
                first,
                prepared,
                Path(directory),
                market="a_share",
                source_snapshot="20240701",
            )
            metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))

            self.assertTrue((artifact / "model.pt").exists())
            self.assertTrue((artifact / "validation_predictions.parquet").exists())
            self.assertTrue((artifact / "cleaning_audit.json").exists())
            self.assertTrue((artifact / "report.md").exists())
            self.assertTrue((artifact / "manifest.json").exists())
            self.assertTrue(metadata["research_only"])
            self.assertEqual(metadata["dataset_hash"], prepared.dataset_hash)
            self.assertEqual(metadata["training_protocol"], "dl-d0-tabular-dual-head-v1")

            loaded = load_deep_artifact(artifact)
            reloaded_predictions = loaded.predict(prepared.validation.metadata.join(
                pd.DataFrame(
                    prepared.validation.x,
                    columns=prepared.feature_columns,
                )
            ), already_transformed=True)
            np.testing.assert_allclose(
                reloaded_predictions["predicted_excess_return"],
                first.validation_predictions["predicted_excess_return"],
                rtol=1e-5,
                atol=1e-6,
            )
            metadata_path = artifact / "metadata.json"
            metadata_path.write_text(metadata_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "deep_artifact_checksum"):
                load_deep_artifact(artifact)

    def test_training_budget_cannot_drop_whole_dates(self):
        split = DatasetSplit(
            x=np.zeros((3, 1), dtype=np.float32),
            y_class=np.zeros(3, dtype=np.int64),
            y_return=np.zeros(3, dtype=np.float32),
            metadata=pd.DataFrame(
                {
                    "trade_date": ["20240101", "20240102", "20240103"],
                    "code": ["000001", "000001", "000001"],
                }
            ),
        )
        with self.assertRaisesRegex(ValueError, "deep_training_budget_below_date_count"):
            _bounded_training_indices(split, 2, 7)


if __name__ == "__main__":
    unittest.main()
