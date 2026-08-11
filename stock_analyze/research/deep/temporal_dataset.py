"""Indexed 60-day sequence preparation for the DL-D1 temporal challenger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..feature_registry import DEFAULT_REGISTRY
from .dataset import (
    CLASS_ORDER,
    FeatureTransform,
    _apply_transform,
    _fit_transform,
    prepare_tabular_dataset,
)


DEFAULT_HORIZONS = (3, 5, 10, 20)
SCALE_FREE_SEQUENCE_COLUMNS = (
    "macd_cross",
    "macd_hist",
    "macd_hist_slope",
    "macd_hist_acceleration",
    "macd_zero_state",
    "macd_cross_age",
    "rsi_14",
    "adx_14",
    "natr_14",
    "bollinger_position",
    "bollinger_width",
    "return_1",
    "momentum_5",
    "momentum_20",
    "momentum_60",
    "realized_volatility_20",
    "gap_return",
    "volume_ratio_5_20",
    "volume_zscore_20",
    "mfi_14",
    "amount_ratio_5_20",
)


@dataclass(frozen=True)
class TemporalSplit:
    sequence_indices: np.ndarray
    sequence_lengths: np.ndarray
    static_values: np.ndarray
    industry_context: np.ndarray
    market_context: np.ndarray
    y_class: np.ndarray
    y_return: np.ndarray
    metadata: pd.DataFrame


@dataclass(frozen=True)
class PreparedTemporalDataset:
    horizons: tuple[int, ...]
    sequence_length: int
    sequence_columns: tuple[str, ...]
    static_columns: tuple[str, ...]
    history_values: np.ndarray
    history_validity: np.ndarray
    history_metadata: pd.DataFrame
    sequence_transform: FeatureTransform
    static_transform: FeatureTransform
    train: TemporalSplit
    calibration: TemporalSplit
    validation: TemporalSplit
    audit: dict[str, Any]
    dataset_hash: str


def _normalize_keys(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["code"] = output["code"].astype("string").str.split(".").str[0].str.zfill(6)
    output["trade_date"] = (
        output["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
    )
    return output


def _subset_transform(
    transform: FeatureTransform,
    columns: tuple[str, ...],
) -> FeatureTransform:
    return FeatureTransform(
        medians={column: transform.medians[column] for column in columns},
        scales={column: transform.scales[column] for column in columns},
        lower_bounds={column: transform.lower_bounds[column] for column in columns},
        upper_bounds={column: transform.upper_bounds[column] for column in columns},
        fit_end_date=transform.fit_end_date,
    )


def _wide_targets(
    metadata: pd.DataFrame,
    labels: pd.DataFrame,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    target = metadata[["code", "trade_date"]].copy()
    normalized = _normalize_keys(labels)
    normalized["label_end_date"] = (
        normalized["label_end_date"].astype("string").str.replace("-", "", regex=False).str[:8]
    )
    for horizon in horizons:
        part = normalized.loc[
            normalized["horizon"].eq(horizon),
            ["code", "trade_date", "label_end_date", "label", "excess_return"],
        ].rename(
            columns={
                "label_end_date": f"label_end_date_{horizon}",
                "label": f"label_{horizon}",
                "excess_return": f"excess_return_{horizon}",
            }
        )
        target = target.merge(
            part,
            on=["code", "trade_date"],
            how="inner",
            validate="one_to_one",
        )
    return target


def _sequence_indices(
    history: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    sequence_length: int,
    minimum_observations: int,
) -> tuple[np.ndarray, np.ndarray]:
    by_code: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for code, group in history.groupby("code", sort=False):
        by_code[str(code)] = (
            group["trade_date"].astype(str).to_numpy(),
            group.index.to_numpy(dtype=np.int32),
        )
    output = np.full((len(targets), sequence_length), -1, dtype=np.int32)
    valid = np.zeros(len(targets), dtype=bool)
    for code, target_group in targets.groupby("code", sort=False):
        history_dates, history_indices = by_code.get(
            str(code),
            (np.asarray([], dtype=str), np.asarray([], dtype=np.int32)),
        )
        if len(history_dates) == 0:
            continue
        rows = target_group.index.to_numpy()
        target_dates = target_group["trade_date"].astype(str).to_numpy()
        positions = np.searchsorted(history_dates, target_dates)
        exact = (positions < len(history_dates)) & (history_dates[np.minimum(positions, len(history_dates) - 1)] == target_dates)
        for row, position, is_exact in zip(rows, positions, exact):
            if not is_exact:
                continue
            start = max(0, int(position) - sequence_length + 1)
            indices = history_indices[start : int(position) + 1]
            if len(indices) < minimum_observations:
                continue
            output[row, : len(indices)] = indices
            valid[row] = True
    return output, valid


def _contexts(
    history: pd.DataFrame,
    history_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    value_columns = [f"value_{index}" for index in range(history_values.shape[1])]
    frame = pd.DataFrame(history_values, columns=value_columns, index=history.index)
    frame["trade_date"] = history["trade_date"].astype(str).to_numpy()
    industry_column = "industry_l2" if "industry_l2" in history.columns else "industry"
    if industry_column in history.columns:
        frame["industry"] = history[industry_column].fillna("未分类").astype(str).to_numpy()
    else:
        frame["industry"] = "未分类"
    market = frame.groupby("trade_date", sort=False)[value_columns].transform("mean")
    industry = frame.groupby(["trade_date", "industry"], sort=False)[value_columns].transform("mean")
    return (
        industry.to_numpy(dtype=np.float32),
        market.to_numpy(dtype=np.float32),
    )


def _build_split(
    base_metadata: pd.DataFrame,
    labels: pd.DataFrame,
    target_features: pd.DataFrame,
    history: pd.DataFrame,
    static_columns: tuple[str, ...],
    static_transform: FeatureTransform,
    industry_context: np.ndarray,
    market_context: np.ndarray,
    *,
    horizons: tuple[int, ...],
    sequence_length: int,
    minimum_sequence_observations: int,
) -> TemporalSplit:
    targets = _wide_targets(base_metadata, labels, horizons)
    targets = targets.merge(
        target_features,
        on=["code", "trade_date"],
        how="inner",
        validate="one_to_one",
    ).reset_index(drop=True)
    indices, valid = _sequence_indices(
        history,
        targets,
        sequence_length=sequence_length,
        minimum_observations=minimum_sequence_observations,
    )
    targets = targets.loc[valid].reset_index(drop=True)
    indices = indices[valid]
    sequence_lengths = np.sum(indices >= 0, axis=1).astype(np.int64)
    latest_indices = indices[np.arange(len(indices)), sequence_lengths - 1]
    class_index = {label: index for index, label in enumerate(CLASS_ORDER)}
    y_class = np.column_stack(
        [
            targets[f"label_{horizon}"].astype(str).map(class_index).to_numpy(dtype=np.int64)
            for horizon in horizons
        ]
    )
    if np.any(y_class < 0):
        raise ValueError("temporal_dataset_label")
    y_return = np.column_stack(
        [
            pd.to_numeric(targets[f"excess_return_{horizon}"], errors="coerce").to_numpy(
                dtype=np.float32
            )
            for horizon in horizons
        ]
    )
    if not np.isfinite(y_return).all():
        raise ValueError("temporal_dataset_return")
    static_values = (
        _apply_transform(targets, static_columns, static_transform)
        if static_columns
        else np.zeros((len(targets), 0), dtype=np.float32)
    )
    metadata_columns = ["code", "trade_date"] + [
        item
        for horizon in horizons
        for item in (
            f"label_end_date_{horizon}",
            f"label_{horizon}",
            f"excess_return_{horizon}",
        )
    ]
    return TemporalSplit(
        sequence_indices=indices,
        sequence_lengths=sequence_lengths,
        static_values=static_values,
        industry_context=industry_context[latest_indices],
        market_context=market_context[latest_indices],
        y_class=y_class,
        y_return=y_return,
        metadata=targets[metadata_columns].reset_index(drop=True),
    )


def prepare_temporal_dataset(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    sequence_length: int = 60,
    minimum_sequence_observations: int = 45,
    min_coverage: float = 0.55,
    min_nonzero_rows: int = 100,
    min_nonzero_ratio: float = 0.001,
    max_static_features: int = 32,
    intelligence_lifecycle_path: str | Path | None = None,
) -> PreparedTemporalDataset:
    """Build indexed sequences with train-only transforms and common labels."""

    horizons = tuple(int(value) for value in horizons)
    if not horizons or max(horizons) != 20:
        raise ValueError("temporal_dataset_horizons")
    if minimum_sequence_observations <= 0 or minimum_sequence_observations > sequence_length:
        raise ValueError("temporal_dataset_sequence_support")
    normalized_features = _normalize_keys(features).sort_values(
        ["code", "trade_date"],
        kind="stable",
    ).reset_index(drop=True)
    if normalized_features.duplicated(["code", "trade_date"]).any():
        raise ValueError("temporal_dataset_duplicate_feature_key")
    base = prepare_tabular_dataset(
        normalized_features,
        labels,
        horizon=max(horizons),
        min_coverage=min_coverage,
        min_nonzero_rows=min_nonzero_rows,
        min_nonzero_ratio=min_nonzero_ratio,
        max_features=max_static_features + len(SCALE_FREE_SEQUENCE_COLUMNS),
        intelligence_lifecycle_path=intelligence_lifecycle_path,
    )
    sequence_candidates = tuple(
        column
        for column in SCALE_FREE_SEQUENCE_COLUMNS
        if column in normalized_features.columns
    )
    train_history = normalized_features.loc[
        normalized_features["trade_date"].le(base.transform.fit_end_date)
    ]
    sequence_columns, sequence_transform, sequence_audit = _fit_transform(
        train_history,
        sequence_candidates,
        min_coverage=min_coverage,
        min_nonzero_rows=min_nonzero_rows,
        min_nonzero_ratio=min_nonzero_ratio,
        winsor_lower=0.01,
        winsor_upper=0.99,
    )
    registry_family = {item.name: item.family for item in DEFAULT_REGISTRY}
    static_columns = tuple(
        column
        for column in base.feature_columns
        if column not in sequence_columns
        and registry_family.get(column) != "technical"
    )[:max_static_features]
    if not static_columns:
        fallback = tuple(
            column for column in base.feature_columns if column not in sequence_columns
        )
        static_columns = fallback[:max_static_features]
    static_transform = _subset_transform(base.transform, static_columns)
    history_values = _apply_transform(
        normalized_features,
        sequence_columns,
        sequence_transform,
    )
    history_validity = np.column_stack(
        [
            pd.to_numeric(normalized_features[column], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            .to_numpy(dtype=np.uint8)
            for column in sequence_columns
        ]
    )
    industry_context, market_context = _contexts(
        normalized_features,
        history_values,
    )
    target_feature_columns = ["code", "trade_date", *static_columns]
    target_features = normalized_features[target_feature_columns]
    splits = [
        _build_split(
            split.metadata,
            labels,
            target_features,
            normalized_features,
            static_columns,
            static_transform,
            industry_context,
            market_context,
            horizons=horizons,
            sequence_length=sequence_length,
            minimum_sequence_observations=minimum_sequence_observations,
        )
        for split in (base.train, base.calibration, base.validation)
    ]
    if min(len(split.metadata) for split in splits) == 0:
        raise ValueError("temporal_dataset_empty_split")
    audit = {
        "sequence_length": sequence_length,
        "minimum_sequence_observations": minimum_sequence_observations,
        "sequence_columns": list(sequence_columns),
        "static_columns": list(static_columns),
        "split_rows": {
            name: len(split.metadata)
            for name, split in zip(("train", "calibration", "validation"), splits)
        },
        "split_dates": base.audit["split_dates"],
        "base_dataset_hash": base.dataset_hash,
        "sequence_feature_audit": sequence_audit,
        "intelligence_lifecycle": base.audit["intelligence_lifecycle"],
    }
    hash_payload = {
        "base": base.dataset_hash,
        "horizons": horizons,
        "sequence_length": sequence_length,
        "minimum_sequence_observations": minimum_sequence_observations,
        "sequence_columns": sequence_columns,
        "static_columns": static_columns,
        "sequence_transform": asdict(sequence_transform),
        "rows": audit["split_rows"],
    }
    dataset_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    history_metadata_columns = ["code", "trade_date"]
    for column in ("industry", "industry_l2"):
        if column in normalized_features.columns:
            history_metadata_columns.append(column)
    return PreparedTemporalDataset(
        horizons=horizons,
        sequence_length=sequence_length,
        sequence_columns=sequence_columns,
        static_columns=static_columns,
        history_values=history_values,
        history_validity=history_validity,
        history_metadata=normalized_features[history_metadata_columns].copy(),
        sequence_transform=sequence_transform,
        static_transform=static_transform,
        train=splits[0],
        calibration=splits[1],
        validation=splits[2],
        audit=audit,
        dataset_hash=dataset_hash,
    )
