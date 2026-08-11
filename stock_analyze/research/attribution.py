"""Deterministic daily P&L attribution for A-share and QDII portfolios.

All return-like inputs are decimal returns. ``opening_nav``, fees, constraint
effects, and outputs are monetary amounts in the account base currency.
Security rows are diagnostic detail; the additive portfolio identity is:

    market + industry + factor selection + model selection + sizing + timing
    + cash + cost + constraint + residual = net_pnl

Missing explanatory data is retained in ``residual`` but never treated as
explained. Missing data required to calculate net P&L produces ``unavailable``
unless observed net P&L is supplied. A reconciled identity is marked complete
only when the residual is within the configured evidence limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Mapping


SUPPORTED_MARKETS = frozenset({"a_share", "cn_qdii_etf"})
ATTRIBUTION_CONTRACT_VERSION = "pnl-attribution-v2"
DEFAULT_MAX_RESIDUAL_RATIO = 0.05


@dataclass(frozen=True)
class DailyAttributionInput:
    market: str
    as_of: str
    before_weights: Mapping[str, float]
    after_weights: Mapping[str, float]
    security_returns: Mapping[str, float | None]
    opening_nav: float = 1.0
    benchmark_returns: Mapping[str, float] | float | None = None
    benchmark_exposures: (
        Mapping[str, Mapping[str, float] | float] | None
    ) = None
    factor_exposures: Mapping[str, Mapping[str, float]] = field(
        default_factory=dict
    )
    factor_returns: Mapping[str, float] = field(default_factory=dict)
    cash_return: float | None = 0.0
    estimated_fees: float | Mapping[str, float] | None = 0.0
    realized_fees: float | Mapping[str, float] | None = None
    model_selection_effects: float | Mapping[str, float] | None = None
    sizing_effects: float | Mapping[str, float] | None = None
    timing_effects: float | Mapping[str, float] | None = None
    constraint_effects: float | Mapping[str, float] | None = None
    observed_net_pnl: float | None = None
    strategy_id: str = ""
    account_id: str = ""
    model_policy_status: str = "unknown"
    model_versions: Mapping[str, str] = field(default_factory=dict)
    holding_episode_ids: Mapping[str, str] = field(default_factory=dict)
    declared_unavailable_inputs: tuple[str, ...] = ()
    max_residual_ratio: float = DEFAULT_MAX_RESIDUAL_RATIO
    tolerance: float = 1e-8


@dataclass(frozen=True)
class SecurityAttribution:
    code: str
    before_weight: float
    after_weight: float
    security_return: float | None
    gross_pnl: float | None
    status: str
    holding_episode_id: str = ""
    market_pnl: float = 0.0
    industry_pnl: float = 0.0
    factor_selection_pnl: float = 0.0
    model_selection_pnl: float = 0.0
    sizing_pnl: float = 0.0
    timing_pnl: float = 0.0
    residual_pnl: float | None = None


@dataclass(frozen=True)
class DailyAttributionResult:
    market_id: str
    as_of: str
    status: str
    security: tuple[SecurityAttribution, ...]
    market: float
    industry: float
    alpha: float
    model_selection: float
    sizing: float
    timing: float
    cash: float
    cost: float
    constraint: float
    residual: float
    net_pnl: float | None
    reconciliation_delta: float | None
    factor_breakdown: Mapping[str, float]
    factor_family_breakdown: Mapping[str, float]
    model_selection_breakdown: Mapping[str, float]
    sizing_breakdown: Mapping[str, float]
    timing_breakdown: Mapping[str, float]
    qdii_breakdown: Mapping[str, float]
    constraint_breakdown: Mapping[str, float]
    unavailable_inputs: tuple[str, ...]
    cost_basis: str
    cost_estimation_error: float | None
    opening_nav: float
    opening_cash_weight: float
    closing_cash_weight: float
    turnover: float
    explained_ratio: float
    residual_ratio: float
    top_positive_drivers: tuple[tuple[str, float], ...]
    top_negative_drivers: tuple[tuple[str, float], ...]
    strategy_id: str
    account_id: str
    model_policy_status: str
    model_versions: Mapping[str, str]
    attribution_contract_version: str
    tolerance: float

    @property
    def factor_selection(self) -> float:
        """Compatibility-safe name for the legacy ``alpha`` component."""

        return self.alpha

    @property
    def is_reconciled(self) -> bool:
        return (
            self.reconciliation_delta is not None
            and abs(self.reconciliation_delta) <= self.tolerance
        )

    def to_lineage_rows(
        self,
        decision_run_id: str,
        *,
        fill_ids: Mapping[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Return deterministic records accepted by ``pnl_attributions``."""

        run_id = str(decision_run_id).strip()
        if not run_id:
            raise ValueError("decision_run_id_required")
        fills = {
            str(code): str(fill_id)
            for code, fill_id in (fill_ids or {}).items()
            if str(fill_id).strip()
        }
        summary: dict[str, object] = {
            "decision_run_id": run_id,
            "fill_id": None,
            "security_code": "__PORTFOLIO__",
            "as_of": self.as_of,
            "market": self.market_id,
            "status": self.status,
            "strategy_id": self.strategy_id,
            "account_id": self.account_id,
            "model_policy_status": self.model_policy_status,
            "model_versions": dict(self.model_versions),
            "attribution_contract_version": self.attribution_contract_version,
            "market_pnl": self.market,
            "industry_pnl": self.industry,
            "alpha_pnl": self.alpha,
            "factor_selection_pnl": self.factor_selection,
            "model_selection_pnl": self.model_selection,
            "sizing_pnl": self.sizing,
            "timing_pnl": self.timing,
            "cash_pnl": self.cash,
            "cost_pnl": self.cost,
            "constraint_pnl": self.constraint,
            "residual_pnl": self.residual,
            "net_pnl": self.net_pnl,
            "reconciliation_delta": self.reconciliation_delta,
            "explained_ratio": self.explained_ratio,
            "residual_ratio": self.residual_ratio,
            "factor_breakdown": dict(self.factor_breakdown),
            "factor_family_breakdown": dict(self.factor_family_breakdown),
            "model_selection_breakdown": dict(
                self.model_selection_breakdown
            ),
            "sizing_breakdown": dict(self.sizing_breakdown),
            "timing_breakdown": dict(self.timing_breakdown),
            "qdii_breakdown": dict(self.qdii_breakdown),
            "top_positive_drivers": [
                {"driver": name, "pnl": value}
                for name, value in self.top_positive_drivers
            ],
            "top_negative_drivers": [
                {"driver": name, "pnl": value}
                for name, value in self.top_negative_drivers
            ],
            "unavailable_inputs": list(self.unavailable_inputs),
        }
        summary["pnl_attribution_id"] = _lineage_id(
            run_id,
            self.as_of,
            self.market_id,
            "__PORTFOLIO__",
        )

        rows = [summary]
        for item in self.security:
            row: dict[str, object] = {
                "decision_run_id": run_id,
                "fill_id": fills.get(item.code),
                "security_code": item.code,
                "as_of": self.as_of,
                "market": self.market_id,
                "status": item.status,
                "before_weight": item.before_weight,
                "after_weight": item.after_weight,
                "security_return": item.security_return,
                "gross_pnl": item.gross_pnl,
                "market_pnl": item.market_pnl,
                "industry_pnl": item.industry_pnl,
                "factor_selection_pnl": item.factor_selection_pnl,
                "model_selection_pnl": item.model_selection_pnl,
                "sizing_pnl": item.sizing_pnl,
                "timing_pnl": item.timing_pnl,
                "residual_pnl": item.residual_pnl,
                "holding_episode_id": item.holding_episode_id,
                "strategy_id": self.strategy_id,
                "account_id": self.account_id,
                "model_policy_status": self.model_policy_status,
                "model_versions": dict(self.model_versions),
                "attribution_contract_version": (
                    self.attribution_contract_version
                ),
            }
            row["pnl_attribution_id"] = _lineage_id(
                run_id,
                self.as_of,
                self.market_id,
                item.code,
            )
            rows.append(row)
        return rows


