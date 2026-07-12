"""Auditable per-index sentiment records for QDII shadow research."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ...utils import write_dataframe_csv_atomic


MAX_AGE_DAYS = 14
COLUMNS = [
    "agent",
    "week_end",
    "index_key",
    "score",
    "confidence",
    "drivers",
    "sources",
    "llm_model",
    "prompt_version",
    "observed_at",
    "expires_at",
]


class SentimentValidationError(ValueError):
    pass


def load_theme_sentiment(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.read_csv(
        target,
        dtype={
            "agent": str,
            "week_end": str,
            "index_key": str,
            "drivers": str,
            "sources": str,
            "llm_model": str,
            "prompt_version": str,
            "observed_at": str,
            "expires_at": str,
        },
    )
    return frame.reindex(columns=COLUMNS)


def record_theme_sentiment(
    path: str | Path,
    *,
    agent: str,
    week_end: str,
    index_key: str,
    score: float,
    confidence: float,
    drivers: str,
    sources: str,
    llm_model: str,
    prompt_version: str = "theme_v1",
    observed_at: datetime | None = None,
    force: bool = False,
) -> pd.DataFrame:
    if agent not in {"claude", "codex"}:
        raise SentimentValidationError("agent must be claude or codex")
    if not index_key.strip():
        raise SentimentValidationError("index_key is required")
    if not -1.0 <= float(score) <= 1.0:
        raise SentimentValidationError("score must be in [-1, 1]")
    if not 0.0 <= float(confidence) <= 1.0:
        raise SentimentValidationError("confidence must be in [0, 1]")
    urls = [item.strip() for item in str(sources).split("|") if item.strip()]
    if not urls or any(not item.startswith(("https://", "http://")) for item in urls):
        raise SentimentValidationError("at least one HTTP source URL is required")
    try:
        pd.Timestamp(week_end)
    except ValueError as exc:
        raise SentimentValidationError("week_end must be a date") from exc
    observed = (observed_at or datetime.now()).replace(tzinfo=None)
    row = {
        "agent": agent,
        "week_end": str(week_end)[:10],
        "index_key": index_key.strip(),
        "score": float(score),
        "confidence": float(confidence),
        "drivers": str(drivers).strip(),
        "sources": "|".join(urls),
        "llm_model": str(llm_model).strip(),
        "prompt_version": str(prompt_version).strip(),
        "observed_at": observed.isoformat(timespec="seconds"),
        "expires_at": (observed + timedelta(days=MAX_AGE_DAYS)).isoformat(timespec="seconds"),
    }
    target = Path(path)
    existing = load_theme_sentiment(target)
    key = (
        existing["agent"].astype(str).eq(agent)
        & existing["week_end"].astype(str).eq(row["week_end"])
        & existing["index_key"].astype(str).eq(row["index_key"])
    ) if not existing.empty else pd.Series(dtype=bool)
    if not existing.empty and key.any() and not force:
        raise SentimentValidationError("duplicate theme sentiment row")
    if not existing.empty and key.any():
        existing = existing.loc[~key]
    result = pd.DataFrame([row], columns=COLUMNS) if existing.empty else pd.concat(
        [existing, pd.DataFrame([row])], ignore_index=True
    )
    result = result.sort_values(["observed_at", "agent", "index_key"]).reset_index(drop=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_dataframe_csv_atomic(result, target, index=False, encoding="utf-8")
    return result


def theme_scores_as_of(
    records: pd.DataFrame,
    *,
    agent: str,
    as_of: str | datetime,
) -> dict[str, float]:
    if records is None or records.empty:
        return {}
    cutoff = pd.Timestamp(as_of)
    frame = records.loc[records["agent"].astype(str).eq(agent)].copy()
    frame["_observed"] = pd.to_datetime(frame["observed_at"], errors="coerce")
    frame["_expires"] = pd.to_datetime(frame["expires_at"], errors="coerce")
    frame = frame.loc[frame["_observed"].le(cutoff) & frame["_expires"].ge(cutoff)]
    if frame.empty:
        return {}
    frame = frame.sort_values(["_observed", "index_key"]).groupby("index_key", as_index=False).tail(1)
    age_days = ((cutoff - frame["_observed"]).dt.total_seconds() / 86_400.0).astype(int)
    decay = (1.0 - age_days / MAX_AGE_DAYS).clip(lower=0.0, upper=1.0)
    values = pd.to_numeric(frame["score"], errors="coerce") * pd.to_numeric(frame["confidence"], errors="coerce") * decay
    return {
        str(index_key): float(value)
        for index_key, value in zip(frame["index_key"], values)
        if pd.notna(value)
    }


def attach_point_in_time_sentiment(
    panel: pd.DataFrame,
    records: pd.DataFrame,
    *,
    agent: str,
) -> pd.DataFrame:
    frame = panel.copy()
    if frame.empty:
        frame["theme_sentiment_score"] = pd.Series(dtype=float)
        return frame
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    score_cache: dict[str, dict[str, float]] = {}
    values: list[float] = []
    for trade_date, index_key in zip(dates, frame["index_key"].astype(str)):
        if pd.isna(trade_date):
            values.append(float("nan"))
            continue
        key = trade_date.strftime("%Y-%m-%d")
        if key not in score_cache:
            score_cache[key] = theme_scores_as_of(
                records,
                agent=agent,
                as_of=f"{key}T23:59:59",
            )
        values.append(score_cache[key].get(index_key, float("nan")))
    frame["theme_sentiment_score"] = values
    return frame


__all__ = [
    "SentimentValidationError",
    "attach_point_in_time_sentiment",
    "load_theme_sentiment",
    "record_theme_sentiment",
    "theme_scores_as_of",
]
