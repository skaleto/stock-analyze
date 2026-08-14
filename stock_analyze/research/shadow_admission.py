"""Auditable Shadow admission for sealed transparent strategy campaigns."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ..model_iteration import (
    SHADOW_ADMISSION_CONTRACT,
    TRANSPARENT_RULE_RUNTIME_CONTRACT,
    model_registry_path,
)
from ..utils import now_iso, write_json
from .activation import ModelRegistry
from .classical_specs import transparent_strategy_specs


RULE_RUNTIME_CONTRACT = TRANSPARENT_RULE_RUNTIME_CONTRACT
RULE_PROMOTION_POLICY = "strict-forward-review-v1"
_MARKET_ORDER = ("a_share", "cn_qdii_etf")


def _number(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def evaluate_transparent_shadow_trial(trial: Mapping[str, Any]) -> dict[str, Any]:
    """Separate executable Shadow safety from proof required for Active."""

    metrics = trial.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    stress = trial.get("cost_stress")
    stress = stress if isinstance(stress, Mapping) else {}
    folds = [
        dict(item)
        for item in (trial.get("folds") or [])
        if isinstance(item, Mapping)
    ]
    gate_zero = trial.get("gate_zero")
    gate_zero = gate_zero if isinstance(gate_zero, Mapping) else {}
    checks = {
        "point_in_time_audit": trial.get("point_in_time_audit") is True,
        "gate_zero": gate_zero.get("passed") is True,
        "walk_forward_folds": len(folds) == 3,
        "all_folds_traded": (
            len(folds) == 3
            and all(int(_number(item.get("trade_count"), 0.0)) > 0 for item in folds)
        ),
        "attribution_status": str(metrics.get("attribution_status") or "") == "reconciled",
        "positive_net_return": _number(metrics.get("net_return"), -1.0) > 0.0,
        "maximum_drawdown": _number(metrics.get("max_drawdown"), 1.0) <= 0.25,
        "target_fill_ratio": _number(metrics.get("target_fill_ratio"), -1.0) >= 0.95,
        "missing_liquidity_notional_ratio": (
            _number(metrics.get("missing_liquidity_notional_ratio"), 1.0) <= 0.10
        ),
        "impact_capped_notional_ratio": (
            _number(metrics.get("impact_capped_notional_ratio"), 1.0) <= 0.10
        ),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    positive_folds = sum(
        _number(item.get("net_excess_return"), -1.0) > 0.0
        for item in folds
    )
    promising = bool(
        not reasons
        and _number(metrics.get("net_excess_return"), -1.0) > 0.0
        and _number(stress.get("net_excess_return"), -1.0) >= 0.0
        and positive_folds >= 2
    )
    return {
        "contract": SHADOW_ADMISSION_CONTRACT,
        "passed": not reasons,
        "grade": "promising" if promising else "exploratory" if not reasons else "rejected",
        "reasons": reasons,
        "checks": checks,
        "active_evidence_passed": bool(trial.get("passed_transparent_gates")),
        "positive_excess_folds": int(positive_folds),
        "net_return": _number(metrics.get("net_return"), -1.0),
        "net_excess_return": _number(metrics.get("net_excess_return"), -1.0),
        "cost_stress_net_excess_return": _number(
            stress.get("net_excess_return"), -1.0
        ),
        "max_drawdown": _number(metrics.get("max_drawdown"), 1.0),
        "target_fill_ratio": _number(metrics.get("target_fill_ratio"), -1.0),
        "bootstrap_probability": _number(
            trial.get("bootstrap_probability"), 0.0
        ),
    }


def _selection_key(row: Mapping[str, Any]) -> tuple[object, ...]:
    grade_rank = 2 if row.get("grade") == "promising" else 1
    return (
        grade_rank,
        _number(row.get("net_excess_return"), -1.0),
        _number(row.get("cost_stress_net_excess_return"), -1.0),
        _number(row.get("bootstrap_probability"), 0.0),
        -_number(row.get("max_drawdown"), 1.0),
        str(row.get("trial_id") or ""),
    )


def select_market_shadow_trials(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Select at most one safe, deterministic Shadow candidate per market."""

    if str(report.get("status") or "") not in {"transparent_complete", "complete"}:
        raise ValueError("shadow_admission_campaign_incomplete")
    if _flag(report.get("formal_strategy_activated")):
        raise ValueError("shadow_admission_formal_activation_conflict")
    campaign_id = str(report.get("campaign_id") or "").strip()
    manifest_hash = str(report.get("manifest_hash") or "").strip()
    if not campaign_id or not manifest_hash:
        raise ValueError("shadow_admission_campaign_provenance_missing")

    eligible_by_market: dict[str, list[dict[str, Any]]] = {
        market: [] for market in _MARKET_ORDER
    }
    for raw_scope in report.get("scopes") or []:
        if not isinstance(raw_scope, Mapping):
            continue
        trial = raw_scope.get("display_trial")
        if not isinstance(trial, Mapping):
            continue
        market = str(raw_scope.get("market") or trial.get("market") or "")
        scope = str(
            raw_scope.get("account_scope") or trial.get("account_scope") or ""
        )
        if market not in eligible_by_market or not scope:
            continue
        decision = evaluate_transparent_shadow_trial(trial)
        if not decision["passed"]:
            continue
        eligible_by_market[market].append({
            **decision,
            "market": market,
            "account_scope": scope,
            "trial_id": str(trial.get("trial_id") or ""),
            "spec_id": str(trial.get("spec_id") or ""),
            "spec_hash": str(trial.get("spec_hash") or ""),
            "horizon": int(_number(trial.get("horizon"), 0.0)),
            "campaign_id": campaign_id,
            "manifest_hash": manifest_hash,
            "trial": dict(trial),
        })

    missing = [market for market, rows in eligible_by_market.items() if not rows]
    if missing:
        raise ValueError("shadow_admission_market_missing:" + ",".join(missing))
    return [
        max(eligible_by_market[market], key=_selection_key)
        for market in _MARKET_ORDER
    ]


