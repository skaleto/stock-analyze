"""Cross-sectional factor processing: winsorize, z-score, industry neutralization.

Pure pandas, no sklearn. Produces a per-stock-factor long-form table so the
caller can persist a reproducible snapshot and a score column whose value is
Σ contribution per code.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


UNCLASSIFIED = "未分类"


def winsorize_series(values: pd.Series, lower: float, upper: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.dropna().empty:
        return numeric
    lo = numeric.quantile(lower)
    hi = numeric.quantile(upper)
    if pd.isna(lo) or pd.isna(hi):
        return numeric
    return numeric.clip(lower=lo, upper=hi)


def zscore_series(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return numeric
    mean = float(valid.mean())
    std = float(valid.std(ddof=0))
    if std == 0 or pd.isna(std):
        return numeric.where(numeric.isna(), 0.0)
    return (numeric - mean) / std


def industry_neutralize(values: pd.Series, industry: pd.Series, unclassified_label: str = UNCLASSIFIED) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if industry is None or len(industry) != len(numeric):
        return numeric
    labels = pd.Series(industry).reset_index(drop=True)
    labels = labels.fillna(unclassified_label).replace("", unclassified_label)
    numeric = numeric.reset_index(drop=True)
    out = numeric.copy()
    for label in labels.unique():
        mask = labels == label
        bucket = numeric[mask]
        valid = bucket.dropna()
        if valid.empty:
            continue
        out[mask] = bucket - float(valid.mean())
    return out


def process_factors(
    candidates: pd.DataFrame,
    factors: dict[str, dict[str, Any]],
    factor_processing: dict[str, Any] | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the full factor pipeline and return ``(scored, factor_table)``.

    ``scored`` is the input DataFrame with ``score``, ``score_detail``,
    ``factor_coverage_weight``, ``factor_coverage_ratio``, and
    ``insufficient_factor_coverage`` columns added.

    ``factor_table`` is long-form with columns:
        code, factor, industry, direction, weight_configured, weight_effective,
        raw, winsorized, zscore, neutralized, signed_neutralized, contribution, valid

    Score reproducibility: ``scored.score == factor_table.groupby('code').contribution.sum()``.
    """

    fp = factor_processing or {}
    pipeline_enabled = bool(fp.get("enabled", True))
    winsor_lower = float(fp.get("winsorize_lower", 0.01))
    winsor_upper = float(fp.get("winsorize_upper", 0.99))
    neutralize = bool(fp.get("neutralize_industry", True))
    min_coverage = float(fp.get("min_factor_coverage", 0.6))

    df = candidates.reset_index(drop=True).copy()
    industries = (
        df["industry"].fillna(UNCLASSIFIED).replace("", UNCLASSIFIED)
        if "industry" in df.columns
        else pd.Series([UNCLASSIFIED] * len(df), index=df.index)
    )

    factor_meta = [
        (name, float(spec.get("weight", 0)), spec.get("direction", "high"))
        for name, spec in factors.items()
        if float(spec.get("weight", 0)) > 0 and name in df.columns
    ]
    total_weight = sum(weight for _, weight, _ in factor_meta)

    if not factor_meta or total_weight <= 0:
        df["score"] = 0.0
        df["score_detail"] = ""
        df["factor_coverage_weight"] = 0.0
        df["factor_coverage_ratio"] = 0.0
        df["insufficient_factor_coverage"] = False
        return df, _empty_factor_table()

    intermediate: dict[str, dict[str, pd.Series]] = {}
    coverage_weight = pd.Series(0.0, index=df.index)

    for factor, weight, direction in factor_meta:
        raw = pd.to_numeric(df[factor], errors="coerce")
        if pipeline_enabled:
            winsorized = winsorize_series(raw, winsor_lower, winsor_upper)
            zscore = zscore_series(winsorized)
            neutralized = industry_neutralize(zscore, industries) if neutralize else zscore
        else:
            winsorized = raw.copy()
            zscore = raw.rank(pct=True)
            neutralized = zscore
        signed = neutralized * (-1.0 if direction == "low" else 1.0)
        valid = raw.notna()
        coverage_weight = coverage_weight.add(valid.astype(float) * weight, fill_value=0.0)
        intermediate[factor] = {
            "raw": raw,
            "winsorized": winsorized,
            "zscore": zscore,
            "neutralized": neutralized,
            "signed": signed,
            "valid": valid,
        }

    safe_weight = coverage_weight.where(coverage_weight > 0, np.nan)
    coverage_ratio = (coverage_weight / total_weight).fillna(0.0)
    scale = (total_weight / safe_weight).fillna(0.0)

    composite = pd.Series(0.0, index=df.index)
    rows: list[dict[str, Any]] = []
    for factor, weight, direction in factor_meta:
        data = intermediate[factor]
        signed = data["signed"]
        effective_weight = scale * weight
        contribution = (signed.fillna(0.0)) * effective_weight
        composite = composite.add(contribution, fill_value=0.0)
        for idx in df.index:
            raw_val = data["raw"].at[idx]
            wins_val = data["winsorized"].at[idx]
            z_val = data["zscore"].at[idx]
            neu_val = data["neutralized"].at[idx]
            signed_val = signed.at[idx]
            eff_weight = float(effective_weight.at[idx])
            rows.append(
                {
                    "code": str(df.at[idx, "code"]),
                    "factor": factor,
                    "industry": str(industries.at[idx]),
                    "direction": direction,
                    "weight_configured": weight,
                    "weight_effective": round(eff_weight, 6),
                    "raw": float(raw_val) if pd.notna(raw_val) else None,
                    "winsorized": float(wins_val) if pd.notna(wins_val) else None,
                    "zscore": float(z_val) if pd.notna(z_val) else None,
                    "neutralized": float(neu_val) if pd.notna(neu_val) else None,
                    "signed_neutralized": float(signed_val) if pd.notna(signed_val) else None,
                    "contribution": round(float(contribution.at[idx]), 6),
                    "valid": bool(data["valid"].at[idx]),
                }
            )

    df["score"] = composite.round(4)
    df["factor_coverage_weight"] = coverage_weight.round(4)
    df["factor_coverage_ratio"] = coverage_ratio.round(4)
    insufficient_mask = coverage_ratio < min_coverage
    df["insufficient_factor_coverage"] = insufficient_mask
    if insufficient_mask.any():
        existing = df["data_warnings"].astype(str) if "data_warnings" in df.columns else pd.Series([""] * len(df), index=df.index)
        df.loc[insufficient_mask, "data_warnings"] = existing.loc[insufficient_mask].map(
            lambda value: ";".join([part for part in (value, "insufficient_factor_coverage") if part and part != "nan"])
        )

    df["score_detail"] = _build_score_details(df.index, factor_meta, intermediate, scale)
    factor_table = pd.DataFrame(rows)
    return df, factor_table