def attribute_daily_pnl(
    request: DailyAttributionInput,
) -> DailyAttributionResult:
    """Attribute one account's daily P&L without inventing missing inputs."""

    market_id = str(request.market).strip()
    if market_id not in SUPPORTED_MARKETS:
        raise ValueError(f"attribution_market_unsupported:{market_id}")
    as_of = str(request.as_of).strip()
    if not as_of:
        raise ValueError("attribution_as_of_required")
    opening_nav = _finite_number(request.opening_nav, "opening_nav")
    if opening_nav <= 0.0:
        raise ValueError("attribution_opening_nav_must_be_positive")
    tolerance = _finite_number(request.tolerance, "tolerance")
    if tolerance <= 0.0:
        raise ValueError("attribution_tolerance_must_be_positive")
    max_residual_ratio = _finite_number(
        request.max_residual_ratio,
        "max_residual_ratio",
    )
    if not 0.0 <= max_residual_ratio <= 1.0:
        raise ValueError("attribution_max_residual_ratio_out_of_range")
    strategy_id = str(request.strategy_id).strip()
    account_id = str(request.account_id).strip()
    model_policy_status = str(
        request.model_policy_status or "unknown"
    ).strip().lower()
    model_versions = _normalise_string_mapping(
        request.model_versions,
        "model_versions",
    )
    holding_episode_ids = _normalise_string_mapping(
        request.holding_episode_ids,
        "holding_episode_ids",
    )

    before = _normalise_weights(
        request.before_weights,
        "before_weights",
        tolerance,
    )
    after = _normalise_weights(
        request.after_weights,
        "after_weights",
        tolerance,
    )
    codes = tuple(sorted(set(before) | set(after)))
    security_returns = _normalise_optional_values(
        request.security_returns,
        "security_returns",
    )
    unavailable: set[str] = set()
    unavailable.update(
        str(value).strip()
        for value in request.declared_unavailable_inputs
        if str(value).strip()
    )
    required_missing = False
    security_rows: list[SecurityAttribution] = []
    known_security_pnl = 0.0

    for code in codes:
        before_weight = before.get(code, 0.0)
        after_weight = after.get(code, 0.0)
        security_return = security_returns.get(code)
        if before_weight <= tolerance:
            security_rows.append(
                SecurityAttribution(
                    code=code,
                    before_weight=before_weight,
                    after_weight=after_weight,
                    security_return=security_return,
                    gross_pnl=0.0,
                    status="not_held_at_open",
                    holding_episode_id=holding_episode_ids.get(code, ""),
                )
            )
            continue
        if security_return is None:
            unavailable.add(f"security_return:{code}")
            required_missing = True
            security_rows.append(
                SecurityAttribution(
                    code=code,
                    before_weight=before_weight,
                    after_weight=after_weight,
                    security_return=None,
                    gross_pnl=None,
                    status="unavailable",
                    holding_episode_id=holding_episode_ids.get(code, ""),
                )
            )
            continue
        gross_pnl = before_weight * security_return * opening_nav
        known_security_pnl += gross_pnl
        security_rows.append(
            SecurityAttribution(
                code=code,
                before_weight=before_weight,
                after_weight=after_weight,
                security_return=security_return,
                gross_pnl=gross_pnl,
                status="available",
                holding_episode_id=holding_episode_ids.get(code, ""),
            )
        )

    opening_cash_weight = _cash_weight(before, tolerance)
    closing_cash_weight = _cash_weight(after, tolerance)
    if request.cash_return is None and opening_cash_weight > tolerance:
        cash_pnl = 0.0
        unavailable.add("cash_return")
        required_missing = True
    else:
        cash_return = (
            0.0
            if request.cash_return is None
            else _finite_number(request.cash_return, "cash_return")
        )
        cash_pnl = opening_cash_weight * cash_return * opening_nav

    market_pnl, benchmark_missing, benchmark_by_security = (
        _benchmark_attribution(
        codes=codes,
        weights=before,
        opening_nav=opening_nav,
        returns=request.benchmark_returns,
        exposures=request.benchmark_exposures,
        tolerance=tolerance,
        )
    )
    unavailable.update(benchmark_missing)

    (
        factor_breakdown,
        factor_market_pnl,
        industry_pnl,
        alpha_pnl,
        qdii_breakdown,
        factor_missing,
        factor_by_security,
    ) = _factor_attribution(
        codes=codes,
        weights=before,
        opening_nav=opening_nav,
        exposures=request.factor_exposures,
        returns=request.factor_returns,
        tolerance=tolerance,
    )
    market_pnl += factor_market_pnl
    unavailable.update(factor_missing)
    factor_family_breakdown = _factor_family_breakdown(factor_breakdown)

    model_selection_breakdown = _amount_breakdown(
        request.model_selection_effects,
        "model_selection_effects",
    )
    model_selection_pnl = sum(model_selection_breakdown.values())
    sizing_breakdown = _amount_breakdown(
        request.sizing_effects,
        "sizing_effects",
    )
    sizing_pnl = sum(sizing_breakdown.values())
    timing_breakdown = _amount_breakdown(
        request.timing_effects,
        "timing_effects",
    )
    timing_pnl = sum(timing_breakdown.values())
    if model_policy_status == "rule_only" and (
        abs(model_selection_pnl) > tolerance or model_versions
    ):
        raise ValueError("attribution_model_effect_for_rule_only")
    if abs(model_selection_pnl) > tolerance:
        if model_policy_status not in {
            "active",
            "champion",
            "shadow",
            "research",
        }:
            unavailable.add("model_policy_status")
        if not model_versions:
            unavailable.add("model_versions")

    security_rows = [
        _enrich_security_attribution(
            item,
            benchmark_pnl=benchmark_by_security.get(item.code, 0.0),
            factor_components=factor_by_security.get(item.code, {}),
            model_selection_pnl=model_selection_breakdown.get(item.code, 0.0),
            sizing_pnl=sizing_breakdown.get(item.code, 0.0),
            timing_pnl=timing_breakdown.get(item.code, 0.0),
        )
        for item in security_rows
    ]

    estimated_fee = _sum_non_negative_amounts(
        request.estimated_fees,
        "estimated_fees",
    )
    realized_fee = _sum_non_negative_amounts(
        request.realized_fees,
        "realized_fees",
    )
    turnover = 0.5 * sum(
        abs(after.get(code, 0.0) - before.get(code, 0.0))
        for code in codes
    )
    if realized_fee is not None:
        fee = realized_fee
        cost_basis = "realized"
    elif estimated_fee is not None:
        fee = estimated_fee
        cost_basis = "estimated"
    else:
        fee = 0.0
        cost_basis = "unavailable" if turnover > tolerance else "none"
        if turnover > tolerance:
            unavailable.add("fees")
            required_missing = True
    cost_pnl = -fee
    cost_estimation_error = (
        cost_pnl - (-estimated_fee)
        if realized_fee is not None and estimated_fee is not None
        else None
    )

    constraint_breakdown = _amount_breakdown(
        request.constraint_effects,
        "constraint_effects",
    )
    constraint_pnl = sum(constraint_breakdown.values())

    residual_pnl = (
        known_security_pnl
        - market_pnl
        - industry_pnl
        - alpha_pnl
        - model_selection_pnl
        - sizing_pnl
        - timing_pnl
    )
    component_total = (
        market_pnl
        + industry_pnl
        + alpha_pnl
        + model_selection_pnl
        + sizing_pnl
        + timing_pnl
        + cash_pnl
        + cost_pnl
        + constraint_pnl
        + residual_pnl
    )

    observed_net_pnl = (
        None
        if request.observed_net_pnl is None
        else _finite_number(request.observed_net_pnl, "observed_net_pnl")
    )
    if observed_net_pnl is not None:
        residual_pnl += observed_net_pnl - component_total
        net_pnl: float | None = observed_net_pnl
        status = "partial" if unavailable else "complete"
    elif required_missing:
        net_pnl = None
        status = "unavailable"
    else:
        net_pnl = component_total
        status = "partial" if unavailable else "complete"

    reconciliation_delta: float | None
    if net_pnl is None:
        reconciliation_delta = None
    else:
        additive_total = (
            market_pnl
            + industry_pnl
            + alpha_pnl
            + model_selection_pnl
            + sizing_pnl
            + timing_pnl
            + cash_pnl
            + cost_pnl
            + constraint_pnl
            + residual_pnl
        )
        delta = net_pnl - additive_total
        if abs(delta) > tolerance:
            raise ArithmeticError(
                f"attribution_reconciliation_failed:{delta:.12g}"
            )
        reconciliation_delta = 0.0 if abs(delta) <= tolerance else delta

    if net_pnl is None:
        residual_ratio = 1.0
        explained_ratio = 0.0
    else:
        explained_components = (
            market_pnl,
            industry_pnl,
            alpha_pnl,
            model_selection_pnl,
            sizing_pnl,
            timing_pnl,
            cash_pnl,
            cost_pnl,
            constraint_pnl,
        )
        attribution_basis = max(
            abs(net_pnl),
            sum(abs(value) for value in explained_components)
            + abs(residual_pnl),
            tolerance,
        )
        residual_ratio = min(abs(residual_pnl) / attribution_basis, 1.0)
        explained_ratio = max(0.0, 1.0 - residual_ratio)
        if residual_ratio > max_residual_ratio + tolerance:
            unavailable.add("residual_above_limit")
            if status == "complete":
                status = "partial"

    top_positive_drivers, top_negative_drivers = _ranked_drivers(
        {
            "market_beta": market_pnl,
            "industry_scope": industry_pnl,
            "factor_selection": alpha_pnl,
            "model_selection": model_selection_pnl,
            "sizing": sizing_pnl,
            "timing": timing_pnl,
            "cash": cash_pnl,
            "transaction_cost": cost_pnl,
            "constraints": constraint_pnl,
        },
        tolerance=tolerance,
    )

    return DailyAttributionResult(
        market_id=market_id,
        as_of=as_of,
        status=status,
        security=tuple(security_rows),
        market=market_pnl,
        industry=industry_pnl,
        alpha=alpha_pnl,
        model_selection=model_selection_pnl,
        sizing=sizing_pnl,
        timing=timing_pnl,
        cash=cash_pnl,
        cost=cost_pnl,
        constraint=constraint_pnl,
        residual=residual_pnl,
        net_pnl=net_pnl,
        reconciliation_delta=reconciliation_delta,
        factor_breakdown=factor_breakdown,
        factor_family_breakdown=factor_family_breakdown,
        model_selection_breakdown=model_selection_breakdown,
        sizing_breakdown=sizing_breakdown,
        timing_breakdown=timing_breakdown,
        qdii_breakdown=qdii_breakdown,
        constraint_breakdown=constraint_breakdown,
        unavailable_inputs=tuple(sorted(unavailable)),
        cost_basis=cost_basis,
        cost_estimation_error=cost_estimation_error,
        opening_nav=opening_nav,
        opening_cash_weight=opening_cash_weight,
        closing_cash_weight=closing_cash_weight,
        turnover=turnover,
        explained_ratio=explained_ratio,
        residual_ratio=residual_ratio,
        top_positive_drivers=top_positive_drivers,
        top_negative_drivers=top_negative_drivers,
        strategy_id=strategy_id,
        account_id=account_id,
        model_policy_status=model_policy_status,
        model_versions=model_versions,
        attribution_contract_version=ATTRIBUTION_CONTRACT_VERSION,
        tolerance=tolerance,
    )


