"""Translate persisted market regimes into deterministic portfolio actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


_GROSS_EXPOSURE = {
    "defensive": {"risk_on": 0.96, "mixed": 0.88, "risk_off": 0.72, "unknown": 0.70},
    "trend": {"risk_on": 0.98, "mixed": 0.85, "risk_off": 0.55, "unknown": 0.60},
}


@dataclass(frozen=True)
class RegimeDecision:
    state: str
    gross_exposure: float
    source_date: str | None
    coverage: float
    stale: bool
    warning: str


def regime_decision_from_state(
    state: str,
    *,
    profile: str,
    source_date: str | None,
    coverage: float = 1.0,
    warning: str = "",
) -> RegimeDecision:
    if profile not in _GROSS_EXPOSURE:
        raise ValueError("regime_profile")
    normalized = state if state in _GROSS_EXPOSURE[profile] else "unknown"
    return RegimeDecision(
        state=normalized,
        gross_exposure=_GROSS_EXPOSURE[profile][normalized],
        source_date=source_date,
        coverage=float(coverage),
        stale=normalized == "unknown",
        warning=warning,
    )


def load_regime_decision(
    repo_root: str | Path,
    *,
    market: str,
    as_of: object,
    profile: str,
    max_age_days: int = 10,
) -> RegimeDecision:
    if profile not in _GROSS_EXPOSURE:
        raise ValueError("regime_profile")
    as_of_date = pd.Timestamp(as_of).date()
    run_key = as_of_date.strftime("%Y%m%d")
    directory = Path(repo_root) / "data" / "research" / "regimes" / market
    candidates = sorted(
        path for path in directory.glob("*.parquet")
        if path.stem.isdigit() and path.stem <= run_key
    )
    if not candidates:
        return RegimeDecision(
            "unknown",
            _GROSS_EXPOSURE[profile]["unknown"],
            None,
            0.0,
            True,
            "regime_missing",
        )
    try:
        frame = pd.read_parquet(candidates[-1])
    except Exception:  # noqa: BLE001 - malformed control input must fail closed
        frame = pd.DataFrame()
    if frame.empty or "composite_regime" not in frame.columns:
        return RegimeDecision(
            "unknown",
            _GROSS_EXPOSURE[profile]["unknown"],
            None,
            0.0,
            True,
            "regime_unreadable",
        )
    if "scope" in frame.columns and frame["scope"].astype(str).eq("market").any():
        frame = frame.loc[frame["scope"].astype(str).eq("market")]
    normalized_dates = pd.to_datetime(
        frame.get("trade_date", pd.Series(index=frame.index, dtype=str)).astype(str).str.replace("-", "", regex=False),
        format="%Y%m%d",
        errors="coerce",
    )
    visible = frame.assign(_date=normalized_dates).loc[lambda value: value["_date"].dt.date.le(as_of_date)].sort_values("_date")
    if visible.empty:
        return RegimeDecision(
            "unknown",
            _GROSS_EXPOSURE[profile]["unknown"],
            None,
            0.0,
            True,
            "regime_not_visible",
        )
    latest = visible.iloc[-1]
    source_date = latest["_date"].date()
    age = (as_of_date - source_date).days
    coverage = float(pd.to_numeric(pd.Series([latest.get("regime_coverage")]), errors="coerce").fillna(0.0).iloc[0])
    raw_state = str(latest.get("composite_regime") or "unknown")
    stale = age > max(int(max_age_days), 0)
    state = raw_state if raw_state in {"risk_on", "mixed", "risk_off"} and coverage >= 0.70 and not stale else "unknown"
    warning = ""
    if stale:
        warning = f"regime_stale:{age}d"
    elif coverage < 0.70:
        warning = f"regime_low_coverage:{coverage:.2f}"
    elif state == "unknown":
        warning = "regime_unknown"
    return RegimeDecision(
        state=state,
        gross_exposure=_GROSS_EXPOSURE[profile][state],
        source_date=source_date.isoformat(),
        coverage=coverage,
        stale=stale,
        warning=warning,
    )


def _mean_rank(frame: pd.DataFrame, columns: Iterable[str], *, ascending: bool) -> pd.Series:
    available = [column for column in columns if column in frame.columns]
    if not available:
        return pd.Series(0.0, index=frame.index)
    ranks = pd.DataFrame({
        column: pd.to_numeric(frame[column], errors="coerce").rank(
            pct=True,
            ascending=ascending,
            method="average",
        )
        for column in available
    })
    return ranks.mean(axis=1, skipna=True).fillna(0.5) - 0.5


def apply_regime_policy(
    candidates: pd.DataFrame,
    decision: RegimeDecision,
    *,
    profile: str,
) -> pd.DataFrame:
    if profile not in _GROSS_EXPOSURE:
        raise ValueError("regime_profile")
    result = candidates.copy()
    if result.empty:
        return result
    momentum = _mean_rank(
        result,
        ("momentum_20", "momentum_60", "macd_hist_slope", "relative_strength_20"),
        ascending=True,
    )
    quality = _mean_rank(
        result,
        ("roe", "gross_margin", "cash_conversion", "high_value_add_proxy", "dividend_yield"),
        ascending=True,
    )
    low_risk = _mean_rank(
        result,
        ("low_volatility_60", "realized_volatility_20", "debt_ratio"),
        ascending=False,
    )
    state = decision.state
    effective_state = "risk_off" if state == "unknown" else state
    coefficients = {
        "defensive": {
            "risk_on": (0.10, 0.10, 0.05),
            "mixed": (0.00, 0.15, 0.10),
            "risk_off": (-0.15, 0.25, 0.30),
        },
        "trend": {
            "risk_on": (0.30, 0.05, 0.00),
            "mixed": (0.08, 0.08, 0.05),
            "risk_off": (-0.35, 0.15, 0.25),
        },
    }[profile][effective_state]
    raw_adjustment = coefficients[0] * momentum + coefficients[1] * quality + coefficients[2] * low_risk
    score = pd.to_numeric(result["score"], errors="coerce").fillna(0.0)
    score_scale = max(float(score.std(ddof=0)), 0.5)
    adjustment = raw_adjustment.fillna(0.0) * score_scale
    result["base_score_before_regime"] = score
    result["regime_score_adjustment"] = adjustment
    result["score"] = score + adjustment
    result["regime"] = state
    result["regime_source_date"] = decision.source_date
    result["regime_coverage"] = decision.coverage
    result["regime_stale"] = decision.stale
    result["regime_gross_exposure"] = decision.gross_exposure
    result["regime_applied"] = True
    return result


__all__ = [
    "RegimeDecision",
    "apply_regime_policy",
    "load_regime_decision",
    "regime_decision_from_state",
]
