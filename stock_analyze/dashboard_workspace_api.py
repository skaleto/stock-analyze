"""Bounded resources for the five-workspace React dashboard."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_api import _latest_strategy_model_usage
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


def _generated_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _root(repo_root: str | Path | None) -> Path:
    return Path(repo_root) if repo_root is not None else Path.cwd()


def _check_market(market: str) -> None:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    return str(value or "")[:limit]


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
            return str(candidate.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            return candidate.name
    return None


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
                    _iso_timestamp(
                        raw.get("trained_at")
                        or raw.get("created_at")
                        or registry_record.get("registered_at")
                    )
                ),
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
                "pointInTimeAudit": metrics.get("point_in_time_audit"),
                "candidateFeatureCount": _integer(
                    metrics.get("candidate_feature_count")
                )
                or len(all_features),
                "metrics": {
                    key: metrics.get(key)
                    for key in MODEL_METRIC_KEYS
                    if key in metrics
                },
            }
        )
    return sorted(rows, key=lambda row: (row["horizon"], row["modelVersion"]))


def _source_rows(value: Any) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for row in _rows(value)[:MAX_TABLE_ROWS]:
        bounded.append(
            {
                "source": _text(row.get("source"), limit=256),
                "status": _text(row.get("status"), limit=128),
                "rows": _integer(row.get("rows")),
                "failed": bool(row.get("failed")),
                "as_of": _iso_timestamp(row.get("as_of")),
                "error": _text(
                    row.get("error") or row.get("error_summary"),
                    limit=MAX_TEXT_LENGTH,
                )
                or None,
            }
        )
    return bounded


def _usage_rows(
    value: Any,
    *,
    market: str,
    champion_models: set[tuple[int, str]],
) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
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
        bounded.append(
            {
                "market": market,
                "agent": _text(row.get("agent"), limit=128),
                "strategy_label": _text(
                    row.get("strategy_label") or row.get("agent"),
                    limit=256,
                ),
                "as_of": _iso_timestamp(row.get("as_of")),
                "status": "active",
                "applied_candidates": _integer(row.get("applied_candidates")),
                "candidate_coverage": row.get("candidate_coverage") or 0.0,
                "model_versions": evidenced_versions,
                "fallback_reason": _text(
                    row.get("fallback_reason"),
                    limit=MAX_TEXT_LENGTH,
                ),
                "accounts": _integer(row.get("accounts")),
            }
        )
    return bounded[:MAX_TABLE_ROWS]


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
    return {
        key: (
            _iso_timestamp(raw.get(key))
            if key in {"selected_at", "registered_at"}
            else raw.get(key)
        )
        for key in keys
        if key in raw
    }


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
            "trainedAt": row["trainedAt"],
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
            "predictionAsOf": _iso_timestamp(iteration.get("prediction_as_of")),
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
    return agg._json_safe(payload)
