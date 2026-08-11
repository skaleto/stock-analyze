"""High-level config-first run orchestration for cn_qdii_etf."""

from __future__ import annotations

from datetime import date
from math import ceil
from typing import Any

import pandas as pd

from ...research.strategy_ensemble import (
    load_provider_return_history,
    risk_adjusted_target_weights,
)
from ...utils import write_json
from . import simulator as _sim
from .lookthrough import load_index_profiles
from .strategy import build_signals


QDII_CASH_RESERVE_PCT = 0.02
SELECTION_SNAPSHOT_FILE = "selection_snapshot.json"
UNDERLYING_COMPANY_PREFIX = "underlying_company:"


def _weight_code(value: Any) -> str:
    raw = str(value).split(".", 1)[0]
    return raw.zfill(6) if raw.isdigit() else raw


def _coerce_as_of(as_of: Any) -> date | None:
    if as_of is None or isinstance(as_of, date):
        return as_of
    return date.fromisoformat(str(as_of))


def _apply_underlying_concentration(
    scored: list[dict[str, Any]],
    top_n_by_account: dict[str, int],
    *,
    max_per_index: int = 1,
) -> list[dict[str, Any]]:
    """Prefer distinct underlying indexes before a relaxed fill is needed."""

    by_account: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        by_account.setdefault(str(row.get("account_id") or ""), []).append(dict(row))
    output: list[dict[str, Any]] = []
    for account_id, rows in by_account.items():
        rows.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
        top_n = max(int(top_n_by_account.get(account_id, 5)), 1)
        kept: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("index_key") or f"code:{row.get('code')}")
            if counts.get(key, 0) < max(int(max_per_index), 1):
                kept.append(row)
                counts[key] = counts.get(key, 0) + 1
            else:
                deferred.append(row)
        if len(kept) < top_n:
            # Relax only as far as needed to keep the account investable.
            kept.extend(deferred[: top_n - len(kept)])
        output.extend(kept)
    return output


def _persist_selection_snapshot(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    scored: list[dict[str, Any]],
    top_n_by_account: dict[str, int],
    as_of: date | None,
    allocations: dict[str, dict[str, Any]],
) -> None:
    getter = getattr(provider, "selection_snapshot", None)
    payload = getter() if callable(getter) else {}
    if not isinstance(payload, dict):
        payload = {}
    payload["schema_version"] = max(int(payload.get("schema_version", 1)), 2)
    payload["as_of"] = (as_of or date.today()).isoformat()
    payload.setdefault(
        "universe_hash",
        next((row.get("universe_hash") for row in scored if row.get("universe_hash")), None),
    )
    scopes = payload.setdefault("scopes", {})
    account_scopes = {
        str(account.get("id")): str(account.get("scope") or account.get("id"))
        for account in config.get("accounts", []) or []
    }
    for account_id, scope in account_scopes.items():
        selected = sorted(
            [
                row
                for row in scored
                if str(row.get("account_id") or "") == account_id
                and bool(row.get("allocation_selected", True))
            ],
            key=lambda row: float(row.get("target_weight", 0.0)),
            reverse=True,
        )
        allocation = allocations.get(account_id, {})
        block = scopes.setdefault(scope, {})
        stages = block.setdefault("stages", [])
        stages = [stage for stage in stages if stage.get("key") != "portfolio_target"]
        stages.append(
            {"key": "portfolio_target", "label": "目标持仓", "count": len(selected)}
        )
        block["stages"] = stages
        block["selected_codes"] = list(allocation.get("selected_codes", []))
        block["target_weights"] = dict(allocation.get("target_weights", {}))
        block["optimizer_diagnostics"] = dict(
            allocation.get("optimizer_diagnostics", {})
        )
        block["selected"] = [
            {
                key: row.get(key)
                for key in (
                    "code",
                    "name",
                    "index_key",
                    "theme",
                    "exposure_group",
                    "score",
                    "target_weight",
                    "prediction_applied",
                    "prediction_confidence",
                    "expected_excess_return",
                    "prediction_horizons",
                    "prediction_model_versions",
                    "prediction_fallback_reason",
                    "avg_amount_20",
                    "fund_size_yuan",
                    "discount_premium",
                    "peer_tracking_error_60",
                    "history_start",
                    "history_end",
                    "history_complete",
                )
                if row.get(key) is not None
            }
            for row in selected
        ]
    write_json(store.data_dir / SELECTION_SNAPSHOT_FILE, payload)


