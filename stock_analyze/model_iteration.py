"""Version-pinned model iteration lifecycle and artifact paths."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .utils import now_iso, read_json, write_json


REQUIRED_SHADOW_CYCLES = 12
_MARKET_PREFIX = {
    "a_share": "A",
    "cn_qdii_etf": "Q",
}
_STATUS_LABELS = {
    "research": "研究候选",
    "shadow": "模拟验证",
    "active": "正式使用",
}


def model_registry_path(root: str | Path, market: str, horizon: int) -> Path:
    return (
        Path(root)
        / "data"
        / "research"
        / "models"
        / str(market)
        / str(int(horizon))
        / "registry.json"
    )


def shadow_cycles_path(root: str | Path, market: str, horizon: int) -> Path:
    return model_registry_path(root, market, horizon).with_name("shadow_cycles.json")


def iteration_state_path(root: str | Path, market: str, horizon: int) -> Path:
    return (
        Path(root)
        / "data"
        / "model_iterations"
        / str(market)
        / str(int(horizon))
        / "iteration_state.json"
    )


def iteration_prediction_dir(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
) -> Path:
    return (
        Path(root)
        / "data"
        / "research"
        / "iteration_predictions"
        / str(market)
        / str(int(horizon))
        / _path_token(model_version)
    )


def iteration_prediction_path(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    as_of: str | date,
) -> Path:
    compact_date = str(as_of).replace("-", "")[:8]
    return iteration_prediction_dir(root, market, horizon, model_version) / f"{compact_date}.parquet"


def iteration_portfolio_dir(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
) -> Path:
    return (
        Path(root)
        / "data"
        / "model_iterations"
        / str(market)
        / str(int(horizon))
        / _path_token(model_version)
    )


def lifecycle_label(status: str | None) -> str:
    return _STATUS_LABELS.get(str(status or ""), "未分类")


def read_model_registry(root: str | Path, market: str, horizon: int) -> dict[str, Any]:
    return read_json(
        model_registry_path(root, market, horizon),
        {"champion_model_version": None, "models": {}},
    )


def read_iteration_state(root: str | Path, market: str, horizon: int) -> dict[str, Any]:
    return read_json(
        iteration_state_path(root, market, horizon),
        {
            "schema_version": 1,
            "market": str(market),
            "horizon": int(horizon),
            "current_candidate": None,
            "history": [],
            "updated_at": None,
        },
    )


def version_display_name(
    market: str,
    horizon: int,
    model_version: str,
    registry: dict[str, Any],
) -> str:
    models = registry.get("models") or {}
    insertion_order = {version: index for index, version in enumerate(models)}
    ordered = sorted(
        models,
        key=lambda version: (
            str((models.get(version) or {}).get("registered_at") or ""),
            insertion_order[version],
        ),
    )
    try:
        sequence = ordered.index(model_version) + 1
    except ValueError:
        sequence = len(ordered) + 1
    prefix = _MARKET_PREFIX.get(str(market), str(market)[:1].upper() or "M")
    return f"{prefix}{int(horizon)}-V{sequence:03d}"


def model_version_summary(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or read_model_registry(root, market, horizon)
    metadata = (registry.get("models") or {}).get(model_version) or {}
    cycle_state = read_json(shadow_cycles_path(root, market, horizon), {"models": {}})
    cycles = ((cycle_state.get("models") or {}).get(model_version) or {}).get("cycles") or []
    cycle_count = len(cycles)
    return {
        "market": str(market),
        "horizon": int(horizon),
        "model_version": str(model_version),
        "display_version": version_display_name(market, horizon, model_version, registry),
        "status": str(metadata.get("status") or "research"),
        "status_label": lifecycle_label(metadata.get("status") or "research"),
        "champion_model_version": registry.get("champion_model_version"),
        "shadow_cycles": cycle_count,
        "shadow_cycles_remaining": max(0, REQUIRED_SHADOW_CYCLES - cycle_count),
        "registered_at": metadata.get("registered_at"),
        "artifact": metadata.get("artifact"),
    }


def ensure_iteration_candidate(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    as_of: str | date | None = None,
) -> dict[str, Any] | None:
    """Return one stable Challenger, rotating only after promotion or removal."""

    registry = read_model_registry(root, market, horizon)
    models = registry.get("models") or {}
    champion = str(registry.get("champion_model_version") or "")
    state = read_iteration_state(root, market, horizon)
    current = state.get("current_candidate") or {}
    current_version = str(current.get("model_version") or "")
    stamp = str(as_of or now_iso())
    current_metadata = models.get(current_version) or {}
    current_status = str(current_metadata.get("status") or "")
    current_is_candidate = bool(
        current_version
        and current_version in models
        and current_version != champion
        and current_status != "active"
    )

    if current_version and not current_is_candidate:
        history = state.setdefault("history", [])
        outcome = "promoted" if current_version == champion or current_status == "active" else "retired"
        if not history or history[-1].get("model_version") != current_version or history[-1].get("outcome") != outcome:
            history.append({
                **current,
                "status": current_status or current.get("status"),
                "status_label": lifecycle_label(current_status or current.get("status")),
                "outcome": outcome,
                "ended_at": stamp,
            })
        current_version = ""

    if not current_version:
        current_version = _select_candidate_version(models, champion)

    state.update({
        "schema_version": 1,
        "market": str(market),
        "horizon": int(horizon),
        "updated_at": stamp,
    })
    if not current_version:
        state["current_candidate"] = None
        write_json(iteration_state_path(root, market, horizon), state)
        return None

    summary = model_version_summary(
        root,
        market,
        horizon,
        current_version,
        registry=registry,
    )
    selected_at = current.get("selected_at") if current.get("model_version") == current_version else stamp
    state["current_candidate"] = {**summary, "selected_at": selected_at}
    write_json(iteration_state_path(root, market, horizon), state)
    return summary


def _select_candidate_version(models: dict[str, Any], champion: str) -> str:
    insertion_order = {version: index for index, version in enumerate(models)}
    for status in ("shadow", "research"):
        candidates = [
            version
            for version, metadata in models.items()
            if version != champion and str((metadata or {}).get("status") or "research") == status
        ]
        if candidates:
            return max(
                candidates,
                key=lambda version: (
                    str((models.get(version) or {}).get("registered_at") or ""),
                    insertion_order[version],
                ),
            )
    return ""


def _path_token(value: str) -> str:
    token = str(value).strip()
    if not token or token in {".", ".."} or "/" in token or "\\" in token:
        raise ValueError("invalid_model_version")
    return token
