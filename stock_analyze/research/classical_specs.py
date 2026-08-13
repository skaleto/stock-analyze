"""Immutable, bounded classical-model specifications for scoped tournaments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ClassicalModelSpec:
    spec_id: str
    market: str
    account_scope: str
    horizon: int
    estimator: str
    feature_profile: str
    parameters: tuple[tuple[str, str], ...]
    objective: str = "exact_net_active_return"
    random_state: int = 20260809
    hypothesis_id: str = "legacy"
    feature_allowlist: tuple[str, ...] = ()
    rebalance_frequency: str = "daily"
    ranking_target: str = "raw_excess_return"
    feature_selection_mode: str = "stability_filter_v1"

    @property
    def spec_hash(self) -> str:
        payload = asdict(self)
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    @property
    def parameter_map(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, raw in self.parameters:
            try:
                result[key] = json.loads(raw)
            except json.JSONDecodeError:
                result[key] = raw
        return result

    def as_ledger_spec(self) -> dict[str, str]:
        return {
            "spec_id": self.spec_id,
            "spec_hash": self.spec_hash,
            "market": self.market,
            "account_scope": self.account_scope,
            "horizon": str(self.horizon),
            "estimator": self.estimator,
            "feature_profile": self.feature_profile,
            "parameters": json.dumps(
                self.parameter_map,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "objective": self.objective,
            "random_state": str(self.random_state),
            "hypothesis_id": self.hypothesis_id,
            "feature_allowlist": json.dumps(
                self.feature_allowlist,
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "rebalance_frequency": self.rebalance_frequency,
            "ranking_target": self.ranking_target,
            "feature_selection_mode": self.feature_selection_mode,
        }


def _parameters(**values: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, json.dumps(value, ensure_ascii=True, sort_keys=True))
        for key, value in sorted(values.items())
    )


def a_share_h3_specs(account_scope: str) -> tuple[ClassicalModelSpec, ...]:
    common = {
        "market": "a_share",
        "account_scope": str(account_scope),
        "horizon": 3,
        "feature_profile": "a_share_compact_stationary_v1",
    }
    return (
        ClassicalModelSpec(
            spec_id="ridge_slow_quality_momentum",
            estimator="ridge",
            parameters=_parameters(alpha=20.0, ranking_linear_weight=1.0),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="elastic_net_sparse",
            estimator="elastic_net",
            parameters=_parameters(alpha=0.0005, l1_ratio=0.25, ranking_linear_weight=1.0),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="hgbr_bounded_interactions",
            estimator="hgbr",
            parameters=_parameters(
                learning_rate=0.04,
                max_iter=120,
                max_leaf_nodes=15,
                min_samples_leaf=50,
                l2_regularization=2.0,
                ranking_linear_weight=0.0,
            ),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="ridge_hgbr_fixed_blend",
            estimator="fixed_blend",
            parameters=_parameters(
                ridge_alpha=20.0,
                learning_rate=0.04,
                max_iter=120,
                max_leaf_nodes=15,
                min_samples_leaf=50,
                l2_regularization=2.0,
                ranking_linear_weight=0.75,
            ),
            **common,
        ),
    )


_A_SHARE_H20_QUALITY_LOWVOL = (
    "momentum_20",
    "momentum_60",
    "account_low_volatility_percentile",
    "account_liquidity_percentile",
    "account_quality_percentile",
    "realized_volatility_20",
    "natr_14",
    "roe",
    "roic",
    "cash_conversion",
    "accrual_ratio",
    "free_cashflow_to_assets",
    "gross_profit_to_assets",
    "pe_ttm",
    "pb",
)


def a_share_h20_specs(account_scope: str) -> tuple[ClassicalModelSpec, ...]:
    return (
        ClassicalModelSpec(
            spec_id="h20_momentum_anchor_quality_residual_ridge_v2",
            market="a_share",
            account_scope=str(account_scope),
            horizon=20,
            estimator="ridge",
            feature_profile="a_share_h20_momentum_anchor_residual_v2",
            parameters=_parameters(alpha=25.0, ranking_linear_weight=1.0),
            hypothesis_id="momentum_anchor_quality_residual",
            feature_allowlist=_A_SHARE_H20_QUALITY_LOWVOL,
            rebalance_frequency="monthly",
            ranking_target="momentum_anchor_residual_v1",
            feature_selection_mode="fixed_profile_v1",
        ),
    )


def qdii_h10_specs(account_scope: str) -> tuple[ClassicalModelSpec, ...]:
    common = {
        "market": "cn_qdii_etf",
        "account_scope": str(account_scope),
        "horizon": 10,
        "feature_profile": "qdii_nav_tracking_compact_v1",
    }
    return (
        ClassicalModelSpec(
            spec_id="ridge_nav_tracking",
            estimator="ridge",
            parameters=_parameters(alpha=35.0, ranking_linear_weight=1.0),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="elastic_net_nav_fx",
            estimator="elastic_net",
            parameters=_parameters(alpha=0.001, l1_ratio=0.20, ranking_linear_weight=1.0),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="hgbr_bounded_nav_tracking",
            estimator="hgbr",
            parameters=_parameters(
                learning_rate=0.035,
                max_iter=100,
                max_leaf_nodes=9,
                min_samples_leaf=80,
                l2_regularization=3.0,
                ranking_linear_weight=0.0,
            ),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="ridge_hgbr_fixed_blend",
            estimator="fixed_blend",
            parameters=_parameters(
                ridge_alpha=35.0,
                learning_rate=0.035,
                max_iter=100,
                max_leaf_nodes=9,
                min_samples_leaf=80,
                l2_regularization=3.0,
                ranking_linear_weight=0.75,
            ),
            **common,
        ),
    )


def qdii_h5_specs(account_scope: str) -> tuple[ClassicalModelSpec, ...]:
    common = {
        "market": "cn_qdii_etf",
        "account_scope": str(account_scope),
        "horizon": 5,
        "feature_profile": "qdii_nav_tracking_compact_v1",
    }
    return (
        ClassicalModelSpec(
            spec_id="h5_ridge_nav_tracking",
            estimator="ridge",
            parameters=_parameters(alpha=35.0, ranking_linear_weight=1.0),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="h5_elastic_net_nav_fx",
            estimator="elastic_net",
            parameters=_parameters(
                alpha=0.001,
                l1_ratio=0.20,
                ranking_linear_weight=1.0,
            ),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="h5_hgbr_bounded_nav_tracking",
            estimator="hgbr",
            parameters=_parameters(
                learning_rate=0.035,
                max_iter=100,
                max_leaf_nodes=9,
                min_samples_leaf=80,
                l2_regularization=3.0,
                ranking_linear_weight=0.0,
            ),
            **common,
        ),
        ClassicalModelSpec(
            spec_id="h5_ridge_hgbr_fixed_blend",
            estimator="fixed_blend",
            parameters=_parameters(
                ridge_alpha=35.0,
                learning_rate=0.035,
                max_iter=100,
                max_leaf_nodes=9,
                min_samples_leaf=80,
                l2_regularization=3.0,
                ranking_linear_weight=0.75,
            ),
            **common,
        ),
    )


_MAINLINE_HORIZONS = {
    "a_share": 20,
    "cn_qdii_etf": 10,
}


def mainline_horizon(market: str) -> int:
    normalized = str(market).strip()
    try:
        return _MAINLINE_HORIZONS[normalized]
    except KeyError as exc:
        raise ValueError(f"classical_mainline_market:{normalized}") from exc


def mainline_specs(
    market: str,
    account_scope: str,
) -> tuple[ClassicalModelSpec, ...]:
    normalized = str(market).strip()
    if normalized == "a_share":
        return a_share_h20_specs(account_scope)
    if normalized == "cn_qdii_etf":
        return tuple(
            spec
            for spec in qdii_h10_specs(account_scope)
            if spec.spec_id == "hgbr_bounded_nav_tracking"
        )
    raise ValueError(f"classical_mainline_market:{normalized}")


__all__ = [
    "ClassicalModelSpec",
    "a_share_h3_specs",
    "a_share_h20_specs",
    "mainline_horizon",
    "mainline_specs",
    "qdii_h5_specs",
    "qdii_h10_specs",
]
