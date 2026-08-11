"""Side-effect-free paired evaluation for structured event features."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from ..intelligence.factors import (
    EVENT_FACTOR_COLUMNS,
    EVENT_LITE_FACTOR_COLUMNS,
    attach_event_features,
)
from ..utils import write_text_atomic
from .feature_registry import INTELLIGENCE_FEATURES
from .models import ModelBundle, train_model_bundle
from .storage import ResearchStore


Trainer = Callable[..., ModelBundle]
DELTA_METRICS = (
    "rank_ic",
    "portfolio_sharpe",
    "brier_improvement",
    "net_excess_return",
    "max_drawdown",
    "annual_turnover",
)


def evaluate_intelligence_increment(
    dataset: pd.DataFrame,
    *,
    base_features: Sequence[str],
    event_features: Sequence[str],
    horizon: int,
    trainer: Trainer = train_model_bundle,
    random_state: int = 1729,
    minimum_active_dates: int = 20,
    minimum_active_rows: int = 100,
) -> dict[str, Any]:
    """Train Base and Base+Event on exactly the same rows and seed."""

    frame = dataset.copy()
    available_event_features, support = _measure_event_support(
        frame,
        event_features,
        minimum_active_dates=minimum_active_dates,
        minimum_active_rows=minimum_active_rows,
    )
    if (
        not available_event_features
        or int(support["active_dates"]) < minimum_active_dates
        or int(support["active_rows"]) < minimum_active_rows
    ):
        return {
            "status": "insufficient_support",
            "horizon": int(horizon),
            "support": support,
            "base_features": list(base_features),
            "event_features": list(available_event_features),
            "reason": "event_history_not_yet_sufficient_for_paired_training",
        }

    normalized_base = tuple(
        name for name in base_features if name in frame.columns
    )
    candidate_features = tuple(
        dict.fromkeys((*normalized_base, *available_event_features))
    )
    base_bundle = trainer(
        frame,
        feature_columns=normalized_base,
        horizon=int(horizon),
        random_state=int(random_state),
    )
    candidate_bundle = trainer(
        frame,
        feature_columns=candidate_features,
        horizon=int(horizon),
        random_state=int(random_state),
    )
    base_metrics = dict(base_bundle.metrics)
    candidate_metrics = dict(candidate_bundle.metrics)
    deltas = {
        metric: _numeric_delta(
            candidate_metrics.get(metric),
            base_metrics.get(metric),
        )
        for metric in DELTA_METRICS
        if _is_number(base_metrics.get(metric))
        and _is_number(candidate_metrics.get(metric))
    }
    paired_returns = _paired_period_return_summary(
        base_metrics,
        candidate_metrics,
        random_state=random_state,
    )
    permutation = _event_permutation_importance(
        candidate_bundle,
        frame,
        available_event_features,
        random_state=random_state,
    )
    return {
        "status": "complete",
        "horizon": int(horizon),
        "random_state": int(random_state),
        "support": support,
        "base_features": list(normalized_base),
        "event_features": list(available_event_features),
        "base_metrics": {
            key: base_metrics.get(key) for key in DELTA_METRICS
        },
        "candidate_metrics": {
            key: candidate_metrics.get(key) for key in DELTA_METRICS
        },
        "deltas": deltas,
        "paired_period_return": paired_returns,
        "candidate_selected_features": list(
            candidate_metrics.get("selected_features") or ()
        ),
        "permutation_importance": permutation,
        "decision_boundary": (
            "research_only; never changes strategy activation directly"
        ),
    }


def evaluate_latest_intelligence_effect(
    repo_root: str | Path,
    *,
    market: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate every available horizon and write one immutable report."""

    root = Path(repo_root).resolve()
    cutoff = str(as_of or date.today().isoformat()).replace("-", "")[:8]
    store = ResearchStore(root / "data" / "research")
    snapshot_date = store.latest_common_snapshot_date(
        market,
        as_of=cutoff,
    )
    features = store.read_feature_snapshot(market, snapshot_date)
    excluded = {
        "code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "horizon",
        "label",
        "label_end_date",
        "absolute_return",
        "benchmark_return",
        "excess_return",
        "threshold",
        "max_favorable_excursion",
        "max_adverse_excursion",
    }
    intelligence_names = {item.name for item in INTELLIGENCE_FEATURES}
    base_features = tuple(
        column
        for column in features.select_dtypes(include=[np.number]).columns
        if column not in excluded
        and column not in intelligence_names
        and features[column].notna().mean() >= 0.55
    )
    event_features = tuple(
        name for name in EVENT_LITE_FACTOR_COLUMNS if name in features
    )
    horizon_reports: dict[str, Any] = {}
    available_event_features, preflight = _measure_event_support(
        features,
        event_features,
        minimum_active_dates=20,
        minimum_active_rows=100,
    )
    if (
        not available_event_features
        or int(preflight["active_dates"]) < 20
        or int(preflight["active_rows"]) < 100
    ):
        for horizon in (3, 5, 10, 20):
            horizon_reports[str(horizon)] = {
                "status": "insufficient_support",
                "horizon": horizon,
                "support": preflight,
                "base_features": list(base_features),
                "event_features": list(available_event_features),
                "reason": (
                    "event_history_not_yet_sufficient_for_paired_training"
                ),
            }
    else:
        labels = store.read_label_snapshot(market, snapshot_date)
        for horizon in (3, 5, 10, 20):
            horizon_labels = labels.loc[
                pd.to_numeric(
                    labels["horizon"],
                    errors="coerce",
                ).eq(horizon)
            ]
            if horizon_labels.empty:
                horizon_reports[str(horizon)] = {
                    "status": "insufficient_support",
                    "reason": "horizon_labels_missing",
                }
                continue
            dataset = features.merge(
                horizon_labels,
                on=["code", "trade_date"],
                how="inner",
                suffixes=("", "_label"),
            )
            horizon_reports[str(horizon)] = (
                evaluate_intelligence_increment(
                    dataset,
                    base_features=base_features,
                    event_features=event_features,
                    horizon=horizon,
                )
            )
    qualified = [
        value
        for value in horizon_reports.values()
        if value.get("status") == "complete"
    ]
    report = {
        "schema_version": 1,
        "status": "complete" if qualified else "insufficient_support",
        "market": market,
        "as_of": cutoff,
        "snapshot_date": snapshot_date,
        "factor_set": "event-lite-v1",
        "horizons": horizon_reports,
        "qualified_horizons": len(qualified),
        "activation": "unchanged",
    }
    encoded = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    report_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    report["report_hash"] = report_hash
    reports = root / "reports" / "intelligence"
    reports.mkdir(parents=True, exist_ok=True)
    stem = f"model_incremental_effect_{market}_{cutoff}"
    write_text_atomic(
        reports / f"{stem}.json",
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_text_atomic(
        reports / f"{stem}.md",
        _render_markdown(report),
        encoding="utf-8",
    )
    return report


def _measure_event_support(
    frame: pd.DataFrame,
    event_features: Sequence[str],
    *,
    minimum_active_dates: int,
    minimum_active_rows: int,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    available = tuple(
        name for name in event_features if name in frame.columns
    )
    signal_columns = tuple(
        name for name in available if name != "event_data_coverage"
    )
    if signal_columns:
        signal_matrix = frame.loc[:, signal_columns].apply(
            pd.to_numeric,
            errors="coerce",
        )
        active = signal_matrix.fillna(0.0).abs().gt(1e-12).any(axis=1)
    else:
        active = pd.Series(False, index=frame.index)
    if "event_data_coverage" in frame.columns:
        covered = pd.to_numeric(
            frame["event_data_coverage"],
            errors="coerce",
        ).fillna(0.0).gt(0.0)
    else:
        covered = active
    active = active & covered
    active_dates = int(
        frame.loc[active, "trade_date"].astype(str).nunique()
    )
    support = {
        "rows": len(frame),
        "dates": int(frame["trade_date"].astype(str).nunique()),
        "covered_rows": int(covered.sum()),
        "covered_ratio": float(covered.mean()) if len(frame) else 0.0,
        "active_rows": int(active.sum()),
        "active_ratio": float(active.mean()) if len(frame) else 0.0,
        "active_dates": active_dates,
        "minimum_active_dates": int(minimum_active_dates),
        "minimum_active_rows": int(minimum_active_rows),
    }
    return available, support


def refresh_latest_intelligence_features(
    repo_root: str | Path,
    *,
    market: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Refresh only the event overlay on the latest research snapshot."""

    root = Path(repo_root).resolve()
    cutoff = str(as_of or date.today().isoformat()).replace("-", "")[:8]
    store = ResearchStore(root / "data" / "research")
    snapshot_date = store.latest_common_snapshot_date(
        market,
        as_of=cutoff,
    )
    features = store.read_feature_snapshot(market, snapshot_date)
    features.drop(
        columns=[
            name for name in EVENT_FACTOR_COLUMNS if name in features.columns
        ],
        errors="ignore",
        inplace=True,
    )
    enriched = attach_event_features(
        features,
        root / "data" / "shared" / "intelligence",
        market=market,
        as_of=snapshot_date,
        availability_policy="research",
        copy=False,
    )
    destination = store.write_feature_snapshot(
        market,
        snapshot_date,
        enriched,
    )
    return {
        "status": "complete",
        "market": market,
        "snapshot_date": snapshot_date,
        "rows": len(enriched),
        "event_features": [
            name for name in EVENT_LITE_FACTOR_COLUMNS if name in enriched
        ],
        "snapshot_path": str(destination),
        "snapshot_hash": hashlib.sha256(
            destination.read_bytes()
        ).hexdigest(),
    }


def _paired_period_return_summary(
    base_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    *,
    random_state: int,
) -> dict[str, Any]:
    base_dates = list(
        base_metrics.get("portfolio_period_return_dates") or ()
    )
    candidate_dates = list(
        candidate_metrics.get("portfolio_period_return_dates") or ()
    )
    base_values = list(base_metrics.get("portfolio_period_returns") or ())
    candidate_values = list(
        candidate_metrics.get("portfolio_period_returns") or ()
    )
    base = {
        str(day): float(value)
        for day, value in zip(base_dates, base_values)
    }
    candidate = {
        str(day): float(value)
        for day, value in zip(candidate_dates, candidate_values)
    }
    common = sorted(set(base).intersection(candidate))
    if not common:
        return {
            "periods": 0,
            "mean_delta": None,
            "bootstrap_95_ci": None,
        }
    differences = np.asarray(
        [candidate[day] - base[day] for day in common],
        dtype=float,
    )
    rng = np.random.default_rng(int(random_state))
    samples = np.asarray([
        float(rng.choice(differences, size=len(differences), replace=True).mean())
        for _ in range(2_000)
    ])
    return {
        "periods": len(common),
        "mean_delta": float(differences.mean()),
        "bootstrap_95_ci": [
            float(np.quantile(samples, 0.025)),
            float(np.quantile(samples, 0.975)),
        ],
    }


def _event_permutation_importance(
    bundle: ModelBundle,
    frame: pd.DataFrame,
    event_features: Sequence[str],
    *,
    random_state: int,
) -> dict[str, float | None]:
    target = pd.to_numeric(
        frame["excess_return"],
        errors="coerce",
    )
    baseline = _spearman(
        pd.Series(bundle.predict_excess_return(frame), index=frame.index),
        target,
    )
    rng = np.random.default_rng(int(random_state))
    importance: dict[str, float | None] = {}
    for name in event_features:
        permuted = frame.copy()
        values = permuted[name].to_numpy(copy=True)
        rng.shuffle(values)
        permuted[name] = values
        shuffled = _spearman(
            pd.Series(
                bundle.predict_excess_return(permuted),
                index=frame.index,
            ),
            target,
        )
        importance[name] = (
            float(baseline - shuffled)
            if baseline is not None and shuffled is not None
            else None
        )
    return importance


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    usable = pd.DataFrame({"left": left, "right": right}).dropna()
    if (
        len(usable) < 3
        or usable["left"].nunique() < 2
        or usable["right"].nunique() < 2
    ):
        return None
    return float(usable["left"].corr(usable["right"], method="spearman"))


def _numeric_delta(candidate: object, base: object) -> float:
    return float(candidate) - float(base)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float, np.integer, np.floating)) and bool(
        np.isfinite(float(value))
    )


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 公告事件因子增量评估",
        "",
        f"- 市场：`{report['market']}`",
        f"- 数据快照：`{report['snapshot_date']}`",
        f"- 总体状态：`{report['status']}`",
        f"- 可评估周期：`{report['qualified_horizons']}`",
        "- 当前动作：不改变正式模型或策略激活状态",
        "",
        "## 周期结果",
        "",
    ]
    for horizon, value in report["horizons"].items():
        lines.append(
            f"- {horizon} 日：`{value.get('status', 'unknown')}`"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "evaluate_intelligence_increment",
    "evaluate_latest_intelligence_effect",
    "refresh_latest_intelligence_features",
]
