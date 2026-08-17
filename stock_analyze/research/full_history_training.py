"""Fold-local estimators for the full-history rebuild campaign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler

from .catboost_ranker import fit_catboost_ranker


@dataclass(frozen=True)
class CandidatePrediction:
    estimator: str
    selected_features: tuple[str, ...]
    predictions: np.ndarray
    coefficients: Mapping[str, float]
    metadata: Mapping[str, Any]


def _ordered(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "code", "excess_return"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"full_history_training_columns:{','.join(missing)}")
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


def _select_features(
    train: pd.DataFrame,
    candidates: Sequence[str],
    *,
    minimum_coverage: float,
    max_features: int,
) -> tuple[str, ...]:
    eligible: list[tuple[float, str]] = []
    target = pd.to_numeric(train["excess_return"], errors="coerce")
    for column in candidates:
        if column not in train.columns:
            continue
        values = pd.to_numeric(train[column], errors="coerce")
        if float(values.notna().mean()) < float(minimum_coverage) or values.nunique(dropna=True) <= 1:
            continue
        daily: list[float] = []
        for _, indices in train.groupby("trade_date", sort=True).groups.items():
            x = values.loc[indices]
            y = target.loc[indices]
            valid = x.notna() & y.notna()
            if int(valid.sum()) >= 2 and x.loc[valid].nunique() > 1 and y.loc[valid].nunique() > 1:
                value = x.loc[valid].corr(y.loc[valid], method="spearman")
                if pd.notna(value):
                    daily.append(float(value))
        score = abs(float(np.mean(daily))) if daily else 0.0
        eligible.append((score, str(column)))
    eligible.sort(key=lambda item: (-item[0], item[1]))
    selected = tuple(column for _, column in eligible[: max(1, int(max_features))])
    if not selected:
        raise ValueError("full_history_training_features")
    return selected


def _impute(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    medians: dict[str, float] = {}
    train_values: list[np.ndarray] = []
    validation_values: list[np.ndarray] = []
    for column in columns:
        train_column = pd.to_numeric(train[column], errors="coerce")
        median = float(train_column.median())
        if not np.isfinite(median):
            raise ValueError(f"full_history_training_imputation:{column}")
        medians[column] = median
        train_values.append(train_column.fillna(median).to_numpy(dtype=float))
        validation_values.append(
            pd.to_numeric(validation[column], errors="coerce").fillna(median).to_numpy(dtype=float)
        )
    return np.column_stack(train_values), np.column_stack(validation_values), medians


def _date_weights(frame: pd.DataFrame, decay: float) -> np.ndarray:
    ordered_dates = {day: index for index, day in enumerate(sorted(frame["trade_date"].unique()))}
    latest = max(ordered_dates.values())
    raw = np.asarray([decay ** (latest - ordered_dates[day]) for day in frame["trade_date"]], dtype=float)
    counts = frame.groupby("trade_date")["code"].transform("count").to_numpy(dtype=float)
    return raw / np.maximum(counts, 1.0)


def fit_predict_candidate(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    candidate_features: Sequence[str],
    estimator: str,
    parameters: Mapping[str, Any],
    max_features: int,
    minimum_coverage: float,
    random_state: int,
    validation_labels_visible: bool = False,
) -> CandidatePrediction:
    """Fit one preregistered candidate without consulting validation coverage."""

    train_ordered = _ordered(train)
    validation_ordered = _ordered(validation)
    selected = _select_features(
        train_ordered,
        candidate_features,
        minimum_coverage=minimum_coverage,
        max_features=max_features,
    )
    train_x, validation_x, medians = _impute(train_ordered, validation_ordered, selected)
    train_y = pd.to_numeric(train_ordered["excess_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    coefficients: dict[str, float] = {}
    metadata: dict[str, Any] = {"imputation_values": medians}
    metadata["validation_labels_visible"] = bool(validation_labels_visible)

    if estimator == "elastic_net":
        weights = _date_weights(train_ordered, 1.0)
        scaler = StandardScaler().fit(train_x, sample_weight=weights)
        model = ElasticNet(
            alpha=float(parameters.get("alpha", 0.001)),
            l1_ratio=float(parameters.get("l1_ratio", 0.25)),
            max_iter=5000,
            random_state=int(random_state),
        ).fit(scaler.transform(train_x), train_y, sample_weight=weights)
        predictions = model.predict(scaler.transform(validation_x))
        coefficients = {column: float(model.coef_[index]) for index, column in enumerate(selected)}
    elif estimator == "additive":
        decay = min(max(float(parameters.get("decay", 0.995)), 0.90), 1.0)
        bound = min(max(float(parameters.get("coefficient_bound", 0.35)), 0.01), 1.0)
        weights = _date_weights(train_ordered, decay)
        centered_y = train_y - np.average(train_y, weights=weights)
        raw: list[float] = []
        for index, column in enumerate(selected):
            values = train_x[:, index]
            centered = values - np.average(values, weights=weights)
            denominator = float(np.average(centered * centered, weights=weights))
            coefficient = 0.0 if denominator <= 1e-12 else float(np.average(centered * centered_y, weights=weights) / denominator)
            raw.append(float(np.clip(coefficient, -bound, bound)))
        scale = max(sum(abs(value) for value in raw), 1.0)
        bounded = np.asarray([value / scale for value in raw], dtype=float)
        predictions = validation_x @ bounded
        coefficients = {column: float(bounded[index]) for index, column in enumerate(selected)}
    elif estimator == "lightgbm_lambdarank":
        from lightgbm import LGBMRanker

        ranked = train_ordered.groupby("trade_date")["excess_return"].rank(pct=True, method="first")
        relevance = np.minimum((ranked * 5).astype(int), 4)
        groups = train_ordered.groupby("trade_date", sort=False).size().tolist()
        model = LGBMRanker(
            objective="lambdarank",
            random_state=int(random_state),
            n_estimators=int(parameters.get("n_estimators", 300)),
            learning_rate=float(parameters.get("learning_rate", 0.03)),
            num_leaves=int(parameters.get("num_leaves", 15)),
            max_depth=int(parameters.get("max_depth", 5)),
            min_child_samples=int(parameters.get("min_child_samples", 200)),
            reg_alpha=float(parameters.get("reg_alpha", 1.0)),
            reg_lambda=float(parameters.get("reg_lambda", 8.0)),
            subsample=0.8,
            colsample_bytree=0.8,
            n_jobs=1,
            verbosity=-1,
        ).fit(train_x, relevance, group=groups)
        predictions = model.predict(validation_x)
        metadata["feature_importances"] = {
            column: float(model.feature_importances_[index])
            for index, column in enumerate(selected)
        }
    elif estimator == "catboost_ranker":
        result = fit_catboost_ranker(
            train_ordered,
            validation_ordered,
            feature_columns=selected,
            parameters=parameters,
            random_state=random_state,
            use_validation_for_eval=validation_labels_visible,
        )
        predictions = result.validation_predictions
        metadata["catboost_parameters"] = dict(result.parameters)
    else:
        raise ValueError(f"full_history_training_estimator:{estimator}")

    return CandidatePrediction(
        estimator=str(estimator),
        selected_features=selected,
        predictions=np.asarray(predictions, dtype=float),
        coefficients=coefficients,
        metadata=metadata,
    )


__all__ = ["CandidatePrediction", "fit_predict_candidate"]


def _rank_ic(frame: pd.DataFrame) -> tuple[float, float]:
    daily = []
    for _, group in frame.groupby("trade_date", sort=True):
        if len(group) < 2:
            continue
        value = pd.to_numeric(group["score"], errors="coerce").corr(
            pd.to_numeric(group["excess_return"], errors="coerce"),
            method="spearman",
        )
        if pd.notna(value):
            daily.append(float(value))
    if not daily:
        return 0.0, 0.0
    mean = float(np.mean(daily))
    std = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    return mean, (mean / std if std > 1e-12 else 0.0)


def _select_inner_parameters(
    data: pd.DataFrame,
    *,
    window: Any,
    variants: Sequence[Mapping[str, Any]],
    candidate_features: Sequence[str],
    estimator: str,
    max_features: int,
    minimum_coverage: float,
    portfolio_contract: Mapping[str, Any],
    random_state: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .portfolio_replay import replay_rule_portfolio

    evidence: list[dict[str, Any]] = []
    for variant_number, parameters in enumerate(variants, start=1):
        validations: list[pd.DataFrame] = []
        audits: list[bool] = []
        for inner_number, inner in enumerate(window.inner_folds):
            inner_train = data.loc[
                data["trade_date"].isin(set(inner.train_dates))
                & data["label_end_date"].astype("string").lt(
                    inner.validation_start
                )
            ].copy()
            inner_validation = data.loc[
                data["trade_date"].isin(set(inner.validation_dates))
            ].copy()
            if inner_train.empty or inner_validation.empty:
                raise ValueError(
                    "full_history_inner_fold_empty:"
                    f"{estimator}:{variant_number}:{inner_number}"
                )
            fitted = fit_predict_candidate(
                inner_train,
                inner_validation,
                candidate_features=candidate_features,
                estimator=estimator,
                parameters=parameters,
                max_features=max_features,
                minimum_coverage=minimum_coverage,
                random_state=(
                    int(random_state)
                    + variant_number * 100
                    + inner_number
                ),
            )
            inner_validation = _ordered(inner_validation)
            inner_validation["score"] = fitted.predictions
            inner_validation["fold"] = inner_number
            validations.append(inner_validation)
            audits.append(
                bool(
                    str(inner_train["label_end_date"].max())
                    < str(inner_validation["trade_date"].min())
                )
            )
        evaluation = pd.concat(validations, ignore_index=True).sort_values(
            ["trade_date", "code"], kind="stable"
        )
        replay = replay_rule_portfolio(
            evaluation,
            contract=portfolio_contract,
        )
        score = float(
            (replay.metrics or {}).get("net_excess_return") or 0.0
        )
        if not np.isfinite(score):
            score = float("-inf")
        evidence.append({
            "variant": variant_number,
            "parameters": dict(parameters),
            "inner_fold_count": len(window.inner_folds),
            "point_in_time_audit": all(audits),
            "net_excess_return": score,
            "selected": False,
        })
    selected = max(
        evidence,
        key=lambda item: (
            float(item["net_excess_return"]),
            -int(item["variant"]),
        ),
    )
    selected["selected"] = True
    return dict(selected["parameters"]), evidence


def evaluate_candidate_walk_forward(
    dataset: pd.DataFrame,
    *,
    contract: Any,
    scope: str,
    candidate_features: Sequence[str],
    estimator: str,
    parameters: Mapping[str, Any],
    portfolio_contract: Mapping[str, Any],
    random_state: int,
    parameter_variants: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce four-fold OOS scores and paper-parity exact-cost evidence."""

    import copy

    import json
    from .full_history_windows import build_full_history_windows
    from .portfolio_replay import replay_rule_portfolio
    from .robustness import stationary_block_bootstrap_probability

    data = _ordered(dataset)
    scope_contract = contract.scopes.get(str(scope))
    if scope_contract is None:
        raise ValueError(f"full_history_scope_unknown:{scope}")
    data = data.loc[
        pd.to_numeric(data.get("horizon"), errors="coerce").eq(scope_contract.horizon)
    ].copy()
    windows = build_full_history_windows(
        data["trade_date"].tolist(),
        data["label_end_date"].tolist(),
        contract=contract,
        scope=scope,
    )
    parameter_votes: dict[str, int] = {}
    parameter_scores: dict[str, float] = {}
    parameters_by_key: dict[str, dict[str, Any]] = {}
    validations: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    selected_by_fold: list[tuple[str, ...]] = []
    for fold_number, window in enumerate(windows):
        inner_selection: list[dict[str, Any]] = []
        fold_parameters = dict(parameters)
        if parameter_variants is not None:
            fold_parameters, inner_selection = _select_inner_parameters(
                data,
                window=window,
                variants=parameter_variants,
                candidate_features=candidate_features,
                estimator=estimator,
                max_features=scope_contract.max_features,
                minimum_coverage=contract.minimum_feature_coverage,
                portfolio_contract=portfolio_contract,
                random_state=int(random_state) + fold_number * 1000,
            )
            parameter_key = json.dumps(
                fold_parameters,
                sort_keys=True,
                separators=(",", ":"),
            )
            selected_inner = next(
                item for item in inner_selection if item["selected"]
            )
            parameter_votes[parameter_key] = parameter_votes.get(parameter_key, 0) + 1
            parameter_scores[parameter_key] = parameter_scores.get(parameter_key, 0.0) + float(selected_inner["net_excess_return"])
            parameters_by_key[parameter_key] = fold_parameters
        train = data.loc[
            data["trade_date"].isin(set(window.train_dates))
            & data["label_end_date"].astype("string").lt(
                window.validation_start
            )
        ].copy()
        validation = data.loc[data["trade_date"].isin(set(window.validation_dates))].copy()
        if train.empty or validation.empty:
            raise ValueError(f"full_history_fold_empty:{scope}:{fold_number}")
        fitted = fit_predict_candidate(
            train,
            validation,
            candidate_features=candidate_features,
            estimator=estimator,
            parameters=fold_parameters,
            max_features=scope_contract.max_features,
            minimum_coverage=contract.minimum_feature_coverage,
            random_state=int(random_state) + fold_number,
        )
        validation = _ordered(validation)
        validation["score"] = fitted.predictions
        validation["fold"] = fold_number
        replay = replay_rule_portfolio(validation, contract=portfolio_contract)
        rank_ic, icir = _rank_ic(validation)
        fold_metrics = dict(replay.metrics)
        folds.append({
            "fold": fold_number,
            "train_start": str(train["trade_date"].min()),
            "train_end": str(train["trade_date"].max()),
            "validation_start": str(validation["trade_date"].min()),
            "validation_end": str(validation["trade_date"].max()),
            "trade_count": int(fold_metrics.get("trade_count") or 0),
            "net_excess_return": float(fold_metrics.get("net_excess_return") or 0.0),
            "selected_parameters": fold_parameters,
            "inner_fold_count": len(window.inner_folds),
            "inner_selection": inner_selection,
            "inner_point_in_time_audit": all(item["point_in_time_audit"] for item in inner_selection),
            "rank_ic": rank_ic,
            "icir": icir,
            "selected_features": list(fitted.selected_features),
            "point_in_time_audit": bool(str(train["label_end_date"].max()) < str(validation["trade_date"].min())),
        })
        selected_by_fold.append(fitted.selected_features)
        validations.append(validation)

    evaluation = pd.concat(validations, ignore_index=True).sort_values(
        ["trade_date", "code"], kind="stable"
    )
    replay = replay_rule_portfolio(evaluation, contract=portfolio_contract)
    stress_contract = copy.deepcopy(dict(portfolio_contract))
    stress_contract["execution_cost_multiplier"] = 1.5
    stress = replay_rule_portfolio(evaluation, contract=stress_contract)
    rank_ic, icir = _rank_ic(evaluation)
    active_returns = pd.to_numeric(replay.periods.get("active_return"), errors="coerce").dropna()
    bootstrap = stationary_block_bootstrap_probability(
        active_returns.to_numpy(dtype=float),
        block_length=max(2, min(20, scope_contract.horizon)),
        threshold=0.0,
        samples=2000,
        seed=int(random_state),
    ) if len(active_returns) >= 2 else 0.0
    feature_counts: dict[str, int] = {}
    for selected in selected_by_fold:
        for column in selected:
            feature_counts[column] = feature_counts.get(column, 0) + 1
    stable_features = sorted(
        column for column, count in feature_counts.items()
        if count / len(selected_by_fold) >= contract.minimum_feature_stability
    )
    metrics = dict(replay.metrics)
    frozen_parameters = dict(parameters)
    if parameter_votes:
        frozen_key = max(
            parameter_votes,
            key=lambda key: (
                parameter_votes[key],
                parameter_scores[key],
                key,
            ),
        )
        frozen_parameters = parameters_by_key[frozen_key]
    metrics["rank_ic"] = rank_ic
    metrics["icir"] = icir
    return {
        "estimator": str(estimator),
        "parameters": frozen_parameters,
        "expected_outer_folds": len(windows),
        "point_in_time_audit": all(item["point_in_time_audit"] for item in folds),
        "folds": folds,
        "metrics": metrics,
        "cost_stress": dict(stress.metrics),
        "bootstrap_probability": float(bootstrap),
        "stable_features": stable_features,
        "feature_selection_stability": {
            column: count / len(selected_by_fold)
            for column, count in sorted(feature_counts.items())
        },
        "oos_returns": [
            {"date": str(day), "return": float(group["active_return"].mean())}
            for day, group in replay.periods.groupby("signal_date", sort=True)
        ],
        "daily_active_returns": active_returns.tolist(),
    }


