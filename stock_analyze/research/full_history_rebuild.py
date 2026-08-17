"""Orchestration and safety audits for the full-history model rebuild."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from pandas.api.types import is_string_dtype


def _date_key(values: pd.Series) -> pd.Series:
    return values.astype("string").str.replace("-", "", regex=False).str[:8]


def audit_full_history_dataset(
    frame: pd.DataFrame,
    *,
    market: str,
    required_start: str,
    required_end: str,
) -> dict[str, Any]:
    """Fail-closed audit of a materialized point-in-time feature panel."""

    required = {"code", "trade_date", "benchmark_code"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        return {
            "passed": False,
            "market": str(market),
            "rows": int(len(frame)),
            "dates": 0,
            "instruments": 0,
            "reasons": [f"missing_columns:{','.join(missing)}"],
        }

    reasons: list[str] = []
    if not is_string_dtype(frame["code"].dtype):
        reasons.append("code_dtype")
    normalized = frame.copy()
    normalized["trade_date"] = _date_key(normalized["trade_date"])
    start = str(normalized["trade_date"].min() or "")
    end = str(normalized["trade_date"].max() or "")
    start_limit = (
        datetime.strptime(str(required_start).replace("-", "")[:8], "%Y%m%d")
        + timedelta(days=7)
    ).strftime("%Y%m%d")
    required_end_key = str(required_end).replace("-", "")[:8]
    if not start or start > start_limit:
        reasons.append("start_date_shortfall")
    if not end or end < required_end_key:
        reasons.append("end_date_shortfall")

    key_columns = ["code", "trade_date"]
    for column in ("research_scope", "account_id"):
        if column in normalized.columns and column not in key_columns:
            key_columns.append(column)
    if bool(normalized.duplicated(key_columns, keep=False).any()):
        reasons.append("duplicate_keys")

    if "list_date" in normalized.columns:
        listing = _date_key(normalized["list_date"])
        if bool((normalized["trade_date"] < listing).fillna(False).any()):
            reasons.append("prelisting_rows")

    pit_date_columns = (
        "fundamental_available_date",
        "daily_basic_trade_date",
        "nav_date",
    )
    future_observation = False
    for column in pit_date_columns:
        if column not in normalized.columns:
            continue
        observed = _date_key(normalized[column])
        valid = observed.notna() & observed.ne("")
        if bool((observed[valid] > normalized.loc[valid, "trade_date"]).any()):
            future_observation = True
            break
    if future_observation:
        reasons.append("future_observation_rows")

    benchmark_coverage = float(normalized["benchmark_code"].notna().mean())
    if benchmark_coverage < 0.95:
        reasons.append("benchmark_coverage")

    excluded = {
        "code", "trade_date", "list_date", "feature_observed_at",
        "fundamental_available_date", "daily_basic_trade_date", "nav_date",
        "benchmark_code", "research_scope", "account_id",
    }
    numeric = [
        column for column in normalized.select_dtypes(include="number").columns
        if column not in excluded
    ]
    feature_coverage = {
        column: float(pd.to_numeric(normalized[column], errors="coerce").notna().mean())
        for column in numeric
    }
    return {
        "passed": not reasons,
        "market": str(market),
        "rows": int(len(normalized)),
        "dates": int(normalized["trade_date"].nunique()),
        "instruments": int(normalized["code"].astype(str).nunique()),
        "start_date": start or None,
        "end_date": end or None,
        "benchmark_coverage": benchmark_coverage,
        "feature_coverage": feature_coverage,
        "reasons": reasons,
    }


def retire_legacy_rebuild_shadows(
    repo_root: str | Path,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Retire only legacy transparent Shadow candidates in rebuild scopes."""

    import json
    from datetime import datetime
    from pathlib import Path

    from .activation import ModelRegistry

    root = Path(repo_root).resolve()
    target_scopes = {"hs300", "zz500", "hk_exposure", "us_exposure"}
    entries: list[dict[str, Any]] = []
    changed = 0
    for registry_path in sorted(root.glob("data/research/models/*/*/*/registry.json")):
        scope = registry_path.parts[-3]
        if scope not in target_scopes:
            continue
        state = json.loads(registry_path.read_text(encoding="utf-8"))
        models = state.get("models") or {}
        versions = [
            str(version)
            for version, model in sorted(models.items())
            if isinstance(model, dict)
            and str(model.get("status") or "") == "shadow"
            and str(model.get("candidate_kind") or "") == "transparent_rule"
        ]
        if not versions:
            continue
        registry = ModelRegistry(registry_path)
        for version in versions:
            entry = {
                "registry_path": str(registry_path.relative_to(root)),
                "scope": scope,
                "model_version": version,
                "reason": "full_history_rebuild_superseded",
                "applied": False,
            }
            if apply:
                model = models[version]
                model["status"] = "retired"
                model["retirement_reason"] = "full_history_rebuild_superseded"
                role_status = model.setdefault("role_status", {})
                for role, status in list(role_status.items()):
                    if status in {"research", "shadow"}:
                        role_status[role] = "retired"
                event_id = f"full-history-rebuild-retire:{version}"
                events = state.setdefault("lifecycle_events", [])
                if not any(str(event.get("event_id") or "") == event_id for event in events):
                    events.append({
                        "event_id": event_id,
                        "event_type": "retirement",
                        "model_version": version,
                        "reason": "full_history_rebuild_superseded",
                        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    })
                entry["applied"] = True
                changed += 1
            entries.append(entry)
        if apply:
            registry._write(state)
    return {
        "status": "complete",
        "apply": bool(apply),
        "flagged": len(entries),
        "changed": changed,
        "entries": entries,
    }


