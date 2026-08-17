"""Bounded estimators for signed-IC residual-momentum signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler

from .signed_ic import SignedICConfig, SignedICSelection, select_signed_ic_features


@dataclass(frozen=True)
class SignedCandidatePrediction:
    estimator: str
    predictions: np.ndarray
    selection: SignedICSelection
    parameters: Mapping[str, Any]
    coefficients: Mapping[str, float]


def _date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("trade_date")["code"].transform("count")
    weights = 1.0 / pd.to_numeric(counts, errors="coerce").clip(lower=1.0)
    return (weights / weights.mean()).to_numpy(dtype=float)


def _matrices(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[np.ndarray, np.ndarray]:
    train_parts = []
    validation_parts = []
    for column in columns:
        train_values = pd.to_numeric(train[column], errors="coerce")
        median = float(train_values.median()) if train_values.notna().any() else 0.0
        train_parts.append(train_values.fillna(median).to_numpy(dtype=float))
        validation_parts.append(
            pd.to_numeric(validation[column], errors="coerce")
            .fillna(median)
            .to_numpy(dtype=float)
        )
    return np.column_stack(train_parts), np.column_stack(validation_parts)


def _capped_weights(
    weights: Mapping[str, float],
    *,
    cap: float = 0.35,
) -> dict[str, float]:
    if not weights:
        return {}
    result = {key: max(0.0, float(value)) for key, value in weights.items()}
    for _ in range(len(result) + 2):
        total = sum(result.values())
        if total <= 0.0:
            return {key: 1.0 / len(result) for key in result}
        result = {key: value / total for key, value in result.items()}
        over = {key for key, value in result.items() if value > cap}
        if not over:
            break
        fixed = cap * len(over)
        free = [key for key in result if key not in over]
        free_total = sum(result[key] for key in free)
        for key in over:
            result[key] = cap
        if free and free_total > 0.0:
            for key in free:
                result[key] = result[key] / free_total * (1.0 - fixed)
    total = sum(result.values())
    return {key: value / total for key, value in result.items()}


def fit_signed_candidate(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    candidate_features: Sequence[str],
    feature_families: Mapping[str, str],
    selector_config: SignedICConfig,
    estimator: str,
    parameters: Mapping[str, Any],
    seed: int,
) -> SignedCandidatePrediction:
    """Fit one candidate without reading validation labels."""

    ordered_train = train.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    ordered_validation = validation.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    selection = select_signed_ic_features(
        ordered_train,
        candidate_features=candidate_features,
        feature_families=feature_families,
        config=selector_config,
        seed=seed,
    )
    selected = selection.selected_features
    if not any(feature.startswith("exante_residual_momentum_") for feature in selected):
        raise ValueError("signed_ic_residual_required")
    if len(selected) < 2:
        raise ValueError("signed_ic_features_insufficient")
    transformed_train = selection.transform(ordered_train)
    transformed_validation = selection.transform(ordered_validation)
    train_x, validation_x = _matrices(
        transformed_train, transformed_validation, selected
    )
    scaler = StandardScaler().fit(train_x, sample_weight=_date_weights(ordered_train))
    train_scaled = scaler.transform(train_x)
    validation_scaled = scaler.transform(validation_x)
    coefficients: dict[str, float] = {}
    resolved = dict(parameters)
    if estimator == "signed_ic_composite":
        weights = _capped_weights(selection.weights)
        vector = np.asarray([weights[column] for column in selected], dtype=float)
        predictions = validation_scaled @ vector
        coefficients = dict(zip(selected, vector, strict=True))
        resolved["weight_cap"] = 0.35
    elif estimator == "positive_elastic_net":
        model = ElasticNet(
            alpha=float(resolved.get("alpha", 0.001)),
            l1_ratio=float(resolved.get("l1_ratio", 0.25)),
            positive=True,
            max_iter=5000,
            random_state=int(seed),
        ).fit(
            train_scaled,
            pd.to_numeric(ordered_train["excess_return"], errors="coerce").fillna(0.0),
            sample_weight=_date_weights(ordered_train),
        )
        predictions = model.predict(validation_scaled)
        coefficients = dict(zip(selected, model.coef_, strict=True))
    elif estimator == "monotone_lambdarank":
        from lightgbm import LGBMRanker

        relevance = np.minimum(
            (
                ordered_train.groupby("trade_date")["excess_return"]
                .rank(pct=True, method="first")
                * 5
            ).astype(int),
            4,
        )
        groups = ordered_train.groupby("trade_date", sort=False).size().tolist()
        model = LGBMRanker(
            objective="lambdarank",
            random_state=int(seed),
            n_jobs=1,
            verbosity=-1,
            monotone_constraints=[1] * len(selected),
            n_estimators=int(resolved.get("n_estimators", 250)),
            learning_rate=float(resolved.get("learning_rate", 0.03)),
            num_leaves=int(resolved.get("num_leaves", 15)),
            max_depth=int(resolved.get("max_depth", 5)),
            min_child_samples=int(resolved.get("min_child_samples", 200)),
            reg_lambda=float(resolved.get("reg_lambda", 10.0)),
        ).fit(train_scaled, relevance, group=groups)
        predictions = model.predict(validation_scaled)
    else:
        raise ValueError(f"signed_ic_estimator_unknown:{estimator}")
    return SignedCandidatePrediction(
        estimator=estimator,
        predictions=np.asarray(predictions, dtype=float),
        selection=selection,
        parameters=resolved,
        coefficients=coefficients,
    )


__all__ = ["SignedCandidatePrediction", "fit_signed_candidate"]
