"""Bounded resources for the five-workspace React dashboard."""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_api import (
    _latest_strategy_model_usage,
    build_dashboard_intelligence_data,
)
from .dashboard_runtime import read_dashboard_runtime
from .overlay_guard import AVAILABLE_FACTORS_BY_MARKET, SENTIMENT_FACTORS
from .research.feature_registry import DEFAULT_REGISTRY, INTELLIGENCE_FEATURES


MAX_TABLE_ROWS = 20
MAX_ROLLBACK_ROWS = 5
MAX_FEATURE_ROWS = 20
MAX_MODEL_FEATURES = 20
MAX_TEXT_LENGTH = 1_000
MAX_DIAGNOSTIC_DEPTH = 4
MAX_DIAGNOSTIC_ITEMS = 8
MAX_DIAGNOSTIC_NODES = 128
MAX_DIAGNOSTIC_TEXT = 32_000
MAX_SERIALIZED_BYTES = 250_000
MAX_ABS_NUMERIC = 1_000_000_000_000_000
MODEL_METRIC_KEYS = (
    "rank_ic",
    "mean_rank_ic",
    "icir",
    "brier_score",
    "auc",
    "hit_rate_lift",
    "net_excess_return",
    "turnover",
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
        "intelligence",
        "情报增量",
        ("stock-analyze-intelligence.service",),
        "stock-analyze-intelligence.timer",
    ),
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
        "双策略及候选模型模拟",
        (
            "stock-analyze-model-iteration.service",
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
        (
            "stock-analyze-aggregate-dashboard.service",
            "stock-analyze-daily-summary.service",
        ),
        "stock-analyze-daily-summary.timer",
    ),
)
OPERATIONS_BACKGROUND = (
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
) -> tuple[dict[str, Any], bool]:
    registry_path = (
        root
        / "data"
        / "research"
        / "models"
        / market
        / str(horizon)
        / "registry.json"
    )
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
) -> str | None:
    model_root = root / "data" / "research" / "models" / market / str(horizon)
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


def _model_rows(root: Path, market: str) -> list[dict[str, Any]]:
    health = _mapping(agg._read_model_health(root, market))
    rows: list[dict[str, Any]] = []
    for raw in _rows(health.get("models")):
        metrics = _mapping(raw.get("metrics"))
        all_features = sorted(
            {
                _text(value, limit=128)
                for value in (raw.get("feature_columns") or [])
                if value
            }
        )
        horizon = _integer(raw.get("horizon"))
        model_version = _text(raw.get("model_version"), limit=256)
        registry_record, registry_champion = _registry_evidence(
            root,
            market,
            horizon,
            model_version,
        )
        artifact_ref = _model_artifact_ref(
            root,
            market,
            horizon,
            model_version,
            registry_record,
        )
        gate_reasons = [
            _text(reason, limit=256)
            for reason in (raw.get("gate_reasons") or [])
            if reason
        ][:MAX_TABLE_ROWS]
        registry_active = str(registry_record.get("status") or "") == "active"
        rows.append(
            {
                "modelVersion": model_version,
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
                "artifactRef": artifact_ref,
                "artifactStatus": "available" if artifact_ref else "missing",
                "gatePassed": raw.get("gate_passed") is True,
                "gateReasons": gate_reasons,
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
                "metrics": {
                    key: _scalar(metrics.get(key))
                    for key in MODEL_METRIC_KEYS
                    if key in metrics
                },
            }
        )
    deduplicated: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["horizon"], row["modelVersion"])
        current = deduplicated.get(key)
        if current is None or _model_evidence_rank(row) > _model_evidence_rank(
            current
        ):
            deduplicated[key] = row
    return [deduplicated[key] for key in sorted(deduplicated)]


def _source_rows(value: Any) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _rows(value):
        evidence = {
            "source": _text(row.get("source"), limit=256),
            "status": _text(row.get("status"), limit=128),
            "rows": _integer(row.get("rows")),
            "failed": bool(row.get("failed")),
            "as_of": _scalar(row.get("as_of"), text_limit=256),
            "error": _text(
                row.get("error") or row.get("error_summary"),
                limit=MAX_TEXT_LENGTH,
            )
            or None,
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


def _model_feature_evidence(
    root: Path,
    market: str,
) -> tuple[dict[tuple[int, str], set[str]], dict[str, Any]]:
    health = _mapping(agg._read_model_health(root, market))
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
    root: Path,
    market: str,
) -> dict[str, set[tuple[int, str]]]:
    by_agent: dict[str, set[tuple[int, str]]] = {}
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for row in _rows(_latest_strategy_model_usage(root)):
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
    budget = {"nodes": 512, "text": 80_000}
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
            if len(sorted_keys) > MAX_TABLE_ROWS:
                reasons.add("item_limit")
            for raw_key in sorted_keys:
                key = _text(raw_key, limit=128)
                if key in _RESOURCE_OMIT_KEYS:
                    continue
                if len(result) >= MAX_TABLE_ROWS:
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
        {
            key: _mapping(intelligence.get(key))
            for key in required
        }
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


