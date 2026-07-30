from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping

import numpy as np
import pandas as pd


DEFAULT_STRESS_SHOCKS = {
    "market": -0.20,
    "industry": -0.15,
    "volatility": -0.10,
    "fx": -0.08,
    "premium": -0.10,
}
EXACT_SUPPORT_SEARCH_BUDGET = 96


@dataclass(frozen=True)
class PortfolioLimits:
    max_positions: int
    max_name_weight: float = 1.0
    max_gross_exposure: float = 1.0
    min_cash_weight: float = 0.0
    max_turnover: float = 1.0
    group_caps: Mapping[str, float] = field(default_factory=dict)
    liquidity_cap_column: str = "liquidity_cap"
    required_exposures: tuple[str, ...] = ()
    max_tracking_error: float | None = None
    tolerance: float = 1e-8
    max_iterations: int = 240


@dataclass(frozen=True)
class PortfolioProblem:
    candidates: pd.DataFrame
    current_weights: pd.Series
    benchmark_weights: pd.Series
    covariance: pd.DataFrame
    exposure_matrix: pd.DataFrame
    limits: PortfolioLimits
    cost_bps: float | pd.Series = 0.0
    risk_aversion: float = 1.0
    active_risk_aversion: float = 0.0
    cost_aversion: float = 1.0
    stress_shocks: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_STRESS_SHOCKS)
    )


@dataclass(frozen=True)
class PortfolioSolution:
    weights: pd.Series
    cash_weight: float
    expected_alpha: float
    expected_cost: float
    turnover: float
    volatility: float
    tracking_error: float
    exposures: dict[str, float]
    risk_contributions: dict[str, float]
    stress_losses: dict[str, float]
    binding_constraints: tuple[str, ...]
    fallback_reason: str | None


@dataclass(frozen=True)
class _PreparedGroupConstraint:
    column: str
    value: str
    cap: float
    mask: np.ndarray


@dataclass(frozen=True)
class _PreparedProblem:
    codes: tuple[str, ...]
    candidates: pd.DataFrame
    alpha: np.ndarray
    liquidity_caps: np.ndarray
    current: np.ndarray
    benchmark: np.ndarray
    covariance: np.ndarray
    exposures: pd.DataFrame
    cost_rates: np.ndarray
    limits: PortfolioLimits
    risk_aversion: float
    active_risk_aversion: float
    cost_aversion: float
    stress_shocks: Mapping[str, float]
    group_constraints: tuple[_PreparedGroupConstraint, ...]


def optimize_portfolio(problem: PortfolioProblem) -> PortfolioSolution:
    """Jointly select and size a deterministic long-only portfolio.

    Small universes use deterministic support search. Larger universes first
    solve a continuous relaxation, then project that solution onto the position
    budget and refit once. This keeps selection sensitive to covariance and
    costs while placing a hard bound on production-size search work.
    """

    codes = _candidate_codes(problem.candidates)
    prepared, reason = _prepare_problem(problem, codes)
    if prepared is None:
        return _cash_solution(codes, reason or "invalid_problem")

    search_size = len(prepared.codes) * prepared.limits.max_positions
    if search_size <= EXACT_SUPPORT_SEARCH_BUDGET:
        best_weights = _optimize_small_support(prepared)
    else:
        full_support = tuple(range(len(prepared.codes)))
        relaxed_weights, _ = _solve_support(prepared, full_support)
        if relaxed_weights is None:
            return _cash_solution(prepared.codes, "continuous_relaxation_infeasible")

        support = _sparse_support(prepared, relaxed_weights)
        if not support:
            return _cash_solution(prepared.codes, "non_positive_net_utility")

        if support == full_support:
            best_weights = relaxed_weights
        else:
            best_weights, _ = _solve_support(prepared, support)
            if best_weights is None:
                fallback_support = _feasibility_support(prepared, relaxed_weights)
                best_weights, _ = _solve_support(prepared, fallback_support)

    zero_objective = _objective(
        prepared,
        np.zeros(len(prepared.codes), dtype=float),
    )
    if (
        best_weights is None
        or float(best_weights.sum()) <= prepared.limits.tolerance
        or _objective(prepared, best_weights)
        <= zero_objective + prepared.limits.tolerance
    ):
        return _cash_solution(prepared.codes, "non_positive_net_utility")

    return _build_solution(prepared, best_weights)


