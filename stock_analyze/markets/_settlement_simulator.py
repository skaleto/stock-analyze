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
from math import ceil, isnan
from pathlib import Path
from typing import Any, Protocol

from ..utils import read_json, write_json


logger = logging.getLogger(__name__)
SETTLEMENT_TRANSACTION_FILE = ".settlement_transaction.json"


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
        """Execute all pending orders with trade_date on or before ``as_of``.

        Drains the settlement queue first (so freshly-settled cash is
        available for same-day buys).
        """
        as_of = as_of or date.today()
        self._recover_settlement_transaction(store)
        state = store.load_state()
        pending = store.read_pending()
        trades: list[dict[str, Any]] = []
        remaining_pending: list[dict[str, Any]] = []

        for account_state in state.get("accounts", {}).values():
            self._drain_settlement(account_state, as_of)

        for raw in pending:
            order = self._coerce_order(raw)
            if date.fromisoformat(order.trade_date) > as_of:
                remaining_pending.append(raw)
                continue
            account_state = state["accounts"].get(order.account_id)
            if account_state is None:
                logger.warning("order references unknown account %s", order.account_id)
                remaining_pending.append({**raw, "unfilled_reason": "unknown account"})
                continue
            trade, unfilled_reason = self._execute_order(order, account_state, provider, as_of)
            if trade is not None:
                trades.append(trade)
            else:
                remaining_pending.append({**raw, "unfilled_reason": unfilled_reason or "not filled"})

        transaction_path = self._settlement_transaction_path(store)
        if transaction_path is not None:
            existing_trades = self._json_records(store.read_trades().to_dict(orient="records"))
            transaction = {
                "state": state,
                "pending": remaining_pending,
                "trades": [*existing_trades, *trades],
            }
            write_json(transaction_path, transaction)
            self._commit_settlement_transaction(store, transaction, transaction_path)
        else:
            store.save_state(state)
            store.write_pending(remaining_pending)
            if hasattr(store, "append_trades"):
                store.append_trades(trades)
            if hasattr(store, "write_positions"):
                store.write_positions(state)
        return trades

    @staticmethod
    def _settlement_transaction_path(store: Any) -> Path | None:
        data_dir = getattr(store, "data_dir", None)
        if data_dir is None or not hasattr(store, "write_trades"):
            return None
        return Path(data_dir) / SETTLEMENT_TRANSACTION_FILE

    def _recover_settlement_transaction(self, store: Any) -> None:
        path = self._settlement_transaction_path(store)
        if path is None or not path.exists():
            return
        transaction = read_json(path, None)
        if not isinstance(transaction, dict):
            raise RuntimeError("invalid settlement transaction journal")
        self._commit_settlement_transaction(store, transaction, path)

    @staticmethod
    def _commit_settlement_transaction(
        store: Any,
        transaction: dict[str, Any],
        path: Path,
    ) -> None:
        state = transaction.get("state")
        pending = transaction.get("pending")
        trades = transaction.get("trades")
        if not isinstance(state, dict) or not isinstance(pending, list) or not isinstance(trades, list):
            raise RuntimeError("invalid settlement transaction payload")
        store.save_state(state)
        store.write_pending(pending)
        store.write_trades(trades)
        store.write_positions(state)
        path.unlink(missing_ok=True)

    @classmethod
    def _json_records(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: cls._json_scalar(value) for key, value in row.items()}
            for row in rows
        ]

    @staticmethod
    def _json_scalar(value: Any) -> Any:
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, float) and isnan(value):
            return None
        return value

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

    def _execute_order(
        self,
        order,
        account_state: dict[str, Any],
        provider: Any,
        as_of: date,
    ) -> tuple[dict[str, Any] | None, str | None]:
        quote = provider.execution_quote(
            order.code,
            execute_after=as_of.isoformat(),
            side=self._quote_side(order.side),
            as_of=as_of.isoformat(),
        )
        if quote.paused or quote.price is None or quote.price <= 0:
            return None, quote.reason or "no quote"

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
                return None, "insufficient cash"
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
            return self._trade_record(
                order,
                px,
                cost,
                stamp,
                commission,
                settle_date,
                "buy",
                as_of,
                net_amount=-total_debit,
                cash_after=float(account_state["cash"]),
            ), None

        if order.side == "sell":
            existing = positions.get(order.code)
            if not existing or int(existing.get("shares", 0)) < order.shares:
                logger.info("insufficient shares for sell %s shares=%d", order.code, order.shares)
                return None, "insufficient shares"
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
            return self._trade_record(
                order,
                px,
                gross,
                stamp,
                commission,
                settle_date,
                "sell",
                as_of,
                net_amount=net,
                cash_after=float(account_state.get("cash", 0.0)),
            ), None

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
                return None, "existing long position"
            gross = order.shares * px
            stamp = gross * stamp_rate
            commission = gross * commission_rate
            fees = stamp + commission
            if fees > cash:
                return None, "insufficient cash"
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
            return self._trade_record(
                order,
                px,
                gross,
                stamp,
                commission,
                settle_date,
                "short",
                as_of,
                net_amount=-fees,
                cash_after=float(account_state["cash"]),
            ), None

        if order.side == "cover":
            existing = positions.get(order.code)
            if not existing or int(existing.get("shares", 0)) >= 0:
                return None, "no short position"
            prior_shares = int(existing["shares"])  # negative
            if order.shares > -prior_shares:
                return None, "cover exceeds short position"
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
            return self._trade_record(
                order,
                px,
                gross,
                stamp,
                commission,
                settle_date,
                "cover",
                as_of,
                net_amount=cash_back,
                cash_after=float(account_state["cash"]),
            ), None

        return None, "unsupported side"

    @staticmethod
    def _quote_side(side: str) -> str:
        """Map order side to provider quote-side ('buy' or 'sell')."""
        return "buy" if side in ("buy", "cover") else "sell"

    def _trade_record(
        self,
        order,
        price,
        gross,
        stamp,
        commission,
        settle_date,
        side_label,
        execution_date: date,
        *,
        net_amount: float,
        cash_after: float,
    ) -> dict[str, Any]:
        return {
            "trade_date": execution_date.isoformat(),
            "settle_date": settle_date,
            "account_id": order.account_id,
            "code": order.code,
            "name": "",
            "side": side_label,
            "shares": order.shares,
            "price": price,
            "gross_amount": gross,
            "commission": commission,
            "stamp_tax": stamp,
            "slippage": 0.0,
            "net_amount": net_amount,
            "cash_after": cash_after,
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
                    market_value = shares * px
                else:
                    market_value = -abs(shares) * px
                positions_value += market_value
                avg_cost = float(pos.get("avg_cost", 0.0))
                pos["last_price"] = px
                pos["market_value"] = market_value
                pos["unrealized_pnl"] = (px - avg_cost) * shares
            total = cash + coll + positions_value
            benchmark_code = account_state.get("benchmark", "")
            benchmark_close = None
            benchmark_date = as_of.isoformat()
            if benchmark_code:
                benchmark_quote = provider.price_snapshot(
                    benchmark_code,
                    as_of=as_of.isoformat(),
                )
                benchmark_close = benchmark_quote.close
                benchmark_date = benchmark_quote.trade_date or benchmark_date
            rows.append({
                "date": as_of.isoformat(),
                "account_id": account_id,
                "cash": cash,
                "cash_collateral": coll,
                "market_value": positions_value,
                "positions_value": positions_value,
                "total_value": total,
                "benchmark_code": benchmark_code,
                "benchmark_close": benchmark_close,
                "benchmark_value": None,
                "benchmark_date": benchmark_date,
                "notes": None,
                "source": f"{self.market_id}-daily",
            })
        store.append_nav(rows)
        store.save_state(state)
        if hasattr(store, "write_positions"):
            store.write_positions(state)
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
        top_n_by_account: dict[str, int] | None = None,
        hold_buffer_pct: float = 0.0,
        max_holding_days: int | None = None,
        cash_reserve_pct: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Generate buy/sell orders to bring portfolio toward the top-N of ``scored``."""
        as_of = as_of or date.today()
        trade_date = self._next_business_day(as_of, 1).isoformat()
        state = store.load_state()
        new_orders: list[dict[str, Any]] = []
        existing_pending = store.read_pending()
        existing_keys = {
            (str(order.get("account_id", "")), str(order.get("code", "")))
            for order in existing_pending
            if isinstance(order, dict)
        }

        by_account: dict[str, list[dict[str, Any]]] = {}
        for row in scored:
            by_account.setdefault(row["account_id"], []).append(row)

        for account_id, account_state in state.get("accounts", {}).items():
            rows = by_account.get(account_id, [])
            rows.sort(key=lambda r: float(r.get("score", 0.0)), reverse=True)
            account_top_n = max(int((top_n_by_account or {}).get(account_id, top_n)), 1)
            target_rows = rows[:account_top_n]
            target_codes = {r["code"] for r in target_rows}
            retention_count = max(
                account_top_n,
                ceil(account_top_n * (1.0 + max(float(hold_buffer_pct), 0.0))),
            )
            retention_codes = {r["code"] for r in rows[:retention_count]}
            cash = float(account_state.get("cash", 0.0))
            account_value = cash + float(account_state.get("cash_collateral", 0.0))
            for code, pos in account_state.get("positions", {}).items():
                shares = int(pos.get("shares", 0))
                quote = provider.price_snapshot(code, as_of=as_of.isoformat())
                px = quote.close or float(pos.get("avg_cost", 0.0))
                account_value += shares * px

            reserve = min(max(float(cash_reserve_pct), 0.0), 0.5)
            investable_value = account_value * (1.0 - reserve)
            per_target = min(
                investable_value / account_top_n,
                investable_value * max_single_weight,
            )
            remaining_buying_power = max(cash * (1.0 - reserve), 0.0)

            # Sell what's no longer wanted
            for code, pos in list(account_state.get("positions", {}).items()):
                shares = int(pos.get("shares", 0))
                holding_expired = False
                hold_since = pos.get("hold_since")
                if max_holding_days is not None and hold_since:
                    try:
                        holding_expired = (
                            as_of - date.fromisoformat(str(hold_since))
                        ).days >= int(max_holding_days)
                    except ValueError:
                        holding_expired = False
                outside_retention = code not in retention_codes
                expired_outside_target = holding_expired and code not in target_codes
                order_key = (str(account_id), str(code))
                if (
                    shares > 0
                    and (outside_retention or expired_outside_target)
                    and order_key not in existing_keys
                ):
                    new_orders.append({
                        "code": code, "side": "sell", "shares": shares,
                        "trade_date": trade_date, "account_id": account_id,
                        "target_value": 0.0,
                        "reason": "max_holding_days" if expired_outside_target else "not_in_top_n",
                    })

            # Buy / top-up for target
            for r in target_rows:
                code = r["code"]
                quote = provider.price_snapshot(code, as_of=as_of.isoformat())
                px = quote.close
                if px is None or px <= 0:
                    continue
                lot = self.mechanics.lot_size_for(code)
                current_shares = int(account_state.get("positions", {}).get(code, {}).get("shares", 0))
                target_shares = max(int(per_target / (px * lot)), 0) * lot
                delta = target_shares - current_shares
                order_key = (str(account_id), str(code))
                estimated_share_cost = px * (
                    1.0 + self.mechanics.STAMP_TAX_RATE + self.mechanics.COMMISSION_RATE
                )
                affordable_shares = (
                    max(int(remaining_buying_power / (estimated_share_cost * lot)), 0) * lot
                    if estimated_share_cost > 0
                    else 0
                )
                buy_shares = min(delta, affordable_shares)
                if buy_shares > 0 and order_key not in existing_keys:
                    new_orders.append({
                        "code": code, "side": "buy", "shares": buy_shares,
                        "trade_date": trade_date, "account_id": account_id,
                        "target_value": buy_shares * px,
                        "score": float(r.get("score", 0.0)),
                        "reason": r.get("reason", "top_n"),
                    })
                    remaining_buying_power -= buy_shares * estimated_share_cost

        store.write_pending([*existing_pending, *new_orders])
        return new_orders


__all__ = ["MechanicsProtocol", "SettlementSimulatorBase"]
