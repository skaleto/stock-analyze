"""Bounded resources for the five-workspace React dashboard."""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import sqlite3
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_api import (
    _latest_strategy_model_usage,
    _public_intelligence_error,
    build_dashboard_intelligence_data,
)
from .dashboard_runtime import read_dashboard_runtime
from .overlay_guard import AVAILABLE_FACTORS_BY_MARKET, SENTIMENT_FACTORS
from .research.classical_specs import mainline_horizon, mainline_specs
from .research.feature_registry import DEFAULT_REGISTRY, INTELLIGENCE_FEATURES
from .research.models import TRAINING_PROTOCOL_VERSION


MAX_TABLE_ROWS = 20
MAX_RESOURCE_OBJECT_FIELDS = 64
MAX_ROLLBACK_ROWS = 5
MAX_FEATURE_ROWS = 20
MAX_MODEL_FEATURES = 20
MAX_TEXT_LENGTH = 1_000
MAX_DIAGNOSTIC_DEPTH = 4
MAX_DIAGNOSTIC_ITEMS = 8
MAX_DIAGNOSTIC_NODES = 128
MAX_DIAGNOSTIC_TEXT = 32_000
MAX_RESOURCE_NODES = 1_024
MAX_SERIALIZED_BYTES = 250_000
MAX_ABS_NUMERIC = 1_000_000_000_000_000
WORKSPACE_READ_ERRORS = (
    OSError,
    TypeError,
    ValueError,
    sqlite3.Error,
    agg.DashboardDataError,
)
MODEL_METRIC_KEYS = (
    "rank_ic",
    "mean_rank_ic",
    "icir",
    "brier_score",
    "auc",
    "brier_improvement",
    "hit_rate_lift",
    "hit_rate_uplift",
    "gross_return",
    "net_return",
    "benchmark_return",
    "net_excess_return",
    "turnover",
    "annual_turnover",
    "capital_utilization",
    "beginning_capital_utilization",
    "cash_ratio",
    "rebalance_frequency",
    "scheduled_rebalance_periods",
    "max_drawdown",
    "portfolio_sharpe",
    "portfolio_rebalance_periods",
    "effective_dates",
    "effective_non_overlapping_periods",
    "valid_trial_count",
    "trial_evidence_status",
    "deflated_sharpe_probability",
    "probability_of_backtest_overfit",
    "simulator_version",
    "total_execution_cost",
    "total_commission",
    "total_stamp_tax",
    "total_slippage",
    "execution_cost_bps",
    "impact_bps_p50",
    "impact_bps_p90",
    "impact_capped_notional_ratio",
    "missing_liquidity_notional_ratio",
    "execution_evidence_status",
    "execution_policy_version",
    "decision_count",
    "trade_allowed_count",
    "no_trade_count",
    "all_accounts_profitable",
    "all_accounts_positive_active",
    "forward_evidence_status",
    "forward_cycles",
    "forward_net_excess_return",
    "forward_max_drawdown",
    "forward_all_accounts_positive_active",
    "edge_calibration_available",
    "edge_calibration_reason",
    "edge_calibration_fit_max_date",
    "edge_calibration_version",
    "allocation_contract",
    "model_tilt_cap",
    "alpha_half_life_days",
    "attribution_status",
    "attribution_max_error",
    "cash_position_effect_total",
    "security_selection_return_total",
    "execution_cost_effect_total",
    "active_attribution_total",
    "diagnostic_net_return",
    "diagnostic_benchmark_return",
    "diagnostic_net_excess_return",
    "diagnostic_information_ratio",
    "diagnostic_annual_turnover",
    "diagnostic_trade_count",
    "diagnostic_capital_utilization",
    "gross_exposure_target",
    "gross_exposure_shortfall",
    "model_spec_id",
    "model_spec_hash",
    "training_protocol_version",
)
TABULAR_RESEARCH_METRICS = {
    "rank_ic": "rankIc",
    "icir": "icir",
    "raw_rank_ic": "rawRankIc",
    "raw_icir": "rawIcir",
    "portfolio_cagr": "portfolioCagr",
    "benchmark_cagr": "benchmarkCagr",
    "net_excess_return": "netExcessReturn",
    "max_drawdown": "maxDrawdown",
    "active_max_drawdown": "activeMaxDrawdown",
    "annual_turnover": "annualTurnover",
    "capital_utilization": "capitalUtilization",
    "portfolio_sharpe": "portfolioSharpe",
    "information_ratio": "informationRatio",
    "deflated_sharpe_probability": "deflatedSharpeProbability",
    "probability_of_backtest_overfit": "probabilityOfBacktestOverfit",
}
TABULAR_RESEARCH_LATEST_PATTERN = re.compile(
    r"^regime_tabular_alpha_(?P<as_of>\d{8})_(?P<scope>hs300|zz500)\.json$"
)
TABULAR_RESEARCH_BEST_PATTERN = re.compile(
    r"^regime_tabular_alpha_(?P<as_of>\d{8})_(?P<scope>hs300|zz500)_best\.json$"
)
TABULAR_RESEARCH_EXPERIMENT_PATTERN = re.compile(
    r"^regime_tabular_alpha_(?P<as_of>\d{8})_"
    r"(?P<scope>hs300|zz500)_(?P<config_hash>[0-9a-f]{16})\.json$"
)
CLASSICAL_LOOP_CLOSURE_PATTERN = re.compile(
    r"^classical_autonomous_loop_(?P<as_of>\d{8})\.json$"
)
PUBLIC_STRATEGIES = (
    ("defensive", "claude", "稳健防守"),
    ("trend", "codex", "趋势进攻"),
)
FORMAL_FACTOR_SOURCES = {
    "a_share": {
        "tushare_daily_basic": {"pe", "pb", "dividend_yield"},
        "tushare_fina_indicator_announced": {
            "roe",
            "gross_margin",
            "debt_ratio",
            "net_profit_growth",
        },
        "adjusted_ohlcv": {
            "momentum_20",
            "momentum_60",
            "low_volatility_60",
        },
    },
    "cn_qdii_etf": {
        "fund_daily_adjusted_ohlcv": {
            "momentum_20",
            "momentum_60",
            "low_volatility_60",
            "avg_amount_20",
        },
        "fund_nav": {"discount_premium"},
    },
}
OPERATIONS_MAIN_CHAIN = (
    (
        "market_snapshot",
        "行情与研究快照",
        ("stock-analyze-market-data.service",),
        "stock-analyze-market-data.timer",
    ),
    (
        "research",
        "特征、预测与评估",
        ("stock-analyze-research.service",),
        None,
    ),
    (
        "simulation",
        "正式策略模拟",
        (
            "stock-analyze-claude-daily.service",
            "stock-analyze-codex-daily.service",
            "stock-analyze-claude-cn-qdii-etf-daily.service",
            "stock-analyze-codex-cn-qdii-etf-daily.service",
        ),
        None,
    ),
    (
        "publish",
        "Dashboard 聚合与通知",
        ("stock-analyze-daily-finalize.service",),
        "stock-analyze-daily-summary.timer",
    ),
)
OPERATIONS_BACKGROUND = (
    (
        "intelligence_refresh",
        "情报增量采集",
        "stock-analyze-intelligence.service",
        "stock-analyze-intelligence.timer",
    ),
    (
        "model_iteration",
        "候选模型模拟",
        "stock-analyze-model-iteration.service",
        None,
    ),
    (
        "artifact_backfill",
        "PDF 下载与解析回填",
        "stock-analyze-intelligence-artifact-backfill.service",
        "stock-analyze-intelligence-artifact-backfill.timer",
    ),
    (
        "reconcile",
        "情报对账",
        "stock-analyze-intelligence-reconcile.service",
        "stock-analyze-intelligence-reconcile.timer",
    ),
    (
        "semantic",
        "LLM 语义抽取",
        "stock-analyze-intelligence-semantic.service",
        "stock-analyze-intelligence-semantic.timer",
    ),
    (
        "quality",
        "情报全库质量检查",
        "stock-analyze-intelligence-quality.service",
        "stock-analyze-intelligence-quality.timer",
    ),
)
OPERATIONS_TIMERS = {
    "stock-analyze-market-data.timer": ("行情与研究日链", "daily"),
    "stock-analyze-daily-summary.timer": ("每日运行摘要", "daily"),
    "stock-analyze-intelligence.timer": ("情报增量采集", "daily"),
    "stock-analyze-intelligence-reconcile.timer": ("情报对账", "daily"),
    "stock-analyze-intelligence-artifact-backfill.timer": (
        "PDF 下载解析回填",
        "daily",
    ),
    "stock-analyze-intelligence-semantic.timer": ("LLM 语义抽取", "daily"),
    "stock-analyze-intelligence-quality.timer": ("情报全库质量检查", "weekly"),
    "stock-analyze-ifind-source-audit.timer": ("iFinD 数据源审计", "daily"),
    "stock-analyze-weekly-trigger.timer": ("A股周度复盘", "weekly"),
    "stock-analyze-claude-cn-qdii-etf-weekly.timer": (
        "跨境ETF稳健防守周度复盘",
        "weekly",
    ),
    "stock-analyze-codex-cn-qdii-etf-weekly.timer": (
        "跨境ETF趋势进攻周度复盘",
        "weekly",
    ),
    "stock-analyze-qdii-research.timer": ("跨境ETF周度研究", "weekly"),
    "stock-analyze-weekly-summary.timer": ("每周运行摘要", "weekly"),
    "stock-analyze-monthly-review.timer": ("月度策略复盘", "monthly"),
    "stock-analyze-model-training.timer": ("月度模型训练", "monthly"),
    "stock-analyze-monthly-summary.timer": ("每月运行摘要", "monthly"),
}
OPERATIONS_SCOPES = {"all", "a_share", "cn_qdii_etf", "exceptions"}
OPERATIONS_TIMEZONE = ZoneInfo("Asia/Shanghai")
OPERATIONS_SIMULATION_UNITS_BY_MARKET = {
    "a_share": (
        "stock-analyze-claude-daily.service",
        "stock-analyze-codex-daily.service",
    ),
    "cn_qdii_etf": (
        "stock-analyze-claude-cn-qdii-etf-daily.service",
        "stock-analyze-codex-cn-qdii-etf-daily.service",
    ),
}


def _safe_workspace_read(
    errors: list[dict[str, str]],
    resource: str,
    fallback: Any,
    reader: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return reader(*args, **kwargs)
    except WORKSPACE_READ_ERRORS:
        errors.append({"resource": resource, "reason": "unavailable"})
        return fallback


def _workspace_resource_unavailable(
    value: Any,
    *,
    errors: list[dict[str, str]],
    resource: str,
) -> bool:
    if any(item.get("resource") == resource for item in errors):
        return True
    status = _text(_mapping(value).get("status"), limit=128).strip().lower()
    return status in {"error", "failed", "unavailable"}


def _empty_intelligence_workspace() -> dict[str, Any]:
    worker_stage = {
        "leased": 0,
        "importing": 0,
        "imported": 0,
        "partial": 0,
        "failed": 0,
        "expired": 0,
    }
    decisions = {
        "canonical": 0,
        "no_event": 0,
        "quarantined": 0,
        "failed": 0,
    }
    return {
        "pipeline": {
            "status": "unavailable",
            "documents": 0,
            "artifacts": {},
            "stages": {
                "catalogued": 0,
                "pdfReady": 0,
                "parsed": 0,
                "semanticCompleted": 0,
                "canonicalEvents": 0,
            },
            "backlog": {
                "download": 0,
                "parse": 0,
                "semantic": 0,
                "total": 0,
            },
            "sources": [],
            "snapshotGeneratedAt": None,
            "artifactWorkers": {
                "status": "unavailable",
                "activeLeases": 0,
                "leasedDocuments": 0,
                "completedDocuments": 0,
                "downloadedDocuments": 0,
                "parsedDocuments": 0,
                "latestFinishedAt": None,
                "stages": {
                    "download": dict(worker_stage),
                    "parse": dict(worker_stage),
                },
            },
        },
        "extraction": {
            "status": "unavailable",
            "semanticRuns": {},
            "decisions": dict(decisions),
            "latestBatch": None,
            "contract": {},
        },
        "factorSupply": {
            "status": "unavailable",
            "snapshotDate": None,
            "rows": 0,
            "reportName": None,
            "factorSet": None,
            "factorSets": [],
            "suppliedFactors": 0,
            "modelEligible": False,
            "modelEligibleFactors": [],
            "factors": [],
            "lifecycleCounts": {},
        },
        "modelImpact": {
            "status": "unavailable",
            "asOf": None,
            "snapshotDate": None,
            "reportName": None,
            "factorSet": None,
            "qualifiedHorizons": 0,
            "activation": "unavailable",
            "adopted": False,
            "activeFactors": [],
            "iterationFactors": [],
            "reason": "intelligence_status_unavailable",
            "horizons": [],
        },
        "decisions": decisions,
    }


def _generated_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _root(repo_root: str | Path | None) -> Path:
    return Path(repo_root) if repo_root is not None else Path.cwd()


def _check_market(market: str) -> None:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)


def _integer(value: Any) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return normalized if abs(normalized) <= MAX_ABS_NUMERIC else 0


def _optional_integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if abs(normalized) <= MAX_ABS_NUMERIC else None


def _text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    return str(value or "")[:limit]


def _scalar(value: Any, *, text_limit: int = MAX_TEXT_LENGTH) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_ABS_NUMERIC else None
    if isinstance(value, float):
        return (
            value
            if math.isfinite(value) and abs(value) <= MAX_ABS_NUMERIC
            else None
        )
    timestamp = _iso_timestamp(value)
    if isinstance(timestamp, str):
        return timestamp[:text_limit]
    return None


def _finite_number(value: Any, *, default: float = 0.0) -> int | float | bool:
    sanitized = _scalar(value)
    if isinstance(sanitized, (bool, int, float)):
        return sanitized
    return default


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, dict)]


