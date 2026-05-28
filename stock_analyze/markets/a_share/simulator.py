from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .data_provider import DataProvider, ExecutionQuote
from ...factor_pipeline import UNCLASSIFIED
from .portfolio_controls import annotate_industries, select_top_n_with_controls
from ...store import PortfolioStore
from .strategy import build_signals
from ...utils import next_business_day, now_iso, safe_float


def initialize(config: dict[str, Any], store: PortfolioStore, force: bool = False) -> dict[str, Any]:
    return store.initialize(config, force=force)


def _resolve_as_of(as_of: str | date | None) -> str | None:
    """Normalize an as_of value to an ISO-format string (or None).

    The simulator's downstream contract uses ISO strings throughout
    (pending batch dates, NAV rows, etc.). The forward-mode call sites
    pass strings; the new backtest-mode call sites can pass datetime.date.
    """
    if as_of is None:
        return None
    if isinstance(as_of, date):
        return as_of.isoformat()
    return str(as_of)


def _override_store(store: PortfolioStore, data_root: Path | None) -> PortfolioStore:
    """If ``data_root`` is provided, build a fresh store rooted there.

    This is the integration seam for the backtest engine (Task 7): a single
    overlay run can be driven through a temporary data_root without
    polluting the agent's forward-mode state files.
    """
    if data_root is None:
        return store
    return PortfolioStore(data_root)


def _override_provider_cache(provider: DataProvider, market_data_root: Path | None) -> None:
    """If ``market_data_root`` is provided, rebind the provider's cache root.

    The abstract ``DataProvider`` base class defines a ``cache_dir`` attribute
    that controls where read-only market-data lookups land; every concrete
    provider (Tushare, Baostock, Akshare) inherits it. Backtest mode points
    this at a historical point-in-time cache; forward mode leaves it pointing
    at ``data/shared/cache/``. The ``hasattr`` guard tolerates lightweight
    test stubs that omit the attribute.
    """
    if market_data_root is None:
        return
    if hasattr(provider, "cache_dir"):
        provider.cache_dir = Path(market_data_root)