def _optimize_small_support(problem: _PreparedProblem) -> np.ndarray | None:
    limits = problem.limits
    seeded = {
        index
        for index, weight in enumerate(problem.current)
        if weight > limits.tolerance
    }
    if limits.max_tracking_error is not None:
        seeded.update(
            index
            for index, weight in enumerate(problem.benchmark)
            if weight > limits.tolerance
        )
    if len(seeded) > limits.max_positions:
        seeded = set(
            sorted(
                seeded,
                key=lambda index: (
                    -(problem.current[index] + problem.benchmark[index]),
                    problem.codes[index],
                ),
            )[: limits.max_positions]
        )

    support = tuple(sorted(seeded))
    best_weights, best_objective = _solve_support(problem, support)
    if best_weights is None:
        best_weights = np.zeros(len(problem.codes), dtype=float)
        best_objective = _objective(problem, best_weights)

    while len(support) < limits.max_positions:
        addition = _best_addition(problem, support, best_objective)
        if addition is None:
            break
        support, best_weights, best_objective = addition

    _, best_weights, _ = _improve_by_swaps(
        problem,
        support,
        best_weights,
        best_objective,
    )
    return best_weights


def _sparse_support(
    problem: _PreparedProblem,
    relaxed_weights: np.ndarray,
) -> tuple[int, ...]:
    positive = [
        index
        for index, weight in enumerate(relaxed_weights)
        if weight > problem.limits.tolerance
    ]
    ranked = sorted(
        positive,
        key=lambda index: (
            -float(relaxed_weights[index]),
            -float(problem.alpha[index]),
            problem.codes[index],
        ),
    )
    return tuple(sorted(ranked[: problem.limits.max_positions]))


def _feasibility_support(
    problem: _PreparedProblem,
    relaxed_weights: np.ndarray,
) -> tuple[int, ...]:
    ranked = sorted(
        range(len(problem.codes)),
        key=lambda index: (
            -float(problem.current[index] + problem.benchmark[index]),
            -float(relaxed_weights[index]),
            -float(problem.alpha[index]),
            problem.codes[index],
        ),
    )
    return tuple(sorted(ranked[: problem.limits.max_positions]))


def _best_addition(
    problem: _PreparedProblem,
    support: tuple[int, ...],
    current_objective: float,
) -> tuple[tuple[int, ...], np.ndarray, float] | None:
    best: tuple[tuple[int, ...], np.ndarray, float] | None = None
    support_set = set(support)
    for candidate in range(len(problem.codes)):
        if candidate in support_set:
            continue
        trial_support = tuple(sorted((*support, candidate)))
        weights, objective = _solve_support(problem, trial_support)
        if weights is None or objective <= current_objective + problem.limits.tolerance:
            continue
        if best is None or objective > best[2] + problem.limits.tolerance:
            best = (trial_support, weights, objective)
    return best


def _improve_by_swaps(
    problem: _PreparedProblem,
    support: tuple[int, ...],
    weights: np.ndarray,
    objective: float,
) -> tuple[tuple[int, ...], np.ndarray, float]:
    if not support:
        return support, weights, objective
    for _ in range(3):
        best: tuple[tuple[int, ...], np.ndarray, float] | None = None
        support_set = set(support)
        available = [
            index
            for index in range(len(problem.codes))
            if index not in support_set
        ]
        for removed in support:
            for added in available:
                trial_support = tuple(sorted((support_set - {removed}) | {added}))
                trial_weights, trial_objective = _solve_support(problem, trial_support)
                if (
                    trial_weights is None
                    or trial_objective <= objective + problem.limits.tolerance
                ):
                    continue
                if best is None or trial_objective > best[2] + problem.limits.tolerance:
                    best = (trial_support, trial_weights, trial_objective)
        if best is None:
            break
        support, weights, objective = best
    return support, weights, objective