def _campaign_evidence_report(
    root: Path,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    campaign_id = str(report.get("campaign_id") or "").strip()
    manifest_hash = str(report.get("manifest_hash") or "").strip()
    campaign_root = root / "data/research/campaigns" / campaign_id
    manifest_path = campaign_root / "manifest.json"
    trials_path = campaign_root / "trials.jsonl"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        trial_lines = trials_path.read_text(encoding="utf-8").splitlines()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("shadow_admission_campaign_evidence") from exc
    if (
        not isinstance(manifest, Mapping)
        or str(manifest.get("campaign_id") or "") != campaign_id
        or str(manifest.get("manifest_hash") or "") != manifest_hash
    ):
        raise ValueError("shadow_admission_campaign_manifest_mismatch")

    trials_by_id: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(trial_lines, start=1):
        if not line.strip():
            continue
        try:
            trial = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"shadow_admission_trial_ledger_json:{line_number}"
            ) from exc
        if not isinstance(trial, dict):
            raise ValueError(f"shadow_admission_trial_ledger_row:{line_number}")
        trial_id = str(trial.get("trial_id") or "").strip()
        if not trial_id or trial_id in trials_by_id:
            raise ValueError(f"shadow_admission_trial_identity:{trial_id}")
        trials_by_id[trial_id] = trial

    hydrated = dict(report)
    hydrated_scopes: list[dict[str, Any]] = []
    for raw_scope in report.get("scopes") or []:
        if not isinstance(raw_scope, Mapping):
            continue
        scope_row = dict(raw_scope)
        display_trial = raw_scope.get("display_trial")
        if not isinstance(display_trial, Mapping):
            hydrated_scopes.append(scope_row)
            continue
        trial_id = str(display_trial.get("trial_id") or "").strip()
        evidence = trials_by_id.get(trial_id)
        if evidence is None:
            raise ValueError(f"shadow_admission_trial_evidence_missing:{trial_id}")
        expected_identity = {
            "market": str(raw_scope.get("market") or display_trial.get("market") or ""),
            "account_scope": str(
                raw_scope.get("account_scope")
                or display_trial.get("account_scope")
                or ""
            ),
            "spec_id": str(display_trial.get("spec_id") or ""),
            "spec_hash": str(display_trial.get("spec_hash") or ""),
            "horizon": int(_number(display_trial.get("horizon"), 0.0)),
            "manifest_hash": manifest_hash,
        }
        actual_identity = {
            "market": str(evidence.get("market") or ""),
            "account_scope": str(evidence.get("account_scope") or ""),
            "spec_id": str(evidence.get("spec_id") or ""),
            "spec_hash": str(evidence.get("spec_hash") or ""),
            "horizon": int(_number(evidence.get("horizon"), 0.0)),
            "manifest_hash": str(evidence.get("manifest_hash") or ""),
        }
        if actual_identity != expected_identity:
            raise ValueError(f"shadow_admission_trial_evidence_mismatch:{trial_id}")
        hydrated_trial = dict(evidence)
        if "passed_transparent_gates" in display_trial:
            hydrated_trial["passed_transparent_gates"] = display_trial[
                "passed_transparent_gates"
            ]
        scope_row["display_trial"] = hydrated_trial
        hydrated_scopes.append(scope_row)
    hydrated["scopes"] = hydrated_scopes
    hydrated["trial_evidence_path"] = _relative_to_root(trials_path, root)
    return hydrated


