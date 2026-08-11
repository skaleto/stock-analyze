"""Guarded prediction overlays for distinct defensive and trend strategies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf


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


def load_provider_return_history(
    provider: object,
    codes: list[str] | tuple[str, ...],
    *,
    as_of: object,
    days: int = 90,
) -> pd.DataFrame | None:
    normalized_codes = [_normalize_code(code) for code in codes]
    if hasattr(provider, "return_history"):
        try:
            frame = provider.return_history(codes, as_of=str(as_of), days=days)
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frame = frame.copy()
                frame.columns = [_normalize_code(column) for column in frame.columns]
                return frame.reindex(columns=normalized_codes)
        except Exception:  # noqa: BLE001 - covariance is an optional sizing input
            pass
    series: dict[str, pd.Series] = {}
    if not hasattr(provider, "price_history"):
        return None
    for raw_code, code in zip(codes, normalized_codes):
        try:
            history = provider.price_history(raw_code, as_of=str(as_of), days=days + 1)
        except Exception:  # noqa: BLE001 - one unavailable instrument should fail closed to fallback sizing
            return None
        if not isinstance(history, pd.DataFrame) or history.empty or "close" not in history.columns:
            return None
        dates = history.get("trade_date", history.index).astype(str)
        close = pd.to_numeric(history["close"], errors="coerce")
        series[code] = pd.Series(close.pct_change().to_numpy(), index=dates, name=code)
    combined = pd.concat(series.values(), axis=1).sort_index().tail(days)
    return combined if not combined.empty else None


def _capped_normalize(weights: pd.Series, cap: float) -> pd.Series:
    return _project_weight_budget(weights, cap=cap, budget=1.0)


def _project_weight_budget(
    weights: pd.Series,
    *,
    cap: float,
    budget: float,
    candidates: pd.DataFrame | None = None,
    group_constraints: Mapping[str, float] | None = None,
) -> pd.Series:
    output = pd.Series(0.0, index=weights.index)
    desired = weights.clip(lower=0.0).fillna(0.0)
    budget = min(max(float(budget), 0.0), 1.0)
    cap = min(max(float(cap), 0.0), 1.0)
    active = pd.Series(True, index=weights.index)
    constraints = {
        column: min(max(float(group_cap), 0.0), 1.0)
        for column, group_cap in (group_constraints or {}).items()
        if candidates is not None and column in candidates.columns
    }
    while budget - float(output.sum()) > 1e-10 and active.any():
        remaining_budget = budget - float(output.sum())
        active_desired = desired.where(active, 0.0)
        if active_desired.sum() <= 0.0:
            active_desired = active.astype(float)
        proposal = active_desired / active_desired.sum() * remaining_budget
        scale = 1.0
        for index in proposal.index[active]:
            if proposal.loc[index] > 0.0:
                scale = min(scale, max(cap - output.loc[index], 0.0) / proposal.loc[index])
        for column, group_cap in constraints.items():
            groups = candidates.loc[proposal.index, column].fillna("unclassified").astype(str)
            for group_name in groups.loc[active].unique():
                group_mask = groups.eq(group_name)
                proposed = float(proposal.loc[group_mask].sum())
                if proposed <= 0.0:
                    continue
                headroom = max(group_cap - float(output.loc[group_mask].sum()), 0.0)
                scale = min(scale, headroom / proposed)
        if scale <= 1e-12:
            break
        output += proposal * min(scale, 1.0)
        active &= output.lt(cap - 1e-10)
        for column, group_cap in constraints.items():
            groups = candidates.loc[output.index, column].fillna("unclassified").astype(str)
            for group_name in groups.unique():
                group_mask = groups.eq(group_name)
                if float(output.loc[group_mask].sum()) >= group_cap - 1e-10:
                    active.loc[group_mask] = False
    return output


def risk_adjusted_target_weights(
    candidates: pd.DataFrame,
    *,
    top_n: int,
    max_single_weight: float,
    current_weights: Mapping[str, float] | None = None,
    turnover_penalty: float = 0.20,
    min_trade_weight: float = 0.0,
    return_history: pd.DataFrame | None = None,
    gross_exposure: float = 1.0,
    group_constraints: Mapping[str, float] | None = None,
    risk_aversion: float = 1.0,
    cost_aversion: float = 1.0,
) -> dict[str, float]:
    """Build long-only, capped targets from risk and active model evidence.

    Inverse volatility is the stable base allocation. Active, high-confidence
    forecasts tilt that base rather than replacing it, which keeps one noisy
    forecast from collapsing diversification. Existing holdings are blended
    back in to penalize turnover, and a weight-level no-trade band suppresses
    immaterial changes before the final cap normalization.
    """

    ranked = (
        candidates.sort_values("score", ascending=False)
        if "score" in candidates.columns
        else candidates
    )
    selected = ranked.head(top_n).copy()
    if selected.empty:
        return {}
    selected["_code"] = selected["code"].map(_normalize_code)
    expected = pd.to_numeric(
        selected.get("expected_excess_return", pd.Series(np.nan, index=selected.index)),
        errors="coerce",
    )
    confidence = pd.to_numeric(
        selected.get("prediction_confidence", pd.Series(np.nan, index=selected.index)),
        errors="coerce",
    )
    volatility = pd.to_numeric(
        selected.get("expected_volatility", selected.get("low_volatility_60", pd.Series(0.20, index=selected.index))),
        errors="coerce",
    ).abs()
    valid_volatility = volatility[volatility.gt(0.0) & volatility.notna()]
    if valid_volatility.empty:
        base_utility = pd.Series(1.0, index=selected.index)
        volatility = pd.Series(0.20, index=selected.index)
    else:
        fallback_volatility = float(valid_volatility.median())
        volatility = volatility.where(volatility.gt(0.0), fallback_volatility).fillna(fallback_volatility)
        volatility = volatility.clip(
            lower=max(fallback_volatility * 0.25, 1e-4),
            upper=max(fallback_volatility * 4.0, 1e-4),
        )
        inverse_volatility = 1.0 / volatility
        median_inverse = float(inverse_volatility.median()) or 1.0
        base_utility = (inverse_volatility / median_inverse).clip(lower=0.50, upper=2.00)

    if "prediction_applied" in selected.columns:
        applied = selected["prediction_applied"].fillna(False).astype(bool)
    else:
        applied = expected.notna() & confidence.notna()
    alpha_utility = expected * confidence / volatility
    usable_alpha = alpha_utility.loc[applied & alpha_utility.notna()]
    tilt = pd.Series(1.0, index=selected.index)
    if not usable_alpha.empty:
        center = float(usable_alpha.median())
        mad = float((usable_alpha - center).abs().median())
        scale = mad * 1.4826
        if scale <= 1e-12:
            scale = max(float(usable_alpha.abs().max()), 1e-12)
        standardized = ((alpha_utility - center) / scale).clip(lower=-1.5, upper=1.5)
        tilt.loc[applied] = np.exp(0.45 * standardized.loc[applied].fillna(0.0))

    budget = min(max(float(gross_exposure), 0.0), 1.0)
    desired = _project_weight_budget(
        base_utility * tilt,
        cap=max_single_weight,
        budget=budget,
        candidates=selected,
        group_constraints=group_constraints,
    )

    if return_history is not None and len(selected) > 1:
        history = return_history.copy()
        history.columns = [_normalize_code(column) for column in history.columns]
        code_order = selected["_code"].tolist()
        available = [code for code in code_order if code in history.columns]
        numeric_history = history.loc[:, available].apply(pd.to_numeric, errors="coerce")
        if len(available) == len(code_order) and len(numeric_history.dropna(how="all")) >= 20:
            numeric_history = numeric_history.replace([np.inf, -np.inf], np.nan)
            numeric_history = numeric_history.fillna(numeric_history.median()).fillna(0.0)
            covariance = LedoitWolf().fit(numeric_history.to_numpy(dtype=float)).covariance_ * 252.0
            diagonal_scale = float(np.median(np.diag(covariance)))
            if diagonal_scale > 1e-12:
                covariance /= diagonal_scale
                anchor = desired.to_numpy(dtype=float)
                optimized = anchor.copy()
                for _ in range(40):
                    marginal_risk = np.maximum(covariance @ optimized, 0.0)
                    risk_utility = (anchor + 1e-8) / np.sqrt(np.diag(covariance) + marginal_risk + 1e-8)
                    risk_weights = _project_weight_budget(
                        pd.Series(risk_utility, index=selected.index),
                        cap=max_single_weight,
                        budget=budget,
                        candidates=selected,
                        group_constraints=group_constraints,
                    ).to_numpy(dtype=float)
                    blend = min(max(float(risk_aversion), 0.0) / (1.0 + max(float(risk_aversion), 0.0)), 0.85)
                    optimized = (1.0 - blend) * anchor + blend * risk_weights
                desired = _project_weight_budget(
                    pd.Series(optimized, index=selected.index),
                    cap=max_single_weight,
                    budget=budget,
                    candidates=selected,
                    group_constraints=group_constraints,
                )
    normalized_current: dict[str, float] = {}
    if current_weights:
        normalized_current = {
            _normalize_code(code): max(float(weight), 0.0)
            for code, weight in current_weights.items()
        }
        current = selected["_code"].map(normalized_current).fillna(0.0)
        penalty = min(max(float(turnover_penalty) * max(float(cost_aversion), 0.0), 0.0), 0.95)
        blended = (1.0 - penalty) * desired + penalty * current
        band = max(float(min_trade_weight), 0.0)
        if band > 0.0:
            keep = (desired - current).abs().lt(band)
            blended.loc[keep] = current.loc[keep]
        desired = _project_weight_budget(
            blended,
            cap=max_single_weight,
            budget=budget,
            candidates=selected,
            group_constraints=group_constraints,
        )

    weights = desired
    if weights.sum() <= 0:
        weights = _project_weight_budget(
            pd.Series(1.0, index=selected.index),
            cap=max_single_weight,
            budget=budget,
            candidates=selected,
            group_constraints=group_constraints,
        )
    return {
        code: float(weight)
        for code, weight in zip(selected["_code"], weights)
    }
