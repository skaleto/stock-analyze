"""Industry cap, holding buffer, and max-holding-day controls applied after
factor scoring. Pure pandas + dict logic so it stays trivial to unit-test.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...factor_pipeline import UNCLASSIFIED
from ...utils import parse_date, safe_float


def select_top_n_with_controls(
    scored_candidates: pd.DataFrame,
    account_state: dict[str, Any],
    config: dict[str, Any],
    top_n: int,
    run_date: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Pick TopN from already-scored candidates with industry caps and hold buffer.

    Returns ``(selected_df, warnings)``. ``selected_df`` is a slice of
    ``scored_candidates`` containing at most ``top_n`` rows; ``warnings`` is a
    list of strings to surface to ``SignalResult.warnings``.
    """

    if scored_candidates.empty:
        return scored_candidates, []

    controls = config.get("portfolio_controls", {}) or {}
    max_industry_weight = safe_float(controls.get("max_industry_weight")) or 1.0
    hold_buffer_pct = safe_float(controls.get("hold_buffer_pct")) or 0.0
    max_holding_days = int(controls.get("max_holding_days") or 0)
    unclassified_label = str(controls.get("industry_unclassified_label") or UNCLASSIFIED)

    df = scored_candidates.sort_values("score", ascending=False).reset_index(drop=True).copy()
    df["rank"] = df.index + 1
    df["industry"] = (
        df["industry"].fillna(unclassified_label).replace("", unclassified_label)
        if "industry" in df.columns
        else pd.Series([unclassified_label] * len(df))
    )

    warnings: list[str] = []
    held_codes = set((account_state.get("positions") or {}).keys())
    buffer_limit = max(top_n, int(top_n * (1 + hold_buffer_pct)))

    selected_indices: list[int] = []
    industry_counts: dict[str, int] = {}
    per_industry_cap = max(1, int(round(top_n * max_industry_weight + 1e-9)))

    forced_due_to_holding_period: set[str] = set()
    if max_holding_days > 0 and run_date:
        run_d = parse_date(run_date)
        for code, position in (account_state.get("positions") or {}).items():
            last_buy = position.get("last_buy_date") or position.get("hold_since")
            if not last_buy:
                continue
            try:
                buy_date = parse_date(last_buy)
            except Exception:  # noqa: BLE001
                continue
            if (run_d - buy_date).days >= max_holding_days:
                forced_due_to_holding_period.add(str(code))

    def industry_capacity_left(label: str) -> bool:
        return industry_counts.get(label, 0) < per_industry_cap

    # Pass 1: pick top-ranked stocks subject to industry cap.
    skipped_for_industry: list[int] = []
    for idx, row in df.iterrows():
        if len(selected_indices) >= top_n:
            break
        industry = str(row["industry"])
        if not industry_capacity_left(industry):
            skipped_for_industry.append(idx)
            continue
        selected_indices.append(idx)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1

    # Pass 2: relax cap if we did not fill top_n.
    if len(selected_indices) < top_n and skipped_for_industry:
        warnings.append("industry_cap_relaxed")
        for idx in skipped_for_industry:
            if len(selected_indices) >= top_n:
                break
            selected_indices.append(idx)
            industry = str(df.at[idx, "industry"])
            industry_counts[industry] = industry_counts.get(industry, 0) + 1

    selected_codes = {str(df.at[idx, "code"]).zfill(6) for idx in selected_indices}

    # Pass 3: hold-buffer retention. Existing holdings whose new rank falls
    # inside [top_n, buffer_limit] are kept; they may push the basket size
    # over `top_n` until either rank falls below the buffer or
    # ``max_holding_days`` forces re-evaluation.
    if held_codes:
        retained_extra: list[int] = []
        for idx, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            if code not in held_codes:
                continue
            if code in selected_codes:
                continue
            if code in forced_due_to_holding_period:
                warnings.append(f"max_holding_days_reevaluation:{code}")
                continue
            if int(row["rank"]) <= buffer_limit:
                retained_extra.append(idx)
        for idx in retained_extra:
            selected_codes.add(str(df.at[idx, "code"]).zfill(6))
            selected_indices.append(idx)

    if not selected_indices:
        return df.head(0), warnings

    chosen = df.loc[selected_indices].drop_duplicates(subset="code").reset_index(drop=True)
    return chosen, warnings


def annotate_industries(positions: dict[str, dict[str, Any]], scored: pd.DataFrame) -> None:
    """Inject industry/hold_since fields into positions from the latest scored frame."""

    if scored.empty:
        return
    industry_map = {str(row["code"]).zfill(6): str(row.get("industry", UNCLASSIFIED)) for _, row in scored.iterrows()}
    for code, position in positions.items():
        if not position.get("industry"):
            position["industry"] = industry_map.get(str(code).zfill(6), UNCLASSIFIED)


def stamp_hold_since(position: dict[str, Any], trade_date: str) -> None:
    """Record ``hold_since`` the first time a position is opened or re-opened."""

    if not position.get("hold_since"):
        position["hold_since"] = trade_date
