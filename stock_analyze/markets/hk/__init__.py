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

# Phase 2 follow-up tasks wire the public API here. For the bootstrap
# task (P2-T1) the docstring documents the contract; data_provider,
# simulator, strategy land in P2-T2..P2-T4.
