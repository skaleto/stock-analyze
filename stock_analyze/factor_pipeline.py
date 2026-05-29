"""Cross-sectional factor processing: winsorize, z-score, industry neutralization.

Pure pandas, no sklearn. Produces a per-stock-factor long-form table so the
caller can persist a reproducible snapshot and a score column whose value is
Σ contribution per code.

Broadcast factors (e.g. ``claude_market_sentiment_1w``) bypass the
cross-sectional pipeline (winsorize/z-score/neutralize) — their value is a
single scalar applied uniformly to every candidate's composite score. Apply
``sign × weight × value`` after the classic pipeline; the caller supplies
the scalar via the ``broadcast_values`` kwarg.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd


UNCLASSIFIED = "未分类"

# Broadcast factor names look like ``<agent_id>_market_sentiment_1w`` (and
# future ``<agent_id>_*`` factors). They contribute a constant to every
# candidate's score, not a per-stock value. See
# ``openspec/changes/add-llm-sentiment-alpha-factor/design.md`` §5.
_BROADCAST_FACTOR_RE = re.compile(r"^(claude|codex)_market_sentiment_1w$")

# Sector-sentiment factors (Phase 3): ``<agent>_sector_sentiment``. Unlike
# the broadcast factor above, these ARE per-stock (each stock inherits its
# industry's sentiment), so they flow through the normal winsorize/z-score
# pipeline — EXCEPT industry-neutralization, which would demean an
# industry-constant signal to zero. See ``is_sector_sentiment_factor``.
_SECTOR_FACTOR_RE = re.compile(r"^(claude|codex)_sector_sentiment$")


def is_broadcast_factor(name: str) -> bool:
    """Return True if ``name`` matches a broadcast (market-level) factor."""
    if not name:
        return False
    return bool(_BROADCAST_FACTOR_RE.match(name))


def is_sector_sentiment_factor(name: str) -> bool:
    """Return True if ``name`` is a per-stock sector-sentiment factor.

    These are per-stock (not broadcast) but must skip industry
    neutralization — neutralizing an industry-constant signal zeroes it out.
    """
    if not name:
        return False
    return bool(_SECTOR_FACTOR_RE.match(name))


def load_broadcast_factor(
    agent_id: str,
    factor_name: str,
    as_of: date,
    repo_root: Path,
) -> Optional[float]:
    """Resolve a broadcast factor's scalar value for ``agent_id`` at ``as_of``.

    Currently only ``<agent>_market_sentiment_1w`` is supported (reads
    ``data/<agent>/alt_factors/market_sentiment.csv``). Returns ``None``
    when the factor name is unknown or no sentiment row exists for the
    relevant week.

    For sentiment factors, the returned value is already
    **confidence-weighted**: ``sentiment_score × confidence`` (both ∈
    [-1, 1] and [0, 1] respectively). This lets a low-confidence LLM
    reading (e.g. confidence=0.3) contribute proportionally less to the
    composite score than a high-confidence one (e.g. confidence=0.9),
    even when raw scores are identical. Downstream
    ``process_factors`` consumes the returned scalar verbatim — no
    further confidence handling is required at the application site.
    """
    if not is_broadcast_factor(factor_name):
        return None
    expected = f"{agent_id}_market_sentiment_1w"
    if factor_name != expected:
        return None
    from stock_analyze.markets.a_share.alt_factors import sentiment as alt_sent
    rows = alt_sent.load_sentiment_history(agent_id, repo_root)
    eligible = [r for r in rows if r.week_end <= as_of]
    if not eligible:
        return None
    latest = eligible[-1]
    return float(latest.score) * float(latest.confidence)


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
    *,
    broadcast_values: dict[str, Optional[float]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the full factor pipeline and return ``(scored, factor_table)``.

    ``scored`` is the input DataFrame with ``score``, ``score_detail``,
    ``factor_coverage_weight``, ``factor_coverage_ratio``, and
    ``insufficient_factor_coverage`` columns added.

    ``factor_table`` is long-form with columns:
        code, factor, industry, direction, weight_configured, weight_effective,
        raw, winsorized, zscore, neutralized, signed_neutralized, contribution, valid

    Score reproducibility: ``scored.score == factor_table.groupby('code').contribution.sum()``
    (for the classic factors; broadcast contributions are uniform across codes
    and added on top — they appear in ``factor_table`` as a single row per
    broadcast factor with ``code='__broadcast__'`` and the same value).

    Broadcast factors (``<agent>_market_sentiment_1w`` etc.) skip
    winsorize/z-score/neutralize and add ``sign × weight × value`` uniformly
    across all candidates. The caller resolves the scalar via
    ``load_broadcast_factor`` and passes it through ``broadcast_values``;
    a missing or ``None`` value means the broadcast factor contributes 0.
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

    factor_meta: list[tuple[str, float, str]] = []
    broadcast_meta: list[tuple[str, float, str]] = []
    for name, spec in factors.items():
        weight = float(spec.get("weight", 0))
        direction = str(spec.get("direction", "high"))
        if direction not in {"high", "low"}:
            raise ValueError(f"invalid factor direction for {name}: {direction!r}")
        if weight <= 0:
            continue
        if is_broadcast_factor(name):
            broadcast_meta.append((name, weight, direction))
        elif name in df.columns:
            factor_meta.append((name, weight, direction))
    # total_weight only counts classic factors for per-stock effective-weight
    # rescaling; broadcast contributions are layered on as a pure additive
    # constant after the classic pipeline (they're cross-sectionally uniform,
    # so they cannot affect classic scaling decisions).
    total_weight = sum(weight for _, weight, _ in factor_meta)

    if not factor_meta or total_weight <= 0:
        # No classic factors — but broadcast factors might still contribute a
        # uniform shift. Build a zero composite, layer on broadcasts, and
        # return. (Coverage metrics stay 0 because classic-factor coverage is
        # what they measure; broadcasts are always "fully covered" if a value
        # exists and contribute nothing if not.)
        broadcast_shift = _broadcast_shift(broadcast_meta, broadcast_values)
        df["score"] = round(broadcast_shift, 4)
        df["score_detail"] = (
            f"broadcast:{broadcast_shift:+.4f}" if broadcast_shift else ""
        )
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
            # Sector-sentiment factors skip industry-neutralization: the signal
            # IS the industry tilt, so demeaning within industry would zero it.
            do_neutralize = neutralize and not is_sector_sentiment_factor(factor)
            neutralized = industry_neutralize(zscore, industries) if do_neutralize else zscore
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

    # Layer broadcast factor contribution on top of classic composite. The
    # shift is uniform across all rows, so relative ranking is unchanged but
    # the strategy's "view of the market" (e.g. risk-on vs risk-off) can be
    # reflected as a level shift.
    broadcast_shift = _broadcast_shift(broadcast_meta, broadcast_values)
    if broadcast_shift:
        composite = composite + broadcast_shift

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


def _broadcast_shift(
    broadcast_meta: list[tuple[str, float, str]],
    broadcast_values: dict[str, Optional[float]] | None,
) -> float:
    """Return ``Σ (sign × weight × value)`` for all broadcast factors with a
    resolved value. Missing / None values contribute 0.
    """
    if not broadcast_meta:
        return 0.0
    values = broadcast_values or {}
    total = 0.0
    for name, weight, direction in broadcast_meta:
        value = values.get(name)
        if value is None:
            continue
        sign = -1.0 if direction == "low" else 1.0
        total += sign * weight * float(value)
    return total


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