def _candidate_codes(candidates: pd.DataFrame) -> tuple[str, ...]:
    if not isinstance(candidates, pd.DataFrame) or "code" not in candidates.columns:
        return ()
    return tuple(sorted(candidates["code"].astype(str).tolist()))


def _prepare_problem(
    problem: PortfolioProblem,
    codes: tuple[str, ...],
) -> tuple[_PreparedProblem | None, str | None]:
    if not codes:
        return None, "empty_candidate_universe"
    if len(set(codes)) != len(codes):
        return None, "duplicate_candidate_codes"

    limits = problem.limits
    limit_reason = _validate_limits(limits)
    if limit_reason is not None:
        return None, limit_reason

    raw = problem.candidates.copy()
    raw["code"] = raw["code"].astype(str)
    raw = raw.set_index("code", drop=False).reindex(codes)
    required_candidate_columns = {"alpha", limits.liquidity_cap_column}
    missing_candidate_columns = sorted(required_candidate_columns - set(raw.columns))
    if missing_candidate_columns:
        return None, f"missing_candidate_columns:{','.join(missing_candidate_columns)}"

    alpha = pd.to_numeric(raw["alpha"], errors="coerce")
    liquidity = pd.to_numeric(raw[limits.liquidity_cap_column], errors="coerce")
    if not np.isfinite(alpha.to_numpy(dtype=float)).all():
        return None, "invalid_alpha"
    if (
        not np.isfinite(liquidity.to_numpy(dtype=float)).all()
        or (liquidity < 0.0).any()
    ):
        return None, "invalid_liquidity_caps"
    liquidity = liquidity.clip(upper=1.0)

    for column in sorted(limits.group_caps):
        if column not in raw.columns:
            return None, f"missing_group_columns:{column}"
        if raw[column].isna().any():
            return None, f"missing_group_values:{column}"

    covariance, covariance_reason = _aligned_covariance(problem.covariance, codes)
    if covariance is None:
        return None, covariance_reason

    exposures, exposure_reason = _aligned_exposures(
        problem.exposure_matrix,
        codes,
        limits.required_exposures,
    )
    if exposures is None:
        return None, exposure_reason

    current, current_reason = _aligned_weights(
        problem.current_weights,
        codes,
        "current_weights",
    )
    if current is None:
        return None, current_reason
    benchmark, benchmark_reason = _aligned_weights(
        problem.benchmark_weights,
        codes,
        "benchmark_weights",
    )
    if benchmark is None:
        return None, benchmark_reason

    cost_rates, cost_reason = _aligned_costs(problem.cost_bps, codes)
    if cost_rates is None:
        return None, cost_reason

    scalar_values = {
        "risk_aversion": problem.risk_aversion,
        "active_risk_aversion": problem.active_risk_aversion,
        "cost_aversion": problem.cost_aversion,
    }
    for name, value in scalar_values.items():
        if not math.isfinite(float(value)) or float(value) < 0.0:
            return None, f"invalid_{name}"

    shocks = dict(DEFAULT_STRESS_SHOCKS)
    shocks.update({str(key): float(value) for key, value in problem.stress_shocks.items()})
    if any(not math.isfinite(value) for value in shocks.values()):
        return None, "invalid_stress_shocks"

    group_constraints: list[_PreparedGroupConstraint] = []
    for column in sorted(limits.group_caps):
        values = raw[column].astype(str).to_numpy()
        for value in sorted(np.unique(values).tolist()):
            group_constraints.append(
                _PreparedGroupConstraint(
                    column=column,
                    value=str(value),
                    cap=float(limits.group_caps[column]),
                    mask=np.asarray(values == value, dtype=bool),
                )
            )

    return (
        _PreparedProblem(
            codes=codes,
            candidates=raw,
            alpha=alpha.to_numpy(dtype=float),
            liquidity_caps=liquidity.to_numpy(dtype=float),
            current=current,
            benchmark=benchmark,
            covariance=covariance,
            exposures=exposures,
            cost_rates=cost_rates,
            limits=limits,
            risk_aversion=float(problem.risk_aversion),
            active_risk_aversion=float(problem.active_risk_aversion),
            cost_aversion=float(problem.cost_aversion),
            stress_shocks=shocks,
            group_constraints=tuple(group_constraints),
        ),
        None,
    )


