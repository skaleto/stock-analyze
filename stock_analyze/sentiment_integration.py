"""Shared glue for market-aware LLM sentiment factors."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .factor_pipeline import (
    is_broadcast_factor,
    is_sector_sentiment_factor,
    load_broadcast_factor,
)


def coerce_as_of_date(as_of: date | str | None) -> date:
    if isinstance(as_of, date):
        return as_of
    if isinstance(as_of, str) and as_of:
        try:
            return date.fromisoformat(as_of)
        except ValueError:
            return date.today()
    return date.today()


def default_repo_root() -> Path:
    env_root = os.environ.get("SA_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root)
        if candidate.exists():
            return candidate
    here = Path(__file__).resolve()
    candidate = here.parent.parent
    if (candidate / "stock_analyze").exists():
        return candidate
    return Path.cwd()


def resolve_broadcast_values(
    config: dict[str, Any],
    as_of: date | str | None,
    repo_root: Path | str | None,
    market: str,
) -> dict[str, float | None] | None:
    factors = config.get("factors", {}) or {}
    broadcast_names = [name for name in factors if is_broadcast_factor(name)]
    if not broadcast_names:
        return None
    agent_id = config.get("agent_id")
    if not agent_id:
        return {name: None for name in broadcast_names}
    root = Path(repo_root) if repo_root else default_repo_root()
    as_of_date = coerce_as_of_date(as_of)
    return {
        name: load_broadcast_factor(
            agent_id, name, as_of_date, root, market=market,
        )
        for name in broadcast_names
    }


def apply_sector_sentiment_columns(
    config: dict[str, Any],
    candidates: pd.DataFrame,
    as_of: date | str | None,
    repo_root: Path | str | None,
    market: str,
) -> pd.DataFrame:
    factors = config.get("factors", {}) or {}
    sector_names = [name for name in factors if is_sector_sentiment_factor(name)]
    if not sector_names:
        return candidates
    agent_id = config.get("agent_id")
    if not agent_id:
        return candidates

    from stock_analyze.markets.a_share.alt_factors import sentiment as alt_sent

    root = Path(repo_root) if repo_root else default_repo_root()
    sector_map = alt_sent.load_latest_sector_sentiment(
        agent_id, coerce_as_of_date(as_of), root, market=market,
    )
    out = candidates.copy()
    if "industry" in out.columns and sector_map:
        values = out["industry"].map(sector_map)
    else:
        values = pd.Series([float("nan")] * len(out), index=out.index)
    for name in sector_names:
        out[name] = values
    return out