def _empty_factor_table() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "code",
            "factor",
            "industry",
            "direction",
            "weight_configured",
            "weight_effective",
            "raw",
            "winsorized",
            "zscore",
            "neutralized",
            "signed_neutralized",
            "contribution",
            "valid",
        ]
    )


def _build_score_details(
    index: pd.Index,
    factor_meta: list[tuple[str, float, str]],
    intermediate: dict[str, dict[str, pd.Series]],
    scale: pd.Series,
) -> list[str]:
    """Generate ``factor:zscore:contribution`` strings, kept short for tables."""

    details: list[str] = []
    for idx in index:
        parts: list[tuple[str, float, float]] = []
        for factor, weight, _ in factor_meta:
            data = intermediate[factor]
            if not bool(data["valid"].at[idx]):
                continue
            z_value = float(data["zscore"].at[idx]) if pd.notna(data["zscore"].at[idx]) else 0.0
            contribution = float(data["signed"].at[idx]) * float(scale.at[idx]) * weight if pd.notna(data["signed"].at[idx]) else 0.0
            parts.append((factor, z_value, contribution))
        parts.sort(key=lambda item: abs(item[2]), reverse=True)
        details.append("; ".join(f"{name}:{z:.2f}:{contrib:.2f}" for name, z, contrib in parts[:5]))
    return details
