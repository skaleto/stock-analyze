"""HK paper-trading simulator.

Faithfully models the HK trading rules that differ from A-share:

  - **T+2 settlement**: sells credit cash to a settlement queue tagged
    with ``settle_date = trade_date + 2 business days``. The queue is
    drained on each daily step as settle_date catches up.
  - **No daily limit**: no ±10% block on buy/sell prices.
  - **Variable lot size**: ``mechanics.lot_size_for(code)`` per stock
    (v1 always returns 100).
  - **Simplified shorting**: ``target_shares`` may be negative.
    Opening a short freezes ``|short_value|`` cash as collateral.
    Closing a short releases the collateral + applies realised P/L.
    No margin engine, no borrow availability check, no overnight fees.
  - **Stamp duty 0.13%**: applied on BOTH buy and sell (vs A-share's
    sell-only). Commission + slippage are folded into the execution
    price by the data provider, so the simulator only adds stamp tax.

State schema (per-account in state.json):

```jsonc
{
  "cash": 500000.0,                  // settled, available
  "cash_collateral": 0.0,            // frozen for open shorts
  "positions": {
    "0700.HK": {
      "shares": 200,                 // signed; -100 = 100 short
      "avg_cost": 350.5,
      "last_buy_date": "2026-06-15",
      "hold_since": "2026-06-15",
      "short_collateral": 0.0        // per-position collateral (for shorts)
    },
    ...
  },
  "settlement_queue": [              // T+2 sells waiting for cash credit
    {"settle_date": "2026-06-17", "amount": 12345.67},
    ...
  ]
}
```

Public API mirrors A-share:
  - ``initialize(config, store)``
  - ``execute_due_orders(store, provider, *, as_of)``
  - ``update_nav(store, provider, *, as_of)``
  - ``generate_rebalance_orders(store, provider, scored, *, as_of)``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .data_provider import YFinanceHKProvider
from .mechanics import (
    COMMISSION_RATE,
    SETTLEMENT_DAYS,
    SHORTING_COLLATERAL_RATIO,
    STAMP_TAX_RATE,
    lot_size_for,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Order schema
# ---------------------------------------------------------------------------


@dataclass
class HKOrder:
    """Pending order for the HK simulator.

    ``side`` is one of ``buy``, ``sell``, ``short`` (open short), ``cover``
    (close short). For long-only flows only ``buy`` / ``sell`` are used.
    """

    code: str
    side: str
    shares: int
    trade_date: str  # YYYY-MM-DD; the day on which execution attempts to fill
    target_value: float | None = None
    account_id: str = ""
    score: float | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------


def initialize(config: dict[str, Any], store: Any) -> dict[str, Any]:
    """Seed an empty state.json for an HK competition.

    Each account in ``config['accounts']`` gets cash + an empty
    positions dict + an empty settlement_queue + zero collateral.
    """
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
        "market": "hk",
        "competition_id": config.get("competition_id", "hk-paper"),
        "accounts": accounts,
    }
    store.save_state(state)
    return state


# ---------------------------------------------------------------------------
# Settlement queue helpers
# ---------------------------------------------------------------------------


def _next_business_day(d: date, n: int) -> date:
    """Naive: skip Sat/Sun, advance ``n`` business days. v1 doesn't model
    HK exchange holidays (acceptable for paper-trading approximation)."""
    cur = d
    moves = 0
    while moves < n:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:  # Mon=0..Fri=4
            moves += 1
    return cur


def _drain_settlement(account_state: dict[str, Any], as_of: date) -> float:
    """Move queued amounts whose settle_date <= as_of into ``cash``.

    Returns the total amount credited.
    """
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


# ---------------------------------------------------------------------------
# Execute due orders
# ---------------------------------------------------------------------------


def execute_due_orders(
    store: Any,
    provider: YFinanceHKProvider,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Execute all pending orders with trade_date == as_of.

    Drains the settlement queue first (so freshly-settled cash is
    available for same-day buys). Returns a list of trade-record dicts
    suitable for appending to trades.csv.
    """
    as_of = as_of or date.today()
    state = store.load_state()
    pending = store.read_pending()
    trades: list[dict[str, Any]] = []
    remaining_pending: list[dict[str, Any]] = []

    # Drain settlement first for every account
    for account_id, account_state in state.get("accounts", {}).items():
        _drain_settlement(account_state, as_of)

    for raw in pending:
        order = _coerce_order(raw)
        if order.trade_date != as_of.isoformat():
            remaining_pending.append(raw)
            continue
        account_state = state["accounts"].get(order.account_id)
        if account_state is None:
            logger.warning("order references unknown account %s", order.account_id)
            continue
        trade = _execute_order(order, account_state, provider, as_of)
        if trade is not None:
            trades.append(trade)

    store.save_state(state)
    store.write_pending(remaining_pending)
    return trades


