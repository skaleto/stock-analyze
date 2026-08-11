"""Canonical units for Tushare fund-market fields."""

from __future__ import annotations

import pandas as pd

from .data_provider import TUSHARE_AMOUNT_TO_YUAN


def canonicalize_tushare_amount(
    frame: pd.DataFrame,
    *,
    copy: bool = False,
) -> pd.DataFrame:
    """Expose fund turnover amount in yuan while preserving the raw unit."""

    result = frame.copy() if copy else frame
    if "amount" not in result.columns:
        return result
    if "amount_unit" in result.columns:
        declared = set(result["amount_unit"].dropna().astype(str).str.strip())
        if declared == {"yuan"}:
            result["amount_yuan"] = pd.to_numeric(result["amount"], errors="coerce")
            return result
        if declared:
            raise ValueError("qdii_amount_unit_mismatch")
    raw = pd.to_numeric(result["amount"], errors="coerce")
    result["amount_thousand_yuan"] = raw
    result["amount"] = raw * TUSHARE_AMOUNT_TO_YUAN
    result["amount_yuan"] = result["amount"]
    result["amount_unit"] = "yuan"
    return result


__all__ = ["canonicalize_tushare_amount"]