def _attach_risk_adjusted_weights(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    scored: list[dict[str, Any]],
    top_n_by_account: dict[str, int],
    max_single_weight: float,
    as_of: date,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    state = store.load_state()
    controls = dict(config.get("portfolio_controls", {}) or {})
    defensive = str(config.get("agent_id") or "").lower() == "claude"
    turnover_penalty = float(controls.get("turnover_penalty", 0.50 if defensive else 0.20))
    min_trade_weight = float(controls.get("min_trade_weight", 0.005 if defensive else 0.002))
    max_turnover = min(
        max(float(controls.get("max_turnover", 1.0)), 0.0),
        1.0,
    )
    output = [dict(row) for row in scored]
    selected_output: list[dict[str, Any]] = []
    allocations: dict[str, dict[str, Any]] = {}
    account_configs = {
        str(account.get("id")): dict(account)
        for account in config.get("accounts", []) or []
    }

    for account_id, top_n in top_n_by_account.items():
        account = state.get("accounts", {}).get(account_id, {})
        account_config = account_configs.get(account_id, {})
        all_account_rows = sorted(
            [
                row
                for row in output
                if str(row.get("account_id") or "") == account_id
            ],
            key=lambda row: float(row.get("score", 0.0)),
            reverse=True,
        )
        account_rows = _controlled_account_pool(
            all_account_rows,
            account.get("positions", {}),
            max(int(top_n), 1),
            controls,
            account_id,
        )
        position_values: dict[str, float] = {}
        account_value = max(float(account.get("cash", 0.0)), 0.0)
        for code, position in account.get("positions", {}).items():
            shares = int(position.get("shares", 0))
            quote = provider.price_snapshot(code, as_of=as_of.isoformat())
            price = quote.close or float(position.get("avg_cost", 0.0))
            market_value = max(shares * float(price), 0.0)
            position_values[_weight_code(code)] = market_value
            account_value += market_value
        retained_position_codes = _retained_position_codes(
            all_account_rows,
            account.get("positions", {}),
            max(int(top_n), 1),
            controls,
            as_of,
        )
        current_weights = {
            code: value / account_value
            for code, value in position_values.items()
            if account_value > 0
        }
        account_frame = _attach_qdii_liquidity_caps(
            pd.DataFrame(account_rows),
            account_value,
            max_single_weight,
            controls,
        )
        (
            account_frame,
            company_exposure_constraints,
            company_exposure_metadata,
        ) = _attach_underlying_company_exposures(account_frame, controls)
        benchmark = str(account_config.get("benchmark") or "")
        history_codes = [
            str(row.get("code") or "") for row in account_rows
        ]
        if benchmark:
            history_codes.append(benchmark)
        return_history = load_provider_return_history(
            provider,
            history_codes,
            as_of=as_of,
        )
        group_constraints: dict[str, float] = {}
        for column, control_key, default in (
            ("index_key", "max_index_weight", 0.40),
            ("country", "max_country_weight", 0.60),
        ):
            cap = float(controls.get(control_key, default))
            if column in account_frame.columns and cap < 1.0:
                group_constraints[column] = cap
        diagnostics: dict[str, object] = {}
        regime_gross_exposure = float(
            account_frame.get(
                "regime_gross_exposure", pd.Series([1.0])
            ).iloc[0]
            if not account_frame.empty
            else 1.0
        )
        optimizer_gross_exposure = min(
            max(regime_gross_exposure, 0.0),
            1.0 - QDII_CASH_RESERVE_PCT,
        )
        weights = risk_adjusted_target_weights(
            account_frame,
            top_n=max(int(top_n), 1),
            max_single_weight=max_single_weight,
            current_weights=current_weights,
            turnover_penalty=turnover_penalty,
            min_trade_weight=min_trade_weight,
            return_history=return_history,
            group_constraints=group_constraints,
            exposure_constraints=company_exposure_constraints,
            risk_aversion=float(
                controls.get("risk_aversion", 1.35 if defensive else 0.90)
            ),
            cost_aversion=float(controls.get("cost_aversion", 1.0)),
            max_turnover=max_turnover,
            benchmark_weights={benchmark: 1.0} if benchmark else None,
            diagnostics=diagnostics,
            gross_exposure=optimizer_gross_exposure,
        )
        original_by_weight_code = {
            _weight_code(row.get("code")): str(row.get("code"))
            for row in account_rows
        }
        target_weights = {
            original_by_weight_code[code]: float(weight)
            for code, weight in weights.items()
            if code in original_by_weight_code and float(weight) > 0.0
        }
        for position_code in account.get("positions", {}):
            target_weights.setdefault(str(position_code), 0.0)
        selected_codes = sorted(
            code for code, weight in target_weights.items() if weight > 0.0
        )
        diagnostics = _map_qdii_diagnostics_codes(
            diagnostics,
            original_by_weight_code,
        )
        diagnostics.update(
            {
                "candidate_pool_size": len(account_rows),
                "max_positions": max(int(top_n), 1),
                "max_turnover_limit": max_turnover,
                "cash_reserve_pct": QDII_CASH_RESERVE_PCT,
                "selected_codes": selected_codes,
                "target_weights": target_weights,
                "liquidity_caps": {
                    str(row["code"]): float(row["liquidity_cap"])
                    for _, row in account_frame.iterrows()
                },
                **company_exposure_metadata,
                **_underlying_company_diagnostics(
                    account_frame,
                    weights,
                    diagnostics,
                ),
            }
        )
        for row in account_rows:
            code = _weight_code(row.get("code"))
            if code in weights:
                selected_row = dict(row)
                selected_row["target_weight"] = float(weights[code])
                selected_row["allocation_selected"] = True
                selected_output.append(selected_row)
        selected_weight_codes = {
            _weight_code(code)
            for code in selected_codes
        }
        selected_scores = [
            float(row.get("score", 0.0))
            for row in selected_output
            if str(row.get("account_id") or "") == account_id
            and bool(row.get("allocation_selected"))
        ]
        retention_score = min(selected_scores, default=0.0) - 1e-9
        for offset, row in enumerate(account_rows, start=1):
            code = _weight_code(row.get("code"))
            if code not in retained_position_codes or code in selected_weight_codes:
                continue
            retained_row = dict(row)
            retained_row["raw_score"] = retained_row.get("score")
            retained_row["score"] = retention_score - offset * 1e-9
            retained_row["allocation_selected"] = False
            retained_row["retention_only"] = True
            selected_output.append(retained_row)
        allocations[account_id] = {
            "selected_codes": selected_codes,
            "target_weights": target_weights,
            "optimizer_diagnostics": diagnostics,
        }
    return selected_output, allocations


def _attach_underlying_company_exposures(
    candidates: pd.DataFrame,
    controls: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, float], dict[str, Any]]:
    frame = candidates.copy()
    cap = min(
        max(float(controls.get("max_underlying_company_weight", 0.10)), 0.0),
        1.0,
    )
    profiles = load_index_profiles()
    company_columns: set[str] = set()
    profile_available: list[bool] = []
    weight_coverage: list[float] = []
    row_exposures: list[dict[str, float]] = []

    for _, row in frame.iterrows():
        profile = profiles.get(str(row.get("index_key") or ""))
        profile_available.append(profile is not None)
        exposures: dict[str, float] = {}
        coverage = 0.0
        for constituent in (profile or {}).get("constituents") or []:
            symbol = str(constituent.get("symbol") or "").strip()
            weight = pd.to_numeric(constituent.get("weight"), errors="coerce")
            if not symbol or pd.isna(weight):
                continue
            numeric_weight = float(weight)
            if not 0.0 <= numeric_weight <= 1.0:
                continue
            column = f"{UNDERLYING_COMPANY_PREFIX}{symbol}"
            exposures[column] = numeric_weight
            company_columns.add(column)
            coverage += numeric_weight
        row_exposures.append(exposures)
        weight_coverage.append(min(coverage, 1.0))

    for column in sorted(company_columns):
        frame[column] = [values.get(column, 0.0) for values in row_exposures]
    frame["_underlying_profile_available"] = profile_available
    frame["_underlying_company_weight_coverage"] = weight_coverage
    constraints = (
        {column: cap for column in sorted(company_columns)}
        if cap < 1.0
        else {}
    )
    metadata = {
        "company_exposure_cap": cap,
        "company_exposure_constraint_count": len(constraints),
        "company_exposure_constraint_status": (
            "enforced_measured_exposure" if constraints else "unavailable"
        ),
    }
    return frame, constraints, metadata