def _normalise_weights(
    raw: Mapping[str, float],
    name: str,
    tolerance: float,
) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"attribution_{name}_must_be_mapping")
    result: dict[str, float] = {}
    for raw_code, raw_value in raw.items():
        code = str(raw_code).strip()
        if not code:
            raise ValueError(f"attribution_{name}_code_required")
        if code in result:
            raise ValueError(f"attribution_{name}_duplicate_code:{code}")
        value = _finite_number(raw_value, f"{name}:{code}")
        if value < -tolerance:
            raise ValueError(f"attribution_{name}_negative:{code}")
        result[code] = max(value, 0.0)
    if sum(result.values()) > 1.0 + tolerance:
        raise ValueError(f"attribution_{name}_leveraged")
    return dict(sorted(result.items()))


def _normalise_optional_values(
    raw: Mapping[str, float | None],
    name: str,
) -> dict[str, float | None]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"attribution_{name}_must_be_mapping")
    result: dict[str, float | None] = {}
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError(f"attribution_{name}_key_required")
        if key in result:
            raise ValueError(f"attribution_{name}_duplicate_key:{key}")
        result[key] = (
            None
            if raw_value is None
            else _finite_number(raw_value, f"{name}:{key}")
        )
    return dict(sorted(result.items()))


def _normalise_string_mapping(
    raw: Mapping[str, str],
    name: str,
) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise TypeError(f"attribution_{name}_must_be_mapping")
    result: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if not key:
            raise ValueError(f"attribution_{name}_key_required")
        if not value:
            raise ValueError(f"attribution_{name}_value_required:{key}")
        if key in result:
            raise ValueError(f"attribution_{name}_duplicate_key:{key}")
        result[key] = value
    return dict(sorted(result.items()))


