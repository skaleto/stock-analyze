"""Bounded, account-scoped classical model tournament."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .activation import (
    ModelRegistry,
    activation_evidence_from_metrics,
    evaluate_role_activation,
)
from .classical_specs import ClassicalModelSpec
from .evaluation_windows import (
    build_account_windows,
    open_final_gate,
    seal_evaluation_manifest,
)
from .governance import (
    build_aligned_trial_return_matrix,
    deflated_sharpe_probability,
    probability_of_backtest_overfit,
)
from .models import save_model_bundle, train_model_bundle
from .portfolio_replay import (
    annualized_relative_wealth_excess,
    cumulative_relative_wealth,
    replay_model_portfolio,
    replay_rule_portfolio,
)
from .storage import ResearchStore
from .trial_ledger import TrialLedger


TOURNAMENT_PROTOCOL_VERSION = "scoped-classical-tournament-v2"
SUMMARY_METRIC_KEYS = (
    "rank_ic",
    "icir",
    "brier_score",
    "auc",
    "brier_improvement",
    "hit_rate_uplift",
    "gross_return",
    "net_return",
    "benchmark_return",
    "net_excess_return",
    "portfolio_cagr",
    "benchmark_cagr",
    "cumulative_relative_wealth",
    "annualized_excess_wealth",
    "annual_turnover",
    "max_drawdown",
    "portfolio_sharpe",
    "effective_dates",
    "effective_non_overlapping_periods",
    "valid_trial_count",
    "trial_evidence_status",
    "deflated_sharpe_probability",
    "probability_of_backtest_overfit",
    "simulator_version",
    "execution_cost_bps",
    "execution_evidence_status",
    "edge_calibration_available",
    "edge_calibration_reason",
    "alpha_half_life_days",
    "attribution_status",
    "attribution_max_error",
    "trade_count",
    "model_spec_id",
    "model_spec_hash",
    "ranking_score_source",
    "economic_score_source",
    "diagnostic_net_excess_return",
    "diagnostic_max_drawdown",
    "diagnostic_annual_turnover",
    "diagnostic_trade_count",
    "diagnostic_capital_utilization",
    "diagnostic_information_ratio",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _frame_fingerprint(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    selected = [column for column in columns if column in frame.columns]
    if not selected:
        raise ValueError("tournament_fingerprint_columns_missing")
    hashes = pd.util.hash_pandas_object(
        frame.loc[:, selected],
        index=False,
        categorize=True,
    ).to_numpy()
    return hashlib.sha256(hashes.tobytes()).hexdigest()[:16]


def _tournament_summary(report: dict[str, Any]) -> dict[str, Any]:
    candidates = []
    for candidate in report.get("candidates") or []:
        metrics = candidate.get("metrics") or {}
        candidates.append({
            "model_version": candidate.get("model_version"),
            "spec_id": candidate.get("spec_id"),
            "status": candidate.get("status"),
            "role_status": candidate.get("role_status") or {},
            "artifact": candidate.get("artifact"),
            "account_scope": report.get("account_scope"),
            "horizon": report.get("horizon"),
            "trained_at": report.get("generated_at"),
            "sample_support": metrics.get("fit_train_rows") or 0,
            "feature_columns": metrics.get("selected_features") or [],
            "gate_passed": candidate.get("status") in {"shadow", "active"},
            "gate_reasons": candidate.get("reasons") or [],
            "rejection_reasons": candidate.get("reasons") or [],
            "metrics": {
                key: metrics.get(key)
                for key in SUMMARY_METRIC_KEYS
                if key in metrics
            },
            "development_selection": candidate.get("development_selection"),
            "sealed_final_evaluation": candidate.get("sealed_final_evaluation"),
            "diagnostic_rank_evaluation": candidate.get(
                "diagnostic_rank_evaluation"
            ),
            "activation_evidence": candidate.get("activation_evidence"),
        })
    return {
        "schema_version": 1,
        "evidence_contract_version": report.get("evidence_contract_version"),
        "protocol": report.get("protocol"),
        "status": report.get("status"),
        "market": report.get("market"),
        "account_scope": report.get("account_scope"),
        "horizon": report.get("horizon"),
        "as_of": report.get("as_of"),
        "report_path": report.get("report_path"),
        "generated_at": report.get("generated_at"),
        "candidates": candidates,
    }


def _write_tournament_summary(
    tournament_root: Path,
    report: dict[str, Any],
) -> None:
    write_text_atomic(
        tournament_root / "summary.json",
        json.dumps(
            _json_safe(_tournament_summary(report)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _rank_metrics(frame: pd.DataFrame) -> dict[str, float]:
    values: list[float] = []
    for _, group in frame.groupby("trade_date", sort=True):
        score = pd.to_numeric(group["score"], errors="coerce")
        realized = pd.to_numeric(group["excess_return"], errors="coerce")
        aligned = pd.concat([score, realized], axis=1).dropna()
        if len(aligned) < 3 or aligned.iloc[:, 0].nunique() < 2:
            continue
        value = aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman")
        if pd.notna(value):
            values.append(float(value))
    rank_ic = float(np.mean(values)) if values else 0.0
    standard_deviation = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    icir = (
        rank_ic / standard_deviation
        if standard_deviation > 1e-12
        else 10.0 if rank_ic > 0.0 else 0.0
    )
    return {"rank_ic": rank_ic, "icir": float(icir)}


def _sharpe(returns: Iterable[float]) -> float:
    values = np.asarray(list(returns), dtype=float)
    if len(values) < 2:
        return 0.0
    standard_deviation = float(np.std(values, ddof=1))
    return (
        float(np.mean(values) / standard_deviation * np.sqrt(252.0))
        if standard_deviation > 1e-12 else 0.0
    )


def _trial_result(spec_id: str, replay: Any) -> dict[str, Any]:
    metrics = replay.metrics
    dates = list(metrics.get("portfolio_period_return_dates") or [])
    returns = [float(value) for value in metrics.get("portfolio_period_returns") or []]
    return {
        "spec_id": str(spec_id),
        "sharpe": float(metrics.get("information_ratio", _sharpe(returns))),
        "net_excess_return": float(metrics.get("net_excess_return", 0.0)),
        "oos_returns": [
            {"date": str(day), "return": float(value)}
            for day, value in zip(dates, returns)
        ],
    }


def _baseline_trials(
    final_frame: pd.DataFrame,
    *,
    portfolio_contract: dict[str, Any],
    reference_dates: tuple[str, ...],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    score_sources: list[tuple[str, pd.Series]] = []
    if "momentum_20" in final_frame.columns:
        score_sources.append((
            "baseline_momentum_20",
            pd.to_numeric(final_frame["momentum_20"], errors="coerce"),
        ))
    if "realized_volatility_20" in final_frame.columns:
        score_sources.append((
            "baseline_low_volatility_20",
            -pd.to_numeric(final_frame["realized_volatility_20"], errors="coerce"),
        ))
    score_sources.append((
        "baseline_equal_weight_top_n",
        pd.Series(0.0, index=final_frame.index, dtype=float),
    ))
    for spec_id, score in score_sources:
        candidate = final_frame.copy()
        candidate["score"] = score.fillna(0.0)
        try:
            result = _trial_result(
                spec_id,
                replay_rule_portfolio(candidate, contract=portfolio_contract),
            )
        except ValueError:
            continue
        if tuple(item["date"] for item in result["oos_returns"]) == reference_dates:
            results.append(result)
    performance = portfolio_contract.get("performance")
    performance = performance if isinstance(performance, dict) else {}
    annual_risk_free = float(
        performance.get(
            "risk_free_rate",
            portfolio_contract.get(
                "annual_risk_free_rate",
                portfolio_contract.get("risk_free_rate", 0.02),
            ),
        )
        or 0.0
    )
    periods_per_year = float(
        performance.get(
            "trading_days_per_year",
            portfolio_contract.get("periods_per_year", 252.0),
        )
        or 252.0
    )
    benchmark_levels = (
        final_frame.assign(
            _benchmark=pd.to_numeric(
                final_frame.get("benchmark_entry_price"),
                errors="coerce",
            )
        )
        .groupby(final_frame["trade_date"].astype(str), sort=True)["_benchmark"]
        .median()
        .dropna()
    )
    ordered_dates = list(benchmark_levels.index.astype(str))
    date_positions = {day: index for index, day in enumerate(ordered_dates)}
    benchmark_returns: list[float] = []
    for day in reference_dates:
        position = date_positions.get(str(day), -1)
        if position < 0 or position + 1 >= len(ordered_dates):
            raise ValueError("cash_baseline_benchmark_missing")
        current = float(benchmark_levels.iloc[position])
        following = float(benchmark_levels.iloc[position + 1])
        if current <= 0.0 or following <= 0.0:
            raise ValueError("cash_baseline_benchmark_missing")
        benchmark_returns.append(following / current - 1.0)
    cash_period_return = (
        (1.0 + annual_risk_free) ** (1.0 / periods_per_year) - 1.0
    )
    cash_nav = pd.Series(
        (1.0 + cash_period_return) ** np.arange(len(reference_dates) + 1),
        dtype=float,
    )
    benchmark_nav = pd.Series(
        np.concatenate((
            np.array([1.0], dtype=float),
            np.cumprod(1.0 + np.asarray(benchmark_returns, dtype=float)),
        )),
        dtype=float,
    )
    active_returns = [
        cash_period_return - benchmark_return
        for benchmark_return in benchmark_returns
    ]
    cash_excess = annualized_relative_wealth_excess(
        cash_nav,
        benchmark_nav,
        periods_per_year=periods_per_year,
    )
    results.append({
        "spec_id": "baseline_cash",
        "sharpe": _sharpe(active_returns),
        "net_excess_return": cash_excess,
        "annualized_excess_wealth": cash_excess,
        "cumulative_relative_wealth": cumulative_relative_wealth(
            cash_nav,
            benchmark_nav,
        ),
        "portfolio_nav": cash_nav.astype(float).tolist(),
        "benchmark_nav": benchmark_nav.astype(float).tolist(),
        "oos_returns": [
            {"date": day, "return": float(value)}
            for day, value in zip(reference_dates, active_returns)
        ],
    })
    return results


def run_classical_tournament(
    repo_root: str | Path,
    *,
    market: str,
    account_scope: str,
    horizon: int,
    as_of: str,
    dataset: pd.DataFrame,
    feature_columns: Iterable[str],
    portfolio_contract: dict[str, Any],
    specs: Iterable[ClassicalModelSpec],
) -> dict[str, Any]:
    """Train a sealed family and evaluate its final chronological slice once."""

    root = Path(repo_root)
    run_key = str(as_of).replace("-", "")
    model_root = (
        root / "data" / "research" / "models"
        / str(market) / str(account_scope) / str(int(horizon))
    )
    tournament_root = model_root / "tournaments" / run_key
    report_path = tournament_root / "report.json"
    if report_path.exists():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
        if isinstance(existing, dict):
            _write_tournament_summary(tournament_root, existing)
            return existing

    declared_specs = tuple(specs)
    if not declared_specs:
        raise ValueError("tournament_declared_family_empty")
    for spec in declared_specs:
        if (
            spec.market != market
            or spec.account_scope != account_scope
            or int(spec.horizon) != int(horizon)
        ):
            raise ValueError("tournament_spec_identity_mismatch")
    rebalance_frequencies = {
        str(spec.rebalance_frequency or "daily").strip().lower()
        for spec in declared_specs
    }
    if len(rebalance_frequencies) != 1:
        raise ValueError("tournament_rebalance_frequency_mismatch")
    family_rebalance_frequency = next(iter(rebalance_frequencies))
    effective_portfolio_contract = {
        **portfolio_contract,
        "rebalance_frequency": family_rebalance_frequency,
    }
    normalized = dataset.copy()
    normalized["trade_date"] = normalized["trade_date"].astype(str)
    normalized["label_end_date"] = normalized["label_end_date"].astype(str)
    scope_column = (
        "research_scope"
        if "research_scope" in normalized.columns
        else "account_id" if "account_id" in normalized.columns else ""
    )
    if not scope_column:
        raise ValueError("tournament_scope_missing")
    normalized = normalized.loc[
        normalized[scope_column].astype(str).eq(str(account_scope))
        & pd.to_numeric(normalized["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    if normalized.empty:
        raise ValueError("tournament_scope_data_missing")
    selected_features = tuple(
        column
        for column in feature_columns
        if column in normalized.columns
        and pd.to_numeric(normalized[column], errors="coerce").notna().mean() >= 0.70
    )
    if not selected_features:
        raise ValueError("tournament_features_missing")
    windows = build_account_windows(
        normalized,
        account_scope=account_scope,
        horizon=horizon,
        n_splits=4,
        embargo_days=horizon,
    )
    final_fold = windows.folds[-1]
    development = normalized.loc[
        normalized["trade_date"].isin(final_fold.train_dates)
    ].copy()
    final = normalized.loc[
        normalized["trade_date"].isin(final_fold.validation_dates)
    ].copy()
    if development.empty or final.empty:
        raise ValueError("tournament_window_empty")

    data_fingerprint = _frame_fingerprint(
        normalized,
        (
            "code", "account_id", "research_scope", "trade_date", "horizon",
            "label", "label_end_date", "excess_return", *selected_features,
        ),
    )
    contract_hash = hashlib.sha256(
        json.dumps(effective_portfolio_contract, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    manifest_payload = {
        "protocol": TOURNAMENT_PROTOCOL_VERSION,
        "market": market,
        "account_scope": account_scope,
        "horizon": int(horizon),
        "objective": "exact_net_active_return",
        "spec_hashes": [spec.spec_hash for spec in declared_specs],
        "data_fingerprint": data_fingerprint,
        "portfolio_contract_hash": contract_hash,
        "rebalance_frequency": family_rebalance_frequency,
        "feature_columns": list(selected_features),
        "development_start": str(development["trade_date"].min()),
        "development_end": str(development["trade_date"].max()),
        "final_start": str(final["trade_date"].min()),
        "final_end": str(final["trade_date"].max()),
        "development_folds": 3,
        "embargo_days": int(horizon),
        "historically_consumed": True,
    }
    manifest_path = tournament_root / "evaluation_manifest.json"
    manifest = seal_evaluation_manifest(manifest_path, manifest_payload)
    ledger = TrialLedger(tournament_root / "trial_ledger.json")
    declaration = ledger.declare(
        family_id=(
            f"{market}:{account_scope}:{horizon}:"
            f"{TOURNAMENT_PROTOCOL_VERSION}:{data_fingerprint}"
        ),
        specs=[spec.as_ledger_spec() for spec in declared_specs],
        objective="exact_net_active_return",
    )

    prepared: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    max_features = 10 if market == "cn_qdii_etf" else 12
    for spec in declared_specs:
        try:
            spec_features = tuple(
                column
                for column in selected_features
                if not spec.feature_allowlist or column in spec.feature_allowlist
            )
            if not spec_features:
                raise ValueError("spec_features_missing")
            bundle = train_model_bundle(
                development,
                feature_columns=spec_features,
                horizon=horizon,
                random_state=spec.random_state,
                portfolio_contract=effective_portfolio_contract,
                account_scope=account_scope,
                feature_selection_policy={
                    "max_features": max_features,
                    "max_per_family": 3,
                    "min_coverage": 0.70,
                    "min_stability": 0.75,
                },
                model_spec=spec,
                trial_declaration_id=str(declaration["declaration_id"]),
            )
            artifact = (
                tournament_root / "candidates" / spec.spec_id
                / f"{run_key}-{bundle.model_version}.joblib"
            )
            save_model_bundle(bundle, artifact)
            prepared.append({"spec": spec, "bundle": bundle, "artifact": artifact})
        except Exception as exc:  # noqa: BLE001 - every declared loser is retained
            failures.append({
                "spec_id": spec.spec_id,
                "spec_hash": spec.spec_hash,
                "status": "rejected",
                "reasons": [f"training_failed:{str(exc)[:240]}"],
            })

    if not prepared:
        raise RuntimeError(f"tournament_all_training_failed:{failures}")
    gate_open_count = int(manifest.get("final_gate_open_count") or 0)
    if gate_open_count == 0:
        manifest = open_final_gate(
            manifest_path,
            str(manifest["declaration_id"]),
        )
    elif gate_open_count != 1:
        raise ValueError("final_gate_open_count_invalid")

    store = ResearchStore(root / "data" / "research")
    evaluated: list[dict[str, Any]] = []
    trial_results: list[dict[str, Any]] = []
    replay_by_spec: dict[str, Any] = {}
    bundle_by_spec: dict[str, Any] = {}
    for prepared_item in prepared:
        spec = prepared_item["spec"]
        bundle = prepared_item["bundle"]
        artifact = prepared_item["artifact"]
        try:
            expected = bundle.predict_excess_return(final)
            ranking_predictor = getattr(bundle, "predict_ranking_score", None)
            ranking_score = (
                ranking_predictor(final)
                if callable(ranking_predictor)
                else expected
            )
            uncertainty = bundle.predict_excess_uncertainty(final)
            evaluation = final.copy()
            evaluation["score"] = ranking_score
            evaluation["expected_excess_return"] = expected
            evaluation["prediction_uncertainty_bps"] = np.maximum(
                uncertainty,
                0.0,
            ) * 10_000.0
            evaluation["fold"] = "sealed_final"
            formal_replay = replay_model_portfolio(
                evaluation,
                contract=effective_portfolio_contract,
            )
            diagnostic_replay = replay_rule_portfolio(
                evaluation,
                contract=effective_portfolio_contract,
            )
            rank_metrics = _rank_metrics(evaluation)
            development_metrics = dict(bundle.metrics)
            sealed_final_metrics = {
                **rank_metrics,
                **dict(formal_replay.metrics),
                "oos_predictions": int(len(evaluation)),
                "effective_dates": int(evaluation["trade_date"].nunique()),
                "effective_non_overlapping_periods": int(
                    diagnostic_replay.metrics.get(
                        "portfolio_rebalance_periods",
                        0,
                    )
                ),
                "edge_calibration_available": bool(
                    getattr(bundle.edge_calibrator, "available", False)
                ),
                "ranking_score_source": "raw_model_excess_return",
                "economic_score_source": "training_only_edge_calibration",
                "final_gate_declaration_id": manifest["declaration_id"],
                "final_window_start": manifest_payload["final_start"],
                "final_window_end": manifest_payload["final_end"],
            }
            diagnostic_metrics = {
                **dict(diagnostic_replay.metrics),
                "evaluation_contract": "diagnostic_rank_only-v1",
                "formal_order_source": False,
            }
            diagnostic_prefixed = {
                f"diagnostic_{key}": value
                for key, value in diagnostic_metrics.items()
            }
            metrics = dict(development_metrics)
            metric_sources = {
                key: "development_selection" for key in development_metrics
            }
            metrics.update(sealed_final_metrics)
            metric_sources.update({
                key: "sealed_final_evaluation" for key in sealed_final_metrics
            })
            metrics.update(diagnostic_prefixed)
            metric_sources.update({
                key: "diagnostic_rank_evaluation"
                for key in diagnostic_prefixed
            })
            metrics["evidence_contract_version"] = "windowed-evidence-v1"
            metric_sources["evidence_contract_version"] = "contract"
            evidence_sections = {
                "development_selection": {
                    "window": [
                        manifest_payload["development_start"],
                        manifest_payload["development_end"],
                    ],
                    "metrics": _json_safe(development_metrics),
                },
                "sealed_final_evaluation": {
                    "window": [
                        manifest_payload["final_start"],
                        manifest_payload["final_end"],
                    ],
                    "metrics": _json_safe(sealed_final_metrics),
                },
                "diagnostic_rank_evaluation": {
                    "window": [
                        manifest_payload["final_start"],
                        manifest_payload["final_end"],
                    ],
                    "metrics": _json_safe(diagnostic_metrics),
                },
                "activation_evidence": {
                    "source_windows": {
                        "development_selection": [
                            manifest_payload["development_start"],
                            manifest_payload["development_end"],
                        ],
                        "sealed_final_evaluation": [
                            manifest_payload["final_start"],
                            manifest_payload["final_end"],
                        ],
                    },
                    "metrics": _json_safe(metrics),
                    "metric_sources": dict(metric_sources),
                },
            }
            candidate_root = tournament_root / "candidates" / spec.spec_id
            store.write_parquet_atomic(candidate_root / "final_predictions.parquet", evaluation)
            store.write_parquet_atomic(candidate_root / "final_periods.parquet", formal_replay.periods)
            store.write_parquet_atomic(candidate_root / "final_trades.parquet", formal_replay.trades)
            store.write_parquet_atomic(candidate_root / "final_decisions.parquet", formal_replay.decisions)
            store.write_parquet_atomic(
                candidate_root / "diagnostic_rank_periods.parquet",
                diagnostic_replay.periods,
            )
            store.write_parquet_atomic(
                candidate_root / "diagnostic_rank_trades.parquet",
                diagnostic_replay.trades,
            )
            store.write_parquet_atomic(
                candidate_root / "diagnostic_rank_decisions.parquet",
                diagnostic_replay.decisions,
            )
            result = _trial_result(spec.spec_id, diagnostic_replay)
            trial_results.append(result)
            replay_by_spec[spec.spec_id] = formal_replay
            bundle_by_spec[spec.spec_id] = bundle
            evaluated.append({
                "spec": spec,
                "bundle": bundle,
                "artifact": artifact,
                "metrics": metrics,
                "trial_result": result,
                **evidence_sections,
            })
        except Exception as exc:  # noqa: BLE001 - final evidence fails closed
            failures.append({
                "spec_id": spec.spec_id,
                "spec_hash": spec.spec_hash,
                "status": "rejected",
                "reasons": [f"final_evaluation_failed:{str(exc)[:240]}"],
            })

    if not evaluated:
        raise RuntimeError(f"tournament_all_final_evaluation_failed:{failures}")
    reference_dates = tuple(
        item["date"] for item in evaluated[0]["trial_result"]["oos_returns"]
    )
    baselines = _baseline_trials(
        final,
        portfolio_contract=effective_portfolio_contract,
        reference_dates=reference_dates,
    )
    aligned_inputs = [
        {"trial_id": item["spec_id"], "oos_returns": item["oos_returns"]}
        for item in [*trial_results, *baselines]
        if tuple(row["date"] for row in item["oos_returns"]) == reference_dates
    ]
    try:
        aligned = build_aligned_trial_return_matrix(aligned_inputs)
        pbo = probability_of_backtest_overfit(aligned)
        alignment_status = "aligned"
    except ValueError as exc:
        aligned = pd.DataFrame()
        pbo = 1.0
        alignment_status = str(exc)
    all_sharpes = [float(item["sharpe"]) for item in [*trial_results, *baselines]]
    valid_trial_count = int(len(aligned.columns))
    trial_evidence_status = (
        "available"
        if alignment_status == "aligned" and valid_trial_count >= 4
        else "insufficient_evidence"
    )
    ledger.finalize(
        run_id=f"{run_key}:{manifest['declaration_id']}",
        declaration_id=str(declaration["declaration_id"]),
        results=trial_results,
    )

    registry = ModelRegistry(model_root / "registry.json")
    candidates: list[dict[str, Any]] = []
    for item in evaluated:
        spec = item["spec"]
        bundle = item["bundle"]
        artifact = item["artifact"]
        metrics = dict(item["metrics"])
        governance = {
            "deflated_sharpe_probability": deflated_sharpe_probability(
                observed_sharpe=float(
                    metrics.get("diagnostic_information_ratio", 0.0)
                ),
                trial_sharpes=all_sharpes,
                observations=max(len(aligned.index), 2),
                periods_per_year=252.0,
            ),
            "probability_of_backtest_overfit": float(pbo),
            "pbo_trial_count": valid_trial_count,
            "valid_trial_count": valid_trial_count,
            "declared_trial_count": len(declared_specs),
            "trial_evidence_status": trial_evidence_status,
            "pbo_alignment_status": alignment_status,
            "trial_declaration_id": declaration["declaration_id"],
        }
        metrics["governance"] = governance
        metrics.update(governance)
        activation_evidence = {
            "source_windows": dict(
                item["activation_evidence"]["source_windows"]
            ),
            "metrics": {
                **dict(item["activation_evidence"]["metrics"]),
                "governance": governance,
                **governance,
            },
            "metric_sources": {
                **dict(item["activation_evidence"]["metric_sources"]),
                "governance": "sealed_final_evaluation",
                **{
                    key: "sealed_final_evaluation" for key in governance
                },
            },
        }
        state = registry._read()
        model = state.setdefault("models", {}).setdefault(
            bundle.model_version,
            {"status": "research", "gate_history": []},
        )
        model.update({
            "artifact": str(artifact),
            "account_scope": account_scope,
            "spec_id": spec.spec_id,
            "spec_hash": spec.spec_hash,
            "tournament_report": str(report_path),
            "metrics": _json_safe(metrics),
            "evidence": _json_safe({
                "development_selection": item["development_selection"],
                "sealed_final_evaluation": item["sealed_final_evaluation"],
                "diagnostic_rank_evaluation": item[
                    "diagnostic_rank_evaluation"
                ],
                "activation_evidence": activation_evidence,
            }),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })
        registry._write(state)
        role_gates = evaluate_role_activation(
            activation_evidence_from_metrics(activation_evidence["metrics"]),
            current_status="research",
            target_status="shadow",
        )
        for role, gate in role_gates.items():
            registry.record_role_gate(bundle.model_version, role, gate)
        state = registry.finalize_research_evaluation(bundle.model_version)
        model_state = state["models"][bundle.model_version]
        candidates.append({
            "spec_id": spec.spec_id,
            "spec_hash": spec.spec_hash,
            "model_version": bundle.model_version,
            "artifact": str(artifact),
            "status": str(model_state["status"]),
            "role_status": dict(model_state.get("role_status") or {}),
            "reasons": list(model_state.get("rejection_reasons") or []),
            "metrics": _json_safe(metrics),
            "development_selection": _json_safe(item["development_selection"]),
            "sealed_final_evaluation": _json_safe(item["sealed_final_evaluation"]),
            "diagnostic_rank_evaluation": _json_safe(
                item["diagnostic_rank_evaluation"]
            ),
            "activation_evidence": _json_safe(activation_evidence),
        })

    candidates.extend(failures)
    candidates.sort(key=lambda item: str(item.get("spec_id") or ""))
    shadow_candidates = [
        item for item in candidates if item.get("status") == "shadow"
    ]
    report = {
        "schema_version": 1,
        "evidence_contract_version": "windowed-evidence-v1",
        "protocol": TOURNAMENT_PROTOCOL_VERSION,
        "status": "shadow_available" if shadow_candidates else "no_pass",
        "market": market,
        "account_scope": account_scope,
        "horizon": int(horizon),
        "as_of": str(as_of),
        "data_fingerprint": data_fingerprint,
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "final_gate_open_count": 1,
        "development_window": [
            manifest_payload["development_start"],
            manifest_payload["development_end"],
        ],
        "final_window": [manifest_payload["final_start"], manifest_payload["final_end"]],
        "selected_features": list(selected_features),
        "governance": {
            "probability_of_backtest_overfit": float(pbo),
            "valid_trial_count": valid_trial_count,
            "trial_evidence_status": trial_evidence_status,
            "pbo_alignment_status": alignment_status,
        },
        "baselines": _json_safe(baselines),
        "candidates": candidates,
        "shadow_model_versions": [item["model_version"] for item in shadow_candidates],
        "formal_strategy_activated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_text_atomic(
        report_path,
        json.dumps(_json_safe(report), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_tournament_summary(tournament_root, report)
    return report


__all__ = ["TOURNAMENT_PROTOCOL_VERSION", "run_classical_tournament"]