def _iso_timestamp(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            normalized = isoformat()
        except (TypeError, ValueError, OverflowError):
            return value
        if isinstance(normalized, str):
            return normalized
    return value


def _bounded_diagnostics(value: Any) -> Any:
    budget = {"nodes": MAX_DIAGNOSTIC_NODES, "text": MAX_DIAGNOSTIC_TEXT}

    def sanitize(item: Any, depth: int) -> Any:
        if budget["nodes"] <= 0:
            return None
        budget["nodes"] -= 1
        if depth >= MAX_DIAGNOSTIC_DEPTH:
            return None
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in list(item.items())[:MAX_DIAGNOSTIC_ITEMS]:
                if budget["nodes"] <= 0:
                    break
                result[_text(key, limit=128)] = sanitize(child, depth + 1)
            return result
        if isinstance(item, (list, tuple, set)):
            values = (
                sorted(item, key=str)
                if isinstance(item, set)
                else list(item)
            )
            return [
                sanitize(child, depth + 1)
                for child in values[:MAX_DIAGNOSTIC_ITEMS]
                if budget["nodes"] > 0
            ]
        if isinstance(item, float) and not math.isfinite(item):
            return None
        if item is None or isinstance(item, (bool, int, float)):
            return item
        timestamp = _iso_timestamp(item)
        text = _text(timestamp, limit=min(MAX_TEXT_LENGTH, budget["text"]))
        budget["text"] -= len(text)
        return text

    return sanitize(value, 0)


def _algorithm_family(model: dict[str, Any]) -> str:
    explicit = _text(
        model.get("algorithm_family") or model.get("model_family"),
        limit=128,
    ).strip()
    if explicit:
        return explicit
    return "boosting_ensemble" if model.get("use_boosting") else "multinomial_logit"


def _registry_evidence(
    root: Path,
    market: str,
    horizon: int,
    model_version: str,
    account_scope: str = "",
) -> tuple[dict[str, Any], bool]:
    model_root = root / "data" / "research" / "models" / market
    if account_scope:
        model_root = model_root / account_scope
    registry_path = model_root / str(horizon) / "registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}, False
    if not isinstance(registry, dict):
        return {}, False
    models = registry.get("models")
    if not isinstance(models, dict):
        return {}, False
    record = models.get(model_version)
    if not isinstance(record, dict):
        return {}, False
    return record, str(registry.get("champion_model_version") or "") == model_version


def _model_artifact_ref(
    root: Path,
    market: str,
    horizon: int,
    model_version: str,
    registry_record: dict[str, Any],
    account_scope: str = "",
) -> str | None:
    model_root = root / "data" / "research" / "models" / market
    if account_scope:
        model_root = model_root / account_scope
    model_root = model_root / str(horizon)
    try:
        resolved_model_root = model_root.resolve()
    except OSError:
        return None
    registered = _text(registry_record.get("artifact"), limit=4_096).strip()
    candidates: list[Path] = []
    if registered:
        candidate = Path(registered)
        candidates.append(candidate if candidate.is_absolute() else root / candidate)
        if not candidate.is_absolute():
            candidates.append(model_root / candidate)
    candidates.extend(sorted(model_root.glob(f"*-{model_version}.joblib"), reverse=True))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
            resolved.relative_to(resolved_model_root)
            return str(resolved.relative_to(root.resolve()))
        except (OSError, ValueError):
            continue
    return None


def _champion_activated_at(registry_record: dict[str, Any]) -> Any:
    for gate in reversed(_rows(registry_record.get("gate_history"))):
        if gate.get("passed") is True and gate.get("target_status") == "active":
            return _scalar(gate.get("evaluated_at"), text_limit=256)
    return None


def _evidence_time(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return float("-inf")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return float("-inf")


def _model_evidence_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    completeness = sum(
        (
            row.get("registryStatus") == "available",
            row.get("artifactStatus") == "available",
            bool(row.get("artifactRef")),
        )
    )
    deterministic = json.dumps(
        row,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return (
        _evidence_time(row.get("registeredAt")),
        _evidence_time(row.get("trainedAt")),
        completeness,
        deterministic,
    )


def _model_rows(
    root: Path,
    market: str,
    health: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _rows(health.get("models")):
        training_metrics = _mapping(raw.get("metrics"))
        gate_metrics = _mapping(raw.get("gate_metrics"))
        metrics = {**training_metrics, **gate_metrics}
        all_features = sorted(
            {
                _text(value, limit=128)
                for value in (raw.get("feature_columns") or [])
                if value
            }
        )
        horizon = _integer(raw.get("horizon"))
        model_version = _text(raw.get("model_version"), limit=256)
        account_scope = _text(raw.get("account_scope"), limit=128)
        registry_record, registry_champion = _registry_evidence(
            root,
            market,
            horizon,
            model_version,
            account_scope,
        )
        artifact_ref = _model_artifact_ref(
            root,
            market,
            horizon,
            model_version,
            registry_record,
            account_scope,
        )
        gate_reasons = list(dict.fromkeys([
            _text(reason, limit=256)
            for reason in (
                list(raw.get("gate_reasons") or [])
                + list(raw.get("rejection_reasons") or [])
            )
            if reason
        ]))[:MAX_TABLE_ROWS]
        registry_active = str(registry_record.get("status") or "") == "active"
        rows.append(
            {
                "modelVersion": model_version,
                "specId": _text(
                    raw.get("spec_id")
                    or registry_record.get("spec_id")
                    or metrics.get("model_spec_id"),
                    limit=256,
                ),
                "specHash": _text(
                    raw.get("spec_hash")
                    or registry_record.get("spec_hash")
                    or metrics.get("model_spec_hash"),
                    limit=128,
                ),
                "trainingProtocol": _text(
                    metrics.get("training_protocol_version"),
                    limit=128,
                ),
                "accountScope": account_scope,
                "horizon": horizon,
                "algorithmFamily": _algorithm_family(raw),
                "trainedAt": (
                    _scalar(
                        raw.get("trained_at")
                        or raw.get("created_at"),
                        text_limit=256,
                    )
                ),
                "registeredAt": _scalar(
                    registry_record.get("registered_at"),
                    text_limit=256,
                ),
                "activatedAt": _champion_activated_at(registry_record),
                "sampleSupport": _integer(raw.get("sample_support")),
                "featureColumns": all_features[:MAX_MODEL_FEATURES],
                "_allFeatureColumns": all_features,
                "registryStatus": "available" if registry_record else "missing",
                "lifecycleStatus": _text(
                    registry_record.get("status") or raw.get("status") or "research",
                    limit=128,
                ),
                "artifactRef": artifact_ref,
                "artifactStatus": "available" if artifact_ref else "missing",
                "gatePassed": raw.get("gate_passed") is True,
                "gateReasons": gate_reasons,
                "roleGates": _bounded_diagnostics(raw.get("role_gates")) or {},
                "evaluation": _bounded_diagnostics(
                    raw.get("research_evaluation")
                ) or {},
                "shadowCycles": _integer(raw.get("shadow_cycles")),
                "shadowCyclesRemaining": _integer(
                    raw.get("shadow_cycles_remaining")
                ),
                "isChampion": bool(
                    raw.get("is_champion") is True
                    and registry_champion
                    and registry_active
                    and artifact_ref
                ),
                "pointInTimeAudit": _scalar(
                    metrics.get("point_in_time_audit"),
                    text_limit=128,
                ),
                "candidateFeatureCount": _integer(
                    metrics.get("candidate_feature_count")
                )
                or len(all_features),
                "baselineComparison": _bounded_diagnostics(
                    metrics.get("baseline_comparison")
                ) or {},
                "accountMetrics": _bounded_diagnostics(
                    metrics.get("account_metrics")
                ) or {},
                "noTradeReasonCounts": _bounded_diagnostics(
                    metrics.get("no_trade_reason_counts")
                ) or {},
                "metrics": {
                    key: _scalar(metrics.get(key))
                    for key in MODEL_METRIC_KEYS
                    if key in metrics
                },
            }
        )
    deduplicated: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["accountScope"], row["horizon"], row["modelVersion"])
        current = deduplicated.get(key)
        if current is None or _model_evidence_rank(row) > _model_evidence_rank(
            current
        ):
            deduplicated[key] = row
    return [deduplicated[key] for key in sorted(deduplicated)]


def _deduplicate_model_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["accountScope"], row["horizon"], row["modelVersion"])
        current = deduplicated.get(key)
        if current is None or _model_evidence_rank(row) > _model_evidence_rank(
            current
        ):
            deduplicated[key] = row
    return [deduplicated[key] for key in sorted(deduplicated)]


def _mainline_model_projection(
    market: str,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_horizon = mainline_horizon(market)
    current: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, int, str]] = set()
    lifecycle_rank = {
        "active": 4,
        "shadow": 3,
        "research": 2,
        "rejected": 1,
    }
    declared_scopes = sorted({
        str(row.get("accountScope") or "") for row in rows
    })
    has_account_scoped_models = any(declared_scopes)
    for scope in declared_scopes:
        if not scope and has_account_scoped_models:
            continue
        expected_specs = mainline_specs(market, scope)
        expected_spec_id = expected_specs[0].spec_id if len(expected_specs) == 1 else ""
        expected_spec_hash = expected_specs[0].spec_hash if len(expected_specs) == 1 else ""
        horizon_rows = [
            row
            for row in rows
            if str(row.get("accountScope") or "") == scope
            and _integer(row.get("horizon")) == expected_horizon
        ]
        exact = [
            row for row in horizon_rows
            if str(row.get("specId") or "") == expected_spec_id
            and (
                not scope
                or (
                    str(row.get("specHash") or "") == expected_spec_hash
                    and str(row.get("trainingProtocol") or "")
                    == TRAINING_PROTOCOL_VERSION
                )
            )
        ]
        candidates = exact or [
            row for row in horizon_rows
            if not scope and not str(row.get("specId") or "")
        ]
        if not candidates:
            continue
        selected = max(
            candidates,
            key=lambda row: (
                lifecycle_rank.get(str(row.get("lifecycleStatus") or ""), 0),
                _model_evidence_rank(row),
            ),
        )
        current.append(selected)
        selected_keys.add(
            (
                selected["accountScope"],
                selected["horizon"],
                selected["modelVersion"],
            )
        )
    archive = [
        row for row in rows
        if (row["accountScope"], row["horizon"], row["modelVersion"])
        not in selected_keys
    ]
    archive.sort(key=_model_evidence_rank, reverse=True)
    current.sort(key=lambda row: (row["accountScope"], row["modelVersion"]))
    return current, archive


def _public_model_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _latest_tournament_health(root: Path, market: str) -> dict[str, Any]:
    model_root = root / "data" / "research" / "models" / market
    models: list[dict[str, Any]] = []
    if not model_root.exists():
        return {"status": "unavailable", "models": []}
    for account_dir in sorted(path for path in model_root.iterdir() if path.is_dir()):
        for horizon_dir in sorted(
            (path for path in account_dir.iterdir() if path.is_dir() and path.name.isdigit()),
            key=lambda path: int(path.name),
        ):
            summaries = sorted(
                (horizon_dir / "tournaments").glob("*/summary.json")
            )
            if not summaries:
                continue
            try:
                summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("tournament_summary_invalid") from exc
            for candidate in _rows(summary.get("candidates")):
                if candidate.get("model_version"):
                    activation_metrics = _mapping(
                        _mapping(candidate.get("activation_evidence")).get(
                            "metrics"
                        )
                    )
                    models.append(
                        {
                            **candidate,
                            "account_scope": str(
                                candidate.get("account_scope") or account_dir.name
                            ),
                            "horizon": int(
                                candidate.get("horizon") or horizon_dir.name
                            ),
                            "metrics": {
                                **_mapping(candidate.get("metrics")),
                                **activation_metrics,
                            },
                        }
                    )
    return {
        "status": "available" if models else "unavailable",
        "models": models,
    }


def _latest_baseline_first_health(root: Path, market: str) -> dict[str, Any]:
    """Expose a baseline decision even when the residual produced no model."""

    report_root = root / "reports" / "research"
    if not report_root.exists():
        return {"status": "unavailable", "models": []}
    latest_by_scope: dict[str, dict[str, Any]] = {}
    paths = sorted(
        report_root.glob("baseline_first_*.json"),
        reverse=True,
    )[: MAX_TABLE_ROWS * 5]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("baseline_first_report_invalid") from exc
        if (
            not isinstance(payload, dict)
            or str(payload.get("market") or "") != market
        ):
            continue
        scope = _text(payload.get("account_scope"), limit=128)
        if not scope or scope in latest_by_scope:
            continue
        candidate = _mapping(payload.get("candidate"))
        baseline = _mapping(payload.get("baseline"))
        gate = _mapping(payload.get("incremental_gate"))
        admission = _mapping(payload.get("shadow_admission"))
        deployment_gate = _mapping(
            admission.get("deployment_gate") or payload.get("deployment_gate")
        )
        as_of = _text(payload.get("as_of"), limit=32)
        trained_at = (
            f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:8]}"
            if len(as_of) == 8 and as_of.isdigit()
            else as_of
        )
        status = str(payload.get("status") or "insufficient_evidence")
        horizon = int(payload.get("horizon") or mainline_horizon(market))
        admitted_version = _text(admission.get("model_version"), limit=256)
        confirmed_record: dict[str, Any] = {}
        if (
            status == "development_pass"
            and payload.get("registry_mutated") is True
            and admission.get("admitted") is True
            and admitted_version
        ):
            registry_path = (
                root / "data" / "research" / "models" / market
                / scope / str(horizon) / "registry.json"
            )
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                record = _mapping(
                    _mapping(registry.get("models")).get(admitted_version)
                )
                artifact = Path(str(record.get("artifact") or ""))
                if artifact and not artifact.is_absolute():
                    artifact = root / artifact
                if (
                    str(record.get("status") or "") in {"shadow", "active"}
                    and artifact.is_file()
                ):
                    confirmed_record = record
            except (OSError, json.JSONDecodeError, TypeError):
                confirmed_record = {}
        confirmed_admission = bool(confirmed_record)
        displayed_gate = (
            deployment_gate
            if status == "deployment_blocked"
            else gate
        )
        gate_passed = bool(
            confirmed_admission
            and gate.get("passed") is True
            and (
                not deployment_gate
                or deployment_gate.get("passed") is True
            )
        )
        latest_by_scope[scope] = {
            "model_version": (
                admitted_version
                if confirmed_admission
                else (
                    f"baseline-first-{as_of}-"
                    f"{str(payload.get('model_spec_hash') or '')[:8]}"
                )
            ),
            "spec_id": str(payload.get("model_spec_id") or ""),
            "spec_hash": str(payload.get("model_spec_hash") or ""),
            "account_scope": scope,
            "horizon": horizon,
            "algorithm_family": "transparent_baseline_plus_ridge_residual",
            "trained_at": trained_at,
            "sample_support": int(candidate.get("oos_predictions") or 0),
            "feature_columns": list(
                candidate.get("selected_features") or []
            ),
            "status": (
                "rejected"
                if status == "baseline_wins"
                else str(confirmed_record.get("status") or "shadow")
                if confirmed_admission
                else "research"
            ),
            "gate_passed": gate_passed,
            "gate_reasons": list(displayed_gate.get("reasons") or []),
            "metrics": {
                **candidate,
                "training_protocol_version": str(
                    payload.get("training_protocol_version")
                    or TRAINING_PROTOCOL_VERSION
                ),
                "candidate_feature_count": int(
                    candidate.get("selected_feature_count") or 0
                ),
                "baseline_comparison": {
                    "transparent_baseline": baseline,
                    "candidate_increment": {
                        key: gate.get(key)
                        for key in (
                            "net_excess_return_delta",
                            "max_drawdown_delta",
                            "annual_turnover_delta",
                            "positive_fold_count",
                            "eligible_fold_count",
                        )
                        if key in gate
                    },
                },
            },
            "research_evaluation": {
                "contract": str(payload.get("evaluation_contract") or ""),
                "status": status,
                "decision": str(payload.get("decision") or status),
                "improvement": _mapping(payload.get("improvement")),
                "incremental_gate": gate,
                "deployment_gate": deployment_gate,
                "observed_final_status": str(
                    payload.get("observed_final_status") or ""
                ),
            },
            "is_champion": False,
        }
    return {
        "status": "available" if latest_by_scope else "unavailable",
        "models": [latest_by_scope[key] for key in sorted(latest_by_scope)],
    }


def _latest_unified_arena(root: Path, market: str) -> dict[str, Any]:
    arena_root = root / "data" / "research" / "unified_arena" / market
    reports = sorted(
        path / "report.json"
        for path in arena_root.iterdir()
        if path.is_dir() and path.name.isdigit()
    ) if arena_root.exists() else []
    if not reports:
        return {
            "status": "unavailable",
            "evidenceType": "historical_diagnostic",
            "asOf": None,
            "horizon": 0,
            "scopes": [],
        }
    try:
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unified_arena_report_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("unified_arena_report_invalid")
    metric_names = {
        "net_return": "netReturn",
        "benchmark_return": "benchmarkReturn",
        "net_excess_return": "netExcessReturn",
        "information_ratio": "informationRatio",
        "portfolio_sharpe": "sharpe",
        "max_drawdown": "maxDrawdown",
        "annual_turnover": "annualTurnover",
        "trade_count": "tradeCount",
        "capital_utilization": "capitalUtilization",
        "beginning_capital_utilization": "beginningCapitalUtilization",
        "cash_position_effect_total": "cashPositionEffectTotal",
        "security_selection_return_total": "securitySelectionReturnTotal",
        "execution_cost_effect_total": "executionCostEffectTotal",
        "active_attribution_total": "activeAttributionTotal",
    }
    scopes: list[dict[str, Any]] = []
    for raw_scope in _rows(payload.get("scopes"))[:MAX_TABLE_ROWS]:
        participants = []
        for row in _rows(raw_scope.get("participants"))[:MAX_TABLE_ROWS]:
            metrics = _mapping(row.get("metrics"))
            participants.append({
                "participantId": _text(
                    row.get("participant_id"),
                    limit=256,
                ),
                "participantType": _text(
                    row.get("participant_type"),
                    limit=64,
                ),
                "name": _text(row.get("name"), limit=256),
                "status": _text(row.get("status"), limit=64),
                "metrics": {
                    public: _finite_number(metrics.get(source))
                    for source, public in metric_names.items()
                    if source in metrics
                },
            })
        winner = _mapping(raw_scope.get("winner"))
        scopes.append({
            "accountScope": _text(
                raw_scope.get("account_scope"),
                limit=128,
            ),
            "finalWindow": [
                _text(value, limit=32)
                for value in list(raw_scope.get("final_window") or [])[:2]
            ],
            "evaluationDateCount": _integer(
                raw_scope.get("evaluation_date_count")
            ),
            "winner": (
                {
                    "participantId": _text(
                        winner.get("participant_id"),
                        limit=256,
                    ),
                    "name": _text(winner.get("name"), limit=256),
                    "netExcessReturn": _finite_number(
                        winner.get("net_excess_return")
                    ),
                }
                if winner else None
            ),
            "participants": participants,
        })
    return {
        "status": _text(payload.get("status"), limit=64) or "unavailable",
        "evidenceType": (
            _text(payload.get("evidence_type"), limit=64)
            or "historical_diagnostic"
        ),
        "asOf": _scalar(payload.get("as_of"), text_limit=32),
        "horizon": _integer(payload.get("horizon")),
        "scopes": scopes,
    }


def _source_rows(value: Any) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _rows(value):
        has_error = bool(row.get("error") or row.get("error_summary"))
        rows = _integer(row.get("rows"))
        status = _text(row.get("status"), limit=128)
        if not status:
            if has_error or bool(row.get("failed")):
                status = "failed"
            else:
                status = "available" if rows > 0 else "empty"
        evidence = {
            "source": _text(row.get("source"), limit=256),
            "status": status,
            "rows": rows,
            "failed": bool(row.get("failed")),
            "as_of": _scalar(row.get("as_of"), text_limit=256),
            "error": "数据源状态读取失败" if has_error else None,
        }
        identity = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
        if identity in seen:
            continue
        seen.add(identity)
        bounded.append(evidence)
        if len(bounded) >= MAX_TABLE_ROWS:
            break
    return bounded


