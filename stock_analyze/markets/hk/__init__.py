"""Hong Kong market implementation.

Companion to ``markets.a_share`` (Phase 1). Mirrors the same public API
contract (make_provider, execute_due_orders, update_nav,
generate_rebalance_orders, initialize, build_signals). Per-market
mechanics in ``mechanics.py``; universe definitions in ``universe.py``.

Faithful HK trading rules per
``docs/superpowers/specs/2026-05-27-multi-market-competition-design.md``:
  - T+2 settlement
  - No daily limit
  - Per-stock variable lot size (v1: defaults to 100)
  - Simplified shorting (100% cash collateral)
  - 0.13% stamp duty

Public API is wired at the bottom of this file once data_provider,
simulator, and strategy modules exist (Phase 2 follow-up tasks).
"""

from .data_provider import make_provider
from .simulator import initialize
# High-level, config-driven run entry points (the CLI dispatches these
# generically across markets). The low-level scored/store primitives stay in
# .simulator; .run wraps them with build_signals scoring + per-account top-N.
from .run import (
    execute_due_orders,
    generate_rebalance_orders,
    update_nav,
)
from .strategy import build_signals

__all__ = [
    "build_signals",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "make_provider",
    "update_nav",
]
