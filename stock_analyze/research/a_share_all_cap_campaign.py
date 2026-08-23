"""Frozen baseline and transparent-candidate replay for A-share all-cap research."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..config import load_config
from ..execution_costs import calculate_execution_fill
from ..factor_pipeline import process_factors
from .a_share_all_cap_contract import AllCapContract
from .a_share_all_cap_features import build_decision_calendar
from .evaluation_windows import EvaluationFold, build_account_windows
from .execution_policy import estimate_execution_cost
from .portfolio_replay import PortfolioReplayResult, replay_rule_portfolio


TRIAL_IDS = (
    "official_sleeve_index",
    "pit_sleeve_cap_weight",
    "pit_sleeve_equal_weight",
    "legacy_transparent_scope",
    "sleeve_router_only",
    "all_cap_v2",
)

_AGENT_IDS = ("claude", "codex")
_REQUIRED_TRADING = (
    "lot_size",
    "commission_rate",
    "min_commission",
    "stamp_tax_rate",
    "slippage_rate",
    "max_single_weight",
)
_REQUIRED_EVALUATION = {
    "trade_date",
    "entry_date",
    "label_end_date",
    "code",
    "stable_sleeve",
    "industry",
    "total_mv",
    "official_weight",
    "legacy_eligible",
    "entry_price",
    "entry_open",
    "entry_up_limit",
    "entry_down_limit",
    "entry_status_complete",
    "entry_status_conflict",
    "entry_suspended",
    "benchmark_entry_price",
    "avg_amount_20",
    "realized_volatility_20",
}


@dataclass(frozen=True)
class CampaignInputs:
    evaluation: pd.DataFrame
    portfolio_contract: Mapping[str, Any]
    repo_root: Path
    overlays: Mapping[str, Mapping[str, Any]] | None = None


@dataclass
class TrialEvaluation:
    trial_id: str
    evaluation: pd.DataFrame
    evaluation_dates: tuple[str, ...]
    row_keys: tuple[tuple[str, str, str], ...]
    cost_signature: str
    folds: tuple[EvaluationFold, ...]
    factor_weights: dict[str, dict[str, float]]
    replays: dict[str, PortfolioReplayResult] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignResult:
    trials: Mapping[str, TrialEvaluation]
    folds: tuple[EvaluationFold, ...]


@dataclass(frozen=True)
class NextOpenFill:
    filled_shares: int
    status: str
    reference_price: float | None = None
    execution_price: float | None = None
    gross_amount: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    slippage: float = 0.0
    cash_delta: float = 0.0
    impact_bps: float = 0.0
    participation_rate: float = 0.0


def _date_key(value: object, *, error: str) -> str:
    text = str(value or "").replace("-", "")
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed) or parsed.strftime("%Y%m%d") != text:
        raise ValueError(error)
    return text


def _bool_series(values: pd.Series, *, error: str) -> pd.Series:
    if values.isna().any():
        raise ValueError(error)
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.astype(bool)
    normalized = values.astype("string").str.strip().str.lower()
    if not normalized.isin({"true", "false", "1", "0"}).all():
        raise ValueError(error)
    return normalized.isin({"true", "1"})


def _frozen_portfolio_settings(
    inputs: CampaignInputs,
    contract: AllCapContract,
) -> tuple[dict[str, Any], str]:
    if not isinstance(contract, AllCapContract):
        raise ValueError("all_cap_campaign_contract")
    portfolio = contract.raw.get("portfolio")
    if not isinstance(portfolio, Mapping):
        raise ValueError("all_cap_campaign_portfolio_contract")
    base_fraction = float(portfolio.get("base_max_adv_fraction") or 0.0)
    hard_fraction = float(portfolio.get("hard_max_adv_fraction") or 0.0)
    if (
        not math.isclose(base_fraction, 0.02)
        or not math.isclose(hard_fraction, 0.05)
        or base_fraction > hard_fraction
    ):
        raise ValueError("all_cap_campaign_participation_contract")

    source = inputs.portfolio_contract
    trading = source.get("trading")
    if not isinstance(trading, Mapping):
        raise ValueError("all_cap_campaign_trading_contract")
    if any(name not in trading for name in _REQUIRED_TRADING):
        raise ValueError("all_cap_campaign_trading_contract")
    normalized_trading = {
        name: (
            int(trading[name])
            if name == "lot_size"
            else float(trading[name])
        )
        for name in _REQUIRED_TRADING
    }
    if (
        normalized_trading["lot_size"] <= 0
        or any(
            not math.isfinite(float(value)) or float(value) < 0.0
            for name, value in normalized_trading.items()
            if name != "lot_size"
        )
    ):
        raise ValueError("all_cap_campaign_trading_contract")
    settings = {
        "trading": normalized_trading,
        "base_max_adv_fraction": base_fraction,
        "hard_max_adv_fraction": hard_fraction,
        "settlement_days": 1,
        "sell_proceeds_reusable_same_day": False,
    }
    encoded = json.dumps(
        settings,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return settings, hashlib.sha256(encoded).hexdigest()


def _load_overlays(
    inputs: CampaignInputs,
    contract: AllCapContract,
) -> dict[str, dict[str, Any]]:
    candidates = contract.raw.get("candidates")
    if not isinstance(candidates, Mapping) or set(candidates) != set(_AGENT_IDS):
        raise ValueError("all_cap_campaign_candidates")
    supplied = inputs.overlays
    overlays: dict[str, dict[str, Any]] = {}
    root = Path(inputs.repo_root).resolve()
    for agent in _AGENT_IDS:
        candidate = candidates.get(agent)
        if not isinstance(candidate, Mapping):
            raise ValueError("all_cap_campaign_candidates")
        if candidate.get("factor_weights_policy") != "unchanged":
            raise ValueError("all_cap_campaign_factor_weights")
        if supplied is not None:
            raw_overlay = supplied.get(agent)
            if not isinstance(raw_overlay, Mapping):
                raise ValueError("all_cap_campaign_overlay_missing")
            overlay = dict(raw_overlay)
        else:
            relative = Path(str(candidate.get("strategy_source") or ""))
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("all_cap_campaign_overlay_path") from exc
            overlay = load_config(path, apply_migrations=False)
        if str(overlay.get("agent_id") or "") != agent:
            raise ValueError("all_cap_campaign_overlay_agent")
        factors = overlay.get("factors")
        if not isinstance(factors, Mapping) or not factors:
            raise ValueError("all_cap_campaign_factor_weights")
        weights = [
            float(spec.get("weight"))
            for spec in factors.values()
            if isinstance(spec, Mapping)
        ]
        if (
            len(weights) != len(factors)
            or any(not math.isfinite(value) or value <= 0.0 for value in weights)
            or not math.isclose(math.fsum(weights), 1.0, abs_tol=1e-9)
        ):
            raise ValueError("all_cap_campaign_factor_weights")
        overlays[agent] = overlay
    return overlays


def _validate_development_frame(
    evaluation: pd.DataFrame,
    contract: AllCapContract,
) -> pd.DataFrame:
    missing = _REQUIRED_EVALUATION.difference(evaluation.columns)
    if missing:
        raise ValueError(
            "all_cap_campaign_missing_columns:" + ",".join(sorted(missing))
        )
    frame = evaluation.copy()
    for column in ("trade_date", "entry_date", "label_end_date"):
        frame[column] = frame[column].map(
            lambda value: _date_key(
                value,
                error="all_cap_campaign_dates",
            )
        ).astype("string")
    frame["code"] = frame["code"].astype("string").str.split(".").str[0].str.zfill(6)
    frame["stable_sleeve"] = frame["stable_sleeve"].astype("string")
    start = contract.development_start.strftime("%Y%m%d")
    end = contract.development_end.strftime("%Y%m%d")
    date_columns = ("trade_date", "entry_date", "label_end_date")
    if any(
        not frame[column].between(start, end).all()
        for column in date_columns
    ):
        raise ValueError("all_cap_campaign_development_window")
    if not (
        frame["entry_date"].gt(frame["trade_date"]).all()
        and frame["label_end_date"].gt(frame["trade_date"]).all()
    ):
        raise ValueError("all_cap_campaign_point_in_time_dates")
    sleeves = {item.name for item in contract.sleeves}
    if set(frame["stable_sleeve"].dropna().astype(str)) != sleeves:
        raise ValueError("all_cap_campaign_sleeves")
    if frame.duplicated(
        ["trade_date", "stable_sleeve", "code"],
        keep=False,
    ).any():
        raise ValueError("all_cap_campaign_duplicate_rows")
    label_counts = frame.groupby("trade_date")["label_end_date"].nunique()
    if bool(label_counts.ne(1).any()):
        raise ValueError("all_cap_campaign_label_dates")

    numeric_columns = (
        "total_mv",
        "official_weight",
        "entry_price",
        "entry_open",
        "entry_up_limit",
        "entry_down_limit",
        "benchmark_entry_price",
        "avg_amount_20",
        "realized_volatility_20",
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required_positive = (
        "entry_price",
        "entry_open",
        "entry_up_limit",
        "entry_down_limit",
        "benchmark_entry_price",
        "realized_volatility_20",
    )
    if any(
        frame[column].isna().any()
        or not np.isfinite(frame[column].to_numpy(dtype=float)).all()
        or frame[column].le(0.0).any()
        for column in required_positive
    ):
        raise ValueError("all_cap_campaign_market_inputs")
    if not np.allclose(
        frame["entry_price"].to_numpy(dtype=float),
        frame["entry_open"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("all_cap_campaign_next_open")
    if (
        frame["entry_up_limit"].le(frame["entry_down_limit"]).any()
        or frame["official_weight"].isna().any()
        or frame["official_weight"].lt(0.0).any()
        or frame["total_mv"].isna().any()
        or frame["total_mv"].le(0.0).any()
    ):
        raise ValueError("all_cap_campaign_market_inputs")
    for column in (
        "legacy_eligible",
        "entry_status_complete",
        "entry_status_conflict",
        "entry_suspended",
    ):
        frame[column] = _bool_series(
            frame[column],
            error="all_cap_campaign_status",
        )
    return frame.sort_values(
        ["trade_date", "stable_sleeve", "code"],
        kind="stable",
    ).reset_index(drop=True)


def _build_folds(frame: pd.DataFrame, contract: AllCapContract) -> tuple[EvaluationFold, ...]:
    date_rows = frame.loc[:, ["trade_date", "label_end_date"]].drop_duplicates()
    date_rows["account_id"] = contract.campaign_id
    windows = build_account_windows(
        date_rows,
        account_scope=contract.campaign_id,
        horizon=1,
        n_splits=int(contract.raw["windows"]["walk_forward_folds"]),
        embargo_days=1,
    )
    if len(windows.folds) != 4:
        raise ValueError("all_cap_campaign_folds")
    return windows.folds


def _expand_decision_rows(
    frame: pd.DataFrame,
    contract: AllCapContract,
) -> pd.DataFrame:
    open_dates = tuple(sorted(frame["trade_date"].astype(str).unique()))
    calendar = build_decision_calendar(open_dates, contract).rename(
        columns={"trade_date": "_decision_date"}
    )
    expanded = frame.merge(
        calendar,
        left_on=["trade_date", "stable_sleeve"],
        right_on=["_decision_date", "stable_sleeve"],
        how="inner",
        validate="many_to_many",
    ).drop(columns=["_decision_date"])
    expanded["account_id"] = (
        expanded["agent"].astype("string")
        + ":"
        + expanded["stable_sleeve"].astype("string")
    )
    for column in (
        "trade_date",
        "entry_date",
        "label_end_date",
        "code",
        "stable_sleeve",
        "agent",
        "account_id",
    ):
        expanded[column] = expanded[column].astype("string")
    return expanded.sort_values(
        ["agent", "stable_sleeve", "trade_date", "code"],
        kind="stable",
    ).reset_index(drop=True)


def _normalize_group_weight(
    frame: pd.DataFrame,
    column: str,
) -> pd.Series:
    raw = pd.to_numeric(frame[column], errors="coerce")
    totals = raw.groupby(
        [
            frame["agent"],
            frame["stable_sleeve"],
            frame["trade_date"],
        ]
    ).transform("sum")
    counts = raw.groupby(
        [
            frame["agent"],
            frame["stable_sleeve"],
            frame["trade_date"],
        ]
    ).transform("size")
    return (raw / totals.where(totals.gt(0.0), counts)).fillna(0.0)


def _score_all_cap(
    frame: pd.DataFrame,
    overlays: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for (agent, _sleeve, _date), group in frame.groupby(
        ["agent", "stable_sleeve", "trade_date"],
        sort=False,
    ):
        overlay = overlays[str(agent)]
        scored, _ = process_factors(
            group,
            dict(overlay["factors"]),
            dict(overlay.get("factor_processing") or {}),
        )
        scored["_eligible_for_selection"] = ~scored[
            "insufficient_factor_coverage"
        ].astype(bool)
        parts.append(scored)
    if not parts:
        raise ValueError("all_cap_campaign_no_rows")
    return pd.concat(parts, ignore_index=True, sort=False).sort_values(
        ["agent", "stable_sleeve", "trade_date", "code"],
        kind="stable",
    ).reset_index(drop=True)


def _trial_frame(
    trial_id: str,
    common: pd.DataFrame,
    overlays: Mapping[str, Mapping[str, Any]],
    *,
    initial_cash: float,
    base_fraction: float,
    sleeve_capital_weights: Mapping[str, float],
) -> pd.DataFrame:
    frame = common.copy()
    frame["_eligible_for_selection"] = True
    if trial_id == "official_sleeve_index":
        frame["score"] = frame["official_weight"]
        frame["benchmark_weight"] = _normalize_group_weight(
            frame,
            "official_weight",
        )
        frame["_eligible_for_selection"] = frame["official_weight"].gt(0.0)
    elif trial_id == "pit_sleeve_cap_weight":
        frame["score"] = frame["total_mv"]
        frame["benchmark_weight"] = _normalize_group_weight(frame, "total_mv")
    elif trial_id == "pit_sleeve_equal_weight":
        frame["_equal_weight"] = 1.0
        frame["score"] = 0.0
        frame["benchmark_weight"] = _normalize_group_weight(
            frame,
            "_equal_weight",
        )
    elif trial_id == "legacy_transparent_scope":
        frame = _score_all_cap(frame, overlays)
        frame["benchmark_weight"] = _normalize_group_weight(frame, "total_mv")
        frame["_eligible_for_selection"] &= frame["legacy_eligible"]
    elif trial_id == "sleeve_router_only":
        frame["_equal_weight"] = 1.0
        frame["score"] = 0.0
        frame["benchmark_weight"] = _normalize_group_weight(
            frame,
            "_equal_weight",
        )
    elif trial_id == "all_cap_v2":
        frame = _score_all_cap(frame, overlays)
        frame["benchmark_weight"] = _normalize_group_weight(frame, "total_mv")
    else:
        raise ValueError(f"all_cap_campaign_trial:{trial_id}")

    liquidity = pd.to_numeric(frame["avg_amount_20"], errors="coerce")
    valid_liquidity = liquidity.notna() & np.isfinite(liquidity) & liquidity.gt(0.0)
    account_cash = (
        frame["stable_sleeve"].map(sleeve_capital_weights).astype(float)
        * initial_cash
    )
    frame["liquidity_cap"] = 0.0
    frame.loc[valid_liquidity, "liquidity_cap"] = (
        liquidity.loc[valid_liquidity]
        * base_fraction
        / account_cash.loc[valid_liquidity]
    ).clip(lower=0.0, upper=1.0)
    frame["_eligible_for_selection"] &= valid_liquidity

    status_complete = frame["entry_status_complete"].astype(bool)
    status_conflict = frame["entry_status_conflict"].astype(bool)
    suspended = frame["entry_suspended"].astype(bool)
    frame["entry_buy_allowed"] = (
        status_complete
        & ~status_conflict
        & ~suspended
        & frame["entry_open"].lt(frame["entry_up_limit"])
    )
    frame["entry_sell_allowed"] = (
        status_complete
        & ~status_conflict
        & ~suspended
        & frame["entry_open"].gt(frame["entry_down_limit"])
    )
    frame["hard_risk_exit"] = ~frame["entry_sell_allowed"]
    frame.loc[~frame["_eligible_for_selection"], "liquidity_cap"] = 0.0
    return frame


def build_trial_evaluations(
    inputs: CampaignInputs,
    contract: AllCapContract,
) -> dict[str, TrialEvaluation]:
    """Prepare the six frozen trials without reading any holdout observations."""

    baselines = contract.raw.get("baselines")
    if not isinstance(baselines, tuple) or tuple(baselines) != TRIAL_IDS[:-1]:
        raise ValueError("all_cap_campaign_trial_set")
    settings, cost_signature = _frozen_portfolio_settings(inputs, contract)
    frame = _validate_development_frame(inputs.evaluation, contract)
    folds = _build_folds(frame, contract)
    common = _expand_decision_rows(frame, contract)
    overlays = _load_overlays(inputs, contract)
    factor_weights = {
        agent: {
            str(name): float(spec["weight"])
            for name, spec in overlay["factors"].items()
        }
        for agent, overlay in overlays.items()
    }
    initial_cash = float(inputs.portfolio_contract.get("initial_cash") or 0.0)
    if not math.isfinite(initial_cash) or initial_cash <= 0.0:
        raise ValueError("all_cap_campaign_initial_cash")
    sleeve_capital_weights = {
        sleeve.name: sleeve.capital_weight
        for sleeve in contract.sleeves
    }
    row_keys = tuple(
        common[["account_id", "trade_date", "code"]]
        .itertuples(index=False, name=None)
    )
    dates = tuple(sorted(common["trade_date"].astype(str).unique()))
    prepared = {
        trial_id: TrialEvaluation(
            trial_id=trial_id,
            evaluation=_trial_frame(
                trial_id,
                common,
                overlays,
                initial_cash=initial_cash,
                base_fraction=float(settings["base_max_adv_fraction"]),
                sleeve_capital_weights=sleeve_capital_weights,
            ),
            evaluation_dates=dates,
            row_keys=row_keys,
            cost_signature=cost_signature,
            folds=folds,
            factor_weights=(
                {agent: dict(weights) for agent, weights in factor_weights.items()}
                if trial_id in {"legacy_transparent_scope", "all_cap_v2"}
                else {}
            ),
        )
        for trial_id in TRIAL_IDS
    }
    _assert_comparable_trials(prepared)
    return prepared


def _assert_comparable_trials(
    trials: Mapping[str, TrialEvaluation],
) -> None:
    if tuple(trials) != TRIAL_IDS:
        raise ValueError("all_cap_campaign_trial_set")
    if (
        len({trial.row_keys for trial in trials.values()}) != 1
        or len({trial.evaluation_dates for trial in trials.values()}) != 1
        or len({trial.cost_signature for trial in trials.values()}) != 1
    ):
        raise ValueError("all_cap_campaign_comparability")


def _account_contract(
    inputs: CampaignInputs,
    contract: AllCapContract,
    overlays: Mapping[str, Mapping[str, Any]],
    account_id: str,
) -> dict[str, Any]:
    agent, sleeve_name = account_id.split(":", 1)
    sleeve = next(
        (item for item in contract.sleeves if item.name == sleeve_name),
        None,
    )
    if sleeve is None:
        raise ValueError("all_cap_campaign_account")
    overlay = overlays[agent]
    controls = dict(overlay.get("portfolio_controls") or {})
    source_policy = inputs.portfolio_contract.get("allocation_policy")
    if not isinstance(source_policy, Mapping):
        raise ValueError("all_cap_campaign_optimizer_contract")
    initial_cash = float(inputs.portfolio_contract["initial_cash"])
    top_n = int(inputs.portfolio_contract.get("top_n") or 50)
    return {
        "accounts": [{
            "id": account_id,
            "cash": initial_cash * sleeve.capital_weight,
            "top_n": top_n,
            "hold_buffer_pct": float(controls.get("hold_buffer_pct") or 0.0),
        }],
        "trading": {
            **dict(inputs.portfolio_contract["trading"]),
            "settlement_days": 1,
        },
        "settlement": {"sell_proceeds_reusable_same_day": False},
        "performance": dict(
            inputs.portfolio_contract.get("performance")
            or {"risk_free_rate": 0.02, "trading_days_per_year": 252}
        ),
        "rebalance_frequency": "daily",
        "allocation_policy": {
            **dict(source_policy),
            "group_constraints": {
                "industry": float(controls.get("max_industry_weight") or 1.0),
            },
        },
    }


def _oos_rows(
    frame: pd.DataFrame,
    folds: tuple[EvaluationFold, ...],
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for fold_number, fold in enumerate(folds):
        validation = frame.loc[
            frame["trade_date"].astype(str).isin(fold.validation_dates)
        ].copy()
        if validation.empty:
            raise ValueError(f"all_cap_campaign_fold_rows:{fold_number}")
        validation["fold"] = str(fold_number)
        parts.append(validation)
    return pd.concat(parts, ignore_index=True, sort=False)


def _validate_replay_participation(
    result: PortfolioReplayResult,
    *,
    base_fraction: float,
    hard_fraction: float,
) -> None:
    if result.trades.empty:
        return
    participation = pd.to_numeric(
        result.trades["participation_rate"],
        errors="coerce",
    )
    if participation.isna().any():
        raise ValueError("all_cap_campaign_missing_liquidity")
    if participation.gt(hard_fraction + 1e-12).any():
        raise ValueError("all_cap_campaign_hard_participation")
    if participation.gt(base_fraction + 1e-12).any():
        raise ValueError("all_cap_campaign_base_participation")


def run_development_campaign(
    inputs: CampaignInputs,
    contract: AllCapContract,
) -> CampaignResult:
    """Replay all frozen trials over development OOS rows only."""

    trials = build_trial_evaluations(inputs, contract)
    overlays = _load_overlays(inputs, contract)
    portfolio = contract.raw["portfolio"]
    base_fraction = float(portfolio["base_max_adv_fraction"])
    hard_fraction = float(portfolio["hard_max_adv_fraction"])
    for trial in trials.values():
        oos = _oos_rows(trial.evaluation, trial.folds)
        for account_id, account_frame in oos.groupby("account_id", sort=True):
            replay = replay_rule_portfolio(
                account_frame.reset_index(drop=True),
                contract=_account_contract(
                    inputs,
                    contract,
                    overlays,
                    str(account_id),
                ),
            )
            _validate_replay_participation(
                replay,
                base_fraction=base_fraction,
                hard_fraction=hard_fraction,
            )
            trial.replays[str(account_id)] = replay
    _assert_comparable_trials(trials)
    return CampaignResult(
        trials=trials,
        folds=next(iter(trials.values())).folds,
    )


def _empty_fill(status: str) -> NextOpenFill:
    return NextOpenFill(filled_shares=0, status=status)


def replay_next_open(order: Mapping[str, Any]) -> NextOpenFill:
    """Apply the frozen A-share next-open execution rules to one order."""

    side = str(order.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        return _empty_fill("missing_critical_input")
    required = (
        "requested_shares",
        "entry_open",
        "up_limit",
        "down_limit",
        "status_complete",
        "status_conflict",
        "suspended",
        "trade_date",
    )
    if any(order.get(name) is None for name in required):
        return _empty_fill("missing_critical_input")
    try:
        requested = int(order["requested_shares"])
        price = float(order["entry_open"])
        up_limit = float(order["up_limit"])
        down_limit = float(order["down_limit"])
        trade_date = _date_key(
            order["trade_date"],
            error="all_cap_campaign_execution_date",
        )
    except (TypeError, ValueError):
        return _empty_fill("missing_critical_input")
    if (
        requested <= 0
        or not all(math.isfinite(value) and value > 0.0 for value in (
            price,
            up_limit,
            down_limit,
        ))
        or up_limit <= down_limit
    ):
        return _empty_fill("missing_critical_input")
    if order["status_complete"] is not True or order["status_conflict"] is not False:
        return _empty_fill("missing_critical_input")
    if order["suspended"] is True:
        return _empty_fill("suspended")
    if (side == "buy" and price >= up_limit) or (
        side == "sell" and price <= down_limit
    ):
        return _empty_fill("limit_locked")
    if side == "sell":
        acquired = order.get("acquired_date")
        if acquired is None:
            return _empty_fill("missing_critical_input")
        try:
            acquired_date = _date_key(
                acquired,
                error="all_cap_campaign_execution_date",
            )
        except ValueError:
            return _empty_fill("missing_critical_input")
        if acquired_date >= trade_date:
            return _empty_fill("t_plus_one")
        try:
            requested = min(requested, int(order.get("available_shares") or 0))
        except (TypeError, ValueError):
            return _empty_fill("missing_critical_input")
        if requested <= 0:
            return _empty_fill("no_sellable_shares")

    trading = order.get("trading")
    if not isinstance(trading, Mapping) or any(
        name not in trading for name in _REQUIRED_TRADING
    ):
        return _empty_fill("missing_critical_input")
    try:
        lot = int(trading["lot_size"])
        daily_amount = float(order["avg_daily_amount"])
        volatility = float(order["volatility"])
        base_fraction = float(order.get("base_max_adv_fraction", 0.02))
        hard_fraction = float(order.get("hard_max_adv_fraction", 0.05))
    except (KeyError, TypeError, ValueError):
        return _empty_fill("missing_liquidity")
    if (
        lot <= 0
        or not math.isfinite(daily_amount)
        or daily_amount <= 0.0
        or not math.isfinite(volatility)
        or volatility <= 0.0
    ):
        return _empty_fill("missing_liquidity")
    if (
        not math.isclose(base_fraction, 0.02)
        or not math.isclose(hard_fraction, 0.05)
        or base_fraction > hard_fraction
    ):
        return _empty_fill("participation_contract")

    base_shares = int((daily_amount * base_fraction / price) // lot) * lot
    hard_shares = int((daily_amount * hard_fraction / price) // lot) * lot
    shares = min((requested // lot) * lot, base_shares, hard_shares)
    if shares <= 0:
        return _empty_fill("participation_below_lot")
    baseline_bps = (
        float(trading.get("slippage_bps") or 0.0)
        if trading.get("slippage_bps") is not None
        else float(trading.get("slippage_rate") or 0.0) * 10_000.0
    )
    estimate = estimate_execution_cost(
        order_value=shares * price,
        avg_daily_amount=daily_amount,
        volatility=volatility,
        baseline_bps=baseline_bps,
    )
    fill = calculate_execution_fill(
        reference_price=price,
        shares=shares,
        side=side,
        trading=trading,
        impact_bps=estimate.total_bps,
    )
    participation = fill.gross_amount / daily_amount
    if participation > hard_fraction + 1e-12:
        return _empty_fill("hard_participation_cap")
    return NextOpenFill(
        filled_shares=shares,
        status=(
            "filled"
            if shares == requested
            else "partial_participation_fill"
        ),
        reference_price=fill.reference_price,
        execution_price=fill.execution_price,
        gross_amount=fill.gross_amount,
        commission=fill.commission,
        stamp_tax=fill.stamp_tax,
        slippage=fill.slippage,
        cash_delta=fill.cash_delta,
        impact_bps=fill.impact_bps,
        participation_rate=participation,
    )


__all__ = [
    "TRIAL_IDS",
    "CampaignInputs",
    "CampaignResult",
    "NextOpenFill",
    "TrialEvaluation",
    "build_trial_evaluations",
    "replay_next_open",
    "run_development_campaign",
]
