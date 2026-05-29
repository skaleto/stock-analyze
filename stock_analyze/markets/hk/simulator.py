"""HK paper-trading simulator — thin wrapper over the shared base.

The settlement-queue + buy/sell/short/cover + NAV + rebalance logic now
lives in ``stock_analyze.markets._settlement_simulator.SettlementSimulatorBase``
(extracted per OpenSpec change ``extract-yfinance-provider-base``). This
module keeps only the HK ``HKOrder`` dataclass + a module singleton bound
to HK mechanics, and re-exports the four public functions with unchanged
signatures.

HK rules (via ``markets.hk.mechanics``): T+2 settlement, no daily limit,
variable lot (v1 = 100), simplified shorting (100% cash collateral),
0.13% stamp duty on both sides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from . import mechanics as _mechanics
from .._settlement_simulator import SettlementSimulatorBase


@dataclass
class HKOrder:
    """Pending order for the HK simulator.

    ``side`` is one of ``buy``, ``sell``, ``short`` (open short), ``cover``
    (close short). Long-only flows use only ``buy`` / ``sell``.
    """

    code: str
    side: str
    shares: int
    trade_date: str  # YYYY-MM-DD; the day execution attempts to fill
    target_value: float | None = None
    account_id: str = ""
    score: float | None = None
    reason: str = ""


# Module singleton bound to HK mechanics. The settlement days, fee rates,
# collateral ratio, and lot rule all come from markets.hk.mechanics.
_SIM = SettlementSimulatorBase(mechanics=_mechanics, order_cls=HKOrder, market_id="hk")

# Re-export the settlement helpers at module level so existing tests that
# import them (tests/test_markets_hk_simulator.py) keep working unchanged.
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
    "HKOrder",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "update_nav",
]
