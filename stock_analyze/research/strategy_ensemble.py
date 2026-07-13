"""Guarded prediction overlays for distinct defensive and trend strategies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    family_weights: dict[str, float]
    prediction_weight: float


STRATEGY_PROFILES = {
    "defensive": StrategyProfile(
        "defensive",
        {"quality": 0.40, "risk": 0.20, "flow_breadth": 0.15, "technical": 0.10, "regime": 0.15},
        prediction_weight=0.20,
    ),
    "trend": StrategyProfile(
        "trend",
        {"technical": 0.32, "flow_breadth": 0.25, "industry": 0.18, "quality": 0.10, "regime": 0.15},
        prediction_weight=0.35,
    ),
}


_PROFILE_RANGES = {
    "defensive": {
        "quality": (0.35, 0.50),
        "risk": (0.15, 0.30),
        "flow_breadth": (0.10, 0.20),
        "technical": (0.05, 0.15),
        "regime": (0.10, 0.20),
    },
    "trend": {
        "technical": (0.25, 0.40),
        "flow_breadth": (0.20, 0.30),
        "industry": (0.15, 0.25),
        "quality": (0.05, 0.15),
        "regime": (0.10, 0.20),
    },
}


def validate_strategy_profiles(profiles: Mapping[str, StrategyProfile]) -> None:
    for name, ranges in _PROFILE_RANGES.items():
        if name not in profiles:
            raise ValueError(f"strategy_profile_missing:{name}")
        weights = profiles[name].family_weights
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError(f"strategy_profile_weight_sum:{name}")
        for family, (lower, upper) in ranges.items():
            value = weights.get(family)
            if value is None or not lower <= value <= upper:
                raise ValueError(f"strategy_profile_range:{name}:{family}")


validate_strategy_profiles(STRATEGY_PROFILES)


def _normalize_code(value: object) -> str:
    raw = str(value).split(".")[0]
    return raw.zfill(6) if raw.isdigit() else raw


def attach_active_predictions(
    candidates: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    profile: str,
    min_confidence: float = 0.70,
) -> pd.DataFrame:
    if profile not in STRATEGY_PROFILES:
        raise ValueError("strategy_profile")
    result = candidates.copy()
    result["code"] = result["code"].map(_normalize_code)
    result["base_score"] = pd.to_numeric(result["score"], errors="coerce")
    result["prediction_applied"] = False
    result["prediction_confidence"] = np.nan
    result["expected_excess_return"] = np.nan
    if predictions.empty:
        return result

    prediction_frame = predictions.copy()
    prediction_frame["code"] = prediction_frame["code"].map(_normalize_code)
    if "horizon" in prediction_frame.columns:
        preferred = prediction_frame.loc[pd.to_numeric(prediction_frame["horizon"], errors="coerce") == 5]
        if not preferred.empty:
            prediction_frame = preferred
    prediction_frame = prediction_frame.drop_duplicates("code", keep="last")
    columns = [
        column for column in (
            "code", "confidence", "expected_excess_return", "p_up", "p_down",
            "active_status", "invalidated",
        ) if column in prediction_frame.columns
    ]
    result = result.merge(prediction_frame[columns], on="code", how="left", suffixes=("", "_prediction"))
    confidence = pd.to_numeric(result.get("confidence"), errors="coerce")
    expected = pd.to_numeric(result.get("expected_excess_return_prediction", result.get("expected_excess_return")), errors="coerce")
    active = result.get("active_status", pd.Series("inactive", index=result.index)).astype(str).eq("active")
    invalidated = result.get("invalidated", pd.Series(False, index=result.index)).fillna(False).astype(bool)
    applied = active & confidence.ge(min_confidence) & expected.notna() & ~invalidated

    volatility = pd.to_numeric(
        result.get("expected_volatility", result.get("low_volatility_60", pd.Series(0.20, index=result.index))),
        errors="coerce",
    ).abs().clip(lower=0.05).fillna(0.20)
    risk_adjusted = pd.Series(0.0, index=result.index)
    risk_adjusted.loc[applied] = expected.loc[applied] * confidence.loc[applied] / volatility.loc[applied]
    active_scale = risk_adjusted.loc[applied].abs().max()
    normalized = risk_adjusted / active_scale if pd.notna(active_scale) and active_scale > 0 else risk_adjusted
    base_scale = max(float(result["base_score"].abs().median()), 0.5)
    adjustment = STRATEGY_PROFILES[profile].prediction_weight * normalized * base_scale
    result["score"] = result["base_score"] + adjustment.where(applied, 0.0)
    result["prediction_applied"] = applied
    result["prediction_confidence"] = confidence
    result["expected_excess_return"] = expected
    return result.drop(columns=[column for column in ("confidence", "expected_excess_return_prediction") if column in result.columns])


def load_and_attach_predictions(
    candidates: pd.DataFrame,
    *,
    repo_root: str | Path,
    market: str,
    agent: str,
    as_of: object,
    profile: str,
) -> pd.DataFrame:
    run_key = str(as_of).replace("-", "")[:8]
    path = Path(repo_root) / "data" / market / agent / "predictions" / f"{run_key}.parquet"
    if not path.exists():
        return attach_active_predictions(candidates, pd.DataFrame(), profile=profile)
    try:
        predictions = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - prediction overlay must never break base strategy
        result = attach_active_predictions(candidates, pd.DataFrame(), profile=profile)
        result["prediction_error"] = "prediction_artifact_unreadable"
        return result
    return attach_active_predictions(candidates, predictions, profile=profile)


def _capped_normalize(weights: pd.Series, cap: float) -> pd.Series:
    output = pd.Series(0.0, index=weights.index)
    remaining = weights.clip(lower=0.0).copy()
    budget = 1.0
    while budget > 1e-10 and remaining.sum() > 0:
        allocation = remaining / remaining.sum() * budget
        over = allocation > cap
        if not over.any():
            output += allocation
            break
        output.loc[over] = cap
        budget = 1.0 - float(output.sum())
        remaining.loc[over] = 0.0
    return output


def risk_adjusted_target_weights(
    candidates: pd.DataFrame,
    *,
    top_n: int,
    max_single_weight: float,
    current_weights: Mapping[str, float] | None = None,
    turnover_penalty: float = 0.20,
) -> dict[str, float]:
    selected = candidates.sort_values("score", ascending=False).head(top_n).copy()
    if selected.empty:
        return {}
    expected = pd.to_numeric(selected.get("expected_excess_return"), errors="coerce")
    confidence = pd.to_numeric(selected.get("prediction_confidence"), errors="coerce")
    volatility = pd.to_numeric(
        selected.get("expected_volatility", selected.get("low_volatility_60", pd.Series(0.20, index=selected.index))),
        errors="coerce",
    ).abs().clip(lower=0.05)
    utility = (expected * confidence / volatility).clip(lower=0.0)
    if utility.notna().sum() == 0 or utility.fillna(0.0).sum() <= 0:
        equal = 1.0 / len(selected)
        return {_normalize_code(code): equal for code in selected["code"]}
    utility = utility.fillna(0.0)
    if current_weights:
        current = selected["code"].map(_normalize_code).map(current_weights).fillna(0.0)
        utility = (1.0 - turnover_penalty) * utility + turnover_penalty * current
    weights = _capped_normalize(utility, max_single_weight)
    if weights.sum() <= 0:
        equal = 1.0 / len(selected)
        return {_normalize_code(code): equal for code in selected["code"]}
    return {_normalize_code(code): float(weight) for code, weight in zip(selected["code"], weights)}
