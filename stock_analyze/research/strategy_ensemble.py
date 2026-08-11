"""Guarded prediction overlays for distinct defensive and trend strategies."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from .risk_model import (
    PortfolioLimits,
    PortfolioProblem,
    PortfolioSolution,
    optimize_portfolio,
)


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    family_weights: dict[str, float]
    prediction_weight: float


@dataclass(frozen=True)
class CostAwareTransitionResult:
    weights: dict[str, float]
    decisions: pd.DataFrame


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


def _finite_float(value: object, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    return numeric if math.isfinite(numeric) else float(default)


def _boolean_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def apply_cost_aware_transition(
    candidates: pd.DataFrame,
    *,
    aim_weights: Mapping[str, float],
    current_weights: Mapping[str, float] | None,
    top_n: int,
    rank_buffer_pct: float,
    minimum_target_change: float,
    partial_adjustment_rate: float,
    max_daily_turnover: float,
    cost_safety_multiple: float,
    alpha_persistence: float = 1.0,
    default_round_trip_cost_bps: float = 0.0,
    gross_exposure: float = 1.0,
) -> CostAwareTransitionResult:
    """Move toward an aim portfolio only when forecast edge pays for trading.

    Hard risk exits bypass the target-change band, cost gate, partial move, and
    daily turnover cap. Ordinary changes retain buffered holdings, observe a
    no-trade band, and move only part of the distance toward the frictionless
    target. The returned decisions are the audit contract used by replay,
    Shadow, and the dashboard.
    """

    if "code" not in candidates.columns:
        raise ValueError("cost_aware_candidates_missing_code")
    frame = candidates.copy()
    frame["_code"] = frame["code"].map(_normalize_code)
    if "score" in frame.columns:
        frame["_score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(-np.inf)
        frame = frame.sort_values(["_score", "_code"], ascending=[False, True], kind="stable")
    else:
        frame = frame.sort_values("_code", kind="stable")
    frame = frame.drop_duplicates("_code", keep="first").reset_index(drop=True)
    frame["_rank"] = np.arange(1, len(frame) + 1)
    by_code = frame.set_index("_code", drop=False)

    current = {
        _normalize_code(code): max(_finite_float(weight), 0.0)
        for code, weight in (current_weights or {}).items()
    }
    aim = {
        _normalize_code(code): max(_finite_float(weight), 0.0)
        for code, weight in aim_weights.items()
        if _normalize_code(code) in by_code.index and _finite_float(weight) > 0.0
    }
    position_limit = max(int(top_n), 1)
    buffer_count = max(
        position_limit,
        int(math.ceil(position_limit * (1.0 + max(float(rank_buffer_pct), 0.0)))),
    )
    rank_by_code = {
        str(code): int(rank)
        for code, rank in zip(frame["_code"], frame["_rank"])
    }
    retained = sorted(
        (
            code for code, weight in current.items()
            if weight > 0.0 and rank_by_code.get(code, buffer_count + 1) <= buffer_count
        ),
        key=lambda code: (rank_by_code.get(code, buffer_count + 1), code),
    )[:position_limit]
    selected = list(retained)
    for code in sorted(aim, key=lambda item: (rank_by_code.get(item, len(frame) + 1), item)):
        if code not in selected:
            selected.append(code)
        if len(selected) >= position_limit:
            break
    selected_set = set(selected)

    selected_expected = [
        _finite_float(by_code.loc[code].get("expected_excess_return"))
        for code in selected
        if code in by_code.index
    ]
    best_selected_expected = max(selected_expected, default=0.0)
    all_codes = sorted(set(current).union(selected_set))
    raw_targets: dict[str, float] = {}
    raw_decisions: dict[str, dict[str, object]] = {}
    ordinary_deltas: dict[str, float] = {}
    risk_exit_codes: set[str] = set()
    band = max(float(minimum_target_change), 0.0)
    adjustment_rate = min(max(float(partial_adjustment_rate), 0.0), 1.0)
    persistence = max(float(alpha_persistence), 0.0)
    safety_multiple = max(float(cost_safety_multiple), 0.0)

    for code in all_codes:
        row = by_code.loc[code] if code in by_code.index else pd.Series(dtype=object)
        current_weight = current.get(code, 0.0)
        desired_weight = aim.get(code, 0.0) if code in selected_set else 0.0
        buffered_hold = code in retained and code not in aim
        if buffered_hold:
            desired_weight = current_weight
        hard_risk_exit = _boolean_flag(
            row.get("hard_risk_exit", row.get("risk_exit", False))
        )
        expected_return = _finite_float(row.get("expected_excess_return"))
        round_trip_cost_bps = max(
            _finite_float(row.get("round_trip_cost_bps"), default_round_trip_cost_bps),
            0.0,
        )
        uncertainty_bps = _finite_float(
            row.get("prediction_uncertainty_bps", row.get("uncertainty_bps")),
            -1.0,
        )
        if uncertainty_bps < 0.0:
            lower = _finite_float(row.get("return_q10"), expected_return)
            upper = _finite_float(row.get("return_q90"), expected_return)
            confidence = min(max(_finite_float(row.get("confidence"), 1.0), 0.0), 1.0)
            uncertainty_bps = abs(upper - lower) / (2.0 * 1.281551565545) * (1.0 - confidence) * 10_000.0
        proposed_delta = desired_weight - current_weight
        if proposed_delta >= 0.0:
            gross_edge_bps = max(expected_return, 0.0) * persistence * 10_000.0
        else:
            gross_edge_bps = max(
                best_selected_expected - expected_return,
                -expected_return,
                0.0,
            ) * persistence * 10_000.0
        required_edge_bps = round_trip_cost_bps * safety_multiple + max(uncertainty_bps, 0.0)
        net_edge_bps = gross_edge_bps - required_edge_bps
        reason = ""
        trade_allowed = False
        applied_rate = adjustment_rate
        target_weight = current_weight
        if hard_risk_exit and current_weight > 0.0:
            target_weight = 0.0
            trade_allowed = True
            applied_rate = 1.0
            reason = "hard_risk_exit"
            risk_exit_codes.add(code)
        elif buffered_hold:
            reason = "rank_buffer_hold"
        elif abs(proposed_delta) < band:
            reason = "target_change_below_band"
        elif net_edge_bps <= 0.0:
            reason = "insufficient_net_edge"
        elif adjustment_rate <= 0.0:
            reason = "partial_adjustment_disabled"
        else:
            target_weight = current_weight + adjustment_rate * proposed_delta
            trade_allowed = abs(target_weight - current_weight) > 1e-12
            ordinary_deltas[code] = target_weight - current_weight
        raw_targets[code] = max(target_weight, 0.0)
        raw_decisions[code] = {
            "code": code,
            "rank": rank_by_code.get(code),
            "current_weight": current_weight,
            "aim_weight": desired_weight,
            "target_weight": max(target_weight, 0.0),
            "gross_expected_edge_bps": gross_edge_bps,
            "round_trip_cost_bps": round_trip_cost_bps,
            "uncertainty_bps": max(uncertainty_bps, 0.0),
            "net_expected_edge_bps": net_edge_bps,
            "trade_allowed": trade_allowed,
            "no_trade_reason": reason,
            "partial_adjustment_rate": applied_rate,
        }

    ordinary_turnover = sum(abs(delta) for delta in ordinary_deltas.values())
    turnover_limit = max(float(max_daily_turnover), 0.0)
    turnover_scale = (
        min(turnover_limit / ordinary_turnover, 1.0)
        if ordinary_turnover > 1e-12 else 1.0
    )
    if turnover_scale < 1.0:
        for code, delta in ordinary_deltas.items():
            raw_targets[code] = current.get(code, 0.0) + delta * turnover_scale

    exposure_limit = min(max(float(gross_exposure), 0.0), 1.0)
    excess = sum(raw_targets.values()) - exposure_limit
    if excess > 1e-12:
        positive_deltas = {
            code: max(target - current.get(code, 0.0), 0.0)
            for code, target in raw_targets.items()
            if code not in risk_exit_codes
        }
        buy_total = sum(positive_deltas.values())
        buy_scale = max(1.0 - excess / buy_total, 0.0) if buy_total > 1e-12 else 0.0
        for code, delta in positive_deltas.items():
            raw_targets[code] = current.get(code, 0.0) + delta * buy_scale

    decision_rows: list[dict[str, object]] = []
    for code in all_codes:
        target = max(raw_targets.get(code, 0.0), 0.0)
        decision = raw_decisions[code]
        decision["target_weight"] = target
        changed = abs(target - current.get(code, 0.0)) > 1e-12
        decision["trade_allowed"] = changed
        if changed and code not in risk_exit_codes:
            decision["no_trade_reason"] = ""
        decision_rows.append(decision)

    return CostAwareTransitionResult(
        weights={code: float(weight) for code, weight in raw_targets.items() if weight > 1e-10},
        decisions=pd.DataFrame(decision_rows),
    )


def attach_active_predictions(
    candidates: pd.DataFrame,
    predictions: pd.DataFrame,
    *,
    profile: str,
    min_confidence: float = 0.70,
    horizon_weights: Mapping[int, float] | None = None,
    as_of: object | None = None,
    max_prediction_age_days: int = 5,
) -> pd.DataFrame:
    if profile not in STRATEGY_PROFILES:
        raise ValueError("strategy_profile")
    result = candidates.copy()
    result["code"] = result["code"].map(_normalize_code)
    result["base_score"] = pd.to_numeric(result["score"], errors="coerce")
    result["prediction_applied"] = False
    result["prediction_confidence"] = np.nan
    result["expected_excess_return"] = np.nan
    result["prediction_horizons"] = ""
    result["prediction_model_versions"] = ""
    result["prediction_fallback_reason"] = "prediction_artifact_missing"
    if predictions.empty:
        return result

    prediction_frame = predictions.copy()
    prediction_frame["code"] = prediction_frame["code"].map(_normalize_code)
    if horizon_weights is not None:
        declared = {
            int(horizon): float(weight)
            for horizon, weight in horizon_weights.items()
            if float(weight) > 0.0
        }
        total_weight = sum(declared.values())
        if not declared or total_weight <= 0.0:
            raise ValueError("prediction_horizon_policy")
        declared = {horizon: weight / total_weight for horizon, weight in declared.items()}
        if "horizon" not in prediction_frame.columns:
            return result.assign(prediction_fallback_reason="prediction_horizon_missing")
        prediction_frame["horizon"] = pd.to_numeric(
            prediction_frame["horizon"], errors="coerce"
        ).astype("Int64")
        prediction_frame = prediction_frame.loc[prediction_frame["horizon"].isin(declared)].copy()
        status_column = "ranker_status" if "ranker_status" in prediction_frame.columns else "active_status"
        confidence_column = (
            "ranker_confidence" if "ranker_confidence" in prediction_frame.columns else "confidence"
        )
        prediction_frame["_active"] = prediction_frame.get(
            status_column, pd.Series("inactive", index=prediction_frame.index)
        ).astype(str).eq("active")
        prediction_frame["_confidence"] = pd.to_numeric(
            prediction_frame.get(confidence_column), errors="coerce"
        )
        prediction_frame["_expected"] = pd.to_numeric(
            prediction_frame.get("expected_excess_return"), errors="coerce"
        )
        prediction_frame["_invalidated"] = prediction_frame.get(
            "invalidated", pd.Series(False, index=prediction_frame.index)
        ).fillna(False).astype(bool)
        if as_of is not None:
            observed = pd.to_datetime(prediction_frame.get("as_of"), errors="coerce")
            decision_day = pd.Timestamp(str(as_of)[:10])
            age = (decision_day - observed.dt.normalize()).dt.days
            prediction_frame["_fresh"] = age.between(0, max(int(max_prediction_age_days), 0))
        else:
            prediction_frame["_fresh"] = True
        prediction_frame = prediction_frame.drop_duplicates(["code", "horizon"], keep="last")
        blends: dict[str, dict[str, object]] = {}
        for code, rows in prediction_frame.groupby("code", sort=False):
            indexed = rows.set_index("horizon")
            if any(horizon not in indexed.index for horizon in declared):
                continue
            selected = indexed.loc[list(declared)]
            eligible = (
                selected["_active"].astype(bool)
                & selected["_fresh"].astype(bool)
                & ~selected["_invalidated"].astype(bool)
                & selected["_confidence"].ge(min_confidence)
                & selected["_expected"].notna()
            )
            if not bool(eligible.all()):
                continue
            weights = pd.Series(declared, dtype=float).reindex(selected.index)
            versions = selected.get(
                "model_version", pd.Series("", index=selected.index)
            ).astype(str)
            blends[str(code)] = {
                "confidence": float((selected["_confidence"] * weights).sum()),
                "expected": float((selected["_expected"] * weights).sum()),
                "horizons": ",".join(str(value) for value in selected.index),
                "versions": ",".join(versions.tolist()),
            }
        confidence = result["code"].map(
            {code: values["confidence"] for code, values in blends.items()}
        )
        expected = result["code"].map(
            {code: values["expected"] for code, values in blends.items()}
        )
        applied = confidence.notna() & expected.notna()
        result["prediction_horizons"] = result["code"].map(
            {code: values["horizons"] for code, values in blends.items()}
        ).fillna("")
        result["prediction_model_versions"] = result["code"].map(
            {code: values["versions"] for code, values in blends.items()}
        ).fillna("")
        result["prediction_fallback_reason"] = np.where(
            applied, "", "declared_horizon_unavailable_or_ineligible"
        )
    else:
        prediction_frame = prediction_frame.drop_duplicates("code", keep="last")
        prediction_by_code = prediction_frame.set_index("code")
        confidence = result["code"].map(
            pd.to_numeric(prediction_by_code.get("confidence"), errors="coerce")
        )
        expected = result["code"].map(
            pd.to_numeric(prediction_by_code.get("expected_excess_return"), errors="coerce")
        )
        status_column = "ranker_status" if "ranker_status" in prediction_by_code.columns else "active_status"
        active = result["code"].map(
            prediction_by_code.get(status_column, pd.Series("inactive", index=prediction_by_code.index))
        ).fillna("inactive").astype(str).eq("active")
        invalidated = result["code"].map(
            prediction_by_code.get("invalidated", pd.Series(False, index=prediction_by_code.index))
        ).fillna(False).astype(bool)
        applied = active & confidence.ge(min_confidence) & expected.notna() & ~invalidated
        result["prediction_fallback_reason"] = np.where(
            applied, "", "prediction_inactive_low_confidence_or_invalid"
        )

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
    return result


def load_model_policy(repo_root: str | Path, profile: str) -> dict[str, object] | None:
    path = Path(repo_root) / "configs" / "strategy_competition.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = (payload.get("model_policy") or {}).get(profile)
    if not isinstance(raw, dict):
        return None
    weights = raw.get("horizon_weights")
    if not isinstance(weights, dict) or not weights:
        return None
    return {
        "required_role": str(raw.get("required_role") or "ranker"),
        "horizon_weights": {int(key): float(value) for key, value in weights.items()},
        "max_prediction_age_days": int(raw.get("max_prediction_age_days", 5)),
        "missing_behavior": str(raw.get("missing_behavior") or "rule_only"),
    }


def load_and_attach_predictions(
    candidates: pd.DataFrame,
    *,
    repo_root: str | Path,
    market: str,
    agent: str,
    as_of: object,
    profile: str,
) -> pd.DataFrame:
    policy = load_model_policy(repo_root, profile)
    attach_kwargs = {
        "profile": profile,
        "as_of": as_of,
    }
    if policy:
        attach_kwargs.update(
            {
                "horizon_weights": policy["horizon_weights"],
                "max_prediction_age_days": policy["max_prediction_age_days"],
            }
        )
    run_key = str(as_of).replace("-", "")[:8]
    path = Path(repo_root) / "data" / market / agent / "predictions" / f"{run_key}.parquet"
    if not path.exists():
        return attach_active_predictions(candidates, pd.DataFrame(), **attach_kwargs)
    try:
        predictions = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - prediction overlay must never break base strategy
        result = attach_active_predictions(candidates, pd.DataFrame(), **attach_kwargs)
        result["prediction_error"] = "prediction_artifact_unreadable"
        return result
    return attach_active_predictions(candidates, predictions, **attach_kwargs)


def load_provider_return_history(
    provider: object,
    codes: list[str] | tuple[str, ...],
    *,
    as_of: object,
    days: int = 90,
) -> pd.DataFrame | None:
    code_pairs: dict[str, str] = {}
    for raw_code in codes:
        code_pairs.setdefault(_normalize_code(raw_code), str(raw_code))
    normalized_codes = list(code_pairs)
    requested_codes = list(code_pairs.values())
    if hasattr(provider, "return_history"):
        try:
            frame = provider.return_history(
                requested_codes,
                as_of=str(as_of),
                days=days,
            )
            if isinstance(frame, pd.DataFrame) and not frame.empty:
                frame = frame.copy()
                frame.columns = [_normalize_code(column) for column in frame.columns]
                return frame.reindex(columns=normalized_codes)
        except Exception:  # noqa: BLE001 - covariance is an optional sizing input
            pass
    series: dict[str, pd.Series] = {}
    if not hasattr(provider, "price_history"):
        return None
    for code, raw_code in code_pairs.items():
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
    exposure_constraints: Mapping[str, float] | None = None,
    risk_aversion: float = 1.0,
    cost_aversion: float = 1.0,
    max_turnover: float = 1.0,
    benchmark_weights: Mapping[str, float] | None = None,
    diagnostics: dict[str, object] | None = None,
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
    ranked = ranked.copy()
    market_beta, market_beta_source = _market_beta_estimates(
        ranked,
        return_history=return_history,
        benchmark_weights=benchmark_weights,
    )
    ranked["market_beta"] = market_beta
    joint_solution = _joint_portfolio_solution(
        ranked,
        top_n=top_n,
        max_single_weight=max_single_weight,
        current_weights=current_weights,
        return_history=return_history,
        gross_exposure=gross_exposure,
        group_constraints=group_constraints,
        exposure_constraints=exposure_constraints,
        risk_aversion=risk_aversion,
        cost_aversion=cost_aversion,
        max_turnover=max_turnover,
        benchmark_weights=benchmark_weights,
    )
    if diagnostics is not None:
        diagnostics.update(_solution_diagnostics(joint_solution))
        diagnostics["market_beta_source"] = market_beta_source
        diagnostics["alpha_semantics"] = (
            "calibrated_expected_excess_return_or_dimensionless_rule_utility"
        )
    if joint_solution.fallback_reason is None:
        return {
            str(code): float(weight)
            for code, weight in joint_solution.weights.items()
            if float(weight) > 1e-10
        }
    if exposure_constraints:
        return {}

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


def _market_beta_estimates(
    candidates: pd.DataFrame,
    *,
    return_history: pd.DataFrame | None,
    benchmark_weights: Mapping[str, float] | None,
) -> tuple[pd.Series, str]:
    supplied = pd.to_numeric(
        candidates.get("market_beta", pd.Series(np.nan, index=candidates.index)),
        errors="coerce",
    )
    if supplied.notna().all() and not supplied.empty:
        return supplied.clip(lower=-1.0, upper=3.0), "candidate_exposure"
    fallback = pd.Series(1.0, index=candidates.index, dtype=float)
    if return_history is None or return_history.empty or not benchmark_weights:
        return supplied.fillna(fallback).clip(lower=-1.0, upper=3.0), "fallback_one"

    history = return_history.copy()
    history.columns = [_normalize_code(column) for column in history.columns]
    normalized_benchmarks = {
        _normalize_code(code): max(float(weight), 0.0)
        for code, weight in benchmark_weights.items()
        if _normalize_code(code) in history.columns and float(weight) > 0.0
    }
    total_benchmark_weight = sum(normalized_benchmarks.values())
    if total_benchmark_weight <= 0.0:
        return supplied.fillna(fallback).clip(lower=-1.0, upper=3.0), "fallback_one"
    normalized_benchmarks = {
        code: weight / total_benchmark_weight
        for code, weight in normalized_benchmarks.items()
    }
    numeric = history.apply(pd.to_numeric, errors="coerce")
    benchmark_return = sum(
        numeric[code] * weight
        for code, weight in normalized_benchmarks.items()
    )
    benchmark_variance = float(benchmark_return.var(ddof=1))
    if not np.isfinite(benchmark_variance) or benchmark_variance <= 1e-12:
        return supplied.fillna(fallback).clip(lower=-1.0, upper=3.0), "fallback_one"

    estimates: dict[str, float] = {}
    for raw_code in candidates["code"].astype(str):
        code = _normalize_code(raw_code)
        if code not in numeric.columns:
            continue
        aligned = pd.concat(
            [numeric[code], benchmark_return],
            axis=1,
        ).dropna()
        if len(aligned) < 20:
            continue
        covariance = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]))
        if np.isfinite(covariance):
            estimates[code] = covariance / benchmark_variance
    if not estimates:
        return supplied.fillna(fallback).clip(lower=-1.0, upper=3.0), "fallback_one"
    estimated = candidates["code"].map(
        lambda code: estimates.get(_normalize_code(code), np.nan)
    )
    combined = supplied.where(supplied.notna(), estimated).fillna(1.0)
    return (
        combined.clip(lower=-1.0, upper=3.0),
        "estimated_from_benchmark_returns",
    )


def _joint_portfolio_solution(
    candidates: pd.DataFrame,
    *,
    top_n: int,
    max_single_weight: float,
    current_weights: Mapping[str, float] | None,
    return_history: pd.DataFrame | None,
    gross_exposure: float,
    group_constraints: Mapping[str, float] | None,
    exposure_constraints: Mapping[str, float] | None,
    risk_aversion: float,
    cost_aversion: float,
    max_turnover: float,
    benchmark_weights: Mapping[str, float] | None,
) -> PortfolioSolution:
    frame = candidates.copy()
    if frame.empty or "code" not in frame.columns:
        return optimize_portfolio(
            PortfolioProblem(
                candidates=pd.DataFrame(columns=["code", "alpha", "liquidity_cap"]),
                current_weights=pd.Series(dtype=float),
                benchmark_weights=pd.Series(dtype=float),
                covariance=pd.DataFrame(),
                exposure_matrix=pd.DataFrame(),
                limits=PortfolioLimits(max_positions=max(int(top_n), 1)),
            )
        )
    frame["code"] = frame["code"].map(_normalize_code)
    frame = frame.drop_duplicates("code", keep="first").set_index("code", drop=False)
    score = pd.to_numeric(
        frame.get("score", pd.Series(0.0, index=frame.index)), errors="coerce"
    ).fillna(0.0)
    score_rank = score.rank(method="average", pct=True)
    alpha = 0.01 + 0.04 * score_rank
    expected = pd.to_numeric(
        frame.get("expected_excess_return", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    confidence = pd.to_numeric(
        frame.get("prediction_confidence", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    applied = frame.get(
        "prediction_applied", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    alpha.loc[applied & expected.notna() & confidence.notna()] = (
        expected * confidence
    ).loc[applied & expected.notna() & confidence.notna()]
    frame["alpha"] = alpha.clip(lower=-0.50, upper=0.50)
    frame["liquidity_cap"] = pd.to_numeric(
        frame.get("liquidity_cap", pd.Series(max_single_weight, index=frame.index)),
        errors="coerce",
    ).fillna(max_single_weight).clip(lower=0.0, upper=max_single_weight)

    volatility = pd.to_numeric(
        frame.get(
            "expected_volatility",
            frame.get("low_volatility_60", pd.Series(0.20, index=frame.index)),
        ),
        errors="coerce",
    ).abs()
    median_volatility = float(volatility.loc[volatility.gt(0.0)].median())
    if not np.isfinite(median_volatility):
        median_volatility = 0.20
    volatility = volatility.where(volatility.gt(0.0), median_volatility).fillna(
        median_volatility
    ).clip(lower=0.03, upper=1.50)
    covariance = pd.DataFrame(
        np.diag(np.square(volatility.to_numpy(dtype=float))),
        index=frame.index,
        columns=frame.index,
    )
    if return_history is not None:
        history = return_history.copy()
        history.columns = [_normalize_code(column) for column in history.columns]
        if set(frame.index).issubset(history.columns):
            numeric = history.loc[:, frame.index].apply(pd.to_numeric, errors="coerce")
            numeric = numeric.replace([np.inf, -np.inf], np.nan)
            if len(numeric.dropna(how="all")) >= 20:
                numeric = numeric.fillna(numeric.median()).fillna(0.0)
                covariance = pd.DataFrame(
                    LedoitWolf().fit(numeric.to_numpy(dtype=float)).covariance_ * 252.0,
                    index=frame.index,
                    columns=frame.index,
                )

    exposures = pd.DataFrame(index=frame.index)
    exposures["market_beta"] = pd.to_numeric(
        frame.get("market_beta", pd.Series(1.0, index=frame.index)),
        errors="coerce",
    ).fillna(1.0).clip(lower=-1.0, upper=3.0)
    exposures["volatility_beta"] = volatility / max(float(volatility.median()), 1e-8)
    if "fx_beta" in frame.columns:
        exposures["fx_beta"] = pd.to_numeric(frame["fx_beta"], errors="coerce")
    elif "country" in frame.columns:
        exposures["fx_beta"] = frame["country"].fillna("").astype(str).ne("中国").astype(float)
    if "premium_beta" in frame.columns:
        exposures["premium_beta"] = pd.to_numeric(frame["premium_beta"], errors="coerce")
    elif "discount_premium" in frame.columns:
        exposures["premium_beta"] = pd.to_numeric(
            frame["discount_premium"], errors="coerce"
        ).abs()
    if "industry" in frame.columns:
        for industry in sorted(frame["industry"].fillna("unclassified").astype(str).unique()):
            exposures[f"industry:{industry}"] = (
                frame["industry"].fillna("unclassified").astype(str).eq(industry).astype(float)
            )
    for column in sorted(exposure_constraints or {}):
        if column in frame.columns:
            exposures[column] = pd.to_numeric(
                frame[column], errors="coerce"
            )

    normalized_current = {
        _normalize_code(code): max(float(weight), 0.0)
        for code, weight in (current_weights or {}).items()
        if _normalize_code(code) in frame.index
    }
    normalized_benchmark = {
        _normalize_code(code): max(float(weight), 0.0)
        for code, weight in (benchmark_weights or {}).items()
        if _normalize_code(code) in frame.index
    }
    limits = PortfolioLimits(
        max_positions=max(int(top_n), 1),
        max_name_weight=float(max_single_weight),
        max_gross_exposure=min(max(float(gross_exposure), 0.0), 1.0),
        min_cash_weight=max(0.0, 1.0 - min(max(float(gross_exposure), 0.0), 1.0)),
        max_turnover=min(max(float(max_turnover), 0.0), 1.0),
        group_caps=dict(group_constraints or {}),
        exposure_caps=dict(exposure_constraints or {}),
        required_exposures=("market_beta",),
    )
    return optimize_portfolio(
        PortfolioProblem(
            candidates=frame,
            current_weights=pd.Series(normalized_current, dtype=float),
            benchmark_weights=pd.Series(normalized_benchmark, dtype=float),
            covariance=covariance,
            exposure_matrix=exposures,
            limits=limits,
            cost_bps=15.0,
            risk_aversion=max(float(risk_aversion), 0.0),
            active_risk_aversion=0.35 if normalized_benchmark else 0.0,
            cost_aversion=max(float(cost_aversion), 0.0),
        )
    )


def _solution_diagnostics(solution: PortfolioSolution) -> dict[str, object]:
    return {
        "expected_alpha": solution.expected_alpha,
        "expected_cost": solution.expected_cost,
        "turnover": solution.turnover,
        "volatility": solution.volatility,
        "tracking_error": solution.tracking_error,
        "cash_weight": solution.cash_weight,
        "exposures": solution.exposures,
        "risk_contributions": solution.risk_contributions,
        "stress_losses": solution.stress_losses,
        "binding_constraints": list(solution.binding_constraints),
        "fallback_reason": solution.fallback_reason,
    }
