"""Validated loading and prepared-batch inference for DL-D1 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .inference import _verify_manifest
from .temporal_dataset import PreparedTemporalDataset, TemporalSplit
from .temporal_model import TemporalContextNet
from .temporal_training import _predict_raw


@dataclass
class LoadedTemporalArtifact:
    model: TemporalContextNet
    temperatures: dict[int, float]
    return_scale: float
    device: torch.device

    def predict_prepared(
        self,
        prepared: PreparedTemporalDataset,
        split: TemporalSplit,
        *,
        batch_size: int = 4096,
    ) -> pd.DataFrame:
        if tuple(self.model.horizons) != tuple(prepared.horizons):
            raise ValueError("temporal_inference_horizons")
        logits, returns = _predict_raw(
            self.model,
            prepared,
            split,
            device=self.device,
            return_scale=self.return_scale,
            batch_size=batch_size,
        )
        output = split.metadata[["code", "trade_date"]].copy()
        for horizon in prepared.horizons:
            scaled = logits[horizon] / max(self.temperatures[horizon], 1e-6)
            scaled -= scaled.max(axis=1, keepdims=True)
            probabilities = np.exp(scaled)
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            for index, label in enumerate(("down", "flat", "up")):
                output[f"prob_{label}_{horizon}"] = probabilities[:, index]
            output[f"predicted_label_{horizon}"] = np.asarray(
                ("down", "flat", "up")
            )[np.argmax(probabilities, axis=1)]
            output[f"predicted_excess_return_{horizon}"] = returns[horizon]
        return output


def load_temporal_artifact(
    artifact_path: str | Path,
    *,
    device: str = "cpu",
) -> LoadedTemporalArtifact:
    root = Path(artifact_path)
    manifest = _verify_manifest(root)
    payload = torch.load(root / "model.pt", map_location=device, weights_only=True)
    if payload.get("artifact_schema_version") != 1:
        raise ValueError("temporal_artifact_schema")
    if payload.get("training_protocol") != manifest.get("training_protocol"):
        raise ValueError("temporal_artifact_protocol")
    architecture = payload["architecture"]
    if architecture.get("family") != "temporal_context_gru":
        raise ValueError("temporal_artifact_architecture")
    model = TemporalContextNet(
        sequence_feature_dim=int(architecture["sequence_feature_dim"]),
        static_dim=int(architecture["static_dim"]),
        horizons=tuple(int(value) for value in architecture["horizons"]),
        hidden_dim=int(architecture["hidden_dim"]),
        context_dim=int(architecture["context_dim"]),
        gru_layers=int(architecture["gru_layers"]),
        dropout=float(architecture["dropout"]),
    )
    model.load_state_dict(payload["state_dict"])
    resolved_device = torch.device(device)
    model.to(resolved_device)
    return LoadedTemporalArtifact(
        model=model,
        temperatures={
            int(horizon): float(value)
            for horizon, value in payload["temperatures"].items()
        },
        return_scale=float(payload["return_scale"]),
        device=resolved_device,
    )
