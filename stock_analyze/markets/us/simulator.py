"""US paper-trading simulator.

US rules vs A-share:
  - T+1 settlement (same as A-share since May 2024) — cash credited next day
  - No daily limit
  - lot_size = 1 (any whole share, no rounding loss)
  - Simplified shorting with 100% cash collateral
  - Zero commission, zero stamp tax (retail brokers)

State schema mirrors HK's (cash + cash_collateral + positions
+ settlement_queue) for consistency, but the queue's settle_date is
trade_date + 1 instead of +2.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .data_provider import YFinanceUSProvider
from .mechanics import (
    COMMISSION_RATE,
    SETTLEMENT_DAYS,
    SHORTING_COLLATERAL_RATIO,
    STAMP_TAX_RATE,
    lot_size_for,
)


logger = logging.getLogger(__name__)


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


def initialize(config: dict[str, Any], store: Any) -> dict[str, Any]:
    """Seed empty state.json for a US competition."""
    accounts: dict[str, dict[str, Any]] = {}
    for account in config.get("accounts", []):
        accounts[str(account["id"])] = {
            "scope": account.get("scope"),
            "benchmark": account.get("benchmark"),
            "cash": float(account.get("cash", 0.0)),
            "cash_collateral": 0.0,
            "positions": {},
            "settlement_queue": [],
        }
    state = {
        "market": "us",
        "competition_id": config.get("competition_id", "us-paper"),
        "accounts": accounts,
    }
    store.save_state(state)
    return state


def _next_business_day(d: date, n: int) -> date:
    cur = d
    moves = 0
    while moves < n:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            moves += 1
    return cur


def _drain_settlement(account_state: dict[str, Any], as_of: date) -> float:
    credited = 0.0
    remaining = []
    for item in account_state.get("settlement_queue", []):
        sdate = date.fromisoformat(item["settle_date"])
        if sdate <= as_of:
            credited += float(item["amount"])
        else:
            remaining.append(item)
    if credited > 0:
        account_state["cash"] = float(account_state.get("cash", 0.0)) + credited
    account_state["settlement_queue"] = remaining
    return credited


def execute_due_orders(
    store: Any,
    provider: YFinanceUSProvider,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Execute pending orders with trade_date == as_of."""
    as_of = as_of or date.today()
    state = store.load_state()
    pending = store.read_pending()
    trades: list[dict[str, Any]] = []
    remaining_pending: list[dict[str, Any]] = []

    for account_state in state.get("accounts", {}).values():
        _drain_settlement(account_state, as_of)

    for raw in pending:
        order = _coerce_order(raw)
        if order.trade_date != as_of.isoformat():
            remaining_pending.append(raw)
            continue
        account_state = state["accounts"].get(order.account_id)
        if account_state is None:
            continue
        trade = _execute_order(order, account_state, provider, as_of)
        if trade is not None:
            trades.append(trade)

    store.save_state(state)
    store.write_pending(remaining_pending)
    return trades


def _coerce_order(raw: dict[str, Any]) -> USOrder:
    return USOrder(
        code=raw["code"],
        side=raw["side"],
        shares=int(raw.get("shares", 0)),
        trade_date=raw["trade_date"],
        target_value=raw.get("target_value"),
        account_id=raw.get("account_id", ""),
        score=raw.get("score"),
        reason=raw.get("reason", ""),
    )


