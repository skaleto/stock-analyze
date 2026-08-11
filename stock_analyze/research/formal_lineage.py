"""Projection helpers for the formal paper-trading decision lifecycle."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .attribution import DailyAttributionInput, attribute_daily_pnl
from .lineage import ResearchLineageStore
from .strategy_ensemble import load_model_policy


LINEAGE_DATABASE = Path("data/shared/research_lineage.sqlite3")


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, set)) else False:
        return None
    return value


def _profile(config: Mapping[str, Any]) -> str:
    return "defensive" if str(config.get("agent_id") or "").lower() == "claude" else "trend"


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _candidate_model_versions(candidate: Mapping[str, Any]) -> dict[str, str]:
    raw_horizons = candidate.get("prediction_horizons")
    raw_versions = candidate.get("prediction_model_versions")
    if isinstance(raw_versions, Mapping):
        return {
            str(horizon): str(version)
            for horizon, version in raw_versions.items()
            if str(version)
        }
    horizons = [
        part.strip()
        for part in str(raw_horizons or "").split(",")
        if part.strip()
    ]
    versions = [
        part.strip()
        for part in str(raw_versions or "").split(",")
        if part.strip()
    ]
    if len(horizons) != len(versions):
        return {}
    return dict(zip(horizons, versions))


def _model_decision_evidence(
    candidates: Iterable[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in candidates
        if str(row.get("code") or row.get("security_code") or "")
        != "__broadcast__"
    ]
    evidence_rows = [
        row for row in rows
        if _as_bool(row.get("prediction_evidence_present"))
    ]
    applied = [
        row for row in evidence_rows
        if _as_bool(row.get("prediction_applied"))
    ]
    model_versions: dict[str, str] = {}
    for row in applied:
        for horizon, version in _candidate_model_versions(row).items():
            existing = model_versions.get(horizon)
            if existing is None:
                model_versions[horizon] = version
            elif version not in existing.split("|"):
                model_versions[horizon] = "|".join(
                    sorted({*existing.split("|"), version})
                )
    if applied:
        status = "active"
        fallback_reason = ""
    elif not evidence_rows:
        status = str(policy.get("missing_behavior") or "rule_only")
        fallback_reason = "prediction_application_evidence_missing"
    else:
        status = str(policy.get("missing_behavior") or "rule_only")
        reasons = sorted({
            str(row.get("prediction_fallback_reason") or "")
            for row in evidence_rows
            if str(row.get("prediction_fallback_reason") or "")
        })
        fallback_reason = (
            "|".join(reasons)
            if reasons
            else "no_candidate_prediction_applied"
        )
    return {
        "model_role": str(policy.get("required_role") or "ranker"),
        "model_versions": model_versions,
        "model_policy_status": status,
        "model_applied_candidates": len(applied),
        "model_candidate_coverage": (
            len(applied) / len(rows)
            if rows
            else 0.0
        ),
        "model_fallback_reason": fallback_reason,
    }


def _decision_id(run_id: str, account_id: str) -> str:
    return f"{run_id}:{account_id}"


def _flatten_orders(generated: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in generated:
        parent = dict(raw)
        nested = parent.get("orders")
        if isinstance(nested, list):
            for order in nested:
                rows.append(
                    {
                        **dict(order),
                        "account_id": str(
                            order.get("account_id") or parent.get("account_id") or ""
                        ),
                        "signal_date": parent.get("signal_date"),
                        "execute_after": parent.get("execute_after"),
                        "strategy_id": parent.get("strategy_id"),
                        "optimizer_diagnostics": parent.get(
                            "optimizer_diagnostics", {}
                        ),
                    }
                )
        else:
            rows.append(parent)
    return rows


def _a_share_candidates(store: Any, run_id: str) -> list[dict[str, Any]]:
    frame = store.read_factor_run(run_id)
    if frame.empty or {"account_id", "code"}.difference(frame.columns):
        return []
    rows: list[dict[str, Any]] = []
    for (account_id, code), group in frame.groupby(
        ["account_id", "code"], sort=True, dropna=False
    ):
        selected = bool(group.get("selected", pd.Series(False, index=group.index)).fillna(False).any())
        valid = bool(group.get("valid", pd.Series(True, index=group.index)).fillna(False).any())
        contributions = {
            str(row["factor"]): float(row["contribution"])
            for row in group[["factor", "contribution"]].dropna().to_dict(orient="records")
        }
        first = group.iloc[0]
        prediction_columns = {
            "prediction_applied",
            "prediction_confidence",
            "expected_excess_return",
            "prediction_horizons",
            "prediction_model_versions",
            "prediction_fallback_reason",
        }
        rows.append(
            {
                "account_id": str(account_id),
                "code": str(code).zfill(6),
                "eligible": valid,
                "selected": selected,
                "rejection_reason": "" if valid else "insufficient_factor_coverage",
                "rank_score": float(sum(contributions.values())),
                "factor_contributions": contributions,
                "signal_date": str(first.get("signal_date") or ""),
                "prediction_evidence_present": bool(
                    prediction_columns.intersection(group.columns)
                ),
                **{
                    column: first.get(column)
                    for column in prediction_columns
                    if column in group.columns
                },
            }
        )
    return rows


def _qdii_candidates(
    store: Any,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    path = Path(store.data_dir) / "selection_snapshot.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    scopes = payload.get("scopes") or {}
    rows: list[dict[str, Any]] = []
    for account in config.get("accounts", []) or []:
        account_id = str(account.get("id") or "")
        scope = str(account.get("scope") or account_id)
        block = scopes.get(scope) or {}
        ranked = {
            str(item.get("code") or ""): dict(item)
            for item in block.get("ranked", []) or []
        }
        selected = {
            str(item.get("code") or ""): dict(item)
            for item in block.get("selected", []) or []
        }
        for item in block.get("candidate_evaluations", []) or []:
            code = str(item.get("code") or "")
            detail = {**ranked.get(code, {}), **selected.get(code, {})}
            prediction_columns = {
                "prediction_applied",
                "prediction_confidence",
                "expected_excess_return",
                "prediction_horizons",
                "prediction_model_versions",
                "prediction_fallback_reason",
            }
            rows.append(
                {
                    "account_id": account_id,
                    "code": code,
                    "eligible": bool(item.get("eligible")),
                    "selected": code in set(block.get("selected_codes") or []),
                    "rejection_reason": str(item.get("rejection_reason") or ""),
                    "rank_score": detail.get("score"),
                    "name": item.get("name") or detail.get("name"),
                    "event_evidence": item.get("unconfirmed_hard_events") or [],
                    "universe_hash": block.get("universe_hash")
                    or payload.get("universe_hash"),
                    "prediction_evidence_present": bool(
                        prediction_columns.intersection(detail)
                    ),
                    **{
                        column: detail.get(column)
                        for column in prediction_columns
                        if column in detail
                    },
                }
            )
    return rows


def _account_allocation_context(
    *,
    generated: Iterable[Mapping[str, Any]],
    market: str,
    store: Any,
    account_id: str,
    account: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    target_weights: dict[str, float] = {}
    diagnostics: dict[str, Any] = {}
    for raw in generated:
        if str(raw.get("account_id") or "") != account_id:
            continue
        for key in ("target_weights", "allocation_target_weights"):
            weights = raw.get(key)
            if isinstance(weights, Mapping):
                target_weights.update(
                    {
                        str(code): float(weight)
                        for code, weight in weights.items()
                    }
                )
        raw_diagnostics = raw.get("optimizer_diagnostics")
        if isinstance(raw_diagnostics, Mapping) and raw_diagnostics:
            diagnostics = dict(raw_diagnostics)
    if market != "cn_qdii_etf":
        return target_weights, diagnostics
    path = Path(store.data_dir) / "selection_snapshot.json"
    if not path.exists():
        return target_weights, diagnostics
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return target_weights, diagnostics
    scope = str(account.get("scope") or account_id)
    block = ((payload.get("scopes") or {}).get(scope) or {})
    if not target_weights and isinstance(block.get("target_weights"), Mapping):
        target_weights.update(
            {
                str(code): float(weight)
                for code, weight in block["target_weights"].items()
            }
        )
    if not diagnostics and isinstance(block.get("optimizer_diagnostics"), Mapping):
        diagnostics = dict(block["optimizer_diagnostics"])
    return target_weights, diagnostics


def record_formal_decision(
    *,
    repo_root: str | Path,
    market: str,
    config: Mapping[str, Any],
    store: Any,
    run_id: str,
    as_of: str,
    generated: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append decision, candidate, allocation, and order records."""

    root = Path(repo_root)
    lineage = ResearchLineageStore(root / LINEAGE_DATABASE)
    state_hash = _file_hash(Path(store.state_path))
    feature_path = (
        Path(store.data_dir) / "factor_runs" / f"{run_id}.csv"
        if market == "a_share"
        else Path(store.data_dir) / "selection_snapshot.json"
    )
    feature_hash = _file_hash(feature_path)
    candidates = (
        _a_share_candidates(store, run_id)
        if market == "a_share"
        else _qdii_candidates(store, config)
    )
    generated_rows = [dict(raw) for raw in generated]
    orders = _flatten_orders(generated_rows)
    accounts = {
        str(account.get("id") or ""): dict(account)
        for account in config.get("accounts", []) or []
    }
    account_ids = sorted(
        set(accounts)
        | {str(row.get("account_id") or "") for row in candidates}
        | {str(row.get("account_id") or "") for row in orders}
    )
    policy = load_model_policy(root, _profile(config)) or {}
    inserted = {
        "decision_runs": 0,
        "candidate_evaluations": 0,
        "target_allocations": 0,
        "orders": 0,
    }
    for account_id in account_ids:
        if not account_id:
            continue
        decision_id = _decision_id(run_id, account_id)
        account = accounts.get(account_id, {})
        account_candidates = [
            row for row in candidates
            if str(row.get("account_id") or "") == account_id
        ]
        model_evidence = _model_decision_evidence(
            account_candidates,
            policy,
        )
        target_weights, optimizer_diagnostics = _account_allocation_context(
            generated=generated_rows,
            market=market,
            store=store,
            account_id=account_id,
            account=account,
        )
        decision = _json_value(
            {
                "decision_run_id": decision_id,
                "source_run_id": run_id,
                "agent_id": str(config.get("agent_id") or ""),
                "market": market,
                "strategy_id": str(config.get("strategy_id") or ""),
                "account_id": account_id,
                "scope": account.get("scope"),
                "as_of": str(as_of),
                "account_state_hash": state_hash,
                "feature_snapshot_id": feature_hash or str(as_of),
                "feature_snapshot_path": str(feature_path),
                "horizon_weights": policy.get("horizon_weights") or {},
                **model_evidence,
                "max_prediction_age_days": policy.get(
                    "max_prediction_age_days"
                ),
                "model_missing_behavior": policy.get("missing_behavior"),
                "optimizer_diagnostics": optimizer_diagnostics,
                "config_hash": hashlib.sha256(
                    json.dumps(
                        _json_value(dict(config)),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
        inserted["decision_runs"] += lineage.append_decision_runs(decision)
        account_orders = [
            row for row in orders
            if str(row.get("account_id") or "") == account_id
        ]
        candidate_by_code = {
            str(row.get("code") or ""): dict(row)
            for row in account_candidates
        }
        for order in account_orders:
            code = str(order.get("code") or "")
            candidate_by_code.setdefault(
                code,
                {
                    "account_id": account_id,
                    "code": code,
                    "eligible": True,
                    "selected": False,
                    "rejection_reason": "",
                    "decision_context": "existing_position_rebalance",
                },
            )
        for code in target_weights:
            candidate_by_code.setdefault(
                str(code),
                {
                    "account_id": account_id,
                    "code": str(code),
                    "eligible": True,
                    "selected": True,
                    "rejection_reason": "",
                    "decision_context": "target_allocation",
                    "prediction_evidence_present": False,
                },
            )
        candidate_ids: dict[str, str] = {}
        for code, candidate in sorted(candidate_by_code.items()):
            candidate_id = _stable_id("candidate", decision_id, code)
            candidate_ids[code] = candidate_id
            payload = _json_value(
                {
                    **candidate,
                    "prediction_model_versions": _candidate_model_versions(
                        candidate
                    ),
                    "candidate_evaluation_id": candidate_id,
                    "decision_run_id": decision_id,
                    "security_code": code,
                }
            )
            inserted["candidate_evaluations"] += (
                lineage.append_candidate_evaluations(payload)
            )

        allocation_ids: dict[str, str] = {}
        for order in account_orders:
            target_weights.setdefault(
                str(order.get("code") or ""),
                float(order.get("target_weight") or 0.0),
            )
        for code, target_weight in sorted(target_weights.items()):
            candidate_id = candidate_ids.get(code)
            if not candidate_id:
                continue
            allocation_id = _stable_id("allocation", decision_id, code)
            allocation_ids[code] = allocation_id
            inserted["target_allocations"] += lineage.append_target_allocations(
                _json_value(
                    {
                        "target_allocation_id": allocation_id,
                        "decision_run_id": decision_id,
                        "candidate_evaluation_id": candidate_id,
                        "security_code": code,
                        "target_weight": target_weight,
                        "expected_cost": optimizer_diagnostics.get(
                            "expected_cost"
                        ),
                        "risk_contribution": (
                            optimizer_diagnostics.get("risk_contributions") or {}
                        ).get(code),
                        "binding_constraints": optimizer_diagnostics.get(
                            "binding_constraints", []
                        ),
                        "optimizer_fallback_reason": optimizer_diagnostics.get(
                            "fallback_reason"
                        ),
                    }
                )
            )
        for order in account_orders:
            code = str(order.get("code") or "")
            allocation_id = allocation_ids.get(code)
            if allocation_id is None:
                continue
            order_id = _stable_id(
                "order",
                decision_id,
                code,
                order.get("side"),
                order.get("trade_date") or order.get("execute_after"),
            )
            inserted["orders"] += lineage.append_orders(
                _json_value(
                    {
                        **order,
                        "order_id": order_id,
                        "decision_run_id": decision_id,
                        "target_allocation_id": allocation_id,
                        "security_code": code,
                        "quantity": order.get("shares", order.get("delta_shares")),
                    }
                )
            )
    return {
        "status": "complete",
        "database": str(root / LINEAGE_DATABASE),
        "inserted": inserted,
    }


def record_formal_fills(
    *,
    repo_root: str | Path,
    market: str,
    agent_id: str,
    trades: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Match realized trades to the latest compatible immutable order."""

    lineage = ResearchLineageStore(Path(repo_root) / LINEAGE_DATABASE)
    decision_rows = {
        str(row["decision_run_id"]): row
        for row in lineage.query("decision_runs")
        if str(row.get("market") or "") == str(market)
        and str(row.get("agent_id") or "") == str(agent_id)
    }
    orders = [
        row for row in lineage.query("orders")
        if str(row.get("decision_run_id") or "") in decision_rows
    ]
    filled_order_ids = {
        str(row.get("order_id") or "")
        for row in lineage.query("fills")
    }
    inserted = 0
    unmatched: list[dict[str, Any]] = []
    decision_ids: dict[str, str] = {}
    for trade in trades:
        account_id = str(trade.get("account_id") or "")
        code = str(trade.get("code") or "")
        side = str(trade.get("side") or "")
        compatible = [
            order for order in orders
            if str(order.get("account_id") or "") == account_id
            and str(order.get("security_code") or order.get("code") or "") == code
            and str(order.get("side") or "") == side
            and str(order.get("order_id") or "") not in filled_order_ids
            and str(decision_rows[str(order["decision_run_id"])].get("as_of") or "")
            <= str(trade.get("trade_date") or "")
        ]
        compatible.sort(
            key=lambda order: (
                str(decision_rows[str(order["decision_run_id"])].get("as_of") or ""),
                str(order.get("order_id") or ""),
            ),
            reverse=True,
        )
        if not compatible:
            unmatched.append(dict(trade))
            continue
        order = compatible[0]
        order_id = str(order["order_id"])
        decision_id = str(order["decision_run_id"])
        fill_id = _stable_id(
            "fill",
            order_id,
            trade.get("trade_date"),
            trade.get("shares"),
            trade.get("price"),
            trade.get("net_amount"),
        )
        inserted += lineage.append_fills(
            _json_value(
                {
                    **dict(trade),
                    "fill_id": fill_id,
                    "order_id": order_id,
                    "decision_run_id": decision_id,
                    "filled_at": trade.get("trade_date"),
                    "quantity": trade.get("shares"),
                    "fees": sum(
                        float(trade.get(key) or 0.0)
                        for key in ("commission", "stamp_tax", "slippage")
                    ),
                }
            )
        )
        filled_order_ids.add(order_id)
        decision_ids[account_id] = decision_id
    return {
        "status": "complete" if not unmatched else "partial",
        "inserted": inserted,
        "unmatched": len(unmatched),
        "unmatched_trades": unmatched,
        "decision_ids": decision_ids,
    }


def record_nav_attribution(
    *,
    repo_root: str | Path,
    market: str,
    agent_id: str,
    store: Any,
    nav_rows: Iterable[Mapping[str, Any]],
    trades: Iterable[Mapping[str, Any]],
    decision_ids: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a reconciled, explicitly partial attribution from formal NAV."""

    lineage = ResearchLineageStore(Path(repo_root) / LINEAGE_DATABASE)
    nav_history = store.read_nav()
    trade_rows = [dict(row) for row in trades]
    appended = 0
    unavailable: list[str] = []
    for current in nav_rows:
        account_id = str(current.get("account_id") or "")
        as_of = str(current.get("date") or current.get("trade_date") or "")
        history = nav_history.loc[
            nav_history.get("account_id", pd.Series(dtype=str)).astype(str).eq(account_id)
        ].copy()
        if history.empty or "date" not in history.columns:
            unavailable.append(f"{account_id}:nav_history")
            continue
        previous = history.loc[history["date"].astype(str).lt(as_of)].tail(1)
        if previous.empty:
            unavailable.append(f"{account_id}:previous_nav")
            continue
        prior = previous.iloc[0]
        opening_nav = float(prior.get("total_value") or 0.0)
        closing_nav = float(current.get("total_value") or 0.0)
        if opening_nav <= 0.0:
            unavailable.append(f"{account_id}:opening_nav")
            continue
        prior_market_value = float(
            prior.get("market_value", prior.get("positions_value", 0.0)) or 0.0
        )
        current_market_value = float(
            current.get("market_value", current.get("positions_value", 0.0)) or 0.0
        )
        account_trades = [
            row for row in trade_rows
            if str(row.get("account_id") or "") == account_id
        ]
        realized_fees = sum(
            float(row.get(key) or 0.0)
            for row in account_trades
            for key in ("commission", "stamp_tax", "slippage")
        )
        observed_net_pnl = closing_nav - opening_nav
        gross_position_pnl = observed_net_pnl + realized_fees
        position_return = (
            gross_position_pnl / prior_market_value
            if prior_market_value > 0.0
            else 0.0
        )
        benchmark_return = None
        previous_benchmark = float(prior.get("benchmark_close") or 0.0)
        current_benchmark = float(current.get("benchmark_close") or 0.0)
        if previous_benchmark > 0.0 and current_benchmark > 0.0:
            benchmark_return = current_benchmark / previous_benchmark - 1.0
        decision_id = str((decision_ids or {}).get(account_id) or "")
        if not decision_id:
            compatible = [
                row for row in lineage.query("decision_runs")
                if str(row.get("market") or "") == market
                and str(row.get("agent_id") or "") == agent_id
                and str(row.get("account_id") or "") == account_id
                and str(row.get("as_of") or "") <= as_of
            ]
            compatible.sort(key=lambda row: str(row.get("as_of") or ""), reverse=True)
            decision_id = str(compatible[0]["decision_run_id"]) if compatible else ""
        if not decision_id:
            unavailable.append(f"{account_id}:decision_run")
            continue
        decision_rows = lineage.query(
            "decision_runs",
            {"decision_run_id": decision_id},
            limit=1,
        )
        if not decision_rows:
            unavailable.append(f"{account_id}:decision_run_payload")
            continue
        decision = decision_rows[0]
        model_policy_status = str(
            decision.get("model_policy_status") or "rule_only"
        )
        raw_model_versions = decision.get("model_versions")
        model_versions = (
            {
                str(horizon): str(version)
                for horizon, version in raw_model_versions.items()
                if str(version)
            }
            if isinstance(raw_model_versions, Mapping)
            else {}
        )
        before_weight = min(max(prior_market_value / opening_nav, 0.0), 1.0)
        after_weight = (
            min(max(current_market_value / closing_nav, 0.0), 1.0)
            if closing_nav > 0.0
            else 0.0
        )
        declared_missing = [
            "industry_attribution",
            "factor_attribution",
            "sizing_attribution",
            "timing_attribution",
            "constraint_attribution",
        ]
        if model_policy_status in {"active", "champion", "shadow"}:
            declared_missing.append("model_selection_attribution")
        if market == "cn_qdii_etf":
            declared_missing.extend(["fx_attribution", "premium_attribution"])
        if benchmark_return is None:
            declared_missing.append("benchmark_return")
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market=market,
                as_of=as_of,
                opening_nav=opening_nav,
                before_weights={"__PORTFOLIO__": before_weight},
                after_weights={"__PORTFOLIO__": after_weight},
                security_returns={"__PORTFOLIO__": position_return},
                benchmark_returns=(
                    {"benchmark": benchmark_return}
                    if benchmark_return is not None
                    else None
                ),
                benchmark_exposures=(
                    {"__PORTFOLIO__": {"benchmark": 1.0}}
                    if benchmark_return is not None
                    else None
                ),
                realized_fees=realized_fees,
                observed_net_pnl=observed_net_pnl,
                strategy_id=str(decision.get("strategy_id") or ""),
                account_id=account_id,
                model_policy_status=model_policy_status,
                model_versions=model_versions,
                declared_unavailable_inputs=tuple(declared_missing),
            )
        )
        # NAV-only attribution has a synthetic portfolio instrument; retain
        # the portfolio summary and avoid duplicating that synthetic key as a
        # security detail row.
        rows = result.to_lineage_rows(decision_id)[:1]
        for row in rows:
            row["account_id"] = account_id
        appended += lineage.append_pnl_attributions(rows)
    return {
        "status": "complete" if not unavailable else "partial",
        "inserted": appended,
        "unavailable": unavailable,
    }


__all__ = [
    "LINEAGE_DATABASE",
    "record_formal_decision",
    "record_formal_fills",
    "record_nav_attribution",
]
