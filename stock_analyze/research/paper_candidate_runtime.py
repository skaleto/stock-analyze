"""Four isolated forward paper challengers with immutable provenance.

This runtime deliberately bypasses the model Registry and the four formal
``claude``/``codex`` accounts.  Each challenger owns one versioned
``PortfolioStore`` and consumes only an exact-date immutable feature snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler

from .. import competition
from ..model_shadow import run_shadow_cycle, synthetic_config
from ..run_ledger import RunLedger
from ..store import PortfolioStore
from ..utils import now_iso, read_json, write_json
from .account_features import build_account_feature_view, date_balanced_sample_weights
from .classical_specs import transparent_strategy_specs
from .models import score_transparent_strategy
from .scenario_model import SCENES, _scope_dataset, classify_scenes
from .storage import ResearchStore

DEFAULT_CONTRACT = Path("configs/research/production_paper_challengers_v1.yaml")
STATUS_FILE = "account_status.json"
AGGREGATE_STATUS = Path("data/research/paper_portfolios/current_status.json")
_ALLOWED_MARKETS = ("a_share", "cn_qdii_etf")
_EXPECTED_ACCOUNTS = {
    "a_share": ("hs300", "zz500"),
    "cn_qdii_etf": ("hk_exposure", "us_exposure"),
}
_A_REQUIRED_COLUMNS = (
    "code", "trade_date", "account_id", "research_scope", "close",
    "adjusted_close", "adjusted_low", "breakout_20", "momentum_20",
    "natr_14", "industry", "is_st", "is_suspended", "is_tradable",
)
_Q_REQUIRED_COLUMNS = (
    "code", "trade_date", "account_id", "research_scope", "close", "amount",
    "avg_amount_20", "momentum_20", "momentum_60", "momentum_120",
    "sma_distance_200", "realized_volatility_20", "natr_14",
    "nav_momentum_20", "discount_premium", "tracking_error_20",
    "global_index_momentum", "industry",
)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_key(value: object) -> str:
    key = str(value or "").replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"paper_candidate_date:{value}")
    return key


def _iso_date(value: object) -> str:
    key = _date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:]}"


def _root_path(root: Path, value: object) -> Path:
    path = Path(str(value))
    resolved = path if path.is_absolute() else root / path
    try:
        resolved.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"paper_candidate_path_outside_root:{value}") from exc
    return resolved


def load_paper_candidate_contract(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("paper_candidate_runtime_contract")
    if payload.get("protocol") != "production-paper-challengers-v1":
        raise ValueError("paper_candidate_runtime_protocol")
    if payload.get("paper_trading_only") is not True:
        raise ValueError("paper_candidate_runtime_real_trading_forbidden")
    if payload.get("formal_strategy_activated") is not False:
        raise ValueError("paper_candidate_runtime_formal_boundary")
    if payload.get("registry_mutation_allowed") is not False:
        raise ValueError("paper_candidate_runtime_registry_boundary")
    if payload.get("historical_return_selection_allowed") is not False:
        raise ValueError("paper_candidate_runtime_historical_selection")
    markets = payload.get("markets") or {}
    if tuple(markets) != _ALLOWED_MARKETS:
        raise ValueError("paper_candidate_runtime_markets")
    seen: set[str] = set()
    for market, expected in _EXPECTED_ACCOUNTS.items():
        block = markets.get(market) or {}
        accounts = block.get("accounts") or {}
        if tuple(accounts) != expected:
            raise ValueError(f"paper_candidate_runtime_accounts:{market}")
        if abs(
            sum(float(row.get("cash") or 0.0) for row in accounts.values())
            - float(block.get("initial_cash") or 0.0)
        ) > 0.01:
            raise ValueError(f"paper_candidate_runtime_cash:{market}")
        for account_id, row in accounts.items():
            if account_id in seen or str(row.get("scope")) != account_id:
                raise ValueError(f"paper_candidate_runtime_scope:{account_id}")
            seen.add(account_id)
            if str(row.get("evidence_status")) not in {
                "qualified_candidate", "transparent_challenger",
            }:
                raise ValueError(f"paper_candidate_runtime_evidence:{account_id}")
            if int(row.get("minimum_current_members") or 0) <= 0:
                raise ValueError(f"paper_candidate_runtime_minimum_members:{account_id}")
    scenario_hash = str(payload.get("scenario_contract_sha256") or "")
    if len(scenario_hash) != 64:
        raise ValueError("paper_candidate_runtime_scenario_hash")
    runtime = payload.get("runtime") or {}
    if runtime.get("signal_date_must_equal_snapshot_date") is not True:
        raise ValueError("paper_candidate_runtime_freshness")
    stop = runtime.get("automatic_stop") or {}
    if not 0.0 < float(stop.get("maximum_portfolio_drawdown") or 0.0) < 1.0:
        raise ValueError("paper_candidate_runtime_stop_drawdown")
    return {**payload, "contract_sha256": _canonical_hash(payload)}


def _resolve_contract(root: Path, contract_path: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(contract_path)
    if not path.is_absolute():
        path = root / path
    return path, load_paper_candidate_contract(path)


def _market_profile(contract: Mapping[str, Any], market: str, account_id: str) -> dict[str, Any]:
    block = dict((contract.get("markets") or {})[market])
    account = dict((block.get("accounts") or {})[account_id])
    return {
        "version": 1,
        "source_agent": "paper_candidate_runtime",
        "market": market,
        "horizon": int(block["horizon"]),
        "initial_cash": float(account["cash"]),
        "accounts": [{"id": account_id, **account}],
        "account_id": account_id,
        "top_n": int(account["top_n"]),
        "max_single_weight": float(block["max_single_weight"]),
        "cash_reserve_pct": float(block.get("cash_reserve_pct") or 0.0),
        "min_trade_weight": float(block.get("min_trade_weight") or 0.0),
        "trading": dict(block.get("trading") or {}),
        "automatic_stop": dict((contract.get("runtime") or {}).get("automatic_stop") or {}),
    }


def paper_portfolio_dir(
    repo_root: str | Path,
    contract: Mapping[str, Any],
    market: str,
    account_id: str,
) -> Path:
    return (
        _root_path(Path(repo_root).resolve(), contract["data_root"])
        / market / account_id / str(contract["version"])
    )


def paper_artifact_path(
    repo_root: str | Path,
    contract: Mapping[str, Any],
    market: str,
    account_id: str,
) -> Path:
    return (
        _root_path(Path(repo_root).resolve(), contract["artifact_root"])
        / market / account_id / str(contract["version"]) / "artifact.json"
    )


def _verify_evidence(root: Path, contract: Mapping[str, Any]) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for key, raw in (contract.get("source_evidence") or {}).items():
        if key.endswith("_sha256"):
            continue
        path = _root_path(root, raw)
        expected = str((contract.get("source_evidence") or {}).get(f"{key}_sha256") or "")
        if not path.is_file():
            raise FileNotFoundError(f"paper_candidate_evidence_missing:{key}:{path}")
        if len(expected) != 64 or _file_sha256(path) != expected:
            raise ValueError(f"paper_candidate_evidence_hash:{key}")
        resolved[key] = path
    ledger = json.loads(resolved["qualification_ledger"].read_text(encoding="utf-8"))
    qualified = [
        row for row in ledger.get("decisions") or []
        if row.get("scope") == "hk_exposure"
        and row.get("qualified_for_isolated_paper") is True
        and row.get("status") == "qualified"
    ]
    if len(qualified) != 1:
        raise ValueError("paper_candidate_hk_qualification")
    report = json.loads(resolved["scenario_report"].read_text(encoding="utf-8"))
    if report.get("historical_test_opened") is not False:
        raise ValueError("paper_candidate_historical_window_opened")
    return resolved


def _fit_expert(frame: pd.DataFrame, features: Sequence[str], parameters: Mapping[str, Any], random_state: int) -> dict[str, Any]:
    medians = {
        column: float(pd.to_numeric(frame[column], errors="coerce").median())
        for column in features
    }
    if any(not math.isfinite(value) for value in medians.values()):
        raise ValueError("paper_candidate_artifact_imputation")
    matrix = np.column_stack([
        pd.to_numeric(frame[column], errors="coerce").fillna(medians[column])
        for column in features
    ])
    target = pd.to_numeric(frame["excess_return"], errors="coerce")
    valid = target.notna()
    if not bool(valid.any()):
        raise ValueError("paper_candidate_artifact_target")
    matrix = matrix[valid.to_numpy()]
    target = target.loc[valid]
    training = frame.loc[valid]
    weights = date_balanced_sample_weights(training).to_numpy(dtype=float)
    scaler = StandardScaler().fit(matrix, sample_weight=weights)
    model = ElasticNet(
        alpha=float(parameters["alpha"]),
        l1_ratio=float(parameters["l1_ratio"]),
        max_iter=5000,
        random_state=int(random_state),
    ).fit(scaler.transform(matrix), target.to_numpy(dtype=float), sample_weight=weights)
    return {
        "feature_medians": medians,
        "scaler_mean": scaler.mean_.astype(float).tolist(),
        "scaler_scale": scaler.scale_.astype(float).tolist(),
        "coefficients": model.coef_.astype(float).tolist(),
        "intercept": float(model.intercept_),
        "training_rows": int(len(training)),
        "training_dates": int(training["trade_date"].astype(str).nunique()),
    }


def freeze_hk_candidate_artifact(
    repo_root: str | Path,
    *,
    contract_path: str | Path = DEFAULT_CONTRACT,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    _path, contract = _resolve_contract(root, contract_path)
    destination = paper_artifact_path(root, contract, "cn_qdii_etf", "hk_exposure")
    if destination.exists() and not force:
        return load_hk_candidate_artifact(root, contract_path=contract_path)
    evidence = _verify_evidence(root, contract)
    scenario_path = _root_path(root, contract["scenario_contract"])
    if _file_sha256(scenario_path) != str(contract["scenario_contract_sha256"]):
        raise ValueError("paper_candidate_scenario_contract_hash")
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    scope = dict(scenario["scopes"]["hk_exposure"])
    features = tuple(str(value) for value in scope["features"])
    dataset = _scope_dataset(
        evidence["development_features"], evidence["development_labels"],
        scope="hk_exposure", horizon=int(scope["horizon"]),
        development_end=str(contract["development_end"]),
    )
    dataset = dataset.loc[
        dataset["label_end_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        .le(_date_key(contract["development_end"]))
    ].copy()
    routed = classify_scenes(dataset, scenario["router"])
    experts: dict[str, Any] = {}
    for scene in SCENES:
        training = routed.loc[routed["scene"].eq(scene)].copy()
        if training["trade_date"].astype(str).nunique() < int(scenario["minimum_scene_training_dates"]):
            raise ValueError(f"paper_candidate_artifact_scene_dates:{scene}")
        experts[scene] = _fit_expert(
            training, features, scenario["model"]["parameters"],
            int(scenario["random_state"]),
        )
    baseline = next(
        spec for spec in transparent_strategy_specs("cn_qdii_etf", "hk_exposure")
        if spec.spec_id == scope["baseline_spec_id"]
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "qualified_scenario_specialists",
        "version": str(contract["version"]),
        "market": "cn_qdii_etf",
        "account_id": "hk_exposure",
        "evidence_status": "qualified_candidate",
        "development_start": str(scenario["source_start"]),
        "development_end": str(contract["development_end"]),
        "feature_columns": list(features),
        "estimator": "elastic_net",
        "parameters": dict(scenario["model"]["parameters"]),
        "random_state": int(scenario["random_state"]),
        "residual_weight": float(scenario["residual_weight"]),
        "baseline_spec_id": baseline.spec_id,
        "baseline_spec_hash": baseline.spec_hash,
        "router": dict(scenario["router"]),
        "scene_exposure": dict(scope["exposure"]),
        "experts": experts,
        "source_hashes": {
            key: str((contract.get("source_evidence") or {})[f"{key}_sha256"])
            for key in evidence
        },
        "scenario_contract_sha256": _file_sha256(scenario_path),
        "runtime_contract_sha256": contract["contract_sha256"],
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "fitted_at": now_iso(),
    }
    payload["artifact_sha256"] = _canonical_hash(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(destination)
    return {**payload, "artifact_path": str(destination)}


def load_hk_candidate_artifact(
    repo_root: str | Path,
    *,
    contract_path: str | Path = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    _path, contract = _resolve_contract(root, contract_path)
    path = paper_artifact_path(root, contract, "cn_qdii_etf", "hk_exposure")
    payload = json.loads(path.read_text(encoding="utf-8"))
    observed = str(payload.pop("artifact_sha256", ""))
    expected = _canonical_hash(payload)
    payload["artifact_sha256"] = observed
    if observed != expected:
        raise ValueError("paper_candidate_artifact_hash")
    if payload.get("runtime_contract_sha256") != contract["contract_sha256"]:
        raise ValueError("paper_candidate_artifact_contract")
    if payload.get("development_end") != str(contract["development_end"]):
        raise ValueError("paper_candidate_artifact_development_end")
    return {**payload, "artifact_path": str(path)}


def _predict_expert(frame: pd.DataFrame, artifact: Mapping[str, Any], scene: str) -> np.ndarray:
    expert = (artifact.get("experts") or {}).get(scene)
    if not isinstance(expert, Mapping):
        raise ValueError(f"paper_candidate_artifact_scene:{scene}")
    features = tuple(str(value) for value in artifact["feature_columns"])
    medians = expert["feature_medians"]
    matrix = np.column_stack([
        pd.to_numeric(frame[column], errors="coerce").fillna(float(medians[column]))
        for column in features
    ])
    mean = np.asarray(expert["scaler_mean"], dtype=float)
    scale = np.asarray(expert["scaler_scale"], dtype=float)
    coefficients = np.asarray(expert["coefficients"], dtype=float)
    return ((matrix - mean) / np.where(scale == 0.0, 1.0, scale)) @ coefficients + float(expert["intercept"])


def _baseline_spec(market: str, scope: str, spec_id: str) -> Any:
    matches = [
        spec for spec in transparent_strategy_specs(market, scope)
        if spec.spec_id == spec_id
    ]
    if len(matches) != 1:
        raise ValueError(f"paper_candidate_baseline:{scope}:{spec_id}")
    return matches[0]


def _read_feature_columns(path: Path, desired: Sequence[str]) -> pd.DataFrame:
    import pyarrow.parquet as pq

    schema = set(pq.ParquetFile(path).schema_arrow.names)
    required = {"code", "trade_date", "account_id", "research_scope", "close"}
    missing = sorted(required.difference(schema))
    if missing:
        raise ValueError(f"paper_candidate_feature_schema:{','.join(missing)}")
    columns = [column for column in desired if column in schema]
    frame = pd.read_parquet(path, columns=columns)
    frame["code"] = frame["code"].astype(str).str.split(".").str[0].str.zfill(6)
    frame["trade_date"] = (
        frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    )
    return frame


def _validate_current_scope(
    frame: pd.DataFrame,
    *,
    account: Mapping[str, Any],
    snapshot_date: str,
    required_features: Sequence[str],
    minimum_coverage: float,
) -> pd.DataFrame:
    scope = str(account["scope"])
    latest = frame.loc[
        frame["trade_date"].eq(snapshot_date)
        & frame["research_scope"].astype(str).eq(scope)
    ].copy()
    if len(latest) < int(account["minimum_current_members"]):
        raise ValueError(
            f"paper_candidate_scope_incomplete:{scope}:rows={len(latest)}:"
            f"required={int(account['minimum_current_members'])}"
        )
    duplicate = latest.duplicated("code", keep=False)
    if bool(duplicate.any()):
        raise ValueError(f"paper_candidate_scope_duplicate:{scope}")
    coverage = {
        column: (
            float(pd.to_numeric(latest[column], errors="coerce").notna().mean())
            if column in latest.columns else 0.0
        )
        for column in required_features
    }
    weak = sorted(column for column, value in coverage.items() if value < minimum_coverage)
    if weak:
        raise ValueError(
            f"paper_candidate_feature_coverage:{scope}:{','.join(weak)}"
        )
    return latest


def build_a_share_donchian_signals(
    frame: pd.DataFrame,
    *,
    account: Mapping[str, Any],
    state: Mapping[str, Any],
    snapshot_date: str,
    horizon: int = 20,
    minimum_coverage: float = 0.80,
) -> pd.DataFrame:
    """Frozen long-only 20-day entry / 10-day exit forward challenger."""

    scope = str(account["scope"])
    scoped = frame.loc[frame["research_scope"].astype(str).eq(scope)].copy()
    scoped = scoped.sort_values(["code", "trade_date"], kind="stable")
    low = pd.to_numeric(scoped["adjusted_low"], errors="coerce")
    scoped["donchian_exit_10"] = pd.to_numeric(
        scoped["adjusted_close"], errors="coerce"
    ).lt(low.groupby(scoped["code"], sort=False).transform(
        lambda values: values.shift(1).rolling(10, min_periods=10).min()
    ))
    latest = _validate_current_scope(
        scoped, account=account, snapshot_date=snapshot_date,
        required_features=("adjusted_close", "adjusted_low", "breakout_20", "momentum_20", "natr_14"),
        minimum_coverage=minimum_coverage,
    )
    account_state = (state.get("accounts") or {}).get(str(account["id"]), {})
    held = {str(code).split(".")[0].zfill(6) for code in (account_state.get("positions") or {})}
    breakout = pd.to_numeric(latest["breakout_20"], errors="coerce").gt(0.0)
    st = pd.to_numeric(latest.get("is_st"), errors="coerce").fillna(0.0).ne(0.0)
    suspended = pd.to_numeric(latest.get("is_suspended"), errors="coerce").fillna(0.0).ne(0.0)
    tradable = latest.get("is_tradable", pd.Series(True, index=latest.index)).fillna(True).astype(bool)
    latest["hard_risk_exit"] = latest["code"].isin(held) & (
        latest["donchian_exit_10"].fillna(False).astype(bool) | st
    )
    latest["rule_eligible"] = (
        (breakout & ~st & ~suspended & tradable)
        | latest["code"].isin(held)
    )
    momentum = pd.to_numeric(latest["momentum_20"], errors="coerce")
    volatility = pd.to_numeric(latest["natr_14"], errors="coerce").abs().clip(lower=0.25)
    latest["score"] = momentum / volatility
    if held:
        held_mask = latest["code"].isin(held) & ~latest["hard_risk_exit"]
        latest.loc[held_mask, "score"] = 1_000_000.0 + latest.loc[held_mask, "score"].fillna(0.0)
    output = latest.loc[latest["rule_eligible"]].copy()
    if output.empty:
        output = latest.nlargest(1, "score").copy()
        output["rule_eligible"] = True
        output["target_risky_exposure"] = 0.0
        output["_is_cash_placeholder"] = True
    else:
        output["target_risky_exposure"] = 1.0
        output["_is_cash_placeholder"] = False
    output["signal_kind"] = "transparent_rule"
    output["horizon"] = int(horizon)
    output["spec_id"] = str(account["strategy_id"])
    output["spec_hash"] = _canonical_hash({
        "entry": "adjusted_close_above_prior_20_session_high",
        "exit": "adjusted_close_below_prior_10_session_low",
        "ranking": "momentum_20_divided_by_natr_14",
        "top_n": int(account["top_n"]),
    })
    output["rebalance_frequency"] = "daily"
    output["reason"] = np.where(
        output["hard_risk_exit"].fillna(False), "donchian_exit_10",
        np.where(output["code"].isin(held), "donchian_hold_until_exit", "donchian_entry_20"),
    )
    return output.reset_index(drop=True)


def build_qdii_scene_signals(
    frame: pd.DataFrame,
    *,
    account: Mapping[str, Any],
    scenario_contract: Mapping[str, Any],
    snapshot_date: str,
    minimum_coverage: float,
    artifact: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    scope = str(account["scope"])
    view = build_account_feature_view(frame, account_scope=scope)
    routed = classify_scenes(view, scenario_contract["router"])
    scope_contract = dict(scenario_contract["scopes"][scope])
    features = tuple(str(value) for value in scope_contract["features"])
    current = _validate_current_scope(
        routed, account=account, snapshot_date=snapshot_date,
        required_features=features if artifact is not None else (
            "momentum_60", "momentum_120", "sma_distance_200",
            "discount_premium", "tracking_error_20", "account_liquidity_percentile",
        ),
        minimum_coverage=minimum_coverage,
    )
    spec = _baseline_spec("cn_qdii_etf", scope, str(account["baseline_spec_id"]))
    scored = score_transparent_strategy(current, spec)
    if artifact is not None:
        residual = np.zeros(len(scored), dtype=float)
        for scene in SCENES:
            mask = scored["scene"].eq(scene).to_numpy()
            if bool(mask.any()):
                residual[mask] = _predict_expert(scored.loc[mask], artifact, scene)
        baseline_rank = pd.to_numeric(scored["score"], errors="coerce").rank(
            pct=True, method="average"
        ).fillna(0.5)
        residual_rank = pd.Series(residual, index=scored.index).rank(
            pct=True, method="average"
        ).fillna(0.5)
        weight = float(artifact["residual_weight"])
        scored["baseline_score"] = scored["score"]
        scored["model_residual"] = residual
        scored["score"] = (1.0 - weight) * baseline_rank + weight * residual_rank
    base_exposure = pd.to_numeric(
        scored.get("_target_risky_exposure", 1.0), errors="coerce"
    ).fillna(0.0)
    caps = scored["scene"].map({
        key: float(value) for key, value in scope_contract["exposure"].items()
    })
    scored["target_risky_exposure"] = np.minimum(base_exposure, caps)
    scored["rule_eligible"] = pd.to_numeric(scored["score"], errors="coerce").notna()
    scored["signal_kind"] = "transparent_rule"
    scored["horizon"] = int(scope_contract["horizon"])
    scored["spec_id"] = str(account["strategy_id"])
    scored["spec_hash"] = (
        str(artifact["artifact_sha256"]) if artifact is not None
        else _canonical_hash({
            "baseline_spec_hash": spec.spec_hash,
            "router": scenario_contract["router"],
            "exposure": scope_contract["exposure"],
        })
    )
    scored["model_version"] = (
        str(artifact["version"]) if artifact is not None else "none-transparent-router"
    )
    scored["rebalance_frequency"] = "weekly"
    scored["reason"] = scored["scene"].map(
        lambda value: (
            f"qualified_scenario_specialist:{value}"
            if artifact is not None else f"transparent_scene_router:{value}"
        )
    )
    return scored.reset_index(drop=True)


def _portfolio_drawdown(store: PortfolioStore, account_id: str) -> float:
    nav = store.read_nav()
    if nav.empty or "total_value" not in nav.columns:
        return 0.0
    if "account_id" in nav.columns:
        nav = nav.loc[nav["account_id"].astype(str).eq(account_id)]
    values = pd.to_numeric(nav["total_value"], errors="coerce").dropna()
    if values.empty:
        return 0.0
    peak = float(values.max())
    return 0.0 if peak <= 0.0 else max(0.0, 1.0 - float(values.iloc[-1]) / peak)


def _force_liquidation(
    signals: pd.DataFrame,
    *,
    store: PortfolioStore,
    account_id: str,
) -> pd.DataFrame:
    output = signals.copy()
    held = {
        str(code).split(".")[0].zfill(6)
        for code in (
            (store.load_state().get("accounts") or {})
            .get(account_id, {}).get("positions", {})
        )
    }
    # Positions that left the current universe are intentionally absent from
    # ``output``.  The mechanical transition unions current positions with the
    # empty desired portfolio and therefore still creates their exit orders.
    output["rule_eligible"] = True
    output["target_risky_exposure"] = 0.0
    output["hard_risk_exit"] = output["code"].astype(str).isin(held)
    output["score"] = pd.to_numeric(output.get("score"), errors="coerce").fillna(0.0)
    output.loc[output["hard_risk_exit"], "score"] = 1_000_000_000.0
    output["reason"] = "automatic_drawdown_stop"
    return output


def _snapshot_path(root: Path, market: str, snapshot_date: str) -> Path:
    path = root / "data/research/features" / market / f"{snapshot_date}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"paper_candidate_snapshot_missing:{market}:{snapshot_date}")
    return path


def _current_snapshot_date(root: Path, market: str, as_of: str | None) -> str:
    cutoff = _date_key(as_of or date.today().isoformat())
    observed = ResearchStore(root / "data/research").latest_feature_snapshot_date(
        market, as_of=cutoff
    )
    if as_of is not None and observed != cutoff:
        raise ValueError(
            f"paper_candidate_snapshot_stale:{market}:expected={cutoff}:observed={observed}"
        )
    return observed


def _account_contract(
    contract: Mapping[str, Any], market: str, account_id: str
) -> dict[str, Any]:
    market_block = dict(contract["markets"][market])
    account = dict(market_block["accounts"][account_id])
    return {
        "account": {"id": account_id, **account},
        "trading": {
            **dict(market_block.get("trading") or {}),
            "max_single_weight": float(market_block["max_single_weight"]),
        },
        "rule_execution_policy": dict(market_block["rule_execution_policy"]),
        "rebalance_frequency": (
            "daily" if account["strategy_kind"] == "a_share_donchian" else "weekly"
        ),
        "evidence_status": account["evidence_status"],
        "strategy_kind": account["strategy_kind"],
        "strategy_id": account["strategy_id"],
    }


def _settle_account(
    *,
    market: str,
    profile: Mapping[str, Any],
    store: PortfolioStore,
    provider: Any,
    as_of: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = synthetic_config(profile)
    market_module = competition.get_market_module(market)
    if not store.state_path.exists():
        market_module.initialize(config, store)
    trades = market_module.execute_due_orders(config, store, provider, as_of=as_of)
    nav = market_module.update_nav(
        config, store, provider, as_of=as_of,
        notes=f"paper challenger; trades={len(trades)}",
    )
    return trades, nav


def _run_account(
    root: Path,
    *,
    contract_path: Path,
    contract: Mapping[str, Any],
    scenario: Mapping[str, Any],
    market: str,
    account_id: str,
    snapshot_date: str,
    offline: bool,
) -> dict[str, Any]:
    account = dict(contract["markets"][market]["accounts"][account_id])
    profile = _market_profile(contract, market, account_id)
    data_dir = paper_portfolio_dir(root, contract, market, account_id)
    store = PortfolioStore(data_dir)
    cache_dir = (
        root / "data/shared/cache" if market == "a_share"
        else root / "data/cn_qdii_etf/shared/cache"
    )
    as_of = _iso_date(snapshot_date)
    provider = competition.get_market_module(market).make_provider(
        cache_dir=cache_dir, offline=offline, as_of=as_of
    )
    config = synthetic_config(profile)
    ledger = RunLedger(data_dir)
    previous = read_json(data_dir / STATUS_FILE, {})
    try:
        with ledger.run("run-paper-candidate", as_of, config) as context:
            trades, nav_rows = _settle_account(
                market=market, profile=profile, store=store,
                provider=provider, as_of=as_of,
            )
            snapshot = _snapshot_path(root, market, snapshot_date)
            minimum_coverage = float(contract["runtime"]["current_feature_minimum_coverage"])
            if market == "a_share":
                frame = _read_feature_columns(snapshot, _A_REQUIRED_COLUMNS)
                signals = build_a_share_donchian_signals(
                    frame, account={"id": account_id, **account},
                    state=store.load_state(), snapshot_date=snapshot_date,
                    horizon=int(profile["horizon"]), minimum_coverage=minimum_coverage,
                )
                artifact = None
            else:
                frame = _read_feature_columns(snapshot, _Q_REQUIRED_COLUMNS)
                artifact = None
                if account["strategy_kind"] == "qualified_scenario_specialists":
                    try:
                        artifact = load_hk_candidate_artifact(
                            root, contract_path=contract_path
                        )
                    except FileNotFoundError:
                        artifact = freeze_hk_candidate_artifact(
                            root, contract_path=contract_path
                        )
                signals = build_qdii_scene_signals(
                    frame, account={"id": account_id, **account},
                    scenario_contract=scenario, snapshot_date=snapshot_date,
                    minimum_coverage=minimum_coverage, artifact=artifact,
                )
            drawdown = _portfolio_drawdown(store, account_id)
            stop_limit = float(profile["automatic_stop"]["maximum_portfolio_drawdown"])
            stop_triggered = drawdown >= stop_limit
            if stop_triggered:
                signals = _force_liquidation(
                    signals, store=store, account_id=account_id
                )
            result = run_shadow_cycle(
                market=market, profile=profile, store=store, provider=provider,
                predictions=signals, as_of=as_of, prediction_as_of=as_of,
                run_id=context["run_id"],
                account_contracts={
                    account_id: _account_contract(contract, market, account_id)
                },
                settle=False, preexecuted_trades=trades, prenav_rows=nav_rows,
            )
            status = {
                **result,
                "schema_version": 1,
                "status": "complete",
                "protocol": contract["protocol"],
                "contract_version": contract["version"],
                "contract_sha256": contract["contract_sha256"],
                "market": market,
                "account_id": account_id,
                "scope": account["scope"],
                "benchmark": str(account["benchmark"]),
                "evidence_status": account["evidence_status"],
                "strategy_kind": account["strategy_kind"],
                "strategy_id": account["strategy_id"],
                "snapshot_date": snapshot_date,
                "snapshot_path": str(snapshot),
                "snapshot_sha256": _file_sha256(snapshot),
                "artifact_path": artifact.get("artifact_path") if artifact else None,
                "artifact_sha256": artifact.get("artifact_sha256") if artifact else None,
                "portfolio_path": str(data_dir),
                "formal_strategy_activated": False,
                "registry_mutated": False,
                "drawdown": drawdown,
                "automatic_stop_limit": stop_limit,
                "automatic_stop_triggered": stop_triggered,
                "consecutive_failures": 0,
                "updated_at": now_iso(),
            }
            write_json(data_dir / STATUS_FILE, status)
            return status
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "status": "failed_closed",
            "protocol": contract["protocol"],
            "contract_version": contract["version"],
            "contract_sha256": contract["contract_sha256"],
            "market": market,
            "account_id": account_id,
            "scope": account["scope"],
            "benchmark": str(account["benchmark"]),
            "evidence_status": account["evidence_status"],
            "strategy_kind": account["strategy_kind"],
            "strategy_id": account["strategy_id"],
            "snapshot_date": snapshot_date,
            "portfolio_path": str(data_dir),
            "formal_strategy_activated": False,
            "registry_mutated": False,
            "execution_suspended": True,
            "cash_reason": "input_or_contract_validation_failed",
            "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"[:300],
            "consecutive_failures": int(previous.get("consecutive_failures") or 0) + 1,
            "updated_at": now_iso(),
        }
        write_json(data_dir / STATUS_FILE, failure)
        return failure
    finally:
        persist = getattr(provider, "persist_health", None)
        if callable(persist):
            persist()


def run_production_paper_challengers(
    repo_root: str | Path,
    *,
    contract_path: str | Path = DEFAULT_CONTRACT,
    markets: str | Sequence[str] = "all",
    as_of: str | None = None,
    offline: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path, contract = _resolve_contract(root, contract_path)
    selected_markets = (
        list(_ALLOWED_MARKETS) if markets == "all"
        else [str(markets)] if isinstance(markets, str)
        else [str(value) for value in markets]
    )
    unknown = sorted(set(selected_markets).difference(_ALLOWED_MARKETS))
    if unknown:
        raise ValueError(f"paper_candidate_markets:{','.join(unknown)}")
    scenario_path = _root_path(root, contract["scenario_contract"])
    if _file_sha256(scenario_path) != str(contract["scenario_contract_sha256"]):
        raise ValueError("paper_candidate_scenario_contract_hash")
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for market in selected_markets:
        try:
            snapshot_date = _current_snapshot_date(root, market, as_of)
        except Exception as exc:
            for account_id in _EXPECTED_ACCOUNTS[market]:
                account = contract["markets"][market]["accounts"][account_id]
                results.append({
                    "status": "failed_closed", "market": market,
                    "account_id": account_id, "scope": account["scope"],
                    "evidence_status": account["evidence_status"],
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "formal_strategy_activated": False,
                    "registry_mutated": False,
                })
            continue
        for account_id in _EXPECTED_ACCOUNTS[market]:
            results.append(_run_account(
                root, contract_path=path, contract=contract, scenario=scenario,
                market=market, account_id=account_id,
                snapshot_date=snapshot_date, offline=offline,
            ))
    complete = sum(row.get("status") == "complete" for row in results)
    payload = {
        "schema_version": 1,
        "status": "complete" if complete == len(results) else "failed_closed",
        "protocol": contract["protocol"],
        "contract_version": contract["version"],
        "contract_sha256": contract["contract_sha256"],
        "accounts_expected": len(results),
        "accounts_complete": complete,
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "accounts": results,
        "updated_at": now_iso(),
    }
    destination = root / AGGREGATE_STATUS
    write_json(destination, payload)
    return {**payload, "status_path": str(destination)}


__all__ = [
    "DEFAULT_CONTRACT", "build_a_share_donchian_signals",
    "build_qdii_scene_signals", "freeze_hk_candidate_artifact",
    "load_hk_candidate_artifact", "load_paper_candidate_contract",
    "paper_artifact_path", "paper_portfolio_dir",
    "run_production_paper_challengers",
]