def evaluate_scope_campaign(
    dataset: pd.DataFrame,
    *,
    contract: Any,
    scope: str,
    candidate_features: Sequence[str],
    candidate_declarations: Mapping[str, Sequence[Mapping[str, Any]]],
    portfolio_contract: Mapping[str, Any],
    random_state: int,
) -> dict[str, Any]:
    """Evaluate every declared trial before selecting a development winner."""

    import hashlib
    import json

    from .governance import evaluate_campaign_governance

    trials: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    declared_variant_count = sum(
        len(variants) for variants in candidate_declarations.values()
    )
    declared_family_count = len(candidate_declarations)
    for estimator, variants in candidate_declarations.items():
        normalized_variants = [dict(item) for item in variants]
        declaration = {
            "scope": str(scope),
            "estimator": str(estimator),
            "variants": normalized_variants,
            "inner_splits": int(contract.inner_splits),
        }
        trial_hash = hashlib.sha256(
            json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        trial_id = f"{scope}:{estimator}:{trial_hash}"
        try:
            result = evaluate_candidate_walk_forward(
                dataset,
                contract=contract,
                scope=scope,
                candidate_features=candidate_features,
                estimator=estimator,
                parameters=normalized_variants[0],
                parameter_variants=normalized_variants,
                portfolio_contract=portfolio_contract,
                random_state=int(random_state) + len(trials) + 1,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            failures.append({
                "trial_id": trial_id,
                "estimator": estimator,
                "variants": normalized_variants,
                "error": str(exc),
            })
            continue
        result["trial_id"] = trial_id
        result["parameter_variants"] = normalized_variants
        trials.append(result)
    if not trials:
        return {
            "status": "no_valid_trials",
            "scope": str(scope),
            "declared_trial_count": declared_variant_count,
            "declared_variant_count": declared_variant_count,
            "declared_family_count": declared_family_count,
            "trial_count": 0,
            "failures": failures,
            "trials": [],
        }
    selected = max(
        trials,
        key=lambda item: (
            float((item.get("metrics") or {}).get("net_excess_return") or -1.0),
            float(item.get("bootstrap_probability") or 0.0),
            str(item.get("trial_id") or ""),
        ),
    )
    governance = evaluate_campaign_governance(
        [
            {"trial_id": item["trial_id"], "oos_returns": item["oos_returns"]}
            for item in trials
        ],
        selected_trial_id=selected["trial_id"],
        legacy_trials=[],
    )
    metrics = selected.get("metrics") or {}
    stress = selected.get("cost_stress") or {}
    folds = selected.get("folds") or []
    checks = {
        "all_trials_completed": len(trials) == declared_family_count,
        "point_in_time_audit": selected.get("point_in_time_audit") is True,
        "positive_net_excess_return": float(metrics.get("net_excess_return") or -1.0) > 0.0,
        "cost_stress_net_excess_return": float(stress.get("net_excess_return") or -1.0) >= 0.0,
        "all_positive_excess_folds": len(folds) == 4 and all(float(item.get("net_excess_return") or -1.0) > 0.0 for item in folds),
        "bootstrap_probability": float(selected.get("bootstrap_probability") or 0.0) >= 0.95,
        "deflated_sharpe_probability": float(governance.get("deflated_sharpe_probability") or 0.0) >= 0.95,
        "probability_of_backtest_overfit": float(governance.get("probability_of_backtest_overfit") or 1.0) <= 0.50,
        "trade_activity": int(metrics.get("trade_count") or 0) > 0,
        "target_fill_ratio": float(metrics.get("target_fill_ratio") or 0.0) >= 0.95,
    }
    selected["governance"] = governance
    selected["passed_transparent_gates"] = all(checks.values())
    selected["gate_checks"] = checks
    return {
        "status": "development_pass" if all(checks.values()) else "no_pass",
        "scope": str(scope),
        "declared_trial_count": declared_variant_count,
        "declared_variant_count": declared_variant_count,
        "declared_family_count": declared_family_count,
        "trial_count": len(trials),
        "selected_trial_id": selected["trial_id"],
        "governance": governance,
        "selected": selected,
        "failures": failures,
        "trials": trials,
    }


def evaluate_frozen_historical_test(
    dataset: pd.DataFrame,
    *,
    contract: Any,
    scope: str,
    candidate_features: Sequence[str],
    estimator: str,
    parameters: Mapping[str, Any],
    portfolio_contract: Mapping[str, Any],
    manifest_path: Any,
    declaration_id: str,
    random_state: int,
) -> dict[str, Any]:
    """Open the observed historical test once and evaluate a frozen estimator."""

    import copy

    from .full_history_windows import open_historical_test_once
    from .portfolio_replay import replay_rule_portfolio
    from .robustness import stationary_block_bootstrap_probability

    manifest = open_historical_test_once(manifest_path, declaration_id)
    data = _ordered(dataset)
    scope_contract = contract.scopes.get(str(scope))
    if scope_contract is None:
        raise ValueError(f"full_history_scope_unknown:{scope}")
    data = data.loc[pd.to_numeric(data.get("horizon"), errors="coerce").eq(scope_contract.horizon)].copy()
    development = data.loc[
        data["trade_date"].le(contract.development_end)
        & data["label_end_date"].astype("string").lt(contract.historical_test_start)
    ].copy()
    historical_test = data.loc[data["trade_date"].ge(contract.historical_test_start)].copy()
    if development.empty or historical_test.empty:
        raise ValueError(f"full_history_historical_test_empty:{scope}")
    fitted = fit_predict_candidate(
        development,
        historical_test,
        candidate_features=candidate_features,
        estimator=estimator,
        parameters=parameters,
        max_features=scope_contract.max_features,
        minimum_coverage=contract.minimum_feature_coverage,
        random_state=random_state,
        validation_labels_visible=False,
    )
    historical_test = _ordered(historical_test)
    historical_test["score"] = fitted.predictions
    historical_test["fold"] = "historical_test"
    replay = replay_rule_portfolio(historical_test, contract=portfolio_contract)
    stress_contract = copy.deepcopy(dict(portfolio_contract))
    stress_contract["execution_cost_multiplier"] = 1.5
    stress = replay_rule_portfolio(historical_test, contract=stress_contract)
    rank_ic, icir = _rank_ic(historical_test)
    active = pd.to_numeric(replay.periods.get("active_return"), errors="coerce").dropna()
    bootstrap = stationary_block_bootstrap_probability(
        active.to_numpy(dtype=float),
        block_length=max(2, min(20, scope_contract.horizon)),
        samples=2000,
        seed=int(random_state),
        threshold=0.0,
    ) if len(active) >= 2 else 0.0
    metrics = dict(replay.metrics)
    metrics["rank_ic"] = rank_ic
    metrics["icir"] = icir
    return {
        "historical_test_status": "diagnostic_only_already_observed",
        "historical_test_open_count": int(manifest.get("historical_test_open_count") or 0),
        "development_point_in_time_audit": bool(
            str(development["label_end_date"].max())
            < contract.historical_test_start
        ),
        "development_end": contract.development_end,
        "historical_test_start": contract.historical_test_start,
        "historical_test_end": str(historical_test["trade_date"].max()),
        "estimator": str(estimator),
        "parameters": dict(parameters),
        "selected_features": list(fitted.selected_features),
        "metrics": metrics,
        "cost_stress": dict(stress.metrics),
        "bootstrap_probability": float(bootstrap),
    }


__all__ = [
    "CandidatePrediction",
    "evaluate_candidate_walk_forward",
    "evaluate_frozen_historical_test",
    "evaluate_scope_campaign",
    "fit_predict_candidate",
]
