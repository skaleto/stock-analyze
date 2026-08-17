"""Auditable Shadow admission for sealed transparent strategy campaigns."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from ..markets.cn_qdii_etf import mechanics as qdii_mechanics
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
_ACCOUNT_ORDER = (
    ("a_share", "hs300"),
    ("a_share", "zz500"),
    ("cn_qdii_etf", "hk_exposure"),
    ("cn_qdii_etf", "us_exposure"),
)


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
    expected_folds = int(_number(trial.get("expected_outer_folds"), 3.0))
    if expected_folds < 2 or expected_folds > 10:
        expected_folds = 3
    checks = {
        "point_in_time_audit": trial.get("point_in_time_audit") is True,
        "gate_zero": gate_zero.get("passed") is True,
        "walk_forward_folds": len(folds) == expected_folds,
        "all_folds_traded": (
            len(folds) == expected_folds
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
    safety_reasons = [name for name, passed in checks.items() if not passed]
    active_evidence_passed = trial.get("passed_transparent_gates") is True
    positive_folds = sum(
        _number(item.get("net_excess_return"), -1.0) > 0.0
        for item in folds
    )
    quality_checks = {
        "quality_gate_not_passed": active_evidence_passed,
        "positive_net_excess_return": (
            _number(metrics.get("net_excess_return"), -1.0) > 0.0
        ),
        "cost_stress_net_excess_return": (
            _number(stress.get("net_excess_return"), -1.0) >= 0.0
        ),
        "all_positive_excess_folds": (
            len(folds) == expected_folds and positive_folds == len(folds)
        ),
        "bootstrap_probability": (
            _number(trial.get("bootstrap_probability"), 0.0) >= 0.95
        ),
        "deflated_sharpe_probability": (
            expected_folds < 4
            or _number(trial.get("deflated_sharpe_probability"), 0.0) >= 0.95
        ),
        "probability_of_backtest_overfit": (
            expected_folds < 4
            or _number(trial.get("probability_of_backtest_overfit"), 1.0) <= 0.50
        ),
    }
    quality_reasons = [name for name, passed in quality_checks.items() if not passed]
    reasons = [*safety_reasons, *quality_reasons]
    promising = bool(
        not safety_reasons
        and _number(metrics.get("net_excess_return"), -1.0) > 0.0
        and _number(stress.get("net_excess_return"), -1.0) >= 0.0
        and positive_folds >= 2
    )
    return {
        "contract": SHADOW_ADMISSION_CONTRACT,
        "passed": not safety_reasons and not quality_reasons,
        "grade": (
            "promising" if promising
            else "exploratory" if not safety_reasons
            else "rejected"
        ),
        "reasons": reasons,
        "checks": {**checks, **quality_checks},
        "active_evidence_passed": active_evidence_passed,
        "expected_outer_folds": int(expected_folds),
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


def _validate_campaign_header(report: Mapping[str, Any]) -> tuple[str, str]:
    if str(report.get("status") or "") not in {"transparent_complete", "complete"}:
        raise ValueError("shadow_admission_campaign_incomplete")
    if _flag(report.get("formal_strategy_activated")):
        raise ValueError("shadow_admission_formal_activation_conflict")
    campaign_id = str(report.get("campaign_id") or "").strip()
    manifest_hash = str(report.get("manifest_hash") or "").strip()
    if not campaign_id or not manifest_hash:
        raise ValueError("shadow_admission_campaign_provenance_missing")
    return campaign_id, manifest_hash


def _scope_trials(raw_scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_trials = raw_scope.get("trials")
    if isinstance(raw_trials, list):
        trials = [dict(row) for row in raw_trials if isinstance(row, Mapping)]
        if trials:
            return trials
    display_trial = raw_scope.get("display_trial")
    return [dict(display_trial)] if isinstance(display_trial, Mapping) else []


def decide_account_shadow_trials(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one explicit admitted/blocked decision for every report scope."""

    campaign_id, manifest_hash = _validate_campaign_header(report)
    decisions_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_scope in report.get("scopes") or []:
        if not isinstance(raw_scope, Mapping):
            continue
        market = str(raw_scope.get("market") or "")
        scope = str(raw_scope.get("account_scope") or "")
        identity = (market, scope)
        if identity not in _ACCOUNT_ORDER:
            continue
        if identity in decisions_by_scope:
            raise ValueError(f"shadow_admission_duplicate_scope:{market}:{scope}")

        evaluated: list[dict[str, Any]] = []
        eligible: list[dict[str, Any]] = []
        seen_trial_ids: set[str] = set()
        for trial in _scope_trials(raw_scope):
            trial_market = str(trial.get("market") or market)
            trial_scope = str(trial.get("account_scope") or scope)
            if (trial_market, trial_scope) != identity:
                raise ValueError(
                    f"shadow_admission_trial_scope_mismatch:{market}:{scope}"
                )
            trial_id = str(trial.get("trial_id") or "").strip()
            if not trial_id or trial_id in seen_trial_ids:
                raise ValueError(f"shadow_admission_trial_identity:{trial_id}")
            seen_trial_ids.add(trial_id)
            decision = evaluate_transparent_shadow_trial(trial)
            row = {
                **decision,
                "market": market,
                "account_scope": scope,
                "trial_id": trial_id,
                "spec_id": str(trial.get("spec_id") or ""),
                "spec_hash": str(trial.get("spec_hash") or ""),
                "horizon": int(_number(trial.get("horizon"), 0.0)),
                "campaign_id": campaign_id,
                "manifest_hash": manifest_hash,
                "trial": trial,
            }
            evaluated.append(row)
            if decision["passed"]:
                eligible.append(row)

        if eligible:
            selected = max(eligible, key=_selection_key)
            decisions_by_scope[identity] = {**selected, "status": "admitted"}
        else:
            decisions_by_scope[identity] = {
                "status": "blocked",
                "passed": False,
                "grade": "rejected",
                "reasons": ["no_safe_trial"],
                "market": market,
                "account_scope": scope,
                "campaign_id": campaign_id,
                "manifest_hash": manifest_hash,
                "trial_decisions": [
                    {
                        key: row[key]
                        for key in (
                            "trial_id",
                            "spec_id",
                            "grade",
                            "passed",
                            "reasons",
                            "checks",
                            "net_return",
                            "net_excess_return",
                            "cost_stress_net_excess_return",
                            "max_drawdown",
                            "target_fill_ratio",
                        )
                    }
                    for row in evaluated
                ],
            }

    return [
        decisions_by_scope[identity]
        for identity in _ACCOUNT_ORDER
        if identity in decisions_by_scope
    ]


