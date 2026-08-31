"""Deterministic next-open replay for permanent-portfolio targets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping

import pandas as pd


TRADE_COLUMNS = (
    "signal_date",
    "trade_date",
    "strategy",
    "role",
    "code",
    "side",
    "shares",
    "price",
    "gross_amount",
    "commission",
    "stamp_tax",
    "slippage",
    "net_amount",
    "cash_after",
    "reason",
)
NAV_COLUMNS = (
    "date",
    "strategy",
    "cash_distribution",
    "cash",
    "market_value",
    "total_value",
)
POSITION_COLUMNS = (
    "strategy",
    "role",
    "code",
    "shares",
    "last_price",
    "market_value",
)
TARGET_COLUMNS = ("signal_date", "strategy", "role", "target_weight")
PENDING_COLUMNS = ("signal_date", "strategy", "role", "target_weight", "reason")


@dataclass(frozen=True)
class ReplayResult:
    nav: pd.DataFrame
    positions: pd.DataFrame
    trades: pd.DataFrame
    targets: pd.DataFrame
    pending: pd.DataFrame


def execution_price(
    open_price: float,
    side: str,
    slippage_rate: float,
) -> float:
    direction = 1.0 if side == "BUY" else -1.0
    return float(open_price) * (1.0 + direction * float(slippage_rate))


def commission(gross: float, rate: float, minimum: float) -> float:
    if gross <= 0:
        return 0.0
    return max(float(gross) * float(rate), float(minimum))


def round_lot(shares: float, lot_size: int) -> int:
    if lot_size <= 0:
        raise ValueError("permanent_portfolio_lot_size")
    return max(0, int(float(shares)) // int(lot_size) * int(lot_size))


def _prepare_market(market: pd.DataFrame) -> pd.DataFrame:
    required = {
        "trade_date",
        "role",
        "code",
        "open",
        "close",
        "adjusted_close",
        "is_open",
    }
    if market.empty or not required.issubset(market.columns):
        raise ValueError("permanent_portfolio_replay_market")
    frame = market.copy()
    frame["trade_date"] = (
        frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    )
    frame["role"] = frame["role"].astype(str)
    frame["code"] = frame["code"].astype(str)
    if frame.duplicated(["trade_date", "role"]).any():
        raise ValueError("permanent_portfolio_replay_duplicate")
    for column in ("open", "close", "adjusted_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "adj_factor" in frame.columns:
        frame["adj_factor"] = pd.to_numeric(
            frame["adj_factor"],
            errors="coerce",
        )
    else:
        frame["adj_factor"] = frame["adjusted_close"] / frame["close"]
    if "distribution_cash_per_share" not in frame.columns:
        frame["distribution_cash_per_share"] = 0.0
    frame["distribution_cash_per_share"] = pd.to_numeric(
        frame["distribution_cash_per_share"], errors="coerce"
    )
    if (
        frame[
            [
                "close",
                "adjusted_close",
                "adj_factor",
                "distribution_cash_per_share",
            ]
        ].isna().any().any()
        or (frame[["close", "adjusted_close", "adj_factor"]] <= 0).any().any()
        or (frame["distribution_cash_per_share"] < 0).any()
    ):
        raise ValueError("permanent_portfolio_replay_price")
    frame["is_open"] = frame["is_open"].astype(bool)
    frame["economic_close"] = frame["close"]
    frame["economic_open"] = frame["open"]
    return frame.sort_values(["trade_date", "role"]).reset_index(drop=True)


def replay_strategy(
    market: pd.DataFrame,
    *,
    strategy: str,
    initial_cash: float,
    target_schedule: Mapping[str, Mapping[str, float]],
    lot_size: int = 100,
    commission_rate: float = 0.0003,
    minimum_commission: float = 5.0,
    slippage_rate: float = 0.0005,
    stamp_tax_rate: float = 0.0,
    initial_positions: Mapping[str, int] | None = None,
    initial_pending_signal: str | None = None,
    initial_pending_target: Mapping[str, float] | None = None,
    target_policy: Callable[
        [str, Mapping[str, float], pd.DataFrame],
        Mapping[str, float] | None,
    ]
    | None = None,
) -> ReplayResult:
    frame = _prepare_market(market)
    cash = float(initial_cash)
    if cash <= 0:
        raise ValueError("permanent_portfolio_initial_cash")
    roles = tuple(sorted(set(frame["role"])))
    code_by_role = (
        frame.drop_duplicates("role").set_index("role")["code"].to_dict()
    )
    positions = {
        role: int((initial_positions or {}).get(role, 0))
        for role in roles
    }
    if any(shares < 0 for shares in positions.values()):
        raise ValueError("permanent_portfolio_account_invariant")
    latest_close: dict[str, float] = {}
    schedule = {
        str(day).replace("-", "")[:8]: {
            str(role): float(weight) for role, weight in weights.items()
        }
        for day, weights in target_schedule.items()
    }
    if any(
        weight < 0
        for weights in schedule.values()
        for weight in weights.values()
    ):
        raise ValueError("permanent_portfolio_target_weight")
    processed_signals: set[str] = set()
    pending_signal = (
        str(initial_pending_signal).replace("-", "", 2)[:8]
        if initial_pending_signal is not None
        else None
    )
    pending_target = (
        {
            str(role): float(weight)
            for role, weight in initial_pending_target.items()
        }
        if initial_pending_target is not None
        else None
    )
    if (pending_signal is None) != (pending_target is None):
        raise ValueError("permanent_portfolio_pending_state")
    pending_reasons: dict[str, str] = {}
    nav_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    target_rows = [
        {
            "signal_date": signal_date,
            "strategy": strategy,
            "role": role,
            "target_weight": weight,
        }
        for signal_date, target in sorted(schedule.items())
        for role, weight in sorted(target.items())
    ]

    for trade_date, day_frame in frame.groupby("trade_date", sort=True):
        rows = {
            str(row.role): row
            for row in day_frame.itertuples(index=False)
        }
        cash_distribution = 0.0
        for role, row in rows.items():
            distribution = float(row.distribution_cash_per_share)
            if distribution > 0 and positions[role] > 0:
                credited = positions[role] * distribution
                cash += credited
                cash_distribution += credited
            latest_close[role] = float(row.economic_close)

        eligible = sorted(
            signal_date
            for signal_date in schedule
            if signal_date < trade_date and signal_date not in processed_signals
        )
        if eligible:
            pending_signal = eligible[-1]
            pending_target = dict(schedule[pending_signal])
            processed_signals.update(eligible)
            pending_reasons = {}

        if pending_signal is not None and pending_target is not None:
            open_price_by_role: dict[str, float] = {}
            blocked: dict[str, str] = {}
            for role in roles:
                row = rows.get(role)
                if (
                    row is None
                    or not bool(row.is_open)
                    or pd.isna(row.economic_open)
                    or float(row.economic_open) <= 0
                ):
                    blocked[role] = "asset_not_open"
                else:
                    open_price_by_role[role] = float(row.economic_open)

            valuation_prices = {
                role: open_price_by_role.get(role, latest_close.get(role, 0.0))
                for role in roles
            }
            total_before = cash + sum(
                positions[role] * valuation_prices[role]
                for role in roles
            )

            differences = {
                role: (
                    pending_target.get(role, 0.0) * total_before
                    - positions[role] * open_price_by_role.get(role, 0.0)
                )
                for role in roles
                if role not in blocked
            }
            for side in ("SELL", "BUY"):
                ordered_roles = sorted(
                    differences,
                    key=lambda role: (
                        differences[role]
                        if side == "SELL"
                        else -differences[role],
                        role,
                    ),
                )
                for role in ordered_roles:
                    if role in blocked:
                        continue
                    open_price = open_price_by_role[role]
                    current_value = positions[role] * open_price
                    desired_value = (
                        pending_target.get(role, 0.0) * total_before
                    )
                    difference = desired_value - current_value
                    if side == "SELL" and difference >= 0:
                        continue
                    if side == "BUY" and difference <= 0:
                        continue
                    fill_price = execution_price(
                        open_price,
                        side,
                        slippage_rate,
                    )
                    shares = round_lot(abs(difference) / fill_price, lot_size)
                    if side == "SELL":
                        shares = min(shares, positions[role])
                    else:
                        while shares > 0:
                            gross = shares * fill_price
                            fee = commission(
                                gross,
                                commission_rate,
                                minimum_commission,
                            )
                            if gross + fee <= cash + 1e-9:
                                break
                            shares -= lot_size
                    if shares <= 0:
                        continue
                    gross = shares * fill_price
                    fee = commission(
                        gross,
                        commission_rate,
                        minimum_commission,
                    )
                    tax = gross * stamp_tax_rate if side == "SELL" else 0.0
                    slippage = abs(fill_price - open_price) * shares
                    if side == "SELL":
                        positions[role] -= shares
                        net_amount = gross - fee - tax
                        cash += net_amount
                    else:
                        positions[role] += shares
                        net_amount = -(gross + fee)
                        cash += net_amount
                    if cash < -0.01 or positions[role] < 0:
                        raise ValueError("permanent_portfolio_account_invariant")
                    trade_rows.append(
                        {
                            "signal_date": pending_signal,
                            "trade_date": trade_date,
                            "strategy": strategy,
                            "role": role,
                            "code": code_by_role[role],
                            "side": side,
                            "shares": shares,
                            "price": fill_price,
                            "gross_amount": gross,
                            "commission": fee,
                            "stamp_tax": tax,
                            "slippage": slippage,
                            "net_amount": net_amount,
                            "cash_after": cash,
                            "reason": "target_rebalance",
                        }
                    )
            pending_reasons = blocked
            if not blocked:
                pending_signal = None
                pending_target = None

        market_value = sum(
            positions[role] * latest_close.get(role, 0.0)
            for role in roles
        )
        total_value = cash + market_value
        if cash < -0.01 or any(value < 0 for value in positions.values()):
            raise ValueError("permanent_portfolio_account_invariant")
        if abs(cash + market_value - total_value) > 0.01:
            raise ValueError("permanent_portfolio_asset_identity")
        nav_rows.append(
            {
                "date": trade_date,
                "strategy": strategy,
                "cash_distribution": cash_distribution,
                "cash": cash,
                "market_value": market_value,
                "total_value": total_value,
            }
        )
        if target_policy is not None and trade_date not in schedule:
            actual_weights = {
                role: (
                    positions[role] * latest_close.get(role, 0.0) / total_value
                    if total_value > 0
                    else 0.0
                )
                for role in roles
            }
            generated = target_policy(
                trade_date,
                actual_weights,
                frame.loc[frame["trade_date"].le(trade_date)].copy(),
            )
            if generated is not None:
                target = {
                    str(role): float(weight)
                    for role, weight in generated.items()
                }
                if any(weight < 0 for weight in target.values()):
                    raise ValueError("permanent_portfolio_target_weight")
                schedule[trade_date] = target
                target_rows.extend(
                    {
                        "signal_date": trade_date,
                        "strategy": strategy,
                        "role": role,
                        "target_weight": weight,
                    }
                    for role, weight in sorted(target.items())
                )

    remaining = sorted(
        signal_date
        for signal_date in schedule
        if signal_date not in processed_signals
    )
    if remaining:
        pending_signal = remaining[-1]
        pending_target = dict(schedule[pending_signal])
        pending_reasons = {
            role: "awaiting_next_open" for role in pending_target
        }
    pending_rows = []
    if pending_signal is not None and pending_target is not None:
        pending_rows = [
            {
                "signal_date": pending_signal,
                "strategy": strategy,
                "role": role,
                "target_weight": weight,
                "reason": pending_reasons.get(role, "asset_not_open"),
            }
            for role, weight in sorted(pending_target.items())
        ]
    position_rows = [
        {
            "strategy": strategy,
            "role": role,
            "code": code_by_role[role],
            "shares": shares,
            "last_price": latest_close.get(role, 0.0),
            "market_value": shares * latest_close.get(role, 0.0),
        }
        for role, shares in sorted(positions.items())
    ]
    return ReplayResult(
        nav=pd.DataFrame(nav_rows, columns=NAV_COLUMNS),
        positions=pd.DataFrame(position_rows, columns=POSITION_COLUMNS),
        trades=pd.DataFrame(trade_rows, columns=TRADE_COLUMNS),
        targets=pd.DataFrame(target_rows, columns=TARGET_COLUMNS),
        pending=pd.DataFrame(pending_rows, columns=PENDING_COLUMNS),
    )
