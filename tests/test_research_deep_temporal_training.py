import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.test_research_deep_temporal_dataset import TemporalDatasetTest
from stock_analyze.research.deep.temporal_dataset import prepare_temporal_dataset

try:
    import torch
except ModuleNotFoundError:
    torch = None

if torch is not None:
    from stock_analyze.research.deep.temporal_inference import load_temporal_artifact
    from stock_analyze.research.deep.temporal_training import (
        TemporalTrainingConfig,
        save_temporal_artifact,
        train_temporal_model,
    )


@unittest.skipIf(torch is None, "optional deep-learning dependencies not installed")
class TemporalTrainingTest(unittest.TestCase):
    def test_training_writes_multi_horizon_content_addressed_artifact(self):
        features, labels = TemporalDatasetTest._frames()
        prepared = prepare_temporal_dataset(
            features,
            labels,
            sequence_length=10,
            minimum_sequence_observations=8,
            min_nonzero_rows=4,
            max_static_features=4,
        )
        config = TemporalTrainingConfig(
            epochs=4,
            patience=2,
            batch_size=128,
            hidden_dim=16,
            context_dim=8,
            gru_layers=1,
            dropout=0.0,
            max_training_rows=None,
            device="cpu",
            seed=31,
        )
        result = train_temporal_model(prepared, config)

        self.assertEqual(set(result.metrics), {3, 5, 10, 20})
        self.assertEqual(set(result.temperatures), {3, 5, 10, 20})
        self.assertTrue(
            np.isfinite(
                [
                    value
                    for metrics in result.metrics.values()
                    for value in metrics.values()
                    if value is not None
                ]
            ).all()
        )
        self.assertEqual(len(result.validation_predictions), len(prepared.validation.metadata))

        with tempfile.TemporaryDirectory() as directory:
            artifact = save_temporal_artifact(
                result,
                prepared,
                Path(directory),
                market="a_share",
                source_snapshot="20240101",
            )
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            metadata = json.loads((artifact / "metadata.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["complete"])
            self.assertTrue((artifact / "model.pt").exists())
            self.assertTrue((artifact / "validation_predictions.parquet").exists())
            self.assertTrue(metadata["research_only"])
            self.assertEqual(metadata["training_protocol"], "dl-d1-temporal-context-v1")
            loaded = load_temporal_artifact(artifact)
            replay = loaded.predict_prepared(prepared, prepared.validation)
            np.testing.assert_allclose(
                replay["predicted_excess_return_20"],
                result.validation_predictions["predicted_excess_return_20"],
                rtol=1e-5,
                atol=1e-6,
            )


if __name__ == "__main__":
    unittest.main()