def _validate_limits(limits: PortfolioLimits) -> str | None:
    if int(limits.max_positions) <= 0:
        return "invalid_max_positions"
    bounded = {
        "max_name_weight": limits.max_name_weight,
        "max_gross_exposure": limits.max_gross_exposure,
        "min_cash_weight": limits.min_cash_weight,
        "max_turnover": limits.max_turnover,
    }
    for name, value in bounded.items():
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            return f"invalid_{name}"
    if limits.max_tracking_error is not None and (
        not math.isfinite(float(limits.max_tracking_error))
        or float(limits.max_tracking_error) < 0.0
    ):
        return "invalid_max_tracking_error"
    if int(limits.max_iterations) <= 0:
        return "invalid_max_iterations"
    for column, cap in limits.group_caps.items():
        if (
            not str(column)
            or not math.isfinite(float(cap))
            or not 0.0 <= float(cap) <= 1.0
        ):
            return f"invalid_group_cap:{column}"
    return None


def _aligned_covariance(
    covariance: pd.DataFrame,
    codes: tuple[str, ...],
) -> tuple[np.ndarray | None, str | None]:
    if not isinstance(covariance, pd.DataFrame):
        return None, "invalid_covariance"
    frame = covariance.copy()
    frame.index = frame.index.astype(str)
    frame.columns = frame.columns.astype(str)
    missing = sorted((set(codes) - set(frame.index)) | (set(codes) - set(frame.columns)))
    if missing:
        return None, f"missing_covariance_codes:{','.join(missing)}"
    matrix = frame.reindex(index=codes, columns=codes).apply(
        pd.to_numeric,
        errors="coerce",
    ).to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        return None, "invalid_covariance"
    matrix = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0)
    if float(eigenvalues.min()) < -1e-8 * scale:
        return None, "covariance_not_positive_semidefinite"
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    matrix = (eigenvectors * eigenvalues) @ eigenvectors.T
    return matrix, None


def _aligned_exposures(
    exposure_matrix: pd.DataFrame,
    codes: tuple[str, ...],
    required: tuple[str, ...],
) -> tuple[pd.DataFrame | None, str | None]:
    if not isinstance(exposure_matrix, pd.DataFrame):
        return None, "invalid_exposure_matrix"
    frame = exposure_matrix.copy()
    frame.index = frame.index.astype(str)
    missing_codes = sorted(set(codes) - set(frame.index))
    if missing_codes:
        return None, f"missing_exposure_codes:{','.join(missing_codes)}"
    missing_columns = sorted(set(required) - set(frame.columns))
    if missing_columns:
        return None, f"missing_required_exposures:{','.join(missing_columns)}"
    frame = frame.reindex(codes).apply(pd.to_numeric, errors="coerce")
    for column in required:
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            return None, f"invalid_required_exposures:{column}"
    frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return frame, None


def _aligned_weights(
    raw_weights: pd.Series,
    codes: tuple[str, ...],
    name: str,
) -> tuple[np.ndarray | None, str | None]:
    if raw_weights is None:
        series = pd.Series(dtype=float)
    elif isinstance(raw_weights, pd.Series):
        series = raw_weights.copy()
    else:
        try:
            series = pd.Series(raw_weights, dtype=float)
        except Exception:  # noqa: BLE001 - invalid inputs fail closed
            return None, f"invalid_{name}"
    series.index = series.index.astype(str)
    series = pd.to_numeric(series, errors="coerce")
    outside = sorted(
        code
        for code, value in series.items()
        if code not in codes and pd.notna(value) and float(value) > 1e-12
    )
    if outside:
        return None, f"{name}_outside_candidate_universe:{','.join(outside)}"
    aligned = series.reindex(codes, fill_value=0.0).to_numpy(dtype=float)
    if (
        not np.isfinite(aligned).all()
        or (aligned < -1e-12).any()
        or float(aligned.sum()) > 1.0 + 1e-8
    ):
        return None, f"invalid_{name}"
    return np.clip(aligned, 0.0, None), None