def _cash_weight(weights: Mapping[str, float], tolerance: float) -> float:
    value = 1.0 - sum(weights.values())
    return 0.0 if abs(value) <= tolerance else value


def _enrich_security_attribution(
    item: SecurityAttribution,
    *,
    benchmark_pnl: float,
    factor_components: Mapping[str, float],
    model_selection_pnl: float,
    sizing_pnl: float,
    timing_pnl: float,
) -> SecurityAttribution:
    factor_market = float(factor_components.get("market", 0.0))
    industry = float(factor_components.get("industry", 0.0))
    factor_selection = float(factor_components.get("alpha", 0.0))
    market = benchmark_pnl + factor_market
    residual = (
        None
        if item.gross_pnl is None
        else item.gross_pnl
        - market
        - industry
        - factor_selection
        - model_selection_pnl
        - sizing_pnl
        - timing_pnl
    )
    return replace(
        item,
        market_pnl=market,
        industry_pnl=industry,
        factor_selection_pnl=factor_selection,
        model_selection_pnl=model_selection_pnl,
        sizing_pnl=sizing_pnl,
        timing_pnl=timing_pnl,
        residual_pnl=residual,
    )


def _benchmark_attribution(
    *,
    codes: tuple[str, ...],
    weights: Mapping[str, float],
    opening_nav: float,
    returns: Mapping[str, float] | float | None,
    exposures: Mapping[str, Mapping[str, float] | float] | None,
    tolerance: float,
) -> tuple[float, set[str], dict[str, float]]:
    invested = [code for code in codes if weights.get(code, 0.0) > tolerance]
    if not invested:
        return 0.0, set(), {}
    if returns is None and exposures is None:
        return 0.0, {"benchmark_attribution"}, {}

    benchmark_returns = _benchmark_returns(returns)
    if not benchmark_returns:
        return 0.0, {"benchmark_returns"}, {}
    if exposures is None:
        return 0.0, {"benchmark_exposures"}, {}
    if not isinstance(exposures, Mapping):
        raise TypeError("attribution_benchmark_exposures_must_be_mapping")

    missing: set[str] = set()
    contribution = 0.0
    by_security: dict[str, float] = {}
    for code in invested:
        raw_exposure = exposures.get(code)
        if raw_exposure is None:
            missing.add(f"benchmark_exposure:{code}")
            continue
        if isinstance(raw_exposure, Mapping):
            aligned = {
                str(key): _finite_number(
                    value,
                    f"benchmark_exposure:{code}:{key}",
                )
                for key, value in raw_exposure.items()
            }
        else:
            if len(benchmark_returns) != 1:
                missing.add(f"benchmark_exposure_ambiguous:{code}")
                continue
            aligned = {
                next(iter(benchmark_returns)): _finite_number(
                    raw_exposure,
                    f"benchmark_exposure:{code}",
                )
            }
        for benchmark_id in sorted(aligned):
            if benchmark_id not in benchmark_returns:
                missing.add(f"benchmark_return:{benchmark_id}")
                continue
            value = (
                weights[code]
                * aligned[benchmark_id]
                * benchmark_returns[benchmark_id]
                * opening_nav
            )
            contribution += value
            by_security[code] = by_security.get(code, 0.0) + value
    return contribution, missing, dict(sorted(by_security.items()))


