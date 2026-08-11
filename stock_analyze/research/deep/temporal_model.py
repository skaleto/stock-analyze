"""Compact temporal and cross-sectional context network for DL-D1."""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


class TemporalContextNet(nn.Module):
    def __init__(
        self,
        *,
        sequence_feature_dim: int,
        static_dim: int,
        horizons: tuple[int, ...] = (3, 5, 10, 20),
        hidden_dim: int = 64,
        context_dim: int = 32,
        gru_layers: int = 2,
        dropout: float = 0.15,
    ) -> None:
        super().__init__()
        if sequence_feature_dim <= 0:
            raise ValueError("temporal_model_sequence_feature_dim")
        self.sequence_feature_dim = int(sequence_feature_dim)
        self.static_dim = int(static_dim)
        self.horizons = tuple(int(value) for value in horizons)
        self.hidden_dim = int(hidden_dim)
        self.context_dim = int(context_dim)
        self.gru_layers = int(gru_layers)
        self.dropout = float(dropout)
        self.sequence_encoder = nn.GRU(
            input_size=2 * self.sequence_feature_dim,
            hidden_size=self.hidden_dim,
            num_layers=self.gru_layers,
            batch_first=True,
            dropout=self.dropout if self.gru_layers > 1 else 0.0,
        )
        context_input_dim = 2 * self.sequence_feature_dim + self.static_dim
        self.context_projection = nn.Sequential(
            nn.Linear(context_input_dim, self.context_dim),
            nn.LayerNorm(self.context_dim),
            nn.SiLU(),
        )
        self.context_to_hidden = nn.Linear(self.context_dim, self.hidden_dim)
        self.fusion_gate = nn.Linear(self.hidden_dim + self.context_dim, self.hidden_dim)
        self.fusion_block = nn.Sequential(
            nn.LayerNorm(self.hidden_dim),
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.SiLU(),
            nn.Dropout(self.dropout),
            nn.Linear(self.hidden_dim, self.hidden_dim),
        )
        self.classification_heads = nn.ModuleDict(
            {str(horizon): nn.Linear(self.hidden_dim, 3) for horizon in self.horizons}
        )
        self.return_heads = nn.ModuleDict(
            {str(horizon): nn.Linear(self.hidden_dim, 1) for horizon in self.horizons}
        )

    def forward(
        self,
        sequence_values: torch.Tensor,
        sequence_validity: torch.Tensor,
        sequence_lengths: torch.Tensor,
        static_values: torch.Tensor,
        industry_context: torch.Tensor,
        market_context: torch.Tensor,
    ) -> dict[int, tuple[torch.Tensor, torch.Tensor]]:
        encoded_input = torch.cat((sequence_values, sequence_validity), dim=-1)
        packed = nn.utils.rnn.pack_padded_sequence(
            encoded_input,
            sequence_lengths.detach().cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        _, hidden = self.sequence_encoder(packed)
        own = hidden[-1]
        context_input = torch.cat(
            (static_values, industry_context, market_context),
            dim=-1,
        )
        context = self.context_projection(context_input)
        projected_context = self.context_to_hidden(context)
        gate = torch.sigmoid(self.fusion_gate(torch.cat((own, context), dim=-1)))
        fused = gate * own + (1.0 - gate) * projected_context
        fused = fused + self.fusion_block(fused)
        return {
            horizon: (
                self.classification_heads[str(horizon)](fused),
                self.return_heads[str(horizon)](fused).squeeze(-1),
            )
            for horizon in self.horizons
        }

    def architecture(self) -> dict[str, Any]:
        return {
            "family": "temporal_context_gru",
            "sequence_feature_dim": self.sequence_feature_dim,
            "static_dim": self.static_dim,
            "horizons": list(self.horizons),
            "hidden_dim": self.hidden_dim,
            "context_dim": self.context_dim,
            "gru_layers": self.gru_layers,
            "dropout": self.dropout,
            "parameters": sum(parameter.numel() for parameter in self.parameters()),
        }
