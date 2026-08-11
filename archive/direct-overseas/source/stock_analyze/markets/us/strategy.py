"""US strategy: build_signals adapter.

Symmetric with HK's strategy module but operates on US universe.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ...factor_pipeline import process_factors
from ...sentiment_integration import (
    apply_sector_sentiment_columns,
    resolve_broadcast_values,
)
from .data_provider import YFinanceUSProvider


logger = logging.getLogger(__name__)


_US_FACTOR_DIRECTIONS: dict[str, str] = {
    "pe": "low",
    "pb": "low",
    "momentum_20": "high",
    "momentum_60": "high",
    "low_volatility_60": "low",
    "dividend_yield": "high",
}


def build_signals(
    config: dict[str, Any],
    provider: YFinanceUSProvider,
    *,
    as_of: date | None = None,
    repo_root: str | None = None,
) -> list[dict[str, Any]]:
    """Build per-account signals using the shared factor pipeline."""
    as_of = as_of or date.today()
    rows: list[dict[str, Any]] = []

    overlay_factors_raw = config.get("factors", {}) or {}
    factors_spec: dict[str, dict[str, Any]] = {}
    for name, value in overlay_factors_raw.items():
        if isinstance(value, dict):
            factors_spec[name] = value
        else:
            factors_spec[name] = {
                "weight": float(value),
                "direction": _US_FACTOR_DIRECTIONS.get(name, "high"),
            }

    factor_processing = dict(config.get("factor_processing", {}))
    factor_processing.setdefault("neutralize_industry", False)

    for account in config.get("accounts", []):
        scope = account["scope"]
        spot_df = provider.spot(scope)
        if spot_df.empty:
            logger.warning("US %s universe spot is empty", scope)
            continue
        spot_df = apply_sector_sentiment_columns(
            config, spot_df, as_of, repo_root, market="us",
        )
        broadcast_values = resolve_broadcast_values(
            config, as_of, repo_root, market="us",
        )

        active_factor_cols = [n for n in factors_spec.keys()
                              if n in spot_df.columns]
        has_broadcast = bool(broadcast_values)
        if not active_factor_cols and not has_broadcast:
            logger.warning(
                "US %s: none of overlay factors %s found in spot",
                scope, list(factors_spec),
            )
            continue
        frame_cols = ["code"] + active_factor_cols
        if "industry" in spot_df.columns and "industry" not in frame_cols:
            frame_cols.append("industry")
        frame = spot_df[frame_cols].copy()

        scored, _ = process_factors(
            frame,
            factors=factors_spec,
            factor_processing=factor_processing,
            broadcast_values=broadcast_values,
        )

        for _, r in scored.iterrows():
            rows.append({
                "code": r["code"],
                "account_id": account["id"],
                "score": float(r["score"]),
                "reason": _format_reason(r, active_factor_cols),
            })

    return rows


def _format_reason(row: pd.Series, factor_cols: list[str]) -> str:
    parts: list[str] = []
    for col in factor_cols[:3]:
        if col in row and pd.notna(row[col]):
            parts.append(f"{col}={float(row[col]):+.2f}")
    return "; ".join(parts) if parts else "top_score"


__all__ = ["build_signals"]
