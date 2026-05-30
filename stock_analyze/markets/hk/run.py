"""HK high-level run orchestration (config-driven CLI contract).

These wrap the low-level settlement primitives in :mod:`.simulator`
(which operate on a pre-scored list / the store directly) with the same
config-first signatures the CLI dispatches generically across markets —
mirroring ``markets.a_share.simulator``'s public functions:

    generate_rebalance_orders(config, store, provider, as_of=, run_id=)
    execute_due_orders(config, store, provider, as_of=)
    update_nav(config, store, provider, as_of=, notes=)

The low-level functions stay in :mod:`.simulator` (their unit tests import
them directly); this module only adds the orchestration layer
(``build_signals`` scoring + per-account top-N) the run-weekly / run-daily
flow needs. ``initialize`` is already config-first and is re-exported
straight from :mod:`.simulator`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import simulator as _sim
from .strategy import build_signals


def _coerce_as_of(as_of: Any) -> date | None:
    """Accept the CLI's ISO-string as_of (or date / None)."""
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
    """Score each account's universe and emit pending rebalance orders.

    ``run_id`` is accepted for CLI-contract uniformity; HK orders are tagged
    by account at the settlement layer, so it is not threaded further.
    """
    d = _coerce_as_of(as_of)
    scored = build_signals(config, provider, as_of=d)
    accounts = config.get("accounts", []) or []
    # The low-level applies one top_n per account group; HK baseline uses a
    # uniform top_n across accounts, so take the max (== that value).
    top_n = max((int(a.get("top_n", 50)) for a in accounts), default=50)
    max_single_weight = float((config.get("trading", {}) or {}).get("max_single_weight", 0.05))
    return _sim.generate_rebalance_orders(
        store, provider, scored, as_of=d, top_n=top_n, max_single_weight=max_single_weight,
    )


def execute_due_orders(
    config: dict[str, Any],
    store: Any,
    provider: Any,
    *,
    as_of: Any = None,
    **_ignored: Any,
) -> list[dict[str, Any]]:
    """Execute settlement-due orders (config accepted for CLI uniformity)."""
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
    """Mark NAV to market (config/notes accepted for CLI uniformity)."""
    return _sim.update_nav(store, provider, as_of=_coerce_as_of(as_of))


__all__ = ["generate_rebalance_orders", "execute_due_orders", "update_nav"]
