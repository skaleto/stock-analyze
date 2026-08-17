"""Local signed-IC residual-momentum campaign runner."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from .full_history_rebuild import load_scope_dataset
from .full_history_windows import build_full_history_windows, load_full_history_config
from .governance import evaluate_campaign_governance
from .portfolio_replay import replay_rule_portfolio
from .residual_momentum import ResidualMomentumConfig, build_exante_residual_momentum
from .robustness import stationary_block_bootstrap_probability
from .signed_ic import SignedICConfig
from .signed_ic_training import fit_signed_candidate


FEATURE_FAMILIES = {
    "exante_residual_momentum_20_5": "residual_momentum",
    "exante_residual_momentum_60_5": "residual_momentum",
    "exante_residual_momentum_120_20": "residual_momentum",
    "account_low_volatility_percentile": "low_volatility",
    "account_liquidity_percentile": "liquidity",
    "account_quality_percentile": "quality",
    "roe": "quality",
    "cash_conversion": "quality",
    "free_cashflow_to_assets": "quality",
    "pe_ttm": "value",
    "pb": "value",
}

ESTIMATORS = {
    "signed_ic_composite": {},
    "positive_elastic_net": {"alpha": 0.001, "l1_ratio": 0.25},
    "monotone_lambdarank": {
        "n_estimators": 250,
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 5,
        "min_child_samples": 200,
        "reg_lambda": 10.0,
    },
}


def _benchmark_returns(root: Path, benchmark_code: str) -> pd.DataFrame:
    path = root / "data/shared/backtest_cache/benchmark_daily" / f"{benchmark_code}.csv"
    frame = pd.read_csv(path, dtype={"trade_date": str, "ts_code": str})
    frame = frame.sort_values("trade_date", kind="stable")
    close = pd.to_numeric(frame["close"], errors="coerce")
    return pd.DataFrame({
        "trade_date": frame["trade_date"].astype(str),
        "benchmark_return_1": close.pct_change(fill_method=None),
    })


def _rank_ic(frame: pd.DataFrame) -> tuple[float, float]:
    values = []
    for _, group in frame.groupby("trade_date", sort=True):
        value = pd.to_numeric(group["score"], errors="coerce").corr(
            pd.to_numeric(group["excess_return"], errors="coerce"),
            method="spearman",
        )
        if pd.notna(value):
            values.append(float(value))
    if not values:
        return 0.0, 0.0
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    return mean, mean / std if std > 1e-12 else 0.0


def _fit_and_replay(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    estimator: str,
    parameters: Mapping[str, Any],
    selector_config: SignedICConfig,
    portfolio_contract: Mapping[str, Any],
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    fitted = fit_signed_candidate(
        train,
        validation,
        candidate_features=tuple(FEATURE_FAMILIES),
        feature_families=FEATURE_FAMILIES,
        selector_config=selector_config,
        estimator=estimator,
        parameters=parameters,
        seed=seed,
    )
    scored = validation.sort_values(
        ["trade_date", "code"], kind="stable"
    ).reset_index(drop=True)
    scored["score"] = fitted.predictions
    replay = replay_rule_portfolio(scored, contract=portfolio_contract)
    rank_ic, icir = _rank_ic(scored)
    audit = {
        "metrics": dict(replay.metrics),
        "rank_ic": rank_ic,
        "icir": icir,
        "selected_features": list(fitted.selection.selected_features),
        "directions": dict(fitted.selection.directions),
        "weights": dict(fitted.selection.weights),
        "feature_audits": [
            {
                **item.__dict__,
                "rejection_reasons": list(item.rejection_reasons),
            }
            for item in fitted.selection.audits
        ],
    }
    return audit, scored


def _residual_inner_splits(
    data: pd.DataFrame,
    outer_train_dates: Sequence[str],
) -> list[tuple[pd.DataFrame, pd.DataFrame, str]]:
    outer = data.loc[data["trade_date"].isin(set(outer_train_dates))].copy()
    residual_columns = [
        column for column in FEATURE_FAMILIES
        if column.startswith("exante_residual_momentum_")
    ]
    usable_by_date = (
        outer[residual_columns]
        .notna()
        .any(axis=1)
        .groupby(outer["trade_date"])
        .mean()
    )
    usable_dates = sorted(
        str(day) for day, coverage in usable_by_date.items()
        if float(coverage) >= 0.70
    )
    if len(usable_dates) < 80:
        raise ValueError("signed_ic_residual_warmup_insufficient")
    chunks = [list(chunk) for chunk in np.array_split(usable_dates, 4)]
    if any(not chunk for chunk in chunks):
        raise ValueError("signed_ic_inner_dates_insufficient")
    splits = []
    for index in range(3):
        train_dates = {
            day for chunk in chunks[: index + 1] for day in chunk
        }
        validation_dates = set(chunks[index + 1])
        validation_start = min(validation_dates)
        train = outer.loc[
            outer["trade_date"].isin(train_dates)
            & outer["label_end_date"].astype(str).lt(validation_start)
        ].copy()
        validation = outer.loc[
            outer["trade_date"].isin(validation_dates)
        ].copy()
        splits.append((train, validation, validation_start))
    return splits


def evaluate_signed_scope(
    dataset: pd.DataFrame,
    *,
    contract: Any,
    scope: str,
    portfolio_contract: Mapping[str, Any],
    selector_config: SignedICConfig,
    seed: int = 20260816,
) -> dict[str, Any]:
    """Evaluate fixed signed-signal families with strict inner rejection."""

    scope_contract = contract.scopes[scope]
    data = dataset.loc[
        pd.to_numeric(dataset["horizon"], errors="coerce").eq(scope_contract.horizon)
    ].copy()
    windows = build_full_history_windows(
        data["trade_date"].tolist(),
        data["label_end_date"].tolist(),
        contract=contract,
        scope=scope,
    )
    trials = []
    failures = []
    for estimator, parameters in ESTIMATORS.items():
        validations = []
        folds = []
        rejected = None
        for fold_number, window in enumerate(windows):
            inner_evidence = []
            inner_splits = _residual_inner_splits(data, window.train_dates)
            for inner_number, (
                inner_train, inner_validation, inner_validation_start
            ) in enumerate(inner_splits):
                try:
                    audit, _ = _fit_and_replay(
                        inner_train,
                        inner_validation,
                        estimator=estimator,
                        parameters=parameters,
                        selector_config=selector_config,
                        portfolio_contract=portfolio_contract,
                        seed=seed + fold_number * 100 + inner_number,
                    )
                    inner_evidence.append({
                        "inner_fold": inner_number,
                        "net_excess_return": float(
                            audit["metrics"].get("net_excess_return") or 0.0
                        ),
                        "selected_features": audit["selected_features"],
                        "point_in_time_audit": bool(
                            str(inner_train["label_end_date"].max())
                            < inner_validation_start
                        ),
                    })
                except (ValueError, RuntimeError) as exc:
                    inner_evidence.append({
                        "inner_fold": inner_number,
                        "error": str(exc),
                        "net_excess_return": None,
                        "point_in_time_audit": bool(
                            not inner_train.empty
                            and str(inner_train["label_end_date"].max())
                            < inner_validation_start
                        ),
                    })
            valid_inner = [
                item for item in inner_evidence
                if item["net_excess_return"] is not None
            ]
            positive_count = sum(
                float(item["net_excess_return"]) > 0.0 for item in valid_inner
            )
            aggregate = sum(
                float(item["net_excess_return"]) for item in valid_inner
            )
            if (
                len(valid_inner) != len(inner_splits)
                or positive_count < 2
                or aggregate <= 0.0
            ):
                rejected = {
                    "estimator": estimator,
                    "outer_fold": fold_number,
                    "reason": "inner_signal_rejected",
                    "positive_inner_folds": positive_count,
                    "aggregate_inner_net_excess_return": aggregate,
                    "inner_evidence": inner_evidence,
                }
                break
            train = data.loc[
                data["trade_date"].isin(set(window.train_dates))
                & data["label_end_date"].astype(str).lt(window.validation_start)
            ].copy()
            validation = data.loc[
                data["trade_date"].isin(set(window.validation_dates))
            ].copy()
            residual_available = train[
                [
                    column for column in FEATURE_FAMILIES
                    if column.startswith("exante_residual_momentum_")
                ]
            ].notna().any(axis=1)
            train = train.loc[residual_available].copy()
            audit, scored = _fit_and_replay(
                train,
                validation,
                estimator=estimator,
                parameters=parameters,
                selector_config=selector_config,
                portfolio_contract=portfolio_contract,
                seed=seed + fold_number,
            )
            folds.append({
                "fold": fold_number,
                "validation_start": str(validation["trade_date"].min()),
                "validation_end": str(validation["trade_date"].max()),
                "inner_evidence": inner_evidence,
                "point_in_time_audit": bool(
                    str(train["label_end_date"].max()) < window.validation_start
                ),
                **audit,
            })
            scored["fold"] = fold_number
            validations.append(scored)
        if rejected is not None:
            failures.append(rejected)
            continue
        evaluation = pd.concat(validations, ignore_index=True)
        replay = replay_rule_portfolio(evaluation, contract=portfolio_contract)
        stress_contract = copy.deepcopy(dict(portfolio_contract))
        stress_contract["execution_cost_multiplier"] = 1.5
        stress = replay_rule_portfolio(evaluation, contract=stress_contract)
        active = pd.to_numeric(
            replay.periods.get("active_return"), errors="coerce"
        ).dropna()
        bootstrap = stationary_block_bootstrap_probability(
            active.to_numpy(dtype=float),
            block_length=20,
            samples=2000,
            seed=seed,
            threshold=0.0,
        ) if len(active) >= 2 else 0.0
        trials.append({
            "trial_id": f"{scope}:{estimator}",
            "estimator": estimator,
            "parameters": dict(parameters),
            "folds": folds,
            "metrics": dict(replay.metrics),
            "cost_stress": dict(stress.metrics),
            "bootstrap_probability": float(bootstrap),
            "oos_returns": [
                {"date": str(day), "return": float(group["active_return"].mean())}
                for day, group in replay.periods.groupby("signal_date", sort=True)
            ],
        })
    if not trials:
        return {
            "status": "no_valid_trials",
            "scope": scope,
            "trials": [],
            "failures": failures,
        }
    selected = max(
        trials,
        key=lambda item: float(item["metrics"].get("net_excess_return") or -1.0),
    )
    governance = evaluate_campaign_governance(
        [{"trial_id": item["trial_id"], "oos_returns": item["oos_returns"]} for item in trials],
        selected_trial_id=selected["trial_id"],
        legacy_trials=[],
    )
    checks = {
        "four_outer_folds": len(selected["folds"]) == 4,
        "all_positive_outer_folds": all(
            float(fold["metrics"].get("net_excess_return") or -1.0) > 0.0
            for fold in selected["folds"]
        ),
        "positive_net_excess": float(selected["metrics"].get("net_excess_return") or -1.0) > 0.0,
        "cost_stress": float(selected["cost_stress"].get("net_excess_return") or -1.0) >= 0.0,
        "bootstrap": selected["bootstrap_probability"] >= 0.95,
        "dsr": float(governance.get("deflated_sharpe_probability") or 0.0) >= 0.95,
        "pbo": float(governance.get("probability_of_backtest_overfit") or 1.0) <= 0.20,
    }
    return {
        "status": "development_pass" if all(checks.values()) else "no_pass",
        "scope": scope,
        "selected": selected,
        "governance": governance,
        "gate_checks": checks,
        "trials": trials,
        "failures": failures,
    }


def run_signed_ic_campaign(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    scopes: Sequence[str] = ("hs300", "zz500"),
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract = load_full_history_config(
        root / "configs/research/full_history_rebuild.yaml"
    )
    results = []
    for scope in scopes:
        scope_contract = contract.scopes[scope]
        dataset, _ = load_scope_dataset(
            root / f"data/research/features/a_share/{snapshot_date}.parquet",
            root / f"data/research/labels/a_share/{snapshot_date}.parquet",
            market="a_share",
            scope=scope,
            horizon=scope_contract.horizon,
        )
        benchmark_code = "000300" if scope == "hs300" else "000905"
        dataset = build_exante_residual_momentum(
            dataset,
            _benchmark_returns(root, benchmark_code),
            config=ResidualMomentumConfig(),
        )
        baseline = yaml.safe_load(
            (root / "configs/competition_a_share.yaml").read_text(encoding="utf-8")
        )
        baseline["accounts"] = [
            account for account in baseline.get("accounts") or []
            if str(account.get("id")) == scope
        ]
        results.append(evaluate_signed_scope(
            dataset,
            contract=contract,
            scope=scope,
            portfolio_contract=baseline,
            selector_config=SignedICConfig(),
        ))
    payload = {
        "status": "complete",
        "protocol": "signed-ic-residual-momentum-v1",
        "snapshot_date": str(snapshot_date),
        "results": results,
        "shadow_gate_passed": [
            item["scope"] for item in results
            if item["status"] == "development_pass"
        ],
    }
    run_root = root / "data/research/signed_ic_residual_momentum" / str(snapshot_date)
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / "report.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    payload["report_path"] = str(path)
    return payload


__all__ = ["evaluate_signed_scope", "run_signed_ic_campaign"]
