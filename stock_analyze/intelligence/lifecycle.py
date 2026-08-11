"""Evidence-gated lifecycle state for intelligence factors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


VALID_STATES = {"observing", "research", "model_iteration", "active", "rejected"}
PROMOTED_STATES = {"model_iteration", "active"}


def load_factor_records(path: str | Path) -> dict[str, dict]:
    """Load declared and effective states.

    Legacy scalar declarations remain readable for the Dashboard, but promoted
    states are fail-closed unless they carry a qualified, immutable evidence
    reference. This prevents a hand-edited state string from silently entering
    model training.
    """

    config_path = Path(path)
    if not config_path.exists():
        return {}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    repo_root = (
        config_path.parent.parent
        if config_path.parent.name == "configs"
        else config_path.parent
    ).resolve()
    records: dict[str, dict] = {}
    for raw_name, raw_record in (payload.get("factors") or {}).items():
        name = str(raw_name)
        if isinstance(raw_record, str):
            declared_state = raw_record
            evidence: dict = {}
        elif isinstance(raw_record, dict):
            declared_state = str(raw_record.get("state") or "observing")
            evidence = dict(raw_record.get("evidence") or {})
        else:
            raise ValueError(f"intelligence_factor_record_invalid:{name}")
        if declared_state not in VALID_STATES:
            raise ValueError(
                f"intelligence_factor_state_invalid:{{'{name}': '{declared_state}'}}"
            )
        report_path_value = str(evidence.get("report_path") or "").strip()
        report_hash_value = str(evidence.get("report_hash") or "").strip()
        report_path = (
            (repo_root / report_path_value).resolve()
            if report_path_value
            else None
        )
        report_hash_matches = False
        if (
            report_path is not None
            and report_path.is_relative_to(repo_root)
            and report_path.is_file()
            and len(report_hash_value) == 64
        ):
            report_hash_matches = (
                hashlib.sha256(report_path.read_bytes()).hexdigest()
                == report_hash_value.casefold()
            )
        evidence_qualified = (
            str(evidence.get("status") or "") == "qualified"
            and report_hash_matches
        )
        effective_state = (
            "observing"
            if declared_state in PROMOTED_STATES and not evidence_qualified
            else declared_state
        )
        records[name] = {
            "declared_state": declared_state,
            "effective_state": effective_state,
            "evidence": evidence,
            "evidence_qualified": evidence_qualified,
        }
    return records


def load_factor_states(path: str | Path) -> dict[str, str]:
    return {
        name: str(record["effective_state"])
        for name, record in load_factor_records(path).items()
    }


def model_iteration_features(path: str | Path) -> set[str]:
    return {
        name for name, state in load_factor_states(path).items()
        if state in {"model_iteration", "active"}
    }


def active_features(path: str | Path) -> set[str]:
    return {name for name, state in load_factor_states(path).items() if state == "active"}


__all__ = [
    "VALID_STATES",
    "active_features",
    "load_factor_records",
    "load_factor_states",
    "model_iteration_features",
]