def _campaign_config_path(root: Path, campaign_id: str, market: str) -> Path:
    return (
        root
        / "data/research/campaigns"
        / campaign_id
        / "input"
        / market
        / "payload/configs"
        / f"competition_{market}.yaml"
    )


def _frozen_portfolio_contract(
    root: Path,
    *,
    campaign_id: str,
    market: str,
    account_scope: str,
    rebalance_frequency: str,
) -> dict[str, Any]:
    path = _campaign_config_path(root, campaign_id, market)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"shadow_admission_campaign_config:{market}") from exc
    accounts = [
        dict(account)
        for account in config.get("accounts") or []
        if str(account.get("scope") or account.get("id") or "") == account_scope
    ]
    if len(accounts) != 1:
        raise ValueError(
            f"shadow_admission_account_contract:{market}:{account_scope}"
        )
    return {
        "account": accounts[0],
        "trading": dict(config.get("trading") or {}),
        "rebalance_frequency": str(rebalance_frequency),
        "rule_execution_policy": {
            "version": "campaign-transparent-v1",
            "rank_buffer_pct": 0.20 if market == "a_share" else 0.40,
            "minimum_target_change": 0.0,
            "partial_adjustment_rate": 1.0,
            "max_daily_turnover": 1.0,
            "max_industry_weight": 1.0,
            "max_holding_days": 0,
        },
    }