def select_account_shadow_trials(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Select at most one safe, deterministic Shadow candidate per account."""

    return [
        row
        for row in decide_account_shadow_trials(report)
        if row.get("status") == "admitted"
    ]


def select_market_shadow_trials(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compatibility alias for the account-scoped selector."""

    return select_account_shadow_trials(report)


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
        market = str(
            raw_scope.get("market")
            or (display_trial.get("market") if isinstance(display_trial, Mapping) else "")
            or ""
        )
        scope = str(
            raw_scope.get("account_scope")
            or (
                display_trial.get("account_scope")
                if isinstance(display_trial, Mapping)
                else ""
            )
            or ""
        )
        scope_trials = [
            dict(trial)
            for trial in trials_by_id.values()
            if str(trial.get("market") or "") == market
            and str(trial.get("account_scope") or "") == scope
        ]
        if not scope_trials:
            raise ValueError(
                f"shadow_admission_scope_evidence_missing:{market}:{scope}"
            )
        expected_count = int(_number(raw_scope.get("transparent_trial_count"), 0.0))
        if expected_count and len(scope_trials) != expected_count:
            raise ValueError(
                f"shadow_admission_scope_trial_count:{market}:{scope}"
            )
        for trial in scope_trials:
            trial_id = str(trial.get("trial_id") or "")
            if (
                str(trial.get("manifest_hash") or "") != manifest_hash
                or not str(trial.get("spec_id") or "")
                or not str(trial.get("spec_hash") or "")
                or int(_number(trial.get("horizon"), 0.0)) <= 0
            ):
                raise ValueError(
                    f"shadow_admission_trial_evidence_mismatch:{trial_id}"
                )

        if isinstance(display_trial, Mapping):
            display_id = str(display_trial.get("trial_id") or "").strip()
            evidence = trials_by_id.get(display_id)
            if evidence is None:
                raise ValueError(
                    f"shadow_admission_trial_evidence_missing:{display_id}"
                )
            expected_display_identity = {
                "market": market,
                "account_scope": scope,
                "spec_id": str(display_trial.get("spec_id") or ""),
                "spec_hash": str(display_trial.get("spec_hash") or ""),
                "horizon": int(_number(display_trial.get("horizon"), 0.0)),
                "manifest_hash": manifest_hash,
            }
            actual_display_identity = {
                "market": str(evidence.get("market") or ""),
                "account_scope": str(evidence.get("account_scope") or ""),
                "spec_id": str(evidence.get("spec_id") or ""),
                "spec_hash": str(evidence.get("spec_hash") or ""),
                "horizon": int(_number(evidence.get("horizon"), 0.0)),
                "manifest_hash": str(evidence.get("manifest_hash") or ""),
            }
            if actual_display_identity != expected_display_identity:
                raise ValueError(
                    f"shadow_admission_trial_evidence_mismatch:{display_id}"
                )
            hydrated_display = dict(evidence)
            if "passed_transparent_gates" in display_trial:
                hydrated_display["passed_transparent_gates"] = display_trial[
                    "passed_transparent_gates"
                ]
            scope_row["display_trial"] = hydrated_display
        scope_row["trials"] = scope_trials
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
        "settlement": {
            "sell_proceeds_reusable_same_day": bool(
                market == "cn_qdii_etf"
                and qdii_mechanics.SELL_PROCEEDS_REUSABLE_SAME_DAY
            ),
        },
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
    """Freeze and register one transparent Shadow strategy per account."""

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
    decisions = decide_account_shadow_trials(report)
    selected_rows = [
        row for row in decisions if row.get("status") == "admitted"
    ]
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
                    "historical_target_fill_ratio": selected[
                        "target_fill_ratio"
                    ],
                    "historical_bootstrap_probability": selected[
                        "bootstrap_probability"
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
        prior_rule_versions = [
            candidate_version
            for candidate_version, metadata in (state.get("models") or {}).items()
            if candidate_version != version
            and str((metadata or {}).get("candidate_kind") or "")
            == "transparent_rule"
            and str((metadata or {}).get("status") or "") == "shadow"
        ]
        for prior_version in prior_rule_versions:
            state = registry.supersede_shadow(
                prior_version,
                successor_version=version,
                event_id=f"shadow-superseded:{prior_version}:{version}",
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
        "decisions": [
            {
                key: value
                for key, value in decision.items()
                if key != "trial"
            }
            for decision in decisions
        ],
    }


def audit_shadow_quality(
    repo_root: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Report or reject legacy transparent Shadow candidates."""

    root = Path(repo_root).resolve()
    entries: list[dict[str, Any]] = []
    changed = 0
    registry_paths = sorted(
        root.glob("data/research/models/*/*/*/registry.json")
    )
    for registry_path in registry_paths:
        try:
            state = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"shadow_quality_registry:{_relative_to_root(registry_path, root)}"
            ) from exc
        models = state.get("models")
        if not isinstance(models, Mapping):
            continue
        for model_version, raw_model in sorted(models.items()):
            if not isinstance(raw_model, Mapping):
                continue
            if str(raw_model.get("status") or "") != "shadow":
                continue
            if str(raw_model.get("candidate_kind") or "") != "transparent_rule":
                continue
            admission = raw_model.get("development_admission")
            admission = admission if isinstance(admission, Mapping) else {}
            contract = str(admission.get("contract") or "")
            active_evidence_passed = admission.get("active_evidence_passed") is True
            reasons = []
            if contract != SHADOW_ADMISSION_CONTRACT:
                reasons.append("legacy_admission_contract")
            if not active_evidence_passed:
                reasons.append("historical_quality_gate_not_passed")
            if not reasons:
                continue
            entry = {
                "registry_path": _relative_to_root(registry_path, root),
                "model_version": str(model_version),
                "current_status": "shadow",
                "candidate_kind": "transparent_rule",
                "admission_contract": contract,
                "active_evidence_passed": active_evidence_passed,
                "reasons": reasons,
                "action": "reject",
                "applied": False,
            }
            if apply:
                registry = ModelRegistry(registry_path)
                registry.reject_shadow(
                    str(model_version),
                    reason="historical_quality_gate_not_passed",
                    event_id=f"shadow-quality-audit:{model_version}:{contract or 'missing'}",
                )
                entry["applied"] = True
                changed += 1
            entries.append(entry)
    return {
        "status": "audit_required" if entries and not apply else "complete",
        "contract": SHADOW_ADMISSION_CONTRACT,
        "apply": bool(apply),
        "flagged": len(entries),
        "changed": changed,
        "entries": entries,
    }

__all__ = [
    "RULE_PROMOTION_POLICY",
    "RULE_RUNTIME_CONTRACT",
    "SHADOW_ADMISSION_CONTRACT",
    "audit_shadow_quality",
    "admit_campaign_shadows",
    "decide_account_shadow_trials",
    "evaluate_transparent_shadow_trial",
    "select_account_shadow_trials",
    "select_market_shadow_trials",
]
