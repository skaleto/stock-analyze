"""Development-only objective ablation for the classical A-share ranker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from ..utils import write_text_atomic
from .account_features import date_balanced_sample_weights
from .classical_specs import ClassicalModelSpec
from .models import (
    _bounded_cross_section_sample,
    _fit_clip_bounds,
    _impute,
    _ranking_target_values,
    _training_calibration_partition,
    make_purged_walk_forward_splits,
)
from .portfolio_replay import replay_rule_portfolio


EVALUATION_CONTRACT = "cross-sectional-objective-ablation-v1"


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


def _rank_metrics(evaluation: pd.DataFrame) -> tuple[float, float, int]:
    daily: list[float] = []
    for _, group in evaluation.groupby("trade_date", sort=True):
        aligned = group.loc[:, ["score", "excess_return"]].apply(
            pd.to_numeric,
            errors="coerce",
        ).dropna()
        if len(aligned) < 3 or aligned["score"].nunique() < 2:
            continue
        value = aligned["score"].corr(aligned["excess_return"], method="spearman")
        if pd.notna(value):
            daily.append(float(value))
    rank_ic = float(np.mean(daily)) if daily else 0.0
    standard_deviation = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    icir = rank_ic / standard_deviation if standard_deviation > 1e-12 else 0.0
    return rank_ic, float(icir), len(daily)


_BOUNDED_REPLAY_METRICS = (
    "simulator_version",
    "gross_return",
    "net_return",
    "benchmark_return",
    "net_excess_return",
    "portfolio_cagr",
    "benchmark_cagr",
    "cumulative_relative_wealth",
    "annualized_excess_wealth",
    "max_drawdown",
    "annual_turnover",
    "capital_utilization",
    "portfolio_sharpe",
    "information_ratio",
    "portfolio_rebalance_periods",
    "trade_count",
    "execution_cost_bps",
    "attribution_status",
    "execution_evidence_status",
    "missing_liquidity_notional_ratio",
    "impact_capped_notional_ratio",
    "all_accounts_positive_active",
    "scheduled_rebalance_periods",
    "replay_contract",
)


def _bounded_replay_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metrics.get(key)
        for key in _BOUNDED_REPLAY_METRICS
        if key in metrics
    }


def _score_bucket_returns(evaluation: pd.DataFrame) -> list[dict[str, Any]]:
    ranked = evaluation.loc[:, ["trade_date", "score", "excess_return"]].copy()
    ranked["score_percentile"] = ranked.groupby("trade_date", sort=False)[
        "score"
    ].rank(pct=True, method="first")
    ranked["bucket"] = np.minimum(
        np.floor(ranked["score_percentile"] * 5.0).astype(int),
        4,
    ) + 1
    return [
        {
            "bucket": int(bucket),
            "mean_excess_return": float(
                pd.to_numeric(group["excess_return"], errors="coerce").mean()
            ),
            "observations": int(len(group)),
        }
        for bucket, group in ranked.groupby("bucket", sort=True)
    ]


def evaluate_ridge_target(
    dataset: pd.DataFrame,
    *,
    feature_columns: Iterable[str],
    target_contract: str,
    horizon: int,
    ridge_alpha: float,
    portfolio_contract: dict[str, Any],
    random_state: int = 20260810,
) -> dict[str, Any]:
    """Evaluate one fixed Ridge target with purged OOS predictions and exact costs."""

    required = {
        "trade_date",
        "label_end_date",
        "horizon",
        "excess_return",
        "entry_date",
        "entry_price",
        "benchmark_entry_price",
    }
    missing = sorted(required.difference(dataset.columns))
    if missing:
        raise ValueError(f"cross_sectional_candidate_missing:{','.join(missing)}")
    data = dataset.loc[
        pd.to_numeric(dataset["horizon"], errors="coerce").eq(int(horizon))
    ].copy()
    data["trade_date"] = data["trade_date"].astype(str)
    data["label_end_date"] = data["label_end_date"].astype(str)
    data = data.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
    columns = tuple(
        column
        for column in feature_columns
        if column in data.columns
        and pd.to_numeric(data[column], errors="coerce").notna().mean() >= 0.70
        and pd.to_numeric(data[column], errors="coerce").nunique(dropna=True) > 1
    )
    if not columns:
        raise ValueError("cross_sectional_candidate_features_missing")

    validation_parts: list[pd.DataFrame] = []
    audit_results: list[bool] = []
    coefficient_parts: list[np.ndarray] = []
    for split_number, split in enumerate(
        make_purged_walk_forward_splits(data, n_splits=3, embargo=int(horizon))
    ):
        outer_train = data.loc[split.train_indices]
        validation = data.loc[split.validation_indices].copy()
        train, _ = _training_calibration_partition(
            outer_train,
            embargo=int(horizon),
        )
        fit_train = _bounded_cross_section_sample(
            train,
            max_rows=100_000,
            random_state=random_state + split_number,
        )
        clip_bounds = _fit_clip_bounds(fit_train, columns)
        train_x, imputation_values = _impute(
            fit_train,
            columns,
            clip_bounds=clip_bounds,
        )
        validation_x, _ = _impute(
            validation,
            columns,
            imputation_values,
            clip_bounds,
        )
        weights = date_balanced_sample_weights(fit_train).to_numpy(dtype=float)
        scaler = StandardScaler().fit(train_x, sample_weight=weights)
        model = Ridge(alpha=float(ridge_alpha)).fit(
            scaler.transform(train_x),
            _ranking_target_values(fit_train, target_contract),
            sample_weight=weights,
        )
        validation["score"] = model.predict(scaler.transform(validation_x))
        validation["fold"] = split_number
        validation_parts.append(validation)
        coefficient_parts.append(np.asarray(model.coef_, dtype=float))
        audit_results.append(
            bool(
                str(fit_train["label_end_date"].max())
                < str(validation["trade_date"].min())
            )
        )
    if not validation_parts:
        raise ValueError("cross_sectional_candidate_walk_forward_insufficient")

    evaluation = pd.concat(validation_parts, ignore_index=True).sort_values(
        ["trade_date", "code"],
        kind="stable",
    )
    replay = replay_rule_portfolio(evaluation, contract=portfolio_contract)
    rank_ic, icir, rank_ic_dates = _rank_metrics(evaluation)
    subperiods = []
    for fold, group in evaluation.groupby("fold", sort=True):
        fold_rank_ic, fold_icir, fold_dates = _rank_metrics(group)
        fold_replay = replay_rule_portfolio(group, contract=portfolio_contract)
        subperiods.append({
            "fold": int(fold),
            "start": str(group["trade_date"].min()),
            "end": str(group["trade_date"].max()),
            "rank_ic": fold_rank_ic,
            "icir": fold_icir,
            "rank_ic_dates": fold_dates,
            **_bounded_replay_metrics(dict(fold_replay.metrics)),
        })
    metrics = _bounded_replay_metrics(dict(replay.metrics))
    coefficient_matrix = np.vstack(coefficient_parts)
    return _json_safe({
        "evaluation_contract": EVALUATION_CONTRACT,
        "target_contract": str(target_contract),
        "evidence_scope": "development_only",
        "formal_order_source": False,
        "point_in_time_audit": bool(all(audit_results)),
        "walk_forward_splits": len(validation_parts),
        "oos_predictions": len(evaluation),
        "oos_start": str(evaluation["trade_date"].min()),
        "oos_end": str(evaluation["trade_date"].max()),
        "rank_ic": rank_ic,
        "icir": icir,
        "rank_ic_dates": rank_ic_dates,
        "selected_features": list(columns),
        "selected_feature_count": len(columns),
        "mean_standardized_coefficients": {
            column: float(coefficient_matrix[:, index].mean())
            for index, column in enumerate(columns)
        },
        "coefficient_sign_stability": {
            column: float(
                max(
                    np.mean(coefficient_matrix[:, index] >= 0.0),
                    np.mean(coefficient_matrix[:, index] <= 0.0),
                )
            )
            for index, column in enumerate(columns)
        },
        "score_bucket_returns": _score_bucket_returns(evaluation),
        "subperiods": subperiods,
        "feature_coverage": float(
            evaluation.loc[:, columns].notna().mean().mean()
        ),
        **metrics,
    })


def _gate_metrics(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    checks = {
        "point_in_time_audit": metrics.get("point_in_time_audit") is True,
        "rank_ic": float(metrics.get("rank_ic") or 0.0) > 0.02,
        "icir": float(metrics.get("icir") or 0.0) >= 0.30,
        "net_excess_return": float(metrics.get("net_excess_return") or 0.0) >= 0.02,
        "max_drawdown": float(metrics.get("max_drawdown") or 1.0) <= 0.20,
        "annual_turnover": float(metrics.get("annual_turnover") or 1e9) <= 8.0,
        "capital_utilization": float(metrics.get("capital_utilization") or 0.0) >= 0.85,
        "trade_count": int(metrics.get("trade_count") or 0) > 0,
        "simulator_version": (
            metrics.get("simulator_version") == "paper-parity-daily-v1"
        ),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return not reasons, reasons


def _markdown_report(result: dict[str, Any]) -> str:
    raw = result["target_ablation"]["raw_excess_return"]
    ranked = result["target_ablation"]["daily_cross_sectional_percentile_v1"]
    gate = result["development_gate"]

    def percent(value: Any) -> str:
        return f"{float(value or 0.0):+.2%}"

    lines = [
        "# 横截面选股目标纠偏实测",
        "",
        f"- 市场/账户：`{result['market']}` / `{result['account_scope']}`",
        f"- 数据快照：`{result['as_of']}`",
        f"- 开发窗口：`{result['development_start']}` 至 `{result['development_end']}`",
        "- 旧最终窗：仅标记为已观察诊断，不参与本次晋升",
        "- 正式下单：否",
        "",
        "| 目标 | RankIC | ICIR | 年化净超额 | 相对财富 | 最大回撤 | 年换手 | 资金利用率 | 成交 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| 原始未来收益 | {float(raw.get('rank_ic') or 0):+.4f} | "
            f"{float(raw.get('icir') or 0):+.3f} | {percent(raw.get('net_excess_return'))} | "
            f"{percent(raw.get('cumulative_relative_wealth'))} | {percent(raw.get('max_drawdown'))} | "
            f"{float(raw.get('annual_turnover') or 0):.2f}x | {percent(raw.get('capital_utilization'))} | "
            f"{int(raw.get('trade_count') or 0)} |"
        ),
        (
            f"| 每日横截面排名 | {float(ranked.get('rank_ic') or 0):+.4f} | "
            f"{float(ranked.get('icir') or 0):+.3f} | {percent(ranked.get('net_excess_return'))} | "
            f"{percent(ranked.get('cumulative_relative_wealth'))} | {percent(ranked.get('max_drawdown'))} | "
            f"{float(ranked.get('annual_turnover') or 0):.2f}x | {percent(ranked.get('capital_utilization'))} | "
            f"{int(ranked.get('trade_count') or 0)} |"
        ),
        "",
        "## 分阶段稳定性",
        "",
    ]
    subperiods = ranked.get("subperiods") or []
    if subperiods:
        lines.extend([
            "| 折 | 区间 | RankIC | ICIR | 年化净超额 | 相对财富 | 最大回撤 |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ])
        lines.extend(
            (
                f"| {int(period.get('fold') or 0) + 1} | "
                f"{period.get('start', '')} 至 {period.get('end', '')} | "
                f"{float(period.get('rank_ic') or 0):+.4f} | "
                f"{float(period.get('icir') or 0):+.3f} | "
                f"{percent(period.get('net_excess_return'))} | "
                f"{percent(period.get('cumulative_relative_wealth'))} | "
                f"{percent(period.get('max_drawdown'))} |"
            )
            for period in subperiods
        )
    else:
        lines.append("暂无分阶段明细。")
    lines.extend([
        "",
        "## 排名五档",
        "",
    ])
    buckets = ranked.get("score_bucket_returns") or []
    if buckets:
        lines.extend([
            "| 分数档位 | 平均未来超额 | 样本数 |",
            "| ---: | ---: | ---: |",
        ])
        lines.extend(
            (
                f"| {int(bucket.get('bucket') or 0)} | "
                f"{percent(bucket.get('mean_excess_return'))} | "
                f"{int(bucket.get('observations') or 0)} |"
            )
            for bucket in buckets
        )
    else:
        lines.append("暂无收益分档明细。")
    lines.extend([
        "",
        f"## 结论：{result['status']}",
        "",
        (
            "开发门槛通过。下一步只能冻结版本并开始未来日期 Shadow，不能用旧最终窗回填。"
            if gate["passed"]
            else "开发门槛未通过，保持 Research；不得放松成本或校准门槛强行晋升。"
        ),
        "",
        "未通过项：" + (", ".join(gate["reasons"]) if gate["reasons"] else "无"),
        "",
    ])
    return "\n".join(lines)


def evaluate_cross_sectional_candidate(
    repo_root: str | Path,
    *,
    market: str,
    account_scope: str,
    as_of: str,
    dataset: pd.DataFrame,
    feature_columns: Iterable[str],
    portfolio_contract: dict[str, Any],
    model_spec: ClassicalModelSpec,
    development_start: str,
    development_end: str,
    observed_final_start: str,
    observed_final_end: str,
) -> dict[str, Any]:
    """Run a two-target development ablation without mutating model registry."""

    normalized = dataset.copy()
    normalized["trade_date"] = normalized["trade_date"].astype(str)
    development = normalized.loc[
        normalized["trade_date"].between(
            str(development_start),
            str(development_end),
            inclusive="both",
        )
    ].copy()
    if development.empty:
        raise ValueError("cross_sectional_candidate_development_empty")
    if str(development["trade_date"].max()) > str(development_end):
        raise AssertionError("cross_sectional_candidate_final_leakage")
    effective_contract = {
        **portfolio_contract,
        "rebalance_frequency": str(model_spec.rebalance_frequency),
    }
    alpha = float(model_spec.parameter_map.get("alpha", 25.0))
    raw = evaluate_ridge_target(
        development,
        feature_columns=feature_columns,
        target_contract="raw_excess_return",
        horizon=model_spec.horizon,
        ridge_alpha=alpha,
        portfolio_contract=effective_contract,
        random_state=model_spec.random_state,
    )
    ranked = evaluate_ridge_target(
        development,
        feature_columns=feature_columns,
        target_contract=model_spec.ranking_target,
        horizon=model_spec.horizon,
        ridge_alpha=alpha,
        portfolio_contract=effective_contract,
        random_state=model_spec.random_state,
    )
    passed, reasons = _gate_metrics(ranked)
    safe_as_of = str(as_of).replace("-", "")
    safe_scope = str(account_scope).replace("/", "_")
    root = Path(repo_root)
    report_root = root / "reports" / "research"
    report_root.mkdir(parents=True, exist_ok=True)
    json_path = report_root / f"cross_sectional_alpha_repair_{safe_as_of}_{safe_scope}.json"
    report_path = report_root / f"cross_sectional_alpha_repair_{safe_as_of}_{safe_scope}.md"
    result = _json_safe({
        "schema_version": 1,
        "evaluation_contract": EVALUATION_CONTRACT,
        "status": "development_pass" if passed else "research",
        "market": str(market),
        "account_scope": str(account_scope),
        "as_of": safe_as_of,
        "model_spec_id": model_spec.spec_id,
        "model_spec_hash": model_spec.spec_hash,
        "development_start": str(development_start),
        "development_end": str(development_end),
        "observed_final_start": str(observed_final_start),
        "observed_final_end": str(observed_final_end),
        "observed_final_status": "diagnostic_only_already_observed",
        "formal_order_source": False,
        "registry_mutated": False,
        "target_ablation": {
            "raw_excess_return": raw,
            model_spec.ranking_target: ranked,
        },
        "improvement": {
            "rank_ic": float(ranked.get("rank_ic") or 0.0)
            - float(raw.get("rank_ic") or 0.0),
            "net_excess_return": float(ranked.get("net_excess_return") or 0.0)
            - float(raw.get("net_excess_return") or 0.0),
            "cumulative_relative_wealth": float(
                ranked.get("cumulative_relative_wealth") or 0.0
            ) - float(raw.get("cumulative_relative_wealth") or 0.0),
        },
        "development_gate": {"passed": passed, "reasons": reasons},
        "report_path": str(report_path),
        "json_path": str(json_path),
    })
    write_text_atomic(
        json_path,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_text_atomic(report_path, _markdown_report(result), encoding="utf-8")
    return result


__all__ = [
    "EVALUATION_CONTRACT",
    "evaluate_cross_sectional_candidate",
    "evaluate_ridge_target",
]