def _model_version(selected: Mapping[str, Any]) -> str:
    identity = "|".join(
        (
            SHADOW_ADMISSION_CONTRACT,
            str(selected["campaign_id"]),
            str(selected["manifest_hash"]),
            str(selected["trial_id"]),
            str(selected["spec_hash"]),
        )
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", str(selected["spec_id"]).lower()).strip("-")
    return f"rule-{slug}-{suffix}"


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def admit_campaign_shadows(
    repo_root: str | Path,
    campaign_report: str | Path,
) -> dict[str, Any]:
    """Freeze and register one transparent Shadow strategy per market."""

    root = Path(repo_root).resolve()
    report_path = Path(campaign_report)
    if not report_path.is_absolute():
        report_path = root / report_path
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("shadow_admission_campaign_report") from exc
    if not isinstance(report, dict):
        raise ValueError("shadow_admission_campaign_report")
    report = _campaign_evidence_report(root, report)
    selected_rows = select_market_shadow_trials(report)
    admitted: list[dict[str, Any]] = []
    for selected in selected_rows:
        market = str(selected["market"])
        scope = str(selected["account_scope"])
        horizon = int(selected["horizon"])
        matches = [
            spec
            for spec in transparent_strategy_specs(market, scope)
            if spec.spec_id == selected["spec_id"]
        ]
        if len(matches) != 1 or matches[0].spec_hash != selected["spec_hash"]:
            raise ValueError(
                f"shadow_admission_spec_mismatch:{market}:{scope}:{selected['spec_id']}"
            )
        spec = matches[0]
        portfolio_contract = _frozen_portfolio_contract(
            root,
            campaign_id=str(selected["campaign_id"]),
            market=market,
            account_scope=scope,
            rebalance_frequency=spec.rebalance_frequency,
        )
        registry_path = model_registry_path(
            root,
            market,
            horizon,
            account_scope=scope,
        )
        version = _model_version(selected)
        artifact_path = registry_path.parent / "shadow_candidates" / f"{version}.json"
        artifact = {
            "schema_version": 1,
            "candidate_kind": "transparent_rule",
            "runtime_contract": RULE_RUNTIME_CONTRACT,
            "admission_contract": SHADOW_ADMISSION_CONTRACT,
            "promotion_policy": RULE_PROMOTION_POLICY,
            "model_version": version,
            "market": market,
            "account_scope": scope,
            "horizon": horizon,
            "campaign_id": selected["campaign_id"],
            "manifest_hash": selected["manifest_hash"],
            "source_report": _relative_to_root(report_path, root),
            "source_trial_ledger": str(report.get("trial_evidence_path") or ""),
            "source_trial_id": selected["trial_id"],
            "source_campaign_status": str(report.get("status") or ""),
            "source_trial_passed_active_gates": bool(
                selected["active_evidence_passed"]
            ),
            "admission": {
                key: value
                for key, value in selected.items()
                if key not in {"trial"}
            },
            "spec": asdict(spec),
            "portfolio_contract": portfolio_contract,
            "frozen_at": now_iso(),
        }
        if artifact_path.exists():
            existing = json.loads(artifact_path.read_text(encoding="utf-8"))
            comparable = dict(existing)
            comparable.pop("frozen_at", None)
            expected = json.loads(
                json.dumps(artifact, ensure_ascii=True, sort_keys=True)
            )
            expected.pop("frozen_at", None)
            if comparable != expected:
                raise ValueError(f"shadow_admission_artifact_conflict:{version}")
        else:
            write_json(artifact_path, artifact)
        artifact_ref = _relative_to_root(artifact_path, root)
        registry = ModelRegistry(registry_path)
        state = registry.admit_development_shadow(
            version,
            metadata={
                "candidate_kind": "transparent_rule",
                "runtime_contract": RULE_RUNTIME_CONTRACT,
                "spec_id": spec.spec_id,
                "spec_hash": spec.spec_hash,
                "artifact": artifact_ref,
                "account_scope": scope,
                "admission_grade": selected["grade"],
                "source_campaign": selected["campaign_id"],
                "source_manifest_hash": selected["manifest_hash"],
                "source_trial_id": selected["trial_id"],
                "promotion_policy": RULE_PROMOTION_POLICY,
                "metrics": {
                    "training_protocol_version": RULE_RUNTIME_CONTRACT,
                    "point_in_time_audit": True,
                    "historical_net_return": selected["net_return"],
                    "historical_net_excess_return": selected["net_excess_return"],
                    "historical_cost_stress_net_excess_return": selected[
                        "cost_stress_net_excess_return"
                    ],
                    "historical_max_drawdown": selected["max_drawdown"],
                    "historical_positive_excess_folds": selected[
                        "positive_excess_folds"
                    ],
                },
            },
            admission={
                "contract": SHADOW_ADMISSION_CONTRACT,
                "grade": selected["grade"],
                "checks": selected["checks"],
                "active_evidence_passed": selected["active_evidence_passed"],
                "promotion_policy": RULE_PROMOTION_POLICY,
            },
        )
        model = state["models"][version]
        admitted.append({
            "market": market,
            "account_scope": scope,
            "spec_id": spec.spec_id,
            "model_version": version,
            "grade": selected["grade"],
            "status": model["status"],
            "formal_strategy_activated": bool(
                state.get("formal_strategy_activated")
            ),
            "registry_path": _relative_to_root(registry_path, root),
            "artifact_path": artifact_ref,
        })
    return {
        "status": "complete",
        "contract": SHADOW_ADMISSION_CONTRACT,
        "campaign_id": str(report.get("campaign_id") or ""),
        "manifest_hash": str(report.get("manifest_hash") or ""),
        "formal_strategy_activated": False,
        "admitted": admitted,
    }


__all__ = [
    "RULE_PROMOTION_POLICY",
    "RULE_RUNTIME_CONTRACT",
    "SHADOW_ADMISSION_CONTRACT",
    "admit_campaign_shadows",
    "evaluate_transparent_shadow_trial",
    "select_market_shadow_trials",
]
