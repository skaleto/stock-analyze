"""Backtest engine main loop.

Drives a day-by-day historical replay over [start, end] using a thin
``BacktestProvider`` that satisfies the subset of the ``DataProvider``
interface that the simulator's execution + NAV code paths actually need
(``next_trading_day`` / ``price_snapshot`` / ``benchmark_close`` /
``execution_quote`` / ``execution_price``).

Signal generation on Fridays is currently a simplified top-N selection (low
PE first) — not the full overlay-driven factor pipeline. The MVP delivers a
working pipeline end-to-end; a follow-up task can bridge the full
``factor_pipeline`` into the backtest's ``PointInTimeView`` if/when needed.

Output schema matches the forward simulator (daily_nav.csv, trades.csv,
signals.csv, performance_summary.json), so the same dashboard renderer can
visualize both.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from .data_view import PointInTimeView
from .types import BacktestMetrics, BacktestResult


SIGNAL_DAY_WEEKDAY = 4  # Friday


# ---------------------------------------------------------------------------
# Lightweight stand-ins for DataProvider's return types
# (matches stock_analyze.data_provider.{PriceSnapshot,ExecutionQuote} shape)
# ---------------------------------------------------------------------------


@dataclass
class _PriceSnapshot:
    code: str
    trade_date: Optional[str]
    close: Optional[float]
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    amount: Optional[float] = None
    momentum_20: Optional[float] = None
    momentum_60: Optional[float] = None
    avg_amount_20: Optional[float] = None
    low_volatility_60: Optional[float] = None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = "backtest"
    warning: str = ""


@dataclass
class _ExecutionQuote:
    code: str
    trade_date: Optional[str]
    price: Optional[float]
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False
    source: str = "backtest"
    reason: str = ""


# ---------------------------------------------------------------------------
# BacktestProvider
# ---------------------------------------------------------------------------


class BacktestProvider:
    """Minimal provider that reads from ``PointInTimeView``.

    Only implements the methods that the simulator's ``execute_due_orders``
    and ``update_nav`` paths call. Signal generation is handled separately
    by the engine (does NOT call ``generate_rebalance_orders``).
    """

    def __init__(self, view: PointInTimeView, trade_days: List[date]) -> None:
        self._view = view
        self._trade_days = sorted(trade_days)
        # Provider-protocol attribute (override target for simulator's
        # _override_provider_cache helper). Unused in backtest mode but
        # must exist so the helper doesn't trip on missing attribute.
        self.cache_dir: Optional[Path] = view.cache_root

    # ---- DataProvider methods ------------------------------------------

    def next_trading_day(self, day) -> str:
        """Return the next trading day >= ``day`` (YYYY-MM-DD string)."""
        d = _coerce_date(day)
        for td in self._trade_days:
            if td >= d:
                return td.isoformat()
        # No future day in window → return last known
        if self._trade_days:
            return self._trade_days[-1].isoformat()
        return d.isoformat()

    def price_snapshot(self, code: str, as_of: Optional[str] = None,
                         spot_row: Optional[dict] = None) -> _PriceSnapshot:
        """Return a snapshot from backtest_cache/daily/<as_of>.csv."""
        d = _coerce_date(as_of) if as_of else self._view.as_of
        daily = self._view.daily(as_of=d)
        if daily.empty:
            return _PriceSnapshot(code=code, trade_date=None, close=None,
                                    paused=True, source="backtest_miss")
        row = daily[daily["ts_code"] == code]
        if row.empty:
            return _PriceSnapshot(code=code, trade_date=d.isoformat(),
                                    close=None, paused=True,
                                    source="backtest_miss")
        r = row.iloc[0]
        return _PriceSnapshot(
            code=code,
            trade_date=d.isoformat(),
            close=_safe_float(r.get("close")),
            open=_safe_float(r.get("open")),
            high=_safe_float(r.get("high")),
            low=_safe_float(r.get("low")),
            amount=_safe_float(r.get("amount")),
            source="backtest_view",
        )

    def benchmark_close(self, code: str,
                          as_of: Optional[str] = None) -> tuple[Optional[float], Optional[str]]:
        """Best-effort: read benchmark close from daily file by ts_code."""
        d = _coerce_date(as_of) if as_of else self._view.as_of
        daily = self._view.daily(as_of=d)
        if daily.empty:
            return None, None
        # Heuristic: benchmark "000300" -> "000300.SH"
        bench_ts = _normalize_benchmark(code)
        row = daily[daily["ts_code"] == bench_ts]
        if row.empty:
            return None, None
        return _safe_float(row.iloc[0].get("close")), d.isoformat()

    def execution_quote(self, code: str, execute_after: str,
                          side: str, as_of: Optional[str] = None) -> _ExecutionQuote:
        """Return an open-price quote for the trade day >= ``execute_after``."""
        target = _coerce_date(execute_after)
        actual = next((td for td in self._trade_days if td >= target), None)
        if actual is None:
            return _ExecutionQuote(code=code, trade_date=None, price=None,
                                     reason="no_trade_day")
        daily = self._view.daily(as_of=actual)
        if daily.empty:
            return _ExecutionQuote(code=code, trade_date=actual.isoformat(),
                                     price=None, reason="no_daily_data")
        row = daily[daily["ts_code"] == code]
        if row.empty:
            return _ExecutionQuote(code=code, trade_date=actual.isoformat(),
                                     price=None, reason="code_missing")
        return _ExecutionQuote(
            code=code, trade_date=actual.isoformat(),
            price=_safe_float(row.iloc[0].get("open")),
            source="backtest_open",
        )

    def execution_price(self, code: str, execute_after: str,
                         side: str) -> tuple[Optional[float], Optional[str]]:
        q = self.execution_quote(code, execute_after, side)
        return q.price, q.trade_date

    # ---- Health/ledger stubs (called by simulator) ---------------------

    def record_health(self, *args: Any, **kwargs: Any) -> None:
        """No-op for backtest mode."""

    def persist_health(self) -> None:
        """No-op for backtest mode."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _normalize_benchmark(code: str) -> str:
    """000300 → 000300.SH ; 000905 → 000905.SH (best-effort heuristic)."""
    if not code:
        return ""
    code = str(code).strip()
    if "." in code:
        return code
    return f"{code}.SH"


