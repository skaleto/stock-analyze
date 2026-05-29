"""US paper-trading simulator — thin wrapper over the shared base.

The settlement-queue + buy/sell/short/cover + NAV + rebalance logic now
lives in ``stock_analyze.markets._settlement_simulator.SettlementSimulatorBase``
(extracted per OpenSpec change ``extract-yfinance-provider-base``). This
module keeps only the US ``USOrder`` dataclass + a module singleton bound
to US mechanics, and re-exports the four public functions with unchanged
signatures.

US rules (via ``markets.us.mechanics``): T+1 settlement, no daily limit,
lot_size = 1, simplified shorting (100% cash collateral), zero commission
+ zero stamp tax. Because the fee rates are 0, the shared fee math
(``gross × rate``) yields 0 — so US trade records still show
``commission == 0.0`` and ``stamp_tax == 0.0``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from . import mechanics as _mechanics
from .._settlement_simulator import SettlementSimulatorBase


@dataclass
class USOrder:
    code: str
    side: str
    shares: int
    trade_date: str
    target_value: float | None = None
    account_id: str = ""
    score: float | None = None
    reason: str = ""


# Module singleton bound to US mechanics (T+1, zero-fee, lot=1).
_SIM = SettlementSimulatorBase(mechanics=_mechanics, order_cls=USOrder, market_id="us")

# Re-export the settlement helpers at module level so existing tests that
# import them (tests/test_markets_us.py) keep working unchanged.
_next_business_day = SettlementSimulatorBase._next_business_day
_drain_settlement = SettlementSimulatorBase._drain_settlement


def initialize(config: dict[str, Any], store: Any) -> dict[str, Any]:
    return _SIM.initialize(config, store)


def execute_due_orders(store: Any, provider: Any, *, as_of: date | None = None) -> list[dict[str, Any]]:
    return _SIM.execute_due_orders(store, provider, as_of=as_of)


def update_nav(store: Any, provider: Any, *, as_of: date | None = None) -> list[dict[str, Any]]:
    return _SIM.update_nav(store, provider, as_of=as_of)


def generate_rebalance_orders(
    store: Any,
    provider: Any,
    scored: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    top_n: int = 50,
    max_single_weight: float = 0.05,
) -> list[dict[str, Any]]:
    return _SIM.generate_rebalance_orders(
        store, provider, scored, as_of=as_of, top_n=top_n, max_single_weight=max_single_weight,
    )


__all__ = [
    "USOrder",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "update_nav",
]