def _benchmark_returns(
    raw: Mapping[str, float] | float | None,
) -> dict[str, float]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {
            str(key): _finite_number(value, f"benchmark_return:{key}")
            for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
        }
    return {"benchmark": _finite_number(raw, "benchmark_return")}


def _factor_attribution(
    *,
    codes: tuple[str, ...],
    weights: Mapping[str, float],
    opening_nav: float,
    exposures: Mapping[str, Mapping[str, float]],
    returns: Mapping[str, float],
    tolerance: float,
) -> tuple[
    dict[str, float],
    float,
    float,
    float,
    dict[str, float],
    set[str],
    dict[str, dict[str, float]],
]:
    invested = [code for code in codes if weights.get(code, 0.0) > tolerance]
    if not invested:
        return {}, 0.0, 0.0, 0.0, {}, set(), {}
    if not isinstance(exposures, Mapping):
        raise TypeError("attribution_factor_exposures_must_be_mapping")
    if not isinstance(returns, Mapping):
        raise TypeError("attribution_factor_returns_must_be_mapping")
    if not exposures and not returns:
        return {}, 0.0, 0.0, 0.0, {}, {"factor_attribution"}, {}

    factor_returns = {
        str(factor): _finite_number(value, f"factor_return:{factor}")
        for factor, value in returns.items()
    }
    breakdown: dict[str, float] = {}
    by_security: dict[str, dict[str, float]] = {}
    missing: set[str] = set()
    for code in invested:
        raw_code_exposures = exposures.get(code)
        if raw_code_exposures is None:
            if factor_returns:
                missing.add(f"factor_exposure:{code}")
            continue
        if not isinstance(raw_code_exposures, Mapping):
            raise TypeError(f"attribution_factor_exposure_must_be_mapping:{code}")
        for raw_factor in sorted(raw_code_exposures, key=str):
            factor = str(raw_factor)
            exposure = _finite_number(
                raw_code_exposures[raw_factor],
                f"factor_exposure:{code}:{factor}",
            )
            if abs(exposure) <= tolerance:
                continue
            if factor not in factor_returns:
                missing.add(f"factor_return:{factor}")
                continue
            contribution = (
                weights[code] * exposure * factor_returns[factor] * opening_nav
            )
            breakdown[factor] = breakdown.get(factor, 0.0) + contribution
            category = _factor_category(factor)
            security_components = by_security.setdefault(code, {})
            security_components[category] = (
                security_components.get(category, 0.0) + contribution
            )

    market = sum(
        value
        for factor, value in breakdown.items()
        if _factor_category(factor) == "market"
    )
    industry = sum(
        value
        for factor, value in breakdown.items()
        if _factor_category(factor) == "industry"
    )
    alpha = sum(
        value
        for factor, value in breakdown.items()
        if _factor_category(factor) == "alpha"
    )
    qdii = {"fx": 0.0, "premium": 0.0}
    qdii_seen: set[str] = set()
    for factor, value in breakdown.items():
        subtype = _qdii_subtype(factor)
        if subtype is not None:
            qdii[subtype] += value
            qdii_seen.add(subtype)
    return (
        dict(sorted(breakdown.items())),
        market,
        industry,
        alpha,
        {key: qdii[key] for key in sorted(qdii_seen)},
        missing,
        {
            code: dict(sorted(components.items()))
            for code, components in sorted(by_security.items())
        },
    )


