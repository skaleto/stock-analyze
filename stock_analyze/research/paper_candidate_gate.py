"""First-layer Research to isolated-paper qualification gate."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import pandas as pd
import yaml

from ..utils import write_text_atomic


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: object, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def load_gate_contract(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("protocol") != "paper-candidate-gate-v1":
        raise ValueError("paper_candidate_gate_contract")
    if payload.get("policy_layer") != "research_to_isolated_paper":
        raise ValueError("paper_candidate_gate_layer")
    if payload.get("formal_strategy_activated") is not False:
        raise ValueError("paper_candidate_gate_formal_boundary")
    if payload.get("registry_mutation_allowed") is not False:
        raise ValueError("paper_candidate_gate_registry_boundary")
    if payload.get("second_layer_active_gate_unchanged") is not True:
        raise ValueError("paper_candidate_gate_second_layer")
    if payload.get("comparison") != "router_only":
        raise ValueError("paper_candidate_gate_comparator")
    return {**payload, "contract_sha256": _canonical_hash(payload)}


def _label_contract(repo_root: Path, market: str, snapshot: str) -> str:
    path = repo_root / "data/research/labels" / market / f"{snapshot}.parquet"
    if not path.is_file():
        return "missing"
    frame = pd.read_parquet(path, columns=["label_contract_version"])
    values = sorted(set(frame["label_contract_version"].dropna().astype(str)))
    return values[0] if len(values) == 1 else ",".join(values) or "missing"


def _initial_cash(repo_root: Path, market: str, scope: str) -> float:
    path = repo_root / f"configs/competition_{market}.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    matches = [
        account for account in payload.get("accounts") or []
        if str(account.get("id") or "") == scope
    ]
    if len(matches) != 1:
        raise ValueError(f"paper_candidate_gate_account:{market}:{scope}")
    cash = _number(matches[0].get("cash"))
    if not math.isfinite(cash) or cash <= 0.0:
        raise ValueError(f"paper_candidate_gate_cash:{market}:{scope}")
    return cash


def evaluate_scope_result(
    repo_root: str | Path,
    source_report: Mapping[str, Any],
    scope_result: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    scope = str(scope_result.get("scope") or "")
    market = str(scope_result.get("market") or "")
    source_status = str(scope_result.get("status") or "")
    checks: dict[str, bool] = {}
    metrics: dict[str, Any] = {
        "source_status": source_status,
        "known_development_evidence_reassessment": True,
    }
    if source_status == "insufficient_data":
        return {
            "scope": scope, "market": market,
            "status": "insufficient_data",
            "qualified_for_isolated_paper": False,
            "reasons": list(scope_result.get("reasons") or ["source_insufficient_data"]),
            "checks": checks, "metrics": metrics,
        }

    candidate_name = str(contract["candidate"])
    comparator_name = str(contract["comparison"])
    result_metrics = dict(scope_result.get("metrics") or {})
    candidate = dict(result_metrics.get(candidate_name) or {})
    comparator = dict(result_metrics.get(comparator_name) or {})
    candidate_folds = list((scope_result.get("fold_metrics") or {}).get(candidate_name) or [])
    comparator_folds = list((scope_result.get("fold_metrics") or {}).get(comparator_name) or [])
    folds = list(scope_result.get("folds") or [])
    expected_folds = len(candidate_folds)

    annual_candidate = _number(candidate.get("annualized_excess_wealth"))
    annual_comparator = _number(comparator.get("annualized_excess_wealth"))
    annual_delta = annual_candidate - annual_comparator
    cumulative_delta = (
        _number(candidate.get("cumulative_relative_wealth"))
        - _number(comparator.get("cumulative_relative_wealth"))
    )
    incremental_cost = max(
        _number(candidate.get("total_execution_cost"), 0.0)
        - _number(comparator.get("total_execution_cost"), 0.0),
        0.0,
    )
    capital_base = _initial_cash(root, market, scope) * max(expected_folds, 1)
    incremental_cost_return = incremental_cost / capital_base
    fold_deltas = [
        _number(candidate_row.get("net_excess_return"))
        - _number(comparator_row.get("net_excess_return"))
        for candidate_row, comparator_row in zip(candidate_folds, comparator_folds)
    ]
    fold_median = float(median(fold_deltas)) if fold_deltas else math.nan
    disaster = dict(contract["disaster_fold"])
    disaster_rows = []
    for candidate_row, comparator_row, delta in zip(
        candidate_folds, comparator_folds, fold_deltas
    ):
        reasons = []
        if _number(candidate_row.get("net_excess_return")) < float(
            disaster["minimum_candidate_net_excess"]
        ):
            reasons.append("candidate_net_excess")
        if delta < float(disaster["minimum_delta_vs_router"]):
            reasons.append("delta_vs_router")
        if _number(candidate_row.get("max_drawdown"), 1.0) > float(
            disaster["maximum_drawdown"]
        ):
            reasons.append("max_drawdown")
        if reasons:
            disaster_rows.append({
                "fold": candidate_row.get("fold"),
                "reasons": reasons,
            })

    label_contract = _label_contract(
        root, market, str(source_report.get("snapshot_date") or "")
    )
    checks.update({
        "source_protocol": source_report.get("protocol") == contract["required_source_protocol"],
        "historical_test_closed": source_report.get("historical_test_opened") is False,
        "source_formal_inactive": source_report.get("formal_strategy_activated") is False,
        "source_registry_unmodified": source_report.get("registry_mutated") is False,
        "label_contract": label_contract == contract["required_label_contract"],
        "fold_count": expected_folds == 4 and len(comparator_folds) == 4,
        "point_in_time_audit": len(folds) == 4 and all(row.get("point_in_time_audit") is True for row in folds),
        "all_scenes_fitted": (scope_result.get("gate_checks") or {}).get("all_scenes_fitted") is True,
        "simulator_version": candidate.get("simulator_version") == contract["required_simulator_version"],
        "attribution_status": candidate.get("attribution_status") == contract["required_attribution_status"],
        "trade_activity": int(_number(candidate.get("trade_count"), 0.0)) > 0,
        "candidate_positive_net_excess": annual_candidate > float(contract["minimum_candidate_annualized_net_excess"]),
        "fold_delta_median": fold_median > float(contract["minimum_fold_delta_median_exclusive"]),
        "no_disaster_fold": not disaster_rows,
        "aggregate_drawdown": _number(candidate.get("max_drawdown"), 1.0) <= float(contract["maximum_aggregate_drawdown"]),
        "aggregate_turnover": _number(candidate.get("annual_turnover"), math.inf) <= float(contract["maximum_aggregate_annual_turnover"]),
        "economic_increment": (
            annual_delta >= float(contract["minimum_annualized_increment_vs_router"])
            or cumulative_delta >= (
                float(contract["minimum_cumulative_incremental_cost_multiple"])
                * incremental_cost_return
            )
        ),
    })
    metrics.update({
        "candidate": candidate_name,
        "comparator": comparator_name,
        "candidate_annualized_net_excess": annual_candidate,
        "comparator_annualized_net_excess": annual_comparator,
        "annualized_increment": annual_delta,
        "cumulative_increment": cumulative_delta,
        "incremental_execution_cost": incremental_cost,
        "capital_base": capital_base,
        "incremental_cost_return": incremental_cost_return,
        "fold_deltas": fold_deltas,
        "fold_delta_median": fold_median,
        "disaster_folds": disaster_rows,
        "max_drawdown": _number(candidate.get("max_drawdown")),
        "annual_turnover": _number(candidate.get("annual_turnover")),
        "label_contract_version": label_contract,
    })
    reasons = [key for key, passed in checks.items() if not passed]
    qualified = not reasons
    return {
        "scope": scope, "market": market,
        "status": "qualified" if qualified else "rejected",
        "qualified_for_isolated_paper": qualified,
        "reasons": reasons, "checks": checks, "metrics": metrics,
    }


def apply_paper_candidate_gate(
    repo_root: str | Path,
    *,
    source_report_path: str | Path,
    contract_path: str | Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    source_path = Path(source_report_path)
    if not source_path.is_absolute():
        source_path = root / source_path
    config = Path(contract_path)
    if not config.is_absolute():
        config = root / config
    contract = load_gate_contract(config)
    report = json.loads(source_path.read_text(encoding="utf-8"))
    decisions = [
        evaluate_scope_result(root, report, result, contract=contract)
        for result in report.get("results") or []
    ]
    source_hash = _file_sha256(source_path)
    payload = {
        "schema_version": 1,
        "protocol": contract["protocol"],
        "contract_sha256": contract["contract_sha256"],
        "source_report": str(source_path),
        "source_report_sha256": source_hash,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "known_development_evidence_reassessment": True,
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "second_layer_active_gate_unchanged": True,
        "decisions": decisions,
    }
    run_id = hashlib.sha256(
        f"{contract['contract_sha256']}|{source_hash}".encode("utf-8")
    ).hexdigest()[:16]
    destination = (
        root / "data/research/paper_candidates"
        / contract["protocol"] / f"{run_id}.json"
    )
    write_text_atomic(
        destination,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**payload, "qualification_path": str(destination)}


__all__ = [
    "apply_paper_candidate_gate", "evaluate_scope_result",
    "load_gate_contract",
]