def _aligned_costs(
    raw_costs: float | pd.Series,
    codes: tuple[str, ...],
) -> tuple[np.ndarray | None, str | None]:
    if np.isscalar(raw_costs):
        value = float(raw_costs)
        values = np.full(len(codes), value, dtype=float)
    elif isinstance(raw_costs, pd.Series):
        series = raw_costs.copy()
        series.index = series.index.astype(str)
        series = pd.to_numeric(series, errors="coerce")
        if set(codes) - set(series.index):
            return None, "missing_cost_bps"
        values = series.reindex(codes).to_numpy(dtype=float)
    else:
        return None, "invalid_cost_bps"
    if not np.isfinite(values).all() or (values < 0.0).any():
        return None, "invalid_cost_bps"
    return values / 10_000.0, None


def _solve_support(
    problem: _PreparedProblem,
    support: tuple[int, ...],
) -> tuple[np.ndarray | None, float]:
    if not support:
        zero = np.zeros(len(problem.codes), dtype=float)
        return zero, _objective(problem, zero)

    support_mask = np.zeros(len(problem.codes), dtype=bool)
    support_mask[list(support)] = True
    start = np.where(support_mask, problem.current, 0.0)
    weights = _project(problem, start, support_mask)
    if weights is None:
        return None, -math.inf

    best_weights = weights.copy()
    best_objective = _objective(problem, weights)
    eigenvalue = float(np.linalg.eigvalsh(problem.covariance).max())
    lipschitz = (
        problem.risk_aversion + problem.active_risk_aversion
    ) * max(eigenvalue, 1e-8)
    base_step = min(20.0, 1.0 / max(lipschitz, 0.05))

    stable_iterations = 0
    for iteration in range(problem.limits.max_iterations):
        portfolio_gradient = problem.covariance @ weights
        active_gradient = problem.covariance @ (weights - problem.benchmark)
        trade_direction = np.sign(weights - problem.current)
        gradient = (
            problem.alpha
            - problem.risk_aversion * portfolio_gradient
            - problem.active_risk_aversion * active_gradient
            - problem.cost_aversion * problem.cost_rates * trade_direction
        )
        step = base_step / math.sqrt(1.0 + iteration / 20.0)
        proposal = weights + step * gradient
        projected = _project(problem, proposal, support_mask)
        if projected is None:
            return None, -math.inf
        objective = _objective(problem, projected)
        if objective > best_objective + problem.limits.tolerance:
            best_weights = projected.copy()
            best_objective = objective
        delta = float(np.max(np.abs(projected - weights)))
        weights = projected
        if delta <= problem.limits.tolerance:
            stable_iterations += 1
            if stable_iterations >= 5:
                break
        else:
            stable_iterations = 0
    return best_weights, best_objective


def _project(
    problem: _PreparedProblem,
    raw: np.ndarray,
    support_mask: np.ndarray,
) -> np.ndarray | None:
    limits = problem.limits
    weights = _project_basic(problem, raw, support_mask)

    if limits.max_tracking_error is not None:
        for _ in range(12):
            tracking_error = _tracking_error(problem, weights)
            if tracking_error <= limits.max_tracking_error + limits.tolerance:
                break
            if tracking_error <= 0.0:
                break
            ratio = float(limits.max_tracking_error) / tracking_error
            weights = problem.benchmark + ratio * (weights - problem.benchmark)
            weights = _project_basic(problem, weights, support_mask)
        if _tracking_error(problem, weights) > limits.max_tracking_error + 1e-7:
            return None

    current_base = _project_basic(
        problem,
        np.where(support_mask, problem.current, 0.0),
        support_mask,
    )
    base_turnover = _turnover(current_base, problem.current)
    if base_turnover > limits.max_turnover + limits.tolerance:
        return None
    proposed_turnover = _turnover(weights, problem.current)
    if proposed_turnover > limits.max_turnover + limits.tolerance:
        headroom = limits.max_turnover - base_turnover
        span = proposed_turnover - base_turnover
        ratio = 0.0 if span <= limits.tolerance else max(0.0, min(1.0, headroom / span))
        weights = current_base + ratio * (weights - current_base)
        weights = _project_basic(problem, weights, support_mask)

    if limits.max_tracking_error is not None:
        tracking_error = _tracking_error(problem, weights)
        if tracking_error > limits.max_tracking_error + 1e-7:
            return None
    if _turnover(weights, problem.current) > limits.max_turnover + 1e-7:
        return None
    return weights


