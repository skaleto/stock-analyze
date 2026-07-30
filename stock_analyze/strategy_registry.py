"""Product-facing strategy season registry and pair-divergence guard."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from . import competition


REGISTRY_PATH = Path("configs/strategy_competition.json")
PAIR_SLOTS = ("claude", "codex")


class StrategyRegistryInvalid(ValueError):
    """The strategy season registry is missing or malformed."""


class StrategyPairInvalid(ValueError):
    """The two configured strategy slots are not sufficiently distinct."""


def load_strategy_registry(
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    path = root / REGISTRY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyRegistryInvalid(f"strategy_registry_unreadable:{path}") from exc
    required = {"season_id", "name", "effective_date", "factor_distance_floor", "slots"}
    if not required.issubset(payload):
        missing = sorted(required - set(payload))
        raise StrategyRegistryInvalid(f"strategy_registry_missing:{','.join(missing)}")
    slots = payload.get("slots")
    if not isinstance(slots, dict) or any(slot not in slots for slot in PAIR_SLOTS):
        raise StrategyRegistryInvalid("strategy_registry_slots:claude,codex required")
    return payload


def strategy_slot(
    agent_id: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_strategy_registry(repo_root)
    slot = registry["slots"].get(agent_id)
    if not isinstance(slot, dict):
        return {
            "label": agent_id,
            "description": "",
            "color": "#8391a3",
        }
    return dict(slot)


def strategy_display_name(
    agent_id: str,
    repo_root: str | Path | None = None,
) -> str:
    return str(strategy_slot(agent_id, repo_root).get("label") or agent_id)


def _factor_weights(overlay: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for key, raw in (overlay.get("factors") or {}).items():
        value = raw.get("weight") if isinstance(raw, dict) else raw
        try:
            weight = float(value)
        except (TypeError, ValueError) as exc:
            raise StrategyPairInvalid(f"strategy_pair_weight_invalid:{key}") from exc
        if not math.isfinite(weight) or weight < 0:
            raise StrategyPairInvalid(f"strategy_pair_weight_invalid:{key}")
        weights[str(key)] = weight
    return weights


def factor_weight_distance(
    left: dict[str, Any],
    right: dict[str, Any],
) -> float:
    """Return total variation distance between normalized factor weights."""

    left_weights = _factor_weights(left)
    right_weights = _factor_weights(right)
    left_total = sum(left_weights.values())
    right_total = sum(right_weights.values())
    if left_total <= 0 or right_total <= 0:
        raise StrategyPairInvalid("strategy_pair_weight_sum:positive totals required")
    keys = set(left_weights) | set(right_weights)
    return 0.5 * sum(
        abs(left_weights.get(key, 0.0) / left_total - right_weights.get(key, 0.0) / right_total)
        for key in keys
    )


def validate_strategy_pair(
    overlays: dict[str, dict[str, Any]],
    *,
    factor_distance_floor: float,
) -> dict[str, Any]:
    if any(slot not in overlays for slot in PAIR_SLOTS):
        raise StrategyPairInvalid("strategy_pair_slots:claude,codex required")
    strategy_ids: dict[str, str] = {}
    names: dict[str, str] = {}
    weight_sums: dict[str, float] = {}
    for slot in PAIR_SLOTS:
        overlay = overlays[slot]
        if overlay.get("agent_id") != slot:
            raise StrategyPairInvalid(f"strategy_pair_agent_id:{slot}")
        strategy_id = str(overlay.get("strategy_id") or "")
        name = str(overlay.get("name") or "")
        if not strategy_id or not name:
            raise StrategyPairInvalid(f"strategy_pair_identity:{slot}")
        weight_sum = sum(_factor_weights(overlay).values())
        if not math.isclose(weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise StrategyPairInvalid(
                f"strategy_pair_weight_sum:{slot}={weight_sum:.8f}"
            )
        strategy_ids[slot] = strategy_id
        names[slot] = name
        weight_sums[slot] = weight_sum
    if len(set(strategy_ids.values())) != len(PAIR_SLOTS):
        raise StrategyPairInvalid("strategy_pair_strategy_id:must differ")
    if len(set(names.values())) != len(PAIR_SLOTS):
        raise StrategyPairInvalid("strategy_pair_name:must differ")
    distance = factor_weight_distance(overlays[PAIR_SLOTS[0]], overlays[PAIR_SLOTS[1]])
    if distance + 1e-12 < factor_distance_floor:
        raise StrategyPairInvalid(
            f"strategy_pair_factor_distance:{distance:.4f}<{factor_distance_floor:.4f}"
        )
    return {
        "status": "valid",
        "strategy_ids": strategy_ids,
        "names": names,
        "weight_sums": weight_sums,
        "factor_distance": distance,
        "factor_distance_floor": factor_distance_floor,
    }


def validate_market_strategy_pair(
    market: str,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    registry = load_strategy_registry(root)
    overlays: dict[str, dict[str, Any]] = {}
    for agent_id in PAIR_SLOTS:
        path = competition.resolve_market_paths(market, agent_id, root).config_path
        try:
            overlays[agent_id] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StrategyPairInvalid(f"strategy_pair_overlay_unreadable:{path}") from exc
    return validate_strategy_pair(
        overlays,
        factor_distance_floor=float(registry["factor_distance_floor"]),
    )


__all__ = [
    "PAIR_SLOTS",
    "StrategyPairInvalid",
    "StrategyRegistryInvalid",
    "factor_weight_distance",
    "load_strategy_registry",
    "strategy_display_name",
    "strategy_slot",
    "validate_market_strategy_pair",
    "validate_strategy_pair",
]
