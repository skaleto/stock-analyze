from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from .data_provider import AkshareProvider, ExecutionQuote
from .store import PortfolioStore
from .strategy import build_signals
from .utils import next_business_day, now_iso, safe_float, write_json


def initialize(config: dict[str, Any], store: PortfolioStore, force: bool = False) -> dict[str, Any]:
    return store.initialize(config, force=force)


def generate_rebalance_orders(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: AkshareProvider,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    state = store.initialize(config)
    all_selected: list[pd.DataFrame] = []
    pending_batches: list[dict[str, Any]] = []
    run_date = as_of or date.today().isoformat()
    execute_after = provider.next_trading_day(run_date) if hasattr(provider, "next_trading_day") else next_business_day(run_date)

    for account in config.get("accounts", []):
        account_id = str(account["id"])
        signal = build_signals(config, account, provider, as_of=as_of)
        selected = signal.selected.copy()
        all_selected.append(selected)
        orders = build_target_orders(config, state["accounts"][account_id], selected)
        pending_batches.append(
            {
                "run_id": f"{config.get('strategy_id', 'strategy')}-{account_id}-{run_date}",
                "strategy_id": config.get("strategy_id", "strategy"),
                "account_id": account_id,
                "scope": account["scope"],
                "signal_date": run_date,
                "execute_after": execute_after,
                "created_at": now_iso(),
                "orders": orders,
                "warnings": signal.warnings,
            }
        )

    existing = [batch for batch in store.load_pending() if batch.get("signal_date") != run_date]
    store.save_pending(existing + pending_batches)
    if all_selected:
        store.save_signals(pd.concat(all_selected, ignore_index=True))
    return pending_batches


def build_target_orders(config: dict[str, Any], account_state: dict[str, Any], selected: pd.DataFrame) -> list[dict[str, Any]]:
    top_n = max(len(selected), 1)
    total_value = account_total_value(account_state)
    target_value = total_value / top_n
    max_single_weight = safe_float(config.get("trading", {}).get("max_single_weight"))
    if max_single_weight is not None and max_single_weight > 0:
        target_value = min(target_value, total_value * max_single_weight)
    lot_size = int(config.get("trading", {}).get("lot_size", 100))
    current_positions = account_state.get("positions", {})

    targets: dict[str, dict[str, Any]] = {}
    for _, row in selected.iterrows():
        price = safe_float(row.get("latest_price"))
        if price is None or price <= 0:
            continue
        target_shares = int(target_value // (price * lot_size)) * lot_size
        targets[str(row["code"]).zfill(6)] = {
            "code": str(row["code"]).zfill(6),
            "name": row.get("name", ""),
            "target_shares": target_shares,
            "target_value": round(target_shares * price, 2),
            "target_weight": round((target_shares * price / total_value), 6) if total_value else None,
            "reference_price": price,
            "score": row.get("score"),
            "reason": row.get("score_detail", ""),
        }

    for code, position in current_positions.items():
        targets.setdefault(
            code,
            {
                "code": code,
                "name": position.get("name", ""),
                "target_shares": 0,
                "target_value": 0,
                "target_weight": 0,
                "reference_price": position.get("last_price"),
                "score": None,
                "reason": "not_selected",
            },
        )

    orders = []
    for code, target in sorted(targets.items()):
        current_shares = int(current_positions.get(code, {}).get("shares", 0))
        target_shares = int(target["target_shares"])
        if target_shares == current_shares:
            continue
        side = "buy" if target_shares > current_shares else "sell"
        orders.append(
            {
                "code": code,
                "name": target.get("name", ""),
                "side": side,
                "current_shares": current_shares,
                "target_shares": target_shares,
                "delta_shares": target_shares - current_shares,
                "target_value": target.get("target_value"),
                "target_weight": target.get("target_weight"),
                "reference_price": target.get("reference_price"),
                "score": target.get("score"),
                "reason": target.get("reason"),
                "status": "pending",
            }
        )
    return orders


def execute_due_orders(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: AkshareProvider,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    state = store.initialize(config)
    pending = store.load_pending()
    run_date = as_of or date.today().isoformat()
    remaining: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    for batch in pending:
        if str(batch.get("execute_after", "")) > run_date:
            remaining.append(batch)
            continue
        account = state["accounts"][batch["account_id"]]
        refresh_sellable_positions(account, run_date)
        sells = [order for order in batch.get("orders", []) if order.get("side") == "sell"]
        buys = [order for order in batch.get("orders", []) if order.get("side") == "buy"]
        retained_orders: list[dict[str, Any]] = []
        for order in sells + buys:
            trade = execute_order(config, account, order, provider, batch.get("execute_after"), batch.get("account_id"), run_date)
            if trade:
                trade_rows.append(trade)
            if order.get("status") != "filled":
                retained_orders.append(order)
        if retained_orders:
            updated_batch = dict(batch)
            updated_batch["orders"] = retained_orders
            updated_batch["last_attempt_at"] = now_iso()
            remaining.append(updated_batch)

    store.save_pending(remaining)
    store.save_state(state)
    store.append_trades(trade_rows)
    store.write_positions(state)
    return trade_rows


def execute_order(
    config: dict[str, Any],
    account: dict[str, Any],
    order: dict[str, Any],
    provider: AkshareProvider,
    execute_after: str,
    account_id: str,
    run_date: str | None = None,
) -> dict[str, Any] | None:
    code = str(order["code"]).zfill(6)
    side = order["side"]
    order["attempts"] = int(order.get("attempts") or 0) + 1
    order["last_attempt_at"] = now_iso()
    target_shares = int(order["target_shares"])
    current = account.get("positions", {}).get(code, {})
    current_shares = int(current.get("shares", 0))
    raw_delta = target_shares - current_shares
    if raw_delta == 0:
        mark_order_filled(order, current_shares)
        return None

    quote = execution_quote(provider, code, execute_after, side, run_date or execute_after)
    if quote.reason:
        mark_order_unfilled(order, quote.reason, current_shares, target_shares)
        return None
    price = quote.price
    trade_date = quote.trade_date or execute_after
    if price is None or price <= 0:
        mark_order_unfilled(order, "execution_price_missing", current_shares, target_shares)
        return None

    trading = config.get("trading", {})
    lot_size = int(trading.get("lot_size", 100))
    slippage_rate = float(trading.get("slippage_rate", 0))
    commission_rate = float(trading.get("commission_rate", 0))
    min_commission = float(trading.get("min_commission", 0))
    stamp_tax_rate = float(trading.get("stamp_tax_rate", 0))
    side_multiplier = 1 + slippage_rate if side == "buy" else 1 - slippage_rate
    execution_price = round(price * side_multiplier, 4)

    if side == "sell":
        available_shares = int(current.get("available_shares", current_shares))
        shares = min(abs(raw_delta), current_shares, available_shares)
        if shares <= 0:
            reason = "no_position" if current_shares <= 0 else "no_sellable_shares"
            mark_order_unfilled(order, reason, current_shares, target_shares)
            return None
    else:
        desired = max(raw_delta, 0)
        shares = (desired // lot_size) * lot_size
        estimated_cost = shares * execution_price
        estimated_fee = max(estimated_cost * commission_rate, min_commission) if shares else 0
        while shares > 0 and estimated_cost + estimated_fee > float(account.get("cash", 0)):
            shares -= lot_size
            estimated_cost = shares * execution_price
            estimated_fee = max(estimated_cost * commission_rate, min_commission) if shares else 0

    if shares <= 0:
        reason = "insufficient_cash" if side == "buy" else "no_sellable_shares"
        mark_order_unfilled(order, reason, current_shares, target_shares)
        return None

    gross = shares * execution_price
    commission = max(gross * commission_rate, min_commission)
    stamp_tax = gross * stamp_tax_rate if side == "sell" else 0.0
    slippage = abs(execution_price - price) * shares

    if side == "sell":
        net = gross - commission - stamp_tax
        account["cash"] = float(account.get("cash", 0)) + net
        new_shares = current_shares - shares
        if new_shares <= 0:
            account.get("positions", {}).pop(code, None)
        else:
            current["shares"] = new_shares
            current["available_shares"] = max(int(current.get("available_shares", new_shares)) - shares, 0)
            current["last_price"] = execution_price
            current["market_value"] = new_shares * execution_price
            account["positions"][code] = current
    else:
        net = gross + commission
        account["cash"] = float(account.get("cash", 0)) - net
        new_shares = current_shares + shares
        old_cost = float(current.get("avg_cost", execution_price)) * current_shares
        avg_cost = (old_cost + gross + commission) / new_shares
        available_shares = int(current.get("available_shares", current_shares))
        account.setdefault("positions", {})[code] = {
            "name": order.get("name", code),
            "shares": new_shares,
            "available_shares": min(available_shares, new_shares),
            "avg_cost": round(avg_cost, 4),
            "last_buy_date": trade_date,
            "last_price": execution_price,
            "market_value": round(new_shares * execution_price, 2),
            "unrealized_pnl": round((execution_price - avg_cost) * new_shares, 2),
            "score": order.get("score"),
            "reason": order.get("reason", ""),
            "updated_at": now_iso(),
        }

    update_order_progress(order, account, code, target_shares, side, shares)
    return {
        "trade_date": trade_date,
        "account_id": account_id,
        "code": code,
        "name": order.get("name", code),
        "side": side,
        "shares": shares,
        "price": execution_price,
        "gross_amount": round(gross, 2),
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "slippage": round(slippage, 2),
        "net_amount": round(net, 2),
        "cash_after": round(float(account.get("cash", 0)), 2),
        "reason": order.get("reason", ""),
    }


def update_nav(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: AkshareProvider,
    as_of: str | None = None,
    notes: str = "",
) -> list[dict[str, Any]]:
    state = store.initialize(config)
    run_date = as_of or date.today().isoformat()
    rows: list[dict[str, Any]] = []
    for account_id, account in state.get("accounts", {}).items():
        market_value = 0.0
        for code, position in account.get("positions", {}).items():
            snapshot = provider.price_snapshot(code, as_of=as_of)
            price = snapshot.close or safe_float(position.get("last_price")) or safe_float(position.get("avg_cost")) or 0
            shares = int(position.get("shares", 0))
            position["last_price"] = price
            position["market_value"] = round(shares * price, 2)
            position["unrealized_pnl"] = round((price - float(position.get("avg_cost", price))) * shares, 2)
            position["updated_at"] = now_iso()
            market_value += shares * price
        benchmark_close, benchmark_date = provider.benchmark_close(account.get("benchmark"), as_of=as_of)
        rows.append(
            {
                "date": run_date,
                "account_id": account_id,
                "cash": round(float(account.get("cash", 0)), 2),
                "market_value": round(market_value, 2),
                "total_value": round(float(account.get("cash", 0)) + market_value, 2),
                "benchmark_code": account.get("benchmark"),
                "benchmark_close": benchmark_close,
                "benchmark_date": benchmark_date,
                "notes": notes,
            }
        )
    store.save_state(state)
    store.append_nav(rows)
    store.write_positions(state)
    return rows


def account_total_value(account_state: dict[str, Any]) -> float:
    cash = float(account_state.get("cash", 0))
    market_value = sum(float(position.get("market_value", 0)) for position in account_state.get("positions", {}).values())
    return cash + market_value


def execution_quote(provider: AkshareProvider, code: str, execute_after: str, side: str, run_date: str) -> ExecutionQuote:
    if hasattr(provider, "execution_quote"):
        return provider.execution_quote(code, execute_after, side, as_of=run_date)
    price, trade_date = provider.execution_price(code, execute_after, side)
    reason = "" if price else "execution_quote_missing"
    return ExecutionQuote(code=code, trade_date=trade_date, price=price, reason=reason)


def refresh_sellable_positions(account: dict[str, Any], run_date: str) -> None:
    for position in account.get("positions", {}).values():
        shares = int(position.get("shares", 0))
        last_buy_date = str(position.get("last_buy_date") or "")
        if not last_buy_date or last_buy_date < run_date:
            position["available_shares"] = shares
        else:
            position["available_shares"] = min(int(position.get("available_shares", 0)), shares)


def mark_order_unfilled(order: dict[str, Any], reason: str, current_shares: int, target_shares: int) -> None:
    order["status"] = "pending"
    order["unfilled_reason"] = reason
    order["current_shares"] = current_shares
    order["target_shares"] = target_shares
    order["delta_shares"] = target_shares - current_shares


def mark_order_filled(order: dict[str, Any], current_shares: int) -> None:
    order["status"] = "filled"
    order["unfilled_reason"] = ""
    order["current_shares"] = current_shares
    order["delta_shares"] = 0


def update_order_progress(
    order: dict[str, Any],
    account: dict[str, Any],
    code: str,
    target_shares: int,
    side: str,
    filled_shares: int,
) -> None:
    current_shares = int(account.get("positions", {}).get(code, {}).get("shares", 0))
    delta = target_shares - current_shares
    order["current_shares"] = current_shares
    order["delta_shares"] = delta
    order["last_filled_shares"] = filled_shares
    if delta == 0:
        mark_order_filled(order, current_shares)
        return
    order["status"] = "partial"
    if side == "buy" and delta > 0:
        order["unfilled_reason"] = "insufficient_cash"
    elif side == "sell" and delta < 0:
        order["unfilled_reason"] = "no_sellable_shares"
    else:
        order["unfilled_reason"] = "partial_fill"