def build_dashboard_model_research_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
) -> dict[str, Any]:
    """Build a bounded, evidence-backed model lifecycle snapshot."""

    _check_market(market)
    root = _root(repo_root)
    all_models = _model_rows(root, market)
    models = all_models[:MAX_TABLE_ROWS]
    selected_features = sorted(
        {
            feature
            for model in all_models
            for feature in model.pop("_allFeatureColumns")
        }
    )
    registry_names = {item.name for item in DEFAULT_REGISTRY}
    intelligence_names = {item.name for item in INTELLIGENCE_FEATURES}
    intelligence_features = sorted(set(selected_features) & intelligence_names)
    structured_features = sorted(
        set(selected_features) & (registry_names - intelligence_names)
    )
    unclassified_features = sorted(set(selected_features) - registry_names)
    raw_source_health = agg._read_research_source_health(root, market)
    source_health = _source_rows(raw_source_health)
    iteration = _mapping(agg._read_model_iteration_status(root, market))
    champion_models = {
        (row["horizon"], row["modelVersion"])
        for row in all_models
        if row["isChampion"]
    }
    usage = _usage_rows(
        _latest_strategy_model_usage(root),
        market=market,
        champion_models=champion_models,
    )
    champions = [
        {
            "modelVersion": row["modelVersion"],
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
    required_cycles = _integer(candidate.get("shadow_cycles")) + _integer(
        candidate.get("shadow_cycles_remaining")
    )
    passed = sum(1 for row in all_models if row["gatePassed"])
    candidate_count = max(
        (row["candidateFeatureCount"] for row in all_models),
        default=0,
    )
    audited = [
        row["pointInTimeAudit"]
        for row in all_models
        if row["pointInTimeAudit"] is not None
    ]
    point_in_time_status = (
        "passed"
        if audited and all(value is True for value in audited)
        else "failed"
        if audited
        else "unavailable"
    )
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
            "status": "success" if all_models else "empty",
            "primary": f"{len(all_models)} 个研究版本",
            "secondary": (
                f"{sum(row['sampleSupport'] for row in all_models)} 条样本支持"
            ),
        },
        {
            "key": "validation",
            "label": "测试验收",
            "status": "success" if passed else "research",
            "primary": f"{passed} / {len(all_models)} 通过",
            "secondary": (
                f"{sum(len(row['gateReasons']) for row in all_models)} 个阻塞项"
            ),
        },
        {
            "key": "simulation",
            "label": "模拟运行",
            "status": "running" if candidate else "waiting_upstream",
            "primary": str(candidate.get("display_version") or "等待候选"),
            "secondary": (
                f"{_integer(candidate.get('shadow_cycles'))} / "
                f"{required_cycles or 12} 个观察周期"
            ),
        },
        {
            "key": "adoption",
            "label": "正式采用",
            "status": "success" if champions and usage else "waiting_upstream",
            "primary": f"{len(champions)} 个 Champion",
            "secondary": f"{len(usage)} 个正式策略账户已采用",
        },
    ]
    payload = {
        "generated_at": _generated_at(),
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
        "training": {"models": models},
        "validation": {
            "passed": passed,
            "total": len(all_models),
            "models": models,
        },
        "simulation": {
            "status": _text(iteration.get("status"), limit=128) or "unavailable",
            "candidate": candidate or None,
            "account": _simulation_account(iteration),
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
                "eligibleRows": _integer(iteration.get("eligible_rows")),
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
    profiles = _public_strategy_profiles(root, market)
    manifests, quality = _model_feature_evidence(root, market)
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

    intelligence = _mapping(
        build_dashboard_intelligence_data(
            repo_root=root,
            market=market,
            agent="codex",
            limit=1,
        )
    )
    factor_supply = _mapping(intelligence.get("factorSupply"))
    iteration = _mapping(agg._read_model_iteration_status(root, market))
    candidate = _mapping(iteration.get("candidate"))
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
    active_lineage = _active_lineage_models(root, market)

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
                    research_unavailable_evidence=missing_manifest_evidence,
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=overlay_factors,
                    research_items=applied_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=["strategy_overlay"],
                    research_evidence=lineage_evidence,
                    research_unavailable_evidence=missing_manifest_evidence,
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=overlay_factors,
                    research_items=applied_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=["strategy_overlay"],
                    research_evidence=lineage_evidence,
                    research_unavailable_evidence=missing_manifest_evidence,
                ),
                "modelAdoption": {
                    "status": "active" if applied_identities else "rule_only",
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
                    f"正式决策采用 {len(applied_identities)} 个模型版本"
                    if applied_identities
                    else "本期规则驱动"
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
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=set(),
                    research_items=all_model_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=manifest_evidence,
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=set(),
                    research_items=all_model_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=[],
                    research_evidence=intelligence_manifest_evidence,
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
                ),
                "traditionalFactors": _usage_cell(
                    formal_items=set(),
                    research_items=candidate_features,
                    formal_eligible=formal_traditional_names,
                    research_eligible=research_traditional_names,
                    formal_evidence=[],
                    research_evidence=candidate_evidence,
                ),
                "intelligenceFactors": _usage_cell(
                    formal_items=set(),
                    research_items=candidate_features,
                    formal_eligible=formal_intelligence_names,
                    research_eligible=research_intelligence_names,
                    formal_evidence=[],
                    research_evidence=candidate_evidence,
                ),
                "impact": (
                    f"本期 {_integer(iteration.get('selected_count'))} 个入选，"
                    f"{_integer(iteration.get('trades_executed'))} 笔成交"
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
    pipeline_stages = _mapping(pipeline.get("stages"))
    backlog = _mapping(pipeline.get("backlog"))
    decisions = _mapping(intelligence.get("decisions"))
    intelligence_stages = [
        {
            "key": "documents",
            "label": "公告与政策",
            "status": "success" if _integer(pipeline.get("documents")) else "empty",
            "primary": f"{_integer(pipeline.get('documents'))} 篇目录",
            "secondary": f"{len(_rows(pipeline.get('sources')))} 个来源",
        },
        {
            "key": "artifacts",
            "label": "下载与解析",
            "status": "running" if _integer(backlog.get("total")) else "success",
            "primary": f"{_integer(pipeline_stages.get('parsed'))} 篇已解析",
            "secondary": f"{_integer(backlog.get('total'))} 篇积压",
        },
        {
            "key": "semantic",
            "label": "语义事件",
            "status": (
                "success"
                if _integer(pipeline_stages.get("semanticCompleted"))
                else "research"
            ),
            "primary": (
                f"{_integer(pipeline_stages.get('canonicalEvents'))} 个标准事件"
            ),
            "secondary": f"{_integer(decisions.get('failed'))} 个失败",
        },
        {
            "key": "intelligence_factors",
            "label": "情报因子",
            "status": "success" if factor_supply.get("modelEligible") else "research",
            "primary": f"{_integer(factor_supply.get('suppliedFactors'))} 个已计算",
            "secondary": (
                f"{len(factor_supply.get('modelEligibleFactors') or [])} 个可入模"
            ),
        },
    ]
    payload = {
        "generated_at": _generated_at(),
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
    if not text or text in {"n/a", "never"}:
        return None
    text = re.sub(r"^[A-Za-z]{3}\s+", "", text)
    if text.endswith(" CST"):
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
        r"(?i)\b(token|api[_-]?key|apikey|"
        r"access[_-]?key(?:[_-]?(?:id|secret))?|secret|password)"
        r"\b(\s*[:=]\s*)([^,\s;&]+)",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)([?&](?:token|api[_-]?key|access[_-]?key|secret|password)=)"
        r"[^&\s]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\bcredential(\s+)([^\s,;]+)",
        _redact_credential_value,
        text,
    )
    standalone_patterns = (
        r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}"
        r"(?![A-Za-z0-9_-])",
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
    return text[:200]


def _redact_credential_value(match: re.Match[str]) -> str:
    value = match.group(2)
    diagnostic_words = {
        "check",
        "config",
        "configuration",
        "error",
        "expired",
        "file",
        "invalid",
        "load",
        "loading",
        "missing",
        "not",
        "provider",
        "refresh",
        "required",
        "rotation",
        "source",
        "status",
        "store",
        "unavailable",
        "validation",
    }
    looks_secret = len(value) >= 8
    if value.lower() in diagnostic_words or not looks_secret:
        return match.group(0)
    return f"credential{match.group(1)}<redacted>"


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
) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            raw_rows = deque(csv.DictReader(handle), maxlen=max(40, limit * 4))
    except (FileNotFoundError, OSError, csv.Error):
        return []
    selected: dict[str, tuple[tuple[int, str, int], dict[str, str]]] = {}
    for index, row in enumerate(raw_rows):
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        finished_at = str(row.get("finished_at") or "")
        terminal = int(bool(finished_at) or status not in {"", "running"})
        rank = (terminal, finished_at, index)
        if run_id not in selected or rank >= selected[run_id][0]:
            selected[run_id] = (rank, row)
    return [
        pair[1]
        for pair in sorted(
            selected.values(),
            key=lambda pair: (
                str(pair[1].get("started_at") or ""),
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
                    }
                )
    rows.sort(
        key=lambda row: (row["startedAt"], row["runId"]),
        reverse=True,
    )
    return rows[:limit]


def _operations_intelligence(
    root: Path,
) -> dict[str, Any]:
    try:
        intelligence = _mapping(
            build_dashboard_intelligence_data(
                repo_root=root,
                market="a_share",
                agent="codex",
                limit=1,
            )
        )
    except Exception:  # noqa: BLE001 - one observability source may degrade
        return {
            "status": "unavailable",
            "backlog": None,
            "artifactWorkers": None,
        }
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
    }


