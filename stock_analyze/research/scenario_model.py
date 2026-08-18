"""Preregistered three-scene residual-model experiment.

The campaign reads development rows only, keeps scene construction transparent,
and writes Research evidence without touching any model registry.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler

from .account_features import build_account_feature_view, date_balanced_sample_weights
from .labels import LABEL_CONTRACT_VERSION
from .models import score_transparent_strategy
from .portfolio_replay import replay_rule_portfolio
from .classical_specs import transparent_strategy_specs


SCENES = ("expansion", "range", "stress")
ABLATIONS = (
    "transparent_reference",
    "router_only",
    "pooled_residual",
    "scenario_specialists",
)


@dataclass(frozen=True)
class FoldBoundary:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str


def _date_key(value: object) -> str:
    key = str(value or "").replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"scenario_model_date:{value}")
    return key


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_scenario_contract(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scenario_model_contract")
    if payload.get("protocol") != "scenario-specialists-v1":
        raise ValueError("scenario_model_protocol")
    if payload.get("research_only") is not True or payload.get("formal_order_source") is not False:
        raise ValueError("scenario_model_research_boundary")
    if payload.get("open_historical_test") is not False:
        raise ValueError("scenario_model_historical_test_closed")
    if tuple((payload.get("router") or {}).get("scenes") or ()) != SCENES:
        raise ValueError("scenario_model_scenes")
    if tuple((payload.get("model") or {}).get("ablations") or ()) != ABLATIONS:
        raise ValueError("scenario_model_ablations")
    if float(payload.get("residual_weight") or 0.0) != 0.10:
        raise ValueError("scenario_model_residual_weight")
    folds = payload.get("outer_folds") or []
    if len(folds) != 4:
        raise ValueError("scenario_model_outer_folds")
    development_end = _date_key(payload.get("development_end"))
    for raw in folds:
        boundary = FoldBoundary(*(
            _date_key(raw[key])
            for key in (
                "train_start", "train_end",
                "validation_start", "validation_end",
            )
        ))
        if not (
            boundary.train_start <= boundary.train_end
            < boundary.validation_start <= boundary.validation_end
            <= development_end
        ):
            raise ValueError("scenario_model_fold_boundary")
    scopes = payload.get("scopes") or {}
    if tuple(scopes) != ("hs300", "zz500", "hk_exposure", "us_exposure"):
        raise ValueError("scenario_model_scopes")
    forbidden = tuple(str(value) for value in payload.get("forbidden_feature_prefixes") or [])
    for scope, raw in scopes.items():
        features = tuple(str(value) for value in raw.get("features") or [])
        if len(features) != int(payload.get("maximum_features") or 0):
            raise ValueError(f"scenario_model_feature_budget:{scope}")
        if any(feature.startswith(forbidden) for feature in features):
            raise ValueError(f"scenario_model_forbidden_feature:{scope}")
        exposure = raw.get("exposure") or {}
        if set(exposure) != set(SCENES) or any(
            not 0.0 <= float(value) <= 1.0 for value in exposure.values()
        ):
            raise ValueError(f"scenario_model_exposure:{scope}")
    return {**payload, "contract_sha256": _canonical_hash(payload)}


def classify_scenes(frame: pd.DataFrame, router: Mapping[str, Any]) -> pd.DataFrame:
    """Attach one date-level scene using trailing observables only."""

    required = {
        "trade_date", "momentum_60", "sma_distance_200",
        "realized_volatility_20",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"scenario_router_columns:{','.join(missing)}")
    source = frame.copy()
    source["trade_date"] = source["trade_date"].astype(str)
    momentum = pd.to_numeric(source["momentum_60"], errors="coerce")
    distance = pd.to_numeric(source["sma_distance_200"], errors="coerce")
    volatility = pd.to_numeric(source["realized_volatility_20"], errors="coerce")
    source["_positive_momentum"] = momentum.gt(0.0).where(momentum.notna())
    source["_momentum"] = momentum
    source["_distance"] = distance
    source["_volatility"] = volatility
    daily = source.groupby("trade_date", sort=True).agg(
        scene_momentum=("_momentum", "median"),
        scene_sma_distance=("_distance", "median"),
        scene_breadth=("_positive_momentum", "mean"),
        scene_volatility=("_volatility", "median"),
    ).reset_index()
    trailing = max(int(router.get("trailing_sessions") or 0), 1)
    minimum = max(int(router.get("minimum_history_sessions") or 0), 1)
    quantile = float(router.get("high_volatility_quantile"))
    daily["scene_volatility_boundary"] = (
        daily["scene_volatility"].shift(1).rolling(trailing, min_periods=minimum).quantile(quantile)
    )
    high_volatility = (
        daily["scene_volatility_boundary"].notna()
        & daily["scene_volatility"].gt(daily["scene_volatility_boundary"])
    )
    breadth_floor = float(router.get("expansion_breadth_floor"))
    breadth_ceiling = float(router.get("stress_breadth_ceiling"))
    stress = (
        (daily["scene_momentum"].lt(0.0) & daily["scene_sma_distance"].lt(0.0))
        | (daily["scene_breadth"].le(breadth_ceiling) & high_volatility)
    )
    expansion = (
        daily["scene_momentum"].gt(0.0)
        & daily["scene_sma_distance"].gt(0.0)
        & daily["scene_breadth"].ge(breadth_floor)
        & ~high_volatility
        & ~stress
    )
    daily["scene"] = np.select(
        [stress, expansion], ["stress", "expansion"], default="range"
    )
    return source.drop(
        columns=["_positive_momentum", "_momentum", "_distance", "_volatility"]
    ).merge(daily, on="trade_date", how="left", validate="many_to_one")


def _fit_elastic_net(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    features: Sequence[str],
    parameters: Mapping[str, Any],
    random_state: int,
) -> np.ndarray:
    medians = {
        column: float(pd.to_numeric(train[column], errors="coerce").median())
        for column in features
    }
    if any(not np.isfinite(value) for value in medians.values()):
        raise ValueError("scenario_model_imputation")
    train_x = np.column_stack([
        pd.to_numeric(train[column], errors="coerce").fillna(medians[column])
        for column in features
    ])
    validation_x = np.column_stack([
        pd.to_numeric(validation[column], errors="coerce").fillna(medians[column])
        for column in features
    ])
    target = pd.to_numeric(train["excess_return"], errors="coerce").fillna(0.0)
    weights = date_balanced_sample_weights(train).to_numpy(dtype=float)
    scaler = StandardScaler().fit(train_x, sample_weight=weights)
    model = ElasticNet(
        alpha=float(parameters["alpha"]),
        l1_ratio=float(parameters["l1_ratio"]),
        max_iter=5000,
        random_state=int(random_state),
    ).fit(scaler.transform(train_x), target.to_numpy(dtype=float), sample_weight=weights)
    return np.asarray(model.predict(scaler.transform(validation_x)), dtype=float)


def _rank(values: pd.Series, dates: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").groupby(
        dates.astype(str), sort=False
    ).rank(pct=True, method="average").fillna(0.5)


def _blend_score(
    baseline_score: pd.Series,
    residual: np.ndarray,
    trade_dates: pd.Series,
    weight: float,
) -> pd.Series:
    baseline_rank = _rank(baseline_score, trade_dates)
    residual_rank = _rank(pd.Series(residual, index=baseline_score.index), trade_dates)
    return (1.0 - float(weight)) * baseline_rank + float(weight) * residual_rank


def _rank_ic(frame: pd.DataFrame) -> float:
    values: list[float] = []
    for _, group in frame.groupby("trade_date", sort=True):
        if len(group) < 2:
            continue
        value = pd.to_numeric(group["score"], errors="coerce").corr(
            pd.to_numeric(group["excess_return"], errors="coerce"),
            method="spearman",
        )
        if pd.notna(value):
            values.append(float(value))
    return float(np.mean(values)) if values else 0.0


def _scope_dataset(
    feature_path: Path,
    label_path: Path,
    *,
    scope: str,
    horizon: int,
    development_end: str,
) -> pd.DataFrame:
    filters = [("trade_date", "<=", str(development_end))]
    features = pd.read_parquet(feature_path, filters=filters)
    labels = pd.read_parquet(label_path, filters=filters)
    observed = set(
        labels.get("label_contract_version", pd.Series(dtype=str)).dropna().astype(str)
    )
    if observed != {LABEL_CONTRACT_VERSION}:
        raise ValueError("scenario_model_label_contract")
    for frame in (features, labels):
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frame["trade_date"] = frame["trade_date"].astype(str)
    scoped = build_account_feature_view(features, account_scope=scope)
    scoped_labels = labels.loc[
        labels["account_id"].astype(str).eq(scope)
        & pd.to_numeric(labels["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    label_columns = [
        column for column in scoped_labels.columns
        if column in {"code", "trade_date"} or column not in scoped.columns
    ]
    return scoped.merge(
        scoped_labels[label_columns],
        on=["code", "trade_date"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


def _baseline_spec(market: str, scope: str, spec_id: str) -> Any:
    matches = [
        spec for spec in transparent_strategy_specs(market, scope)
        if spec.spec_id == spec_id
    ]
    if len(matches) != 1:
        raise ValueError(f"scenario_model_baseline_spec:{scope}:{spec_id}")
    return matches[0]


def evaluate_scope(
    dataset: pd.DataFrame,
    *,
    contract: Mapping[str, Any],
    scope: str,
    portfolio_contract: Mapping[str, Any],
) -> dict[str, Any]:
    scope_contract = dict((contract.get("scopes") or {})[scope])
    market = str(scope_contract["market"])
    features = tuple(scope_contract["features"])
    missing = sorted(set(features).difference(dataset.columns))
    if missing:
        return {"scope": scope, "market": market, "status": "insufficient_data", "reasons": [f"missing_features:{','.join(missing)}"]}
    coverage = {
        column: float(pd.to_numeric(dataset[column], errors="coerce").notna().mean())
        for column in features
    }
    weak = sorted(
        column for column, value in coverage.items()
        if value < float(contract["minimum_feature_coverage"])
    )
    if weak:
        return {"scope": scope, "market": market, "status": "insufficient_data", "reasons": [f"feature_coverage:{','.join(weak)}"], "feature_coverage": coverage}

    routed = classify_scenes(dataset, contract["router"])
    baseline = score_transparent_strategy(
        routed,
        _baseline_spec(market, scope, str(scope_contract["baseline_spec_id"])),
    )
    baseline["baseline_score"] = pd.to_numeric(baseline["score"], errors="coerce")
    base_exposure = pd.to_numeric(
        baseline.get("_target_risky_exposure", 1.0), errors="coerce"
    ).fillna(0.0)
    scene_caps = baseline["scene"].map(
        {key: float(value) for key, value in scope_contract["exposure"].items()}
    )
    baseline["router_exposure"] = np.minimum(base_exposure, scene_caps)

    fold_rows: list[dict[str, Any]] = []
    evaluations: dict[str, list[pd.DataFrame]] = {key: [] for key in ABLATIONS}
    minimum_scene_dates = int(contract["minimum_scene_training_dates"])
    model_parameters = dict(contract["model"]["parameters"])
    residual_weight = float(contract["residual_weight"])
    all_scenes_fitted = True
    point_in_time_audit = True
    for fold_number, raw in enumerate(contract["outer_folds"]):
        boundary = FoldBoundary(*(
            _date_key(raw[key]) for key in (
                "train_start", "train_end",
                "validation_start", "validation_end",
            )
        ))
        train = baseline.loc[
            baseline["trade_date"].between(boundary.train_start, boundary.train_end)
            & baseline["label_end_date"].astype(str).lt(boundary.validation_start)
        ].copy()
        validation = baseline.loc[
            baseline["trade_date"].between(boundary.validation_start, boundary.validation_end)
        ].copy()
        if train.empty or validation.empty:
            raise ValueError(f"scenario_model_fold_empty:{scope}:{fold_number}")
        pit = str(train["label_end_date"].max()) < boundary.validation_start
        point_in_time_audit = point_in_time_audit and pit
        pooled = _fit_elastic_net(
            train, validation, features=features, parameters=model_parameters,
            random_state=int(contract["random_state"]) + fold_number,
        )
        specialist = np.zeros(len(validation), dtype=float)
        train_scene_dates: dict[str, int] = {}
        for scene_index, scene in enumerate(SCENES):
            scene_train = train.loc[train["scene"].eq(scene)]
            scene_validation = validation.loc[validation["scene"].eq(scene)]
            scene_dates = int(scene_train["trade_date"].nunique())
            train_scene_dates[scene] = scene_dates
            if scene_dates < minimum_scene_dates:
                all_scenes_fitted = False
                continue
            if scene_validation.empty:
                continue
            predictions = _fit_elastic_net(
                scene_train, scene_validation, features=features,
                parameters=model_parameters,
                random_state=int(contract["random_state"]) + fold_number * 10 + scene_index,
            )
            positions = np.flatnonzero(validation["scene"].eq(scene).to_numpy())
            specialist[positions] = predictions

        frames = {
            "transparent_reference": validation.copy(),
            "router_only": validation.copy(),
            "pooled_residual": validation.copy(),
            "scenario_specialists": validation.copy(),
        }
        frames["transparent_reference"]["score"] = frames["transparent_reference"]["baseline_score"]
        frames["router_only"]["score"] = frames["router_only"]["baseline_score"]
        frames["pooled_residual"]["score"] = _blend_score(
            validation["baseline_score"], pooled, validation["trade_date"], residual_weight
        )
        frames["scenario_specialists"]["score"] = _blend_score(
            validation["baseline_score"], specialist, validation["trade_date"], residual_weight
        )
        for key, frame in frames.items():
            frame["fold"] = fold_number
            if key != "transparent_reference":
                frame["_target_risky_exposure"] = frame["router_exposure"]
            evaluations[key].append(frame)
        fold_rows.append({
            "fold": fold_number,
            "train_start": str(train["trade_date"].min()),
            "train_end": str(train["trade_date"].max()),
            "validation_start": str(validation["trade_date"].min()),
            "validation_end": str(validation["trade_date"].max()),
            "point_in_time_audit": pit,
            "train_scene_dates": train_scene_dates,
            "validation_scene_share": {
                scene: float(validation["scene"].eq(scene).mean()) for scene in SCENES
            },
        })

    replay_contract = {
        **copy.deepcopy(dict(portfolio_contract)),
        "rebalance_frequency": str(scope_contract["rebalance_frequency"]),
    }
    results: dict[str, Any] = {}
    fold_metrics: dict[str, list[dict[str, Any]]] = {}
    for key, parts in evaluations.items():
        evaluation = pd.concat(parts, ignore_index=True, sort=False)
        replay = replay_rule_portfolio(evaluation, contract=replay_contract)
        results[key] = {
            **dict(replay.metrics),
            "rank_ic": _rank_ic(evaluation),
        }
        fold_metrics[key] = []
        for fold_number in range(4):
            part = evaluation.loc[pd.to_numeric(evaluation["fold"]).eq(fold_number)]
            fold_replay = replay_rule_portfolio(part, contract=replay_contract)
            fold_metrics[key].append({
                "fold": fold_number,
                "net_excess_return": float(fold_replay.metrics["net_excess_return"]),
                "max_drawdown": float(fold_replay.metrics["max_drawdown"]),
                "annual_turnover": float(fold_replay.metrics["annual_turnover"]),
            })

    candidate = results["scenario_specialists"]
    reference = results["transparent_reference"]
    router = results["router_only"]
    pooled = results["pooled_residual"]
    gates = dict(contract["gates"])
    deltas = {
        "reference": float(candidate["net_excess_return"] - reference["net_excess_return"]),
        "router": float(candidate["net_excess_return"] - router["net_excess_return"]),
        "pooled": float(candidate["net_excess_return"] - pooled["net_excess_return"]),
    }
    positive_folds = {}
    for comparator in ("transparent_reference", "router_only", "pooled_residual"):
        positive_folds[comparator] = sum(
            candidate_row["net_excess_return"] > comparator_row["net_excess_return"]
            for candidate_row, comparator_row in zip(
                fold_metrics["scenario_specialists"], fold_metrics[comparator]
            )
        )
    scene_shares = {
        scene: float(baseline["scene"].eq(scene).mean()) for scene in SCENES
    }
    checks = {
        "point_in_time_audit": point_in_time_audit is True,
        "all_scenes_fitted": all_scenes_fitted is True,
        "scene_coverage": all(value >= float(gates["minimum_scene_share"]) for value in scene_shares.values()),
        "candidate_net_excess_return": float(candidate["net_excess_return"]) >= float(gates["minimum_candidate_net_excess_return"]),
        "delta_vs_reference": deltas["reference"] >= float(gates["minimum_aggregate_delta_vs_reference"]),
        "delta_vs_router": deltas["router"] >= float(gates["minimum_aggregate_delta_vs_router"]),
        "delta_vs_pooled": deltas["pooled"] >= float(gates["minimum_aggregate_delta_vs_pooled"]),
        "positive_folds_vs_reference": positive_folds["transparent_reference"] >= int(gates["minimum_positive_folds_vs_reference"]),
        "positive_folds_vs_router": positive_folds["router_only"] >= int(gates["minimum_positive_folds_vs_router"]),
        "positive_folds_vs_pooled": positive_folds["pooled_residual"] >= int(gates["minimum_positive_folds_vs_pooled"]),
        "drawdown_delta": float(candidate["max_drawdown"] - reference["max_drawdown"]) <= float(gates["maximum_drawdown_delta_vs_reference"]),
        "turnover_ratio": float(candidate["annual_turnover"]) <= float(reference["annual_turnover"]) * float(gates["maximum_turnover_ratio_vs_reference"]),
        "rank_ic": float(candidate["rank_ic"]) >= float(gates["minimum_rank_ic"]),
    }
    return {
        "scope": scope,
        "market": market,
        "status": "development_pass" if all(checks.values()) else "no_pass",
        "contract_sha256": contract["contract_sha256"],
        "feature_coverage": coverage,
        "scene_shares": scene_shares,
        "folds": fold_rows,
        "metrics": results,
        "fold_metrics": fold_metrics,
        "deltas": deltas,
        "positive_folds": positive_folds,
        "gate_checks": checks,
        "gate_reasons": [key for key, passed in checks.items() if not passed],
        "formal_strategy_activated": False,
        "registry_mutated": False,
    }


def run_scenario_model_experiment(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    config_path: str | Path,
    scopes: Sequence[str] | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    config = Path(config_path)
    if not config.is_absolute():
        config = root / config
    contract = load_scenario_contract(config)
    requested = tuple(scopes or contract["scopes"].keys())
    unknown = sorted(set(requested).difference(contract["scopes"]))
    if unknown:
        raise ValueError(f"scenario_model_scope:{','.join(unknown)}")
    results: list[dict[str, Any]] = []
    for scope in requested:
        scope_contract = contract["scopes"][scope]
        market = str(scope_contract["market"])
        feature_path = root / "data/research/features" / market / f"{snapshot_date}.parquet"
        label_path = root / "data/research/labels" / market / f"{snapshot_date}.parquet"
        dataset = _scope_dataset(
            feature_path, label_path, scope=scope,
            horizon=int(scope_contract["horizon"]),
            development_end=str(contract["development_end"]),
        )
        baseline = yaml.safe_load(
            (root / f"configs/competition_{market}.yaml").read_text(encoding="utf-8")
        )
        baseline["accounts"] = [
            account for account in baseline.get("accounts") or []
            if str(account.get("id") or "") == scope
        ]
        results.append(evaluate_scope(
            dataset, contract=contract, scope=scope, portfolio_contract=baseline
        ))
    payload = {
        "status": "complete",
        "protocol": contract["protocol"],
        "contract_sha256": contract["contract_sha256"],
        "snapshot_date": str(snapshot_date),
        "development_end": contract["development_end"],
        "historical_test_opened": False,
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "results": results,
    }
    destination = root / "data/research/scenario_models" / str(snapshot_date) / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return {**payload, "report_path": str(destination)}


__all__ = [
    "ABLATIONS", "SCENES", "classify_scenes", "evaluate_scope",
    "load_scenario_contract", "run_scenario_model_experiment",
]
