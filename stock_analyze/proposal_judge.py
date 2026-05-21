from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import competition
from .config import config_hash
from .monthly_review import default_month_for
from .utils import safe_float, write_json


DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_NEEDS_HUMAN = "needs_human"

MAX_FACTOR_WEIGHT = 0.35
MAX_FACTOR_WEIGHT_DELTA = 0.05
MAX_PATCH_PATHS = 6
MAX_INDUSTRY_WEIGHT = 0.35
MIN_RATIONALE_CHARS = 20

ALLOWED_PATCH_TOP_LEVEL = frozenset(
    {"factors", "factor_processing", "portfolio_controls", "filters"}
)
KNOWN_FACTORS = frozenset(
    {
        "pe",
        "pb",
        "roe",
        "gross_margin",
        "debt_ratio",
        "net_profit_growth",
        "momentum_20",
        "momentum_60",
        "low_volatility_60",
        "dividend_yield",
    }
)


def proposal_path(agent_id: str, month: str, repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root) if repo_root else Path.cwd()
    return root / "data" / agent_id / "proposals" / f"{month}-strategy.json"


def decision_path(agent_id: str, month: str, repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root) if repo_root else Path.cwd()
    return root / "data" / "competition" / "decisions" / f"{month}-{agent_id}.json"


def judge_all(
    month: str | None = None,
    agents: list[str] | None = None,
    repo_root: str | Path | None = None,
    reviewer: str = "referee",
) -> list[dict[str, Any]]:
    root = Path(repo_root) if repo_root else Path.cwd()
    target_month = month or default_month_for()
    target_agents = agents or competition.list_agents(root)
    results = []
    for agent_id in target_agents:
        proposal = proposal_path(agent_id, target_month, root)
        if not proposal.exists():
            continue
        results.append(judge_proposal(agent_id, target_month, root, reviewer=reviewer))
    return results


