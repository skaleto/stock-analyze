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


def write_evolution(
    agent_id: str,
    old_overlay: dict[str, Any],
    new_overlay: dict[str, Any],
    reasoning_md: str,
    repo_root: str | Path | None = None,
    month: str | None = None,
    reviewer: str = "llm-direct",
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
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    target_month = month or default_month_for()

    # 1. Guard check first; if this raises, no side effects happen.
    overlay_guard.validate(agent_id, new_overlay, repo_root=root)

    # Resolve hashes through the same `competition.load`-style merge so
    # they are comparable with the hashes already in `runs.csv`.
    from_hash = _config_hash_for_overlay(agent_id, old_overlay, root)
    to_hash = _config_hash_for_overlay(agent_id, new_overlay, root)

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
    write_text_atomic(
        diff_path,
        json.dumps(
            {
                "agent_id": agent_id,
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
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
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


def _config_hash_for_overlay(
    agent_id: str,
    overlay: dict[str, Any],
    root: Path,
) -> str:
    """Compute the same hash ``runs.csv`` writes, given an in-memory overlay.

    Mirrors ``competition.load(agent_id)`` but using the passed-in overlay
    instead of re-reading the disk file — so callers can hash both ``old``
    and ``new`` without round-tripping through the filesystem.
    """

    try:
        merged = competition.validate_overlay(agent_id, overlay, repo_root=root)
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
