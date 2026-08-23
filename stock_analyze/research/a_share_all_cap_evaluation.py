"""Per-sleeve evidence, capacity, and aggregate reporting for all-cap research."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .activation import AllCapSleeveGateReport, evaluate_all_cap_sleeve_gate
from .a_share_all_cap_contract import AllCapContract
from .cross_sectional_candidate import _rank_metrics
from .governance import (
    TrialRegistry,
    evaluate_campaign_governance,
)
from .portfolio_replay import (
    PortfolioReplayResult,
    _annualized_return,
    _drawdown,
    _normalized_nav,
    annualized_relative_wealth_excess,
)
from .robustness import contribution_concentration


_DATA_REASON_ORDER = (
    "development_window",
    "critical_membership_coverage",
    "daily_bar_coverage",
    "daily_basic_coverage",
    "adjustment_coverage",
    "core_factor_daily_coverage",
    "checksum_valid",
    "unbiased_universe",
    "point_in_time_audit",
)


@dataclass(frozen=True)
class AllCapGateReport:
    passed: bool
    sleeves: Mapping[str, AllCapSleeveGateReport]
    aggregate: Mapping[str, object]
    reasons: tuple[str, ...]


def _finite_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_key(value: object) -> str | None:
    text = str(value or "").replace("-", "")
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed) or parsed.strftime("%Y%m%d") != text:
        return None
    return text


def _invalid_capacity() -> dict[str, object]:
    missing = math.nan
    return {
        "order_count": 0,
        "orders_within_base_adv": missing,
        "orders_within_hard_adv": missing,
        "participation_rate_p50": missing,
        "participation_rate_p90": missing,
        "participation_rate_p95": missing,
        "participation_rate_p99": missing,
        "maximum_order_adv_fraction": missing,
        "liquidation_days": {
            "normal": missing,
            "half_volume": missing,
            "consecutive_limit_down": missing,
        },
        "maximum_liquidation_days": missing,
        "aum_scenarios": {},
    }


def capacity_metrics(
    orders: pd.DataFrame,
    *,
    base_adv_fraction: float = 0.02,
    hard_adv_fraction: float = 0.05,
    aum_multipliers: Sequence[int] = (1, 5, 10, 20),
) -> dict[str, object]:
    """Summarize order participation and conservative liquidation capacity."""

    if (
        not math.isclose(float(base_adv_fraction), 0.02)
        or not math.isclose(float(hard_adv_fraction), 0.05)
        or base_adv_fraction > hard_adv_fraction
        or orders.empty
        or "participation_rate" not in orders
    ):
        return _invalid_capacity()
    participation = pd.to_numeric(
        orders["participation_rate"], errors="coerce"
    )
    if (
        participation.isna().any()
        or not np.isfinite(participation.to_numpy(dtype=float)).all()
        or participation.lt(0.0).any()
    ):
        return _invalid_capacity()

    notional_column = next(
        (
            name
            for name in ("position_notional", "liquidation_notional", "gross_amount")
            if name in orders
        ),
        None,
    )
    adv_column = next(
        (name for name in ("avg_daily_amount", "adv_notional") if name in orders),
        None,
    )
    if notional_column is None or adv_column is None:
        return _invalid_capacity()
    notionals = pd.to_numeric(orders[notional_column], errors="coerce").abs()
    adv = pd.to_numeric(orders[adv_column], errors="coerce")
    if (
        notionals.isna().any()
        or adv.isna().any()
        or not np.isfinite(notionals.to_numpy(dtype=float)).all()
        or not np.isfinite(adv.to_numpy(dtype=float)).all()
        or adv.le(0.0).any()
    ):
        return _invalid_capacity()

    normal = np.ceil(notionals / (adv * hard_adv_fraction))
    half_volume = np.ceil(notionals / (adv * 0.5 * hard_adv_fraction))
    blocked = pd.to_numeric(
        orders.get("consecutive_limit_down_days", 0.0), errors="coerce"
    )
    if np.isscalar(blocked):
        blocked = pd.Series(float(blocked), index=orders.index)
    if blocked.isna().any() or blocked.lt(0.0).any():
        return _invalid_capacity()
    limit_down = normal + blocked
    liquidation = {
        "normal": float(normal.max()),
        "half_volume": float(half_volume.max()),
        "consecutive_limit_down": float(limit_down.max()),
    }

    scenarios: dict[str, dict[str, float]] = {}
    for raw_multiplier in aum_multipliers:
        multiplier = int(raw_multiplier)
        if multiplier <= 0:
            return _invalid_capacity()
        scaled = participation * multiplier
        fill_fraction = np.minimum(
            1.0,
            hard_adv_fraction / scaled.replace(0.0, np.nan),
        ).fillna(1.0)
        scenarios[str(multiplier)] = {
            "maximum_order_adv_fraction": float(scaled.max()),
            "orders_within_hard_adv": float(scaled.le(hard_adv_fraction).mean()),
            "unfilled_rate": float(1.0 - fill_fraction.mean()),
        }

    return {
        "order_count": int(len(orders)),
        "orders_within_base_adv": float(
            participation.le(base_adv_fraction + 1e-12).mean()
        ),
        "orders_within_hard_adv": float(
            participation.le(hard_adv_fraction + 1e-12).mean()
        ),
        "participation_rate_p50": float(participation.quantile(0.50)),
        "participation_rate_p90": float(participation.quantile(0.90)),
        "participation_rate_p95": float(participation.quantile(0.95)),
        "participation_rate_p99": float(participation.quantile(0.99)),
        "maximum_order_adv_fraction": float(participation.max()),
        "liquidation_days": liquidation,
        "maximum_liquidation_days": float(max(liquidation.values())),
        "aum_scenarios": scenarios,
    }


def registered_governance_metrics(
    trials: Sequence[dict[str, Any]],
    *,
    selected_trial_id: str,
    registry: TrialRegistry,
) -> dict[str, Any]:
    """Evaluate DSR/PBO against all comparable registered and current trials."""

    current = [dict(trial) for trial in trials]
    current_ids = [str(trial.get("trial_id") or "") for trial in current]
    if (
        not current_ids
        or not all(current_ids)
        or len(current_ids) != len(set(current_ids))
    ):
        raise ValueError("all_cap_evaluation_trial_ids")
    for trial in current:
        registry.record(trial)
    registered = registry.read()
    result = evaluate_campaign_governance(
        registered,
        selected_trial_id=selected_trial_id,
        legacy_trials=(),
    )
    return {
        **result,
        "pbo_trial_count": int(result["valid_trial_count"]),
    }


def aggregate_all_cap_metrics(
    sleeve_periods: Mapping[str, pd.DataFrame],
    csi_all_share: pd.DataFrame,
    contract: AllCapContract,
) -> dict[str, object]:
    """Build the fixed 35/30/25/10 account against CSI All Share."""

    weights = {
        sleeve.name: float(sleeve.capital_weight)
        for sleeve in contract.sleeves
        if sleeve.capital_weight > 0.0
    }
    if set(sleeve_periods) != set(weights):
        raise ValueError("all_cap_evaluation_aggregate_sleeves")
    parts: list[pd.DataFrame] = []
    expected_dates: tuple[str, ...] | None = None
    for sleeve in weights:
        frame = sleeve_periods[sleeve]
        if not {"signal_date", "net_return"}.issubset(frame.columns):
            raise ValueError("all_cap_evaluation_aggregate_schema")
        normalized = frame.loc[:, ["signal_date", "net_return"]].copy()
        normalized["signal_date"] = normalized["signal_date"].astype("string")
        normalized["net_return"] = pd.to_numeric(
            normalized["net_return"], errors="coerce"
        )
        normalized = normalized.sort_values("signal_date", kind="stable")
        dates = tuple(normalized["signal_date"].astype(str))
        if (
            normalized["signal_date"].duplicated().any()
            or normalized["net_return"].isna().any()
            or not np.isfinite(normalized["net_return"].to_numpy(dtype=float)).all()
        ):
            raise ValueError("all_cap_evaluation_aggregate_values")
        if expected_dates is None:
            expected_dates = dates
        elif dates != expected_dates:
            raise ValueError("all_cap_evaluation_aggregate_dates")
        normalized = normalized.rename(columns={"net_return": sleeve})
        parts.append(normalized.set_index("signal_date"))

    benchmark = csi_all_share.copy()
    if not {"signal_date", "benchmark_return"}.issubset(benchmark.columns):
        raise ValueError("all_cap_evaluation_aggregate_benchmark")
    benchmark["signal_date"] = benchmark["signal_date"].astype("string")
    benchmark["benchmark_return"] = pd.to_numeric(
        benchmark["benchmark_return"], errors="coerce"
    )
    benchmark = benchmark.sort_values("signal_date", kind="stable")
    if (
        tuple(benchmark["signal_date"].astype(str)) != expected_dates
        or benchmark["signal_date"].duplicated().any()
        or benchmark["benchmark_return"].isna().any()
        or not np.isfinite(benchmark["benchmark_return"].to_numpy(dtype=float)).all()
    ):
        raise ValueError("all_cap_evaluation_aggregate_benchmark")
    aligned = pd.concat(parts, axis=1)
    aggregate_returns = sum(aligned[name] * weight for name, weight in weights.items())
    portfolio_nav = _normalized_nav(aggregate_returns)
    benchmark_nav = _normalized_nav(benchmark["benchmark_return"])
    return {
        "benchmark": str(contract.raw["aggregate_benchmark"]),
        "sleeve_weights": weights,
        "observations": int(len(aggregate_returns)),
        "annualized_net_return": _annualized_return(aggregate_returns),
        "annualized_benchmark_return": _annualized_return(
            benchmark["benchmark_return"]
        ),
        "annualized_net_excess_return": annualized_relative_wealth_excess(
            portfolio_nav,
            benchmark_nav,
        ),
        "max_drawdown": _drawdown(aggregate_returns),
        "benchmark_max_drawdown": _drawdown(benchmark["benchmark_return"]),
        "period_returns": aggregate_returns.astype(float).tolist(),
        "period_return_dates": list(expected_dates or ()),
    }


def _group_compounded_returns(
    periods: pd.DataFrame,
    group: pd.Series,
) -> dict[str, float]:
    result: dict[str, float] = {}
    active = pd.to_numeric(periods["active_return"], errors="coerce")
    for name, values in active.groupby(group, sort=True):
        clean = values.dropna().clip(lower=-0.99)
        if clean.empty:
            continue
        result[str(name)] = float(np.prod(1.0 + clean.to_numpy(dtype=float)) - 1.0)
    return result


def summarize_sleeve_evidence(
    evaluation: pd.DataFrame,
    replay: PortfolioReplayResult,
    *,
    double_cost_replay: PortfolioReplayResult,
    governance: Mapping[str, object],
    data_evidence: Mapping[str, object],
    benchmark_max_drawdown: float,
) -> dict[str, object]:
    """Compose existing replay, rank, governance, and robustness diagnostics."""

    if not {"trade_date", "score"}.issubset(evaluation.columns):
        raise ValueError("all_cap_evaluation_rank_schema")
    target = "excess_return" if "excess_return" in evaluation else "return_1"
    if target not in evaluation:
        raise ValueError("all_cap_evaluation_rank_schema")
    rank_frame = evaluation.rename(columns={target: "excess_return"})
    rank_ic, icir, rank_ic_dates = _rank_metrics(rank_frame)

    periods = replay.periods.copy()
    required_periods = {
        "signal_date",
        "fold",
        "active_return",
        "net_return",
        "benchmark_return",
    }
    if periods.empty or not required_periods.issubset(periods.columns):
        raise ValueError("all_cap_evaluation_period_schema")
    periods["signal_date"] = periods["signal_date"].astype("string")
    numeric_periods = periods.loc[
        :, ["active_return", "net_return", "benchmark_return"]
    ].apply(pd.to_numeric, errors="coerce")
    if (
        numeric_periods.isna().any(axis=None)
        or not np.isfinite(numeric_periods.to_numpy(dtype=float)).all()
    ):
        raise ValueError("all_cap_evaluation_period_values")
    periods.loc[:, numeric_periods.columns] = numeric_periods
    fold_returns = _group_compounded_returns(periods, periods["fold"].astype(str))
    years = periods["signal_date"].str.slice(0, 4)
    year_returns = _group_compounded_returns(periods, years)
    concentration = contribution_concentration(
        {year: max(value, 0.0) for year, value in year_returns.items()}
    )
    capacity = capacity_metrics(replay.trades)
    portfolio_drawdown = _finite_number(replay.metrics.get("max_drawdown"))
    benchmark_drawdown = _finite_number(benchmark_max_drawdown)
    if portfolio_drawdown is None or benchmark_drawdown is None:
        drawdown_multiple = math.nan
    elif benchmark_drawdown <= 0.0:
        drawdown_multiple = 0.0 if portfolio_drawdown <= 0.0 else math.inf
    else:
        drawdown_multiple = portfolio_drawdown / benchmark_drawdown
    if "impact_cost" in replay.trades:
        impact_cost = float(
            pd.to_numeric(replay.trades["impact_cost"], errors="coerce")
            .fillna(0.0)
            .sum()
        )
    elif {"gross_amount", "impact_bps"}.issubset(replay.trades.columns):
        gross = pd.to_numeric(replay.trades["gross_amount"], errors="coerce")
        impact_bps = pd.to_numeric(replay.trades["impact_bps"], errors="coerce")
        impact_cost = float((gross * impact_bps / 10_000.0).fillna(0.0).sum())
    else:
        impact_cost = 0.0
    costs = {
        "commission": _finite_number(replay.metrics.get("total_commission")),
        "stamp_tax": _finite_number(replay.metrics.get("total_stamp_tax")),
        "slippage": _finite_number(replay.metrics.get("total_slippage")),
        "impact": impact_cost,
        "total": _finite_number(replay.metrics.get("total_execution_cost")),
    }
    base_metrics = replay.metrics
    result: dict[str, object] = {
        **dict(data_evidence),
        "evidence_scope": "development_only",
        "oos_start": str(periods["signal_date"].min()),
        "oos_end": str(periods["signal_date"].max()),
        "gross_return": base_metrics.get("gross_return"),
        "net_return": base_metrics.get("net_return"),
        "benchmark_return": base_metrics.get("benchmark_return"),
        "net_excess_return": base_metrics.get("net_excess_return"),
        "single_cost_net_excess_return": base_metrics.get("net_excess_return"),
        "double_cost_net_excess_return": double_cost_replay.metrics.get(
            "net_excess_return"
        ),
        "rank_ic": rank_ic,
        "icir": icir,
        "rank_ic_dates": rank_ic_dates,
        "oos_folds": len(fold_returns),
        "positive_oos_folds": sum(value > 0.0 for value in fold_returns.values()),
        "fold_net_excess_returns": fold_returns,
        "oos_dates": int(periods["signal_date"].nunique()),
        "completed_trades": int(base_metrics.get("trade_count") or len(replay.trades)),
        "max_drawdown": portfolio_drawdown,
        "benchmark_max_drawdown": benchmark_drawdown,
        "benchmark_drawdown_multiple": drawdown_multiple,
        "annual_turnover": base_metrics.get("annual_turnover"),
        "target_fill_rate": base_metrics.get("target_fill_ratio"),
        "cost_attribution": costs,
        "attribution_status": base_metrics.get("attribution_status"),
        "deflated_sharpe_probability": governance.get(
            "deflated_sharpe_probability"
        ),
        "probability_of_backtest_overfit": governance.get(
            "probability_of_backtest_overfit"
        ),
        "pbo_trial_count": governance.get(
            "pbo_trial_count",
            governance.get("valid_trial_count"),
        ),
        "calendar_year_net_excess_returns": year_returns,
        "positive_calendar_years": sum(value > 0.0 for value in year_returns.values()),
        "single_year_positive_excess_share": concentration["largest_share"],
        "simulator_version": base_metrics.get("simulator_version"),
        "cost_stress": {
            "1x": base_metrics.get("net_excess_return"),
            "2x": double_cost_replay.metrics.get("net_excess_return"),
        },
        **capacity,
    }
    return result


def _data_gate_reasons(
    metrics: Mapping[str, object],
    contract: AllCapContract,
) -> tuple[str, ...]:
    data_gates = contract.raw["data_gates"]
    start = _date_key(metrics.get("oos_start"))
    end = _date_key(metrics.get("oos_end"))
    development_ok = (
        metrics.get("evidence_scope") == "development_only"
        and start is not None
        and end is not None
        and contract.development_start.strftime("%Y%m%d") <= start <= end
        and end <= contract.development_end.strftime("%Y%m%d")
    )
    checks = {
        "development_window": development_ok,
        "critical_membership_coverage": (
            (_finite_number(metrics.get("critical_membership_coverage")) or -1.0)
            >= float(data_gates["critical_membership_coverage"])
        ),
        "daily_bar_coverage": (
            (_finite_number(metrics.get("daily_bar_coverage")) or -1.0)
            >= float(data_gates["daily_bar_coverage"])
        ),
        "daily_basic_coverage": (
            (_finite_number(metrics.get("daily_basic_coverage")) or -1.0)
            >= float(data_gates["daily_basic_coverage"])
        ),
        "adjustment_coverage": (
            (_finite_number(metrics.get("adjustment_coverage")) or -1.0)
            >= float(data_gates["adjustment_coverage"])
        ),
        "core_factor_daily_coverage": (
            (_finite_number(metrics.get("core_factor_daily_coverage")) or -1.0)
            >= float(data_gates["core_factor_daily_coverage"])
        ),
        "checksum_valid": (
            data_gates.get("checksum_required") is not True
            or metrics.get("checksum_valid") is True
        ),
        "unbiased_universe": (
            data_gates.get("unbiased_universe_required") is not True
            or metrics.get("unbiased_universe") is True
        ),
        "point_in_time_audit": (
            data_gates.get("pit_required") is not True
            or metrics.get("point_in_time_audit") is True
        ),
    }
    return tuple(name for name in _DATA_REASON_ORDER if not checks[name])


def evaluate_all_cap_gate(
    evidence_by_sleeve: Mapping[str, Mapping[str, object]],
    contract: AllCapContract,
    *,
    aggregate: Mapping[str, object] | None = None,
) -> AllCapGateReport:
    """Gate every funded sleeve independently, then evaluate the fixed aggregate."""

    if not isinstance(contract, AllCapContract):
        raise ValueError("all_cap_evaluation_contract")
    evaluation_gates = dict(contract.raw["evaluation_gates"])
    sleeve_reports: dict[str, AllCapSleeveGateReport] = {}
    weights = {
        sleeve.name: float(sleeve.capital_weight)
        for sleeve in contract.sleeves
        if sleeve.capital_weight > 0.0
    }
    for sleeve in weights:
        raw_metrics = evidence_by_sleeve.get(sleeve)
        if not isinstance(raw_metrics, Mapping):
            sleeve_reports[sleeve] = AllCapSleeveGateReport(
                passed=False,
                reasons=("evidence_missing",),
                metrics={},
            )
            continue
        metrics = dict(raw_metrics)
        data_reasons = _data_gate_reasons(metrics, contract)
        evaluation = evaluate_all_cap_sleeve_gate(metrics, evaluation_gates)
        reasons = (*data_reasons, *evaluation.reasons)
        sleeve_reports[sleeve] = AllCapSleeveGateReport(
            passed=not reasons,
            reasons=reasons,
            metrics=metrics,
        )

    aggregate_metrics = dict(aggregate or {})
    aggregate_metrics.setdefault("benchmark", str(contract.raw["aggregate_benchmark"]))
    aggregate_metrics.setdefault("sleeve_weights", weights)
    aggregate_excess = _finite_number(
        aggregate_metrics.get("annualized_net_excess_return")
    )
    benchmark_matches = (
        aggregate_metrics.get("benchmark") == contract.raw["aggregate_benchmark"]
    )
    reported_weights = aggregate_metrics.get("sleeve_weights")
    weights_match = (
        isinstance(reported_weights, Mapping)
        and set(reported_weights) == set(weights)
        and all(
            _finite_number(reported_weights.get(name)) is not None
            and math.isclose(float(reported_weights[name]), weight)
            for name, weight in weights.items()
        )
    )
    aggregate_return_passed = (
        aggregate_excess is not None
        and aggregate_excess
        >= float(evaluation_gates["minimum_aggregate_annualized_net_excess_return"])
    )
    aggregate_passed = benchmark_matches and weights_match and aggregate_return_passed
    aggregate_metrics["passed"] = aggregate_passed
    reasons = tuple(
        [
            *(f"sleeve:{name}" for name, report in sleeve_reports.items() if not report.passed),
            *(() if benchmark_matches else ("aggregate_benchmark",)),
            *(() if weights_match else ("aggregate_sleeve_weights",)),
            *(() if aggregate_return_passed else ("aggregate_annualized_net_excess_return",)),
        ]
    )
    return AllCapGateReport(
        passed=not reasons,
        sleeves=sleeve_reports,
        aggregate=aggregate_metrics,
        reasons=reasons,
    )


__all__ = [
    "AllCapGateReport",
    "aggregate_all_cap_metrics",
    "capacity_metrics",
    "evaluate_all_cap_gate",
    "registered_governance_metrics",
    "summarize_sleeve_evidence",
]
