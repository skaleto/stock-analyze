"""Version-pinned model iteration lifecycle and artifact paths."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .research.classical_specs import mainline_horizon, mainline_specs
from .research.models import TRAINING_PROTOCOL_VERSION
from .utils import now_iso, read_json, write_json


REQUIRED_SHADOW_CYCLES = 12
SHADOW_ADMISSION_CONTRACT = "personal-quant-shadow-v1"
TRANSPARENT_RULE_RUNTIME_CONTRACT = "transparent-rule-shadow-v1"
_MARKET_PREFIX = {
    "a_share": "A",
    "cn_qdii_etf": "Q",
}
_STATUS_LABELS = {
    "research": "研究候选",
    "shadow": "模拟验证",
    "active": "正式使用",
    "rejected": "未通过验收",
    "superseded": "已被替代",
    "quarantined": "已隔离",
}


def model_registry_path(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
) -> Path:
    base = (
        Path(root)
        / "data"
        / "research"
        / "models"
        / str(market)
    )
    if account_scope:
        base = base / _path_token(account_scope)
    return base / str(int(horizon)) / "registry.json"


def shadow_cycles_path(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
) -> Path:
    return model_registry_path(
        root,
        market,
        horizon,
        account_scope=account_scope,
    ).with_name("shadow_cycles.json")


def usable_shadow_cycle_count(model_state: dict[str, Any]) -> int:
    """Return only prediction weeks backed by realized forward NAV evidence."""

    cycles = list(model_state.get("cycles") or [])
    explicit = model_state.get("usable_cycle_count")
    if explicit is not None:
        try:
            return min(len(cycles), max(0, int(explicit)))
        except (TypeError, ValueError):
            return 0
    forward_counts = []
    for cycle in cycles:
        metrics = (cycle or {}).get("metrics") or {}
        if metrics.get("forward_evidence_status") != "available":
            continue
        try:
            forward_counts.append(max(0, int(metrics.get("forward_cycles") or 0)))
        except (TypeError, ValueError):
            continue
    return min(len(cycles), max(forward_counts, default=0))


def iteration_state_path(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
) -> Path:
    base = (
        Path(root)
        / "data"
        / "model_iterations"
        / str(market)
    )
    if account_scope:
        base = base / _path_token(account_scope)
    return base / str(int(horizon)) / "iteration_state.json"


def iteration_prediction_dir(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    *,
    account_scope: str | None = None,
) -> Path:
    base = (
        Path(root)
        / "data"
        / "research"
        / "iteration_predictions"
        / str(market)
    )
    if account_scope:
        base = base / _path_token(account_scope)
    return base / str(int(horizon)) / _path_token(model_version)


def iteration_prediction_path(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    as_of: str | date,
    *,
    account_scope: str | None = None,
) -> Path:
    compact_date = str(as_of).replace("-", "")[:8]
    return iteration_prediction_dir(
        root,
        market,
        horizon,
        model_version,
        account_scope=account_scope,
    ) / f"{compact_date}.parquet"


def iteration_portfolio_dir(
    root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    *,
    account_scope: str | None = None,
) -> Path:
    base = (
        Path(root)
        / "data"
        / "model_iterations"
        / str(market)
    )
    if account_scope:
        base = base / _path_token(account_scope)
    return base / str(int(horizon)) / _path_token(model_version)


def lifecycle_label(status: str | None) -> str:
    return _STATUS_LABELS.get(str(status or ""), "未分类")


def read_model_registry(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
) -> dict[str, Any]:
    return read_json(
        model_registry_path(
            root,
            market,
            horizon,
            account_scope=account_scope,
        ),
        {"champion_model_version": None, "models": {}},
    )


def read_iteration_state(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
) -> dict[str, Any]:
    return read_json(
        iteration_state_path(
            root,
            market,
            horizon,
            account_scope=account_scope,
        ),
        {
            "schema_version": 1,
            "market": str(market),
            "account_scope": str(account_scope or ""),
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
    account_scope: str | None = None,
) -> dict[str, Any]:
    registry = registry or read_model_registry(
        root,
        market,
        horizon,
        account_scope=account_scope,
    )
    metadata = (registry.get("models") or {}).get(model_version) or {}
    cycle_state = read_json(
        shadow_cycles_path(
            root,
            market,
            horizon,
            account_scope=account_scope,
        ),
        {"models": {}},
    )
    model_cycles = (cycle_state.get("models") or {}).get(model_version) or {}
    cycle_count = usable_shadow_cycle_count(model_cycles)
    return {
        "market": str(market),
        "account_scope": str(account_scope or metadata.get("account_scope") or ""),
        "horizon": int(horizon),
        "model_version": str(model_version),
        "spec_id": str(metadata.get("spec_id") or ""),
        "spec_hash": str(metadata.get("spec_hash") or ""),
        "display_version": version_display_name(market, horizon, model_version, registry),
        "status": str(metadata.get("status") or "research"),
        "status_label": lifecycle_label(metadata.get("status") or "research"),
        "champion_model_version": registry.get("champion_model_version"),
        "shadow_cycles": cycle_count,
        "shadow_cycles_remaining": max(0, REQUIRED_SHADOW_CYCLES - cycle_count),
        "registered_at": metadata.get("registered_at"),
        "artifact": metadata.get("artifact"),
        "candidate_kind": metadata.get("candidate_kind"),
        "runtime_contract": metadata.get("runtime_contract"),
        "admission_grade": metadata.get("admission_grade"),
        "source_campaign": metadata.get("source_campaign"),
        "source_trial_id": metadata.get("source_trial_id"),
        "promotion_policy": metadata.get("promotion_policy"),
    }


def ensure_iteration_candidate(
    root: str | Path,
    market: str,
    horizon: int,
    *,
    account_scope: str | None = None,
    as_of: str | date | None = None,
) -> dict[str, Any] | None:
    """Return the current Challenger, pinning only versions already in Shadow."""

    registry = read_model_registry(
        root,
        market,
        horizon,
        account_scope=account_scope,
    )
    models = registry.get("models") or {}
    champion = str(registry.get("champion_model_version") or "")
    state = read_iteration_state(
        root,
        market,
        horizon,
        account_scope=account_scope,
    )
    current = state.get("current_candidate") or {}
    current_version = str(current.get("model_version") or "")
    stamp = str(as_of or now_iso())
    current_metadata = models.get(current_version) or {}
    current_status = str(current_metadata.get("status") or "")
    expected_spec_id = _expected_mainline_spec_id(
        market,
        horizon,
        account_scope=account_scope,
    )
    expected_spec_hash = (
        _expected_mainline_spec_hash(
            market,
            horizon,
            account_scope=account_scope,
        )
        if account_scope
        else ""
    )
    eligible_versions = _eligible_candidate_versions(
        models,
        expected_spec_id=expected_spec_id,
        expected_spec_hash=expected_spec_hash,
        require_current_protocol=bool(account_scope),
    )
    current_is_pinned = bool(
        current_version
        and current_version in eligible_versions
        and current_version != champion
        and current_status == "shadow"
    )
    selected_version = (
        current_version
        if current_is_pinned
        else _select_candidate_version(
            models,
            champion,
            expected_spec_id=expected_spec_id,
            expected_spec_hash=expected_spec_hash,
            require_current_protocol=bool(account_scope),
        )
    )

    if current_version and current_version != selected_version:
        history = state.setdefault("history", [])
        if current_version == champion or current_status == "active":
            outcome = "promoted"
        elif current_status in {"rejected", "superseded", "quarantined"}:
            outcome = current_status
        elif current_status == "research" and selected_version:
            outcome = "superseded"
        else:
            outcome = "retired"
        if not history or history[-1].get("model_version") != current_version or history[-1].get("outcome") != outcome:
            history.append({
                **current,
                "status": current_status or current.get("status"),
                "status_label": lifecycle_label(current_status or current.get("status")),
                "outcome": outcome,
                "ended_at": stamp,
            })
    current_version = selected_version

    state.update({
        "schema_version": 1,
        "market": str(market),
        "account_scope": str(account_scope or ""),
        "horizon": int(horizon),
        "updated_at": stamp,
    })
    if not current_version:
        state["current_candidate"] = None
        write_json(
            iteration_state_path(
                root,
                market,
                horizon,
                account_scope=account_scope,
            ),
            state,
        )
        return None

    summary = model_version_summary(
        root,
        market,
        horizon,
        current_version,
        registry=registry,
        account_scope=account_scope,
    )
    selected_at = current.get("selected_at") if current.get("model_version") == current_version else stamp
    state["current_candidate"] = {**summary, "selected_at": selected_at}
    write_json(
        iteration_state_path(
            root,
            market,
            horizon,
            account_scope=account_scope,
        ),
        state,
    )
    return summary


def _expected_mainline_spec_id(
    market: str,
    horizon: int,
    *,
    account_scope: str | None,
) -> str:
    try:
        if int(horizon) != mainline_horizon(market):
            return ""
        specs = mainline_specs(market, str(account_scope or ""))
    except ValueError:
        return ""
    return str(specs[0].spec_id) if len(specs) == 1 else ""


def _expected_mainline_spec_hash(
    market: str,
    horizon: int,
    *,
    account_scope: str | None,
) -> str:
    try:
        if int(horizon) != mainline_horizon(market):
            return ""
        specs = mainline_specs(market, str(account_scope or ""))
    except ValueError:
        return ""
    return str(specs[0].spec_hash) if len(specs) == 1 else ""


def _eligible_candidate_versions(
    models: dict[str, Any],
    *,
    expected_spec_id: str,
    expected_spec_hash: str = "",
    require_current_protocol: bool = False,
) -> set[str]:
    admitted_rules = {
        version
        for version, metadata in models.items()
        if str((metadata or {}).get("candidate_kind") or "")
        == "transparent_rule"
        and str((metadata or {}).get("runtime_contract") or "")
        == TRANSPARENT_RULE_RUNTIME_CONTRACT
        and str(
            (((metadata or {}).get("development_admission") or {}).get(
                "contract"
            ))
            or ""
        )
        == SHADOW_ADMISSION_CONTRACT
        and bool(str((metadata or {}).get("spec_id") or ""))
        and bool(str((metadata or {}).get("spec_hash") or ""))
    }
    if not expected_spec_id:
        return set(models)
    exact = {
        version
        for version, metadata in models.items()
        if str((metadata or {}).get("spec_id") or "") == expected_spec_id
        and (
            not expected_spec_hash
            or str((metadata or {}).get("spec_hash") or "")
            == expected_spec_hash
        )
        and (
            not require_current_protocol
            or str(((metadata or {}).get("metrics") or {}).get(
                "training_protocol_version"
            ) or "") == TRAINING_PROTOCOL_VERSION
        )
    }
    if exact or admitted_rules:
        return exact | admitted_rules
    if require_current_protocol:
        return set()
    return {
        version
        for version, metadata in models.items()
        if not str((metadata or {}).get("spec_id") or "")
    }


def _select_candidate_version(
    models: dict[str, Any],
    champion: str,
    *,
    expected_spec_id: str = "",
    expected_spec_hash: str = "",
    require_current_protocol: bool = False,
) -> str:
    insertion_order = {version: index for index, version in enumerate(models)}
    eligible_versions = _eligible_candidate_versions(
        models,
        expected_spec_id=expected_spec_id,
        expected_spec_hash=expected_spec_hash,
        require_current_protocol=require_current_protocol,
    )
    for status in ("shadow", "research"):
        candidates = [
            version
            for version, metadata in models.items()
            if version in eligible_versions
            and version != champion
            and str((metadata or {}).get("status") or "research") == status
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