def _coerce_order(raw: dict[str, Any]) -> HKOrder:
    return HKOrder(
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
    order: HKOrder,
    account_state: dict[str, Any],
    provider: YFinanceHKProvider,
    as_of: date,
) -> dict[str, Any] | None:
    """Match a single order against the provider's execution quote.

    Returns None if the order can't fill (no quote, insufficient cash, etc).
    """
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
        stamp = cost * STAMP_TAX_RATE
        commission = cost * COMMISSION_RATE
        total_debit = cost + stamp + commission
        if total_debit > cash:
            logger.info("insufficient cash for buy %s shares=%d", order.code, order.shares)
            return None
        account_state["cash"] = cash - total_debit
        existing = positions.get(order.code, {"shares": 0, "avg_cost": 0.0})
        new_shares = int(existing.get("shares", 0)) + order.shares
        old_cost_basis = float(existing.get("avg_cost", 0.0)) * int(existing.get("shares", 0))
        new_cost_basis = old_cost_basis + cost
        positions[order.code] = {
            "shares": new_shares,
            "avg_cost": new_cost_basis / new_shares if new_shares != 0 else 0.0,
            "last_buy_date": as_of.isoformat(),
            "hold_since": existing.get("hold_since", as_of.isoformat()),
            "short_collateral": float(existing.get("short_collateral", 0.0)),
        }
        return _trade_record(order, px, cost, stamp, commission, settle_date, "buy")

    if order.side == "sell":
        existing = positions.get(order.code)
        if not existing or int(existing.get("shares", 0)) < order.shares:
            logger.info("insufficient shares for sell %s shares=%d", order.code, order.shares)
            return None
        gross = order.shares * px
        stamp = gross * STAMP_TAX_RATE
        commission = gross * COMMISSION_RATE
        net = gross - stamp - commission
        # T+2: net cash credited to settlement_queue, not cash
        account_state.setdefault("settlement_queue", []).append(
            {"settle_date": settle_date, "amount": net}
        )
        new_shares = int(existing["shares"]) - order.shares
        if new_shares == 0:
            del positions[order.code]
        else:
            positions[order.code] = {
                **existing,
                "shares": new_shares,
            }
        return _trade_record(order, px, gross, stamp, commission, settle_date, "sell")

    if order.side == "short":
        # Open a short: freeze cash collateral, position becomes negative
        gross = order.shares * px
        stamp = gross * STAMP_TAX_RATE
        commission = gross * COMMISSION_RATE
        coll = gross * SHORTING_COLLATERAL_RATIO
        net_debit = coll + stamp + commission  # collateral leaves cash too
        if net_debit > cash:
            return None
        account_state["cash"] = cash - net_debit
        account_state["cash_collateral"] = collateral + coll
        existing = positions.get(order.code, {"shares": 0, "avg_cost": 0.0})
        prior_shares = int(existing.get("shares", 0))
        # If user is shorting on top of an existing long, that doesn't make
        # sense in v1 — block.
        if prior_shares > 0:
            account_state["cash"] = cash  # undo
            account_state["cash_collateral"] = collateral
            return None
        new_shares = prior_shares - order.shares  # more negative
        old_cost_basis = abs(prior_shares) * float(existing.get("avg_cost", 0.0))
        new_cost_basis = old_cost_basis + gross
        positions[order.code] = {
            "shares": new_shares,
            "avg_cost": new_cost_basis / abs(new_shares) if new_shares != 0 else 0.0,
            "last_buy_date": as_of.isoformat(),
            "hold_since": existing.get("hold_since", as_of.isoformat()),
            "short_collateral": float(existing.get("short_collateral", 0.0)) + coll,
        }
        return _trade_record(order, px, gross, stamp, commission, settle_date, "short")

    if order.side == "cover":
        # Close a short: buy back shares, release collateral, apply P/L
        existing = positions.get(order.code)
        if not existing or int(existing.get("shares", 0)) >= 0:
            return None
        prior_shares = int(existing["shares"])  # negative
        if order.shares > -prior_shares:
            return None  # can't cover more than open short
        gross = order.shares * px
        stamp = gross * STAMP_TAX_RATE
        commission = gross * COMMISSION_RATE
        # Released collateral = original collateral × (shares_covered / total_short_shares)
        per_pos_coll = float(existing.get("short_collateral", 0.0))
        coll_released = per_pos_coll * (order.shares / abs(prior_shares))
        # P/L = (avg_cost - cover_px) × shares_covered  (cover px low = profit)
        avg_cost = float(existing.get("avg_cost", 0.0))
        pnl = (avg_cost - px) * order.shares
        cash_back = coll_released + pnl - stamp - commission
        account_state["cash"] = cash + cash_back
        account_state["cash_collateral"] = collateral - coll_released
        new_shares = prior_shares + order.shares  # less negative
        if new_shares == 0:
            del positions[order.code]
        else:
            positions[order.code] = {
                **existing,
                "shares": new_shares,
                "short_collateral": per_pos_coll - coll_released,
            }
        return _trade_record(order, px, gross, stamp, commission, settle_date, "cover")

    return None


