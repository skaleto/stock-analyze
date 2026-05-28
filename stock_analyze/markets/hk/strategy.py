"""HK strategy: build_signals adapter.

Wires the HK data provider into the shared
:func:`stock_analyze.factor_pipeline.process_factors` for cross-market
factor processing (winsorize / zscore / industry-neutralize — HK v1
skips industry neutralization since yfinance doesn't expose industry
codes consistently).

The public surface matches A-share's ``build_signals`` so the CLI can
dispatch by market id.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ...factor_pipeline import process_factors
from .data_provider import YFinanceHKProvider


logger = logging.getLogger(__name__)


# Direction conventions for HK v1 factors. "high" = larger is better;
# "low" = smaller is better (the pipeline flips the sign internally).
_HK_FACTOR_DIRECTIONS: dict[str, str] = {
    "pe": "low",
    "pb": "low",
    "momentum_20": "high",
    "momentum_60": "high",
    "low_volatility_60": "low",
    "dividend_yield": "high",
}


def build_signals(
    config: dict[str, Any],
    provider: YFinanceHKProvider,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return a list of {code, account_id, score, reason} dicts.

    Calls the shared factor pipeline against per-account universes
    (HSI for the 'hsi' account, HSCEI for 'hscei'). v1 factor set:
    pe / pb / momentum_20 / momentum_60 / low_volatility_60 / dividend_yield.
    """
    as_of = as_of or date.today()
    rows: list[dict[str, Any]] = []

    # Translate overlay's flat ``factors`` dict into the per-factor spec
    # shape that process_factors expects.
    overlay_factors_raw = config.get("factors", {}) or {}
    factors_spec: dict[str, dict[str, Any]] = {}
    for name, value in overlay_factors_raw.items():
        if isinstance(value, dict):
            # Overlay already in nested form
            factors_spec[name] = value
        else:
            # Flat weight — fill in direction from the HK convention
            factors_spec[name] = {
                "weight": float(value),
                "direction": _HK_FACTOR_DIRECTIONS.get(name, "high"),
            }

    factor_processing = dict(config.get("factor_processing", {}))
    # HK v1 disables industry neutralization since we don't have industry codes
    factor_processing.setdefault("neutralize_industry", False)

    for account in config.get("accounts", []):
        scope = account["scope"]
        spot_df = provider.spot(scope)
        if spot_df.empty:
            logger.warning("HK %s universe spot is empty", scope)
            continue

        # Keep only the columns the pipeline needs: 'code' identifier + each
        # active factor column.
        active_factor_cols = [name for name in factors_spec.keys()
                              if name in spot_df.columns]
        if not active_factor_cols:
            logger.warning(
                "HK %s: none of overlay factors %s found in spot",
                scope, list(factors_spec),
            )
            continue
        frame = spot_df[["code"] + active_factor_cols].copy()

        scored, _factor_table = process_factors(
            frame,
            factors=factors_spec,
            factor_processing=factor_processing,
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
    """Build a short human-readable reason — top factor contributions."""
    parts: list[str] = []
    for col in factor_cols[:3]:
        if col in row and pd.notna(row[col]):
            parts.append(f"{col}={float(row[col]):+.2f}")
    return "; ".join(parts) if parts else "top_score"


__all__ = ["build_signals"]