def _usage_rows(
    value: Any,
    *,
    market: str,
    champion_models: set[tuple[int, str]],
) -> list[dict[str, Any]]:
    by_agent: dict[str, dict[str, Any]] = {}
    for row in _rows(value):
        if row.get("market") != market or row.get("status") != "active":
            continue
        raw_versions = row.get("model_versions")
        versions = _mapping(raw_versions)
        evidenced_versions = {
            _text(horizon, limit=32): _text(version, limit=256)
            for horizon, version in versions.items()
            if (
                _integer(horizon),
                _text(version, limit=256),
            )
            in champion_models
        }
        if not evidenced_versions:
            continue
        agent = _text(row.get("agent"), limit=128)
        if not agent:
            continue
        evidence = {
            "market": market,
            "agent": agent,
            "strategy_label": _text(
                row.get("strategy_label") or row.get("agent"),
                limit=256,
            ),
            "as_of": _scalar(row.get("as_of"), text_limit=256),
            "status": "active",
            "applied_candidates": _integer(row.get("applied_candidates")),
            "candidate_coverage": _finite_number(row.get("candidate_coverage")),
            "model_versions": evidenced_versions,
            "fallback_reason": _text(
                row.get("fallback_reason"),
                limit=MAX_TEXT_LENGTH,
            ),
            "accounts": _integer(row.get("accounts")),
        }
        current = by_agent.get(agent)
        evidence_rank = (
            _evidence_time(evidence["as_of"]),
            json.dumps(evidence, sort_keys=True, ensure_ascii=False),
        )
        current_rank = (
            _evidence_time(current["as_of"]),
            json.dumps(current, sort_keys=True, ensure_ascii=False),
        ) if current is not None else None
        if current_rank is None or evidence_rank > current_rank:
            by_agent[agent] = evidence
    return [by_agent[agent] for agent in sorted(by_agent)][:MAX_TABLE_ROWS]