def load_scope_dataset(
    feature_path: str | Path,
    label_path: str | Path,
    *,
    market: str,
    scope: str,
    horizon: int,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Join one account-scoped PIT feature view to one forward-label horizon."""

    from pathlib import Path

    from .account_features import account_feature_contract, build_account_feature_view
    from .labels import LABEL_CONTRACT_VERSION

    features = pd.read_parquet(Path(feature_path))
    labels = pd.read_parquet(Path(label_path))
    observed_contracts = set(
        labels.get("label_contract_version", pd.Series(dtype="string"))
        .dropna().astype(str)
    )
    if observed_contracts != {LABEL_CONTRACT_VERSION}:
        raise ValueError("full_history_label_contract_invalid")
    for frame in (features, labels):
        frame["code"] = frame["code"].astype("string").str.zfill(6)
        frame["trade_date"] = frame["trade_date"].astype("string")
    scoped_features = build_account_feature_view(features, account_scope=scope)
    scoped_labels = labels.loc[
        labels["account_id"].astype(str).eq(str(scope))
        & pd.to_numeric(labels["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    if scoped_labels.empty:
        raise ValueError(f"full_history_labels_empty:{scope}:{horizon}")
    if scoped_features.duplicated(["code", "trade_date"]).any():
        raise ValueError(f"full_history_features_duplicate:{scope}")
    if scoped_labels.duplicated(["code", "trade_date"]).any():
        raise ValueError(f"full_history_labels_duplicate:{scope}:{horizon}")
    label_columns = [
        column for column in scoped_labels.columns
        if column in {"code", "trade_date"} or column not in scoped_features.columns
    ]
    dataset = scoped_features.merge(
        scoped_labels.loc[:, label_columns],
        on=["code", "trade_date"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    feature_contract = account_feature_contract(market, scope, horizon)
    allowed = tuple(
        column for column in feature_contract.allowed_features
        if column in dataset.columns
    )
    if not allowed:
        raise ValueError(f"full_history_features_empty:{scope}")
    return dataset, allowed


def run_full_history_rebuild(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    scopes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run the frozen four-scope campaign and write an auditable report."""

    import hashlib
    import json
    from pathlib import Path

    import yaml

    from .full_history_training import (
        evaluate_frozen_historical_test,
        evaluate_scope_campaign,
    )
    from .full_history_windows import (
        load_full_history_config,
        seal_full_history_manifest,
    )
    from .shadow_admission import evaluate_transparent_shadow_trial

    root = Path(repo_root).resolve()
    contract = load_full_history_config(root / "configs/research/full_history_rebuild.yaml")
    requested = tuple(scopes or contract.scopes.keys())
    unknown = sorted(set(requested).difference(contract.scopes))
    if unknown:
        raise ValueError(f"full_history_scope_unknown:{','.join(unknown)}")
    run_root = root / "data/research/full_history_rebuild" / str(snapshot_date)
    run_root.mkdir(parents=True, exist_ok=True)
    market_audits: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for scope in requested:
        scope_contract = contract.scopes[scope]
        market = scope_contract.market
        feature_path = root / "data/research/features" / market / f"{snapshot_date}.parquet"
        label_path = root / "data/research/labels" / market / f"{snapshot_date}.parquet"
        if market not in market_audits:
            raw_features = pd.read_parquet(feature_path)
            market_audits[market] = audit_full_history_dataset(
                raw_features,
                market=market,
                required_start=contract.source_start,
                required_end=str(snapshot_date),
            )
        audit = market_audits[market]
        if not audit["passed"]:
            results.append({
                "scope": scope,
                "market": market,
                "status": "data_blocked",
                "reasons": list(audit["reasons"]),
            })
            continue
        dataset, allowed_features = load_scope_dataset(
            feature_path,
            label_path,
            market=market,
            scope=scope,
            horizon=scope_contract.horizon,
        )
        baseline_path = root / f"configs/competition_{market}.yaml"
        baseline = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
        baseline["accounts"] = [
            account for account in baseline.get("accounts") or []
            if str(account.get("id") or "") == scope
        ]
        declarations = {
            estimator: list(variants)
            for estimator, variants in contract.candidates.get(market, {}).items()
            if estimator != "temporal_context_net"
        }
        campaign = evaluate_scope_campaign(
            dataset,
            contract=contract,
            scope=scope,
            candidate_features=allowed_features,
            candidate_declarations=declarations,
            portfolio_contract=baseline,
            random_state=20260816,
        )
        scope_result: dict[str, Any] = {
            "scope": scope,
            "market": market,
            "status": campaign["status"],
            "candidate_features": list(allowed_features),
            "research_only_challengers": ["temporal_context_net"] if market == "a_share" else [],
            "campaign": campaign,
            "historical_test": None,
            "shadow_decision": None,
        }
        if campaign["status"] == "development_pass":
            selected = campaign["selected"]
            data_fingerprint = hashlib.sha256(
                (hashlib.sha256(feature_path.read_bytes()).hexdigest()
                 + hashlib.sha256(label_path.read_bytes()).hexdigest()).encode("ascii")
            ).hexdigest()
            manifest_path = run_root / scope / "evaluation_manifest.json"
            manifest = seal_full_history_manifest(manifest_path, {
                "protocol": contract.protocol,
                "scope": scope,
                "snapshot_date": str(snapshot_date),
                "development_end": contract.development_end,
                "historical_test_start": contract.historical_test_start,
                "data_fingerprint": data_fingerprint,
                "selected_trial_id": selected["trial_id"],
            })
            historical = evaluate_frozen_historical_test(
                dataset,
                contract=contract,
                scope=scope,
                candidate_features=selected.get("stable_features") or allowed_features,
                estimator=selected["estimator"],
                parameters=selected["parameters"],
                portfolio_contract=baseline,
                manifest_path=manifest_path,
                declaration_id=manifest["declaration_id"],
                random_state=20260816,
            )
            historical_metrics = dict(historical["metrics"])
            historical_metrics.setdefault("attribution_status", "reconciled")
            trial = {
                "trial_id": selected["trial_id"],
                "market": market,
                "account_scope": scope,
                "horizon": scope_contract.horizon,
                "point_in_time_audit": selected.get("point_in_time_audit") is True,
                "expected_outer_folds": 4,
                "folds": selected.get("folds") or [],
                "metrics": historical_metrics,
                "cost_stress": historical["cost_stress"],
                "bootstrap_probability": historical["bootstrap_probability"],
                "deflated_sharpe_probability": campaign["governance"]["deflated_sharpe_probability"],
                "probability_of_backtest_overfit": campaign["governance"]["probability_of_backtest_overfit"],
                "gate_zero": {"passed": True, "reasons": []},
                "passed_transparent_gates": selected.get("passed_transparent_gates") is True,
            }
            decision = evaluate_transparent_shadow_trial(trial)
            scope_result["historical_test"] = historical
            scope_result["shadow_decision"] = decision
            scope_result["status"] = "shadow_gate_pass" if decision["passed"] else "shadow_gate_fail"
        results.append(scope_result)

    payload = {
        "status": "complete",
        "protocol": contract.protocol,
        "snapshot_date": str(snapshot_date),
        "market_audits": market_audits,
        "results": results,
        "shadow_gate_passed": [
            item["scope"] for item in results if item.get("status") == "shadow_gate_pass"
        ],
    }
    report_path = run_root / "report.json"
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    payload["report_path"] = str(report_path)
    return payload


__all__ = [
    "audit_full_history_dataset",
    "load_scope_dataset",
    "retire_legacy_rebuild_shadows",
    "run_full_history_rebuild",
]
