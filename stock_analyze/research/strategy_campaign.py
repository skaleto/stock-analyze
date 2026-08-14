"""Bounded, evidence-first strategy recovery campaign orchestration."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from .attribution import summarize_replay_attribution
from .campaign_report import (
    write_final_campaign_report,
    write_transparent_campaign_report,
)
from ..utils import write_text_atomic
from .account_features import build_account_feature_view, date_balanced_sample_weights
from .classical_specs import (
    incremental_residual_specs,
    transparent_strategy_specs,
)
from .trial_ledger import CampaignLedger
from .governance import evaluate_campaign_governance
from .models import (
    _bounded_cross_section_sample,
    _fit_clip_bounds,
    _impute,
    make_purged_walk_forward_splits,
    score_transparent_strategy,
)
from .portfolio_replay import replay_rule_portfolio
from .robustness import (
    classify_market_regimes,
    contribution_concentration,
    paired_block_bootstrap_probability,
    stationary_block_bootstrap_probability,
    summarize_regime_performance,
)
from .strategy_viability import evaluate_execution_viability


CAMPAIGN_THRESHOLDS: dict[str, float | int] = {
    "maximum_drawdown": 0.25,
    "minimum_target_fill_ratio": 0.95,
    "maximum_liquidity_impact_rejection_ratio": 0.10,
    "minimum_positive_walk_forward_folds": 2,
    "walk_forward_fold_count": 3,
    "minimum_deflated_sharpe_probability": 0.95,
    "maximum_probability_of_backtest_overfit": 0.50,
    "cost_stress_multiplier": 2.0,
    "bootstrap_samples": 10_000,
    "bootstrap_seed": 20260814,
}

CAMPAIGN_SCOPES: dict[str, tuple[str, ...]] = {
    "a_share": ("hs300", "zz500"),
    "cn_qdii_etf": ("hk_exposure", "us_exposure"),
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _return_drawdown(values: Sequence[float]) -> float:
    clean = pd.to_numeric(pd.Series(values), errors="coerce").fillna(0.0).clip(
        lower=-0.99
    )
    if clean.empty:
        return 0.0
    nav = np.cumprod(1.0 + clean.to_numpy(dtype=float))
    return abs(float(np.min(nav / np.maximum.accumulate(nav) - 1.0)))


def _security_contributions(periods: pd.DataFrame) -> dict[str, float]:
    totals: dict[str, float] = {}
    if "security_selection_contributions" not in periods.columns:
        return totals
    for value in periods["security_selection_contributions"]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if not isinstance(value, Mapping):
            continue
        for code, contribution in value.items():
            totals[str(code)] = totals.get(str(code), 0.0) + float(contribution)
    return totals


def evaluate_transparent_spec(
    dataset: pd.DataFrame,
    *,
    spec: Any,
    portfolio_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one predeclared rule on three purged, executable OOS folds."""

    required = {
        "trade_date",
        "label_end_date",
        "entry_date",
        "entry_price",
        "benchmark_entry_price",
        "code",
    }
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(
            f"strategy_campaign_dataset_missing:{','.join(sorted(missing))}"
        )
    data = dataset.copy().sort_values(["trade_date", "code"], kind="stable").reset_index(
        drop=True
    )
    data["trade_date"] = data["trade_date"].astype(str)
    data["label_end_date"] = data["label_end_date"].astype(str)
    splits = make_purged_walk_forward_splits(
        data,
        n_splits=3,
        embargo=int(spec.horizon),
    )
    if len(splits) != 3:
        raise ValueError("strategy_campaign_walk_forward_insufficient")
    validation_parts: list[pd.DataFrame] = []
    point_in_time_audits: list[bool] = []
    for fold, split in enumerate(splits):
        train = data.loc[split.train_indices]
        validation = data.loc[split.validation_indices].copy()
        point_in_time_audits.append(
            bool(
                not train.empty
                and not validation.empty
                and str(train["label_end_date"].max())
                < str(validation["trade_date"].min())
            )
        )
        scored = score_transparent_strategy(validation, spec)
        scored["fold"] = fold
        validation_parts.append(scored)
    evaluation = pd.concat(validation_parts, ignore_index=True).sort_values(
        ["trade_date", "code"], kind="stable"
    )
    contract = {**dict(portfolio_contract), "rebalance_frequency": spec.rebalance_frequency}
    replay = replay_rule_portfolio(evaluation, contract=contract)
    stress_contract = {
        **contract,
        "execution_cost_multiplier": float(
            CAMPAIGN_THRESHOLDS["cost_stress_multiplier"]
        ),
    }
    stress = replay_rule_portfolio(evaluation, contract=stress_contract)
    fold_rows: list[dict[str, Any]] = []
    for fold in range(3):
        fold_evaluation = evaluation.loc[evaluation["fold"].eq(fold)].copy()
        fold_replay = replay_rule_portfolio(fold_evaluation, contract=contract)
        fold_stress = replay_rule_portfolio(
            fold_evaluation,
            contract=stress_contract,
        )
        fold_rows.append({
            "fold": fold,
            "start": str(fold_evaluation["trade_date"].min()),
            "end": str(fold_evaluation["trade_date"].max()),
            "trade_count": int(fold_replay.metrics["trade_count"]),
            "net_return": float(fold_replay.metrics["net_return"]),
            "net_excess_return": float(fold_replay.metrics["net_excess_return"]),
            "max_drawdown": float(fold_replay.metrics["max_drawdown"]),
            "cost_stress_net_excess_return": float(
                fold_stress.metrics["net_excess_return"]
            ),
        })

    metrics = dict(replay.metrics)
    stress_metrics = dict(stress.metrics)
    benchmark_drawdown = _return_drawdown(metrics["benchmark_period_returns"])
    execution_gate = evaluate_execution_viability(metrics, stress_metrics)
    positive_folds = sum(
        float(item["net_excess_return"]) > 0.0 for item in fold_rows
    )
    gate_zero_checks = {
        "point_in_time_audit": bool(all(point_in_time_audits)),
        "walk_forward_splits": len(fold_rows) == 3,
        "all_folds_traded": all(int(item["trade_count"]) > 0 for item in fold_rows),
        "attribution_reconciled": str(metrics.get("attribution_status")) == "reconciled",
    }
    gate_one_checks = {
        "net_return": float(metrics["net_return"]) > 0.0,
        "net_excess_return": float(metrics["net_excess_return"]) > 0.0,
        "positive_walk_forward_folds": positive_folds >= 2,
        "maximum_drawdown": float(metrics["max_drawdown"]) <= 0.25,
        "drawdown_vs_benchmark": (
            float(metrics["max_drawdown"]) - benchmark_drawdown <= 0.02
        ),
        **dict(execution_gate["checks"]),
    }
    active_returns = replay.periods.sort_values("signal_date")["active_return"]
    bootstrap_probability = stationary_block_bootstrap_probability(
        active_returns,
        block_length=int(spec.horizon),
        samples=int(CAMPAIGN_THRESHOLDS["bootstrap_samples"]),
        seed=int(CAMPAIGN_THRESHOLDS["bootstrap_seed"]),
    )
    years = replay.periods["signal_date"].astype(str).str[:4]
    yearly_contributions = {
        str(year): float(group["active_return"].sum())
        for year, group in replay.periods.assign(_year=years).groupby("_year")
    }
    year_concentration = contribution_concentration(yearly_contributions)
    security_contributions = _security_contributions(replay.periods)
    security_concentration = contribution_concentration(security_contributions)
    regimes: dict[str, Any]
    if {"benchmark_close", "benchmark_sma_200", "benchmark_momentum_60"}.issubset(
        evaluation.columns
    ):
        benchmark = (
            evaluation.loc[:, [
                "trade_date", "benchmark_close", "benchmark_sma_200",
                "benchmark_momentum_60",
            ]]
            .drop_duplicates("trade_date")
            .rename(columns={"trade_date": "date"})
        )
        benchmark["regime"] = classify_market_regimes(benchmark)
        regime_frame = replay.periods.loc[:, ["signal_date", "active_return"]].merge(
            benchmark.loc[:, ["date", "regime"]],
            left_on="signal_date",
            right_on="date",
            how="left",
        )
        regimes = summarize_regime_performance(regime_frame)
    else:
        regimes = {"status": "unavailable"}
    return _json_safe({
        "trial_id": f"{spec.market}:{spec.account_scope}:{spec.spec_id}",
        "spec_id": spec.spec_id,
        "spec_hash": spec.spec_hash,
        "market": spec.market,
        "account_scope": spec.account_scope,
        "horizon": int(spec.horizon),
        "walk_forward_splits": len(fold_rows),
        "point_in_time_audit": bool(all(point_in_time_audits)),
        "oos_start": str(evaluation["trade_date"].min()),
        "oos_end": str(evaluation["trade_date"].max()),
        "oos_predictions": int(len(evaluation)),
        "folds": fold_rows,
        "metrics": metrics,
        "cost_stress": stress_metrics,
        "gate_zero": {
            "passed": all(gate_zero_checks.values()),
            "checks": gate_zero_checks,
            "reasons": [name for name, passed in gate_zero_checks.items() if not passed],
        },
        "gate_one_pre_family": {
            "passed": all(gate_one_checks.values()),
            "checks": gate_one_checks,
            "reasons": [name for name, passed in gate_one_checks.items() if not passed],
        },
        "bootstrap_probability": bootstrap_probability,
        "year_concentration": year_concentration,
        "security_concentration": security_concentration,
        "regimes": regimes,
        "attribution": summarize_replay_attribution(replay.periods),
        "oos_returns": [
            {"date": str(row.signal_date), "return": float(row.active_return)}
            for row in replay.periods.sort_values("signal_date").itertuples()
        ],
    })