def judge_proposal(
    agent_id: str,
    month: str,
    repo_root: str | Path | None = None,
    reviewer: str = "referee",
    persist: bool = True,
) -> dict[str, Any]:
    """Judge one monthly strategy proposal with deterministic guardrails.

    The judge deliberately avoids predicting returns. It only decides whether
    the proposed config patch is small, legal, auditable, and data-backed enough
    to be auto-applied. Ambiguous proposals become `needs_human`.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    proposal_file = proposal_path(agent_id, month, root)
    reasons: list[str] = []
    warnings: list[str] = []
    violations: list[str] = []

    try:
        proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        proposal = {}
        violations.append(f"proposal_missing:{proposal_file}")
    except json.JSONDecodeError as exc:
        proposal = {}
        violations.append(f"proposal_invalid_json:{exc.msg}")

    current_config = competition.load(agent_id, repo_root=root)
    current_hash = config_hash(current_config)
    overlay = _read_json_mapping(paths.config_path)
    patch = proposal.get("patch") if isinstance(proposal, dict) else None
    no_change = bool(proposal.get("no_change")) if isinstance(proposal, dict) else False

    if not isinstance(proposal, dict):
        violations.append("proposal_must_be_object")
        patch = {}
    if patch is None:
        patch = {}
    if not isinstance(patch, dict):
        violations.append("patch_must_be_object")
        patch = {}

    if str(proposal.get("agent_id") or agent_id) != agent_id:
        violations.append("agent_id_mismatch")

    if no_change and patch:
        warnings.append("no_change_with_non_empty_patch")

    _validate_proposal_text(proposal, reasons, warnings, violations, no_change=no_change)
    _validate_patch_shape(patch, violations)
    patch_paths = _flatten_patch_paths(patch)
    if len(patch_paths) > MAX_PATCH_PATHS:
        warnings.append(f"patch_too_broad:{len(patch_paths)}>{MAX_PATCH_PATHS}")

    based_on = str(proposal.get("based_on_config_hash") or "").strip()
    if based_on and based_on != current_hash:
        warnings.append(f"config_hash_mismatch:proposal={based_on}:current={current_hash}")

    if not _monthly_review_exists(root, month):
        warnings.append(f"monthly_review_missing:{month}")

    merged_overlay = _deep_merge(overlay, patch)
    _validate_factor_changes(overlay, merged_overlay, patch, reasons, warnings, violations)
    _validate_portfolio_changes(overlay, merged_overlay, patch, reasons, warnings, violations)

    try:
        _validate_merged_overlay(agent_id, merged_overlay, root)
    except Exception as exc:  # noqa: BLE001
        violations.append(f"merged_overlay_invalid:{exc}")

    if violations:
        decision = DECISION_REJECTED
        risk_level = "high"
        confidence = 0.95
    elif warnings:
        decision = DECISION_NEEDS_HUMAN
        risk_level = "medium"
        confidence = 0.65
    else:
        decision = DECISION_APPROVED
        risk_level = "low"
        confidence = 0.8
        if no_change:
            reasons.append("proposal_no_change")
        else:
            reasons.append("patch_is_small_and_within_guardrails")

    payload = {
        "schema_version": 1,
        "month": month,
        "agent_id": agent_id,
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "risk_level": risk_level,
        "confidence": confidence,
        "required_human_attention": decision != DECISION_APPROVED,
        "reasons": reasons,
        "warnings": warnings,
        "violations": violations,
        "patch_paths": patch_paths,
        "patch": patch,
        "proposal_path": _relative_or_str(proposal_file, root),
        "proposal_hash": _hash_mapping(proposal),
        "current_config_hash": current_hash,
    }
    if persist:
        write_json(decision_path(agent_id, month, root), payload)
    return payload


def validate_patch_for_apply(
    agent_id: str,
    patch: dict[str, Any],
    repo_root: str | Path | None = None,
) -> None:
    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    overlay = _read_json_mapping(paths.config_path)
    violations: list[str] = []
    _validate_patch_shape(patch, violations)
    if violations:
        raise ValueError("; ".join(violations))
    merged = _deep_merge(overlay, patch)
    _validate_merged_overlay(agent_id, merged, root)


def _validate_proposal_text(
    proposal: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    violations: list[str],
    *,
    no_change: bool,
) -> None:
    rationale = str(proposal.get("rationale") or "").strip()
    expected = str(proposal.get("expected_effect") or "").strip()
    risks = proposal.get("risks")
    if no_change:
        if rationale:
            reasons.append("no_change_has_rationale")
        return
    if len(rationale) < MIN_RATIONALE_CHARS:
        warnings.append("rationale_too_short")
    else:
        reasons.append("rationale_present")
    if not expected:
        warnings.append("expected_effect_missing")
    if not isinstance(risks, list) or not risks:
        warnings.append("risks_missing")
    if proposal.get("no_change") is None:
        violations.append("no_change_missing")


def _validate_patch_shape(patch: dict[str, Any], violations: list[str]) -> None:
    for key in patch:
        if key not in ALLOWED_PATCH_TOP_LEVEL:
            violations.append(f"patch_top_level_not_allowed:{key}")


def _validate_factor_changes(
    old_overlay: dict[str, Any],
    new_overlay: dict[str, Any],
    patch: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    violations: list[str],
) -> None:
    factor_patch = patch.get("factors")
    if factor_patch is None:
        return
    if not isinstance(factor_patch, dict):
        violations.append("factors_patch_must_be_object")
        return
    old_factors = old_overlay.get("factors") or {}
    new_factors = new_overlay.get("factors") or {}
    for factor, spec in factor_patch.items():
        if factor not in KNOWN_FACTORS:
            violations.append(f"unknown_factor:{factor}")
            continue
        if not isinstance(spec, dict):
            violations.append(f"factor_spec_must_be_object:{factor}")
            continue
        old_spec = old_factors.get(factor) or {}
        new_spec = new_factors.get(factor) or {}
        if (
            factor in old_factors
            and "direction" in spec
            and spec.get("direction") != old_spec.get("direction")
        ):
            warnings.append(f"factor_direction_changed:{factor}")
        if "weight" in spec:
            old_weight = safe_float(old_spec.get("weight")) or 0.0
            new_weight = safe_float(new_spec.get("weight"))
            if new_weight is None or new_weight < 0:
                violations.append(f"invalid_factor_weight:{factor}")
                continue
            delta = abs(new_weight - old_weight)
            if delta > MAX_FACTOR_WEIGHT_DELTA + 1e-9:
                warnings.append(f"factor_weight_delta_too_large:{factor}:{delta:.4f}")
            if new_weight > MAX_FACTOR_WEIGHT + 1e-9:
                warnings.append(f"factor_weight_too_high:{factor}:{new_weight:.4f}")
            reasons.append(f"factor_weight_checked:{factor}")
    total = sum(
        max(safe_float(spec.get("weight")) or 0.0, 0.0)
        for spec in new_factors.values()
        if isinstance(spec, dict)
    )
    if total < 0.95 or total > 1.05:
        warnings.append(f"factor_weight_sum_out_of_range:{total:.4f}")


def _validate_portfolio_changes(
    old_overlay: dict[str, Any],
    new_overlay: dict[str, Any],
    patch: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
    violations: list[str],
) -> None:
    pc_patch = patch.get("portfolio_controls")
    if pc_patch is None:
        return
    if not isinstance(pc_patch, dict):
        violations.append("portfolio_controls_patch_must_be_object")
        return
    new_pc = new_overlay.get("portfolio_controls") or {}
    old_pc = old_overlay.get("portfolio_controls") or {}
    if "max_industry_weight" in pc_patch:
        value = safe_float(new_pc.get("max_industry_weight"))
        old_value = safe_float(old_pc.get("max_industry_weight"))
        if value is None:
            violations.append("invalid_max_industry_weight")
        elif value > MAX_INDUSTRY_WEIGHT + 1e-9:
            warnings.append(f"max_industry_weight_too_high:{value:.4f}")
        elif (
            old_value is not None
            and abs(value - old_value) > MAX_FACTOR_WEIGHT_DELTA + 1e-9
        ):
            warnings.append(f"max_industry_weight_delta_too_large:{abs(value - old_value):.4f}")
        else:
            reasons.append("max_industry_weight_checked")


def _validate_merged_overlay(agent_id: str, overlay: dict[str, Any], root: Path) -> None:
    """Validate the merged overlay in memory.

    Uses :func:`competition.validate_overlay`, which performs the same checks
    as `competition.load` but never writes to disk. The previous
    write-then-load-then-restore pattern introduced a file-level TOCTOU race
    that any concurrent ``competition.load`` (e.g. a manual ``run-weekly``
    invocation) could observe.
    """

    competition.validate_overlay(agent_id, overlay, repo_root=root)


def _monthly_review_exists(root: Path, month: str) -> bool:
    return (root / "data" / "competition" / "monthly_reviews" / f"{month}.json").exists()


def _read_json_mapping(path: Path) -> dict[str, Any]:
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


def _flatten_patch_paths(patch: Any, prefix: str = "") -> list[str]:
    if not isinstance(patch, dict):
        return [prefix] if prefix else []
    if not patch:
        return []
    paths: list[str] = []
    for key, value in patch.items():
        child = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            paths.extend(_flatten_patch_paths(value, child))
        else:
            paths.append(child)
    return paths


def _hash_mapping(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