def _candidate(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    if not raw:
        return {}
    keys = (
        "model_version",
        "display_version",
        "status",
        "status_label",
        "selected_at",
        "registered_at",
        "shadow_cycles",
        "shadow_cycles_remaining",
        "horizon",
        "account_scope",
        "candidate_kind",
        "admission_grade",
        "source_campaign",
        "source_trial_id",
        "promotion_policy",
    )
    result: dict[str, Any] = {}
    for key in keys:
        if key not in raw:
            continue
        if key in {"shadow_cycles", "shadow_cycles_remaining"}:
            result[key] = _integer(raw.get(key))
        elif key == "horizon":
            value = _scalar(raw.get(key), text_limit=32)
            result[key] = (
                value
                if isinstance(value, (bool, int, float, str))
                else None
            )
        elif key in {"selected_at", "registered_at"}:
            result[key] = _scalar(raw.get(key), text_limit=256)
        else:
            result[key] = _text(raw.get(key), limit=256)
    return result


def _simulation_account(iteration: dict[str, Any]) -> dict[str, Any] | None:
    account_id = _text(iteration.get("account_id"), limit=256)
    account_label = _text(
        iteration.get("portfolio_label") or iteration.get("label"),
        limit=256,
    )
    isolation = _text(iteration.get("isolation"), limit=MAX_TEXT_LENGTH)
    portfolio_ref = _text(
        iteration.get("portfolio_ref") or iteration.get("portfolio_path"),
        limit=MAX_TEXT_LENGTH,
    )
    if not any((account_id, account_label, isolation, portfolio_ref)):
        return None
    return {
        "accountId": account_id,
        "accountLabel": account_label,
        "isolation": isolation,
        "navRows": _integer(iteration.get("nav_rows")),
        "portfolioRef": portfolio_ref,
    }


def _simulation_accounts(iteration: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = {
        _text(raw.get("account_id") or raw.get("id"), limit=256): raw
        for raw in _rows(iteration.get("account_candidates"))
        if _text(raw.get("account_id") or raw.get("id"), limit=256)
    }
    rows: list[dict[str, Any]] = []
    for raw in _rows(iteration.get("accounts")):
        account_id = _text(raw.get("account_id") or raw.get("id"), limit=256)
        if not account_id:
            continue
        candidate = candidates.get(account_id, {})
        rows.append({
            "accountId": account_id,
            "scope": _text(raw.get("scope"), limit=256),
            "benchmark": _text(raw.get("benchmark"), limit=256),
            "selectedCount": _integer(raw.get("selected_count")),
            "candidateVersion": _text(candidate.get("model_version"), limit=256),
            "candidateLabel": _text(candidate.get("display_version"), limit=256),
            "candidateKind": _text(candidate.get("candidate_kind"), limit=128),
            "admissionGrade": _text(candidate.get("admission_grade"), limit=128),
            "candidateStatus": _text(candidate.get("status"), limit=128),
            "candidateStatusLabel": _text(candidate.get("status_label"), limit=256),
            "sourceCampaign": _text(candidate.get("source_campaign"), limit=256),
            "sourceTrialId": _text(candidate.get("source_trial_id"), limit=256),
            "participationStatus": _text(
                raw.get("participation_status")
                or candidate.get("participation_status"),
                limit=128,
            ),
            "predictionStatus": _text(candidate.get("prediction_status"), limit=128),
            "rebalanceFrequency": _text(
                raw.get("rebalance_frequency"), limit=128
            ),
            "rebalanceDue": (
                bool(raw.get("rebalance_due"))
                if raw.get("rebalance_due") is not None
                else None
            ),
            "lastRebalanceSignalDate": _scalar(
                raw.get("last_rebalance_signal_date"), text_limit=256
            ),
            "targetRiskyExposure": _finite_number(
                raw.get("target_risky_exposure")
            ),
            "date": _scalar(raw.get("date"), text_limit=256),
            "cash": _finite_number(raw.get("cash")),
            "marketValue": _finite_number(raw.get("market_value")),
            "totalValue": _finite_number(raw.get("total_value")),
            "benchmarkClose": _finite_number(raw.get("benchmark_close")),
        })
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _model_evaluation(model: dict[str, Any] | None) -> dict[str, Any]:
    if not model:
        return {
            "status": "unavailable",
            "simulatorVersion": None,
            "baselineComparison": {},
            "accountMetrics": {},
        }
    metrics = _mapping(model.get("metrics"))
    return {
        "status": (
            "available"
            if metrics.get("simulator_version") == "paper-parity-daily-v1"
            else "unavailable"
        ),
        "modelVersion": model.get("modelVersion"),
        "simulatorVersion": metrics.get("simulator_version"),
        "grossReturn": metrics.get("gross_return"),
        "netReturn": metrics.get("net_return"),
        "benchmarkReturn": metrics.get("benchmark_return"),
        "netExcessReturn": metrics.get("net_excess_return"),
        "maxDrawdown": metrics.get("max_drawdown"),
        "annualTurnover": metrics.get("annual_turnover"),
        "capitalUtilization": metrics.get("capital_utilization"),
        "cashRatio": metrics.get("cash_ratio"),
        "rebalanceFrequency": metrics.get("rebalance_frequency"),
        "scheduledRebalancePeriods": _optional_integer(
            metrics.get("scheduled_rebalance_periods")
        ),
        "sharpe": metrics.get("portfolio_sharpe"),
        "executionCost": metrics.get("total_execution_cost"),
        "executionCostBps": metrics.get("execution_cost_bps"),
        "impactBpsP50": metrics.get("impact_bps_p50"),
        "impactBpsP90": metrics.get("impact_bps_p90"),
        "impactCappedNotionalRatio": metrics.get(
            "impact_capped_notional_ratio"
        ),
        "missingLiquidityNotionalRatio": metrics.get(
            "missing_liquidity_notional_ratio"
        ),
        "executionEvidenceStatus": metrics.get("execution_evidence_status"),
        "executionPolicyVersion": metrics.get("execution_policy_version"),
        "edgeCalibrationVersion": metrics.get("edge_calibration_version"),
        "allocationContract": metrics.get("allocation_contract"),
        "modelTiltCap": metrics.get("model_tilt_cap"),
        "decisionCount": _optional_integer(metrics.get("decision_count")),
        "tradeAllowedCount": _optional_integer(
            metrics.get("trade_allowed_count")
        ),
        "noTradeCount": _optional_integer(metrics.get("no_trade_count")),
        "noTradeReasonCounts": _mapping(model.get("noTradeReasonCounts")),
        "effectivePeriods": _integer(
            metrics.get("effective_non_overlapping_periods")
        ),
        "validTrialCount": _integer(metrics.get("valid_trial_count")),
        "trialEvidenceStatus": _text(
            metrics.get("trial_evidence_status"),
            limit=128,
        ),
        "baselineComparison": _mapping(model.get("baselineComparison")),
        "accountMetrics": _mapping(model.get("accountMetrics")),
    }


def _model_attribution_evidence(root: Path, market: str) -> dict[str, Any]:
    lineage_path = root / "data" / "shared" / "research_lineage.sqlite3"
    if not lineage_path.exists():
        return {
            "status": "unavailable",
            "formalModelApplied": False,
            "completeCount": 0,
            "totalCount": 0,
            "rows": [],
        }
    from .research.lineage import ResearchLineageStore

    lineage = ResearchLineageStore(lineage_path)
    decisions = [
        row
        for row in lineage.query("decision_runs")
        if str(row.get("market") or "") == market
    ]
    decision_by_id = {
        str(row.get("decision_run_id") or ""): row
        for row in decisions
    }
    raw_rows = [
        row
        for row in lineage.query("pnl_attributions")
        if str(row.get("decision_run_id") or "") in decision_by_id
        and str(row.get("security_code") or "") == "__PORTFOLIO__"
    ]
    raw_rows.sort(
        key=lambda row: (
            str(row.get("as_of") or ""),
            str(row.get("pnl_attribution_id") or ""),
        ),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for raw in raw_rows[:MAX_TABLE_ROWS]:
        decision = decision_by_id.get(str(raw.get("decision_run_id") or ""), {})
        model_versions = _mapping(
            raw.get("model_versions") or decision.get("model_versions")
        )
        policy_status = _text(
            raw.get("model_policy_status")
            or decision.get("model_policy_status")
            or "rule_only",
            limit=128,
        )
        rows.append({
            "asOf": _scalar(raw.get("as_of"), text_limit=256),
            "strategyId": _text(
                raw.get("strategy_id") or decision.get("strategy_id"),
                limit=256,
            ),
            "accountId": _text(
                raw.get("account_id") or decision.get("account_id"),
                limit=256,
            ),
            "status": _text(raw.get("status"), limit=128),
            "modelPolicyStatus": policy_status,
            "modelVersions": {
                _text(key, limit=32): _text(value, limit=256)
                for key, value in model_versions.items()
            },
            "netPnl": _finite_number(raw.get("net_pnl")),
            "modelSelectionPnl": _finite_number(
                raw.get("model_selection_pnl")
            ),
            "explainedRatio": _finite_number(raw.get("explained_ratio")),
            "residualRatio": _finite_number(raw.get("residual_ratio")),
            "positiveDrivers": _bounded_diagnostics(
                raw.get("top_positive_drivers")
            ) or [],
            "negativeDrivers": _bounded_diagnostics(
                raw.get("top_negative_drivers")
            ) or [],
            "unavailableInputs": [
                _text(value, limit=256)
                for value in (raw.get("unavailable_inputs") or [])
            ][:MAX_TABLE_ROWS],
        })
    applied = any(
        row["modelPolicyStatus"] in {"active", "champion"}
        and bool(row["modelVersions"])
        for row in rows
    )
    return {
        "status": "available" if rows else "empty",
        "formalModelApplied": applied,
        "completeCount": sum(row["status"] == "complete" for row in rows),
        "totalCount": len(rows),
        "rows": rows,
    }


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def _enforce_serialized_size(payload: dict[str, Any]) -> dict[str, Any]:
    payload["truncated"] = False
    payload["truncationReason"] = None
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    payload["truncated"] = True
    payload["truncationReason"] = "serialized_size_limit"

    # Preserve the five stages and all count summaries; remove duplicated and
    # optional detail in a fixed order until the response fits.
    payload["validation"]["models"] = []
    payload["simulation"]["decision"]["diagnostics"] = None
    payload["adoption"]["rollbackCandidates"] = []
    payload["dataPreparation"]["selectedFeatures"] = []
    payload["dataPreparation"]["unclassifiedFeatures"] = []

    models = payload["training"]["models"]
    while len(models) > 1 and _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        models.pop()
    sources = payload["dataPreparation"]["sources"]
    while sources and _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        sources.pop()
    usage = payload["adoption"]["strategyUsage"]
    while len(usage) > 1 and _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        usage.pop()

    if _serialized_size(payload) >= MAX_SERIALIZED_BYTES and models:
        models[0]["gateReasons"] = []
        models[0]["featureColumns"] = []
        models[0]["metrics"] = {}
    if _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        payload["adoption"]["strategyUsage"] = []
        payload["adoption"]["champions"] = []
    if _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        payload["training"]["models"] = []

    if _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        raise ValueError("dashboard_workspace_payload_exceeds_size_limit")
    return payload


def _public_strategy_profiles(
    root: Path,
    market: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for public_key, agent, fallback_label in PUBLIC_STRATEGIES:
        paths = agg._resolve_dashboard_paths(market, agent, root)
        try:
            profile = agg._dashboard_strategy_profile(
                paths,
                root=root,
                market=market,
                agent=agent,
            )
        except (OSError, TypeError, ValueError, agg.DashboardDataError):
            profile = {}
        factors = sorted(
            {
                _text(item.get("key"), limit=128)
                for item in _rows(profile.get("factors"))
                if item.get("key")
            }
        )
        result[public_key] = {
            "label": _text(
                profile.get("agent_label") or fallback_label,
                limit=256,
            ),
            "factors": factors,
        }
    return result


def _model_feature_evidence_from_health(
    health: dict[str, Any],
) -> tuple[dict[tuple[int, str], set[str]], dict[str, Any]]:
    manifests: dict[tuple[int, str], set[str]] = {}
    audit_by_model: dict[tuple[int, str], bool] = {}
    for row in _rows(health.get("models")):
        horizon = _integer(row.get("horizon"))
        version = _text(row.get("model_version"), limit=256).strip()
        if horizon <= 0 or not version:
            continue
        identity = (horizon, version)
        manifests.setdefault(identity, set()).update(
            _text(value, limit=128)
            for value in (row.get("feature_columns") or [])
            if value
        )
        metrics = _mapping(row.get("metrics"))
        audit_by_model[identity] = (
            audit_by_model.get(identity, False)
            or metrics.get("point_in_time_audit") is True
        )
    audited = sum(audit_by_model.values())
    model_count = len(manifests)
    return manifests, {
        "status": "available" if manifests else "unavailable",
        "modelCount": model_count,
        "pointInTimeAuditedModels": audited,
        "pointInTimeFailedModels": max(0, model_count - audited),
        "missingRateStatus": "not_recorded",
        "outlierStatus": "not_recorded",
    }


def _normalize_trade_dates(values: Any) -> list[str]:
    try:
        series = pd.Series(values, dtype="string")
    except (TypeError, ValueError):
        return []
    normalized = (
        series.dropna()
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
        .str[:8]
    )
    return sorted(
        {
            value
            for value in normalized.tolist()
            if isinstance(value, str)
            and len(value) == 8
            and value.isdigit()
        }
    )


def _structured_snapshot_coverage(root: Path, market: str) -> dict[str, Any]:
    feature_root = root / "data" / "research" / "features" / market
    paths = sorted(feature_root.glob("[0-9]" * 8 + ".parquet"))
    if not paths:
        return {
            "status": "not_recorded",
            "snapshotAsOf": None,
            "rangeStart": None,
            "rangeEnd": None,
            "latestTradeDate": None,
            "latestSnapshot": None,
            "snapshotCount": 0,
            "inspectedSnapshots": 0,
            "readableSnapshots": 0,
            "datedSnapshots": 0,
        }

    boundary_paths = [paths[0]]
    if paths[-1] != paths[0]:
        boundary_paths.append(paths[-1])
    content_dates: list[str] = []
    readable = 0
    dated = 0
    for path in boundary_paths:
        try:
            frame = pd.read_parquet(path, columns=["trade_date"])
            dates = _normalize_trade_dates(frame["trade_date"])
        except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
            continue
        readable += 1
        if dates:
            dated += 1
        content_dates.extend(dates)

    observed_dates = sorted(set(content_dates))
    latest = paths[-1]
    try:
        artifact = str(latest.relative_to(root))
    except ValueError:
        artifact = latest.name
    complete = dated == len(boundary_paths)
    return {
        "status": "available" if complete else "partial",
        "snapshotAsOf": latest.stem,
        "rangeStart": observed_dates[0] if observed_dates else None,
        "rangeEnd": observed_dates[-1] if observed_dates else None,
        "latestTradeDate": observed_dates[-1] if observed_dates else None,
        "latestSnapshot": _text(artifact, limit=MAX_TEXT_LENGTH),
        "snapshotCount": len(paths),
        "inspectedSnapshots": len(boundary_paths),
        "readableSnapshots": readable,
        "datedSnapshots": dated,
    }


def _manifest_label(identity: tuple[int, str]) -> str:
    horizon, version = identity
    return f"研究模型 {_text(version, limit=256)} ({horizon}日)"


def _manifest_evidence(prefix: str, identity: tuple[int, str]) -> str:
    horizon, version = identity
    return f"{prefix}:{horizon}:{_text(version, limit=256)}"


def _usage_cell(
    *,
    formal_items: set[str],
    research_items: set[str],
    formal_eligible: set[str],
    research_eligible: set[str],
    formal_evidence: list[str],
    research_evidence: list[str],
    research_unavailable_evidence: list[str] | None = None,
    observing: bool = False,
) -> dict[str, Any]:
    formal_used = sorted(formal_items & formal_eligible)
    research_used = sorted(research_items & research_eligible)
    formal_evidence_used = list(
        dict.fromkeys(
            _text(value, limit=MAX_TEXT_LENGTH)
            for value in formal_evidence
            if value
        )
    )[:MAX_TABLE_ROWS]
    research_evidence_used = list(
        dict.fromkeys(
            _text(value, limit=MAX_TEXT_LENGTH)
            for value in research_evidence
            if value
        )
    )[:MAX_TABLE_ROWS]
    missing_manifest_evidence = list(
        dict.fromkeys(
            _text(value, limit=MAX_TEXT_LENGTH)
            for value in (research_unavailable_evidence or [])
            if value
        )
    )[:MAX_TABLE_ROWS]
    if not formal_used:
        formal_evidence_used = []
    if not research_used:
        research_evidence_used = []
    count = len(formal_used) + len(research_used)
    formal_status = "used" if formal_used else "not_used"
    research_status = (
        "used"
        if research_used
        else "unavailable"
        if missing_manifest_evidence
        else "observing"
        if observing
        else "not_used"
    )
    legacy_features = sorted(set(formal_used) | set(research_used))
    legacy_evidence = list(
        dict.fromkeys([*formal_evidence_used, *research_evidence_used])
    )[:MAX_TABLE_ROWS]
    return {
        "status": (
            "used"
            if count
            else "unavailable"
            if missing_manifest_evidence
            else "observing"
            if observing
            else "not_used"
        ),
        "count": count,
        "countSemantics": "formal_plus_research_namespace_items",
        "features": legacy_features[:MAX_FEATURE_ROWS],
        "evidence": legacy_evidence if count or observing else [],
        "formalCount": len(formal_used),
        "formalFactors": formal_used[:MAX_FEATURE_ROWS],
        "formalStatus": formal_status,
        "researchCount": len(research_used),
        "researchFeatures": research_used[:MAX_FEATURE_ROWS],
        "researchStatus": research_status,
        "missingManifestEvidence": missing_manifest_evidence,
        "evidenceByNamespace": {
            "formal": formal_evidence_used,
            "research": research_evidence_used,
        },
    }


def _active_lineage_models(
    usage: Any,
    market: str,
) -> dict[str, set[tuple[int, str]]]:
    by_agent: dict[str, set[tuple[int, str]]] = {}
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for row in _rows(usage):
        if row.get("market") != market:
            continue
        agent = _text(row.get("agent"), limit=128)
        if not agent:
            continue
        previous = latest_by_agent.get(agent)
        if previous is None or _text(
            row.get("as_of"), limit=256
        ) >= _text(previous.get("as_of"), limit=256):
            latest_by_agent[agent] = row
    for agent, row in latest_by_agent.items():
        if row.get("status") != "active":
            continue
        identities: set[tuple[int, str]] = set()
        for raw_horizon, raw_version in _mapping(
            row.get("model_versions")
        ).items():
            identity = (
                _integer(raw_horizon),
                _text(raw_version, limit=256),
            )
            if identity[0] > 0 and identity[1]:
                identities.add(identity)
        by_agent[agent] = identities
    return by_agent


_RESOURCE_OMIT_KEYS = frozenset(
    {
        "rowsByDecision",
        "raw",
        "rawText",
        "raw_text",
        "rawProse",
        "raw_prose",
        "prose",
        "content",
        "body",
    }
)


def _bounded_resource(value: Any) -> tuple[Any, list[str]]:
    budget = {"nodes": MAX_RESOURCE_NODES, "text": 80_000}
    reasons: set[str] = set()

    def empty_like(item: Any) -> Any:
        if isinstance(item, dict):
            return {}
        if isinstance(item, (list, tuple, set)):
            return []
        return None

    def sanitize(item: Any, depth: int) -> Any:
        if budget["nodes"] <= 0:
            reasons.add("node_budget_exhausted")
            return empty_like(item)
        if depth >= 8:
            reasons.add("depth_limit")
            return empty_like(item)
        budget["nodes"] -= 1
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            sorted_keys = sorted(item, key=str)
            if len(sorted_keys) > MAX_RESOURCE_OBJECT_FIELDS:
                reasons.add("item_limit")
            for raw_key in sorted_keys:
                key = _text(raw_key, limit=128)
                if key in _RESOURCE_OMIT_KEYS:
                    continue
                if len(result) >= MAX_RESOURCE_OBJECT_FIELDS:
                    break
                if budget["nodes"] <= 0:
                    reasons.add("node_budget_exhausted")
                    break
                result[key] = sanitize(item[raw_key], depth + 1)
            return result
        if isinstance(item, (list, tuple, set)):
            values = (
                sorted(item, key=lambda child: str(child))
                if isinstance(item, set)
                else list(item)
            )
            if len(values) > MAX_TABLE_ROWS:
                reasons.add("item_limit")
            result: list[Any] = []
            for child in values[:MAX_TABLE_ROWS]:
                if budget["nodes"] <= 0:
                    reasons.add("node_budget_exhausted")
                    break
                result.append(sanitize(child, depth + 1))
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return _scalar(item)
        if budget["text"] <= 0:
            reasons.add("text_budget_exhausted")
            return None
        available = min(MAX_TEXT_LENGTH, budget["text"])
        raw_text = _text(_iso_timestamp(item), limit=MAX_TEXT_LENGTH + 1)
        if len(raw_text) > available:
            reasons.add(
                "text_budget_exhausted"
                if available < MAX_TEXT_LENGTH
                else "text_item_limit"
            )
        text = raw_text[:available]
        budget["text"] -= len(text)
        return text

    return sanitize(value, 0), sorted(reasons)


def _bounded_intelligence_lane(intelligence: dict[str, Any]) -> dict[str, Any]:
    required = (
        "pipeline",
        "extraction",
        "factorSupply",
        "modelImpact",
        "decisions",
    )
    sanitized, reasons = _bounded_resource(
        _sanitize_intelligence_errors(
            {
                key: _mapping(intelligence.get(key))
                for key in required
            }
        )
    )
    lane = sanitized if isinstance(sanitized, dict) else {}
    for key in required:
        if not isinstance(lane.get(key), dict):
            lane[key] = {}
            reasons.append("required_object_repaired")
    unique_reasons = sorted(set(reasons))
    lane["truncated"] = bool(unique_reasons)
    lane["truncationReasons"] = unique_reasons
    return lane


def _sanitize_intelligence_errors(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, child in value.items():
            normalized_key = str(key).replace("_", "").lower()
            sanitized[key] = (
                _public_intelligence_error(child)
                if normalized_key in {"error", "errorsummary"}
                else _sanitize_intelligence_errors(child)
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_intelligence_errors(item) for item in value]
    return value


def _enforce_data_intelligence_size(
    payload: dict[str, Any],
) -> dict[str, Any]:
    payload["truncated"] = False
    payload["truncationReason"] = None
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload
    payload["truncated"] = True
    payload["truncationReason"] = "serialized_size_limit"
    payload["intelligence"]["extraction"]["latestBatch"] = None
    payload["intelligence"]["pipeline"]["sources"] = []
    payload["intelligence"]["factorSupply"]["factors"] = []
    payload["structured"]["selectedFeatures"] = []
    payload["structured"]["formalFactorNamespace"]["activeFactors"] = []
    payload["structured"]["researchFeatureNamespace"]["selectedFeatures"] = []
    payload["intelligence"]["featureNamespace"]["selectedFeatures"] = []
    for row in payload["structured"]["sources"]:
        row["useLocations"] = []
    for row in payload["usageMatrix"]:
        for key in (
            "structuredData",
            "traditionalFactors",
            "intelligenceFactors",
        ):
            row[key]["features"] = []
            row[key]["evidence"] = []
            row[key]["formalFactors"] = []
            row[key]["researchFeatures"] = []
            row[key]["evidenceByNamespace"] = {
                "formal": [],
                "research": [],
            }
    if _serialized_size(payload) >= MAX_SERIALIZED_BYTES:
        raise ValueError("dashboard_data_intelligence_payload_exceeds_size_limit")
    return payload


def _tabular_research_summary(raw: Any) -> dict[str, Any] | None:
    report = _mapping(raw)
    config_hash = _text(report.get("config_hash"), limit=64)
    if not config_hash:
        return None
    metrics = _mapping(report.get("metrics"))
    gate = _mapping(report.get("development_gate"))
    development = _mapping(report.get("development"))
    checks = {
        _text(key, limit=128): value is True
        for key, value in _mapping(gate.get("checks")).items()
        if isinstance(value, bool) and _text(key, limit=128)
    }
    buckets = [
        {
            "bucket": _integer(row.get("bucket")),
            "meanExcessReturn": _finite_number(row.get("mean_excess_return")),
            "observations": _integer(row.get("observations")),
        }
        for row in _rows(report.get("score_buckets"))[:5]
    ]
    reasons = [
        _text(reason, limit=128)
        for reason in list(gate.get("reasons") or [])[:MAX_DIAGNOSTIC_ITEMS]
        if _text(reason, limit=128)
    ]
    calibration_rows = _rows(report.get("calibrations"))
    calibration_diagnostics = _mapping(report.get("calibration_diagnostics"))
    no_trade_reasons = [
        {
            "reason": _text(reason, limit=128),
            "count": _integer(count),
        }
        for reason, count in sorted(
            _mapping(calibration_diagnostics.get("no_trade_reasons")).items(),
            key=lambda item: _integer(item[1]),
            reverse=True,
        )[:MAX_DIAGNOSTIC_ITEMS]
        if _text(reason, limit=128)
    ]
    return {
        "status": _text(report.get("status"), limit=64) or "research",
        "protocolVersion": (
            _text(report.get("protocol_version"), limit=128) or "not_recorded"
        ),
        "configHash": config_hash,
        "accountScope": (
            _text(report.get("account_scope"), limit=64) or "not_recorded"
        ),
        "asOf": _text(report.get("as_of"), limit=32) or "not_recorded",
        "estimator": (
            _text(report.get("estimator"), limit=128) or "not_recorded"
        ),
        "target": _text(report.get("target"), limit=128) or "not_recorded",
        "selectedFeatureCount": _integer(report.get("selected_feature_count")),
        "developmentStart": (
            _text(development.get("start"), limit=32) or "not_recorded"
        ),
        "developmentEnd": (
            _text(development.get("end"), limit=32) or "not_recorded"
        ),
        "oosStart": _text(report.get("oos_start"), limit=32) or "not_recorded",
        "oosEnd": _text(report.get("oos_end"), limit=32) or "not_recorded",
        "formalOrderSource": report.get("formal_order_source") is True,
        "registryMutated": report.get("registry_mutated") is True,
        "metrics": {
            public_key: _finite_number(metrics.get(source_key))
            for source_key, public_key in TABULAR_RESEARCH_METRICS.items()
        },
        "gate": {
            "passed": gate.get("passed") is True,
            "reasons": reasons,
            "checks": checks,
            "positiveFolds": _integer(gate.get("positive_folds")),
            "bucketSpearman": _finite_number(gate.get("bucket_spearman")),
        },
        "buckets": buckets,
        "calibration": {
            "enabled": bool(calibration_rows or calibration_diagnostics),
            "foldCount": _integer(calibration_diagnostics.get("fold_count")),
            "economicPredictionCoverage": _finite_number(
                calibration_diagnostics.get("economic_prediction_coverage")
            ),
            "positiveLowerBoundCoverage": _finite_number(
                calibration_diagnostics.get("positive_lower_bound_coverage")
            ),
            "uncertaintyBpsP50": _finite_number(
                calibration_diagnostics.get("uncertainty_bps_p50")
            ),
            "uncertaintyBpsP90": _finite_number(
                calibration_diagnostics.get("uncertainty_bps_p90")
            ),
            "optimizerTrackingErrorP50": _finite_number(
                calibration_diagnostics.get("optimizer_tracking_error_p50")
            ),
            "optimizerTrackingErrorP90": _finite_number(
                calibration_diagnostics.get("optimizer_tracking_error_p90")
            ),
            "noTradeReasons": no_trade_reasons,
        },
    }


def _classical_loop_closure_summary(raw: Any) -> dict[str, Any] | None:
    report = _mapping(raw)
    best_config_hash = _text(report.get("best_config_hash"), limit=64)
    if not best_config_hash:
        return None
    blockers = []
    next_run_conditions = []
    next_run_codes = {
        "historical_information_coverage",
        "untouched_lockbox",
    }
    for row in _rows(report.get("blockers"))[:MAX_DIAGNOSTIC_ITEMS]:
        code = _text(row.get("code"), limit=128)
        if not code:
            continue
        projected = {
            "code": code,
            "measured": _finite_number(row.get("measured")),
            "required": _finite_number(row.get("required")),
            "evidence": _text(row.get("evidence"), limit=256),
        }
        if code in next_run_codes:
            next_run_conditions.append(projected)
        else:
            blockers.append(projected)
    return {
        "status": _text(report.get("status"), limit=64) or "research_blocked",
        "asOf": _text(report.get("as_of"), limit=32) or "not_recorded",
        "decision": _text(report.get("decision"), limit=128) or "not_recorded",
        "bestConfigHash": best_config_hash,
        "officialImmutableTrials": _integer(
            report.get("official_immutable_trials")
        ),
        "diagnosticExperiments": _integer(report.get("diagnostic_experiments")),
        "passedChecks": _integer(report.get("passed_checks")),
        "totalChecks": _integer(report.get("total_checks")),
        "formalStrategyWeight": _finite_number(
            report.get("formal_strategy_weight")
        ),
        "blockers": blockers,
        "nextRunConditions": next_run_conditions,
    }


def _tabular_forward_summary(raw: Any) -> dict[str, Any] | None:
    status = _mapping(raw)
    config_hash = _text(status.get("config_hash"), limit=64)
    model_id = _text(status.get("model_id"), limit=128)
    if not config_hash or not model_id:
        return None
    matured = _mapping(status.get("matured_evidence"))
    portfolio = _mapping(status.get("portfolio"))
    drift = _mapping(status.get("drift"))
    promotion = _mapping(status.get("promotion"))
    checks = [
        {
            "key": _text(key, limit=64),
            "passed": bool(value),
        }
        for key, value in list(_mapping(promotion.get("checks")).items())[
            :MAX_DIAGNOSTIC_ITEMS
        ]
        if _text(key, limit=64)
    ]
    buckets = [
        {
            "bucket": _integer(row.get("bucket")),
            "meanExcessReturn": _finite_number(row.get("mean_excess_return")),
            "observations": _integer(row.get("observations")),
        }
        for row in _rows(matured.get("buckets"))[:5]
    ]
    return {
        "status": _text(status.get("status"), limit=64) or "not_started",
        "lifecycleStatus": _text(
            status.get("lifecycle_status"), limit=64
        ) or "forward_observation",
        "modelId": model_id,
        "configHash": config_hash,
        "accountScope": _text(status.get("account_scope"), limit=64),
        "horizon": _integer(status.get("horizon")),
        "observationStart": _text(status.get("observation_start"), limit=32),
        "latestPredictionDate": _text(
            status.get("latest_prediction_date"), limit=32
        ) or None,
        "observationDays": _integer(status.get("observation_days")),
        "predictionRows": _integer(status.get("prediction_rows")),
        "latestCandidates": _integer(status.get("latest_candidates")),
        "latestSelected": _integer(status.get("latest_selected")),
        "maturedEvidence": {
            "status": _text(matured.get("status"), limit=64) or "waiting_for_horizon",
            "maturedRows": _integer(matured.get("matured_rows")),
            "maturedDays": _integer(matured.get("matured_days")),
            "latestLabelEnd": _text(matured.get("latest_label_end"), limit=32) or None,
            "rankIc": _finite_number(matured.get("rank_ic")),
            "icir": _finite_number(matured.get("icir")),
            "rawRankIc": _finite_number(matured.get("raw_rank_ic")),
            "rawIcir": _finite_number(matured.get("raw_icir")),
            "topBottomSpread": _finite_number(matured.get("top_bottom_spread")),
            "buckets": buckets,
        },
        "portfolio": {
            "status": _text(portfolio.get("status"), limit=64) or "waiting_for_next_open",
            "periods": _integer(portfolio.get("periods")),
            "rebalancePeriods": _integer(portfolio.get("rebalance_periods")),
            "trades": _integer(portfolio.get("trades")),
            "netReturn": _finite_number(portfolio.get("net_return")),
            "benchmarkReturn": _finite_number(portfolio.get("benchmark_return")),
            "netExcessReturn": _finite_number(portfolio.get("net_excess_return")),
            "maxDrawdown": _finite_number(portfolio.get("max_drawdown")),
            "activeMaxDrawdown": _finite_number(
                portfolio.get("active_max_drawdown")
            ),
            "informationRatio": _finite_number(portfolio.get("information_ratio")),
            "annualTurnover": _finite_number(portfolio.get("annual_turnover")),
            "capitalUtilization": _finite_number(
                portfolio.get("capital_utilization")
            ),
            "executionCostBps": _finite_number(
                portfolio.get("execution_cost_bps")
            ),
        },
        "drift": {
            "status": _text(drift.get("status"), limit=64) or "unknown",
            "medianFeatureCoverage": _finite_number(
                drift.get("median_feature_coverage")
            ),
            "medianOutOfRangeRatio": _finite_number(
                drift.get("median_out_of_range_ratio")
            ),
        },
        "promotion": {
            "status": _text(promotion.get("status"), limit=64) or "evidence_pending",
            "passedChecks": sum(1 for row in checks if row["passed"]),
            "totalChecks": len(checks),
            "checks": checks,
            "automaticPromotion": bool(promotion.get("automatic_promotion")),
        },
        "formalStrategyWeight": _finite_number(
            status.get("formal_strategy_weight")
        ) or 0.0,
        "formalOrderSource": bool(status.get("formal_order_source")),
        "updatedAt": _text(status.get("updated_at"), limit=64) or None,
    }


def _read_tabular_forward_observation(
    root: Path,
    market: str,
) -> dict[str, Any] | None:
    if market != "a_share":
        return None
    scope_root = (
        root / "data" / "research" / "tabular_forward" / market / "zz500"
    )
    candidates: list[Path] = []
    try:
        current = json.loads(
            (scope_root / "current.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        current = {}
    config_hash = _text(_mapping(current).get("config_hash"), limit=64)
    if config_hash and re.fullmatch(r"[0-9a-f]{8,64}", config_hash):
        candidates.append(scope_root / config_hash / "status.json")
    def modified_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    candidates.extend(
        sorted(
            scope_root.glob("*/status.json"),
            key=modified_ns,
            reverse=True,
        )[:MAX_DIAGNOSTIC_ITEMS]
    )
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            summary = _tabular_forward_summary(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            summary = None
        if summary is not None:
            return summary
    return None


def _read_tabular_research_evidence(root: Path, market: str) -> dict[str, Any]:
    unavailable = {
        "status": "unavailable",
        "formalStrategyWeight": 0.0,
        "formalOrderSource": False,
        "latest": None,
        "best": None,
        "experiments": [],
        "closure": None,
        "forwardObservation": None,
    }
    if market != "a_share":
        return unavailable
    forward_observation = _read_tabular_forward_observation(root, market)
    report_dir = root / "reports" / "research"
    if not report_dir.exists() and forward_observation is None:
        return unavailable

    candidates: dict[str, list[tuple[str, int, Path]]] = {
        "latest": [],
        "best": [],
        "experiment": [],
    }
    patterns = (
        ("latest", TABULAR_RESEARCH_LATEST_PATTERN),
        ("best", TABULAR_RESEARCH_BEST_PATTERN),
        ("experiment", TABULAR_RESEARCH_EXPERIMENT_PATTERN),
    )
    for path in report_dir.glob("regime_tabular_alpha_*.json"):
        for kind, pattern in patterns:
            match = pattern.fullmatch(path.name)
            if match:
                try:
                    modified_ns = path.stat().st_mtime_ns
                except OSError:
                    modified_ns = 0
                candidates[kind].append((match.group("as_of"), modified_ns, path))
                break

    def read_summary(path: Path) -> dict[str, Any] | None:
        try:
            return _tabular_research_summary(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    latest = next(
        (
            summary
            for _, _, path in sorted(candidates["latest"], reverse=True)
            if (summary := read_summary(path)) is not None
        ),
        None,
    )
    best = next(
        (
            summary
            for _, _, path in sorted(candidates["best"], reverse=True)
            if (summary := read_summary(path)) is not None
        ),
        None,
    )
    experiments = [
        summary
        for _, _, path in sorted(candidates["experiment"], reverse=True)
        if (summary := read_summary(path)) is not None
    ][:MAX_DIAGNOSTIC_ITEMS]

    closure_candidates: list[tuple[str, int, Path]] = []
    for path in report_dir.glob("classical_autonomous_loop_*.json"):
        match = CLASSICAL_LOOP_CLOSURE_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        try:
            modified_ns = path.stat().st_mtime_ns
        except OSError:
            modified_ns = 0
        closure_candidates.append((match.group("as_of"), modified_ns, path))

    def read_closure(path: Path) -> dict[str, Any] | None:
        try:
            return _classical_loop_closure_summary(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    closure = next(
        (
            summary
            for _, _, path in sorted(closure_candidates, reverse=True)
            if (summary := read_closure(path)) is not None
        ),
        None,
    )
    if (
        latest is None
        and best is None
        and not experiments
        and forward_observation is None
    ):
        return unavailable
    selected = best or latest or (experiments[0] if experiments else None)
    return {
        "status": "available",
        "formalStrategyWeight": 0.0,
        "formalOrderSource": bool(
            selected.get("formalOrderSource") if selected else False
        ),
        "latest": latest,
        "best": best,
        "experiments": experiments,
        "closure": closure,
        "forwardObservation": forward_observation,
    }


def _read_strategy_campaign(root: Path, market: str) -> dict[str, Any]:
    reports = root / "reports" / "research"
    candidates = sorted(
        reports.glob("*-final.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not candidates:
        candidates = sorted(
            reports.glob("*-transparent.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    unavailable = {
        "status": "unavailable",
        "campaignId": None,
        "manifestHash": None,
        "completedAt": None,
        "formalStrategyActivated": False,
        "scopes": [],
    }
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        scopes: list[dict[str, Any]] = []
        for raw_scope in _rows(payload.get("scopes")):
            if str(raw_scope.get("market") or "") != market:
                continue
            selected_id = str(
                raw_scope.get("selected_incremental_spec_id")
                or raw_scope.get("selected_spec_id")
                or ""
            )
            diagnostic_id = str(
                raw_scope.get("best_diagnostic_spec_id") or ""
            )
            displayed_id = selected_id or diagnostic_id
            trials = [
                *_rows(raw_scope.get("incremental_trials")),
                *_rows(raw_scope.get("trials")),
            ]
            selected = _mapping(raw_scope.get("display_trial"))
            if str(selected.get("spec_id") or "") != displayed_id:
                selected = next(
                    (
                        item for item in trials
                        if str(item.get("spec_id") or "") == displayed_id
                    ),
                    {},
                )
            metrics = _mapping(selected.get("metrics"))
            gate_two = _mapping(selected.get("gate_two"))
            governance = _mapping(gate_two.get("governance"))
            gate_three = _mapping(selected.get("gate_three"))
            reasons = [
                _text(item, limit=256)
                for item in raw_scope.get("reasons") or []
                if _text(item, limit=256)
            ][:MAX_DIAGNOSTIC_ITEMS]
            scopes.append({
                "accountScope": _text(raw_scope.get("account_scope"), limit=64),
                "status": _text(raw_scope.get("status"), limit=64) or "unavailable",
                "selectedRuleSpecId": (
                    _text(raw_scope.get("selected_spec_id"), limit=128) or None
                ),
                "selectedIncrementalSpecId": (
                    _text(
                        raw_scope.get("selected_incremental_spec_id"),
                        limit=128,
                    ) or None
                ),
                "bestDiagnosticSpecId": (
                    _text(raw_scope.get("best_diagnostic_spec_id"), limit=128)
                    or None
                ),
                "diagnosticOnly": bool(
                    raw_scope.get("diagnostic_only") and not selected_id
                ),
                "reasons": reasons,
                "transparentTrialCount": int(
                    raw_scope.get("transparent_trial_count")
                    if raw_scope.get("transparent_trial_count") is not None
                    else len(_rows(raw_scope.get("trials")))
                ),
                "incrementalTrialCount": int(
                    raw_scope.get("incremental_trial_count")
                    if raw_scope.get("incremental_trial_count") is not None
                    else len(_rows(raw_scope.get("incremental_trials")))
                ),
                "netReturn": _finite_number(metrics.get("net_return")),
                "benchmarkReturn": _finite_number(metrics.get("benchmark_return")),
                "netExcessReturn": _finite_number(metrics.get("net_excess_return")),
                "sharpe": _finite_number(metrics.get("portfolio_sharpe")),
                "maxDrawdown": _finite_number(metrics.get("max_drawdown")),
                "targetFillRatio": _finite_number(metrics.get("target_fill_ratio")),
                "costStressNetExcessReturn": _finite_number(
                    _mapping(selected.get("cost_stress")).get("net_excess_return")
                ),
                "deflatedSharpeProbability": _finite_number(
                    governance.get("deflated_sharpe_probability")
                ),
                "probabilityOfBacktestOverfit": _finite_number(
                    governance.get("probability_of_backtest_overfit")
                ),
                "pairedBootstrapProbability": _finite_number(
                    gate_three.get("paired_bootstrap_probability")
                ),
                "attribution": _bounded_diagnostics(
                    _mapping(selected.get("attribution"))
                ),
                "folds": _bounded_diagnostics(selected.get("folds") or [])[:3],
                "regimes": _bounded_diagnostics(
                    _mapping(selected.get("regimes"))
                ),
            })
        if not scopes:
            continue
        return {
            "status": _text(payload.get("status"), limit=64) or "unavailable",
            "campaignId": _text(payload.get("campaign_id"), limit=256) or None,
            "manifestHash": _text(payload.get("manifest_hash"), limit=256) or None,
            "completedAt": _iso_timestamp(
                payload.get("completed_at") or payload.get("generated_at")
            ),
            "formalStrategyActivated": bool(
                payload.get("formal_strategy_activated", False)
            ),
            "scopes": scopes[:MAX_TABLE_ROWS],
        }
    return unavailable


def build_dashboard_model_research_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
) -> dict[str, Any]:
    """Build a bounded, evidence-backed model lifecycle snapshot."""

    _check_market(market)
    root = _root(repo_root)
    errors: list[dict[str, str]] = []
    health = _mapping(
        _safe_workspace_read(
            errors,
            "model_health",
            {"status": "unavailable", "models": []},
            agg._read_model_health,
            root,
            market,
        )
    )
    model_health_available = not _workspace_resource_unavailable(
        health,
        errors=errors,
        resource="model_health",
    )
    all_models = _model_rows(root, market, health)
    tournament_health = _mapping(
        _safe_workspace_read(
            errors,
            "model_tournament_summary",
            {"status": "unavailable", "models": []},
            _latest_tournament_health,
            root,
            market,
        )
    )
    tournament_model_rows = _model_rows(root, market, tournament_health)
    baseline_first_health = _mapping(
        _safe_workspace_read(
            errors,
            "baseline_first_evaluation",
            {"status": "unavailable", "models": []},
            _latest_baseline_first_health,
            root,
            market,
        )
    )
    baseline_first_rows = _model_rows(root, market, baseline_first_health)
    latest_model_rows = _model_rows(
        root,
        market,
        {"models": health.get("latest_models") or health.get("models") or []},
    )
    evidence_rows = _deduplicate_model_rows(
        all_models
        + latest_model_rows
        + tournament_model_rows
        + baseline_first_rows
    )
    displayed_model_rows, archived_model_rows = _mainline_model_projection(
        market,
        evidence_rows,
    )
    latest_models = [
        {
            key: row.get(key)
            for key in (
                "modelVersion",
                "specId",
                "accountScope",
                "horizon",
                "trainedAt",
                "registeredAt",
                "lifecycleStatus",
                "gatePassed",
                "gateReasons",
            )
        }
        for row in displayed_model_rows
    ]
    models = [
        _public_model_row(row)
        for row in displayed_model_rows[:MAX_TABLE_ROWS]
    ]
    archive_status_counts: dict[str, int] = {}
    for row in archived_model_rows:
        status = str(row.get("lifecycleStatus") or "research")
        archive_status_counts[status] = archive_status_counts.get(status, 0) + 1
    archived_models = [
        _public_model_row(row)
        for row in archived_model_rows[:5]
    ]
    selected_features = sorted(
        {
            feature
            for model in displayed_model_rows
            for feature in model.get("_allFeatureColumns", [])
        }
    )
    registry_names = {item.name for item in DEFAULT_REGISTRY}
    intelligence_names = {item.name for item in INTELLIGENCE_FEATURES}
    intelligence_features = sorted(set(selected_features) & intelligence_names)
    structured_features = sorted(
        set(selected_features) & (registry_names - intelligence_names)
    )
    unclassified_features = sorted(set(selected_features) - registry_names)
    raw_source_health = _safe_workspace_read(
        errors,
        "source_health",
        [],
        agg._read_research_source_health,
        root,
        market,
    )
    source_health = _source_rows(raw_source_health)
    iteration = _mapping(
        _safe_workspace_read(
            errors,
            "model_iteration",
            {
                "status": "unavailable",
                "candidate": None,
                "champion": None,
            },
            agg._read_model_iteration_status,
            root,
            market,
        )
    )
    champion_models = {
        (row["horizon"], row["modelVersion"])
        for row in all_models
        if row["isChampion"]
    }
    formal_usage = _safe_workspace_read(
        errors,
        "strategy_model_usage",
        [],
        _latest_strategy_model_usage,
        root,
    )
    formal_usage_available = not _workspace_resource_unavailable(
        formal_usage,
        errors=errors,
        resource="strategy_model_usage",
    )
    iteration_available = not _workspace_resource_unavailable(
        iteration,
        errors=errors,
        resource="model_iteration",
    )
    usage = _usage_rows(
        formal_usage,
        market=market,
        champion_models=champion_models,
    )
    champions = [
        {
            "modelVersion": row["modelVersion"],
            "accountScope": row["accountScope"],
            "horizon": row["horizon"],
            "activatedAt": row["activatedAt"],
            "artifactRef": row["artifactRef"],
        }
        for row in all_models
        if row["isChampion"]
    ][:MAX_TABLE_ROWS]
    rollback_candidates = [
        {
            "modelVersion": _text(row.get("model_version"), limit=256),
            "displayVersion": _text(
                row.get("display_version") or row.get("model_version"),
                limit=256,
            ),
            "outcome": _text(row.get("outcome"), limit=128),
            "endedAt": _iso_timestamp(row.get("ended_at")),
        }
        for row in reversed(_rows(iteration.get("version_history")))
        if row.get("model_version")
    ][:MAX_ROLLBACK_ROWS]
    candidate = _candidate(iteration.get("candidate"))
    candidate_model = next(
        (
            row
            for row in all_models
            if row.get("modelVersion") == candidate.get("model_version")
            and (
                not candidate.get("account_scope")
                or row.get("accountScope") == candidate.get("account_scope")
            )
            and (
                not candidate.get("horizon")
                or _integer(row.get("horizon"))
                == _integer(candidate.get("horizon"))
            )
        ),
        None,
    )
    attribution = _mapping(
        _safe_workspace_read(
            errors,
            "model_attribution",
            {
                "status": "unavailable",
                "formalModelApplied": False,
                "completeCount": 0,
                "totalCount": 0,
                "rows": [],
            },
            _model_attribution_evidence,
            root,
            market,
        )
    )
    tabular_research = _mapping(
        _safe_workspace_read(
            errors,
            "tabular_research",
            {
                "status": "unavailable",
                "formalStrategyWeight": 0.0,
                "formalOrderSource": False,
                "latest": None,
                "best": None,
                "experiments": [],
            },
            _read_tabular_research_evidence,
            root,
            market,
        )
    )
    historical_comparison = _mapping(
        _safe_workspace_read(
            errors,
            "unified_model_arena",
            {
                "status": "unavailable",
                "evidenceType": "historical_diagnostic",
                "asOf": None,
                "horizon": 0,
                "scopes": [],
            },
            _latest_unified_arena,
            root,
            market,
        )
    )
    strategy_campaign = _mapping(
        _safe_workspace_read(
            errors,
            "strategy_campaign",
            {
                "status": "unavailable",
                "campaignId": None,
                "manifestHash": None,
                "completedAt": None,
                "formalStrategyActivated": False,
                "scopes": [],
            },
            _read_strategy_campaign,
            root,
            market,
        )
    )
    tabular_run = _mapping(
        tabular_research.get("best") or tabular_research.get("latest")
    )
    tabular_gate = _mapping(tabular_run.get("gate"))
    tabular_gate_reasons = list(tabular_gate.get("reasons") or [])
    use_tabular_stage = bool(tabular_run) and not displayed_model_rows
    tabular_estimator = _text(tabular_run.get("estimator"), limit=128)
    tabular_estimator_label = {
        "lightgbm_regression": "LightGBM 回归排序",
        "lightgbm_lambdarank": "LightGBM LambdaRank",
        "lightgbm_top_tail_classifier": "LightGBM 顶端分类",
    }.get(tabular_estimator, "估计器未记录")
    required_cycles = _integer(candidate.get("shadow_cycles")) + _integer(
        candidate.get("shadow_cycles_remaining")
    )
    passed = sum(1 for row in displayed_model_rows if row["gatePassed"])
    candidate_count = max(
        (row["candidateFeatureCount"] for row in displayed_model_rows),
        default=0,
    )
    audited = [
        row["pointInTimeAudit"]
        for row in displayed_model_rows
        if row["pointInTimeAudit"] is not None
    ]
    point_in_time_status = (
        "passed"
        if audited and all(value is True for value in audited)
        else "failed"
        if audited
        else "unavailable"
    )
    account_labels = {
        "hs300": "沪深 300",
        "zz500": "中证 500",
        "hk": "香港跨境 ETF",
        "us": "美国跨境 ETF",
        "hk_exposure": "香港跨境 ETF",
        "us_exposure": "美国跨境 ETF",
        "": "旧版市场级",
    }
    account_summaries: list[dict[str, Any]] = []
    for account_scope in sorted({row["accountScope"] for row in displayed_model_rows}):
        account_models = [
            row for row in displayed_model_rows
            if row["accountScope"] == account_scope
        ]
        lifecycle = [str(row.get("lifecycleStatus") or "research") for row in account_models]
        latest_status = next(
            (
                status for status in ("active", "shadow", "research", "rejected")
                if status in lifecycle
            ),
            lifecycle[0] if lifecycle else "unavailable",
        )
        best = max(
            account_models,
            key=lambda row: (
                _finite_number(row["metrics"].get("rank_ic"))
                if _finite_number(row["metrics"].get("rank_ic")) is not None
                else float("-inf"),
                _finite_number(row["metrics"].get("net_excess_return"))
                if _finite_number(row["metrics"].get("net_excess_return")) is not None
                else float("-inf"),
                str(row.get("modelVersion") or ""),
            ),
        )
        account_summaries.append({
            "accountScope": account_scope,
            "accountLabel": account_labels.get(account_scope, account_scope),
            "candidateCount": len(account_models),
            "shadowCount": sum(status == "shadow" for status in lifecycle),
            "rejectedCount": sum(status == "rejected" for status in lifecycle),
            "latestStatus": latest_status,
            "bestModelVersion": best["modelVersion"],
            "bestRankIc": _finite_number(best["metrics"].get("rank_ic")),
            "bestNetExcessReturn": _finite_number(
                best["metrics"].get("net_excess_return")
            ),
            "bestTradeCount": _integer(best["metrics"].get("trade_count")),
            "bestEdgeCalibrationAvailable": (
                best["metrics"].get("edge_calibration_available") is True
            ),
        })
    stages = [
        {
            "key": "data",
            "label": "数据准备",
            "status": "success" if selected_features else "unavailable",
            "primary": f"{len(selected_features)} 个已选特征",
            "secondary": f"{len(_rows(raw_source_health))} 个来源状态",
        },
        {
            "key": "training",
            "label": "模型训练",
            "status": (
                "unavailable"
                if (
                    not model_health_available
                    and not displayed_model_rows
                    and not tabular_run
                )
                else "success"
                if displayed_model_rows or tabular_run
                else "empty"
            ),
            "primary": (
                "经典模型 1 个候选"
                if use_tabular_stage
                else f"{len(displayed_model_rows)} 个最新研究版本"
            ),
            "secondary": (
                f"{_integer(tabular_run.get('selectedFeatureCount'))} 个特征 · "
                f"{tabular_estimator_label}"
                if use_tabular_stage
                else (
                    f"{sum(row['sampleSupport'] for row in displayed_model_rows)} "
                    "条样本支持"
                )
            ),
        },
        {
            "key": "validation",
            "label": "测试验收",
            "status": (
                "unavailable"
                if (
                    not model_health_available
                    and not displayed_model_rows
                    and not tabular_run
                )
                else "success"
                if (
                    (use_tabular_stage and tabular_gate.get("passed") is True)
                    or (not use_tabular_stage and passed)
                )
                else "research"
            ),
            "primary": (
                "经典模型已通过"
                if use_tabular_stage and tabular_gate.get("passed") is True
                else f"经典模型 {len(tabular_gate_reasons)} 项未通过"
                if use_tabular_stage
                else f"{passed} / {len(displayed_model_rows)} 通过"
            ),
            "secondary": (
                f"注册模型 {passed} / {len(displayed_model_rows)} 通过"
                if use_tabular_stage
                else (
                    f"{sum(len(row['gateReasons']) for row in displayed_model_rows)} "
                    "个阻塞项"
                )
            ),
        },
        {
            "key": "simulation",
            "label": "模拟运行",
            "status": (
                "unavailable"
                if not iteration_available
                else "running"
                if candidate
                else "waiting_upstream"
            ),
            "primary": str(candidate.get("display_version") or "等待候选"),
            "secondary": (
                f"{_integer(candidate.get('shadow_cycles'))} / "
                f"{required_cycles or 12} 个观察周期"
            ),
        },
        {
            "key": "adoption",
            "label": "正式采用",
            "status": (
                "unavailable"
                if not model_health_available or not formal_usage_available
                else "success"
                if champions and usage
                else "waiting_upstream"
            ),
            "primary": f"{len(champions)} 个 Champion",
            "secondary": f"{len(usage)} 个正式策略账户已采用",
        },
    ]
    payload = {
        "generated_at": _generated_at(),
        "errors": errors,
        "market": market,
        "market_label": agg.MARKET_LABELS.get(market, market),
        "stages": stages,
        "dataPreparation": {
            "sources": source_health,
            "candidateFeatureCount": candidate_count,
            "selectedFeatureCount": len(selected_features),
            "structuredFeatureCount": len(structured_features),
            "intelligenceFeatureCount": len(intelligence_features),
            "unclassifiedFeatureCount": len(unclassified_features),
            "unclassifiedFeatures": unclassified_features[:MAX_FEATURE_ROWS],
            "selectedFeatures": selected_features[:MAX_FEATURE_ROWS],
            "pointInTimeAudit": point_in_time_status,
            "gaps": [
                row["source"]
                for row in source_health
                if row["failed"]
                or row["status"] in {"source_unavailable", "failed"}
            ],
        },
        "training": {
            "models": models,
            "latestModels": latest_models[:MAX_TABLE_ROWS],
            "accounts": account_summaries,
            "archive": {
                "total": len(archived_model_rows),
                "byStatus": archive_status_counts,
                "recent": archived_models,
            },
        },
        "validation": {
            "passed": passed,
            "total": len(displayed_model_rows),
            "models": models,
            "accounts": account_summaries,
        },
        "tabularResearch": tabular_research,
        "historicalComparison": historical_comparison,
        "strategyCampaign": strategy_campaign,
        "simulation": {
            "status": _text(iteration.get("status"), limit=128) or "unavailable",
            "candidate": candidate or None,
            "account": _simulation_account(iteration),
            "accounts": _simulation_accounts(iteration),
            "evaluation": _model_evaluation(candidate_model),
            "predictionAsOf": _scalar(
                iteration.get("prediction_as_of"),
                text_limit=256,
            ),
            "predictionStatus": (
                "available" if iteration.get("prediction_as_of") else "missing"
            ),
            "cyclesCompleted": _integer(candidate.get("shadow_cycles")),
            "cyclesRequired": required_cycles or 12,
            "decision": {
                "candidateRows": _integer(iteration.get("candidate_rows")),
                "modelEligibleRows": _integer(
                    iteration.get("model_eligible_rows", iteration.get("eligible_rows"))
                ),
                "eligibleRows": _integer(iteration.get("eligible_rows")),
                "scopeRejectedRows": _integer(iteration.get("scope_rejected_rows")),
                "selectedCount": _integer(iteration.get("selected_count")),
                "tradesExecuted": _integer(iteration.get("trades_executed")),
                "pendingOrders": _integer(iteration.get("pending_orders")),
                "cashOnly": bool(iteration.get("cash_only")),
                "cashReason": (
                    _text(iteration.get("cash_reason"), limit=256) or None
                ),
                "diagnostics": (
                    _bounded_diagnostics(
                        _mapping(iteration.get("decision_diagnostics"))
                    )
                    or None
                ),
            },
        },
        "adoption": {
            "champions": champions,
            "rollbackCandidates": rollback_candidates,
            "strategyUsage": usage,
        },
        "attribution": attribution,
    }
    safe_payload = agg._json_safe(payload)
    return _enforce_serialized_size(safe_payload)


def build_dashboard_data_intelligence_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
) -> dict[str, Any]:
    """Build bounded structured-data and intelligence supply evidence."""

    _check_market(market)
    root = _root(repo_root)
    errors: list[dict[str, str]] = []
    profiles = _public_strategy_profiles(root, market)
    model_health = _mapping(
        _safe_workspace_read(
            errors,
            "model_health",
            {"status": "unavailable", "models": []},
            agg._read_model_health,
            root,
            market,
        )
    )
    model_health_available = not _workspace_resource_unavailable(
        model_health,
        errors=errors,
        resource="model_health",
    )
    manifests, quality = _model_feature_evidence_from_health(model_health)
    iteration = _mapping(
        _safe_workspace_read(
            errors,
            "model_iteration",
            {
                "status": "unavailable",
                "candidate": None,
                "champion": None,
            },
            agg._read_model_iteration_status,
            root,
            market,
        )
    )
    iteration_available = not _workspace_resource_unavailable(
        iteration,
        errors=errors,
        resource="model_iteration",
    )
    formal_usage = _safe_workspace_read(
        errors,
        "strategy_model_usage",
        [],
        _latest_strategy_model_usage,
        root,
    )
    formal_usage_available = not _workspace_resource_unavailable(
        formal_usage,
        errors=errors,
        resource="strategy_model_usage",
    )
    model_health_unavailable_evidence = (
        [] if model_health_available else ["model_health:unavailable"]
    )
    strategy_usage_unavailable_evidence = (
        []
        if formal_usage_available
        else ["strategy_model_usage:unavailable"]
    )
    iteration_unavailable_evidence = (
        [] if iteration_available else ["model_iteration:unavailable"]
    )
    intelligence = _mapping(
        _safe_workspace_read(
            errors,
            "intelligence",
            _empty_intelligence_workspace(),
            build_dashboard_intelligence_data,
            repo_root=root,
            market=market,
            agent="codex",
            limit=1,
        )
    )
    coverage = _structured_snapshot_coverage(root, market)
    all_model_features = {
        feature for features in manifests.values() for feature in features
    }
    research_intelligence_names = {
        item.name
        for item in INTELLIGENCE_FEATURES
        if market in item.markets
    }
    formal_intelligence_names = (
        set(AVAILABLE_FACTORS_BY_MARKET.get(market, set()))
        & set(SENTIMENT_FACTORS)
    )
    research_traditional_names = {
        item.name
        for item in DEFAULT_REGISTRY
        if market in item.markets and item.family != "market_intelligence"
    }
    formal_traditional_names = (
        set(AVAILABLE_FACTORS_BY_MARKET.get(market, set()))
        - set(SENTIMENT_FACTORS)
    )
    active_formal_factors = {
        factor
        for profile in profiles.values()
        for factor in profile.get("factors", [])
        if factor in formal_traditional_names
    }
    selected_research_traditional = (
        all_model_features & research_traditional_names
    )

    source_groups: dict[str, dict[str, Any]] = {}
    for definition in DEFAULT_REGISTRY:
        if (
            market not in definition.markets
            or definition.family == "market_intelligence"
        ):
            continue
        row = source_groups.setdefault(
            definition.source,
            {
                "source": definition.source,
                "researchFeatureCount": 0,
                "selectedModelFeatureCount": 0,
                "strategyFactorCount": 0,
                "activeStrategyFactorCount": 0,
                "status": "declared",
                "useLocations": [],
            },
        )
        row["researchFeatureCount"] += 1
        if definition.name in all_model_features:
            row["selectedModelFeatureCount"] += 1
            row["status"] = "used"
            row["useLocations"].extend(
                _manifest_label(identity)
                for identity, features in manifests.items()
                if definition.name in features
            )
    for source, factor_names in FORMAL_FACTOR_SOURCES.get(market, {}).items():
        row = source_groups.setdefault(
            source,
            {
                "source": source,
                "researchFeatureCount": 0,
                "selectedModelFeatureCount": 0,
                "strategyFactorCount": 0,
                "activeStrategyFactorCount": 0,
                "status": "declared",
                "useLocations": [],
            },
        )
        row["strategyFactorCount"] += len(factor_names)
        active_names = {
            factor
            for profile in profiles.values()
            for factor in profile.get("factors", [])
            if factor in factor_names
        }
        row["activeStrategyFactorCount"] += len(active_names)
        if active_names:
            row["status"] = "used"
        row["useLocations"].extend(
            _text(profile.get("label"), limit=256)
            for profile in profiles.values()
            if set(profile.get("factors", [])) & factor_names
        )
    for row in source_groups.values():
        row["useLocations"] = sorted(set(row["useLocations"]))[:MAX_TABLE_ROWS]

    family_groups: dict[str, dict[str, Any]] = {}
    for definition in DEFAULT_REGISTRY:
        if (
            market not in definition.markets
            or definition.family == "market_intelligence"
        ):
            continue
        row = family_groups.setdefault(
            definition.family,
            {
                "family": definition.family,
                "definedFeatureCount": 0,
                "selectedFeatureCount": 0,
            },
        )
        row["definedFeatureCount"] += 1
        row["selectedFeatureCount"] += int(
            definition.name in all_model_features
        )

    factor_supply = _mapping(intelligence.get("factorSupply"))
    candidate = (
        _mapping(iteration.get("candidate"))
        if iteration_available
        else {}
    )
    candidate_identity = (
        _integer(candidate.get("horizon")),
        _text(candidate.get("model_version"), limit=256),
    )
    candidate_registered = candidate_identity in manifests
    candidate_features = (
        set(manifests[candidate_identity]) if candidate_registered else set()
    )
    candidate_evidence = (
        [_manifest_evidence("candidate_registry", candidate_identity)]
        if candidate_registered
        else []
    )
    active_lineage = _active_lineage_models(formal_usage, market)

    usage_matrix: list[dict[str, Any]] = []
    for public_key, agent, fallback_label in PUBLIC_STRATEGIES:
        profile = profiles.get(public_key) or {
            "label": fallback_label,
            "factors": [],
        }
        overlay_factors = set(profile.get("factors", []))
        applied_identities = active_lineage.get(agent, set())
        resolvable_identities = applied_identities & set(manifests)
        missing_identities = applied_identities - set(manifests)
        applied_features = {
            feature
            for identity in resolvable_identities
            for feature in manifests.get(identity, set())
        }
        lineage_evidence = [
            _manifest_evidence("decision_lineage", identity)
            for identity in sorted(resolvable_identities)
        ]
        missing_manifest_evidence = [
            _manifest_evidence("missing_manifest", identity)
            for identity in sorted(missing_identities)
        ]
        lineage_unavailable_evidence = [
            *missing_manifest_evidence,
            *model_health_unavailable_evidence,
            *strategy_usage_unavailable_evidence,
        ]
        usage_matrix.append(
            {
                "consumerKey": public_key,
                "consumerLabel": _text(
                    profile.get("label") or fallback_label,
                    limit=256,
                ),
                "structuredData": _usage_cell(
                    formal_items=overlay_factors,
                    research_items=applied_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=["strategy_overlay"],
                    research_evidence=lineage_evidence,
                    research_unavailable_evidence=lineage_unavailable_evidence,
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=overlay_factors,
                    research_items=applied_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=["strategy_overlay"],
                    research_evidence=lineage_evidence,
                    research_unavailable_evidence=lineage_unavailable_evidence,
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=overlay_factors,
                    research_items=applied_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=["strategy_overlay"],
                    research_evidence=lineage_evidence,
                    research_unavailable_evidence=lineage_unavailable_evidence,
                ),
                "modelAdoption": {
                    "status": (
                        "unavailable"
                        if not formal_usage_available
                        else "active"
                        if applied_identities
                        else "rule_only"
                    ),
                    "modelCount": len(applied_identities),
                    "resolvableManifestCount": len(resolvable_identities),
                    "missingManifestCount": len(missing_identities),
                    "models": [
                        {
                            "horizon": identity[0],
                            "modelVersion": identity[1],
                            "manifestStatus": (
                                "available"
                                if identity in manifests
                                else "unavailable"
                            ),
                            "evidence": _manifest_evidence(
                                "decision_lineage",
                                identity,
                            ),
                            "missingManifestEvidence": (
                                None
                                if identity in manifests
                                else _manifest_evidence(
                                    "missing_manifest",
                                    identity,
                                )
                            ),
                        }
                        for identity in sorted(applied_identities)
                    ][:MAX_TABLE_ROWS],
                },
                "impact": (
                    "正式采用状态不可用"
                    if not formal_usage_available
                    else (
                        f"正式决策采用 {len(applied_identities)} 个模型版本"
                        if applied_identities
                        else "本期规则驱动"
                    )
                ),
                "lineageStatus": (
                    None if formal_usage_available else "unavailable"
                ),
            }
        )

    manifest_evidence = [
        _manifest_evidence("model_feature_manifest", identity)
        for identity in sorted(manifests)
    ]
    intelligence_manifest_evidence = [
        _manifest_evidence("model_feature_manifest", identity)
        for identity, features in sorted(manifests.items())
        if features & research_intelligence_names
    ]
    usage_matrix.extend(
        [
            {
                "consumerKey": "research_model",
                "consumerLabel": "研究模型",
                "structuredData": _usage_cell(
                    formal_items=set(),
                    research_items=all_model_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=manifest_evidence,
                    research_unavailable_evidence=(
                        model_health_unavailable_evidence
                    ),
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=set(),
                    research_items=all_model_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=manifest_evidence,
                    research_unavailable_evidence=(
                        model_health_unavailable_evidence
                    ),
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=set(),
                    research_items=all_model_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=[],
                    research_evidence=intelligence_manifest_evidence,
                    research_unavailable_evidence=(
                        model_health_unavailable_evidence
                    ),
                    observing=bool(factor_supply.get("suppliedFactors")),
                ),
                "impact": (
                    f"{len(all_model_features)} 个训练特征，"
                    f"{len(all_model_features & research_intelligence_names)}"
                    " 个来自情报"
                ),
            },
            {
                "consumerKey": "candidate_simulation",
                "consumerLabel": "候选模拟账户",
                "structuredData": _usage_cell(
                    formal_items=set(),
                    research_items=candidate_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=candidate_evidence,
                    research_unavailable_evidence=[
                        *model_health_unavailable_evidence,
                        *iteration_unavailable_evidence,
                    ],
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=set(),
                    research_items=candidate_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=candidate_evidence,
                    research_unavailable_evidence=[
                        *model_health_unavailable_evidence,
                        *iteration_unavailable_evidence,
                    ],
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=set(),
                    research_items=candidate_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=[],
                    research_evidence=candidate_evidence,
                    research_unavailable_evidence=[
                        *model_health_unavailable_evidence,
                        *iteration_unavailable_evidence,
                    ],
                ),
                "impact": (
                    "候选模拟状态不可用"
                    if not iteration_available
                    else (
                        f"本期 {_integer(iteration.get('selected_count'))} 个入选，"
                        f"{_integer(iteration.get('trades_executed'))} 笔成交"
                    )
                ),
            },
        ]
    )

    structured_stages = [
        {
            "key": "sources",
            "label": "行情与财务",
            "status": (
                "success"
                if any(
                    row["status"] == "used" for row in source_groups.values()
                )
                else "research"
            ),
            "primary": f"{len(source_groups)} 个数据源",
            "secondary": (
                f"{sum(row['selectedModelFeatureCount'] for row in source_groups.values())}"
                " 个模型特征 · "
                f"{sum(row['activeStrategyFactorCount'] for row in source_groups.values())}"
                " 个策略因子"
            ),
        },
        {
            "key": "quality",
            "label": "清洗与质量",
            "status": "success" if manifests else "unavailable",
            "primary": (
                f"{quality['pointInTimeAuditedModels']} / "
                f"{quality['modelCount']} 个模型通过点时审计"
            ),
            "secondary": "点时证据来自模型元数据",
        },
        {
            "key": "traditional",
            "label": "传统量化因子",
            "status": (
                "success"
                if active_formal_factors or selected_research_traditional
                else "research"
            ),
            "primary": (
                f"{len(active_formal_factors)} 个正式策略 · "
                f"{len(selected_research_traditional)} 个研究模型"
            ),
            "secondary": (
                f"{len(formal_traditional_names)} 个策略可用 · "
                f"{len(research_traditional_names)} 个研究定义"
            ),
        },
    ]
    pipeline = _mapping(intelligence.get("pipeline"))
    intelligence_available = (
        pipeline.get("status") != "unavailable"
        and not any(item["resource"] == "intelligence" for item in errors)
    )
    pipeline_stages = _mapping(pipeline.get("stages"))
    backlog = _mapping(pipeline.get("backlog"))
    decisions = _mapping(intelligence.get("decisions"))
    intelligence_stages = [
        {
            "key": "documents",
            "label": "公告与政策",
            "status": (
                "unavailable"
                if not intelligence_available
                else "success"
                if _integer(pipeline.get("documents"))
                else "empty"
            ),
            "primary": f"{_integer(pipeline.get('documents'))} 篇目录",
            "secondary": f"{len(_rows(pipeline.get('sources')))} 个来源",
        },
        {
            "key": "artifacts",
            "label": "下载与解析",
            "status": (
                "unavailable"
                if not intelligence_available
                else "running"
                if _integer(backlog.get("total"))
                else "success"
            ),
            "primary": f"{_integer(pipeline_stages.get('parsed'))} 篇已解析",
            "secondary": f"{_integer(backlog.get('total'))} 篇积压",
        },
        {
            "key": "semantic",
            "label": "语义事件",
            "status": (
                "unavailable"
                if not intelligence_available
                else (
                    "success"
                    if _integer(pipeline_stages.get("semanticCompleted"))
                    else "research"
                )
            ),
            "primary": (
                f"{_integer(pipeline_stages.get('canonicalEvents'))} 个标准事件"
            ),
            "secondary": f"{_integer(decisions.get('failed'))} 个失败",
        },
        {
            "key": "intelligence_factors",
            "label": "情报因子",
            "status": (
                "unavailable"
                if not intelligence_available
                else "success"
                if factor_supply.get("modelEligible")
                else "research"
            ),
            "primary": f"{_integer(factor_supply.get('suppliedFactors'))} 个已计算",
            "secondary": (
                f"{len(factor_supply.get('modelEligibleFactors') or [])} 个可入模"
            ),
        },
    ]
    payload = {
        "generated_at": _generated_at(),
        "errors": errors,
        "market": market,
        "market_label": agg.MARKET_LABELS.get(market, market),
        "structured": {
            "stages": structured_stages,
            "sources": sorted(
                source_groups.values(), key=lambda row: row["source"]
            )[:MAX_TABLE_ROWS],
            "coverage": coverage,
            "factorGroups": sorted(
                family_groups.values(), key=lambda row: row["family"]
            )[:MAX_TABLE_ROWS],
            "selectedFeatures": sorted(selected_research_traditional)[
                :MAX_FEATURE_ROWS
            ],
            "formalFactorNamespace": {
                "definedFactorCount": len(formal_traditional_names),
                "activeFactorCount": len(active_formal_factors),
                "activeFactors": sorted(active_formal_factors)[
                    :MAX_FEATURE_ROWS
                ],
            },
            "researchFeatureNamespace": {
                "selectedFeatures": sorted(selected_research_traditional)[
                    :MAX_FEATURE_ROWS
                ],
                "definedFeatureCount": len(research_traditional_names),
            },
            "quality": quality,
        },
        "intelligence": {
            "stages": intelligence_stages,
            "featureNamespace": {
                "definedFeatureCount": len(research_intelligence_names),
                "selectedFeatureCount": len(
                    all_model_features & research_intelligence_names
                ),
                "selectedFeatures": sorted(
                    all_model_features & research_intelligence_names
                )[:MAX_FEATURE_ROWS],
            },
            **_mapping(_bounded_intelligence_lane(intelligence)),
        },
        "usageMatrix": usage_matrix,
    }
    return _enforce_data_intelligence_size(
        agg._json_safe(payload)
    )


def _operations_now(value: datetime | None) -> datetime:
    current = value or datetime.now(tz=OPERATIONS_TIMEZONE)
    if current.tzinfo is None:
        return current.replace(tzinfo=OPERATIONS_TIMEZONE)
    return current.astimezone(OPERATIONS_TIMEZONE)


def _operations_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"n/a", "never"}:
        return None
    match = re.search(
        r"\d{4}-\d{2}-\d{2}"
        r"(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?"
        r"(?:\s*CST|Z|[+-]\d{2}:?\d{2})?",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        text = match.group(0)
    if text.upper().endswith(" CST"):
        text = text[:-4] + "+08:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=OPERATIONS_TIMEZONE)
    return parsed.astimezone(OPERATIONS_TIMEZONE)


def _operations_service_status(
    row: dict[str, Any] | None,
    *,
    current: datetime,
    runtime_available: bool,
) -> str:
    if not runtime_available:
        return "unavailable"
    if not row:
        return "waiting_schedule"
    if str(row.get("loadState") or "loaded").lower() != "loaded":
        return "unavailable"
    if str(row.get("activeState") or "").lower() in {
        "active",
        "activating",
        "reloading",
    }:
        return "running"
    started = _operations_timestamp(row.get("startedAt"))
    if started is None or started.date() != current.date():
        return "waiting_schedule"
    result = str(row.get("result") or "unknown").lower()
    exit_status = row.get("exitStatus")
    if result == "success" and exit_status == 75:
        return "skipped"
    if result not in {"success", "unknown", ""}:
        return "failed"
    if exit_status not in {0, None}:
        return "failed"
    if result == "success":
        return "success"
    return "waiting_schedule"


def _operations_chain_status(
    statuses: list[str],
    *,
    upstream_ready: bool,
    runtime_available: bool,
) -> str:
    if not runtime_available:
        return "unavailable"
    if "unavailable" in statuses:
        return "unavailable"
    if "failed" in statuses:
        return "failed"
    if "running" in statuses:
        return "running"
    if statuses and all(status in {"success", "skipped"} for status in statuses):
        return (
            "skipped"
            if all(status == "skipped" for status in statuses)
            else "success"
        )
    if not upstream_ready:
        return "waiting_upstream"
    return "waiting_schedule"


def _looks_secret_token(value: object) -> bool:
    token = str(value or "").strip()
    if (
        len(token) >= 2
        and token[0] in {"'", '"'}
        and token[-1] == token[0]
    ):
        token = token[1:-1]
    return (
        len(token) >= 16
        and not any(character.isspace() for character in token)
        and any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
    )


def _redact_named_secret(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}<redacted>"


def _redact_secret_token_if_needed(match: re.Match[str]) -> str:
    if not _looks_secret_token(match.group("value")):
        return match.group(0)
    return f"{match.group('prefix')}<redacted>"


def _sanitize_run_error(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(
        r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s@]+)@",
        r"\1<redacted>@",
        text,
    )
    text = re.sub(
        r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)"
        r"(?P<prefix>"
        r"(?<![A-Za-z0-9_])[\"']?"
        r"(?:"
        r"token|api[_-]?key|apikey|"
        r"access[_-]?key(?:[_-]?(?:id|secret))?|secret|password|"
        r"[A-Z][A-Z0-9_]*(?:"
        r"_API_KEY|_TOKEN|_PASSWORD|_SECRET|"
        r"_ACCESS_KEY[A-Z0-9_]*"
        r")"
        r")"
        r"[\"']?\s*[:=]\s*"
        r")"
        r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\s;&}\]]+)",
        _redact_named_secret,
        text,
    )
    text = re.sub(
        r"(?i)([?&](?:token|api[_-]?key|access[_-]?key|secret|password)=)"
        r"[^&\s]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)"
        r"(?P<prefix>"
        r"(?<![A-Za-z0-9_])"
        r"(?:auth(?:entication|orization)?|provider|credential|"
        r"secret|token|api)"
        r"\s+[\"']?key[\"']?\s*[:=]\s*"
        r")"
        r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\s;&}\]]+)",
        _redact_secret_token_if_needed,
        text,
    )
    text = re.sub(
        r"(?i)"
        r"(?P<prefix>"
        r"(?<![A-Za-z0-9_])[\"']?credential[\"']?"
        r"(?:\s*[:=]\s*|\s+)"
        r")"
        r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\s;&}\]]+)",
        _redact_secret_token_if_needed,
        text,
    )
    standalone_patterns = (
        r"(?<![A-Za-z0-9_-])sk-(?:live|proj|ant(?:-api\d+)?|or-v1)-"
        r"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
        r"(?<![A-Za-z0-9_-])sk_(?:live|test)_"
        r"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])",
        r"(?<![A-Za-z0-9_-])AIza[A-Za-z0-9_-]{30,}"
        r"(?![A-Za-z0-9_-])",
        r"(?<![A-Za-z0-9_-])(?:gh[pousr]_|github_pat_)"
        r"[A-Za-z0-9_]{20,}(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{16,}"
        r"(?![A-Za-z0-9-])",
        r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}"
        r"(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:AKID|LTAI)[A-Za-z0-9]{12,}"
        r"(?![A-Za-z0-9])",
    )
    for pattern in standalone_patterns:
        text = re.sub(pattern, "<redacted>", text)
    text = re.sub(
        r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{24,}"
        r"(?![A-Za-z0-9_-])",
        _redact_legacy_sk_token,
        text,
    )
    return text[:200]


