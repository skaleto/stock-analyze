"""Point-in-time-safe tabular dataset preparation for deep research models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..feature_registry import INTELLIGENCE_FEATURES
from ..models import _select_features
from ...intelligence.lifecycle import load_factor_records, model_iteration_features


CLASS_ORDER = ("down", "flat", "up")
INTELLIGENCE_PREFIXES = (
    "event_",
    "announcement_",
    "policy_",
    "news_",
    "earnings_event_",
    "buyback_event_",
    "shareholder_flow_event_",
    "contract_event_",
    "corporate_action_event_",
    "legal_risk_event_",
    "delisting_risk_event_",
    "capital_structure_event_",
)
EXCLUDED_FEATURE_COLUMNS = {
    "code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "horizon",
    "label",
    "label_end_date",
    "absolute_return",
    "benchmark_return",
    "excess_return",
    "threshold",
    "max_favorable_excursion",
    "max_adverse_excursion",
}


def _date_key(values: pd.Series) -> pd.Series:
    return values.astype("string").str.replace("-", "", regex=False).str[:8]


@dataclass(frozen=True)
class DatasetSplit:
    x: np.ndarray
    y_class: np.ndarray
    y_return: np.ndarray
    metadata: pd.DataFrame


@dataclass(frozen=True)
class FeatureTransform:
    medians: dict[str, float]
    scales: dict[str, float]
    lower_bounds: dict[str, float]
    upper_bounds: dict[str, float]
    fit_end_date: str


@dataclass(frozen=True)
class PreparedDeepDataset:
    horizon: int
    feature_columns: tuple[str, ...]
    train: DatasetSplit
    calibration: DatasetSplit
    validation: DatasetSplit
    transform: FeatureTransform
    audit: dict[str, Any]
    dataset_hash: str


def _validate_keys(features: pd.DataFrame, labels: pd.DataFrame) -> None:
    required_features = {"code", "trade_date"}
    required_labels = {
        "code",
        "trade_date",
        "horizon",
        "label_end_date",
        "label",
        "excess_return",
    }
    if missing := required_features.difference(features.columns):
        raise ValueError(f"deep_dataset_missing_feature_columns:{','.join(sorted(missing))}")
    if missing := required_labels.difference(labels.columns):
        raise ValueError(f"deep_dataset_missing_label_columns:{','.join(sorted(missing))}")
    if features.duplicated(["code", "trade_date"]).any():
        raise ValueError("deep_dataset_duplicate_feature_key")
    if labels.duplicated(["code", "trade_date", "horizon"]).any():
        raise ValueError("deep_dataset_duplicate_label_key")


def _join_frames(features: pd.DataFrame, labels: pd.DataFrame, horizon: int) -> pd.DataFrame:
    _validate_keys(features, labels)
    feature_frame = features.copy()
    label_frame = labels.loc[labels["horizon"].eq(int(horizon))].copy()
    if label_frame.empty:
        raise ValueError(f"deep_dataset_horizon_missing:{horizon}")
    for frame in (feature_frame, label_frame):
        frame["code"] = frame["code"].astype("string").str.split(".").str[0].str.zfill(6)
        frame["trade_date"] = _date_key(frame["trade_date"])
    label_frame["label_end_date"] = _date_key(label_frame["label_end_date"])
    invalid_labels = sorted(set(label_frame["label"].dropna().astype(str)).difference(CLASS_ORDER))
    if invalid_labels:
        raise ValueError(f"deep_dataset_label:{','.join(invalid_labels)}")
    joined = feature_frame.merge(
        label_frame,
        on=["code", "trade_date"],
        how="inner",
        suffixes=("", "_label"),
        validate="one_to_one",
    )
    joined["excess_return"] = pd.to_numeric(joined["excess_return"], errors="coerce")
    joined = joined.loc[
        joined["label"].isin(CLASS_ORDER)
        & joined["excess_return"].replace([np.inf, -np.inf], np.nan).notna()
        & joined["label_end_date"].notna()
    ].sort_values(["trade_date", "code"], kind="stable")
    if joined.empty:
        raise ValueError("deep_dataset_empty")
    return joined.reset_index(drop=True)


def _split_point_in_time(
    frame: pd.DataFrame,
    *,
    train_fraction: float,
    calibration_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, str]]:
    dates = np.asarray(sorted(frame["trade_date"].astype(str).unique()))
    if len(dates) < 20:
        raise ValueError("deep_dataset_insufficient_dates")
    calibration_index = max(1, min(len(dates) - 2, int(len(dates) * train_fraction)))
    validation_index = max(
        calibration_index + 1,
        min(len(dates) - 1, int(len(dates) * (train_fraction + calibration_fraction))),
    )
    calibration_start = str(dates[calibration_index])
    validation_start = str(dates[validation_index])
    train = frame.loc[
        (frame["trade_date"] < calibration_start)
        & (frame["label_end_date"] < calibration_start)
    ].copy()
    calibration = frame.loc[
        (frame["trade_date"] >= calibration_start)
        & (frame["trade_date"] < validation_start)
        & (frame["label_end_date"] < validation_start)
    ].copy()
    validation = frame.loc[frame["trade_date"] >= validation_start].copy()
    if min(len(train), len(calibration), len(validation)) == 0:
        raise ValueError("deep_dataset_empty_split")
    return train, calibration, validation, {
        "train_start": str(train["trade_date"].min()),
        "train_end": str(train["trade_date"].max()),
        "calibration_start": str(calibration["trade_date"].min()),
        "calibration_end": str(calibration["trade_date"].max()),
        "validation_start": str(validation["trade_date"].min()),
        "validation_end": str(validation["trade_date"].max()),
    }


def _feature_candidates(
    frame: pd.DataFrame,
    intelligence_columns: set[str],
    permitted_intelligence_features: set[str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    candidates = []
    dropped: dict[str, str] = {}
    for column in frame.columns:
        if column in EXCLUDED_FEATURE_COLUMNS or column.endswith("_label"):
            continue
        if column in intelligence_columns and column not in permitted_intelligence_features:
            dropped[column] = "intelligence_lifecycle_not_promoted"
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().any():
            candidates.append(column)
    return tuple(sorted(candidates)), dropped


def _intelligence_lifecycle(
    frame: pd.DataFrame,
    path: str | Path | None,
) -> tuple[set[str], set[str], dict[str, Any]]:
    registered = {item.name for item in INTELLIGENCE_FEATURES}
    prefixed = {
        column
        for column in frame.columns
        if column.startswith(INTELLIGENCE_PREFIXES)
    }
    config_path = Path(path).resolve() if path is not None else None
    if config_path is None:
        return registered | prefixed, set(), {
            "config_path": None,
            "config_hash": None,
            "permitted_features": [],
            "policy": "fail_closed",
        }
    if not config_path.is_file():
        raise ValueError(f"deep_intelligence_lifecycle_missing:{config_path}")
    records = load_factor_records(config_path)
    permitted = model_iteration_features(config_path)
    return registered | prefixed | set(records), permitted, {
        "config_path": str(config_path),
        "config_hash": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "permitted_features": sorted(permitted),
        "policy": "evidence_qualified_model_iteration_or_active",
    }


def _fit_transform(
    train: pd.DataFrame,
    candidates: tuple[str, ...],
    *,
    min_coverage: float,
    min_nonzero_rows: int,
    min_nonzero_ratio: float,
    winsor_lower: float,
    winsor_upper: float,
) -> tuple[tuple[str, ...], FeatureTransform, dict[str, Any]]:
    selected: list[str] = []
    dropped: dict[str, str] = {}
    diagnostics: dict[str, dict[str, float | int]] = {}
    medians: dict[str, float] = {}
    scales: dict[str, float] = {}
    lower_bounds: dict[str, float] = {}
    upper_bounds: dict[str, float] = {}
    for column in candidates:
        values = pd.to_numeric(train[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        coverage = float(values.notna().mean())
        finite = values.dropna()
        nonzero_rows = int(finite.ne(0.0).sum())
        nonzero_ratio = float(nonzero_rows / max(len(finite), 1))
        unique_values = int(finite.nunique())
        diagnostics[column] = {
            "coverage": round(coverage, 6),
            "nonzero_rows": nonzero_rows,
            "nonzero_ratio": round(nonzero_ratio, 6),
            "unique_values": unique_values,
        }
        if coverage < min_coverage:
            dropped[column] = "low_coverage"
            continue
        if unique_values <= 1:
            dropped[column] = "constant"
            continue
        if nonzero_rows < min_nonzero_rows or nonzero_ratio < min_nonzero_ratio:
            dropped[column] = "insufficient_nonzero_support"
            continue
        lower = float(finite.quantile(winsor_lower))
        upper = float(finite.quantile(winsor_upper))
        clipped = finite.clip(lower, upper)
        median = float(clipped.median())
        q25 = float(clipped.quantile(0.25))
        q75 = float(clipped.quantile(0.75))
        scale = q75 - q25
        if not np.isfinite(scale) or scale <= 1e-12:
            scale = float(clipped.std(ddof=0))
        if not np.isfinite(scale) or scale <= 1e-12:
            dropped[column] = "zero_scale"
            continue
        selected.append(column)
        medians[column] = median
        scales[column] = scale
        lower_bounds[column] = lower
        upper_bounds[column] = upper
    if not selected:
        raise ValueError("deep_dataset_no_features")
    transform = FeatureTransform(
        medians=medians,
        scales=scales,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        fit_end_date=str(train["trade_date"].max()),
    )
    return tuple(selected), transform, {"dropped": dropped, "diagnostics": diagnostics}


def _apply_transform(frame: pd.DataFrame, columns: tuple[str, ...], transform: FeatureTransform) -> np.ndarray:
    output = np.empty((len(frame), len(columns)), dtype=np.float32)
    for index, column in enumerate(columns):
        values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        values = values.clip(transform.lower_bounds[column], transform.upper_bounds[column])
        values = values.fillna(transform.medians[column])
        output[:, index] = ((values - transform.medians[column]) / transform.scales[column]).to_numpy(
            dtype=np.float32
        )
    return output


def _to_split(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    transform: FeatureTransform,
) -> DatasetSplit:
    class_index = {label: index for index, label in enumerate(CLASS_ORDER)}
    return DatasetSplit(
        x=_apply_transform(frame, columns, transform),
        y_class=frame["label"].astype(str).map(class_index).to_numpy(dtype=np.int64),
        y_return=frame["excess_return"].to_numpy(dtype=np.float32),
        metadata=frame[
            ["code", "trade_date", "label_end_date", "label", "excess_return"]
        ].reset_index(drop=True),
    )


def prepare_tabular_dataset(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    horizon: int,
    train_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
    min_coverage: float = 0.55,
    min_nonzero_rows: int = 100,
    min_nonzero_ratio: float = 0.001,
    winsor_lower: float = 0.01,
    winsor_upper: float = 0.99,
    intelligence_lifecycle_path: str | Path | None = None,
    max_features: int = 48,
) -> PreparedDeepDataset:
    """Join, audit, split, and normalize a point-in-time training snapshot."""

    if not 0.0 < train_fraction < 1.0:
        raise ValueError("deep_dataset_train_fraction")
    if not 0.0 < calibration_fraction < 1.0 - train_fraction:
        raise ValueError("deep_dataset_calibration_fraction")
    joined = _join_frames(features, labels, horizon)
    train, calibration, validation, split_dates = _split_point_in_time(
        joined,
        train_fraction=train_fraction,
        calibration_fraction=calibration_fraction,
    )
    intelligence_columns, permitted_intelligence, lifecycle_audit = _intelligence_lifecycle(
        joined,
        intelligence_lifecycle_path,
    )
    candidates, lifecycle_dropped = _feature_candidates(
        joined,
        intelligence_columns,
        permitted_intelligence,
    )
    eligible_columns, transform, audit = _fit_transform(
        train,
        candidates,
        min_coverage=min_coverage,
        min_nonzero_rows=min_nonzero_rows,
        min_nonzero_ratio=min_nonzero_ratio,
        winsor_lower=winsor_lower,
        winsor_upper=winsor_upper,
    )
    selection_frame = train.copy()
    for column in eligible_columns:
        selection_frame[column] = pd.to_numeric(
            selection_frame[column],
            errors="coerce",
        ).replace([np.inf, -np.inf], np.nan)
    columns, selection_audit = _select_features(
        selection_frame,
        eligible_columns,
        max_features=max_features,
        min_coverage=min_coverage,
        min_abs_ic=0.0,
        min_stability=0.0,
        min_ic_t_stat=0.0,
    )
    transform = FeatureTransform(
        medians={column: transform.medians[column] for column in columns},
        scales={column: transform.scales[column] for column in columns},
        lower_bounds={column: transform.lower_bounds[column] for column in columns},
        upper_bounds={column: transform.upper_bounds[column] for column in columns},
        fit_end_date=transform.fit_end_date,
    )
    audit["dropped"] = {**lifecycle_dropped, **audit["dropped"]}
    for column in set(eligible_columns).difference(columns):
        audit["dropped"][column] = "feature_selection_pruned"
    audit.update(
        {
            "input_rows": len(joined),
            "candidate_feature_count": len(candidates),
            "selected_feature_count": len(columns),
            "selected_features": list(columns),
            "feature_selection": selection_audit,
            "split_dates": split_dates,
            "split_rows": {
                "train": len(train),
                "calibration": len(calibration),
                "validation": len(validation),
            },
            "class_order": list(CLASS_ORDER),
            "intelligence_lifecycle": lifecycle_audit,
        }
    )
    hash_payload = {
        "horizon": int(horizon),
        "features": columns,
        "split_dates": split_dates,
        "rows": audit["split_rows"],
        "transform": {
            "medians": transform.medians,
            "scales": transform.scales,
            "lower_bounds": transform.lower_bounds,
            "upper_bounds": transform.upper_bounds,
        },
    }
    digest = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    identity_columns = ["code", "trade_date", "label_end_date", "label", "excess_return"]
    digest.update(
        pd.util.hash_pandas_object(joined[identity_columns], index=False).to_numpy().tobytes()
    )
    for column in columns:
        digest.update(
            pd.util.hash_pandas_object(
                pd.to_numeric(joined[column], errors="coerce"),
                index=False,
            ).to_numpy().tobytes()
        )
    dataset_hash = digest.hexdigest()[:16]
    return PreparedDeepDataset(
        horizon=int(horizon),
        feature_columns=columns,
        train=_to_split(train, columns, transform),
        calibration=_to_split(calibration, columns, transform),
        validation=_to_split(validation, columns, transform),
        transform=transform,
        audit=audit,
        dataset_hash=dataset_hash,
    )
