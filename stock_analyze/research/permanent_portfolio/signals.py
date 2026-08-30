"""Pure target-weight rules for the two permanent portfolios."""

from __future__ import annotations

from collections.abc import Mapping
import math

import pandas as pd


ROLES = ("equity", "bond", "cash", "gold")
DEFAULT_TIE_BREAK = ("cash", "bond", "gold", "equity")
RANK_WEIGHTS = (0.40, 0.30, 0.20, 0.10)
MOMENTUM_MONTHS = (12, 6, 1, 0)


def fixed_target_weights(
    actual: Mapping[str, float],
    *,
    lower: float,
    upper: float,
) -> dict[str, float] | None:
    try:
        weights = {role: float(actual[role]) for role in ROLES}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("permanent_portfolio_actual_weights") from exc
    if any(not math.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("permanent_portfolio_actual_weights")
    if all(float(lower) <= weights[role] <= float(upper) for role in ROLES):
        return None
    return {role: 0.25 for role in ROLES}


def dynamic_target_weights(
    observations: pd.DataFrame,
    *,
    tie_break: tuple[str, ...] = DEFAULT_TIE_BREAK,
) -> dict[str, float]:
    required_columns = {"role", "months_ago", "adjusted_close"}
    if observations.empty or not required_columns.issubset(observations.columns):
        raise ValueError("permanent_portfolio_momentum_window")
    frame = observations.loc[:, sorted(required_columns)].copy()
    frame["role"] = frame["role"].astype(str)
    frame["months_ago"] = pd.to_numeric(
        frame["months_ago"],
        errors="coerce",
    )
    frame["adjusted_close"] = pd.to_numeric(
        frame["adjusted_close"],
        errors="coerce",
    )
    if frame.duplicated(["role", "months_ago"]).any():
        raise ValueError("permanent_portfolio_momentum_window")
    indexed = frame.set_index(["role", "months_ago"])["adjusted_close"]
    required = {
        (role, month)
        for role in ROLES
        for month in MOMENTUM_MONTHS
    }
    if not required.issubset(indexed.index):
        raise ValueError("permanent_portfolio_momentum_window")
    selected = indexed.loc[list(required)]
    if selected.isna().any() or (selected <= 0).any():
        raise ValueError("permanent_portfolio_momentum_window")
    if set(tie_break) != set(ROLES):
        raise ValueError("permanent_portfolio_tie_break")

    cash_6_1 = indexed["cash", 1] / indexed["cash", 6] - 1.0
    cash_12_1 = indexed["cash", 1] / indexed["cash", 12] - 1.0
    scores = {"cash": 0.0}
    for role in ("equity", "bond", "gold"):
        momentum_6_1 = indexed[role, 1] / indexed[role, 6] - 1.0
        momentum_12_1 = indexed[role, 1] / indexed[role, 12] - 1.0
        scores[role] = 0.5 * (momentum_6_1 - cash_6_1) + 0.5 * (
            momentum_12_1 - cash_12_1
        )

    priority = {role: index for index, role in enumerate(tie_break)}
    ranked = sorted(
        ROLES,
        key=lambda role: (-scores[role], priority[role]),
    )
    return {
        role: RANK_WEIGHTS[index]
        for index, role in enumerate(ranked)
    }
