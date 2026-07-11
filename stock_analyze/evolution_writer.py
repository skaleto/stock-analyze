"""Write the four artifacts of an LLM-direct strategy evolution.

When an LLM agent decides on a monthly strategy change, it calls
:func:`write_evolution` with the **old** and **new** overlay dicts plus a
free-form markdown reasoning text. The writer is responsible for:

1. Running ``overlay_guard.validate`` on the new overlay; on failure no
   side effects happen (we abort before touching disk).
2. Auto-backing the **prior** overlay file content into
   ``configs/agents/_history/<from_hash>.yaml`` (only if that hash is not
   already there).
3. Overwriting ``configs/agents/<agent>.yaml`` with the new overlay (JSON
   syntax, matching the project convention).
4. Writing ``data/<agent>/evolution_log/<YYYY-MM>.md`` containing the
   reasoning text verbatim.
5. Writing ``data/<agent>/evolution_diff/<YYYY-MM>.json`` containing the
   structured diff per design §4.
6. Appending one row to ``data/<agent>/config_evolution.csv`` with the
   ``reasoning_file`` and ``diff_file`` columns. If the CSV pre-existed
   under the old (Change-A) schema, the file is migrated in place to add
   the new columns.

The writer is intentionally pure-Python with no LLM call. It is invoked
from the slash command body after the LLM has already drafted the new
overlay and reasoning text.
"""

from __future__ import annotations

import csv
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import competition, overlay_guard
from .config import config_hash
from .monthly_review import default_month_for
from .utils import ensure_dirs, write_text_atomic


# Columns of ``data/<agent>/config_evolution.csv`` after this change.
# Old (Change-A) rows had a subset of these; on first write we migrate.
EVOLUTION_COLUMNS: list[str] = [
    "event",
    "event_at",
    "agent_id",
    "market",
    "month",
    "from_hash",
    "to_hash",
    "diff_summary",
    "reasoning_file",
    "diff_file",
    "reviewer",
]


# Columns kept for backwards-compatibility when migrating existing CSV files
# written by the deleted ``proposal_apply`` flow.
_LEGACY_COLUMNS: list[str] = [
    "event",
    "event_at",
    "agent_id",
    "month",
    "source_proposal",
    "decision_path",
    "from_hash",
    "to_hash",
    "patch_paths",
    "reviewer",
]


_BACKTEST_NOT_PREVALIDATED = object()