def _quote_side(side: str) -> str:
    """Map order side to provider quote-side ('buy' or 'sell')."""
    return "buy" if side in ("buy", "cover") else "sell"


def _trade_record(
    order: HKOrder,
    price: float,
    gross: float,
    stamp: float,
    commission: float,
    settle_date: str,
    side_label: str,
) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Update NAV
# ---------------------------------------------------------------------------


def update_nav(
    store: Any,
    provider: YFinanceHKProvider,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Compute and persist daily NAV for each account.

    NAV formula:
      total_value = cash + cash_collateral + Σ(position_market_value)

    For short positions: market_value = -|shares| × current_price + collateral
      (i.e. liability = current cost to buy back; reduces equity)
    """
    as_of = as_of or date.today()
    state = store.load_state()
    rows: list[dict[str, Any]] = []
    for account_id, account_state in state.get("accounts", {}).items():
        # Drain settlement first so today's NAV reflects newly-settled cash
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
                # short: liability is |shares|*px; collateral covers it
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
            "source": "hk-daily",
        })
    store.append_nav(rows)
    return rows


# ---------------------------------------------------------------------------
# Generate rebalance orders
# ---------------------------------------------------------------------------


def generate_rebalance_orders(
    store: Any,
    provider: YFinanceHKProvider,
    scored: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    top_n: int = 50,
    max_single_weight: float = 0.05,
) -> list[dict[str, Any]]:
    """Generate buy/sell orders to bring portfolio toward the top-N of ``scored``.

    ``scored`` is a DataFrame-equivalent list of {code, score, account_id}
    rows produced by strategy.build_signals. Per account, take the top_n
    by score, compute target_value per position with the max_single_weight
    cap, and emit:
      - Sell orders for current holdings not in target set
      - Buy orders for new target holdings
      - Adjust quantity for held-and-still-target stocks

    Orders are written to pending with trade_date = T+1 business day.
    """
    as_of = as_of or date.today()
    trade_date = _next_business_day(as_of, 1).isoformat()
    state = store.load_state()
    new_orders: list[dict[str, Any]] = []

    # Group scored by account
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

        # Per-stock target value
        per_target = min(
            account_value / max(top_n, 1),
            account_value * max_single_weight,
        )

        # Sell what's no longer wanted
        for code, pos in list(account_state.get("positions", {}).items()):
            shares = int(pos.get("shares", 0))
            if shares > 0 and code not in target_codes:
                new_orders.append({
                    "code": code, "side": "sell", "shares": shares,
                    "trade_date": trade_date, "account_id": account_id,
                    "target_value": 0.0, "reason": "not_in_top_n",
                })

        # Buy / top-up for target
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
    "HKOrder",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "update_nav",
]