# ---------------------------------------------------------------------------
# Trade calendar
# ---------------------------------------------------------------------------


def _load_trade_days(cache_root: Path, start: date, end: date) -> List[date]:
    path = cache_root / "trade_cal.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype={"cal_date": str})
    if "is_open" in df.columns:
        df = df[df["is_open"] == 1]
    days = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date.tolist()
    return [d for d in days if start <= d <= end]


def _is_signal_day(d: date) -> bool:
    return d.weekday() == SIGNAL_DAY_WEEKDAY


# ---------------------------------------------------------------------------
# Signal generation (MVP: simple low-PE top-N from PointInTimeView)
# ---------------------------------------------------------------------------


def _compute_signals(view: PointInTimeView, overlay: dict,
                       as_of: date, universe: List[str]) -> List[dict]:
    """Produce one signal-row per (account, code) for the top-N selection.

    MVP simplification: rank candidates by ascending PE_TTM (low PE first),
    take top N from each account's universe. Real forward simulator uses the
    full factor_pipeline; bridging that is future work.
    """
    daily_basic = view.daily_basic(as_of=as_of)
    if daily_basic.empty:
        return []

    available_codes = set(view.universe(as_of=as_of, indices=universe))
    df = daily_basic[daily_basic["ts_code"].isin(available_codes)].copy()
    if df.empty:
        return []

    # Drop missing PE and sort ascending (low first)
    df = df.dropna(subset=["pe_ttm"])
    df = df[df["pe_ttm"] > 0]
    df = df.sort_values("pe_ttm")

    rows: List[dict] = []
    for account in overlay.get("accounts", []):
        top_n = int(account.get("top_n", 50))
        selected = df.head(top_n)
        for _, r in selected.iterrows():
            rows.append({
                "signal_date": as_of.isoformat(),
                "account_id": account["id"],
                "ts_code": r["ts_code"],
                "score": -float(r["pe_ttm"]),  # higher score = better
            })
    return rows


