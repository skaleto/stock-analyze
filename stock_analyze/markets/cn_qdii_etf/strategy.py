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

    for account in config.get("accounts", []) or []:
        scope = account["scope"]
        spot_df = provider.spot(scope)
        if spot_df.empty:
            logger.warning("cn_qdii_etf %s universe spot is empty", scope)
            continue
        active = [name for name in factors_spec if name in spot_df.columns]
        if not active:
            logger.warning("cn_qdii_etf %s: none of overlay factors found", scope)
            continue
        frame_cols = ["code"] + active
        if "industry" in spot_df.columns:
            frame_cols.append("industry")
        scored, _factor_table = process_factors(
            spot_df[frame_cols].copy(),
            factors=factors_spec,
            factor_processing=factor_processing,
        )
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
