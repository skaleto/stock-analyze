"""Backtest engine main loop.

Drives a day-by-day historical replay over [start, end] using a thin
``BacktestProvider`` that satisfies the subset of the ``DataProvider``
interface that the simulator's execution + NAV code paths actually need
(``next_trading_day`` / ``price_snapshot`` / ``benchmark_close`` /
``execution_quote`` / ``execution_price``).

Signal generation follows the baseline schedule and runs the full overlay ``factor_pipeline``
(winsorize → z-score → industry-neutralize → weighted combine) when
``backtest.use_full_pipeline`` is true — the default in the A-share
baseline — driving selection from the same factor mix as live trading. When
the flag is off it falls back to a legacy low-PE top-N proxy.

Output schema matches the forward simulator (daily_nav.csv, trades.csv,
signals.csv, performance_summary.json), so the same dashboard renderer can
visualize both.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

import pandas as pd

from ....research.execution_policy import estimate_market_impact_bps
from ....research.regime_policy import apply_regime_policy, regime_decision_from_state
from ....research.strategy_ensemble import risk_adjusted_target_weights
from .data_view import PointInTimeView
from .exceptions import BacktestDataUnavailable
from .types import BacktestMetrics, BacktestResult


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


_POSITION_KEY_SEP = "::"


def _position_key(account_id: str, ts_code: str) -> str:
    return f"{account_id}{_POSITION_KEY_SEP}{ts_code}"


def _position_code(key: Any, pos: dict) -> str:
    code = pos.get("ts_code")
    if code:
        return str(code)
    key_s = str(key)
    if _POSITION_KEY_SEP in key_s:
        return key_s.split(_POSITION_KEY_SEP, 1)[1]
    return key_s


def _account_positions(state: dict, account_id: str) -> Iterator[Tuple[str, dict]]:
    for key, pos in state.get("positions", {}).items():
        if pos.get("account_id") == account_id:
            yield _position_code(key, pos), pos


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
        """Read a benchmark close from its dedicated point-in-time history."""
        d = _coerce_date(as_of) if as_of else self._view.as_of
        return self._view.benchmark_close(code, as_of=d)

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
    # trade_cal.csv is stored newest-first; the engine loop relies on
    # chronological order (pending orders execute_after the *next* day, and
    # NAV/return series must be ascending). Without this sort the loop runs
    # backwards and no pending order ever becomes due → zero trades.
    return sorted(d for d in days if start <= d <= end)


def _is_signal_day(d: date, signal_day: str = "every_trading_day") -> bool:
    normalized = str(signal_day or "every_trading_day").strip().lower()
    if normalized == "every_trading_day":
        return True
    if normalized in {"friday", "weekly_friday"}:
        return d.weekday() == 4
    raise ValueError(f"unsupported_signal_day:{signal_day}")


# ---------------------------------------------------------------------------
# Signal generation (two scoring paths: full overlay factor_pipeline
# preferred; legacy low-PE top-N from PointInTimeView as fallback)
# ---------------------------------------------------------------------------


def _compute_signals(view: PointInTimeView, overlay: dict,
                       as_of: date, universe: List[str]) -> List[dict]:
    """Produce one signal-row per (account, code) for the top-N selection.

    Two scoring paths (OpenSpec change bridge-factor-pipeline-into-backtest):

    - ``overlay.backtest.use_full_pipeline == True`` → delegate to
      ``scoring.score_with_overlay``, which runs the overlay's actual
      factor mix through the same ``factor_pipeline.process_factors`` as
      live trading. This makes the gate test the real overlay.
    - Otherwise → the legacy low-PE top-N proxy below, used only as a
      fallback when ``use_full_pipeline`` is off. The A-share baseline sets
      the flag to true, so the full pipeline is the normal path.
    """
    if overlay.get("backtest", {}).get("use_full_pipeline", False):
        from .scoring import score_with_overlay
        return score_with_overlay(view, overlay, as_of, universe)

    daily_basic = view.daily_basic(as_of=as_of)
    if daily_basic.empty:
        return []

    rows: List[dict] = []
    for account in overlay.get("accounts", []):
        scope = str(account.get("scope") or "")
        if scope and scope not in universe:
            continue
        available_codes = set(
            view.universe(as_of=as_of, indices=[scope] if scope else universe)
        )
        df = daily_basic[daily_basic["ts_code"].isin(available_codes)].copy()
        if df.empty:
            continue
        df = df.dropna(subset=["pe_ttm"])
        df = df[df["pe_ttm"] > 0].sort_values("pe_ttm")
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

    Uses the same covariance/turnover-aware target policy as live trading,
    then emits deltas for next-day execution.
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
            for code, pos in _account_positions(state, acc_id)
        }
        acc_signals = [s for s in signals if s["account_id"] == acc_id]
        if not acc_signals:
            continue

        # Total value for this account at current prices
        positions_value = sum(qty * price_map.get(code, 0.0)
                              for code, qty in current_qty.items())
        total_value = cash + positions_value
        target_codes = [s["ts_code"] for s in acc_signals]
        return_history = view.return_history(target_codes, as_of=as_of, days=90)
        volatility = (
            return_history.std(ddof=0) * (252.0 ** 0.5)
            if not return_history.empty
            else pd.Series(dtype=float)
        )
        candidates = pd.DataFrame([
            {
                "code": signal["ts_code"],
                "score": signal.get("score", 0.0),
                "low_volatility_60": volatility.get(signal["ts_code"]),
                "momentum_20": (
                    float((1.0 + return_history[signal["ts_code"]].dropna().tail(20)).prod() - 1.0)
                    if signal["ts_code"] in return_history.columns
                    and not return_history[signal["ts_code"]].dropna().empty
                    else None
                ),
            }
            for signal in acc_signals
        ])
        profile = "defensive" if str(overlay.get("agent_id") or "").lower() == "claude" else "trend"
        benchmark_returns = view.benchmark_return_history(
            str(account.get("benchmark") or "000300.SH"),
            as_of=as_of,
            days=60,
        )
        if len(benchmark_returns) < 20:
            regime_state = "unknown"
        else:
            momentum_20 = float((1.0 + benchmark_returns.tail(20)).prod() - 1.0)
            volatility_20 = float(benchmark_returns.tail(20).std(ddof=0) * (252.0 ** 0.5))
            if momentum_20 < -0.03 or volatility_20 > 0.30:
                regime_state = "risk_off"
            elif momentum_20 > 0.02 and volatility_20 < 0.28:
                regime_state = "risk_on"
            else:
                regime_state = "mixed"
        regime_decision = regime_decision_from_state(
            regime_state,
            profile=profile,
            source_date=as_of.isoformat(),
            warning="backtest_regime_insufficient" if regime_state == "unknown" else "",
        )
        candidates = apply_regime_policy(candidates, regime_decision, profile=profile)
        current_weights = {
            str(code).split(".", 1)[0].zfill(6): qty * price_map.get(code, 0.0) / total_value
            for code, qty in current_qty.items()
            if total_value > 0
        }
        controls = overlay.get("portfolio_controls", {}) or {}
        defensive = str(overlay.get("agent_id") or "").lower() == "claude"
        target_weights = risk_adjusted_target_weights(
            candidates,
            top_n=len(target_codes),
            max_single_weight=float(overlay.get("trading", {}).get("max_single_weight", 1.0)),
            current_weights=current_weights,
            turnover_penalty=float(controls.get("turnover_penalty", 0.45 if defensive else 0.20)),
            min_trade_weight=float(controls.get("min_trade_weight", 0.003 if defensive else 0.001)),
            return_history=return_history,
            risk_aversion=1.35 if defensive else 0.90,
            gross_exposure=regime_decision.gross_exposure,
        )

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

        # Rebalance top-N targets to covariance- and turnover-aware weights.
        for code in target_codes:
            price = price_map.get(code)
            if not price or price <= 0:
                continue
            weight_code = str(code).split(".", 1)[0].zfill(6)
            target_value = total_value * float(target_weights.get(weight_code, 0.0))
            target_qty = int((target_value / price) // lot_size * lot_size)
            current = current_qty.get(code, 0)
            delta = target_qty - current
            daily_row = daily.loc[daily["ts_code"].eq(code)]
            amount = (
                float(pd.to_numeric(daily_row["amount"], errors="coerce").dropna().iloc[-1])
                if not daily_row.empty and "amount" in daily_row.columns
                and pd.to_numeric(daily_row["amount"], errors="coerce").notna().any()
                else None
            )
            impact_bps = estimate_market_impact_bps(
                order_value=abs(delta) * price,
                avg_daily_amount=amount,
                volatility=volatility.get(code),
                baseline_bps=float(overlay.get("trading", {}).get("slippage_rate", 0.0)) * 10_000.0,
            )
            if delta > 0:
                orders.append({
                    "ts_code": code,
                    "side": "BUY",
                    "quantity": int(delta),
                    "account_id": acc_id,
                    "estimated_impact_bps": impact_bps,
                })
            elif delta < 0:
                orders.append({
                    "ts_code": code,
                    "side": "SELL",
                    "quantity": int(-delta),
                    "account_id": acc_id,
                    "estimated_impact_bps": impact_bps,
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
            effective_slippage_rate = max(
                slippage_rate,
                float(order.get("estimated_impact_bps", 0.0) or 0.0) / 10_000.0,
            )
            price = quote.price * (1 + effective_slippage_rate if order["side"] == "BUY"
                                    else 1 - effective_slippage_rate)
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
                key = _position_key(acc_id, order["ts_code"])
                pos = state["positions"].setdefault(
                    key,
                    {
                        "ts_code": order["ts_code"],
                        "qty": 0,
                        "account_id": acc_id,
                        "avg_cost": price,
                    },
                )
                pos["qty"] = pos.get("qty", 0) + qty
            else:  # SELL
                key = _position_key(acc_id, order["ts_code"])
                pos = state["positions"].get(key)
                if pos is None:
                    legacy_pos = state["positions"].get(order["ts_code"])
                    if legacy_pos and legacy_pos.get("account_id") == acc_id:
                        key = order["ts_code"]
                        pos = legacy_pos
                    else:
                        pos = {}
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
                    state["positions"].pop(key, None)

            trades.append({
                "date": trade_day.isoformat(),
                "account_id": acc_id,
                "ts_code": order["ts_code"],
                "side": order["side"],
                "quantity": qty,
                "price": price,
                "commission": commission,
                "stamp_tax": stamp,
                "slippage": abs(price - quote.price) * qty,
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
        for code, pos in _account_positions(state, acc_id):
            snap = provider.price_snapshot(code, as_of=trade_day.isoformat())
            close = snap.close if snap.close is not None else pos.get("avg_cost", 0.0)
            positions_value += pos.get("qty", 0) * (close or 0.0)
        benchmark_code = account.get("benchmark")
        benchmark_close = None
        benchmark_date = None
        if benchmark_code:
            benchmark_close, benchmark_date = provider.benchmark_close(
                str(benchmark_code),
                as_of=trade_day.isoformat(),
            )
        rows.append({
            "date": trade_day.isoformat(),
            "account_id": acc_id,
            "cash": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "total_value": round(cash + positions_value, 2),
            "benchmark_code": benchmark_code,
            "benchmark_close": benchmark_close,
            "benchmark_date": benchmark_date,
        })
    return rows


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _compute_metrics(daily_nav: pd.DataFrame) -> BacktestMetrics:
    if daily_nav.empty:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    required = {"date", "account_id", "total_value", "benchmark_close"}
    missing = sorted(required.difference(daily_nav.columns))
    if missing:
        raise BacktestDataUnavailable("benchmark_history_incomplete", {"missing_columns": missing})

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

    benchmark_parts: list[pd.Series] = []
    for _account_id, group in daily_nav.groupby("account_id", sort=False):
        ordered = group.sort_values("date").copy()
        closes = pd.to_numeric(ordered["benchmark_close"], errors="coerce")
        values = pd.to_numeric(ordered["total_value"], errors="coerce")
        if closes.isna().any() or closes.empty or closes.iloc[0] <= 0 or values.isna().any():
            raise BacktestDataUnavailable("benchmark_history_incomplete")
        normalized_value = float(values.iloc[0]) * closes / float(closes.iloc[0])
        benchmark_parts.append(pd.Series(normalized_value.to_numpy(), index=ordered["date"].astype(str)))
    benchmark_value = pd.concat(benchmark_parts, axis=1).sum(axis=1).sort_index()
    aligned = pd.concat(
        [portfolio.rename("portfolio"), benchmark_value.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    active_returns = aligned["portfolio"].pct_change() - aligned["benchmark"].pct_change()
    active_returns = active_returns.dropna()
    tracking_error = float(active_returns.std(ddof=1)) if len(active_returns) > 1 else 0.0
    information_ratio = (
        float(active_returns.mean()) / tracking_error * (252 ** 0.5)
        if tracking_error > 0
        else 0.0
    )

    return BacktestMetrics(
        cum_return=cum,
        annual_return=float(annual),
        sharpe=float(sharpe),
        max_drawdown=max_dd,
        information_ratio=information_ratio,
    )


def _validate_market_data(
    view: PointInTimeView,
    provider: BacktestProvider,
    trade_days: List[date],
    overlay: dict,
) -> None:
    missing_daily = [d.isoformat() for d in trade_days if view.daily(as_of=d).empty]
    missing_basic = [d.isoformat() for d in trade_days if view.daily_basic(as_of=d).empty]
    if missing_daily or missing_basic:
        raise BacktestDataUnavailable(
            "market_history_incomplete",
            {
                "missing_daily": missing_daily[:10],
                "missing_daily_basic": missing_basic[:10],
            },
        )

    missing_benchmarks: dict[str, list[str]] = {}
    for account in overlay.get("accounts", []):
        code = str(account.get("benchmark") or "")
        if not code:
            missing_benchmarks[str(account.get("id") or "unknown")] = ["benchmark_not_configured"]
            continue
        missing_dates: list[str] = []
        for d in trade_days:
            close, benchmark_date = provider.benchmark_close(code, as_of=d.isoformat())
            if close is None or benchmark_date != d.isoformat():
                missing_dates.append(d.isoformat())
        if missing_dates:
            missing_benchmarks[code] = missing_dates[:10]
    if missing_benchmarks:
        raise BacktestDataUnavailable(
            "benchmark_history_incomplete",
            {"missing": missing_benchmarks},
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
        raise BacktestDataUnavailable(
            "trade_calendar_empty",
            {"start": start.isoformat(), "end": end.isoformat()},
        )

    state = _init_state(overlay)
    view = PointInTimeView(as_of=trade_days[-1], cache_root=market_data_root)
    provider = BacktestProvider(view, trade_days)
    _validate_market_data(view, provider, trade_days, overlay)

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

        # 3. Generate signals according to the locked baseline schedule.
        signal_day = overlay.get("schedule", {}).get("signal_day", "every_trading_day")
        if _is_signal_day(d, signal_day):
            signals = _compute_signals(view, overlay, d, universe)
            all_signals.extend(signals)
            if signals:
                # execute_after = next trade day after today
                exec_after = next((td for td in trade_days if td > d), d)
                batches = _build_pending_batch(
                    signals, overlay, d, exec_after, view, state, run_id,
                )
                pending.extend(batches)

    if not all_signals:
        raise BacktestDataUnavailable(
            "signal_generation_empty",
            {"start": start.isoformat(), "end": end.isoformat()},
        )

    # Persist final products
    daily_nav_df = pd.DataFrame(all_nav_rows,
                                  columns=["date", "account_id", "cash",
                                           "positions_value", "total_value",
                                           "benchmark_code", "benchmark_close",
                                           "benchmark_date"])
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
