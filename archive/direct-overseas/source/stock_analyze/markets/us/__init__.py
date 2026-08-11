"""US market implementation.

Companion to ``markets.a_share`` (Phase 1) and ``markets.hk`` (Phase 2).
Mirrors the same public API contract.

Faithful US trading rules per
``docs/superpowers/specs/2026-05-27-multi-market-competition-design.md``:
  - T+1 settlement (since May 2024)
  - No daily limit
  - lot_size = 1 (any whole share, no fractional in v1)
  - Simplified shorting (100% cash collateral)
  - Zero commission (retail), zero stamp tax

Public API:
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
