"""High-level config-first run orchestration for cn_qdii_etf."""

from __future__ import annotations

from datetime import date
from typing import Any

from . import simulator as _sim
from .strategy import build_signals


def _coerce_as_of(as_of: Any) -> date | None:
    if as_of is None or isinstance(as_of, date):
        return as_of
    return date.fromisoformat(str(as_of))


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
    top_n = max((int(a.get("top_n", 5)) for a in accounts), default=5)
    max_single_weight = float((config.get("trading", {}) or {}).get("max_single_weight", 0.20))
    return _sim.generate_rebalance_orders(
        store,
        provider,
        scored,
        as_of=d,
        top_n=top_n,
        max_single_weight=max_single_weight,
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
