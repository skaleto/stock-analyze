"""Versioned feature declarations used to reproduce research snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    family: str
    lookback: int
    availability_lag: int
    version: str
    description: str = ""


def registry_hash(definitions: Iterable[FeatureDefinition]) -> str:
    payload = [asdict(item) for item in sorted(definitions, key=lambda item: item.name)]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


TECHNICAL_FEATURES = tuple(
    FeatureDefinition(name, "technical", lookback, 0, "technical-v1")
    for name, lookback in (
        ("sma_5", 5),
        ("sma_10", 10),
        ("sma_20", 20),
        ("sma_60", 60),
        ("macd_dif", 35),
        ("macd_dea", 35),
        ("macd_hist", 35),
        ("macd_cross", 36),
        ("macd_hist_slope", 36),
        ("rsi_14", 14),
        ("adx_14", 28),
        ("natr_14", 14),
        ("bollinger_position", 20),
        ("momentum_20", 20),
        ("momentum_60", 60),
        ("realized_volatility_20", 20),
        ("volume_ratio_5_20", 20),
        ("turnover_percentile_60", 60),
    )
)


DEFAULT_REGISTRY = TECHNICAL_FEATURES
DEFAULT_REGISTRY_HASH = registry_hash(DEFAULT_REGISTRY)