def _factor_category(factor: str) -> str:
    value = factor.strip().lower()
    if (
        value in {"market", "market_beta", "beta"}
        or value.startswith("market:")
        or value.startswith("market_")
    ):
        return "market"
    if value.startswith("industry:") or value.startswith("industry_"):
        return "industry"
    return "alpha"


def _factor_family_breakdown(
    breakdown: Mapping[str, float],
) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for factor, value in breakdown.items():
        family = _factor_family(factor)
        grouped[family] = grouped.get(family, 0.0) + value
    return dict(sorted(grouped.items()))


def _factor_family(factor: str) -> str:
    category = _factor_category(factor)
    if category != "alpha":
        return category
    qdii_subtype = _qdii_subtype(factor)
    if qdii_subtype is not None:
        return f"qdii_{qdii_subtype}"
    value = factor.strip().lower()
    if any(
        token in value
        for token in (
            "event",
            "announcement",
            "policy",
            "news",
            "sentiment",
        )
    ):
        return "market_intelligence"
    if any(
        token in value
        for token in (
            "macd",
            "momentum",
            "volatility",
            "rsi",
            "adx",
            "sma",
            "ema",
            "bollinger",
            "volume",
            "turnover",
            "obv",
        )
    ):
        return "technical"
    if any(
        token in value
        for token in (
            "pe",
            "pb",
            "roe",
            "margin",
            "growth",
            "debt",
            "cashflow",
            "profit",
            "quality",
        )
    ):
        return "fundamental"
    return "other_alpha"