def _underlying_company_diagnostics(
    candidates: pd.DataFrame,
    weights: dict[str, float],
    optimizer_diagnostics: dict[str, object],
) -> dict[str, Any]:
    indexed = candidates.copy()
    indexed["_weight_code"] = indexed["code"].map(_weight_code)
    indexed = indexed.drop_duplicates("_weight_code").set_index("_weight_code")
    gross = sum(max(float(weight), 0.0) for weight in weights.values())
    profile_coverage = 0.0
    company_weight_coverage = 0.0
    if gross > 0.0:
        for code, weight in weights.items():
            if code not in indexed.index:
                continue
            row = indexed.loc[code]
            allocation = max(float(weight), 0.0) / gross
            profile_coverage += allocation * float(
                bool(row.get("_underlying_profile_available", False))
            )
            company_weight_coverage += allocation * float(
                row.get("_underlying_company_weight_coverage", 0.0) or 0.0
            )
    measured = {
        str(key): float(value)
        for key, value in dict(
            optimizer_diagnostics.get("exposures", {}) or {}
        ).items()
        if str(key).startswith(UNDERLYING_COMPANY_PREFIX)
    }
    return {
        "underlying_profile_coverage": profile_coverage,
        "underlying_company_weight_coverage": company_weight_coverage,
        "max_measured_company_exposure": max(measured.values(), default=0.0),
    }