def generate_rebalance_orders(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: DataProvider,
    as_of: str | date | None = None,
    run_id: str | None = None,
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
    state = store.initialize(config)
    # repo_root controls where strategy.build_signals reads broadcast-factor
    # data (e.g. data/<agent>/alt_factors/market_sentiment.csv). When None,
    # strategy._resolve_default_repo_root() resolves via (in priority order):
    # SA_REPO_ROOT env > __file__-anchored walk > Path.cwd(). The
    # file-anchored fallback is robust to CWD drift (operator running CLI
    # from anywhere).
    all_selected: list[pd.DataFrame] = []
    all_factor_tables: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []
    pending_batches: list[dict[str, Any]] = []
    run_date = as_of or date.today().isoformat()
    execute_after = provider.next_trading_day(run_date) if hasattr(provider, "next_trading_day") else next_business_day(run_date)

    for account in config.get("accounts", []):
        account_id = str(account["id"])
        signal = build_signals(
            config, account, provider, as_of=as_of,
            repo_root=repo_root,
        )
        scored = signal.candidates.copy()
        account_state = state["accounts"][account_id]
        top_n = int(account.get("top_n", 10))
        selected, control_warnings = select_top_n_with_controls(scored, account_state, config, top_n, run_date=run_date)
        warnings = list(signal.warnings) + control_warnings
        if not selected.empty:
            selected = selected.copy()
            selected["account_id"] = account_id
            selected["pool"] = account["scope"]
        all_selected.append(selected)
        annotate_industries(account_state.get("positions", {}), scored)

        factor_table = signal.factor_table
        if not factor_table.empty:
            factor_table = factor_table.copy()
            factor_table["signal_date"] = run_date
            selected_codes = set(str(code).zfill(6) for code in selected.get("code", pd.Series([], dtype=str)).tolist())
            factor_table["selected"] = factor_table["code"].map(lambda code: str(code).zfill(6) in selected_codes)
            all_factor_tables.append(factor_table)

        coverage_rows.extend(_coverage_rows(scored, config.get("factors", {}), account_id, run_date))

        orders = build_target_orders(
            config, account_state, selected, fallback_pool=scored
        )
        pending_batches.append(
            {
                "run_id": run_id or f"{config.get('strategy_id', 'strategy')}-{account_id}-{run_date}",
                "strategy_id": config.get("strategy_id", "strategy"),
                "account_id": account_id,
                "scope": account["scope"],
                "signal_date": run_date,
                "execute_after": execute_after,
                "created_at": now_iso(),
                "orders": orders,
                "warnings": warnings,
            }
        )

    existing = [batch for batch in store.load_pending() if batch.get("signal_date") != run_date]
    store.save_pending(existing + pending_batches)
    if all_selected:
        non_empty_selected = [df for df in all_selected if not df.empty]
        if non_empty_selected:
            store.save_signals(pd.concat(non_empty_selected, ignore_index=True))
    if all_factor_tables:
        factor_snapshot = pd.concat(all_factor_tables, ignore_index=True)
        store.write_factor_snapshot(factor_snapshot, run_id or _fallback_run_id(config, run_date))
    if coverage_rows:
        store.append_factor_coverage(coverage_rows)
    return pending_batches


def _fallback_run_id(config: dict[str, Any], run_date: str) -> str:
    return f"{config.get('strategy_id', 'strategy')}-rebalance-{run_date}"


def _coverage_rows(scored: pd.DataFrame, factors: dict[str, Any], account_id: str, signal_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = max(len(scored), 1)
    for factor in factors:
        if factor not in scored.columns:
            continue
        column = pd.to_numeric(scored[factor], errors="coerce")
        valid = column.dropna()
        rows.append(
            {
                "signal_date": signal_date,
                "account_id": account_id,
                "factor": factor,
                "coverage_pct": round(len(valid) / total, 4),
                "missing_count": int(total - len(valid)),
                "mean": float(valid.mean()) if not valid.empty else None,
                "p5": float(valid.quantile(0.05)) if not valid.empty else None,
                "p50": float(valid.quantile(0.50)) if not valid.empty else None,
                "p95": float(valid.quantile(0.95)) if not valid.empty else None,
                "std": float(valid.std(ddof=0)) if not valid.empty else None,
            }
        )
    return rows


def build_target_orders(
    config: dict[str, Any],
    account_state: dict[str, Any],
    selected: pd.DataFrame,
    *,
    fallback_pool: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Build pending buy/sell orders for the next trading day.

    The caller passes ``selected`` (top-N after industry caps + hold buffer)
    and the full scored ``fallback_pool``. Sizing applies in this order:

      1. Equal-weight target ``total_value / top_n``, capped by
         ``max_single_weight × total_value`` (5% by default).
      2. ``target_shares = int(target_value // (price × lot_size)) × lot_size``.
      3. **Tier 1 — 1-lot fallback**: if ``target_shares == 0`` but 1 lot
         still fits under the 5% cap (``price × lot_size ≤ cap``), bump to
         ``lot_size``. This rescues stocks priced ¥100-250 that the strict
         equal-weight formula would otherwise drop.
      4. **Tier 2 — skip-down fill**: if fewer than ``top_n`` slots have
         non-zero target_shares after processing ``selected``, walk
         ``fallback_pool`` in score-rank order, pulling in the next
         eligible candidate (1 lot under cap). This guarantees the
         strategy actually expresses its top-N intent rather than leaving
         slots empty just because the highest-ranked picks are unbuyable.

    Stocks priced > ``max_single_value / lot_size`` (¥250 under default
    baseline) are still structurally excluded — buying any of them would
    breach the 5% single-stock cap.
    """
    top_n = max(len(selected), 1)
    total_value = account_total_value(account_state)
    target_value = total_value / top_n
    max_single_weight = safe_float(config.get("trading", {}).get("max_single_weight"))
    if max_single_weight is not None and max_single_weight > 0:
        max_single_value = total_value * max_single_weight
        target_value = min(target_value, max_single_value)
    else:
        max_single_value = None
    lot_size = int(config.get("trading", {}).get("lot_size", 100))
    current_positions = account_state.get("positions", {})

    def _compute_target_shares(price: float) -> int:
        raw = int(target_value // (price * lot_size)) * lot_size
        if raw == 0 and max_single_value is not None and price * lot_size <= max_single_value:
            # Tier 1: 1 lot fits under cap — buy 1 lot rather than leave slot empty.
            return lot_size
        return raw

    def _make_target(code: str, row, price: float, target_shares: int,
                       *, fallback: bool = False) -> dict[str, Any]:
        base_reason = row.get("score_detail", "") if hasattr(row, "get") else ""
        if fallback:
            base_reason = f"{base_reason};fallback_fill" if base_reason else "fallback_fill"
        return {
            "code": code,
            "name": row.get("name", ""),
            "industry": row.get("industry") or UNCLASSIFIED,
            "target_shares": target_shares,
            "target_value": round(target_shares * price, 2),
            "target_weight": round((target_shares * price / total_value), 6) if total_value else None,
            "reference_price": price,
            "score": row.get("score"),
            "reason": base_reason,
        }

    targets: dict[str, dict[str, Any]] = {}
    filled_count = 0
    for _, row in selected.iterrows():
        price = safe_float(row.get("latest_price"))
        if price is None or price <= 0:
            continue
        code = str(row["code"]).zfill(6)
        target_shares = _compute_target_shares(price)
        targets[code] = _make_target(code, row, price, target_shares)
        if target_shares > 0:
            filled_count += 1

    # Tier 2: skip-down fill from fallback_pool when selected leaves slots empty.
    if fallback_pool is not None and filled_count < top_n:
        already_seen = set(targets.keys())
        need = top_n - filled_count
        for _, row in fallback_pool.iterrows():
            if need <= 0:
                break
            price = safe_float(row.get("latest_price"))
            if price is None or price <= 0:
                continue
            code = str(row["code"]).zfill(6)
            if code in already_seen:
                continue
            target_shares = _compute_target_shares(price)
            if target_shares == 0:
                continue  # still unbuyable — try next candidate
            targets[code] = _make_target(code, row, price, target_shares, fallback=True)
            already_seen.add(code)
            need -= 1

    for code, position in current_positions.items():
        targets.setdefault(
            code,
            {
                "code": code,
                "name": position.get("name", ""),
                "industry": position.get("industry") or UNCLASSIFIED,
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
                "industry": target.get("industry") or UNCLASSIFIED,
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
    provider: DataProvider,
    as_of: str | date | None = None,
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
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
    provider: DataProvider,
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
        preserved_industry = current.get("industry") or order.get("industry") or UNCLASSIFIED
        preserved_hold_since = current.get("hold_since") or trade_date
        account.setdefault("positions", {})[code] = {
            "name": order.get("name", code),
            "industry": preserved_industry,
            "shares": new_shares,
            "available_shares": min(available_shares, new_shares),
            "avg_cost": round(avg_cost, 4),
            "last_buy_date": trade_date,
            "hold_since": preserved_hold_since,
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
    provider: DataProvider,
    as_of: str | date | None = None,
    notes: str = "",
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
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


def execution_quote(provider: DataProvider, code: str, execute_after: str, side: str, run_date: str) -> ExecutionQuote:
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