def _project_basic(
    problem: _PreparedProblem,
    raw: np.ndarray,
    support_mask: np.ndarray,
) -> np.ndarray:
    limits = problem.limits
    caps = np.minimum(problem.liquidity_caps, float(limits.max_name_weight))
    weights = np.where(support_mask, np.asarray(raw, dtype=float), 0.0)
    weights = np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0)
    weights = np.clip(weights, 0.0, caps)

    for constraint in problem.group_constraints:
        mask = constraint.mask & support_mask
        group_weight = float(weights[mask].sum())
        if group_weight > constraint.cap and group_weight > 0.0:
            weights[mask] *= constraint.cap / group_weight

    budget = min(
        float(limits.max_gross_exposure),
        1.0 - float(limits.min_cash_weight),
    )
    gross = float(weights.sum())
    if gross > budget and gross > 0.0:
        weights *= budget / gross
    weights[weights < limits.tolerance * 0.1] = 0.0
    return weights


def _objective(problem: _PreparedProblem, weights: np.ndarray) -> float:
    expected_alpha = float(problem.alpha @ weights)
    portfolio_variance = float(weights @ problem.covariance @ weights)
    active = weights - problem.benchmark
    active_variance = float(active @ problem.covariance @ active)
    expected_cost = float(problem.cost_rates @ np.abs(weights - problem.current))
    return (
        expected_alpha
        - 0.5 * problem.risk_aversion * max(portfolio_variance, 0.0)
        - 0.5 * problem.active_risk_aversion * max(active_variance, 0.0)
        - problem.cost_aversion * expected_cost
    )


def _turnover(weights: np.ndarray, current: np.ndarray) -> float:
    current_cash = max(1.0 - float(current.sum()), 0.0)
    target_cash = max(1.0 - float(weights.sum()), 0.0)
    return 0.5 * (
        float(np.abs(weights - current).sum())
        + abs(target_cash - current_cash)
    )


def _tracking_error(problem: _PreparedProblem, weights: np.ndarray) -> float:
    active = weights - problem.benchmark
    variance = float(active @ problem.covariance @ active)
    return math.sqrt(max(variance, 0.0))


def _build_solution(
    problem: _PreparedProblem,
    weights: np.ndarray,
) -> PortfolioSolution:
    weight_series = pd.Series(weights, index=problem.codes, dtype=float, name="weight")
    expected_alpha = float(problem.alpha @ weights)
    expected_cost = float(problem.cost_rates @ np.abs(weights - problem.current))
    variance = float(weights @ problem.covariance @ weights)
    volatility = math.sqrt(max(variance, 0.0))
    marginal_variance = problem.covariance @ weights
    if volatility > problem.limits.tolerance:
        components = weights * marginal_variance / volatility
    else:
        components = np.zeros_like(weights)
    risk_contributions = {
        code: float(value)
        for code, value in zip(problem.codes, components)
    }

    exposure_values = problem.exposures.to_numpy(dtype=float).T @ weights
    active_exposure_values = (
        problem.exposures.to_numpy(dtype=float).T
        @ (weights - problem.benchmark)
    )
    exposures = {
        str(column): float(value)
        for column, value in zip(problem.exposures.columns, exposure_values)
    }
    exposures.update(
        {
            f"active:{column}": float(value)
            for column, value in zip(
                problem.exposures.columns,
                active_exposure_values,
            )
        }
    )
    exposures.update(_group_exposures(problem, weights))

    return PortfolioSolution(
        weights=weight_series,
        cash_weight=max(1.0 - float(weights.sum()), 0.0),
        expected_alpha=expected_alpha,
        expected_cost=expected_cost,
        turnover=_turnover(weights, problem.current),
        volatility=volatility,
        tracking_error=_tracking_error(problem, weights),
        exposures=exposures,
        risk_contributions=risk_contributions,
        stress_losses=_stress_losses(problem, exposures),
        binding_constraints=_binding_constraints(problem, weights),
        fallback_reason=None,
    )