def _strategy_family(spec_id: str) -> str:
    value = str(spec_id)
    return value.rsplit("_", 1)[0] if value.endswith(("_01", "_02")) else value


def resolve_transparent_scope(
    trials: Sequence[Mapping[str, Any]],
    *,
    legacy_trials: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Apply the frozen family, economic, and robustness gates to one scope."""

    rows = [dict(item) for item in trials]
    if len(rows) != 6:
        raise ValueError("campaign_scope_transparent_trial_count")
    markets = {str(item.get("market") or "") for item in rows}
    scopes = {str(item.get("account_scope") or "") for item in rows}
    if len(markets) != 1 or len(scopes) != 1:
        raise ValueError("campaign_scope_transparent_mixed")
    if any(not bool((item.get("gate_zero") or {}).get("passed")) for item in rows):
        return {
            "market": next(iter(markets)),
            "account_scope": next(iter(scopes)),
            "status": "insufficient_data",
            "selected_spec_id": None,
            "reasons": sorted({
                str(reason)
                for item in rows
                for reason in (item.get("gate_zero") or {}).get("reasons") or []
            }),
            "trials": rows,
            "legacy_trial_count": int(len(legacy_trials)),
        }

    family_positive: dict[str, bool] = {}
    for family in sorted({_strategy_family(str(item["spec_id"])) for item in rows}):
        family_rows = [
            item for item in rows
            if _strategy_family(str(item["spec_id"])) == family
        ]
        family_positive[family] = bool(
            len(family_rows) == 2
            and all(float((item.get("metrics") or {}).get("net_excess_return") or 0.0) > 0.0 for item in family_rows)
        )

    resolved: list[dict[str, Any]] = []
    for item in rows:
        governance = evaluate_campaign_governance(
            rows,
            selected_trial_id=str(item["trial_id"]),
            legacy_trials=list(legacy_trials),
        )
        regimes = item.get("regimes") or {}
        regime_drawdown_passed = bool(
            isinstance(regimes, Mapping)
            and str(regimes.get("status") or "") != "unavailable"
            and all(
                float((regimes.get(name) or {}).get("max_drawdown") or 0.0)
                <= float(CAMPAIGN_THRESHOLDS["maximum_drawdown"])
                for name in ("bull", "range", "down")
            )
        )
        checks = {
            "family_variants_positive": family_positive[
                _strategy_family(str(item["spec_id"]))
            ],
            "deflated_sharpe_probability": float(
                governance["deflated_sharpe_probability"]
            ) >= float(CAMPAIGN_THRESHOLDS["minimum_deflated_sharpe_probability"]),
            "probability_of_backtest_overfit": float(
                governance["probability_of_backtest_overfit"]
            ) <= float(CAMPAIGN_THRESHOLDS["maximum_probability_of_backtest_overfit"]),
            "stationary_bootstrap": float(item.get("bootstrap_probability") or 0.0) >= 0.95,
            "year_concentration": bool((item.get("year_concentration") or {}).get("passed")),
            "security_concentration": bool((item.get("security_concentration") or {}).get("passed")),
            "regime_drawdown": regime_drawdown_passed,
        }
        gate_two = {
            "passed": bool(all(checks.values())),
            "checks": checks,
            "reasons": [name for name, passed in checks.items() if not passed],
            "governance": governance,
        }
        resolved.append({
            **item,
            "gate_two": gate_two,
            "passed_transparent_gates": bool(
                (item.get("gate_one_pre_family") or {}).get("passed")
                and gate_two["passed"]
            ),
        })

    survivors = [item for item in resolved if item["passed_transparent_gates"]]
    selected = max(
        survivors,
        key=lambda item: (
            float((item.get("metrics") or {}).get("net_excess_return") or 0.0),
            str(item.get("spec_id") or ""),
        ),
        default=None,
    )
    return {
        "market": next(iter(markets)),
        "account_scope": next(iter(scopes)),
        "status": "transparent_survivor" if selected else "falsified",
        "selected_spec_id": str(selected["spec_id"]) if selected else None,
        "reasons": [] if selected else ["no_transparent_candidate_passed_gates_1_2"],
        "trials": resolved,
        "legacy_trial_count": int(len(legacy_trials)),
    }


def incremental_specs_for_scope(
    scope_result: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Return the predeclared ML budget only when a transparent rule survives."""

    if (
        str(scope_result.get("status") or "") != "transparent_survivor"
        or not str(scope_result.get("selected_spec_id") or "")
    ):
        return ()
    return incremental_residual_specs(
        str(scope_result["market"]),
        str(scope_result["account_scope"]),
        baseline_spec_id="TRANSPARENT_SURVIVOR",
    )


def _dated_returns(result: Mapping[str, Any]) -> pd.Series:
    values = {
        str(item.get("date") or ""): float(item.get("return"))
        for item in result.get("oos_returns") or []
        if str(item.get("date") or "")
    }
    return pd.Series(values, dtype=float).sort_index()


def evaluate_incremental_gate(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    horizon: int,
) -> dict[str, Any]:
    """Judge one fixed residual model only on its paired executable increment."""

    base_metrics = baseline.get("metrics") or {}
    candidate_metrics = candidate.get("metrics") or {}
    base_excess = float(base_metrics.get("net_excess_return") or 0.0)
    candidate_excess = float(candidate_metrics.get("net_excess_return") or 0.0)
    base_folds = {
        int(item.get("fold") or 0): float(item.get("net_excess_return") or 0.0)
        for item in baseline.get("folds") or []
    }
    candidate_folds = {
        int(item.get("fold") or 0): float(item.get("net_excess_return") or 0.0)
        for item in candidate.get("folds") or []
    }
    common_folds = sorted(set(base_folds).intersection(candidate_folds))
    fold_deltas = [
        {
            "fold": fold,
            "baseline_net_excess_return": base_folds[fold],
            "candidate_net_excess_return": candidate_folds[fold],
            "delta": candidate_folds[fold] - base_folds[fold],
        }
        for fold in common_folds
    ]
    base_returns = _dated_returns(baseline)
    candidate_returns = _dated_returns(candidate)
    aligned = pd.concat(
        [candidate_returns.rename("candidate"), base_returns.rename("baseline")],
        axis=1,
        join="inner",
    ).dropna()
    paired_probability = (
        paired_block_bootstrap_probability(
            aligned["candidate"],
            aligned["baseline"],
            block_length=int(horizon),
            samples=int(CAMPAIGN_THRESHOLDS["bootstrap_samples"]),
            seed=int(CAMPAIGN_THRESHOLDS["bootstrap_seed"]),
        )
        if len(aligned) and len(aligned) == len(base_returns) == len(candidate_returns)
        else 0.0
    )
    base_turnover = float(base_metrics.get("annual_turnover") or 0.0)
    candidate_turnover = float(candidate_metrics.get("annual_turnover") or 0.0)
    turnover_passed = (
        candidate_turnover <= base_turnover * 1.25
        if base_turnover > 0.0
        else candidate_turnover <= 0.0
    )
    checks = {
        "positive_net_increment": candidate_excess - base_excess > 0.0,
        "positive_fold_majority": (
            len(fold_deltas) == 3
            and sum(float(item["delta"]) > 0.0 for item in fold_deltas) >= 2
        ),
        "paired_block_bootstrap": paired_probability >= 0.95,
        "drawdown_degradation": (
            float(candidate_metrics.get("max_drawdown") or 0.0)
            - float(base_metrics.get("max_drawdown") or 0.0)
            <= 0.02
        ),
        "turnover_increase": turnover_passed,
        "double_cost_increment": (
            float((candidate.get("cost_stress") or {}).get("net_excess_return") or 0.0)
            > float((baseline.get("cost_stress") or {}).get("net_excess_return") or 0.0)
        ),
        "feature_direction_stability": bool(
            (candidate.get("feature_direction_stability") or {}).get("passed")
        ),
    }
    return _json_safe({
        "passed": bool(all(checks.values())),
        "checks": checks,
        "reasons": [name for name, passed in checks.items() if not passed],
        "net_excess_return_delta": candidate_excess - base_excess,
        "max_drawdown_delta": (
            float(candidate_metrics.get("max_drawdown") or 0.0)
            - float(base_metrics.get("max_drawdown") or 0.0)
        ),
        "annual_turnover_delta": candidate_turnover - base_turnover,
        "paired_bootstrap_probability": paired_probability,
        "fold_deltas": fold_deltas,
    })


def _feature_direction_stability(
    fold_directions: Sequence[Mapping[str, float]],
) -> dict[str, Any]:
    features = sorted({str(key) for row in fold_directions for key in row})
    scores = {
        feature: float(np.mean([
            abs(float(row.get(feature, 0.0))) for row in fold_directions
        ]))
        for feature in features
    }
    major = sorted(features, key=lambda feature: (-scores[feature], feature))[:3]
    details: dict[str, Any] = {}
    for feature in major:
        signs = [
            int(np.sign(float(row.get(feature, 0.0))))
            for row in fold_directions
            if abs(float(row.get(feature, 0.0))) > 1e-12
        ]
        positive = signs.count(1)
        negative = signs.count(-1)
        details[feature] = {
            "consistent_folds": max(positive, negative),
            "observed_folds": len(signs),
            "mean_absolute_direction": scores[feature],
        }
    passed = bool(
        major
        and all(int(details[feature]["consistent_folds"]) >= 2 for feature in major)
    )
    return {
        "passed": passed,
        "major_features": major,
        "features": details,
    }


def evaluate_incremental_residual(
    dataset: pd.DataFrame,
    *,
    baseline_spec: Any,
    incremental_spec: Any,
    portfolio_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one frozen residual tilt against a surviving transparent rule."""

    if incremental_spec.estimator not in {"ridge", "hgbr"}:
        raise ValueError(f"campaign_incremental_estimator:{incremental_spec.estimator}")
    required = {
        "trade_date", "label_end_date", "entry_date", "entry_price",
        "benchmark_entry_price", "code", "excess_return",
    }
    missing = required.difference(dataset.columns)
    if missing:
        raise ValueError(
            f"campaign_incremental_dataset_missing:{','.join(sorted(missing))}"
        )
    data = dataset.copy().sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    data["trade_date"] = data["trade_date"].astype(str)
    data["label_end_date"] = data["label_end_date"].astype(str)
    features = tuple(
        column
        for column in incremental_spec.feature_allowlist
        if column in data.columns
        and pd.to_numeric(data[column], errors="coerce").notna().mean() >= 0.70
        and pd.to_numeric(data[column], errors="coerce").nunique(dropna=True) > 1
    )
    if not features:
        raise ValueError("campaign_incremental_features_missing")
    parameters = incremental_spec.parameter_map
    tilt_weight = float(parameters.get("residual_tilt_weight") or 0.05)
    if abs(tilt_weight - 0.05) > 1e-12:
        raise ValueError("campaign_incremental_tilt_not_frozen")
    splits = make_purged_walk_forward_splits(
        data,
        n_splits=3,
        embargo=int(incremental_spec.horizon),
    )
    if len(splits) != 3:
        raise ValueError("campaign_incremental_walk_forward_insufficient")

    validation_parts: list[pd.DataFrame] = []
    point_in_time_audits: list[bool] = []
    fold_directions: list[dict[str, float]] = []
    for fold, split in enumerate(splits):
        train = data.loc[split.train_indices].copy()
        validation = data.loc[split.validation_indices].copy()
        train_scored = score_transparent_strategy(train, baseline_spec)
        validation_scored = score_transparent_strategy(validation, baseline_spec)
        train_scored["_residual_target"] = (
            pd.to_numeric(train_scored["excess_return"], errors="coerce")
            .groupby(train_scored["trade_date"], sort=False)
            .rank(pct=True, method="average")
            - pd.to_numeric(train_scored["score"], errors="coerce")
        )
        train_scored = train_scored.loc[train_scored["_residual_target"].notna()].copy()
        fit_train = _bounded_cross_section_sample(
            train_scored,
            max_rows=100_000,
            random_state=int(incremental_spec.random_state) + fold,
        )
        if len(fit_train) < 100:
            raise ValueError("campaign_incremental_training_support")
        clip_bounds = _fit_clip_bounds(fit_train, features)
        train_x, imputation = _impute(
            fit_train,
            features,
            clip_bounds=clip_bounds,
        )
        validation_x, _ = _impute(
            validation_scored,
            features,
            values=imputation,
            clip_bounds=clip_bounds,
        )
        weights = date_balanced_sample_weights(fit_train).to_numpy(dtype=float)
        target = pd.to_numeric(
            fit_train["_residual_target"], errors="coerce"
        ).to_numpy(dtype=float)
        if incremental_spec.estimator == "ridge":
            scaler = StandardScaler().fit(train_x, sample_weight=weights)
            model = Ridge(alpha=float(parameters["alpha"]))
            model.fit(scaler.transform(train_x), target, sample_weight=weights)
            raw_prediction = np.asarray(
                model.predict(scaler.transform(validation_x)), dtype=float
            )
            directions = {
                feature: float(model.coef_[index])
                for index, feature in enumerate(features)
            }
        else:
            model = HistGradientBoostingRegressor(
                learning_rate=float(parameters["learning_rate"]),
                max_iter=int(parameters["max_iter"]),
                max_leaf_nodes=int(parameters["max_leaf_nodes"]),
                min_samples_leaf=int(parameters["min_samples_leaf"]),
                l2_regularization=float(parameters["l2_regularization"]),
                random_state=int(incremental_spec.random_state),
            )
            model.fit(train_x, target, sample_weight=weights)
            raw_prediction = np.asarray(model.predict(validation_x), dtype=float)
            directions = {}
            prediction_series = pd.Series(raw_prediction, index=validation_scored.index)
            for feature in features:
                correlation = pd.to_numeric(
                    validation_scored[feature], errors="coerce"
                ).corr(prediction_series, method="spearman")
                directions[feature] = float(correlation) if pd.notna(correlation) else 0.0
        residual_percentile = pd.Series(
            raw_prediction,
            index=validation_scored.index,
        ).groupby(validation_scored["trade_date"], sort=False).rank(
            pct=True,
            method="average",
        )
        validation_scored["baseline_score"] = pd.to_numeric(
            validation_scored["score"], errors="coerce"
        )
        validation_scored["residual_score"] = residual_percentile
        validation_scored["score"] = (
            (1.0 - tilt_weight) * validation_scored["baseline_score"]
            + tilt_weight * validation_scored["residual_score"]
        )
        validation_scored["fold"] = fold
        validation_parts.append(validation_scored)
        fold_directions.append(directions)
        point_in_time_audits.append(
            bool(
                str(fit_train["label_end_date"].max())
                < str(validation_scored["trade_date"].min())
            )
        )

    evaluation = pd.concat(validation_parts, ignore_index=True).sort_values(
        ["trade_date", "code"], kind="stable"
    )
    contract = {
        **dict(portfolio_contract),
        "rebalance_frequency": incremental_spec.rebalance_frequency,
    }
    stress_contract = {
        **contract,
        "execution_cost_multiplier": float(CAMPAIGN_THRESHOLDS["cost_stress_multiplier"]),
    }
    replay = replay_rule_portfolio(evaluation, contract=contract)
    stress = replay_rule_portfolio(evaluation, contract=stress_contract)
    fold_rows: list[dict[str, Any]] = []
    for fold in range(3):
        part = evaluation.loc[evaluation["fold"].eq(fold)].copy()
        fold_replay = replay_rule_portfolio(part, contract=contract)
        fold_stress = replay_rule_portfolio(part, contract=stress_contract)
        fold_rows.append({
            "fold": fold,
            "start": str(part["trade_date"].min()),
            "end": str(part["trade_date"].max()),
            "net_return": float(fold_replay.metrics["net_return"]),
            "net_excess_return": float(fold_replay.metrics["net_excess_return"]),
            "max_drawdown": float(fold_replay.metrics["max_drawdown"]),
            "cost_stress_net_excess_return": float(
                fold_stress.metrics["net_excess_return"]
            ),
        })
    return _json_safe({
        "trial_id": (
            f"{incremental_spec.market}:{incremental_spec.account_scope}:"
            f"{incremental_spec.spec_id}"
        ),
        "spec_id": incremental_spec.spec_id,
        "spec_hash": incremental_spec.spec_hash,
        "market": incremental_spec.market,
        "account_scope": incremental_spec.account_scope,
        "estimator": incremental_spec.estimator,
        "horizon": int(incremental_spec.horizon),
        "bound_baseline_spec_id": baseline_spec.spec_id,
        "residual_tilt_weight": tilt_weight,
        "selected_features": list(features),
        "walk_forward_splits": 3,
        "point_in_time_audit": bool(all(point_in_time_audits)),
        "folds": fold_rows,
        "metrics": dict(replay.metrics),
        "cost_stress": dict(stress.metrics),
        "feature_direction_stability": _feature_direction_stability(fold_directions),
        "attribution": summarize_replay_attribution(replay.periods),
        "oos_returns": [
            {"date": str(row.signal_date), "return": float(row.active_return)}
            for row in replay.periods.sort_values("signal_date").itertuples()
        ],
    })


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_campaign_inputs(
    input_manifests: Sequence[Path],
) -> dict[str, dict[str, Any]]:
    """Verify immutable training bundles before any strategy result is read."""

    loaded: dict[str, dict[str, Any]] = {}
    for raw_path in input_manifests:
        manifest_path = Path(raw_path).resolve()
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"campaign_input_manifest_invalid:{manifest_path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"campaign_input_manifest_invalid:{manifest_path}")
        market = str(value.get("market") or "").strip()
        if market not in CAMPAIGN_SCOPES:
            raise ValueError(f"campaign_input_market_invalid:{market}")
        if market in loaded:
            raise ValueError(f"campaign_input_market_duplicate:{market}")
        if str(value.get("kind") or "") != "research_training_input":
            raise ValueError(f"campaign_input_kind_invalid:{market}")
        if value.get("read_only_input") is not True:
            raise ValueError(f"campaign_input_not_read_only:{market}")
        payload_root = manifest_path.parent / "payload"
        for entry in value.get("files") or []:
            relative = Path(str(entry.get("path") or ""))
            if not str(relative) or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"campaign_input_path_invalid:{relative}")
            payload_path = payload_root / relative
            if not payload_path.is_file():
                raise ValueError(f"campaign_input_file_missing:{relative}")
            expected_size = int(entry.get("size") or -1)
            if payload_path.stat().st_size != expected_size:
                raise ValueError(f"campaign_input_size_mismatch:{relative}")
            if _sha256(payload_path) != str(entry.get("sha256") or ""):
                raise ValueError(f"campaign_input_hash_mismatch:{relative}")
        loaded[market] = {
            **value,
            "_manifest_path": str(manifest_path),
            "_payload_root": str(payload_root),
        }
    if set(loaded) != set(CAMPAIGN_SCOPES):
        missing = sorted(set(CAMPAIGN_SCOPES) - set(loaded))
        raise ValueError(f"campaign_input_markets_missing:{','.join(missing)}")
    return loaded


def _supplemental_source_paths(repo_root: Path) -> tuple[Path, ...]:
    fixed = (
        repo_root / "data/shared/backtest_cache/benchmark_daily/000300.csv",
        repo_root / "data/shared/backtest_cache/benchmark_daily/000905.csv",
        repo_root / "data/research/baseline_first/a_share/hs300/window_manifest.json",
        repo_root / "data/research/baseline_first/a_share/zz500/window_manifest.json",
        repo_root / "data/research/baseline_first/cn_qdii_etf/hk_exposure/window_manifest.json",
        repo_root / "data/research/baseline_first/cn_qdii_etf/us_exposure/window_manifest.json",
    )
    legacy_periods = tuple(sorted(
        (repo_root / "data/research/models").glob(
            "**/tournaments/**/final_periods.parquet"
        )
    ))
    return (*fixed, *legacy_periods)


def _freeze_campaign_inputs(
    *,
    repo_root: Path,
    campaign_root: Path,
    input_manifests: Sequence[Path],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    source_inputs = load_campaign_inputs(tuple(input_manifests))
    frozen_manifest_paths: list[Path] = []
    for market, manifest in sorted(source_inputs.items()):
        source_manifest = Path(str(manifest["_manifest_path"]))
        source_payload = Path(str(manifest["_payload_root"]))
        destination_root = campaign_root / "input" / market
        destination_payload = destination_root / "payload"
        destination_manifest = destination_root / "manifest.json"
        destination_root.mkdir(parents=True, exist_ok=True)
        for entry in manifest.get("files") or []:
            relative = Path(str(entry["path"]))
            source = source_payload / relative
            destination = destination_payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if (
                    destination.stat().st_size != int(entry["size"])
                    or _sha256(destination) != str(entry["sha256"])
                ):
                    raise ValueError(f"campaign_frozen_input_mismatch:{relative}")
            else:
                shutil.copy2(source, destination)
                destination.chmod(0o444)
        if destination_manifest.exists():
            if _sha256(destination_manifest) != _sha256(source_manifest):
                raise ValueError(f"campaign_frozen_manifest_mismatch:{market}")
        else:
            shutil.copy2(source_manifest, destination_manifest)
            destination_manifest.chmod(0o444)
        frozen_manifest_paths.append(destination_manifest)

    supplemental: list[dict[str, Any]] = []
    for source in _supplemental_source_paths(repo_root):
        if not source.is_file():
            raise FileNotFoundError(f"campaign_supplemental_missing:{source}")
        relative = source.relative_to(repo_root)
        destination = campaign_root / "input" / "supplemental" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = _sha256(source)
        size = source.stat().st_size
        if destination.exists():
            if destination.stat().st_size != size or _sha256(destination) != digest:
                raise ValueError(f"campaign_supplemental_mismatch:{relative}")
        else:
            shutil.copy2(source, destination)
            destination.chmod(0o444)
        supplemental.append({
            "path": str(relative),
            "sha256": digest,
            "size": int(size),
            "frozen_path": str(destination),
        })
    return load_campaign_inputs(tuple(frozen_manifest_paths)), supplemental


def _payload_file(
    manifest: Mapping[str, Any],
    *,
    prefix: str,
    suffix: str,
) -> Path:
    matches = [
        Path(str(manifest["_payload_root"])) / str(item["path"])
        for item in manifest.get("files") or []
        if str(item.get("path") or "").startswith(prefix)
        and str(item.get("path") or "").endswith(suffix)
    ]
    if len(matches) != 1:
        raise ValueError(f"campaign_payload_file_ambiguous:{prefix}:{suffix}")
    return matches[0]


def _supplemental_path(campaign_root: Path, relative: str) -> Path:
    path = campaign_root / "input" / "supplemental" / relative
    if not path.is_file():
        raise FileNotFoundError(f"campaign_supplemental_frozen_missing:{relative}")
    return path


def _load_portfolio_contract(
    manifest: Mapping[str, Any],
    *,
    market: str,
    account_scope: str,
) -> dict[str, Any]:
    config_path = _payload_file(
        manifest,
        prefix="configs/competition_",
        suffix=".yaml",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    accounts = [
        dict(account)
        for account in config.get("accounts") or []
        if str(account.get("scope") or account.get("id") or "") == account_scope
    ]
    if len(accounts) != 1:
        raise ValueError(f"campaign_account_contract_missing:{market}:{account_scope}")
    return {
        "accounts": accounts,
        "trading": dict(config.get("trading") or {}),
        "performance": dict(config.get("performance") or {
            "risk_free_rate": 0.02,
            "trading_days_per_year": 252,
        }),
        "schedule": dict(config.get("schedule") or {}),
        "rule_execution_policy": {
            "version": "campaign-transparent-v1",
            "rank_buffer_pct": 0.20 if market == "a_share" else 0.40,
            "minimum_target_change": 0.0,
            "partial_adjustment_rate": 1.0,
            "max_daily_turnover": 1.0,
            "max_industry_weight": 1.0,
            "max_holding_days": 0,
        },
    }


def _load_benchmark_frame(
    campaign_root: Path,
    *,
    code: str,
) -> pd.DataFrame:
    path = _supplemental_path(
        campaign_root,
        f"data/shared/backtest_cache/benchmark_daily/{code}.csv",
    )
    frame = pd.read_csv(
        path,
        dtype={"trade_date": str, "ts_code": str, "code": str},
    )
    frame["trade_date"] = frame["trade_date"].astype(str)
    frame["benchmark_close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.sort_values("trade_date", kind="stable")
    frame["benchmark_sma_200"] = frame["benchmark_close"].rolling(
        200, min_periods=200
    ).mean()
    frame["benchmark_momentum_60"] = frame["benchmark_close"].pct_change(
        60, fill_method=None
    )
    return frame.loc[:, [
        "trade_date", "benchmark_close", "benchmark_sma_200",
        "benchmark_momentum_60",
    ]]


def _window_payload(
    campaign_root: Path,
    *,
    market: str,
    account_scope: str,
) -> dict[str, Any]:
    path = _supplemental_path(
        campaign_root,
        f"data/research/baseline_first/{market}/{account_scope}/window_manifest.json",
    )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        payload = dict(state["payload"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("campaign_window_manifest_invalid") from exc
    if (
        str(payload.get("market") or "") != market
        or str(payload.get("account_scope") or "") != account_scope
    ):
        raise ValueError("campaign_window_manifest_scope_mismatch")
    return payload


def _load_scope_dataset(
    campaign_root: Path,
    manifest: Mapping[str, Any],
    *,
    market: str,
    account_scope: str,
    horizon: int,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    snapshot = str(manifest.get("snapshot_date") or "")
    features_path = _payload_file(
        manifest,
        prefix=f"data/research/features/{market}/",
        suffix=f"{snapshot}.parquet",
    )
    labels_path = _payload_file(
        manifest,
        prefix=f"data/research/labels/{market}/",
        suffix=f"{snapshot}.parquet",
    )
    features = pd.read_parquet(features_path)
    labels = pd.read_parquet(labels_path)
    for frame in (features, labels):
        frame["code"] = frame["code"].astype("string").str.zfill(6)
        frame["trade_date"] = frame["trade_date"].astype("string")
        for column in ("account_id", "research_scope", "label_end_date", "entry_date"):
            if column in frame.columns:
                frame[column] = frame[column].astype("string")
    scope_column = "research_scope" if "research_scope" in features else "account_id"
    label_scope = "research_scope" if "research_scope" in labels else "account_id"
    features = features.loc[features[scope_column].eq(account_scope)].copy()
    labels = labels.loc[
        labels[label_scope].eq(account_scope)
        & pd.to_numeric(labels["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    if features.empty or labels.empty:
        raise ValueError(f"campaign_scope_data_missing:{market}:{account_scope}")
    if set(labels["label_contract_version"].dropna().astype(str)) != {"next-open-v2"}:
        raise ValueError("campaign_label_contract_invalid")
    if not labels["unbiased_universe"].fillna(False).astype(bool).all():
        raise ValueError("campaign_universe_not_point_in_time")
    features = build_account_feature_view(features, account_scope=account_scope)
    join_columns = ["code", "trade_date"]
    if "account_id" in features and "account_id" in labels:
        join_columns.append("account_id")
    dataset = features.merge(
        labels,
        on=join_columns,
        how="inner",
        suffixes=("", "_label"),
        validate="one_to_one",
    )
    window = _window_payload(
        campaign_root,
        market=market,
        account_scope=account_scope,
    )
    start = str(window["development_start"])
    end = str(window["final_end"])
    dataset = dataset.loc[
        dataset["trade_date"].astype(str).between(start, end)
    ].copy()
    if dataset.empty:
        raise ValueError("campaign_consumed_window_empty")
    if not (
        dataset["entry_date"].astype(str).gt(dataset["trade_date"].astype(str)).all()
        and dataset["label_end_date"].astype(str).gt(dataset["trade_date"].astype(str)).all()
    ):
        raise ValueError("campaign_point_in_time_dates_invalid")
    contract = _load_portfolio_contract(
        manifest,
        market=market,
        account_scope=account_scope,
    )
    if market == "a_share":
        benchmark_code = str(contract["accounts"][0]["benchmark"])[:6]
        dataset = dataset.merge(
            _load_benchmark_frame(campaign_root, code=benchmark_code),
            on="trade_date",
            how="left",
            validate="many_to_one",
        )
    else:
        benchmark_code = str(contract["accounts"][0]["benchmark"])[:6]
        benchmark = (
            features.loc[features["code"].eq(benchmark_code), [
                "trade_date", "close", "sma_200", "momentum_60",
            ]]
            .rename(columns={
                "close": "benchmark_close",
                "sma_200": "benchmark_sma_200",
                "momentum_60": "benchmark_momentum_60",
            })
            .drop_duplicates("trade_date")
        )
        dataset = dataset.merge(
            benchmark,
            on="trade_date",
            how="left",
            validate="many_to_one",
        )
    metadata = {
        "snapshot_date": snapshot,
        "rows": int(len(dataset)),
        "dates": int(dataset["trade_date"].nunique()),
        "codes": int(dataset["code"].nunique()),
        "start": str(dataset["trade_date"].min()),
        "end": str(dataset["trade_date"].max()),
        "window_manifest": window,
        "label_contract_version": "next-open-v2",
        "point_in_time_audit": True,
    }
    return dataset, contract, metadata


def _source_commit(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _campaign_manifest_payload(
    *,
    repo_root: Path,
    campaign_id: str,
    as_of: str,
    inputs: Mapping[str, Mapping[str, Any]],
    supplemental_inputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    transparent = [
        spec.as_ledger_spec()
        for market, scopes in CAMPAIGN_SCOPES.items()
        for scope in scopes
        for spec in transparent_strategy_specs(market, scope)
    ]
    incremental = [
        spec.as_ledger_spec()
        for market, scopes in CAMPAIGN_SCOPES.items()
        for scope in scopes
        for baseline_id in ("TRANSPARENT_SURVIVOR",)
        for spec in incremental_residual_specs(
            market,
            scope,
            baseline_spec_id=baseline_id,
        )
    ]
    return {
        "campaign_id": str(campaign_id),
        "as_of": str(as_of),
        "source_commit": _source_commit(repo_root),
        "simulator_version": "paper-parity-daily-v1",
        "input_fingerprints": [
            {
                "market": market,
                "snapshot_date": str(value.get("snapshot_date") or ""),
                "source_fingerprint": str(value.get("source_fingerprint") or ""),
                "manifest_path": str(value.get("_manifest_path") or ""),
            }
            for market, value in sorted(inputs.items())
        ],
        "supplemental_inputs": [
            {
                "path": str(item.get("path") or ""),
                "sha256": str(item.get("sha256") or ""),
                "size": int(item.get("size") or 0),
            }
            for item in supplemental_inputs
        ],
        "thresholds": CAMPAIGN_THRESHOLDS,
        "transparent_specs": transparent,
        "incremental_specs": incremental,
    }


def _frozen_campaign_inputs(campaign_root: Path) -> dict[str, dict[str, Any]]:
    manifests = tuple(
        campaign_root / "input" / market / "manifest.json"
        for market in sorted(CAMPAIGN_SCOPES)
    )
    return load_campaign_inputs(manifests)


def _load_comparable_legacy_trials(
    campaign_root: Path,
    *,
    market: str,
    account_scope: str,
    expected_dates: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = (
        campaign_root
        / "input"
        / "supplemental"
        / "data"
        / "research"
        / "models"
        / market
        / account_scope
    )
    expected = tuple(str(value) for value in expected_dates)
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for path in sorted(root.glob("**/tournaments/**/final_periods.parquet")):
        frame = pd.read_parquet(path, columns=["signal_date", "active_return"])
        frame["signal_date"] = frame["signal_date"].astype(str)
        frame = frame.sort_values("signal_date", kind="stable")
        dates = tuple(frame["signal_date"])
        if dates != expected or frame["signal_date"].duplicated().any():
            rejected += 1
            continue
        relative = path.relative_to(root)
        accepted.append({
            "trial_id": f"legacy:{market}:{account_scope}:{relative.parent}",
            "oos_returns": [
                {"date": str(row.signal_date), "return": float(row.active_return)}
                for row in frame.itertuples()
            ],
        })
    return accepted, {
        "status": "aligned" if accepted else "none_aligned",
        "accepted": int(len(accepted)),
        "rejected_misaligned": int(rejected),
    }


def _trial_rows_for_parquet(trials: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in trials:
        metrics = item.get("metrics") or {}
        rows.append({
            "trial_id": str(item.get("trial_id") or ""),
            "spec_id": str(item.get("spec_id") or ""),
            "spec_hash": str(item.get("spec_hash") or ""),
            "market": str(item.get("market") or ""),
            "account_scope": str(item.get("account_scope") or ""),
            "stage": str(item.get("stage") or ""),
            "net_return": float(metrics.get("net_return") or 0.0),
            "benchmark_return": float(metrics.get("benchmark_return") or 0.0),
            "net_excess_return": float(metrics.get("net_excess_return") or 0.0),
            "portfolio_sharpe": float(metrics.get("portfolio_sharpe") or 0.0),
            "max_drawdown": float(metrics.get("max_drawdown") or 0.0),
            "annual_turnover": float(metrics.get("annual_turnover") or 0.0),
            "passed": bool(
                item.get("passed_transparent_gates")
                or (item.get("gate_three") or {}).get("passed")
            ),
            "result_json": json.dumps(
                _json_safe(dict(item)),
                ensure_ascii=False,
                sort_keys=True,
            ),
        })
    return pd.DataFrame(rows)


def _read_json_object(path: Path, *, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(error) from exc
    if not isinstance(value, dict):
        raise ValueError(error)
    return value


def _transparent_spec_by_id(market: str, scope: str, spec_id: str) -> Any:
    matches = [
        spec
        for spec in transparent_strategy_specs(market, scope)
        if spec.spec_id == str(spec_id)
    ]
    if len(matches) != 1:
        raise ValueError(f"campaign_transparent_spec_missing:{market}:{scope}:{spec_id}")
    return matches[0]


def run_strategy_campaign(
    *,
    repo_root: Path,
    campaign_id: str,
    as_of: str,
    stage: str,
    input_manifests: Sequence[Path],
) -> dict[str, Any]:
    """Run one immutable transparent stage and its optional bounded ML stage."""

    if not str(campaign_id or "").strip():
        raise ValueError("campaign_id_missing")
    if not str(as_of or "").strip():
        raise ValueError("campaign_as_of_missing")
    normalized_stage = str(stage).strip().replace("-", "_")
    if normalized_stage not in {"transparent", "incremental_ml"}:
        raise ValueError(f"campaign_stage_invalid:{normalized_stage}")
    root = Path(repo_root).resolve()
    campaign_root = root / "data" / "research" / "campaigns" / str(campaign_id)
    ledger = CampaignLedger(campaign_root)
    if ledger.manifest_path.exists():
        inputs = _frozen_campaign_inputs(campaign_root)
        manifest = _read_json_object(
            ledger.manifest_path,
            error="campaign_manifest_invalid",
        )
        if str(manifest.get("source_commit") or "") != _source_commit(root):
            raise ValueError("campaign_source_commit_mismatch")
    else:
        if normalized_stage != "transparent":
            raise ValueError("campaign_transparent_stage_required")
        if len(tuple(input_manifests)) != 2:
            raise ValueError("campaign_input_manifest_count")
        inputs, supplemental = _freeze_campaign_inputs(
            repo_root=root,
            campaign_root=campaign_root,
            input_manifests=tuple(input_manifests),
        )
        manifest = ledger.declare(
            _campaign_manifest_payload(
                repo_root=root,
                campaign_id=str(campaign_id),
                as_of=str(as_of),
                inputs=inputs,
                supplemental_inputs=supplemental,
            )
        )
    manifest_hash = str(manifest["manifest_hash"])

    if normalized_stage == "transparent":
        report_path = root / "reports" / "research" / f"{campaign_id}-transparent.json"
        existing_trials = {
            str(item.get("trial_id") or ""): item
            for item in ledger.read_trials()
            if str(item.get("stage") or "") == "transparent"
        }
        datasets: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]] = {}
        for market, scopes in CAMPAIGN_SCOPES.items():
            for scope in scopes:
                dataset, contract, metadata = _load_scope_dataset(
                    campaign_root,
                    inputs[market],
                    market=market,
                    account_scope=scope,
                    horizon=20 if market == "a_share" else 10,
                )
                datasets[(market, scope)] = (dataset, contract, metadata)
                for spec in transparent_strategy_specs(market, scope):
                    trial_id = f"{market}:{scope}:{spec.spec_id}"
                    if trial_id in existing_trials:
                        continue
                    trial = evaluate_transparent_spec(
                        dataset,
                        spec=spec,
                        portfolio_contract=contract,
                    )
                    ledger.record_trial(
                        manifest_hash=manifest_hash,
                        stage="transparent",
                        trial=trial,
                    )
        transparent_trials = [
            item for item in ledger.read_trials()
            if str(item.get("stage") or "") == "transparent"
        ]
        if len(transparent_trials) != 24:
            raise ValueError(
                f"campaign_transparent_incomplete:{len(transparent_trials)}/24"
            )
        scopes_payload: list[dict[str, Any]] = []
        for market, scopes in CAMPAIGN_SCOPES.items():
            for scope in scopes:
                scope_trials = [
                    dict(item) for item in transparent_trials
                    if str(item.get("market") or "") == market
                    and str(item.get("account_scope") or "") == scope
                ]
                expected_dates = [
                    str(item["date"])
                    for item in scope_trials[0].get("oos_returns") or []
                ]
                legacy, alignment = _load_comparable_legacy_trials(
                    campaign_root,
                    market=market,
                    account_scope=scope,
                    expected_dates=expected_dates,
                )
                resolved = resolve_transparent_scope(
                    scope_trials,
                    legacy_trials=legacy,
                )
                resolved["data"] = datasets[(market, scope)][2]
                resolved["legacy_alignment"] = alignment
                scopes_payload.append(resolved)
        result_path = campaign_root / "transparent-results.parquet"
        _trial_rows_for_parquet([
            trial
            for scope in scopes_payload
            for trial in scope.get("trials") or []
        ]).to_parquet(result_path, index=False)
        report = write_transparent_campaign_report(
            root,
            campaign_id=str(campaign_id),
            manifest_hash=manifest_hash,
            scopes=scopes_payload,
        )
        return {
            "status": "transparent_complete",
            "campaign_id": str(campaign_id),
            "stage": normalized_stage,
            "manifest_hash": manifest_hash,
            "manifest_path": str(ledger.manifest_path),
            "trial_count": 24,
            "result_path": str(result_path),
            "report_path": report["json_path"],
            "cached_before_run": bool(report_path.exists() and len(existing_trials) == 24),
        }

    final_path = root / "reports" / "research" / f"{campaign_id}-final.json"
    if final_path.is_file():
        payload = _read_json_object(final_path, error="campaign_final_report_invalid")
        return {
            "status": "complete",
            "campaign_id": str(campaign_id),
            "stage": normalized_stage,
            "manifest_hash": manifest_hash,
            "report_path": str(final_path),
            "scopes": payload.get("scopes") or [],
            "cached_before_run": True,
        }
    transparent_report = _read_json_object(
        root / "reports" / "research" / f"{campaign_id}-transparent.json",
        error="campaign_transparent_report_missing",
    )
    transparent_scopes = [dict(item) for item in transparent_report.get("scopes") or []]
    if len(transparent_scopes) != 4:
        raise ValueError("campaign_transparent_scope_count")
    all_ledger_trials = ledger.read_trials()
    incremental_existing = {
        str(item.get("trial_id") or ""): item
        for item in all_ledger_trials
        if str(item.get("stage") or "") == "incremental_ml"
    }
    final_scopes: list[dict[str, Any]] = []
    for scope_result in transparent_scopes:
        specs = incremental_specs_for_scope(scope_result)
        if not specs:
            final_scopes.append(scope_result)
            continue
        market = str(scope_result["market"])
        scope = str(scope_result["account_scope"])
        selected_spec_id = str(scope_result["selected_spec_id"])
        baseline_spec = _transparent_spec_by_id(market, scope, selected_spec_id)
        baseline_trials = [
            item for item in scope_result.get("trials") or []
            if str(item.get("spec_id") or "") == selected_spec_id
        ]
        if len(baseline_trials) != 1:
            raise ValueError("campaign_selected_baseline_trial_missing")
        baseline_trial = baseline_trials[0]
        dataset, contract, _ = _load_scope_dataset(
            campaign_root,
            inputs[market],
            market=market,
            account_scope=scope,
            horizon=int(baseline_spec.horizon),
        )
        scope_incremental: list[dict[str, Any]] = []
        for spec in specs:
            trial_id = f"{market}:{scope}:{spec.spec_id}"
            if trial_id in incremental_existing:
                trial = dict(incremental_existing[trial_id])
            else:
                trial = evaluate_incremental_residual(
                    dataset,
                    baseline_spec=baseline_spec,
                    incremental_spec=spec,
                    portfolio_contract=contract,
                )
                trial["gate_three"] = evaluate_incremental_gate(
                    baseline_trial,
                    trial,
                    horizon=int(spec.horizon),
                )
                ledger.record_trial(
                    manifest_hash=manifest_hash,
                    stage="incremental_ml",
                    trial=trial,
                )
            scope_incremental.append(trial)
        passing = [
            item for item in scope_incremental
            if bool((item.get("gate_three") or {}).get("passed"))
        ]
        selected_incremental = max(
            passing,
            key=lambda item: (
                float((item.get("gate_three") or {}).get("net_excess_return_delta") or 0.0),
                str(item.get("spec_id") or ""),
            ),
            default=None,
        )
        final_scopes.append({
            **scope_result,
            "status": "shadow_ready" if selected_incremental else "baseline_only",
            "selected_incremental_spec_id": (
                str(selected_incremental["spec_id"])
                if selected_incremental else None
            ),
            "incremental_trials": scope_incremental,
            "reasons": (
                [] if selected_incremental else ["ml_no_proven_increment"]
            ),
        })
    incremental_trials = [
        item for item in ledger.read_trials()
        if str(item.get("stage") or "") == "incremental_ml"
    ]
    if len(incremental_trials) > 8:
        raise ValueError("campaign_incremental_budget_exceeded")
    incremental_path = campaign_root / "incremental-results.parquet"
    _trial_rows_for_parquet(incremental_trials).to_parquet(
        incremental_path,
        index=False,
    )
    report = write_final_campaign_report(
        root,
        campaign_id=str(campaign_id),
        manifest_hash=manifest_hash,
        scopes=final_scopes,
    )
    return {
        "status": "complete",
        "campaign_id": str(campaign_id),
        "stage": normalized_stage,
        "manifest_hash": manifest_hash,
        "transparent_trial_count": 24,
        "incremental_trial_count": int(len(incremental_trials)),
        "result_path": str(incremental_path),
        "report_path": report["json_path"],
        "scopes": final_scopes,
        "cached_before_run": False,
    }


__all__ = [
    "CAMPAIGN_SCOPES",
    "CAMPAIGN_THRESHOLDS",
    "evaluate_incremental_gate",
    "evaluate_incremental_residual",
    "evaluate_transparent_spec",
    "incremental_specs_for_scope",
    "load_campaign_inputs",
    "resolve_transparent_scope",
    "run_strategy_campaign",
]
