"""Simulator bindings for domestic cross-border ETF paper trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .._settlement_simulator import SettlementSimulatorBase
from . import mechanics as _mechanics


@dataclass
class ETFOrder:
    code: str
    side: str
    shares: int
    trade_date: str
    target_value: float | None = None
    account_id: str = ""
    score: float | None = None
    reason: str = ""


_SIM = SettlementSimulatorBase(
    mechanics=_mechanics,
    order_cls=ETFOrder,
    market_id="cn_qdii_etf",
)

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
    top_n: int = 5,
    max_single_weight: float = 0.20,
    top_n_by_account: dict[str, int] | None = None,
    hold_buffer_pct: float = 0.0,
    max_holding_days: int | None = None,
) -> list[dict[str, Any]]:
    return _SIM.generate_rebalance_orders(
        store,
        provider,
        scored,
        as_of=as_of,
        top_n=top_n,
        max_single_weight=max_single_weight,
        top_n_by_account=top_n_by_account,
        hold_buffer_pct=hold_buffer_pct,
        max_holding_days=max_holding_days,
    )


__all__ = [
    "ETFOrder",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "update_nav",
]
