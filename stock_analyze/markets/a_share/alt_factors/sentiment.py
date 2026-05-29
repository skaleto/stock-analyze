"""Market-sentiment alt-factor: record + load.

The operator collects one sentiment reading per week by chatting with that
agent's LLM client (Claude.ai for ``claude``, ChatGPT for ``codex``) and
records it via ``record_market_sentiment`` (also exposed as the
``record-sentiment`` CLI subcommand). This module persists exactly one
durable row per (agent_id, week_end), with strict validation and a
duplicate-rejection rule that requires explicit ``force=True`` to overwrite.

See ``openspec/changes/add-llm-sentiment-alpha-factor/design.md`` §3 for
the full operator workflow and ``specs/weekly-market-sentiment-recording``
for the formal contract.
"""
from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional


CSV_HEADER = (
    "week_end_date,sentiment_score,confidence,key_drivers,sources,"
    "llm_model,prompt_version,recorded_at"
)


class DuplicateSentimentEntry(Exception):
    """Raised when a (agent_id, week_end) row already exists and force=False."""


@dataclass
class SentimentRow:
    week_end: date
    score: float
    confidence: float
    drivers: List[str]
    sources: List[str]
    llm_model: str
    prompt_version: str
    recorded_at: str


def _csv_path(agent_id: str, repo_root: Path) -> Path:
    # Phase 1 (Task 10) migration: A-share data now lives under data/a_share/<agent>/.
    # Phase 2/3 will refactor this to accept a `market` kwarg.
    return Path(repo_root) / "data" / "a_share" / agent_id / "alt_factors" / "market_sentiment.csv"


def _parse_row(row: dict) -> SentimentRow:
    return SentimentRow(
        week_end=date.fromisoformat(row["week_end_date"]),
        score=float(row["sentiment_score"]),
        confidence=float(row["confidence"]),
        drivers=row["key_drivers"].split("|") if row["key_drivers"] else [],
        sources=row["sources"].split("|") if row["sources"] else [],
        llm_model=row["llm_model"],
        prompt_version=row["prompt_version"],
        recorded_at=row["recorded_at"],
    )


def _serialise_row(row: SentimentRow) -> List[str]:
    # We use '|' as the inner separator for both drivers and sources because
    # driver text occasionally contains '、' or ','; '|' is rare in Chinese
    # financial commentary and keeps CSV parsing trivial.
    return [
        row.week_end.isoformat(),
        f"{row.score:.4f}",
        f"{row.confidence:.4f}",
        "|".join(row.drivers),
        "|".join(row.sources),
        row.llm_model,
        row.prompt_version,
        row.recorded_at,
    ]


