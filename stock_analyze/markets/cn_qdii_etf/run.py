"""High-level config-first run orchestration for cn_qdii_etf."""

from __future__ import annotations

from datetime import date
from typing import Any

from ...utils import write_json
from . import simulator as _sim
from .strategy import build_signals


QDII_CASH_RESERVE_PCT = 0.02
SELECTION_SNAPSHOT_FILE = "selection_snapshot.json"


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
