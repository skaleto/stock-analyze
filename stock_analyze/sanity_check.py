"""Weekly anomaly detector for per-agent data directories.

Runs a battery of cheap, idempotent checks against an agent's
``data/<agent>/`` files (NAV, positions, trades, forward IC, factor
coverage) and reports any anomalies that look like pipeline regressions —
not strategy losses, but plumbing problems.

The checks are deliberately conservative: each one targets a real
failure mode we've actually seen in this project's history, so a green
result from sanity_check is meaningful evidence the data plumbing is
healthy, not a noisy fire alarm.

Public surface:

* :class:`Anomaly` — dataclass with ``severity``, ``check_name``,
  ``message``, ``detail``.
* :func:`check_agent` — run all checks and return a flat list.
* :func:`format_report` — render the list to a human-readable string.
* :func:`max_severity` — collapse a list to a single worst-case label.
* CLI entry-point via ``python3 -m stock_analyze sanity-check --agent X``.

Designed for the weekly Friday rhythm: invoked after ``run-weekly``
finishes, output appended to ``logs/sanity_check.log``. Critical findings
trip the same PIPELINE_FAILURES.log notification path as a hard pipeline
crash (see ``scripts/notify-pipeline-failure.sh``).

Each check returns 0+ :class:`Anomaly` records. None of the checks raise
on missing input files — a missing ``daily_nav.csv`` for a brand-new
agent is treated as ``info`` (not ``critical``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .markets.a_share.data_provider import INDEX_CODES
from .store import (
    DAILY_NAV_FILE,
    FACTOR_FORWARD_IC_FILE,
    POSITIONS_FILE,
    SIGNALS_FILE,
    TRADES_FILE,
    PortfolioStore,
)
from .utils import safe_float


SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}


@dataclass
class Anomaly:
    """A single sanity-check finding.

    ``severity`` is one of ``info`` | ``warn`` | ``critical``. The CLI
    exit code maps from ``max_severity`` (0/1/2) so operators can wire
    the command into a notification rule.

    ``detail`` holds whatever raw numbers the check measured so a human
    investigating later doesn't have to re-derive them.
    """

    severity: str
    check_name: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_nav_jump(
    nav_df: pd.DataFrame,
    *,
    warn_threshold: float = 0.05,
    critical_threshold: float = 0.10,
) -> list[Anomaly]:
    """Flag any single-day NAV change beyond a plausible market move.

    A-share daily limit is ±10% (±20% for STAR / ChiNext), so a portfolio
    NAV moving >5% in one day with no corresponding trade is a strong
    signal that either pricing data is corrupt or a trade rounding bug
    blew up the position count. >10% is almost certainly broken.

    We aggregate across all accounts before computing the day-over-day
    change because a single account's NAV can move with cash inflow /
    outflow; the totalled portfolio shouldn't.
    """
    if nav_df.empty:
        return []
    if "total_value" not in nav_df.columns or "date" not in nav_df.columns:
        return []
    daily = nav_df.groupby("date")["total_value"].sum().sort_index()
    if len(daily) < 2:
        return []
    pct = daily.pct_change().dropna()
    findings: list[Anomaly] = []
    for date, change in pct.items():
        change = float(change)
        if abs(change) >= critical_threshold:
            findings.append(
                Anomaly(
                    severity="critical",
                    check_name="nav_jump",
                    message=(
                        f"NAV moved {change:+.1%} on {date} — beyond the "
                        f"±{critical_threshold:.0%} plausible-market band; "
                        "investigate price feed / trade rounding."
                    ),
                    detail={"date": str(date), "pct_change": change},
                )
            )
        elif abs(change) >= warn_threshold:
            findings.append(
                Anomaly(
                    severity="warn",
                    check_name="nav_jump",
                    message=(
                        f"NAV moved {change:+.1%} on {date} — large but "
                        "within daily limit. Confirm against benchmark."
                    ),
                    detail={"date": str(date), "pct_change": change},
                )
            )
    return findings


def check_position_count_drop(
    positions_df: pd.DataFrame,
    *,
    expected_min: int = 50,
) -> list[Anomaly]:
    """Flag when current position count is materially below ``expected_min``.

    The locked baseline runs two top_50 accounts so a healthy state
    typically holds 50-100 positions. A sudden drop to <50 means either
    the universe fetch failed (so the strategy had nothing to score) or
    the lot-size+5%-cap math collapsed (the Tier-1+2 sizing fix in
    simulator.py is the guard against that — this check is the backstop).
    """
    if positions_df.empty:
        return [
            Anomaly(
                severity="info",
                check_name="position_count",
                message="positions.csv is empty (cold-start agent or pre-init)",
                detail={"row_count": 0},
            )
        ]
    n = len(positions_df)
    if n < expected_min:
        severity = "critical" if n < expected_min // 2 else "warn"
        return [
            Anomaly(
                severity=severity,
                check_name="position_count",
                message=(
                    f"Holding {n} positions, expected ≥ {expected_min}. "
                    "Likely universe fetch failure or sizing collapse."
                ),
                detail={"row_count": n, "expected_min": expected_min},
            )
        ]
    return []


def check_forward_ic_coverage(
    ic_df: pd.DataFrame,
    *,
    nan_ratio_threshold: float = 0.30,
    lookback_weeks: int = 4,
) -> list[Anomaly]:
    """Flag when too many factor IC values are NaN over the recent window.

    Forward IC NaN-ing means the factor pipeline isn't producing enough
    paired (factor, forward_return) observations to compute correlation.
    Common cause: factor coverage dropped to near zero (data source down)
    or the universe shrunk so far the IC denominator is too small.
    """
    if ic_df.empty:
        return []
    if "signal_date" not in ic_df.columns or "ic" not in ic_df.columns:
        return []
    dates = sorted(ic_df["signal_date"].unique())
    recent = dates[-lookback_weeks:]
    recent_df = ic_df[ic_df["signal_date"].isin(recent)]
    total = len(recent_df)
    if total == 0:
        return []
    # An "ic_status" column written by the factor pipeline marks
    # NaN-producing rows explicitly; fall back to checking the value
    # itself if the column is missing.
    if "ic_status" in recent_df.columns:
        nan_rows = recent_df[recent_df["ic_status"] != "ok"]
    else:
        nan_rows = recent_df[recent_df["ic"].isna()]
    ratio = len(nan_rows) / total
    if ratio >= nan_ratio_threshold:
        return [
            Anomaly(
                severity="warn",
                check_name="forward_ic_coverage",
                message=(
                    f"{ratio:.0%} of factor IC rows over the last "
                    f"{lookback_weeks} weeks are NaN (>{nan_ratio_threshold:.0%}); "
                    "check factor coverage and universe size."
                ),
                detail={
                    "nan_ratio": ratio,
                    "lookback_weeks": lookback_weeks,
                    "sample_size": total,
                },
            )
        ]
    return []


def check_benchmark_code_dtype(nav_df: pd.DataFrame) -> list[Anomaly]:
    """Catch the int-coercion regression in daily_nav.benchmark_code.

    Symptom: pandas reads ``benchmark_code='000300'`` as int 300, and
    downstream groupbys split or fail. ``store.py.read_nav`` pins the
    dtype to str now, but a third-party tool writing to the same file
    could still corrupt it. We verify every value either:
      - parses as one of the canonical INDEX_CODES (str values), or
      - has a 6-character leading-zero form that round-trips, or
      - is the literal empty string / NaN.

    Anything else is a critical regression because store.py invariant
    is the foundation other dtype hints layer on.
    """
    if nav_df.empty or "benchmark_code" not in nav_df.columns:
        return []
    valid = set(INDEX_CODES.values())
    bad: list[Any] = []
    for raw in nav_df["benchmark_code"].dropna().unique():
        text = str(raw)
        if text == "" or text == "nan":
            continue
        if text in valid:
            continue
        if text.isdigit() and len(text) == 6 and text.lstrip("0") != text:
            # leading-zero form — looks correct even if not in our canonical set
            continue
        bad.append(raw)
    if bad:
        return [
            Anomaly(
                severity="critical",
                check_name="benchmark_code_dtype",
                message=(
                    f"daily_nav.benchmark_code contains non-canonical values: "
                    f"{sorted(map(str, bad))[:5]}. Likely int coercion regression — "
                    "check writer dtype hints."
                ),
                detail={"bad_values": [str(x) for x in bad]},
            )
        ]
    return []


def check_trades_freshness(
    trades_df: pd.DataFrame,
    *,
    stale_days: int = 14,
) -> list[Anomaly]:
    """Flag when the most recent trade is older than ``stale_days``.

    This is ``info``-level because a quiet stretch isn't necessarily
    pathological — the strategy might just be holding through low-turnover
    weeks. But a >2-week silence combined with active signal generation
    is a hint that the execution layer might be broken (pending orders
    not converting), so the operator should glance at it.
    """
    if trades_df.empty:
        return [
            Anomaly(
                severity="info",
                check_name="trades_freshness",
                message="trades.csv is empty (cold-start agent or just initialised)",
                detail={"row_count": 0},
            )
        ]
    if "trade_date" not in trades_df.columns:
        return []
    try:
        last = pd.to_datetime(trades_df["trade_date"]).max()
    except (TypeError, ValueError):
        return []
    if pd.isna(last):
        return []
    today = pd.Timestamp.today().normalize()
    age = (today - last.normalize()).days
    if age > stale_days:
        return [
            Anomaly(
                severity="info",
                check_name="trades_freshness",
                message=(
                    f"Last trade was {age} days ago ({last.date().isoformat()}); "
                    "verify execution layer is converting pending orders."
                ),
                detail={"age_days": age, "last_trade": str(last.date())},
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------


def _resolve_repo_root(repo_root: Path | str | None) -> Path:
    """Mirror strategy.py's repo_root resolution: env > explicit > __file__-anchor."""
    if repo_root is not None:
        return Path(repo_root)
    import os

    env_root = os.environ.get("SA_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root)
        if candidate.exists():
            return candidate
    here = Path(__file__).resolve()
    file_anchored = here.parent.parent
    if (file_anchored / "stock_analyze").exists():
        return file_anchored
    return Path.cwd()


def check_agent(
    agent_id: str,
    repo_root: Path | str | None = None,
) -> list[Anomaly]:
    """Run every check against ``data/<agent>/`` and return a flat list.

    Missing files are tolerated — the only ``critical`` paths are
    benchmark_code dtype regressions and double-digit NAV jumps. A
    cold-start agent (empty positions / trades) produces ``info``-level
    notices, not failures.
    """
    root = _resolve_repo_root(repo_root)
    data_dir = root / "data" / agent_id
    store = PortfolioStore(data_dir)

    findings: list[Anomaly] = []

    nav_df = store.read_nav()
    findings += check_nav_jump(nav_df)
    findings += check_benchmark_code_dtype(nav_df)

    positions_df = store.read_positions()
    findings += check_position_count_drop(positions_df)

    trades_df = store.read_trades()
    findings += check_trades_freshness(trades_df)

    ic_df = store.read_forward_ic()
    findings += check_forward_ic_coverage(ic_df)

    return findings


def max_severity(findings: list[Anomaly]) -> str:
    """Return the worst severity in ``findings``, or ``"info"`` if empty."""
    if not findings:
        return "info"
    return max(findings, key=lambda a: SEVERITY_ORDER.get(a.severity, 0)).severity


def format_report(agent_id: str, findings: list[Anomaly]) -> str:
    """Render findings as a human-readable text block.

    Format mirrors the ``validate-overlay`` CLI output so operators see a
    consistent shape across tooling. Each line is prefixed with a
    severity tag so grep'ing for ``[critical]`` is easy.
    """
    if not findings:
        return f"agent={agent_id}: ✓ no anomalies"
    header = f"agent={agent_id}: {len(findings)} finding(s)"
    body_lines = []
    for f in findings:
        tag = f.severity.upper()
        body_lines.append(f"  [{tag}] {f.check_name}: {f.message}")
    return header + "\n" + "\n".join(body_lines)


__all__ = [
    "Anomaly",
    "check_agent",
    "check_benchmark_code_dtype",
    "check_forward_ic_coverage",
    "check_nav_jump",
    "check_position_count_drop",
    "check_trades_freshness",
    "format_report",
    "max_severity",
]