def _redact_legacy_sk_token(match: re.Match[str]) -> str:
    token = match.group(0)[3:]
    character_classes = sum(
        (
            any(character.islower() for character in token),
            any(character.isupper() for character in token),
            any(character.isdigit() for character in token),
        )
    )
    if character_classes < 3 or len(set(token)) < 12:
        return match.group(0)
    return "<redacted>"


def _operations_chain_units(
    key: str,
    units: tuple[str, ...],
    *,
    scope: str,
) -> tuple[str, ...]:
    if key != "simulation" or scope not in competition.MARKETS:
        return units
    # model-iteration.service loops both markets in one process, so its
    # systemd result cannot be attributed safely to either single market.
    return OPERATIONS_SIMULATION_UNITS_BY_MARKET[scope]


def _operations_run_rows(
    path: Path,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            raw_rows = deque(csv.DictReader(handle), maxlen=max(40, limit * 4))
    except (FileNotFoundError, OSError, csv.Error):
        return []
    selected: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
    for index, row in enumerate(raw_rows):
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        finished_rank = _operations_timestamp(
            row.get("finished_at")
        )
        started_rank = _operations_timestamp(row.get("started_at"))
        terminal = int(
            finished_rank is not None or status not in {"", "running"}
        )
        rank = (
            terminal,
            finished_rank.timestamp() if finished_rank else float("-inf"),
            started_rank.timestamp() if started_rank else float("-inf"),
            index,
            run_id,
        )
        if run_id not in selected or rank >= selected[run_id][0]:
            selected_row: dict[str, Any] = dict(row)
            selected_row["_append_index"] = index
            selected_row["_finished_rank"] = (
                finished_rank.timestamp() if finished_rank else None
            )
            selected_row["_started_rank"] = (
                started_rank.timestamp() if started_rank else None
            )
            selected[run_id] = (rank, selected_row)
    return [
        pair[1]
        for pair in sorted(
            selected.values(),
            key=lambda pair: (
                pair[1].get("_finished_rank")
                if pair[1].get("_finished_rank") is not None
                else (
                    pair[1].get("_started_rank")
                    if pair[1].get("_started_rank") is not None
                    else float("-inf")
                ),
                pair[1].get("_started_rank")
                if pair[1].get("_started_rank") is not None
                else float("-inf"),
                int(pair[1].get("_append_index") or 0),
                str(pair[1].get("run_id") or ""),
            ),
            reverse=True,
        )[:limit]
    ]


def _recent_strategy_runs(
    root: Path,
    scope: str,
    *,
    limit: int = MAX_TABLE_ROWS,
) -> list[dict[str, Any]]:
    markets = (
        [scope]
        if scope in competition.MARKETS
        else list(competition.MARKETS)
    )
    rows: list[dict[str, Any]] = []
    for market in markets:
        for public_key, agent, label in PUBLIC_STRATEGIES:
            path = root / "data" / market / agent / "runs.csv"
            for raw in _operations_run_rows(path, limit=limit):
                status = _text(raw.get("status"), limit=32).lower() or "unknown"
                if scope == "exceptions" and status != "failed":
                    continue
                rows.append(
                    {
                        "runId": _text(raw.get("run_id"), limit=256),
                        "market": market,
                        "strategyKey": public_key,
                        "strategyLabel": label,
                        "command": _text(raw.get("command"), limit=128),
                        "asOf": _text(raw.get("as_of"), limit=64),
                        "status": status,
                        "startedAt": _text(raw.get("started_at"), limit=64),
                        "finishedAt": _text(raw.get("finished_at"), limit=64),
                        "durationMs": _integer(raw.get("duration_ms")),
                        "errorSummary": _sanitize_run_error(
                            raw.get("error_summary")
                        ),
                        "_finishedRank": raw.get("_finished_rank"),
                        "_startedRank": raw.get("_started_rank"),
                        "_appendIndex": raw.get("_append_index"),
                    }
                )
    rows.sort(
        key=lambda row: (
            row["_finishedRank"]
            if row["_finishedRank"] is not None
            else (
                row["_startedRank"]
                if row["_startedRank"] is not None
                else float("-inf")
            ),
            row["_startedRank"]
            if row["_startedRank"] is not None
            else float("-inf"),
            row["_appendIndex"],
            row["runId"],
        ),
        reverse=True,
    )
    bounded_rows = rows[:limit]
    for row in bounded_rows:
        row.pop("_finishedRank", None)
        row.pop("_startedRank", None)
        row.pop("_appendIndex", None)
    return bounded_rows


def _formal_daily_freshness(
    root: Path,
    scope: str,
    *,
    current: datetime,
) -> dict[str, Any]:
    markets = [scope] if scope in competition.MARKETS else list(competition.MARKETS)
    expected = len(markets) * len(PUBLIC_STRATEGIES)
    successes_by_date: dict[str, set[tuple[str, str]]] = {}
    current_failed = False
    for market in markets:
        for _public_key, agent, _label in PUBLIC_STRATEGIES:
            path = root / "data" / market / agent / "runs.csv"
            for row in _operations_run_rows(path, limit=100):
                if str(row.get("command") or "") != "run-daily":
                    continue
                target = str(row.get("as_of") or "").strip()
                if not target:
                    target = str(row.get("started_at") or "")[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
                    continue
                status = str(row.get("status") or "").strip().lower()
                if status == "success":
                    successes_by_date.setdefault(target, set()).add((market, agent))
                elif target == current.date().isoformat() and status in {
                    "failed",
                    "error",
                }:
                    current_failed = True

    today = current.date().isoformat()
    completed = len(successes_by_date.get(today, set()))
    complete_dates = sorted(
        target
        for target, identities in successes_by_date.items()
        if len(identities) == expected
    )
    return {
        "asOfDate": today,
        "lastCompleteDate": complete_dates[-1] if complete_dates else None,
        "completedTasks": completed,
        "expectedTasks": expected,
        "hasCurrentFailure": current_failed,
    }


def _operations_local_backfill(root: Path) -> dict[str, Any]:
    path = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "artifact_backfill_state.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "status": "unavailable",
            "phase": None,
            "reason": None,
            "updatedAt": None,
        }
    history = _rows(_mapping(payload).get("history"))
    latest = _mapping(history[-1]) if history else {}
    return {
        "status": str(latest.get("status") or "unavailable"),
        "phase": _scalar(payload.get("phase"), text_limit=32),
        "reason": _scalar(latest.get("reason"), text_limit=256),
        "updatedAt": _scalar(payload.get("updated_at"), text_limit=128),
    }


def _operations_intelligence(
    intelligence: Any,
    *,
    root: Path,
) -> dict[str, Any]:
    intelligence = _mapping(intelligence)
    pipeline = _mapping(intelligence.get("pipeline"))
    backlog = {
        key: _integer(_mapping(pipeline.get("backlog")).get(key))
        for key in ("download", "parse", "semantic", "total")
    }
    workers = _mapping(pipeline.get("artifactWorkers"))
    return {
        "status": str(pipeline.get("status") or "unavailable"),
        "snapshotGeneratedAt": _scalar(
            pipeline.get("snapshotGeneratedAt"),
            text_limit=128,
        ),
        "backlog": backlog,
        "artifactWorkers": {
            "status": str(workers.get("status") or "unavailable"),
            "activeLeases": _integer(workers.get("activeLeases")),
            "latestFinishedAt": _scalar(
                workers.get("latestFinishedAt"),
                text_limit=128,
            ),
        },
        "localBackfill": _operations_local_backfill(root),
    }


def _operations_disk(root: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(root)
        operational_capacity = usage.used + usage.free
        ratio = usage.used / operational_capacity if operational_capacity else 0.0
    except OSError:
        return {"status": "unavailable", "usedRatio": None}
    return {
        "status": "available",
        "usedRatio": round(ratio, 6),
        "totalBytes": int(usage.total),
        "freeBytes": int(usage.free),
    }


def _operations_interventions(
    recent_runs: list[dict[str, Any]],
    *,
    disk: dict[str, Any],
    background: dict[str, Any],
    current: datetime,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    disk_ratio = disk.get("usedRatio")
    if isinstance(disk_ratio, (int, float)) and disk_ratio >= 0.88:
        items.append(
            {
                "key": "disk_capacity",
                "severity": "critical",
                "title": "磁盘使用率超过 88%",
                "evidence": f"{disk_ratio:.1%}",
            }
        )
    elif isinstance(disk_ratio, (int, float)) and disk_ratio >= 0.80:
        items.append(
            {
                "key": "disk_capacity",
                "severity": "warning",
                "title": "磁盘使用率超过 80%",
                "evidence": f"{disk_ratio:.1%}",
            }
        )

    credential_terms = (
        "credential",
        "unauthorized",
        "forbidden",
        "api_key",
        "api-key",
        "access key",
        "invalid token",
        "凭据",
        "密钥",
    )
    for row in recent_runs:
        error = str(row.get("errorSummary") or "").lower()
        if row.get("status") == "failed" and any(
            term in error for term in credential_terms
        ):
            items.append(
                {
                    "key": f"credential:{row['market']}:{row['strategyKey']}:"
                    f"{row['runId']}",
                    "severity": "critical",
                    "title": f"{row['strategyLabel']} 凭据错误",
                    "evidence": error[:200],
                }
            )
            break

    backlog = _mapping(background.get("backlog"))
    workers = _mapping(background.get("artifactWorkers"))
    local_backfill = _mapping(background.get("localBackfill"))
    observed_times = [
        timestamp
        for timestamp in (
            _operations_timestamp(workers.get("latestFinishedAt")),
            _operations_timestamp(local_backfill.get("updatedAt")),
        )
        if timestamp is not None
    ]
    latest_finished = max(observed_times) if observed_times else None
    if (
        _integer(backlog.get("total")) > 0
        and latest_finished is not None
        and (current - latest_finished).total_seconds() > 24 * 3600
        and _integer(workers.get("activeLeases")) == 0
    ):
        items.append(
            {
                "key": "artifact_worker_stale",
                "severity": "critical",
                "title": "PDF 回填超过 24 小时没有完成记录",
                "evidence": latest_finished.isoformat(timespec="seconds"),
            }
        )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in recent_runs:
        grouped.setdefault(
            (
                str(row.get("market") or ""),
                str(row.get("strategyKey") or ""),
                str(row.get("command") or ""),
            ),
            [],
        ).append(row)
    for key, rows in grouped.items():
        consecutive = 0
        for row in rows:
            if row.get("status") != "failed":
                break
            consecutive += 1
        if consecutive >= 2:
            items.append(
                {
                    "key": "consecutive_failure:" + ":".join(key),
                    "severity": "critical",
                    "title": (
                        f"{rows[0]['strategyLabel']} "
                        f"{rows[0]['command']} 连续失败"
                    ),
                    "evidence": f"{consecutive} 次",
                }
            )
    return items[:MAX_TABLE_ROWS]


def _bound_operations_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        bounded = value[:MAX_TEXT_LENGTH]
        return bounded, bounded != value
    if isinstance(value, list):
        changed = len(value) > MAX_TABLE_ROWS
        result: list[Any] = []
        for item in value[:MAX_TABLE_ROWS]:
            bounded_item, item_changed = _bound_operations_value(item)
            result.append(bounded_item)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, dict):
        changed = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            bounded_item, item_changed = _bound_operations_value(item)
            result[str(key)] = bounded_item
            changed = changed or item_changed
        return result, changed
    return value, False


def _operations_minimal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": payload.get("generated_at"),
        "errors": _rows(payload.get("errors"))[:MAX_TABLE_ROWS],
        "scope": payload.get("scope"),
        "runtime": {
            key: _mapping(payload.get("runtime")).get(key)
            for key in ("status", "lastKnownAt", "reason")
        },
        "dailyFreshness": {
            key: _mapping(payload.get("dailyFreshness")).get(key)
            for key in (
                "asOfDate",
                "status",
                "lastCompleteDate",
                "completedTasks",
                "expectedTasks",
            )
        },
        "mainChain": [
            {
                key: row.get(key)
                for key in ("key", "label", "status", "primary", "secondary")
            }
            for row in _rows(payload.get("mainChain"))[:MAX_TABLE_ROWS]
        ],
        "background": {
            key: _mapping(payload.get("background")).get(key)
            for key in (
                "status",
                "snapshotGeneratedAt",
                "backlog",
                "localBackfill",
            )
        },
        "backgroundWorkers": [
            {
                key: row.get(key)
                for key in ("key", "label", "status", "loadState", "reason")
            }
            for row in _rows(payload.get("backgroundWorkers"))[
                :MAX_TABLE_ROWS
            ]
        ],
        "schedules": {
            cadence: [
                {
                    key: row.get(key)
                    for key in (
                        "unit",
                        "label",
                        "status",
                        "loadState",
                        "reason",
                    )
                }
                for row in _rows(_mapping(payload.get("schedules")).get(cadence))[
                    :MAX_TABLE_ROWS
                ]
            ]
            for cadence in ("daily", "weekly", "monthly")
        },
        "recentRuns": [],
        "disk": {
            key: _mapping(payload.get("disk")).get(key)
            for key in ("status", "usedRatio")
        },
        "interventions": [
            {
                key: row.get(key)
                for key in ("key", "severity", "title", "evidence")
            }
            for row in _rows(payload.get("interventions"))[:MAX_TABLE_ROWS]
        ],
        "truncated": True,
        "truncationReason": "serialized_size_limit",
    }


def _enforce_operations_size(
    payload: dict[str, Any],
    *,
    pre_truncated: bool,
) -> dict[str, Any]:
    payload["truncated"] = pre_truncated
    payload["truncationReason"] = (
        "serialized_size_limit" if pre_truncated else None
    )
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    payload["truncated"] = True
    payload["truncationReason"] = "serialized_size_limit"
    for row in _rows(payload.get("recentRuns")):
        row["errorSummary"] = ""
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    for stage in _rows(payload.get("mainChain")):
        stage["units"] = [
            {
                key: unit.get(key)
                for key in ("unit", "status", "loadState", "reason")
            }
            for unit in _rows(stage.get("units"))
        ]
        stage["crossMarketUnits"] = [
            {
                key: unit.get(key)
                for key in (
                    "unit",
                    "status",
                    "loadState",
                    "reason",
                    "loadReason",
                )
            }
            for unit in _rows(stage.get("crossMarketUnits"))
        ]
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    payload["recentRuns"] = _rows(payload.get("recentRuns"))[:10]
    for worker in _rows(payload.get("backgroundWorkers")):
        for key in (
            "serviceUnit",
            "timerUnit",
            "lastResult",
            "startedAt",
            "finishedAt",
            "nextTriggerAt",
        ):
            worker.pop(key, None)
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    payload["recentRuns"] = []
    for stage in _rows(payload.get("mainChain")):
        stage["units"] = []
        stage["crossMarketUnits"] = []
    if _serialized_size(payload) < MAX_SERIALIZED_BYTES:
        return payload

    minimal = _operations_minimal_payload(payload)
    bounded_minimal, _ = _bound_operations_value(minimal)
    return agg._json_safe(bounded_minimal)


def build_dashboard_operations_center_data(
    *,
    repo_root: str | Path | None = None,
    scope: str = "all",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded, read-only snapshot of scheduled runtime evidence."""

    if scope not in OPERATIONS_SCOPES:
        from .dashboard_http import InvalidDashboardQuery

        raise InvalidDashboardQuery(
            "scope must be all, a_share, cn_qdii_etf, or exceptions"
        )
    root = _root(repo_root)
    current = _operations_now(now)
    errors: list[dict[str, str]] = []
    runtime = _mapping(
        _safe_workspace_read(
            errors,
            "runtime",
            {
                "status": "unavailable",
                "last_known_at": None,
                "reason": "runtime_status_unavailable",
                "services": {},
                "timers": {},
            },
            read_dashboard_runtime,
        )
    )
    runtime_available = runtime.get("status") == "available"
    services = _mapping(runtime.get("services"))
    timers = _mapping(runtime.get("timers"))

    main_chain: list[dict[str, Any]] = []
    upstream_ready = True
    for key, label, units, timer_unit in OPERATIONS_MAIN_CHAIN:
        cross_market_units: list[dict[str, Any]] = []
        units = _operations_chain_units(key, units, scope=scope)
        statuses = [
            _operations_service_status(
                _mapping(services.get(unit)) or None,
                current=current,
                runtime_available=runtime_available,
            )
            for unit in units
        ]
        status = _operations_chain_status(
            statuses,
            upstream_ready=upstream_ready,
            runtime_available=runtime_available,
        )
        timer = _mapping(timers.get(timer_unit)) if timer_unit else {}
        completed = sum(
            item in {"success", "skipped"} for item in statuses
        )
        main_chain.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "primary": f"{completed} / {len(units)} 个任务完成",
                "secondary": (
                    f"下次 {timer['nextTriggerAt']}"
                    if timer.get("nextTriggerAt")
                    else "由上游成功后自动触发"
                ),
                "units": [
                    {
                        "unit": unit,
                        **_mapping(services.get(unit)),
                        "status": statuses[index],
                    }
                    for index, unit in enumerate(units)
                ][:MAX_TABLE_ROWS],
                "crossMarketUnits": cross_market_units[:MAX_TABLE_ROWS],
            }
        )
        upstream_ready = status == "success"

    intelligence = _safe_workspace_read(
        errors,
        "intelligence",
        _empty_intelligence_workspace(),
        build_dashboard_intelligence_data,
        repo_root=root,
        market="a_share",
        agent="codex",
        limit=1,
    )
    background = _operations_intelligence(intelligence, root=root)
    background_workers: list[dict[str, Any]] = []
    for key, label, service_unit, timer_unit in OPERATIONS_BACKGROUND:
        service = _mapping(services.get(service_unit))
        timer = _mapping(timers.get(timer_unit))
        service_load_state = str(service.get("loadState") or "loaded")
        background_workers.append(
            {
                "key": key,
                "label": label,
                "status": _operations_service_status(
                    service or None,
                    current=current,
                    runtime_available=runtime_available,
                ),
                "loadState": service_load_state,
                "reason": (
                    service.get("reason")
                    if service_load_state.lower() != "loaded"
                    else None
                ),
                "serviceUnit": service_unit,
                "timerUnit": timer_unit or "",
                "lastResult": service.get("result"),
                "startedAt": service.get("startedAt"),
                "finishedAt": service.get("finishedAt"),
                "nextTriggerAt": timer.get("nextTriggerAt"),
                "backlog": (
                    background.get("backlog")
                    if key == "artifact_backfill"
                    else (
                        {
                            "semantic": _integer(
                                _mapping(background.get("backlog")).get(
                                    "semantic"
                                )
                            )
                        }
                        if key == "semantic"
                        and isinstance(background.get("backlog"), dict)
                        else None
                    )
                ),
            }
        )

    schedules: dict[str, list[dict[str, Any]]] = {
        "daily": [],
        "weekly": [],
        "monthly": [],
    }
    for unit, (label, cadence) in OPERATIONS_TIMERS.items():
        timer = _mapping(timers.get(unit))
        load_state = str(timer.get("loadState") or "loaded")
        timer_available = (
            runtime_available
            and bool(timer)
            and load_state.lower() == "loaded"
        )
        timer_reason = None
        if not timer_available:
            if timer and load_state.lower() != "loaded":
                timer_reason = (
                    timer.get("reason")
                    or f"unit_load_state_{load_state}"
                )
            else:
                timer_reason = (
                    runtime.get("reason")
                    or "runtime_timer_evidence_unavailable"
                )
        schedules[cadence].append(
            {
                "unit": unit,
                "label": label,
                "status": (
                    "unavailable"
                    if not timer_available
                    else (
                        "active"
                        if timer.get("activeState") == "active"
                        else "inactive"
                    )
                ),
                "loadState": load_state,
                "reason": timer_reason,
                "lastTriggerAt": timer.get("lastTriggerAt"),
                "nextTriggerAt": timer.get("nextTriggerAt"),
                "automation": "automatic",
            }
        )
    for rows in schedules.values():
        rows.sort(
            key=lambda row: (
                str(row.get("nextTriggerAt") or ""),
                row["unit"],
            )
        )

    recent_runs = _recent_strategy_runs(root, scope)
    freshness = _formal_daily_freshness(root, scope, current=current)
    has_current_failure = bool(freshness.pop("hasCurrentFailure"))
    main_statuses = {row["status"] for row in main_chain}
    if not runtime_available:
        freshness_status = "unavailable"
    elif freshness["completedTasks"] == freshness["expectedTasks"]:
        freshness_status = "success"
    elif "unavailable" in main_statuses:
        freshness_status = "unavailable"
    elif has_current_failure or "failed" in main_statuses:
        freshness_status = "failed"
    elif "running" in main_statuses:
        freshness_status = "running"
    else:
        freshness_status = "waiting"
    freshness["status"] = freshness_status
    disk = _operations_disk(root)
    payload = {
        "generated_at": current.isoformat(timespec="seconds"),
        "errors": errors,
        "scope": scope,
        "runtime": {
            "status": runtime.get("status") or "unavailable",
            "lastKnownAt": runtime.get("last_known_at"),
            "reason": runtime.get("reason"),
        },
        "dailyFreshness": freshness,
        "mainChain": main_chain,
        "background": background,
        "backgroundWorkers": background_workers,
        "schedules": schedules,
        "recentRuns": recent_runs,
        "disk": disk,
        "interventions": _operations_interventions(
            recent_runs,
            disk=disk,
            background=background,
            current=current,
        ),
    }
    if scope == "exceptions":
        payload["mainChain"] = [
            row
            for row in main_chain
            if row["status"] in {"failed", "unavailable"}
        ]
        payload["backgroundWorkers"] = [
            row
            for row in background_workers
            if row["status"] in {"failed", "unavailable"}
        ]
    bounded_payload, pre_truncated = _bound_operations_value(payload)
    safe_payload = agg._json_safe(bounded_payload)
    for row in safe_payload["recentRuns"]:
        if row.get("errorSummary") is None:
            row["errorSummary"] = ""
    return _enforce_operations_size(
        safe_payload,
        pre_truncated=pre_truncated,
    )