def _group_exposures(
    problem: _PreparedProblem,
    weights: np.ndarray,
) -> dict[str, float]:
    return {
        f"group:{constraint.column}:{constraint.value}": float(
            weights[constraint.mask].sum()
        )
        for constraint in problem.group_constraints
    }


def _stress_losses(
    problem: _PreparedProblem,
    exposures: Mapping[str, float],
) -> dict[str, float]:
    market = max(
        0.0,
        -float(problem.stress_shocks["market"])
        * float(exposures.get("market_beta", 0.0)),
    )
    volatility = max(
        0.0,
        -float(problem.stress_shocks["volatility"])
        * float(exposures.get("volatility_beta", 0.0)),
    )
    fx = max(
        0.0,
        -float(problem.stress_shocks["fx"])
        * float(exposures.get("fx_beta", 0.0)),
    )
    premium = max(
        0.0,
        -float(problem.stress_shocks["premium"])
        * float(exposures.get("premium_beta", 0.0)),
    )
    industry_values = [
        float(value)
        for key, value in exposures.items()
        if key.startswith("industry:")
        and not key.startswith("active:")
    ]
    if not industry_values:
        industry_values = [
            float(value)
            for key, value in exposures.items()
            if key.startswith("group:industry:")
        ]
    industry = (
        max(
            0.0,
            -float(problem.stress_shocks["industry"])
            * max(industry_values),
        )
        if industry_values
        else 0.0
    )
    return {
        "market": market,
        "industry": industry,
        "volatility": volatility,
        "fx": fx,
        "premium": premium,
    }


def _binding_constraints(
    problem: _PreparedProblem,
    weights: np.ndarray,
) -> tuple[str, ...]:
    limits = problem.limits
    tolerance = max(limits.tolerance * 10.0, 1e-7)
    bindings: set[str] = set()
    budget = min(limits.max_gross_exposure, 1.0 - limits.min_cash_weight)
    gross = float(weights.sum())
    if abs(gross - budget) <= tolerance:
        bindings.add("max_gross_exposure")
    if abs((1.0 - gross) - limits.min_cash_weight) <= tolerance:
        bindings.add("min_cash_weight")
    if int((weights > limits.tolerance).sum()) >= limits.max_positions:
        bindings.add("max_positions")
    if _turnover(weights, problem.current) >= limits.max_turnover - tolerance:
        bindings.add("max_turnover")
    if (
        limits.max_tracking_error is not None
        and _tracking_error(problem, weights)
        >= limits.max_tracking_error - tolerance
    ):
        bindings.add("max_tracking_error")

    for index, code in enumerate(problem.codes):
        if weights[index] >= limits.max_name_weight - tolerance:
            bindings.add(f"max_name_weight:{code}")
        if weights[index] >= problem.liquidity_caps[index] - tolerance:
            bindings.add(f"liquidity:{code}")

    for constraint in problem.group_constraints:
        if float(weights[constraint.mask].sum()) >= constraint.cap - tolerance:
            bindings.add(f"group:{constraint.column}:{constraint.value}")
    return tuple(sorted(bindings))


def _cash_solution(
    codes: tuple[str, ...],
    reason: str,
) -> PortfolioSolution:
    weights = pd.Series(0.0, index=codes, dtype=float, name="weight")
    return PortfolioSolution(
        weights=weights,
        cash_weight=1.0,
        expected_alpha=0.0,
        expected_cost=0.0,
        turnover=0.0,
        volatility=0.0,
        tracking_error=0.0,
        exposures={},
        risk_contributions={code: 0.0 for code in codes},
        stress_losses={key: 0.0 for key in DEFAULT_STRESS_SHOCKS},
        binding_constraints=("fail_closed",),
        fallback_reason=reason,
    )
