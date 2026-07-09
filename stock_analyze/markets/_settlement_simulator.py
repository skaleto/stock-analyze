"""Shared settlement-queue simulator base for the HK + US markets.

Extracted per OpenSpec change ``extract-yfinance-provider-base`` (C2). HK
and US simulators were near-identical (settlement queue + buy/sell/short/
cover + update_nav + generate_rebalance_orders), differing only in
settlement days (HK T+2 / US T+1), fee rates (HK stamp+commission / US
zero-fee), lot rule, and the NAV source label. All of that is now
parameterized through a small :class:`MechanicsProtocol`.

A-share is NOT affected — it has its own (Tushare-lineage) simulator.

NOTE: the short / cover accounting lifted here preserves the *current*
behavior (including the known short-sale NAV mark-to-market issue tracked
by OpenSpec change ``fix-short-sale-nav-accounting``). That fix lands in
this single base afterwards, so both markets get corrected at once.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class MechanicsProtocol(Protocol):
    """The per-market constants + lot rule the simulator base needs."""

    SETTLEMENT_DAYS: int
    STAMP_TAX_RATE: float
    COMMISSION_RATE: float
    SHORTING_COLLATERAL_RATIO: float

    def lot_size_for(self, code: str) -> int: ...


class SettlementSimulatorBase:
    """Settlement-queue paper-trading simulator, parameterized by mechanics.

    HK / US simulator modules construct a module singleton:
        _SIM = SettlementSimulatorBase(mechanics=<market>.mechanics,
                                       order_cls=<Market>Order, market_id="hk")
    and expose byte-identical-signature free functions delegating to it.
    """

    def __init__(self, mechanics: MechanicsProtocol, order_cls: type, market_id: str) -> None:
        self.mechanics = mechanics
        self.order_cls = order_cls
        self.market_id = market_id

    # --- initialize --------------------------------------------------

    def initialize(self, config: dict[str, Any], store: Any) -> dict[str, Any]:
        """Seed an empty state.json for the competition."""
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
            "market": self.market_id,
            "competition_id": config.get("competition_id", f"{self.market_id}-paper"),
            "accounts": accounts,
        }
        store.save_state(state)
        return state

    # --- settlement queue helpers ------------------------------------

    @staticmethod
    def _next_business_day(d: date, n: int) -> date:
        """Skip Sat/Sun, advance ``n`` business days. v1 ignores exchange
        holidays (acceptable for paper-trading approximation)."""
        cur = d
        moves = 0
        while moves < n:
            cur = cur + timedelta(days=1)
            if cur.weekday() < 5:  # Mon=0..Fri=4
                moves += 1
        return cur

    @staticmethod
    def _drain_settlement(account_state: dict[str, Any], as_of: date) -> float:
        """Move queued amounts whose settle_date <= as_of into ``cash``."""
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

    # --- execute due orders ------------------------------------------

    def execute_due_orders(self, store: Any, provider: Any, *, as_of: date | None = None) -> list[dict[str, Any]]:
        """Execute all pending orders with trade_date == as_of.

        Drains the settlement queue first (so freshly-settled cash is
        available for same-day buys).
        """
        as_of = as_of or date.today()
        state = store.load_state()
        pending = store.read_pending()
        trades: list[dict[str, Any]] = []
        remaining_pending: list[dict[str, Any]] = []

        for account_state in state.get("accounts", {}).values():
            self._drain_settlement(account_state, as_of)

        for raw in pending:
            order = self._coerce_order(raw)
            if order.trade_date != as_of.isoformat():
                remaining_pending.append(raw)
                continue
            account_state = state["accounts"].get(order.account_id)
            if account_state is None:
                logger.warning("order references unknown account %s", order.account_id)
                continue
            trade = self._execute_order(order, account_state, provider, as_of)
            if trade is not None:
                trades.append(trade)

        store.save_state(state)
        store.write_pending(remaining_pending)
        return trades

    def _coerce_order(self, raw: dict[str, Any]):
        return self.order_cls(
            code=raw["code"],
            side=raw["side"],
            shares=int(raw.get("shares", 0)),
            trade_date=raw["trade_date"],
            target_value=raw.get("target_value"),
            account_id=raw.get("account_id", ""),
            score=raw.get("score"),
            reason=raw.get("reason", ""),
        )

    def _execute_order(self, order, account_state: dict[str, Any], provider: Any, as_of: date) -> dict[str, Any] | None:
        quote = provider.execution_quote(
            order.code, execute_after=as_of.isoformat(), side=self._quote_side(order.side)
        )
        if quote.paused or quote.price is None or quote.price <= 0:
            return None

        px = float(quote.price)
        settle_date = self._next_business_day(as_of, self.mechanics.SETTLEMENT_DAYS).isoformat()
        stamp_rate = self.mechanics.STAMP_TAX_RATE
        commission_rate = self.mechanics.COMMISSION_RATE
        collateral_ratio = self.mechanics.SHORTING_COLLATERAL_RATIO

        positions = account_state.setdefault("positions", {})
        cash = float(account_state.get("cash", 0.0))
        collateral = float(account_state.get("cash_collateral", 0.0))

        if order.side == "buy":
            cost = order.shares * px
            stamp = cost * stamp_rate
            commission = cost * commission_rate
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
            return self._trade_record(order, px, cost, stamp, commission, settle_date, "buy")

        if order.side == "sell":
            existing = positions.get(order.code)
            if not existing or int(existing.get("shares", 0)) < order.shares:
                logger.info("insufficient shares for sell %s shares=%d", order.code, order.shares)
                return None
            gross = order.shares * px
            stamp = gross * stamp_rate
            commission = gross * commission_rate
            net = gross - stamp - commission
            # Settlement: net cash credited to queue, not cash (T+N)
            account_state.setdefault("settlement_queue", []).append(
                {"settle_date": settle_date, "amount": net}
            )
            new_shares = int(existing["shares"]) - order.shares
            if new_shares == 0:
                del positions[order.code]
            else:
                positions[order.code] = {**existing, "shares": new_shares}
            return self._trade_record(order, px, gross, stamp, commission, settle_date, "sell")

        if order.side == "short":
            # Model A (OpenSpec change fix-short-sale-nav-accounting): route
            # the short-sale PROCEEDS into cash_collateral; cash only moves by
            # fees. This keeps NAV (= cash + cash_collateral + positions_value)
            # correct, because the +gross proceeds in cash_collateral net
            # against the -gross liability that the short position contributes
            # to positions_value. With SHORTING_COLLATERAL_RATIO == 1.0 the
            # proceeds equal the 100% collateral, so "deposit proceeds" and
            # "freeze full collateral" coincide.
            existing = positions.get(order.code, {"shares": 0, "avg_cost": 0.0})
            prior_shares = int(existing.get("shares", 0))
            # Shorting on top of an existing long is not supported in v1.
            if prior_shares > 0:
                return None
            gross = order.shares * px
            stamp = gross * stamp_rate
            commission = gross * commission_rate
            fees = stamp + commission
            if fees > cash:
                return None
            account_state["cash"] = cash - fees
            account_state["cash_collateral"] = collateral + gross
            new_shares = prior_shares - order.shares  # more negative
            old_cost_basis = abs(prior_shares) * float(existing.get("avg_cost", 0.0))
            new_cost_basis = old_cost_basis + gross
            positions[order.code] = {
                "shares": new_shares,
                "avg_cost": new_cost_basis / abs(new_shares) if new_shares != 0 else 0.0,
                "last_buy_date": as_of.isoformat(),
                "hold_since": existing.get("hold_since", as_of.isoformat()),
                "short_collateral": float(existing.get("short_collateral", 0.0)) + gross,
            }
            return self._trade_record(order, px, gross, stamp, commission, settle_date, "short")

        if order.side == "cover":
            existing = positions.get(order.code)
            if not existing or int(existing.get("shares", 0)) >= 0:
                return None
            prior_shares = int(existing["shares"])  # negative
            if order.shares > -prior_shares:
                return None  # can't cover more than open short
            gross = order.shares * px  # buyback cost
            stamp = gross * stamp_rate
            commission = gross * commission_rate
            per_pos_coll = float(existing.get("short_collateral", 0.0))
            coll_released = per_pos_coll * (order.shares / abs(prior_shares))
            # Model A: cash += released_collateral − buyback − fees. The
            # P/L is embedded in (released − buyback): with 100% ratio the
            # released collateral equals shares×open_price, so
            # released − buyback = shares×(open_price − cover_price). No
            # separate pnl term (the old code added both, double-counting).
            cash_back = coll_released - gross - stamp - commission
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
            return self._trade_record(order, px, gross, stamp, commission, settle_date, "cover")

        return None

    @staticmethod
    def _quote_side(side: str) -> str:
        """Map order side to provider quote-side ('buy' or 'sell')."""
        return "buy" if side in ("buy", "cover") else "sell"

    def _trade_record(self, order, price, gross, stamp, commission, settle_date, side_label) -> dict[str, Any]:
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

    # --- update NAV --------------------------------------------------

    def update_nav(self, store: Any, provider: Any, *, as_of: date | None = None) -> list[dict[str, Any]]:
        """Compute and persist daily NAV for each account.

        total_value = cash + cash_collateral + Σ(position_market_value),
        where a short position contributes ``-|shares| × current_price``.
        """
        as_of = as_of or date.today()
        state = store.load_state()
        rows: list[dict[str, Any]] = []
        for account_id, account_state in state.get("accounts", {}).items():
            self._drain_settlement(account_state, as_of)
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
                "market_value": positions_value,
                "positions_value": positions_value,
                "total_value": total,
                "benchmark_code": account_state.get("benchmark", ""),
                "benchmark_close": None,
                "benchmark_value": None,
                "benchmark_date": as_of.isoformat(),
                "notes": None,
                "source": f"{self.market_id}-daily",
            })
        store.append_nav(rows)
        return rows

    # --- generate rebalance orders -----------------------------------

    def generate_rebalance_orders(
        self,
        store: Any,
        provider: Any,
        scored: list[dict[str, Any]],
        *,
        as_of: date | None = None,
        top_n: int = 50,
        max_single_weight: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Generate buy/sell orders to bring portfolio toward the top-N of ``scored``."""
        as_of = as_of or date.today()
        trade_date = self._next_business_day(as_of, 1).isoformat()
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
                lot = self.mechanics.lot_size_for(code)
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


__all__ = ["MechanicsProtocol", "SettlementSimulatorBase"]