def _execute_order(
    order: USOrder,
    account_state: dict[str, Any],
    provider: YFinanceUSProvider,
    as_of: date,
) -> dict[str, Any] | None:
    quote = provider.execution_quote(
        order.code, execute_after=as_of.isoformat(), side=_quote_side(order.side)
    )
    if quote.paused or quote.price is None or quote.price <= 0:
        return None

    px = float(quote.price)
    settle_date = _next_business_day(as_of, SETTLEMENT_DAYS).isoformat()

    positions = account_state.setdefault("positions", {})
    cash = float(account_state.get("cash", 0.0))
    collateral = float(account_state.get("cash_collateral", 0.0))

    if order.side == "buy":
        cost = order.shares * px
        # US: zero commission, zero stamp
        if cost > cash:
            return None
        account_state["cash"] = cash - cost
        existing = positions.get(order.code, {"shares": 0, "avg_cost": 0.0})
        new_shares = int(existing.get("shares", 0)) + order.shares
        old_basis = float(existing.get("avg_cost", 0.0)) * int(existing.get("shares", 0))
        new_basis = old_basis + cost
        positions[order.code] = {
            "shares": new_shares,
            "avg_cost": new_basis / new_shares if new_shares != 0 else 0.0,
            "last_buy_date": as_of.isoformat(),
            "hold_since": existing.get("hold_since", as_of.isoformat()),
            "short_collateral": float(existing.get("short_collateral", 0.0)),
        }
        return _trade_record(order, px, cost, 0.0, 0.0, settle_date, "buy")

    if order.side == "sell":
        existing = positions.get(order.code)
        if not existing or int(existing.get("shares", 0)) < order.shares:
            return None
        gross = order.shares * px
        # T+1: net cash credited next biz day
        account_state.setdefault("settlement_queue", []).append(
            {"settle_date": settle_date, "amount": gross}
        )
        new_shares = int(existing["shares"]) - order.shares
        if new_shares == 0:
            del positions[order.code]
        else:
            positions[order.code] = {**existing, "shares": new_shares}
        return _trade_record(order, px, gross, 0.0, 0.0, settle_date, "sell")

    if order.side == "short":
        gross = order.shares * px
        coll = gross * SHORTING_COLLATERAL_RATIO
        if coll > cash:
            return None
        account_state["cash"] = cash - coll
        account_state["cash_collateral"] = collateral + coll
        existing = positions.get(order.code, {"shares": 0, "avg_cost": 0.0})
        prior_shares = int(existing.get("shares", 0))
        if prior_shares > 0:
            account_state["cash"] = cash
            account_state["cash_collateral"] = collateral
            return None
        new_shares = prior_shares - order.shares
        old_basis = abs(prior_shares) * float(existing.get("avg_cost", 0.0))
        new_basis = old_basis + gross
        positions[order.code] = {
            "shares": new_shares,
            "avg_cost": new_basis / abs(new_shares) if new_shares != 0 else 0.0,
            "last_buy_date": as_of.isoformat(),
            "hold_since": existing.get("hold_since", as_of.isoformat()),
            "short_collateral": float(existing.get("short_collateral", 0.0)) + coll,
        }
        return _trade_record(order, px, gross, 0.0, 0.0, settle_date, "short")

    if order.side == "cover":
        existing = positions.get(order.code)
        if not existing or int(existing.get("shares", 0)) >= 0:
            return None
        prior_shares = int(existing["shares"])
        if order.shares > -prior_shares:
            return None
        per_pos_coll = float(existing.get("short_collateral", 0.0))
        coll_released = per_pos_coll * (order.shares / abs(prior_shares))
        avg_cost = float(existing.get("avg_cost", 0.0))
        pnl = (avg_cost - px) * order.shares
        cash_back = coll_released + pnl
        account_state["cash"] = cash + cash_back
        account_state["cash_collateral"] = collateral - coll_released
        new_shares = prior_shares + order.shares
        if new_shares == 0:
            del positions[order.code]
        else:
            positions[order.code] = {
                **existing,
                "shares": new_shares,
                "short_collateral": per_pos_coll - coll_released,
            }
        return _trade_record(order, px, order.shares * px, 0.0, 0.0, settle_date, "cover")

    return None


def _quote_side(side: str) -> str:
    return "buy" if side in ("buy", "cover") else "sell"