def _ranked_drivers(
    drivers: Mapping[str, float],
    *,
    tolerance: float,
    limit: int = 5,
) -> tuple[tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]]:
    positive = sorted(
        (
            (str(name), float(value))
            for name, value in drivers.items()
            if value > tolerance
        ),
        key=lambda item: (-item[1], item[0]),
    )[:limit]
    negative = sorted(
        (
            (str(name), float(value))
            for name, value in drivers.items()
            if value < -tolerance
        ),
        key=lambda item: (item[1], item[0]),
    )[:limit]
    return tuple(positive), tuple(negative)


def _qdii_subtype(factor: str) -> str | None:
    value = factor.strip().lower()
    if (
        value in {"fx", "currency", "currency_return"}
        or value.startswith("fx:")
        or value.startswith("fx_")
        or value.startswith("currency:")
    ):
        return "fx"
    if (
        value in {"premium", "discount_premium"}
        or value.startswith("premium:")
        or value.startswith("premium_")
    ):
        return "premium"
    return None


def _sum_non_negative_amounts(
    raw: float | Mapping[str, float] | None,
    name: str,
) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        values = [
            _finite_number(value, f"{name}:{key}")
            for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
        ]
    else:
        values = [_finite_number(raw, name)]
    if any(value < 0.0 for value in values):
        raise ValueError(f"attribution_{name}_negative")
    return sum(values)


def _amount_breakdown(
    raw: float | Mapping[str, float] | None,
    name: str,
) -> dict[str, float]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {
            str(key): _finite_number(value, f"{name}:{key}")
            for key, value in sorted(raw.items(), key=lambda item: str(item[0]))
        }
    return {"total": _finite_number(raw, name)}


def _finite_number(raw: object, name: str) -> float:
    if isinstance(raw, bool):
        raise TypeError(f"attribution_{name}_must_be_numeric")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"attribution_{name}_must_be_numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"attribution_{name}_must_be_finite")
    return value


def _lineage_id(
    decision_run_id: str,
    as_of: str,
    market: str,
    security_code: str,
) -> str:
    payload = json.dumps(
        [decision_run_id, as_of, market, security_code],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