def _operations_disk(root: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(root)
        ratio = usage.used / usage.total if usage.total else 0.0
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
    if isinstance(disk_ratio, (int, float)) and disk_ratio >= 0.85:
        items.append(
            {
                "key": "disk_capacity",
                "severity": "critical",
                "title": "磁盘使用率超过 85%",
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
    latest_finished = _operations_timestamp(workers.get("latestFinishedAt"))
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
    try:
        runtime = _mapping(read_dashboard_runtime())
    except Exception:  # noqa: BLE001 - local hosts may not provide systemd
        runtime = {
            "status": "unavailable",
            "last_known_at": None,
            "reason": "runtime_status_unavailable",
            "services": {},
            "timers": {},
        }
    runtime_available = runtime.get("status") == "available"
    services = _mapping(runtime.get("services"))
    timers = _mapping(runtime.get("timers"))

    main_chain: list[dict[str, Any]] = []
    upstream_ready = True
    for key, label, units, timer_unit in OPERATIONS_MAIN_CHAIN:
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
                        "status": statuses[index],
                        **_mapping(services.get(unit)),
                    }
                    for index, unit in enumerate(units)
                ][:MAX_TABLE_ROWS],
            }
        )
        upstream_ready = status == "success"

    background = _operations_intelligence(root)
    background_workers: list[dict[str, Any]] = []
    for key, label, service_unit, timer_unit in OPERATIONS_BACKGROUND:
        service = _mapping(services.get(service_unit))
        timer = _mapping(timers.get(timer_unit))
        background_workers.append(
            {
                "key": key,
                "label": label,
                "status": _operations_service_status(
                    service or None,
                    current=current,
                    runtime_available=runtime_available,
                ),
                "serviceUnit": service_unit,
                "timerUnit": timer_unit,
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
        schedules[cadence].append(
            {
                "unit": unit,
                "label": label,
                "status": (
                    "unavailable"
                    if not runtime_available or not timer
                    else (
                        "active"
                        if timer.get("activeState") == "active"
                        else "inactive"
                    )
                ),
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
    disk = _operations_disk(root)
    payload = {
        "generated_at": current.isoformat(timespec="seconds"),
        "scope": scope,
        "runtime": {
            "status": runtime.get("status") or "unavailable",
            "lastKnownAt": runtime.get("last_known_at"),
            "reason": runtime.get("reason"),
        },
        "dailyFreshness": {
            "asOfDate": current.date().isoformat(),
            "status": (
                "unavailable"
                if not runtime_available
                else (
                    "failed"
                    if any(row["status"] == "failed" for row in main_chain)
                    else (
                        "running"
                        if any(
                            row["status"] == "running" for row in main_chain
                        )
                        else (
                            "success"
                            if all(
                                row["status"] == "success"
                                for row in main_chain
                            )
                            else "waiting"
                        )
                    )
                )
            ),
        },
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
    safe_payload = agg._json_safe(payload)
    for row in safe_payload["recentRuns"]:
        if row.get("errorSummary") is None:
            row["errorSummary"] = ""
    if _serialized_size(safe_payload) >= MAX_SERIALIZED_BYTES:
        raise ValueError("dashboard_operations_payload_exceeds_size_limit")
    return safe_payload