def _controlled_account_pool(
    ranked_rows: list[dict[str, Any]],
    positions: dict[str, Any],
    top_n: int,
    controls: dict[str, Any],
    account_id: str,
) -> list[dict[str, Any]]:
    multiple = max(int(controls.get("candidate_pool_multiple", 3)), 3)
    pool = [dict(row) for row in ranked_rows[: top_n * multiple]]
    included = {_weight_code(row.get("code")) for row in pool}
    held_codes = {_weight_code(code) for code in positions}
    for row in ranked_rows:
        code = _weight_code(row.get("code"))
        if code in held_codes and code not in included:
            pool.append(dict(row))
            included.add(code)
    minimum_score = min(
        (float(row.get("score", 0.0)) for row in ranked_rows),
        default=0.0,
    )
    for offset, (raw_code, position) in enumerate(positions.items(), start=1):
        code = _weight_code(raw_code)
        if code in included:
            continue
        pool.append(
            {
                "code": str(raw_code),
                "name": position.get("name", ""),
                "account_id": account_id,
                "score": minimum_score - offset * 1e-6,
                "reason": "held_position_outside_signal_universe",
                "low_volatility_60": float(
                    position.get("expected_volatility") or 0.20
                ),
                "avg_amount_20": position.get("avg_amount_20")
                or position.get("avg_daily_amount"),
                "index_key": position.get("index_key") or "unclassified",
                "country": position.get("country") or "unclassified",
                "synthetic_holding": True,
            }
        )
        included.add(code)
    return pool


def _retained_position_codes(
    ranked_rows: list[dict[str, Any]],
    positions: dict[str, Any],
    top_n: int,
    controls: dict[str, Any],
    as_of: date,
) -> set[str]:
    rank_by_code = {
        _weight_code(row.get("code")): rank
        for rank, row in enumerate(ranked_rows, start=1)
    }
    retention_count = max(
        top_n,
        ceil(top_n * (1.0 + max(float(controls.get("hold_buffer_pct", 0.0)), 0.0))),
    )
    max_holding_days = controls.get("max_holding_days")
    retained: set[str] = set()
    for raw_code, position in positions.items():
        code = _weight_code(raw_code)
        if rank_by_code.get(code, retention_count + 1) > retention_count:
            continue
        if max_holding_days is not None and position.get("hold_since"):
            try:
                age = (as_of - date.fromisoformat(str(position["hold_since"]))).days
            except ValueError:
                age = 0
            if age >= int(max_holding_days):
                continue
        retained.add(code)
    return retained


def _attach_qdii_liquidity_caps(
    candidates: pd.DataFrame,
    account_value: float,
    max_single_weight: float,
    controls: dict[str, Any],
) -> pd.DataFrame:
    frame = candidates.copy()
    frame["liquidity_cap"] = float(max_single_weight)
    participation = min(
        max(float(controls.get("max_liquidity_participation", 0.05)), 0.0),
        1.0,
    )
    if (
        "avg_amount_20" in frame.columns
        and account_value > 0
        and participation > 0
    ):
        amounts = pd.to_numeric(frame["avg_amount_20"], errors="coerce")
        observed_caps = amounts * participation / float(account_value)
        valid = observed_caps.notna() & observed_caps.gt(0.0)
        frame.loc[valid, "liquidity_cap"] = observed_caps.loc[valid].clip(
            upper=float(max_single_weight)
        )
    return frame


