"""Apply an audited, idempotent multi-market strategy release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import competition, overlay_guard
from .evolution_writer import write_evolution
from .strategy_registry import (
    load_strategy_registry,
    validate_strategy_pair,
)


class StrategyReleaseInvalid(ValueError):
    """The release manifest cannot be safely applied."""


def _read_json(path: Path, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyReleaseInvalid(f"strategy_release_unreadable:{source}:{path}") from exc
    if not isinstance(payload, dict):
        raise StrategyReleaseInvalid(f"strategy_release_invalid:{source}:{path}")
    return payload


def apply_strategy_release(
    manifest_path: str | Path,
    repo_root: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = _read_json(manifest_path, "manifest")
    release_id = str(manifest.get("release_id") or "")
    month = str(manifest.get("month") or "")
    reviewer = str(manifest.get("reviewer") or "")
    entries = manifest.get("entries")
    if not release_id or not month or not reviewer or not isinstance(entries, list):
        raise StrategyReleaseInvalid("strategy_release_manifest_fields")

    prepared: list[dict[str, Any]] = []
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise StrategyReleaseInvalid("strategy_release_entry_type")
        market = str(raw.get("market") or "")
        agent_id = str(raw.get("agent_id") or "")
        overlay_name = str(raw.get("overlay") or "")
        reasoning = str(raw.get("reasoning") or "")
        if market not in competition.MARKETS or not agent_id or not overlay_name or not reasoning:
            raise StrategyReleaseInvalid("strategy_release_entry_fields")
        identity = (market, agent_id)
        if identity in seen:
            raise StrategyReleaseInvalid(f"strategy_release_duplicate:{market}:{agent_id}")
        seen.add(identity)
        desired_path = manifest_path.parent / overlay_name
        desired = _read_json(desired_path, "overlay")
        overlay_guard.validate(
            agent_id,
            desired,
            repo_root=root,
            market=market,
        )
        live_path = competition.resolve_market_paths(market, agent_id, root).config_path
        current = _read_json(live_path, "live_overlay")
        prepared.append(
            {
                "market": market,
                "agent_id": agent_id,
                "reasoning": reasoning,
                "current": current,
                "desired": desired,
            }
        )
        pairs.setdefault(market, {})[agent_id] = desired

    registry = load_strategy_registry(root)
    floor = float(registry["factor_distance_floor"])
    pair_results = {
        market: validate_strategy_pair(overlays, factor_distance_floor=floor)
        for market, overlays in pairs.items()
    }

    results: list[dict[str, Any]] = []
    for entry in prepared:
        market = entry["market"]
        agent_id = entry["agent_id"]
        if entry["current"] == entry["desired"]:
            results.append(
                {"market": market, "agent_id": agent_id, "status": "unchanged"}
            )
            continue
        if dry_run:
            results.append(
                {"market": market, "agent_id": agent_id, "status": "would_evolve"}
            )
            continue
        result = write_evolution(
            agent_id,
            entry["current"],
            entry["desired"],
            entry["reasoning"],
            repo_root=root,
            month=month,
            reviewer=reviewer,
            market=market,
        )
        results.append(
            {
                "market": market,
                "agent_id": agent_id,
                "status": result["status"],
                "from_hash": result["from_hash"],
                "to_hash": result["to_hash"],
            }
        )
    return {
        "release_id": release_id,
        "month": month,
        "dry_run": dry_run,
        "pairs": pair_results,
        "entries": results,
    }


__all__ = ["StrategyReleaseInvalid", "apply_strategy_release"]
