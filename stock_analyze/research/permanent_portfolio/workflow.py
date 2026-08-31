"""Development, holdout, and immutable evidence workflow."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any

import pandas as pd

from ...utils import write_text_atomic
from .contract import (
    PermanentPortfolioContract,
    canonical_hash,
    load_contract,
)
from .data import load_market_publication
from .engine import ReplayResult, replay_strategy
from .metrics import calculate_metrics, rolling_series
from .signals import dynamic_target_weights, fixed_target_weights


STORE_RELATIVE = Path("data/research/permanent_portfolio/v1")
REPORT_RELATIVE = Path("reports/research/permanent_portfolio/v1/dashboard.json")
PORTFOLIOS = (
    "fixed",
    "dynamic",
    "equity_buy_hold",
    "equal_weight_buy_hold",
    "cash_buy_hold",
)


def _date_key(value: Any) -> str:
    key = str(value).replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"permanent_portfolio_date:{value}")
    return key


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _json_value(value.item())
    raise ValueError(f"permanent_portfolio_json:{type(value).__name__}")


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        _json_value(record)
        for record in frame.to_dict(orient="records")
    ]


def _with_roles(
    market: pd.DataFrame,
    contract: PermanentPortfolioContract,
) -> pd.DataFrame:
    frame = market.copy()
    frame["trade_date"] = frame["trade_date"].map(_date_key)
    if "role" not in frame.columns:
        role_by_code = {asset.code: asset.role for asset in contract.assets}
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["role"] = frame["ts_code"].map(role_by_code)
        frame["code"] = frame["ts_code"]
    elif "code" not in frame.columns:
        code_by_role = {asset.role: asset.code for asset in contract.assets}
        frame["code"] = frame["role"].map(code_by_role)
    if frame["role"].isna().any() or frame["code"].isna().any():
        raise ValueError("permanent_portfolio_market_roles")
    return frame


def _momentum_observations(
    market: pd.DataFrame,
    *,
    as_of: str,
) -> pd.DataFrame:
    timestamp = pd.Timestamp(as_of)
    rows: list[dict[str, Any]] = []
    dated = market.copy()
    if "is_open" in dated.columns:
        dated = dated.loc[dated["is_open"].astype(bool)].copy()
    dated["_date"] = pd.to_datetime(dated["trade_date"], format="%Y%m%d")
    for months_ago in (12, 6, 1, 0):
        cutoff = timestamp - pd.DateOffset(months=months_ago)
        eligible = dated.loc[dated["_date"].le(cutoff)]
        for role, group in eligible.groupby("role", sort=False):
            latest = group.sort_values("_date").iloc[-1]
            rows.append(
                {
                    "role": role,
                    "months_ago": months_ago,
                    "adjusted_close": latest["adjusted_close"],
                }
            )
    return pd.DataFrame(rows)


def _cost_summary(result: ReplayResult) -> tuple[float, float, int]:
    if result.trades.empty:
        return 0.0, 0.0, 0
    turnover = float(result.trades["gross_amount"].sum())
    cost = float(
        result.trades[["commission", "stamp_tax", "slippage"]]
        .sum(axis=1)
        .sum()
    )
    return turnover, cost, int(len(result.trades))


def _portfolio_payload(
    result: ReplayResult,
    *,
    cash_nav: pd.DataFrame,
) -> dict[str, Any]:
    nav = result.nav.merge(
        cash_nav[["date", "total_value"]].rename(
            columns={"total_value": "cash_benchmark_value"}
        ),
        on="date",
        how="left",
        validate="one_to_one",
    )
    turnover, cost, count = _cost_summary(result)
    series = rolling_series(nav)
    return {
        "metrics": calculate_metrics(
            nav,
            total_turnover=turnover,
            total_cost=cost,
            trade_count=count,
        ),
        "nav": _records(result.nav),
        "series": _records(series),
        "trades": _records(result.trades),
        "targets": _records(result.targets),
        "positions": _records(result.positions),
        "pending": _records(result.pending),
    }


def evaluate_window(
    market: pd.DataFrame,
    *,
    contract: PermanentPortfolioContract,
    start_date: str,
    end_date: str,
    cost_multiplier: float = 1.0,
) -> dict[str, Any]:
    start_key = _date_key(start_date)
    end_key = _date_key(end_date)
    full = _with_roles(market, contract)
    window = full.loc[
        full["trade_date"].between(start_key, end_key)
    ].copy()
    if window.empty or set(window["role"]) != {
        asset.role for asset in contract.assets
    }:
        raise ValueError("permanent_portfolio_evaluation_window")
    first_date = window["trade_date"].min()
    initial_signal = (
        pd.Timestamp(first_date) - pd.Timedelta(days=1)
    ).strftime("%Y%m%d")
    pre_window = full.loc[full["trade_date"].lt(first_date)]
    if pre_window.empty:
        raise ValueError("permanent_portfolio_momentum_warmup")
    initial_dynamic = dynamic_target_weights(
        _momentum_observations(
            full,
            as_of=initial_signal,
        )
    )
    month_ends = set(
        window.assign(
            _month=pd.to_datetime(
                window["trade_date"],
                format="%Y%m%d",
            ).dt.to_period("M")
        )
        .groupby("_month")["trade_date"]
        .max()
        .tolist()
    )
    fixed_policy = lambda _date, weights, _history: fixed_target_weights(
        weights,
        lower=contract.lower_band,
        upper=contract.upper_band,
    )

    def dynamic_policy(
        date: str,
        _weights: Mapping[str, float],
        _history: pd.DataFrame,
    ) -> Mapping[str, float] | None:
        if date not in month_ends:
            return None
        return dynamic_target_weights(
            _momentum_observations(full, as_of=date)
        )

    shared = {
        "market": window,
        "initial_cash": contract.initial_cash,
        "lot_size": contract.lot_size,
        "commission_rate": contract.commission_rate * cost_multiplier,
        "minimum_commission": contract.minimum_commission * cost_multiplier,
        "slippage_rate": contract.slippage_rate * cost_multiplier,
        "stamp_tax_rate": contract.stamp_tax_rate,
    }
    fixed_target = {asset.role: 0.25 for asset in contract.assets}
    results = {
        "fixed": replay_strategy(
            strategy="fixed",
            target_schedule={initial_signal: fixed_target},
            target_policy=fixed_policy,
            **shared,
        ),
        "dynamic": replay_strategy(
            strategy="dynamic",
            target_schedule={initial_signal: initial_dynamic},
            target_policy=dynamic_policy,
            **shared,
        ),
        "equity_buy_hold": replay_strategy(
            strategy="equity_buy_hold",
            target_schedule={initial_signal: {"equity": 1.0}},
            **shared,
        ),
        "equal_weight_buy_hold": replay_strategy(
            strategy="equal_weight_buy_hold",
            target_schedule={initial_signal: fixed_target},
            **shared,
        ),
        "cash_buy_hold": replay_strategy(
            strategy="cash_buy_hold",
            target_schedule={initial_signal: {"cash": 1.0}},
            **shared,
        ),
    }
    cash_nav = results["cash_buy_hold"].nav
    return {
        "start_date": start_key,
        "end_date": end_key,
        "cost_multiplier": float(cost_multiplier),
        "portfolios": {
            name: _portfolio_payload(result, cash_nav=cash_nav)
            for name, result in results.items()
        },
    }


def _study_version(contract: PermanentPortfolioContract) -> str:
    prefix = "permanent_portfolio_"
    if not contract.study_id.startswith(prefix):
        raise ValueError("permanent_portfolio_study_id")
    version = contract.study_id[len(prefix):]
    if version not in {"v1", "v2"}:
        raise ValueError("permanent_portfolio_study_id")
    return version


def _study_root(
    repo_root: str | Path,
    *,
    contract: PermanentPortfolioContract | None = None,
) -> Path:
    relative = (
        STORE_RELATIVE
        if contract is None
        else Path(
            "data/research/permanent_portfolio"
        ) / _study_version(contract)
    )
    root = Path(repo_root).resolve() / relative
    root.mkdir(parents=True, exist_ok=True)
    return root


def _report_relative(contract: PermanentPortfolioContract) -> Path:
    return (
        Path("reports/research/permanent_portfolio")
        / _study_version(contract)
        / "dashboard.json"
    )


def _market_index(root: Path) -> tuple[dict[str, Any], str]:
    market_root = root / "market_data"
    latest_path = market_root / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    unsigned = dict(latest)
    recorded = unsigned.pop("manifest_sha256", None)
    if not isinstance(recorded, str) or canonical_hash(unsigned) != recorded:
        raise ValueError("permanent_portfolio_market_bundle")
    return latest, recorded


def _latest_market_with_evidence(
    root: Path,
    *,
    partition: str,
    market_index: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    market_root = root / "market_data"
    if market_index is None:
        latest, bundle_sha256 = _market_index(root)
    else:
        latest = dict(market_index)
        unsigned = dict(latest)
        bundle_sha256 = str(unsigned.pop("manifest_sha256", ""))
        if canonical_hash(unsigned) != bundle_sha256:
            raise ValueError("permanent_portfolio_market_bundle")
    partition_manifest = latest.get(partition)
    if not isinstance(partition_manifest, dict):
        raise ValueError("permanent_portfolio_market_partition")
    publication = (
        market_root
        / partition
        / str(partition_manifest["publication_id"])
    )
    frame, manifest = load_market_publication(publication)
    if (
        manifest["manifest_sha256"]
        != partition_manifest.get("manifest_sha256")
    ):
        raise ValueError("permanent_portfolio_latest_manifest")
    return frame, {
        "market_bundle_sha256": bundle_sha256,
        "partition_manifest_sha256": str(manifest["manifest_sha256"]),
        "partition_data_sha256": str(manifest["data_sha256"]),
        "schema_version": int(manifest.get("schema_version") or 1),
        "accounting_version": manifest.get("accounting_version"),
    }


def _latest_market(root: Path, *, partition: str) -> pd.DataFrame:
    frame, _evidence = _latest_market_with_evidence(
        root,
        partition=partition,
    )
    return frame


def _fixture_market_evidence(market: pd.DataFrame) -> dict[str, Any]:
    digest = canonical_hash({"records": _records(market)})
    return {
        "market_bundle_sha256": digest,
        "partition_manifest_sha256": digest,
        "partition_data_sha256": digest,
        "schema_version": (
            2 if "distribution_cash_per_share" in market.columns else 1
        ),
        "accounting_version": (
            "cash_distributions_v2"
            if "distribution_cash_per_share" in market.columns
            else None
        ),
    }


def _assert_market_accounting(
    contract: PermanentPortfolioContract,
    evidence: Mapping[str, Any],
) -> None:
    if contract.accounting_version != "cash_distributions_v2":
        return
    if (
        int(evidence.get("schema_version") or 0) != 2
        or evidence.get("accounting_version") != "cash_distributions_v2"
    ):
        raise ValueError("permanent_portfolio_market_accounting")


def _code_evidence() -> dict[str, str]:
    package_root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_root.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    try:
        git_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=package_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        git_revision = "unavailable"
    return {
        "code_sha256": digest.hexdigest(),
        "git_revision": git_revision,
    }


def _write_artifact(
    root: Path,
    *,
    stage: str,
    payload: Mapping[str, Any],
) -> tuple[Path, str]:
    unsigned = _json_value(dict(payload))
    artifact_sha256 = canonical_hash(unsigned)
    artifact = {**unsigned, "artifact_sha256": artifact_sha256}
    destination = root / "results" / stage / artifact_sha256
    destination.mkdir(parents=True, exist_ok=True)
    artifact_path = destination / "result.json"
    encoded = (
        json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    if artifact_path.exists():
        if artifact_path.read_text(encoding="utf-8") != encoded:
            raise ValueError("permanent_portfolio_artifact_conflict")
    else:
        write_text_atomic(artifact_path, encoded, encoding="utf-8")
    return artifact_path, artifact_sha256


def run_development(
    *,
    repo_root: str | Path,
    contract_path: str | Path,
    market_frame_fixture: pd.DataFrame | None = None,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    root = _study_root(repo_root, contract=contract)
    if market_frame_fixture is not None:
        market = market_frame_fixture.copy()
        market_evidence = _fixture_market_evidence(market)
    else:
        market, market_evidence = _latest_market_with_evidence(
            root,
            partition="development",
        )
    _assert_market_accounting(contract, market_evidence)
    code_evidence = _code_evidence()
    if "trade_date" not in market.columns:
        raise ValueError("permanent_portfolio_development_window")
    dates = market["trade_date"].map(_date_key)
    if dates.ge(contract.holdout_start).any():
        raise ValueError("permanent_portfolio_development_window")
    evaluation = evaluate_window(
        market,
        contract=contract,
        start_date=contract.development_start,
        end_date=contract.development_end,
    )
    stress = evaluate_window(
        market,
        contract=contract,
        start_date=contract.development_start,
        end_date=contract.development_end,
        cost_multiplier=2.0,
    )
    schema_version = int(contract.raw.get("schema_version") or 1)
    payload = {
        "schema_version": schema_version,
        "study_id": contract.study_id,
        "accounting_version": contract.accounting_version,
        "evidence_class": contract.evidence_class,
        "status": "development_complete",
        "contract_sha256": canonical_hash(contract.raw),
        "market_bundle_sha256": market_evidence["market_bundle_sha256"],
        "development_data_sha256": market_evidence[
            "partition_data_sha256"
        ],
        **code_evidence,
        "evaluation": evaluation,
        "cost_stress_2x": stress,
    }
    artifact_path, artifact_sha256 = _write_artifact(
        root,
        stage="development",
        payload=payload,
    )
    state = {
        "schema_version": schema_version,
        "study_id": contract.study_id,
        "accounting_version": contract.accounting_version,
        "evidence_class": contract.evidence_class,
        "status": "development_complete",
        "development_artifact": str(artifact_path.resolve()),
        "development_sha256": artifact_sha256,
        "contract_sha256": payload["contract_sha256"],
        "market_bundle_sha256": payload["market_bundle_sha256"],
        "development_data_sha256": payload["development_data_sha256"],
        **code_evidence,
    }
    state["state_sha256"] = canonical_hash(state)
    manifests = root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        manifests / "state.json",
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return state


def open_holdout_once(
    root: str | Path,
    development_artifact: str | Path,
    *,
    expected_sha256: str,
    expected_contract_sha256: str,
    expected_market_bundle_sha256: str,
    expected_code_sha256: str,
    expected_git_revision: str,
) -> dict[str, Any]:
    study_root = Path(root)
    development_path = Path(development_artifact).resolve()
    state_path = study_root / "manifests" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    unsigned_state = dict(state)
    recorded_state_sha256 = unsigned_state.pop("state_sha256", None)
    expected_state = {
        "development_artifact": str(development_path),
        "development_sha256": expected_sha256,
        "contract_sha256": expected_contract_sha256,
        "market_bundle_sha256": expected_market_bundle_sha256,
        "code_sha256": expected_code_sha256,
        "git_revision": expected_git_revision,
    }
    if (
        state.get("status") != "development_complete"
        or recorded_state_sha256 != canonical_hash(unsigned_state)
        or any(state.get(key) != value for key, value in expected_state.items())
    ):
        raise ValueError("permanent_portfolio_development_state_binding")
    development = json.loads(
        development_path.read_text(encoding="utf-8")
    )
    unsigned_development = dict(development)
    recorded_sha256 = unsigned_development.pop("artifact_sha256", None)
    if (
        development.get("status") != "development_complete"
        or recorded_sha256 != expected_sha256
        or canonical_hash(unsigned_development) != expected_sha256
        or any(
            development.get(key) != value
            for key, value in {
                "contract_sha256": expected_contract_sha256,
                "market_bundle_sha256": expected_market_bundle_sha256,
                "code_sha256": expected_code_sha256,
                "git_revision": expected_git_revision,
            }.items()
        )
    ):
        raise ValueError("permanent_portfolio_development_binding")
    manifests = study_root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    marker_path = manifests / "holdout_opened.json"
    marker: dict[str, Any] = {
        "schema_version": 1,
        "status": "holdout_opened",
        "development_artifact": str(development_path),
        "development_sha256": expected_sha256,
        "contract_sha256": expected_contract_sha256,
        "market_bundle_sha256": expected_market_bundle_sha256,
        "code_sha256": expected_code_sha256,
        "git_revision": expected_git_revision,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    marker["marker_sha256"] = canonical_hash(marker)
    data = (
        json.dumps(
            marker,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            marker_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise ValueError(
            "permanent_portfolio_holdout_already_opened"
        ) from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return marker


def run_holdout(
    *,
    repo_root: str | Path,
    contract_path: str | Path,
    development_artifact_path: str | Path,
    expected_development_sha256: str,
    market_frame_fixture: pd.DataFrame | None = None,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    root = _study_root(repo_root, contract=contract)
    contract_sha256 = canonical_hash(contract.raw)
    code_evidence = _code_evidence()
    if market_frame_fixture is not None:
        market = market_frame_fixture.copy()
        market_index = None
        market_evidence = _fixture_market_evidence(market)
    else:
        market_index, market_bundle_sha256 = _market_index(root)
        market = None
        market_evidence = {
            "market_bundle_sha256": market_bundle_sha256,
        }
    marker = open_holdout_once(
        root,
        development_artifact_path,
        expected_sha256=expected_development_sha256,
        expected_contract_sha256=contract_sha256,
        expected_market_bundle_sha256=market_evidence[
            "market_bundle_sha256"
        ],
        expected_code_sha256=code_evidence["code_sha256"],
        expected_git_revision=code_evidence["git_revision"],
    )
    if market is None:
        market, holdout_evidence = _latest_market_with_evidence(
            root,
            partition="holdout",
            market_index=market_index,
        )
    else:
        holdout_evidence = market_evidence
    _assert_market_accounting(contract, holdout_evidence)
    dates = market["trade_date"].map(_date_key)
    holdout_dates = dates.loc[dates.ge(contract.holdout_start)]
    if holdout_dates.empty:
        raise ValueError("permanent_portfolio_holdout_window")
    holdout_end = str(holdout_dates.max())
    evaluation = evaluate_window(
        market,
        contract=contract,
        start_date=contract.holdout_start,
        end_date=holdout_end,
    )
    stress = evaluate_window(
        market,
        contract=contract,
        start_date=contract.holdout_start,
        end_date=holdout_end,
        cost_multiplier=2.0,
    )
    schema_version = int(contract.raw.get("schema_version") or 1)
    payload = {
        "schema_version": schema_version,
        "study_id": contract.study_id,
        "accounting_version": contract.accounting_version,
        "evidence_class": contract.evidence_class,
        "status": "holdout_complete",
        "contract_sha256": contract_sha256,
        "market_bundle_sha256": market_evidence["market_bundle_sha256"],
        "holdout_data_sha256": holdout_evidence["partition_data_sha256"],
        **code_evidence,
        "development_sha256": expected_development_sha256,
        "holdout_marker_sha256": marker["marker_sha256"],
        "evaluation": evaluation,
        "cost_stress_2x": stress,
    }
    artifact_path, artifact_sha256 = _write_artifact(
        root,
        stage="holdout",
        payload=payload,
    )
    state_path = root / "manifests" / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {"schema_version": 1}
    )
    state.pop("state_sha256", None)
    state.update(
        {
            "schema_version": schema_version,
            "study_id": contract.study_id,
            "accounting_version": contract.accounting_version,
            "evidence_class": contract.evidence_class,
            "status": "holdout_complete",
            "holdout_end": holdout_end,
            "holdout_artifact": str(artifact_path),
            "holdout_sha256": artifact_sha256,
            "holdout_marker_sha256": marker["marker_sha256"],
        }
    )
    state["state_sha256"] = canonical_hash(state)
    write_text_atomic(
        state_path,
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    development = json.loads(
        Path(development_artifact_path).read_text(encoding="utf-8")
    )
    dashboard = {
        "schema_version": schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study": state,
        "development": development.get("evaluation"),
        "holdout": evaluation,
        "forward": {"status": "unavailable"},
    }
    dashboard["dashboard_sha256"] = canonical_hash(dashboard)
    report_path = Path(repo_root).resolve() / _report_relative(contract)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        report_path,
        json.dumps(
            dashboard,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return state