def _trade_record(order, price, gross, stamp, commission, settle_date, side_label):
    return {
        "trade_date": order.trade_date,
        "settle_date": settle_date,
        "account_id": order.account_id,
        "code": order.code,
        "side": side_label,
        "shares": order.shares,
        "price": price,
        "gross_amount": gross,
        "commission": commission,
        "stamp_tax": stamp,
        "score": order.score,
        "reason": order.reason,
    }


def update_nav(
    store: Any,
    provider: YFinanceUSProvider,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Daily NAV per account."""
    as_of = as_of or date.today()
    state = store.load_state()
    rows: list[dict[str, Any]] = []
    for account_id, account_state in state.get("accounts", {}).items():
        _drain_settlement(account_state, as_of)
        cash = float(account_state.get("cash", 0.0))
        coll = float(account_state.get("cash_collateral", 0.0))
        positions_value = 0.0
        for code, pos in account_state.get("positions", {}).items():
            shares = int(pos.get("shares", 0))
            if shares == 0:
                continue
            quote = provider.price_snapshot(code, as_of=as_of.isoformat())
            px = quote.close or float(pos.get("avg_cost", 0.0))
            if shares > 0:
                positions_value += shares * px
            else:
                positions_value -= abs(shares) * px
        total = cash + coll + positions_value
        rows.append({
            "date": as_of.isoformat(),
            "account_id": account_id,
            "cash": cash,
            "cash_collateral": coll,
            "positions_value": positions_value,
            "total_value": total,
            "benchmark_code": account_state.get("benchmark", ""),
            "benchmark_value": None,
            "benchmark_date": as_of.isoformat(),
            "source": "us-daily",
        })
    store.append_nav(rows)
    return rows


def generate_rebalance_orders(
    store: Any,
    provider: YFinanceUSProvider,
    scored: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    top_n: int = 50,
    max_single_weight: float = 0.05,
) -> list[dict[str, Any]]:
    """Generate buy/sell orders bringing portfolio toward top-N."""
    as_of = as_of or date.today()
    trade_date = _next_business_day(as_of, 1).isoformat()
    state = store.load_state()
    new_orders: list[dict[str, Any]] = []

    by_account: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        by_account.setdefault(row["account_id"], []).append(row)

    for account_id, account_state in state.get("accounts", {}).items():
        rows = by_account.get(account_id, [])
        rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
        target_codes = {r["code"] for r in rows[:top_n]}
        cash = float(account_state.get("cash", 0.0))
        account_value = cash + float(account_state.get("cash_collateral", 0.0))
        for code, pos in account_state.get("positions", {}).items():
            shares = int(pos.get("shares", 0))
            quote = provider.price_snapshot(code, as_of=as_of.isoformat())
            px = quote.close or float(pos.get("avg_cost", 0.0))
            account_value += shares * px

        per_target = min(
            account_value / max(top_n, 1),
            account_value * max_single_weight,
        )

        for code, pos in list(account_state.get("positions", {}).items()):
            shares = int(pos.get("shares", 0))
            if shares > 0 and code not in target_codes:
                new_orders.append({
                    "code": code, "side": "sell", "shares": shares,
                    "trade_date": trade_date, "account_id": account_id,
                    "target_value": 0.0, "reason": "not_in_top_n",
                })

        for r in rows[:top_n]:
            code = r["code"]
            quote = provider.price_snapshot(code, as_of=as_of.isoformat())
            px = quote.close
            if px is None or px <= 0:
                continue
            lot = lot_size_for(code)
            current_shares = int(account_state.get("positions", {}).get(code, {}).get("shares", 0))
            target_shares = max(int(per_target / (px * lot)), 0) * lot
            delta = target_shares - current_shares
            if delta > 0:
                new_orders.append({
                    "code": code, "side": "buy", "shares": delta,
                    "trade_date": trade_date, "account_id": account_id,
                    "target_value": per_target,
                    "score": float(r.get("score", 0.0)),
                    "reason": r.get("reason", "top_n"),
                })

    store.write_pending(new_orders)
    return new_orders


__all__ = [
    "USOrder",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "update_nav",
]
