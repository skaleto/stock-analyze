"""Neural architecture and objectives for the tabular deep challenger."""

from __future__ import annotations

from typing import Any

import torch
from torch import nn
from torch.nn import functional as functional


class ResidualBlock(nn.Module):
    def __init__(self, width: int, dropout: float) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(width, width),
            nn.LayerNorm(width),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(width, width),
            nn.Dropout(dropout),
        )
        self.activation = nn.SiLU()

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return self.activation(values + self.layers(values))


class DualHeadTabularNet(nn.Module):
    """Shared tabular encoder with classification and excess-return heads."""

    def __init__(
        self,
        *,
        input_dim: int,
        hidden_dim: int = 128,
        bottleneck_dim: int = 64,
        dropout: float = 0.15,
        class_count: int = 3,
    ) -> None:
        super().__init__()
        if input_dim <= 0:
            raise ValueError("deep_model_input_dim")
        self.input_dim = int(input_dim)
        self.hidden_dim = int(hidden_dim)
        self.bottleneck_dim = int(bottleneck_dim)
        self.dropout = float(dropout)
        self.class_count = int(class_count)
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.SiLU(),
            nn.Dropout(dropout),
            ResidualBlock(hidden_dim, dropout),
            nn.Linear(hidden_dim, bottleneck_dim),
            nn.LayerNorm(bottleneck_dim),
            nn.SiLU(),
        )
        self.classification_head = nn.Linear(bottleneck_dim, class_count)
        self.return_head = nn.Linear(bottleneck_dim, 1)

    def forward(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        encoded = self.encoder(features)
        return self.classification_head(encoded), self.return_head(encoded).squeeze(-1)

    def architecture(self) -> dict[str, Any]:
        return {
            "family": "dual_head_tabular_residual_mlp",
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "bottleneck_dim": self.bottleneck_dim,
            "dropout": self.dropout,
            "class_count": self.class_count,
            "parameters": sum(parameter.numel() for parameter in self.parameters()),
        }


def deterministic_pairwise_rank_loss(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    date_groups: torch.Tensor,
    *,
    max_pairs_per_date: int = 128,
) -> torch.Tensor:
    """Compare deterministic low/high pairs within each trading date."""

    losses: list[torch.Tensor] = []
    for group in torch.unique(date_groups):
        indices = torch.nonzero(date_groups == group, as_tuple=False).flatten()
        if indices.numel() < 2:
            continue
        ordered = indices[torch.argsort(targets[indices])]
        pair_count = min(int(ordered.numel() // 2), int(max_pairs_per_date))
        if pair_count <= 0:
            continue
        low = ordered[:pair_count]
        high = ordered[-pair_count:].flip(0)
        differences = targets[high] - targets[low]
        valid = differences.abs() > 1e-12
        if not torch.any(valid):
            continue
        prediction_difference = predictions[high][valid] - predictions[low][valid]
        direction = torch.sign(differences[valid])
        losses.append(functional.softplus(-prediction_difference * direction).mean())
    if not losses:
        return predictions.sum() * 0.0
    return torch.stack(losses).mean()


def combined_objective(
    logits: torch.Tensor,
    predicted_returns: torch.Tensor,
    labels: torch.Tensor,
    target_returns: torch.Tensor,
    date_groups: torch.Tensor,
    *,
    class_weights: torch.Tensor | None = None,
    return_scale: float = 100.0,
    classification_weight: float = 0.45,
    regression_weight: float = 0.35,
    ranking_weight: float = 0.20,
) -> tuple[torch.Tensor, dict[str, float]]:
    classification = functional.cross_entropy(logits, labels, weight=class_weights)
    scaled_target = target_returns * float(return_scale)
    regression = functional.smooth_l1_loss(predicted_returns, scaled_target, beta=0.5)
    ranking = deterministic_pairwise_rank_loss(predicted_returns, target_returns, date_groups)
    loss = (
        float(classification_weight) * classification
        + float(regression_weight) * regression
        + float(ranking_weight) * ranking
    )
    return loss, {
        "classification": float(classification.detach().cpu()),
        "regression": float(regression.detach().cpu()),
        "ranking": float(ranking.detach().cpu()),
    }
