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


def transparent_strategy_specs(
    market: str,
    account_scope: str,
) -> tuple[ClassicalModelSpec, ...]:
    """Return the frozen, explainable strategy-recovery trial family."""

    normalized = str(market).strip()
    scope = str(account_scope).strip()
    if normalized == "a_share":
        common = {
            "market": normalized,
            "account_scope": scope,
            "horizon": 20,
            "estimator": "rule",
            "rebalance_frequency": "monthly",
            "ranking_target": "transparent_rule_score_v1",
            "feature_selection_mode": "fixed_profile_v1",
            "random_state": 20260814,
        }
        return (
            ClassicalModelSpec(
                spec_id="A_MOM_01",
                feature_profile="a_share_medium_momentum_v1",
                parameters=_parameters(
                    momentum_60_weight=0.60,
                    momentum_120_weight=0.40,
                    rebalance_trading_days=20,
                    target_risky_exposure=1.0,
                ),
                hypothesis_id="medium_term_momentum",
                feature_allowlist=("momentum_60", "momentum_120"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="A_MOM_02",
                feature_profile="a_share_multi_horizon_momentum_v1",
                parameters=_parameters(
                    momentum_20_weight=0.20,
                    momentum_60_weight=0.40,
                    momentum_120_weight=0.40,
                    rebalance_trading_days=20,
                    target_risky_exposure=1.0,
                ),
                hypothesis_id="multi_horizon_momentum",
                feature_allowlist=("momentum_20", "momentum_60", "momentum_120"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="A_QMLV_01",
                feature_profile="a_share_quality_momentum_low_vol_v1",
                parameters=_parameters(
                    momentum_weight=0.45,
                    quality_weight=0.35,
                    low_volatility_weight=0.20,
                    rebalance_trading_days=20,
                    target_risky_exposure=1.0,
                ),
                hypothesis_id="quality_momentum_low_volatility",
                feature_allowlist=(
                    "momentum_60",
                    "momentum_120",
                    "account_quality_percentile",
                    "account_low_volatility_percentile",
                ),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="A_QMLV_02",
                feature_profile="a_share_quality_value_momentum_v1",
                parameters=_parameters(
                    momentum_weight=0.35,
                    quality_weight=0.30,
                    value_weight=0.20,
                    low_volatility_weight=0.15,
                    rebalance_trading_days=20,
                    target_risky_exposure=1.0,
                ),
                hypothesis_id="quality_value_momentum",
                feature_allowlist=(
                    "momentum_60",
                    "momentum_120",
                    "account_quality_percentile",
                    "pe_ttm",
                    "pb",
                    "account_low_volatility_percentile",
                ),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="A_REGIME_01",
                feature_profile="a_share_momentum_regime_v1",
                parameters=_parameters(
                    base_strategy="A_MOM_01",
                    benchmark_sma_window=200,
                    risk_off_exposure=0.50,
                    risk_on_exposure=1.0,
                    rebalance_trading_days=20,
                ),
                hypothesis_id="momentum_with_market_regime",
                feature_allowlist=("momentum_60", "momentum_120"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="A_REGIME_02",
                feature_profile="a_share_quality_momentum_regime_v1",
                parameters=_parameters(
                    base_strategy="A_QMLV_01",
                    benchmark_sma_window=200,
                    risk_off_exposure=0.50,
                    risk_on_exposure=1.0,
                    rebalance_trading_days=20,
                ),
                hypothesis_id="quality_momentum_with_market_regime",
                feature_allowlist=(
                    "momentum_60",
                    "momentum_120",
                    "account_quality_percentile",
                    "account_low_volatility_percentile",
                ),
                **common,
            ),
        )
    if normalized == "cn_qdii_etf":
        common = {
            "market": normalized,
            "account_scope": scope,
            "horizon": 10,
            "estimator": "rule",
            "rebalance_frequency": "weekly",
            "ranking_target": "transparent_rule_score_v1",
            "feature_selection_mode": "fixed_profile_v1",
            "random_state": 20260814,
        }
        return (
            ClassicalModelSpec(
                spec_id="Q_TREND_01",
                feature_profile="qdii_absolute_trend_v1",
                parameters=_parameters(
                    trend_windows=[60, 120, 200],
                    exposure_by_positive_votes={"0": 0.0, "1": 0.5, "2": 1.0, "3": 1.0},
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_absolute_trend",
                feature_allowlist=("momentum_60", "momentum_120", "sma_200"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="Q_TREND_02",
                feature_profile="qdii_slow_absolute_trend_v1",
                parameters=_parameters(
                    trend_windows=[120, 200],
                    exposure_by_positive_votes={"0": 0.0, "1": 0.5, "2": 1.0},
                    max_risky_exposure=0.85,
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_slow_absolute_trend",
                feature_allowlist=("momentum_120", "sma_200"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="Q_DUAL_01",
                feature_profile="qdii_dual_momentum_v1",
                parameters=_parameters(
                    momentum_60_weight=0.50,
                    momentum_120_weight=0.50,
                    require_sma_200=True,
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_dual_momentum",
                feature_allowlist=("momentum_60", "momentum_120", "sma_200"),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="Q_DUAL_02",
                feature_profile="qdii_dual_momentum_low_vol_v1",
                parameters=_parameters(
                    dual_momentum_weight=0.80,
                    low_volatility_weight=0.20,
                    require_sma_200=True,
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_dual_momentum_low_volatility",
                feature_allowlist=(
                    "momentum_60",
                    "momentum_120",
                    "sma_200",
                    "account_low_volatility_percentile",
                ),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="Q_TRACK_01",
                feature_profile="qdii_trend_product_quality_v1",
                parameters=_parameters(
                    trend_weight=0.70,
                    product_quality_weight=0.20,
                    liquidity_weight=0.10,
                    trend_windows=[60, 120, 200],
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_trend_product_quality",
                feature_allowlist=(
                    "momentum_60",
                    "momentum_120",
                    "sma_200",
                    "discount_premium",
                    "tracking_error_20",
                    "account_liquidity_percentile",
                ),
                **common,
            ),
            ClassicalModelSpec(
                spec_id="Q_TRACK_02",
                feature_profile="qdii_slow_trend_product_quality_v1",
                parameters=_parameters(
                    trend_weight=0.70,
                    product_quality_weight=0.20,
                    liquidity_weight=0.10,
                    trend_windows=[120, 200],
                    max_risky_exposure=0.85,
                    rebalance_trading_days=5,
                ),
                hypothesis_id="qdii_slow_trend_product_quality",
                feature_allowlist=(
                    "momentum_120",
                    "sma_200",
                    "discount_premium",
                    "tracking_error_20",
                    "account_liquidity_percentile",
                ),
                **common,
            ),
        )
    raise ValueError(f"transparent_strategy_market:{normalized}")


def incremental_residual_specs(
    market: str,
    account_scope: str,
    *,
    baseline_spec_id: str,
) -> tuple[ClassicalModelSpec, ...]:
    """Return the only two ML residual trials allowed after a baseline survives."""

    normalized = str(market).strip()
    if normalized not in {"a_share", "cn_qdii_etf"}:
        raise ValueError(f"incremental_residual_market:{normalized}")
    horizon = 20 if normalized == "a_share" else 10
    cadence = "monthly" if normalized == "a_share" else "weekly"
    alpha = 25.0 if normalized == "a_share" else 35.0
    common = {
        "market": normalized,
        "account_scope": str(account_scope).strip(),
        "horizon": horizon,
        "feature_profile": f"{normalized}_fixed_residual_v1",
        "hypothesis_id": "bounded_incremental_residual",
        "feature_allowlist": (
            "momentum_20",
            "momentum_60",
            "momentum_120",
            "account_low_volatility_percentile",
            "account_liquidity_percentile",
            "account_quality_percentile",
        ),
        "rebalance_frequency": cadence,
        "ranking_target": "baseline_residual_v1",
        "feature_selection_mode": "fixed_profile_v1",
        "random_state": 20260814,
    }
    prefix = "A" if normalized == "a_share" else "Q"
    return (
        ClassicalModelSpec(
            spec_id=f"{prefix}_{baseline_spec_id}_RIDGE_RESIDUAL_05",
            estimator="ridge",
            parameters=_parameters(
                alpha=alpha,
                baseline_spec_id=baseline_spec_id,
                residual_tilt_weight=0.05,
            ),
            **common,
        ),
        ClassicalModelSpec(
            spec_id=f"{prefix}_{baseline_spec_id}_HGBR_RESIDUAL_05",
            estimator="hgbr",
            parameters=_parameters(
                baseline_spec_id=baseline_spec_id,
                learning_rate=0.03,
                max_iter=100,
                max_leaf_nodes=7,
                min_samples_leaf=100,
                l2_regularization=5.0,
                residual_tilt_weight=0.05,
            ),
            **common,
        ),
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
            spec_id="h20_momentum_baseline_residual_ridge_v1",
            market="a_share",
            account_scope=str(account_scope),
            horizon=20,
            estimator="ridge",
            feature_profile="a_share_h20_momentum_baseline_residual_v1",
            parameters=_parameters(
                alpha=25.0,
                ranking_linear_weight=1.0,
                residual_tilt_weight=0.10,
            ),
            hypothesis_id="momentum_baseline_residual",
            feature_allowlist=_A_SHARE_H20_QUALITY_LOWVOL,
            rebalance_frequency="monthly",
            ranking_target="momentum_anchor_residual_v1",
            feature_selection_mode="fixed_profile_v1",
        ),
    )


_QDII_H10_TREND_FEATURES = (
    "nav_momentum_20",
    "account_residual_momentum_20",
    "account_residual_momentum_60",
    "sma_distance_20",
    "natr_14",
    "discount_premium",
    "premium_persistence_20",
    "tracking_difference_20",
    "tracking_error_20",
    "account_low_volatility_percentile",
    "account_liquidity_percentile",
    "global_index_momentum",
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
            spec_id="h10_trend_baseline_residual_ridge_v1",
            estimator="ridge",
            parameters=_parameters(
                alpha=35.0,
                ranking_linear_weight=1.0,
                residual_tilt_weight=0.10,
            ),
            hypothesis_id="qdii_absolute_trend_baseline_residual",
            feature_allowlist=_QDII_H10_TREND_FEATURES,
            rebalance_frequency="weekly",
            ranking_target="qdii_trend_anchor_residual_v1",
            feature_selection_mode="fixed_profile_v1",
            **common,
        ),
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
            if spec.spec_id == "h10_trend_baseline_residual_ridge_v1"
        )
    raise ValueError(f"classical_mainline_market:{normalized}")


__all__ = [
    "ClassicalModelSpec",
    "a_share_h3_specs",
    "a_share_h20_specs",
    "incremental_residual_specs",
    "mainline_horizon",
    "mainline_specs",
    "qdii_h5_specs",
    "qdii_h10_specs",
    "transparent_strategy_specs",
]