# ---------------------------------------------------------------------------
# Pending order generation (writes pending_orders.json for simulator)
# ---------------------------------------------------------------------------


def _build_pending_batch(signals: List[dict], overlay: dict, as_of: date,
                          execute_after: date, view: PointInTimeView,
                          state: dict, run_id: str) -> List[dict]:
    """Translate signals into pending orders the simulator can execute.

    For each account, target equal-weighted positions across its top-N. Compute
    delta vs current holdings (in state["positions"]) and emit BUY/SELL orders
    with execute_after = next trade day after as_of.
    """
    batches: List[dict] = []
    daily = view.daily(as_of=as_of)
    if daily.empty:
        return batches

    price_map = {r["ts_code"]: float(r["close"])
                  for _, r in daily.iterrows()
                  if pd.notna(r.get("close"))}

    for account in overlay.get("accounts", []):
        acc_id = account["id"]
        cash = float(state["cash_by_account"].get(acc_id, account.get("cash", 0)))
        # Current positions (qty per ts_code) for this account
        current_qty = {
            code: pos["qty"]
            for code, pos in state.get("positions", {}).items()
            if pos.get("account_id") == acc_id
        }
        acc_signals = [s for s in signals if s["account_id"] == acc_id]
        if not acc_signals:
            continue

        # Total value for this account at current prices
        positions_value = sum(qty * price_map.get(code, 0.0)
                              for code, qty in current_qty.items())
        total_value = cash + positions_value
        target_codes = [s["ts_code"] for s in acc_signals]
        target_value_each = total_value / max(len(target_codes), 1)

        orders: List[dict] = []
        lot_size = int(overlay.get("trading", {}).get("lot_size", 100))

        # SELL anything not in target
        for code, qty in current_qty.items():
            if code not in target_codes and qty > 0:
                orders.append({
                    "ts_code": code,
                    "side": "SELL",
                    "quantity": int(qty),
                    "account_id": acc_id,
                })

        # BUY top-N targets to reach target_value_each
        for code in target_codes:
            price = price_map.get(code)
            if not price or price <= 0:
                continue
            target_qty = int((target_value_each / price) // lot_size * lot_size)
            current = current_qty.get(code, 0)
            delta = target_qty - current
            if delta > 0:
                orders.append({
                    "ts_code": code,
                    "side": "BUY",
                    "quantity": int(delta),
                    "account_id": acc_id,
                })
            elif delta < 0:
                orders.append({
                    "ts_code": code,
                    "side": "SELL",
                    "quantity": int(-delta),
                    "account_id": acc_id,
                })

        if orders:
            batches.append({
                "run_id": run_id,
                "account_id": acc_id,
                "signal_date": as_of.isoformat(),
                "execute_after": execute_after.isoformat(),
                "orders": orders,
            })

    return batches


# ---------------------------------------------------------------------------
# Execution + NAV
# ---------------------------------------------------------------------------


def _execute_pending(pending: List[dict], trade_day: date,
                       provider: BacktestProvider, state: dict,
                       overlay: dict) -> List[dict]:
    """Execute orders whose execute_after <= trade_day. Return trade rows."""
    trades: List[dict] = []
    remaining: List[dict] = []
    trading = overlay.get("trading", {})
    commission_rate = float(trading.get("commission_rate", 0.0003))
    stamp_tax_rate = float(trading.get("stamp_tax_rate", 0.0005))
    slippage_rate = float(trading.get("slippage_rate", 0.0))
    min_commission = float(trading.get("min_commission", 5))

    for batch in pending:
        execute_after = date.fromisoformat(batch["execute_after"])
        if execute_after > trade_day:
            remaining.append(batch)
            continue
        acc_id = batch["account_id"]
        unfilled: List[dict] = []
        for order in batch["orders"]:
            quote = provider.execution_quote(
                order["ts_code"], execute_after.isoformat(),
                order["side"], as_of=trade_day.isoformat(),
            )
            if quote.price is None or quote.price <= 0:
                # Carry forward to next attempt
                unfilled.append(order)
                continue
            price = quote.price * (1 + slippage_rate if order["side"] == "BUY"
                                    else 1 - slippage_rate)
            qty = order["quantity"]
            gross = price * qty
            commission = max(gross * commission_rate, min_commission)
            stamp = gross * stamp_tax_rate if order["side"] == "SELL" else 0.0
            net = gross + commission + stamp if order["side"] == "BUY" else gross - commission - stamp

            # Cash + positions update
            if order["side"] == "BUY":
                cash = state["cash_by_account"].get(acc_id, 0.0)
                if net > cash:
                    unfilled.append(order)
                    continue
                state["cash_by_account"][acc_id] = cash - net
                pos = state["positions"].setdefault(
                    order["ts_code"],
                    {"qty": 0, "account_id": acc_id, "avg_cost": price},
                )
                pos["qty"] = pos.get("qty", 0) + qty
            else:  # SELL
                pos = state["positions"].get(order["ts_code"], {})
                cur_qty = pos.get("qty", 0)
                if cur_qty < qty:
                    qty = cur_qty
                    if qty <= 0:
                        unfilled.append(order)
                        continue
                pos["qty"] = cur_qty - qty
                state["cash_by_account"][acc_id] = (
                    state["cash_by_account"].get(acc_id, 0.0) + net
                )
                if pos["qty"] <= 0:
                    state["positions"].pop(order["ts_code"], None)

            trades.append({
                "date": trade_day.isoformat(),
                "account_id": acc_id,
                "ts_code": order["ts_code"],
                "side": order["side"],
                "quantity": qty,
                "price": price,
                "commission": commission,
                "stamp_tax": stamp,
                "slippage": gross * slippage_rate,
            })

        if unfilled:
            remaining.append({**batch, "orders": unfilled})

    # Mutate caller's pending list in-place
    pending.clear()
    pending.extend(remaining)
    return trades


def _update_nav(trade_day: date, state: dict, overlay: dict,
                  provider: BacktestProvider) -> List[dict]:
    """Compute daily NAV per account and return rows."""
    rows: List[dict] = []
    for account in overlay.get("accounts", []):
        acc_id = account["id"]
        cash = float(state["cash_by_account"].get(acc_id, 0.0))
        positions_value = 0.0
        for code, pos in state.get("positions", {}).items():
            if pos.get("account_id") != acc_id:
                continue
            snap = provider.price_snapshot(code, as_of=trade_day.isoformat())
            close = snap.close if snap.close is not None else pos.get("avg_cost", 0.0)
            positions_value += pos.get("qty", 0) * (close or 0.0)
        rows.append({
            "date": trade_day.isoformat(),
            "account_id": acc_id,
            "cash": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "total_value": round(cash + positions_value, 2),
        })
    return rows


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _compute_metrics(daily_nav: pd.DataFrame) -> BacktestMetrics:
    if daily_nav.empty:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    portfolio = daily_nav.groupby("date")["total_value"].sum().sort_index()
    if len(portfolio) < 2:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    returns = portfolio.pct_change().dropna()
    if returns.empty:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)

    cum = float(portfolio.iloc[-1] / portfolio.iloc[0] - 1)
    daily_mean = float(returns.mean())
    daily_std = float(returns.std()) if len(returns) > 1 else 0.0
    annual = (1 + daily_mean) ** 252 - 1
    vol = daily_std * (252 ** 0.5)
    sharpe = (annual / vol) if vol > 0 else 0.0

    cummax = portfolio.cummax()
    drawdown = portfolio / cummax - 1
    max_dd = float(drawdown.min())

    return BacktestMetrics(
        cum_return=cum,
        annual_return=float(annual),
        sharpe=float(sharpe),
        max_drawdown=max_dd,
        information_ratio=float(sharpe),  # IR ≈ sharpe (no benchmark yet in MVP)
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _init_state(overlay: dict) -> dict:
    state = {
        "cash_by_account": {},
        "positions": {},
    }
    for account in overlay.get("accounts", []):
        state["cash_by_account"][account["id"]] = float(account.get("cash", 0))
    return state


def run_backtest(
    overlay: dict,
    start: date,
    end: date,
    universe: List[str],
    market_data_root: Path,
    out_dir: Path,
    *,
    in_memory: bool = False,
    run_id: str = "backtest",
) -> BacktestResult:
    """Execute a historical backtest of ``overlay`` over [start, end].

    Parameters
    ----------
    overlay
        Strategy config dict (same shape as ``configs/agents/*.yaml``
        merged with baseline). Must include ``accounts`` and ``trading``.
    start / end
        Inclusive window bounds.
    universe
        List of index short-names (``hs300`` / ``zz500``) to sample from.
    market_data_root
        Path to backtest_cache/ produced by ``prepare-backtest-data``.
    out_dir
        Where to write daily_nav.csv / trades.csv / signals.csv etc.
    in_memory
        If True, skip per-day disk writes (only emit final products).

    Returns
    -------
    BacktestResult
        Container with out_dir + summary metrics.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    trade_days = _load_trade_days(market_data_root, start, end)
    if not trade_days:
        # Empty window — still produce empty outputs
        (out_dir / "daily_nav.csv").write_text(
            "date,account_id,cash,positions_value,total_value\n"
        )
        (out_dir / "trades.csv").write_text(
            "date,account_id,ts_code,side,quantity,price,"
            "commission,stamp_tax,slippage\n"
        )
        (out_dir / "signals.csv").write_text(
            "signal_date,account_id,ts_code,score\n"
        )
        return BacktestResult(out_dir=out_dir, start=start, end=end,
                               metrics=BacktestMetrics(0, 0, 0, 0, 0))

    state = _init_state(overlay)
    view = PointInTimeView(as_of=trade_days[-1], cache_root=market_data_root)
    provider = BacktestProvider(view, trade_days)

    pending: List[dict] = []
    all_trades: List[dict] = []
    all_nav_rows: List[dict] = []
    all_signals: List[dict] = []

    for d in trade_days:
        # Update view to current day (mutating as_of so daily lookups work)
        view.as_of = d

        # 1. Execute any pending orders whose execute_after <= today
        new_trades = _execute_pending(pending, d, provider, state, overlay)
        all_trades.extend(new_trades)

        # 2. Update NAV (mark-to-market)
        nav_rows = _update_nav(d, state, overlay, provider)
        all_nav_rows.extend(nav_rows)

        # 3. Friday: generate signals + pending orders for next trade day
        if _is_signal_day(d):
            signals = _compute_signals(view, overlay, d, universe)
            all_signals.extend(signals)
            if signals:
                # execute_after = next trade day after today
                exec_after = next((td for td in trade_days if td > d), d)
                batches = _build_pending_batch(
                    signals, overlay, d, exec_after, view, state, run_id,
                )
                pending.extend(batches)

    # Persist final products
    daily_nav_df = pd.DataFrame(all_nav_rows,
                                  columns=["date", "account_id", "cash",
                                           "positions_value", "total_value"])
    daily_nav_df.to_csv(out_dir / "daily_nav.csv", index=False)

    trades_df = pd.DataFrame(all_trades,
                              columns=["date", "account_id", "ts_code", "side",
                                       "quantity", "price", "commission",
                                       "stamp_tax", "slippage"])
    trades_df.to_csv(out_dir / "trades.csv", index=False)

    signals_df = pd.DataFrame(all_signals,
                               columns=["signal_date", "account_id", "ts_code",
                                        "score"])
    signals_df.to_csv(out_dir / "signals.csv", index=False)

    metrics = _compute_metrics(daily_nav_df)
    summary = {
        "cum_return": metrics.cum_return,
        "annual_return": metrics.annual_return,
        "sharpe": metrics.sharpe,
        "max_drawdown": metrics.max_drawdown,
        "information_ratio": metrics.information_ratio,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n_trade_days": len(trade_days),
        "n_trades": len(all_trades),
    }
    (out_dir / "performance_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )

    return BacktestResult(out_dir=out_dir, start=start, end=end, metrics=metrics)
