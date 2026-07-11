"""Signal builder for domestic cross-border ETF universes."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ...factor_pipeline import process_factors
from .data_provider import CNQDIETFProvider


logger = logging.getLogger(__name__)

ETF_FACTOR_DIRECTIONS: dict[str, str] = {
    "momentum_20": "high",
    "momentum_60": "high",
    "low_volatility_60": "low",
    "avg_amount_20": "high",
    "discount_premium": "low",
}


def build_signals(
    config: dict[str, Any],
    provider: CNQDIETFProvider,
    *,
    as_of: date | None = None,
    repo_root: str | None = None,
) -> list[dict[str, Any]]:
    as_of = as_of or date.today()
    rows: list[dict[str, Any]] = []
    factors_spec = _factor_spec(config.get("factors", {}) or {})
    factor_processing = dict(config.get("factor_processing", {}) or {})
    factor_processing.setdefault("neutralize_industry", False)
    filters = dict(config.get("filters", {}) or {})

    for account in config.get("accounts", []) or []:
        scope = account["scope"]
        spot_df = provider.spot(scope)
        if spot_df.empty:
            logger.warning("cn_qdii_etf %s universe spot is empty", scope)
            continue
        eligible = spot_df.copy()
        if "paused" in eligible.columns:
            eligible = eligible.loc[~eligible["paused"].fillna(True).astype(bool)]
        if "avg_amount_20" in eligible.columns:
            min_amount = float(filters.get("min_avg_amount_20", 0.0))
            amounts = pd.to_numeric(eligible["avg_amount_20"], errors="coerce")
            eligible = eligible.loc[amounts >= min_amount]
        if "listing_age_days" in eligible.columns:
            min_listing_days = int(filters.get("min_listing_days", 0))
            ages = pd.to_numeric(eligible["listing_age_days"], errors="coerce")
            eligible = eligible.loc[ages.isna() | (ages >= min_listing_days)]
        max_candidates = int(filters.get("max_fetch_candidates", len(eligible)) or 0)
        if max_candidates > 0 and len(eligible) > max_candidates:
            if "avg_amount_20" in eligible.columns:
                eligible = eligible.assign(
                    _liquidity=pd.to_numeric(eligible["avg_amount_20"], errors="coerce")
                ).nlargest(max_candidates, "_liquidity").drop(columns="_liquidity")
            else:
                eligible = eligible.head(max_candidates)
        active = [name for name in factors_spec if name in eligible.columns]
        if not active:
            logger.warning("cn_qdii_etf %s: none of overlay factors found", scope)
            continue
        frame_cols = ["code"] + active
        if "industry" in spot_df.columns:
            frame_cols.append("industry")
        scored, _factor_table = process_factors(
            eligible[frame_cols].copy(),
            factors=factors_spec,
            factor_processing=factor_processing,
        )
        if "insufficient_factor_coverage" in scored.columns:
            scored = scored.loc[~scored["insufficient_factor_coverage"]].copy()
        for _, r in scored.iterrows():
            rows.append(
                {
                    "code": r["code"],
                    "account_id": account["id"],
                    "score": float(r["score"]),
                    "reason": _format_reason(r, active),
                }
            )
    return rows


def _factor_spec(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if isinstance(value, dict):
            out[name] = value
        else:
            out[name] = {
                "weight": float(value),
                "direction": ETF_FACTOR_DIRECTIONS.get(name, "high"),
            }
    return out


def _format_reason(row: pd.Series, factor_cols: list[str]) -> str:
    parts: list[str] = []
    for col in factor_cols[:3]:
        if col in row and pd.notna(row[col]):
            parts.append(f"{col}={float(row[col]):+.4f}")
    return "; ".join(parts) if parts else "top_score"


__all__ = ["ETF_FACTOR_DIRECTIONS", "build_signals"]
