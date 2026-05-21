"""Dual-agent competition runtime.

Loads ``configs/competition.yaml`` as a shared fairness baseline plus
``configs/agents/<agent_id>.yaml`` as per-agent strategy overlay, validates
that the overlay does not override baseline-locked fields, and resolves
on-disk paths so each agent has its own state/reports namespace while sharing
market-data cache.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import canonical_json, load_config, migrate_strategy_config


COMPETITION_CONFIG_FILE = "configs/competition.yaml"
AGENTS_CONFIG_DIR = "configs/agents"
DATA_ROOT = "data"
REPORTS_ROOT = "reports"
SHARED_DATA_DIR = "shared"
COMPETITION_DATA_DIR = "competition"
COMPETITION_REPORTS_DIR = "competition"
COMPETITION_METADATA_FILE = "competition_metadata.json"

# Fields where overlay must not override baseline. Use dotted paths for
# nested fields; ``accounts.*.cash`` matches every account's cash field.
BASELINE_LOCKED_PATHS: tuple[str, ...] = (
    "competition_id",
    "start_date",
    "initial_cash",
    "schedule.execution",
    "schedule.signal_day",
    "trading.lot_size",
    "trading.commission_rate",
    "trading.min_commission",
    "trading.stamp_tax_rate",
    "trading.slippage_rate",
    "trading.max_single_weight",
    "accounts.*.cash",
    "accounts.*.scope",
    "accounts.*.benchmark",
    "accounts.*.top_n",
)

# Per-agent overlay is only permitted to set these top-level keys (plus
# ``agent_id`` / ``strategy_id`` / ``name``). Other keys raise on load.
OVERLAY_ALLOWED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "agent_id",
        "strategy_id",
        "name",
        "factors",
        "factor_processing",
        "portfolio_controls",
        "filters",
    }
)


class CompetitionBaselineLocked(RuntimeError):
    """Raised when an agent overlay tries to override a locked baseline field."""

    def __init__(self, field: str, baseline_value: Any, overlay_value: Any) -> None:
        super().__init__(
            f"competition_baseline_locked:{field} "
            f"(baseline={baseline_value!r}, overlay={overlay_value!r})"
        )
        self.field = field
        self.baseline_value = baseline_value
        self.overlay_value = overlay_value


class UnknownAgent(KeyError):
    """Raised when ``resolve_agent_paths`` cannot find a matching overlay."""


@dataclass(frozen=True)
class AgentPaths:
    agent_id: str
    config_path: Path
    data_dir: Path
    reports_dir: Path
    shared_cache_dir: Path
    competition_data_dir: Path
    competition_reports_dir: Path


def list_agents(repo_root: str | Path | None = None) -> list[str]:
    """Return the agent IDs declared by ``configs/agents/*.yaml`` files."""

    root = Path(repo_root) if repo_root else Path.cwd()
    agent_dir = root / AGENTS_CONFIG_DIR
    if not agent_dir.exists():
        return []
    return sorted(path.stem for path in agent_dir.glob("*.yaml"))


def resolve_agent_paths(agent_id: str, repo_root: str | Path | None = None) -> AgentPaths:
    """Return the on-disk layout for a given agent."""

    root = Path(repo_root) if repo_root else Path.cwd()
    overlay_path = root / AGENTS_CONFIG_DIR / f"{agent_id}.yaml"
    if not overlay_path.exists():
        known = list_agents(root)
        raise UnknownAgent(f"unknown_agent:{agent_id}; known={known}")
    return AgentPaths(
        agent_id=agent_id,
        config_path=root / AGENTS_CONFIG_DIR / f"{agent_id}.yaml",
        data_dir=root / DATA_ROOT / agent_id,
        reports_dir=root / REPORTS_ROOT / agent_id,
        shared_cache_dir=root / DATA_ROOT / SHARED_DATA_DIR / "cache",
        competition_data_dir=root / DATA_ROOT / COMPETITION_DATA_DIR,
        competition_reports_dir=root / REPORTS_ROOT / COMPETITION_REPORTS_DIR,
    )


def load_baseline(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Load and return the competition baseline config."""

    root = Path(repo_root) if repo_root else Path.cwd()
    return load_config(root / COMPETITION_CONFIG_FILE)


def validate_overlay(
    agent_id: str,
    overlay: dict[str, Any],
    repo_root: str | Path | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an in-memory overlay and return the merged config.

    Same checks as :func:`load` (top-level whitelist, baseline-locked paths,
    config migration) but **never touches disk** beyond reading the baseline.
    Use this to test hypothetical overlays (e.g. proposed monthly patches)
    without leaving a temporary file that concurrent readers could observe.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    if baseline is None:
        baseline = load_baseline(root)

    _validate_overlay_top_level(overlay, agent_id)
    _validate_locked_paths(baseline, overlay)

    merged = _deep_merge(baseline, overlay)
    merged.setdefault("agent_id", agent_id)
    merged.setdefault("strategy_id", overlay.get("strategy_id", agent_id))
    migrate_strategy_config(merged)
    return merged


def load(agent_id: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Load a merged config = baseline + agent overlay.

    Validates that the overlay does not override locked baseline fields and
    that overlay only declares allowed top-level keys. Applies the standard
    `migrate_strategy_config` defaults so downstream code (factor pipeline,
    portfolio controls, performance) sees the same shape as in single-agent
    mode.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = resolve_agent_paths(agent_id, repo_root=root)

    if not paths.config_path.exists():
        raise UnknownAgent(f"unknown_agent:{agent_id}; missing {paths.config_path}")

    baseline = load_baseline(root)
    overlay = json.loads(paths.config_path.read_text(encoding="utf-8"))

    _validate_overlay_top_level(overlay, agent_id)
    _validate_locked_paths(baseline, overlay)

    merged = _deep_merge(baseline, overlay)
    merged.setdefault("agent_id", agent_id)
    merged.setdefault("strategy_id", overlay.get("strategy_id", agent_id))
    migrate_strategy_config(merged)
    return merged


def baseline_hash(baseline: dict[str, Any]) -> str:
    payload = canonical_json(baseline)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Internals


def _validate_overlay_top_level(overlay: dict[str, Any], agent_id: str) -> None:
    extras = set(overlay.keys()) - OVERLAY_ALLOWED_TOP_LEVEL
    if extras:
        raise CompetitionBaselineLocked(
            field=f"overlay_top_level:{sorted(extras)[0]}",
            baseline_value="not_allowed_in_overlay",
            overlay_value=sorted(extras),
        )
    declared = overlay.get("agent_id")
    if declared and declared != agent_id:
        raise CompetitionBaselineLocked(
            field="agent_id",
            baseline_value=agent_id,
            overlay_value=declared,
        )


def _validate_locked_paths(baseline: dict[str, Any], overlay: dict[str, Any]) -> None:
    for locked in BASELINE_LOCKED_PATHS:
        for path, overlay_value in _iter_overlay_paths(overlay, locked):
            baseline_value = _lookup_path(baseline, path)
            if baseline_value is None and overlay_value is not None:
                raise CompetitionBaselineLocked(path, baseline_value, overlay_value)
            if baseline_value != overlay_value and overlay_value is not None:
                raise CompetitionBaselineLocked(path, baseline_value, overlay_value)


def _iter_overlay_paths(overlay: dict[str, Any], pattern: str) -> list[tuple[str, Any]]:
    """Yield concrete (path, value) pairs of `overlay` that match `pattern`.

    Pattern syntax supports `*` only in array positions, e.g. `accounts.*.cash`.
    """

    parts = pattern.split(".")
    return list(_walk_pattern(overlay, parts, []))


def _walk_pattern(node: Any, parts: list[str], trail: list[str]) -> list[tuple[str, Any]]:
    if not parts:
        return [(".".join(trail), node)] if node is not None else []
    head, *rest = parts
    if head == "*":
        if not isinstance(node, list):
            return []
        out: list[tuple[str, Any]] = []
        for item in node:
            identifier = ""
            if isinstance(item, dict):
                identifier = str(item.get("id") or item.get("name") or "")
            out.extend(_walk_pattern(item, rest, trail + [identifier or "*"]))
        return out
    if isinstance(node, dict) and head in node:
        return _walk_pattern(node[head], rest, trail + [head])
    return []


def _lookup_path(node: Any, path: str) -> Any:
    parts = path.split(".")
    cur = node
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            cur = next(
                (
                    item
                    for item in cur
                    if isinstance(item, dict)
                    and (str(item.get("id") or item.get("name") or "") == part)
                ),
                None,
            )
        else:
            return None
        if cur is None:
            return None
    return cur


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = deepcopy(base)
    for key, value in overlay.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = deepcopy(value)
    return out