def write_evolution(
    agent_id: str,
    old_overlay: dict[str, Any],
    new_overlay: dict[str, Any],
    reasoning_md: str,
    repo_root: str | Path | None = None,
    month: str | None = None,
    reviewer: str = "llm-direct",
    *,
    market: str = "a_share",
    validated_backtest_metrics: Any = _BACKTEST_NOT_PREVALIDATED,
) -> dict[str, Any]:
    """Persist the four artifacts and return a summary dict.

    Parameters
    ----------
    agent_id:
        The competing agent (``claude`` or ``codex``).
    old_overlay:
        The previously-on-disk overlay (used to compute ``from_hash`` and
        backup, **not** trusted for guard checks — the new overlay must be
        valid on its own).
    new_overlay:
        The about-to-be-on-disk overlay. Subject to ``overlay_guard.validate``.
    reasoning_md:
        Markdown reasoning text written by the LLM. Saved verbatim.
    repo_root:
        Repo root for path resolution. Defaults to ``Path.cwd()``.
    month:
        Target month (``YYYY-MM``). Defaults to ``default_month_for()``.
    reviewer:
        Free-form label recorded in the CSV. Defaults to ``llm-direct``.
    validated_backtest_metrics:
        Internal release-orchestration hook. When supplied for A-share, the
        caller has already run the gate as part of a multi-overlay preflight.

    Returns
    -------
    dict
        Summary with ``status``, ``from_hash``, ``to_hash``, and paths to
        the four written artifacts.

    Raises
    ------
    overlay_guard.OverlayGuardError
        On any guard violation (no side effects in that case).
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_market_paths(market, agent_id, repo_root=root)
    target_month = month or default_month_for()

    # 1. Guard check first; if this raises, no side effects happen.
    overlay_guard.validate(
        agent_id,
        new_overlay,
        repo_root=root,
        market=market,
    )

    # 1b. Backtest floor gate. If it raises BacktestFloorBreach, no side
    # effects on yaml; we write a breach log and re-raise. Imported lazily
    # so optional backtest support doesn't load when not needed.
    from .markets.a_share.backtest import gate as backtest_gate
    from .markets.a_share.backtest.exceptions import BacktestFloorBreach

    backtest_status = "not_available"
    backtest_metrics = None
    if market == "a_share":
        if validated_backtest_metrics is not _BACKTEST_NOT_PREVALIDATED:
            backtest_metrics = validated_backtest_metrics
            backtest_status = "passed"
        else:
            try:
                backtest_metrics = backtest_gate.validate_overlay_via_backtest(
                    new_overlay, agent_id=agent_id,
                )
                backtest_status = "passed"
            except BacktestFloorBreach as breach:
                _write_floor_breach_log(
                    agent_id=agent_id,
                    market=market,
                    month=target_month,
                    breach=breach,
                    reasoning_md=reasoning_md,
                    repo_root=root,
                )
                raise
            except Exception as exc:  # noqa: BLE001
                # Direct local evolution may not have the historical cache.
                # Manifest releases use strict preflight in strategy_release.
                import logging
                logging.warning(
                    "backtest gate skipped (cache missing or engine error): %s", exc
                )
                backtest_status = "skipped"

    # Resolve hashes through the same `competition.load`-style merge so
    # they are comparable with the hashes already in `runs.csv`.
    from_hash = _config_hash_for_overlay(agent_id, old_overlay, root, market)
    to_hash = _config_hash_for_overlay(agent_id, new_overlay, root, market)

    # 2. Backup the current overlay (idempotent — skip if already present).
    history_path = _history_path(root, from_hash)
    ensure_dirs(history_path.parent)
    if not history_path.exists():
        write_text_atomic(
            history_path,
            json.dumps(old_overlay, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 3. Overwrite the live overlay.
    overlay_path = paths.config_path
    old_text = overlay_path.read_text(encoding="utf-8") if overlay_path.exists() else ""
    try:
        write_text_atomic(
            overlay_path,
            json.dumps(new_overlay, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        if old_text:
            write_text_atomic(overlay_path, old_text, encoding="utf-8")
        raise

    # 4. Markdown reasoning.
    log_path = paths.data_dir / "evolution_log" / f"{target_month}.md"
    ensure_dirs(log_path.parent)
    write_text_atomic(log_path, reasoning_md, encoding="utf-8")

    # 5. Machine-readable diff JSON.
    diff = compute_diff(old_overlay, new_overlay)
    diff_path = paths.data_dir / "evolution_diff" / f"{target_month}.json"
    ensure_dirs(diff_path.parent)
    diff_payload: dict[str, Any] = {
        "agent_id": agent_id,
        "market": market,
        "month": target_month,
        "evolved_at": datetime.now().isoformat(timespec="seconds"),
        "from_config_hash": from_hash,
        "to_config_hash": to_hash,
        "diff": diff,
        "reasoning_file": _relative_or_str(log_path, root),
        "guard_checks_passed": [
            "schema_valid",
            "no_baseline_lock_violation",
            "factors_in_whitelist",
            "weights_in_range",
        ],
        "backtest_status": backtest_status,
    }
    if backtest_metrics is not None:
        diff_payload["backtest_metrics"] = {
            "cum_return": backtest_metrics.cum_return,
            "annual_return": backtest_metrics.annual_return,
            "sharpe": backtest_metrics.sharpe,
            "max_drawdown": backtest_metrics.max_drawdown,
            "information_ratio": backtest_metrics.information_ratio,
        }
        diff_payload["guard_checks_passed"].append("backtest_floor_ok")
    write_text_atomic(
        diff_path,
        json.dumps(diff_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 6. CSV row (migrate columns if needed).
    csv_path = paths.data_dir / "config_evolution.csv"
    diff_summary = summarise_diff(diff)
    _append_evolution_row(
        csv_path,
        {
            "event": "evolve",
            "event_at": datetime.now().isoformat(timespec="seconds"),
            "agent_id": agent_id,
            "market": market,
            "month": target_month,
            "from_hash": from_hash,
            "to_hash": to_hash,
            "diff_summary": diff_summary,
            "reasoning_file": _relative_or_str(log_path, root),
            "diff_file": _relative_or_str(diff_path, root),
            "reviewer": reviewer,
        },
    )

    return {
        "status": "evolved",
        "agent_id": agent_id,
        "market": market,
        "month": target_month,
        "from_hash": from_hash,
        "to_hash": to_hash,
        "overlay_path": str(overlay_path),
        "log_path": str(log_path),
        "diff_path": str(diff_path),
        "csv_path": str(csv_path),
        "history_path": str(history_path),
    }


# ---------------------------------------------------------------------------
# Public helpers


def compute_diff(
    old: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return ``{dotted.path: {from, to}}`` of every leaf that changed.

    Leaves are anything not a dict (numbers, strings, booleans, lists).
    Missing keys appear with ``from=None`` or ``to=None``. Identical leaves
    are omitted.
    """

    result: dict[str, dict[str, Any]] = {}
    _diff_recurse(old, new, prefix="", out=result)
    return result