def _map_qdii_diagnostics_codes(
    diagnostics: dict[str, object],
    original_by_weight_code: dict[str, str],
) -> dict[str, object]:
    output = dict(diagnostics)
    risk = output.get("risk_contributions")
    if isinstance(risk, dict):
        output["risk_contributions"] = {
            original_by_weight_code.get(str(code), str(code)): value
            for code, value in risk.items()
        }
    return output


def _persist_order_allocation(
    store: Any,
    orders: list[dict[str, Any]],
    allocations: dict[str, dict[str, Any]],
    run_id: str | None,
) -> list[dict[str, Any]]:
    signatures = {
        (
            str(order.get("account_id") or ""),
            str(order.get("code") or ""),
            str(order.get("side") or ""),
            str(order.get("trade_date") or ""),
        )
        for order in orders
    }
    enriched: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for raw in store.read_pending():
        order = dict(raw)
        signature = (
            str(order.get("account_id") or ""),
            str(order.get("code") or ""),
            str(order.get("side") or ""),
            str(order.get("trade_date") or ""),
        )
        if signature in signatures:
            allocation = allocations.get(str(order.get("account_id") or ""), {})
            order.update(
                {
                    "run_id": run_id,
                    "allocation_selected_codes": list(
                        allocation.get("selected_codes", [])
                    ),
                    "allocation_target_weights": dict(
                        allocation.get("target_weights", {})
                    ),
                    "optimizer_diagnostics": dict(
                        allocation.get("optimizer_diagnostics", {})
                    ),
                }
            )
            enriched.append(order)
        pending.append(order)
    store.write_pending(pending)
    return enriched


def generate_rebalance_orders(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    as_of: Any = None,
    run_id: str | None = None,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    d = _coerce_as_of(as_of)
    scored = build_signals(config, provider, as_of=d, repo_root=_ignored.get("repo_root"))
    accounts = config.get("accounts", []) or []
    top_n_by_account = {
        str(account["id"]): int(account.get("top_n", 5))
        for account in accounts
    }
    max_single_weight = float((config.get("trading", {}) or {}).get("max_single_weight", 0.20))
    portfolio_controls = dict(config.get("portfolio_controls", {}) or {})
    scored, allocations = _attach_risk_adjusted_weights(
        config,
        store,
        provider,
        scored,
        top_n_by_account,
        max_single_weight,
        d or date.today(),
    )
    _persist_selection_snapshot(
        config,
        store,
        provider,
        scored,
        top_n_by_account,
        d,
        allocations,
    )
    allocation_top_n_by_account = {
        account_id: max(len(allocation.get("selected_codes", [])), 1)
        for account_id, allocation in allocations.items()
    }
    top_n = max(allocation_top_n_by_account.values(), default=1)
    investable_fraction = 1.0 - QDII_CASH_RESERVE_PCT
    execution_scored = [
        {
            **row,
            "target_weight": (
                min(float(row.get("target_weight", 0.0)) / investable_fraction, 1.0)
                if bool(row.get("allocation_selected", True))
                and investable_fraction > 0
                else row.get("target_weight")
            ),
        }
        for row in scored
    ]
    orders = _sim.generate_rebalance_orders(
        store,
        provider,
        execution_scored,
        as_of=d,
        top_n=top_n,
        max_single_weight=min(max_single_weight / investable_fraction, 1.0),
        top_n_by_account=allocation_top_n_by_account,
        hold_buffer_pct=float(portfolio_controls.get("hold_buffer_pct", 0.0)),
        max_holding_days=(
            int(portfolio_controls["max_holding_days"])
            if portfolio_controls.get("max_holding_days") is not None
            else None
        ),
        cash_reserve_pct=QDII_CASH_RESERVE_PCT,
        min_trade_weight=float(
            portfolio_controls.get(
                "min_trade_weight",
                0.005 if str(config.get("agent_id") or "").lower() == "claude" else 0.002,
            )
        ),
    )
    return _persist_order_allocation(store, orders, allocations, run_id)


def execute_due_orders(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    *,
    as_of: Any = None,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    return _sim.execute_due_orders(store, provider, as_of=_coerce_as_of(as_of))


def update_nav(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    *,
    as_of: Any = None,
    notes: str | None = None,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    return _sim.update_nav(store, provider, as_of=_coerce_as_of(as_of))


__all__ = ["generate_rebalance_orders", "execute_due_orders", "update_nav"]
