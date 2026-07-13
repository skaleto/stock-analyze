"""Deterministic point-in-time market and industry regime classification."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

import numpy as np
import pandas as pd


_COMPONENT_WEIGHTS: dict[str, dict[str, float]] = {
    "trend_score": {"index_momentum_20": 0.6, "index_momentum_60": 0.4},
    "volatility_score": {"realized_volatility_20": 0.7, "natr_14": 0.3},
    "liquidity_score": {"amount_momentum_20": 0.35, "flow_persistence": 0.25, "margin_momentum": 0.2, "shibor_change": -0.2},
    "macro_score": {"pmi_change": 0.35, "m2_change": 0.25, "yield_curve_slope": 0.2, "cpi_change": -0.1, "ppi_change": -0.1},
    "global_risk_score": {"global_index_momentum": 0.4, "global_volatility": -0.3, "rmb_depreciation": -0.15, "us_yield_change": -0.15},
}


def _expanding_robust_z(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    values: list[float] = []
    output: list[float] = []
    for value in numeric:
        if pd.notna(value):
            values.append(float(value))
        if pd.isna(value) or len(values) < 3:
            output.append(np.nan if pd.isna(value) else 0.0)
            continue
        history = np.asarray(values, dtype=float)
        median = float(np.median(history))
        mad = float(np.median(np.abs(history - median))) * 1.4826
        scale = mad if mad > 1e-12 else float(np.std(history, ddof=1))
        output.append(float(np.clip((float(value) - median) / scale, -5.0, 5.0)) if scale > 1e-12 else 0.0)
    return pd.Series(output, index=series.index, dtype=float)


def _ensure_scores(frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame.copy()
    for score_name, weights in _COMPONENT_WEIGHTS.items():
        if score_name in scored.columns:
            scored[score_name] = pd.to_numeric(scored[score_name], errors="coerce")
            continue
        weighted = pd.Series(0.0, index=scored.index)
        available_weight = pd.Series(0.0, index=scored.index)
        for component, weight in weights.items():
            if component not in scored.columns:
                continue
            normalized = _expanding_robust_z(scored[component])
            present = normalized.notna()
            weighted = weighted.add(normalized.fillna(0.0) * weight)
            available_weight = available_weight.add(present.astype(float) * abs(weight))
        scored[score_name] = weighted / available_weight.replace(0.0, np.nan)
    return scored


def _three_way(score: float, positive: str, neutral: str, negative: str, threshold: float = 0.35) -> str:
    if not np.isfinite(score):
        return "unknown"
    if score > threshold:
        return positive
    if score < -threshold:
        return negative
    return neutral


def _macro_state(scores: pd.Series) -> pd.Series:
    delta = scores.diff()
    states = []
    for score, change in zip(scores, delta):
        if not np.isfinite(score):
            states.append("unknown")
        elif score > 0.25 and (not np.isfinite(change) or change >= 0):
            states.append("expansion")
        elif score < -0.25 and (not np.isfinite(change) or change <= 0):
            states.append("contraction")
        elif np.isfinite(change) and change > 0:
            states.append("recovery")
        else:
            states.append("slowdown")
    return pd.Series(states, index=scores.index, dtype="string")


def _apply_hysteresis(candidates: pd.Series, persistence: int) -> tuple[pd.Series, pd.Series]:
    active = "unknown"
    pending = "unknown"
    pending_count = 0
    visits: defaultdict[str, int] = defaultdict(int)
    transitions: defaultdict[str, int] = defaultdict(int)
    states: list[str] = []
    probabilities: list[float] = []
    for candidate in candidates.fillna("unknown").astype(str):
        if candidate == "unknown":
            states.append(active)
            probabilities.append(np.nan)
            continue
        visits[candidate] += 1
        if candidate == active:
            pending = candidate
            pending_count = 0
        elif candidate == pending:
            pending_count += 1
        else:
            pending = candidate
            pending_count = 1
        probability = (transitions[candidate] + 1.0) / (visits[candidate] + 2.0)
        if pending_count >= max(1, persistence):
            if candidate != active:
                transitions[candidate] += 1
            active = candidate
            pending_count = 0
            probability = (transitions[candidate] + 1.0) / (visits[candidate] + 2.0)
        states.append(active)
        probabilities.append(float(np.clip(probability, 0.0, 1.0)))
    return pd.Series(states, index=candidates.index, dtype="string"), pd.Series(probabilities, index=candidates.index, dtype=float)


def _classify_group(frame: pd.DataFrame, min_coverage: float, persistence: int) -> pd.DataFrame:
    result = _ensure_scores(frame.sort_values("trade_date")).copy()
    classifiers: dict[str, Callable[[pd.Series], pd.Series]] = {
        "trend": lambda scores: scores.map(lambda value: _three_way(value, "up", "flat", "down")),
        "volatility": lambda scores: scores.map(lambda value: _three_way(value, "high", "normal", "low", 0.5)),
        "liquidity": lambda scores: scores.map(lambda value: _three_way(value, "expanding", "neutral", "contracting")),
        "macro": _macro_state,
        "global_risk": lambda scores: scores.map(lambda value: _three_way(value, "risk_on", "neutral", "risk_off")),
    }
    for dimension, classifier in classifiers.items():
        candidates = classifier(result[f"{dimension}_score"])
        states, probabilities = _apply_hysteresis(candidates, persistence)
        result[f"{dimension}_regime"] = states
        result[f"{dimension}_transition_probability"] = probabilities

    score_columns = list(_COMPONENT_WEIGHTS)
    result["regime_coverage"] = result[score_columns].notna().mean(axis=1)
    risk_balance = (
        result["trend_score"].fillna(0.0)
        + result["liquidity_score"].fillna(0.0)
        + result["macro_score"].fillna(0.0)
        + result["global_risk_score"].fillna(0.0)
        - result["volatility_score"].fillna(0.0)
    ) / 5.0
    result["composite_regime"] = np.select(
        [
            result["regime_coverage"] < min_coverage,
            risk_balance > 0.35,
            risk_balance < -0.35,
        ],
        ["unknown", "risk_on", "risk_off"],
        default="mixed",
    )
    return result


def classify_regimes(
    components: pd.DataFrame,
    *,
    min_coverage: float = 0.70,
    persistence: int = 2,
) -> pd.DataFrame:
    if "trade_date" not in components.columns:
        raise ValueError("regime_missing_trade_date")
    if components.empty:
        return components.copy()
    scope_column = "scope" if "scope" in components.columns else None
    groups = components.groupby(scope_column, sort=False, dropna=False) if scope_column else [("market", components)]
    classified = [_classify_group(group, min_coverage, persistence) for _, group in groups]
    return pd.concat(classified, ignore_index=True).sort_values(([scope_column] if scope_column else []) + ["trade_date"]).reset_index(drop=True)
