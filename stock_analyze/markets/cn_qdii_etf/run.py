"""High-level config-first run orchestration for cn_qdii_etf."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from ...research.strategy_ensemble import (
    load_provider_return_history,
    risk_adjusted_target_weights,
)
from ...utils import write_json
from . import simulator as _sim
from .strategy import build_signals


QDII_CASH_RESERVE_PCT = 0.02
SELECTION_SNAPSHOT_FILE = "selection_snapshot.json"


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
) -> None:
    getter = getattr(provider, "selection_snapshot", None)
    payload = getter() if callable(getter) else {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", 1)
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
        ranked = sorted(
            [row for row in scored if str(row.get("account_id") or "") == account_id],
            key=lambda row: float(row.get("score", 0.0)),
            reverse=True,
        )
        selected = ranked[: max(int(top_n_by_account.get(account_id, 5)), 1)]
        block = scopes.setdefault(scope, {})
        stages = block.setdefault("stages", [])
        stages = [stage for stage in stages if stage.get("key") != "portfolio_target"]
        stages.append(
            {"key": "portfolio_target", "label": "目标持仓", "count": len(selected)}
        )
        block["stages"] = stages
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
) -> list[dict[str, Any]]:
    state = store.load_state()
    controls = dict(config.get("portfolio_controls", {}) or {})
    defensive = str(config.get("agent_id") or "").lower() == "claude"
    turnover_penalty = float(controls.get("turnover_penalty", 0.50 if defensive else 0.20))
    min_trade_weight = float(controls.get("min_trade_weight", 0.005 if defensive else 0.002))
    output = [dict(row) for row in scored]

    for account_id, top_n in top_n_by_account.items():
        account = state.get("accounts", {}).get(account_id, {})
        position_values: dict[str, float] = {}
        account_value = max(float(account.get("cash", 0.0)), 0.0)
        for code, position in account.get("positions", {}).items():
            shares = int(position.get("shares", 0))
            quote = provider.price_snapshot(code, as_of=as_of.isoformat())
            price = quote.close or float(position.get("avg_cost", 0.0))
            market_value = max(shares * float(price), 0.0)
            position_values[_weight_code(code)] = market_value
            account_value += market_value
        current_weights = {
            code: value / account_value
            for code, value in position_values.items()
            if account_value > 0
        }
        account_rows = [row for row in output if str(row.get("account_id") or "") == account_id]
        account_frame = pd.DataFrame(account_rows)
        return_history = load_provider_return_history(
            provider,
            [str(row.get("code") or "") for row in account_rows],
            as_of=as_of,
        )
        group_constraints = {
            column: cap
            for column, cap in (("index_key", 0.40), ("country", 0.60))
            if column in account_frame.columns
        }
        weights = risk_adjusted_target_weights(
            account_frame,
            top_n=max(int(top_n), 1),
            max_single_weight=max_single_weight,
            current_weights=current_weights,
            turnover_penalty=turnover_penalty,
            min_trade_weight=min_trade_weight,
            return_history=return_history,
            group_constraints=group_constraints,
            risk_aversion=1.35 if defensive else 0.90,
            gross_exposure=float(
                account_frame.get("regime_gross_exposure", pd.Series([1.0])).iloc[0]
                if not account_frame.empty
                else 1.0
            ),
        )
        for row in account_rows:
            code = _weight_code(row.get("code"))
            if code in weights:
                row["target_weight"] = weights[code]
    return output


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
    top_n = max(top_n_by_account.values(), default=5)
    max_single_weight = float((config.get("trading", {}) or {}).get("max_single_weight", 0.20))
    portfolio_controls = dict(config.get("portfolio_controls", {}) or {})
    scored = _apply_underlying_concentration(
        scored,
        top_n_by_account,
        max_per_index=int(portfolio_controls.get("max_etfs_per_index", 1)),
    )
    scored = _attach_risk_adjusted_weights(
        config,
        store,
        provider,
        scored,
        top_n_by_account,
        max_single_weight,
        d or date.today(),
    )
    _persist_selection_snapshot(config, store, provider, scored, top_n_by_account, d)
    return _sim.generate_rebalance_orders(
        store,
        provider,
        scored,
        as_of=d,
        top_n=top_n,
        max_single_weight=max_single_weight,
        top_n_by_account=top_n_by_account,
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
