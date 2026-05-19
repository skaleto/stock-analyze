from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import competition
from .config import config_hash
from .monthly_review import default_month_for
from .proposal_judge import (
    DECISION_APPROVED,
    decision_path,
    proposal_path,
    validate_patch_for_apply,
)
from .utils import append_csv, ensure_dirs


EVOLUTION_FILE = "config_evolution.csv"
EVOLUTION_COLUMNS = [
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


def apply_approved_proposals(
    month: str | None = None,
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(repo_root) if repo_root else Path.cwd()
    target_month = month or default_month_for()
    target_agents = agents or competition.list_agents(root)
    results: list[dict[str, Any]] = []
    for agent_id in target_agents:
        decision_file = decision_path(agent_id, target_month, root)
        if not decision_file.exists():
            continue
        decision = _read_json_object(decision_file)
        if decision.get("decision") != DECISION_APPROVED:
            results.append(
                {
                    "agent_id": agent_id,
                    "month": target_month,
                    "status": "skipped",
                    "reason": decision.get("decision"),
                }
            )
            continue
        results.append(apply_decision(agent_id, target_month, root))
    return results


def apply_decision(agent_id: str, month: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    decision_file = decision_path(agent_id, month, root)
    decision = _read_json_object(decision_file)
    if decision.get("decision") != DECISION_APPROVED:
        raise ValueError(f"decision_not_approved:{decision.get('decision')}")

    proposal_file = proposal_path(agent_id, month, root)
    proposal = _read_json_object(proposal_file) if proposal_file.exists() else {}
    patch = decision.get("patch")
    if patch is None:
        patch = proposal.get("patch") or {}
    if not isinstance(patch, dict):
        raise ValueError("patch_must_be_object")

    current_overlay = _read_json_object(paths.config_path)
    from_hash = config_hash(competition.load(agent_id, repo_root=root))
    history_path = _history_path(root, from_hash)
    ensure_dirs(history_path.parent)
    if not history_path.exists():
        history_path.write_text(json.dumps(current_overlay, ensure_ascii=False, indent=2), encoding="utf-8")

    if _already_applied(paths.data_dir, str(decision_file.relative_to(root)), from_hash):
        return {"agent_id": agent_id, "month": month, "status": "already_applied", "from_hash": from_hash}

    if patch:
        validate_patch_for_apply(agent_id, patch, repo_root=root)
        next_overlay = _deep_merge(current_overlay, patch)
    else:
        next_overlay = current_overlay

    old_text = paths.config_path.read_text(encoding="utf-8")
    try:
        paths.config_path.write_text(
            json.dumps(next_overlay, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        to_hash = config_hash(competition.load(agent_id, repo_root=root))
    except Exception:
        paths.config_path.write_text(old_text, encoding="utf-8")
        raise

    _append_evolution(
        paths.data_dir,
        {
            "event": "apply",
            "event_at": datetime.now().isoformat(timespec="seconds"),
            "agent_id": agent_id,
            "month": month,
            "source_proposal": _relative_or_str(proposal_file, root),
            "decision_path": _relative_or_str(decision_file, root),
            "from_hash": from_hash,
            "to_hash": to_hash,
            "patch_paths": ",".join(decision.get("patch_paths") or _flatten_patch_paths(patch)),
            "reviewer": decision.get("reviewer") or "",
        },
    )
    return {
        "agent_id": agent_id,
        "month": month,
        "status": "applied",
        "from_hash": from_hash,
        "to_hash": to_hash,
        "history_path": str(history_path),
    }


def rollback_agent(agent_id: str, to_hash: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    history_path = _history_path(root, to_hash)
    if not history_path.exists():
        raise FileNotFoundError(f"history_not_found:{to_hash}")
    current_hash = config_hash(competition.load(agent_id, repo_root=root))
    old_text = paths.config_path.read_text(encoding="utf-8")
    restored_text = history_path.read_text(encoding="utf-8")
    try:
        paths.config_path.write_text(
            restored_text if restored_text.endswith("\n") else restored_text + "\n",
            encoding="utf-8",
        )
        restored_hash = config_hash(competition.load(agent_id, repo_root=root))
    except Exception:
        paths.config_path.write_text(old_text, encoding="utf-8")
        raise
    _append_evolution(
        paths.data_dir,
        {
            "event": "rollback",
            "event_at": datetime.now().isoformat(timespec="seconds"),
            "agent_id": agent_id,
            "month": "",
            "source_proposal": "",
            "decision_path": "",
            "from_hash": current_hash,
            "to_hash": restored_hash,
            "patch_paths": "",
            "reviewer": "operator",
        },
    )
    return {
        "agent_id": agent_id,
        "status": "rolled_back",
        "from_hash": current_hash,
        "to_hash": restored_hash,
    }


def _history_path(root: Path, digest: str) -> Path:
    return root / "configs" / "agents" / "_history" / f"{digest}.yaml"


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _append_evolution(data_dir: Path, row: dict[str, Any]) -> None:
    append_csv(data_dir / EVOLUTION_FILE, [row], EVOLUTION_COLUMNS)


def _already_applied(data_dir: Path, decision_rel: str, from_hash: str) -> bool:
    path = data_dir / EVOLUTION_FILE
    if not path.exists():
        return False
    import csv

    with path.open("r", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if (
                row.get("event") == "apply"
                and row.get("decision_path") == decision_rel
                and row.get("from_hash") == from_hash
            ):
                return True
    return False


def _flatten_patch_paths(patch: Any, prefix: str = "") -> list[str]:
    if not isinstance(patch, dict):
        return [prefix] if prefix else []
    paths: list[str] = []
    for key, value in patch.items():
        child = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            paths.extend(_flatten_patch_paths(value, child))
        else:
            paths.append(child)
    return paths


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