def _atomic_write(path: Path, rows: List[SentimentRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".",
                                     suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER.split(","))
            for r in rows:
                writer.writerow(_serialise_row(r))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def record_market_sentiment(
    agent_id: str,
    week_end: date,
    score: float,
    confidence: float,
    drivers: List[str],
    sources: List[str],
    llm_model: str,
    prompt_version: str,
    repo_root: Path,
    force: bool = False,
) -> None:
    """Append (or replace) one sentiment row for (agent_id, week_end).

    Validation rules (raise ``ValueError`` on failure):
      - score in [-1.0, 1.0]
      - confidence in [0.0, 1.0]
      - drivers length in [1, 5]

    Duplicate rule: if a row already exists for ``week_end``, raise
    ``DuplicateSentimentEntry`` unless ``force=True``, in which case the
    existing row is replaced.

    Write is atomic (mkstemp + os.replace) so a crash never corrupts the
    on-disk CSV.
    """
    if not -1.0 <= score <= 1.0:
        raise ValueError(f"score must be in [-1.0, 1.0], got {score}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence}")
    if not drivers or len(drivers) > 5:
        raise ValueError(
            f"drivers must have between 1 and 5 entries, got {len(drivers)}"
        )

    path = _csv_path(agent_id, repo_root)
    existing = load_sentiment_history(agent_id, repo_root)
    matching = [r for r in existing if r.week_end == week_end]
    if matching and not force:
        raise DuplicateSentimentEntry(
            f"{agent_id} already has sentiment for week_end={week_end.isoformat()}; "
            "use force=True to overwrite"
        )
    if matching and force:
        existing = [r for r in existing if r.week_end != week_end]

    new_row = SentimentRow(
        week_end=week_end,
        score=score,
        confidence=confidence,
        drivers=list(drivers),
        sources=list(sources),
        llm_model=llm_model,
        prompt_version=prompt_version,
        recorded_at=datetime.now().isoformat(timespec="seconds"),
    )
    existing.append(new_row)
    existing.sort(key=lambda r: r.week_end)
    _atomic_write(path, existing)


def load_sentiment_history(
    agent_id: str,
    repo_root: Path,
    last_n: Optional[int] = None,
) -> List[SentimentRow]:
    """Return all recorded rows for ``agent_id`` in chronological order.

    ``last_n`` (when set) keeps only the most recent N rows.
    """
    path = _csv_path(agent_id, repo_root)
    if not path.exists():
        return []
    rows: List[SentimentRow] = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(_parse_row(row))
    rows.sort(key=lambda r: r.week_end)
    if last_n is not None:
        rows = rows[-last_n:]
    return rows


def load_latest_market_sentiment(
    agent_id: str,
    as_of: date,
    repo_root: Path,
) -> Optional[float]:
    """Return the sentiment score for the most recent week_end ≤ as_of, or None."""
    rows = load_sentiment_history(agent_id, repo_root)
    eligible = [r for r in rows if r.week_end <= as_of]
    if not eligible:
        return None
    return eligible[-1].score


def remove_sentiment(
    agent_id: str,
    week_end: date,
    repo_root: Path,
) -> None:
    """Remove the row matching ``week_end``. Raises ``ValueError`` if not found."""
    existing = load_sentiment_history(agent_id, repo_root)
    new_rows = [r for r in existing if r.week_end != week_end]
    if len(new_rows) == len(existing):
        raise ValueError(
            f"No row found for {agent_id} week_end={week_end.isoformat()}"
        )
    _atomic_write(_csv_path(agent_id, repo_root), new_rows)


# ---------------------------------------------------------------------------
# Phase 3: sector-level sentiment (per-industry, becomes a per-stock factor)
#
# Unlike the single-scalar market sentiment above (broadcast — zero
# cross-sectional effect), sector sentiment records one score per industry
# per week. Each candidate stock inherits its industry's score, so the
# factor genuinely affects ranking. See OpenSpec change
# ``add-llm-sentiment-alpha-factor`` Phase 3 / the 2026-05-29 design.
# ---------------------------------------------------------------------------


SECTOR_CSV_HEADER = (
    "week_end,industry,score,confidence,llm_model,prompt_version,recorded_at"
)


@dataclass
class SectorSentimentRow:
    week_end: date
    industry: str
    score: float
    confidence: float
    llm_model: str
    prompt_version: str
    recorded_at: str


def _sector_csv_path(agent_id: str, repo_root: Path) -> Path:
    return (
        Path(repo_root) / "data" / "a_share" / agent_id
        / "alt_factors" / "sector_sentiment.csv"
    )


def _serialise_sector_row(row: SectorSentimentRow) -> List[str]:
    return [
        row.week_end.isoformat(),
        row.industry,
        f"{row.score:.4f}",
        f"{row.confidence:.4f}",
        row.llm_model,
        row.prompt_version,
        row.recorded_at,
    ]


def _atomic_write_sector(path: Path, rows: List[SectorSentimentRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(SECTOR_CSV_HEADER.split(","))
            for r in rows:
                writer.writerow(_serialise_sector_row(r))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_sector_sentiment(
    agent_id: str,
    repo_root: Path,
) -> List[SectorSentimentRow]:
    """Return all recorded sector-sentiment rows in chronological order."""
    path = _sector_csv_path(agent_id, repo_root)
    if not path.exists():
        return []
    rows: List[SectorSentimentRow] = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(SectorSentimentRow(
                week_end=date.fromisoformat(row["week_end"]),
                industry=row["industry"],
                score=float(row["score"]),
                confidence=float(row["confidence"]),
                llm_model=row.get("llm_model", ""),
                prompt_version=row.get("prompt_version", ""),
                recorded_at=row.get("recorded_at", ""),
            ))
    rows.sort(key=lambda r: (r.week_end, r.industry))
    return rows


def record_sector_sentiment(
    agent_id: str,
    week_end: date,
    sectors: List[dict],
    llm_model: str,
    prompt_version: str,
    repo_root: Path,
    force: bool = False,
) -> int:
    """Record one week of per-industry sentiment for ``agent_id``.

    ``sectors`` is a list of ``{"industry": str, "score": float,
    "confidence": float}``. Validation (raise ``ValueError``):
      - non-empty list, no duplicate industries within the batch
      - each score in [-1.0, 1.0], confidence in [0.0, 1.0]
      - industry non-empty

    Duplicate rule mirrors market sentiment: if the (agent, week_end) already
    has rows, raise ``DuplicateSentimentEntry`` unless ``force=True`` (which
    replaces that week's rows). Returns the number of rows written for the week.
    Write is atomic.
    """
    if not sectors:
        raise ValueError("sectors must be a non-empty list")
    seen_industries: set[str] = set()
    parsed: List[SectorSentimentRow] = []
    recorded_at = datetime.now().isoformat(timespec="seconds")
    for s in sectors:
        industry = str(s.get("industry", "")).strip()
        if not industry:
            raise ValueError(f"sector row missing industry: {s!r}")
        if industry in seen_industries:
            raise ValueError(f"duplicate industry in batch: {industry!r}")
        seen_industries.add(industry)
        score = float(s["score"])
        confidence = float(s["confidence"])
        if not -1.0 <= score <= 1.0:
            raise ValueError(f"score for {industry} must be in [-1.0, 1.0], got {score}")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence for {industry} must be in [0.0, 1.0], got {confidence}")
        parsed.append(SectorSentimentRow(
            week_end=week_end, industry=industry, score=score,
            confidence=confidence, llm_model=llm_model,
            prompt_version=prompt_version, recorded_at=recorded_at,
        ))

    existing = load_sector_sentiment(agent_id, repo_root)
    matching = [r for r in existing if r.week_end == week_end]
    if matching and not force:
        raise DuplicateSentimentEntry(
            f"{agent_id} already has sector sentiment for week_end="
            f"{week_end.isoformat()}; use force=True to overwrite"
        )
    if matching and force:
        existing = [r for r in existing if r.week_end != week_end]
    existing.extend(parsed)
    existing.sort(key=lambda r: (r.week_end, r.industry))
    _atomic_write_sector(_sector_csv_path(agent_id, repo_root), existing)
    return len(parsed)


def load_latest_sector_sentiment(
    agent_id: str,
    as_of: date,
    repo_root: Path,
) -> dict[str, float]:
    """Return ``{industry: score × confidence}`` for the latest week ≤ as_of.

    Empty dict when there is no eligible week. The confidence weighting is
    baked in here (same convention as the broadcast market factor) so the
    strategy layer just maps industry → scalar.
    """
    rows = load_sector_sentiment(agent_id, repo_root)
    eligible = [r for r in rows if r.week_end <= as_of]
    if not eligible:
        return {}
    latest_week = max(r.week_end for r in eligible)
    return {
        r.industry: float(r.score) * float(r.confidence)
        for r in eligible
        if r.week_end == latest_week
    }
