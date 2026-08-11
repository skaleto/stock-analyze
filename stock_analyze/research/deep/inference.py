"""Artifact loading and batch inference for DL-D0."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .dataset import FeatureTransform, _apply_transform
from .model import DualHeadTabularNet


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_manifest(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("deep_artifact_manifest_missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise ValueError("deep_artifact_incomplete")
    for name, record in (manifest.get("files") or {}).items():
        path = root / name
        if not path.is_file() or _file_hash(path) != record.get("sha256"):
            raise ValueError(f"deep_artifact_checksum:{name}")
    return manifest


@dataclass
class LoadedDeepArtifact:
    model: DualHeadTabularNet
    feature_columns: tuple[str, ...]
    transform: FeatureTransform
    class_order: tuple[str, ...]
    temperature: float
    return_scale: float
    device: torch.device

    def predict(
        self,
        frame: pd.DataFrame,
        *,
        already_transformed: bool = False,
        batch_size: int = 16384,
    ) -> pd.DataFrame:
        missing = set(self.feature_columns).difference(frame.columns)
        if missing:
            raise ValueError(f"deep_inference_missing_features:{','.join(sorted(missing))}")
        if already_transformed:
            matrix = frame.loc[:, self.feature_columns].to_numpy(dtype=np.float32)
        else:
            matrix = _apply_transform(frame, self.feature_columns, self.transform)
        logits_parts: list[np.ndarray] = []
        return_parts: list[np.ndarray] = []
        self.model.eval()
        with torch.no_grad():
            for start in range(0, len(matrix), batch_size):
                features = torch.from_numpy(matrix[start : start + batch_size]).to(self.device)
                logits, predicted_returns = self.model(features)
                logits_parts.append(logits.detach().cpu().numpy())
                return_parts.append(
                    predicted_returns.detach().cpu().numpy() / self.return_scale
                )
        logits = np.concatenate(logits_parts)
        logits = logits / max(self.temperature, 1e-6)
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        output = frame[
            [column for column in ("code", "trade_date") if column in frame.columns]
        ].reset_index(drop=True)
        for index, class_name in enumerate(self.class_order):
            output[f"prob_{class_name}"] = probabilities[:, index]
        output["predicted_label"] = np.asarray(self.class_order)[
            np.argmax(probabilities, axis=1)
        ]
        output["predicted_excess_return"] = np.concatenate(return_parts)
        return output


def load_deep_artifact(
    artifact_path: str | Path,
    *,
    device: str = "cpu",
) -> LoadedDeepArtifact:
    root = Path(artifact_path)
    manifest = _verify_manifest(root)
    payload = torch.load(root / "model.pt", map_location=device, weights_only=True)
    if payload.get("artifact_schema_version") != 2:
        raise ValueError("deep_artifact_schema")
    if payload.get("training_protocol") != manifest.get("training_protocol"):
        raise ValueError("deep_artifact_protocol")
    architecture = payload["architecture"]
    if architecture.get("family") != "dual_head_tabular_residual_mlp":
        raise ValueError("deep_artifact_architecture")
    model = DualHeadTabularNet(
        input_dim=int(architecture["input_dim"]),
        hidden_dim=int(architecture["hidden_dim"]),
        bottleneck_dim=int(architecture["bottleneck_dim"]),
        dropout=float(architecture["dropout"]),
        class_count=int(architecture["class_count"]),
    )
    model.load_state_dict(payload["state_dict"])
    resolved_device = torch.device(device)
    model.to(resolved_device)
    return LoadedDeepArtifact(
        model=model,
        feature_columns=tuple(payload["feature_columns"]),
        transform=FeatureTransform(**payload["transform"]),
        class_order=tuple(payload["class_order"]),
        temperature=float(payload["temperature"]),
        return_scale=float(payload["return_scale"]),
        device=resolved_device,
    )
