"""Bounded CatBoost ranking adapter for account-scoped research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CatBoostRankerResult:
    model: Any
    feature_columns: tuple[str, ...]
    imputation_values: Mapping[str, float]
    parameters: Mapping[str, Any]
    validation_predictions: np.ndarray


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "code", "excess_return"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"catboost_ranker_columns:{','.join(missing)}")
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


def _matrix(
    frame: pd.DataFrame,
    columns: Sequence[str],
    imputation: Mapping[str, float],
) -> pd.DataFrame:
    values = pd.DataFrame(index=frame.index)
    for column in columns:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        values[column] = numeric.fillna(float(imputation[column]))
    return values


def fit_catboost_ranker(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    parameters: Mapping[str, Any],
    random_state: int,
    use_validation_for_eval: bool = False,
) -> CatBoostRankerResult:
    """Fit a deterministic ranker with preprocessing learned on train only."""

    from catboost import CatBoostRanker, Pool

    train_ordered = _ordered(train)
    validation_ordered = _ordered(validation)
    columns = tuple(str(column) for column in feature_columns)
    if not columns or any(column not in train_ordered.columns for column in columns):
        raise ValueError("catboost_ranker_features")
    imputation = {
        column: float(pd.to_numeric(train_ordered[column], errors="coerce").median())
        for column in columns
    }
    if any(not np.isfinite(value) for value in imputation.values()):
        raise ValueError("catboost_ranker_imputation")

    resolved = dict(parameters)
    resolved["iterations"] = min(1000, max(1, int(resolved.get("iterations", 500))))
    resolved["depth"] = min(6, max(1, int(resolved.get("depth", 4))))
    resolved["learning_rate"] = float(resolved.get("learning_rate", 0.03))
    resolved["l2_leaf_reg"] = float(resolved.get("l2_leaf_reg", 10.0))
    resolved.update({
        "loss_function": "YetiRank",
        "random_seed": int(random_state),
        "random_strength": 0.0,
        "bootstrap_type": "No",
        "allow_writing_files": False,
        "thread_count": 1,
        "verbose": False,
    })

    train_pool = Pool(
        _matrix(train_ordered, columns, imputation),
        label=pd.to_numeric(train_ordered["excess_return"], errors="coerce").fillna(0.0),
        group_id=train_ordered["trade_date"],
    )
    validation_pool = Pool(
        _matrix(validation_ordered, columns, imputation),
        label=(
            pd.to_numeric(validation_ordered["excess_return"], errors="coerce").fillna(0.0)
            if use_validation_for_eval else None
        ),
        group_id=validation_ordered["trade_date"],
    )
    model = CatBoostRanker(**resolved)
    if use_validation_for_eval:
        model.fit(train_pool, eval_set=validation_pool, use_best_model=False)
    else:
        model.fit(train_pool)
    predictions = np.asarray(model.predict(validation_pool), dtype=float)
    return CatBoostRankerResult(
        model=model,
        feature_columns=columns,
        imputation_values=imputation,
        parameters=resolved,
        validation_predictions=predictions,
    )


__all__ = ["CatBoostRankerResult", "fit_catboost_ranker"]