def summarise_diff(diff: dict[str, dict[str, Any]], limit: int = 6) -> str:
    """One-line human summary of a diff dict (truncated to ``limit`` keys)."""

    if not diff:
        return "no_change"
    fragments: list[str] = []
    for idx, (path, change) in enumerate(diff.items()):
        if idx >= limit:
            fragments.append(f"…+{len(diff) - limit} more")
            break
        from_v = change.get("from")
        to_v = change.get("to")
        fragments.append(f"{path}:{_brief(from_v)}→{_brief(to_v)}")
    return "; ".join(fragments)


# ---------------------------------------------------------------------------
# Internals


def _write_floor_breach_log(
    *,
    agent_id: str,
    market: str,
    month: str,
    breach: Any,  # BacktestFloorBreach — typed as Any to avoid top-level import
    reasoning_md: str,
    repo_root: Path,
) -> Path:
    """Persist a `<month>-floor-breach.md` capturing why the gate rejected.

    Written under ``data/<agent>/evolution_log/`` alongside the normal
    monthly log. The live overlay yaml and ``config_evolution.csv`` are
    NOT touched in this path — the operator must read this file and
    redesign before re-attempting.
    """
    paths = competition.resolve_market_paths(market, agent_id, repo_root=repo_root)
    out = paths.data_dir / "evolution_log" / f"{month}-floor-breach.md"
    ensure_dirs(out.parent)
    m = breach.metrics
    body = (
        f"# {agent_id} 回测准入失败 · {month}\n\n"
        f"## 失败原因\n\n"
        f"- 类型: `{breach.breach_type}`\n"
        f"- 验证窗口指标:\n"
        f"  - 累计: {m.cum_return:+.1%}\n"
        f"  - 年化: {m.annual_return:+.1%}\n"
        f"  - Sharpe: {m.sharpe:.2f}\n"
        f"  - 最大回撤: {m.max_drawdown:+.1%}\n"
        f"  - IR: {m.information_ratio:.2f}\n\n"
        f"## LLM 原始 reasoning\n\n"
        f"{reasoning_md}\n"
    )
    write_text_atomic(out, body, encoding="utf-8")
    return out


def _config_hash_for_overlay(
    agent_id: str,
    overlay: dict[str, Any],
    root: Path,
    market: str,
) -> str:
    """Compute the same hash ``runs.csv`` writes, given an in-memory overlay.

    Mirrors ``competition.load(agent_id)`` but using the passed-in overlay
    instead of re-reading the disk file — so callers can hash both ``old``
    and ``new`` without round-tripping through the filesystem.
    """

    try:
        merged = competition.validate_overlay(
            agent_id,
            overlay,
            repo_root=root,
            market=market,
        )
    except competition.CompetitionBaselineLocked:
        # Shouldn't reach here because guard runs first; fall back to a
        # deepcopy-only hash so callers still get *some* identifier.
        merged = deepcopy(overlay)
        merged.setdefault("agent_id", agent_id)
    return config_hash(merged)


def _history_path(root: Path, digest: str) -> Path:
    return root / "configs" / "agents" / "_history" / f"{digest}.yaml"


def _diff_recurse(
    old: Any,
    new: Any,
    prefix: str,
    out: dict[str, dict[str, Any]],
) -> None:
    # If either side is a mapping while the other is missing or a scalar,
    # recurse into the mapping so each inner leaf shows up in the diff.
    if isinstance(old, dict) or isinstance(new, dict):
        old_map = old if isinstance(old, dict) else {}
        new_map = new if isinstance(new, dict) else {}
        keys = sorted(set(old_map.keys()) | set(new_map.keys()))
        # If both sides are scalars (e.g. None vs None after defaulting),
        # fall through to the leaf check below.
        if keys:
            for key in keys:
                child = f"{prefix}.{key}" if prefix else str(key)
                _diff_recurse(old_map.get(key), new_map.get(key), child, out)
            return
    if old == new:
        return
    out[prefix] = {"from": old, "to": new}


def _brief(value: Any) -> str:
    if value is None:
        return "∅"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) > 30:
        return text[:27] + "…"
    return text


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _append_evolution_row(csv_path: Path, row: dict[str, Any]) -> None:
    """Append one row, migrating the file in place if its header is old."""

    ensure_dirs(csv_path.parent)
    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVOLUTION_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerow(row)
        return

    existing_rows, existing_header = _read_csv_rows(csv_path)
    if existing_header == EVOLUTION_COLUMNS:
        with csv_path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVOLUTION_COLUMNS, extrasaction="ignore")
            writer.writerow(row)
        return

    # Migrate: rewrite with the new schema, padding old rows with empty
    # values for the new columns. Preserves order.
    migrated_rows = [
        {key: old.get(key, "") for key in EVOLUTION_COLUMNS}
        for old in existing_rows
    ]
    migrated_rows.append({key: row.get(key, "") for key in EVOLUTION_COLUMNS})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVOLUTION_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(migrated_rows)


def _read_csv_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return rows, header
