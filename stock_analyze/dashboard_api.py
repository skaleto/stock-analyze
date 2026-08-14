"""Bounded domain resources for the interactive dashboard API.

The runtime files remain the source of truth.  This module controls how much of
that state each HTTP resource reads and returns so one slow research artifact
cannot block the entire dashboard.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import competition
from . import dashboard_aggregator as agg
from .dashboard_finance import build_activity, build_strategy_profile, enrich_rows
from .markets.cn_qdii_etf.lookthrough import build_portfolio_lookthrough
from .utils import safe_float
from .dashboard_http import DashboardResourceNotFound, InvalidDashboardQuery


DEFAULT_ROW_LIMIT = 200
DEFAULT_PREDICTION_LIMIT_PER_HORIZON = 12
MAX_PREDICTION_LIMIT_PER_HORIZON = 50
DEFAULT_INTELLIGENCE_ROW_LIMIT = 30
MAX_INTELLIGENCE_EVIDENCE = 100
MAX_INTELLIGENCE_FACTS = 50
INTELLIGENCE_ERROR_MESSAGE = "情报采集状态读取失败"
SYSTEM_OVERVIEW_READ_ERRORS = (
    OSError,
    UnicodeError,
    json.JSONDecodeError,
    sqlite3.Error,
    agg.DashboardDataError,
)

_COMMON_ROW_FIELDS = {
    "account_id",
    "account_label",
    "code",
    "name",
    "side",
    "side_label",
    "reason",
    "score",
    "exposure_group",
    "theme",
    "industry",
    "index_key",
    "country",
    "sector",
}
_ORDER_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "target_value",
    "target_weight",
    "execute_after",
    "trade_date",
    "signal_date",
    "status",
}
_POSITION_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "available_shares",
    "avg_cost",
    "last_price",
    "market_value",
    "unrealized_pnl",
    "last_buy_date",
    "hold_since",
    "updated_at",
}
_TRADE_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "shares",
    "price",
    "gross_amount",
    "commission",
    "stamp_tax",
    "slippage",
    "net_amount",
    "cash_after",
    "trade_date",
}


def _generated_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _context(
    repo_root: str | Path | None,
    market: str,
    agent: str,
) -> tuple[Path, agg.DashboardAgentPaths]:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    if not agg._dashboard_identity_allowed(market, agent, root):
        raise competition.UnknownAgent(f"unknown_agent:{agent}; market={market}")
    return root, agg._resolve_dashboard_paths(market, agent, root)


def _base(market: str, agent: str) -> dict[str, Any]:
    return {"generated_at": _generated_at(), "market": market, "agent": agent}


def _latest_strategy_model_usage(root: Path) -> list[dict[str, Any]]:
    from .research.lineage import ResearchLineageStore
    from .strategy_registry import PAIR_SLOTS

    path = root / "data" / "shared" / "research_lineage.sqlite3"
    decisions = (
        ResearchLineageStore(path).query("decision_runs")
        if path.exists()
        else []
    )
    labels = agg._strategy_labels(root)
    usage: list[dict[str, Any]] = []
    for market in competition.MARKETS:
        for agent in PAIR_SLOTS:
            matching = [
                row
                for row in decisions
                if str(row.get("market") or "") == market
                and str(row.get("agent_id") or "") == agent
            ]
            matching.sort(
                key=lambda row: (
                    str(row.get("as_of") or ""),
                    str(row.get("source_run_id") or ""),
                    str(row.get("decision_run_id") or ""),
                ),
                reverse=True,
            )
            if not matching:
                usage.append(
                    {
                        "market": market,
                        "agent": agent,
                        "strategy_label": labels.get(agent, agent),
                        "as_of": None,
                        "status": "not_recorded",
                        "applied_candidates": 0,
                        "candidate_coverage": 0.0,
                        "model_versions": {},
                        "fallback_reason": "decision_lineage_missing",
                        "accounts": 0,
                    }
                )
                continue
            latest_source = str(matching[0].get("source_run_id") or "")
            latest = [
                row
                for row in matching
                if str(row.get("source_run_id") or "") == latest_source
            ]
            statuses = {
                str(row.get("model_policy_status") or "rule_only")
                for row in latest
            }
            status = (
                "active"
                if "active" in statuses
                else "rule_only"
                if "rule_only" in statuses
                else sorted(statuses)[0]
            )
            versions: dict[str, str] = {}
            for row in latest:
                raw_versions = row.get("model_versions") or {}
                if isinstance(raw_versions, str):
                    try:
                        raw_versions = json.loads(raw_versions)
                    except json.JSONDecodeError:
                        raw_versions = {}
                if not isinstance(raw_versions, dict):
                    continue
                for horizon, version in raw_versions.items():
                    if str(version):
                        versions[str(horizon)] = str(version)
            coverages = [
                value
                for value in (
                    safe_float(row.get("model_candidate_coverage"))
                    for row in latest
                )
                if value is not None
            ]
            fallbacks = sorted(
                {
                    str(row.get("model_fallback_reason") or "")
                    for row in latest
                    if str(row.get("model_fallback_reason") or "")
                }
            )
            usage.append(
                {
                    "market": market,
                    "agent": agent,
                    "strategy_label": labels.get(agent, agent),
                    "as_of": str(matching[0].get("as_of") or "") or None,
                    "status": status,
                    "applied_candidates": sum(
                        int(row.get("model_applied_candidates") or 0)
                        for row in latest
                    ),
                    "candidate_coverage": (
                        sum(coverages) / len(coverages)
                        if coverages
                        else 0.0
                    ),
                    "model_versions": versions,
                    "fallback_reason": "|".join(fallbacks),
                    "accounts": len(latest),
                }
            )
    return usage


def _intelligence_db_path(root: Path) -> Path:
    return root / "data" / "shared" / "intelligence" / "intelligence.sqlite3"


def _intelligence_connection(root: Path) -> sqlite3.Connection | None:
    path = _intelligence_db_path(root)
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _document_market_sql(alias: str = "d") -> str:
    return (
        f"(json_extract({alias}.metadata_json, '$.market') IS NULL "
        f"OR json_extract({alias}.metadata_json, '$.market') IN (?, 'all'))"
    )


def _count_by_status(
    connection: sqlite3.Connection,
    *,
    table: str,
    status_column: str,
    join_sql: str,
    market: str,
) -> dict[str, int]:
    rows = connection.execute(
        f"""
        SELECT {status_column} AS status, COUNT(*) AS count
        FROM {table}
        {join_sql}
        WHERE {_document_market_sql()}
        GROUP BY {status_column}
        ORDER BY {status_column}
        """,
        (market,),
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def _json_object(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _freshness_status(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
        / 3600.0,
    )
    if age_hours <= 36:
        return "fresh"
    if age_hours <= 72:
        return "aging"
    return "stale"


def _public_intelligence_error(value: object) -> str:
    return INTELLIGENCE_ERROR_MESSAGE if str(value or "").strip() else ""


def _semantic_extraction_contract(root: Path) -> dict[str, Any]:
    profile_id = "a-share-announcement-v1"
    try:
        semantic_config = yaml.safe_load(
            (root / "configs" / "intelligence_semantic.yaml").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        semantic_config = None
    if isinstance(semantic_config, dict):
        profile_id = str(
            semantic_config.get("production_extraction_profile")
            or profile_id
        )

    profile: dict[str, Any] = {}
    profile_root = root / "configs" / "intelligence_extraction_profiles"
    for path in sorted(profile_root.glob("*.json")):
        candidate = _read_json_file(path)
        if str(candidate.get("profile_id") or "") == profile_id:
            profile = candidate
            break
    return {
        "profileId": str(
            profile.get("profile_id") or profile_id
        ),
        "promptVersion": profile.get("prompt_version"),
        "schemaVersion": profile.get("schema_version"),
        "taxonomyVersion": profile.get("taxonomy_version"),
        "evidenceContract": profile.get("evidence_contract"),
    }


def _latest_intelligence_report(
    root: Path,
    pattern: str,
) -> tuple[dict[str, Any], str | None]:
    for path in sorted((root / "reports" / "intelligence").glob(pattern), reverse=True):
        payload = _read_json_file(path)
        if payload:
            return payload, path.name
    return {}, None


def _factor_supply_layer(root: Path, market: str) -> dict[str, Any]:
    configuration = _read_json_file(root / "configs" / "intelligence_factors.json")
    report, report_name = _latest_intelligence_report(
        root,
        f"factor_validation_{market}_*.json",
    )
    configured_factors = configuration.get("factors")
    if not isinstance(configured_factors, dict):
        configured_factors = {}
    report_factors = report.get("factors")
    if not isinstance(report_factors, dict):
        report_factors = {}

    lifecycle_counts: dict[str, int] = {}
    factors: list[dict[str, Any]] = []
    for name in sorted(set(configured_factors) | set(report_factors)):
        configured = configured_factors.get(name)
        metrics = report_factors.get(name)
        configured = configured if isinstance(configured, dict) else {}
        metrics = metrics if isinstance(metrics, dict) else {}
        state = str(configured.get("state") or "unconfigured")
        lifecycle_counts[state] = lifecycle_counts.get(state, 0) + 1
        factors.append(
            {
                "name": name,
                "state": state,
                "coverage": metrics.get("coverage"),
                "activationRate": metrics.get("signal_activation_rate"),
                "dailyIcCount": metrics.get("daily_ic_count"),
                "meanRankIc": metrics.get("mean_rank_ic"),
                "icSignStability": metrics.get("ic_sign_stability"),
                "recommendation": metrics.get("recommendation"),
                "gateReasons": list(metrics.get("gate_reasons") or []),
            }
        )

    factor_sets = configuration.get("factor_sets")
    factor_sets = factor_sets if isinstance(factor_sets, dict) else {}
    model_eligible = [
        item["name"]
        for item in factors
        if item["state"] in {"model_iteration", "active"}
    ]
    return {
        "status": str(report.get("status") or "unavailable"),
        "snapshotDate": report.get("snapshot_date"),
        "rows": int(report.get("rows") or 0),
        "reportName": report_name,
        "factorSet": report.get("factor_set")
        or (next(iter(factor_sets), None) if factor_sets else None),
        "factorSets": [
            {
                "name": name,
                "state": (
                    str(value.get("state") or "unknown")
                    if isinstance(value, dict)
                    else "unknown"
                ),
                "features": (
                    list(value.get("features") or [])
                    if isinstance(value, dict)
                    else []
                ),
            }
            for name, value in sorted(factor_sets.items())
        ],
        "factors": factors,
        "lifecycleCounts": lifecycle_counts,
        "suppliedFactors": len(report_factors),
        "modelEligible": bool(model_eligible),
        "modelEligibleFactors": model_eligible,
    }


def _model_impact_layer(
    root: Path,
    market: str,
    factor_supply: dict[str, Any],
) -> dict[str, Any]:
    report, report_name = _latest_intelligence_report(
        root,
        f"model_incremental_effect_{market}_*.json",
    )
    factors = factor_supply.get("factors")
    factors = factors if isinstance(factors, list) else []
    active_factors = [
        str(item.get("name"))
        for item in factors
        if isinstance(item, dict) and item.get("state") == "active"
    ]
    iteration_factors = [
        str(item.get("name"))
        for item in factors
        if isinstance(item, dict) and item.get("state") == "model_iteration"
    ]
    adopted = bool(active_factors)
    if adopted:
        reason = f"{len(active_factors)} 个情报因子已进入正式模型。"
    elif iteration_factors:
        reason = (
            f"{len(iteration_factors)} 个情报因子仅进入模型迭代，"
            "尚未进入正式模型。"
        )
    elif factors:
        reason = "情报因子仍处于观察或研究阶段，当前未进入正式模型。"
    else:
        reason = "尚未发现可供模型采用的情报因子，当前未进入正式模型。"

    raw_horizons = report.get("horizons")
    raw_horizons = raw_horizons if isinstance(raw_horizons, dict) else {}
    horizons = [
        {
            "horizon": str(horizon),
            "status": (
                str(value.get("status") or "unavailable")
                if isinstance(value, dict)
                else "unavailable"
            ),
            "reason": value.get("reason") if isinstance(value, dict) else None,
            "support": (
                dict(value.get("support") or {})
                if isinstance(value, dict)
                else {}
            ),
            "deltas": (
                dict(value.get("deltas") or {})
                if isinstance(value, dict)
                else {}
            ),
            "baseMetrics": (
                dict(value.get("base_metrics") or {})
                if isinstance(value, dict)
                else {}
            ),
            "candidateMetrics": (
                dict(value.get("candidate_metrics") or {})
                if isinstance(value, dict)
                else {}
            ),
        }
        for horizon, value in sorted(raw_horizons.items())
    ]
    return {
        "status": str(report.get("status") or "unavailable"),
        "asOf": report.get("as_of"),
        "snapshotDate": report.get("snapshot_date"),
        "reportName": report_name,
        "factorSet": report.get("factor_set") or factor_supply.get("factorSet"),
        "qualifiedHorizons": int(report.get("qualified_horizons") or 0),
        "activation": str(report.get("activation") or "unchanged"),
        "adopted": adopted,
        "activeFactors": active_factors,
        "iterationFactors": iteration_factors,
        "reason": reason,
        "horizons": horizons,
    }


def _pipeline_sources(
    connection: sqlite3.Connection,
    *,
    market: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT
          d.source,
          COUNT(*) AS documents,
          MAX(d.published_at) AS latest_published_at,
          MAX(d.first_seen_at) AS latest_seen_at
        FROM documents d
        WHERE {_document_market_sql()}
        GROUP BY d.source
        ORDER BY MAX(d.first_seen_at) DESC, d.source
        LIMIT 20
        """,
        (market,),
    ).fetchall()
    sources: list[dict[str, Any]] = []
    for row in rows:
        source = str(row["source"])
        latest_run = connection.execute(
            """
            SELECT status, started_at, finished_at, fetched, inserted, error
            FROM ingestion_runs
            WHERE source=?
            ORDER BY COALESCE(finished_at, started_at) DESC, run_id DESC
            LIMIT 1
            """,
            (source,),
        ).fetchone()
        cursor = connection.execute(
            """
            SELECT cursor, updated_at
            FROM source_cursors
            WHERE source=?
            """,
            (source,),
        ).fetchone()
        freshness_at = (
            (latest_run["finished_at"] or latest_run["started_at"])
            if latest_run is not None
            else row["latest_seen_at"]
        )
        sources.append(
            {
                "source": source,
                "documents": int(row["documents"] or 0),
                "latestPublishedAt": row["latest_published_at"],
                "lastIngestedAt": freshness_at,
                "freshnessStatus": _freshness_status(freshness_at),
                "latestRunStatus": (
                    str(latest_run["status"]) if latest_run is not None else "unknown"
                ),
                "fetched": int(latest_run["fetched"] or 0) if latest_run else 0,
                "inserted": int(latest_run["inserted"] or 0) if latest_run else 0,
                "error": (
                    _public_intelligence_error(latest_run["error"])
                    if latest_run
                    else ""
                ),
                "cursor": str(cursor["cursor"]) if cursor is not None else None,
                "cursorUpdatedAt": (
                    cursor["updated_at"] if cursor is not None else None
                ),
            }
        )
    return sources


def _snapshot_pipeline_sources(
    raw_sources: object,
) -> list[dict[str, Any]]:
    if not isinstance(raw_sources, list):
        return []
    sources: list[dict[str, Any]] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        freshness_at = raw.get("last_ingested_at")
        sources.append(
            {
                "source": str(raw.get("source") or "unknown"),
                "documents": int(raw.get("documents") or 0),
                "latestPublishedAt": raw.get("latest_published_at"),
                "lastIngestedAt": freshness_at,
                "freshnessStatus": _freshness_status(freshness_at),
                "latestRunStatus": str(
                    raw.get("latest_run_status") or "unknown"
                ),
                "fetched": int(raw.get("fetched") or 0),
                "inserted": int(raw.get("inserted") or 0),
                "error": _public_intelligence_error(
                    raw.get("error") or raw.get("error_summary")
                ),
                "cursor": raw.get("cursor"),
                "cursorUpdatedAt": raw.get("cursor_updated_at"),
            }
        )
    return sources


def _snapshot_pipeline_summary(
    root: Path,
) -> dict[str, Any] | None:
    snapshot = _read_json_file(
        root
        / "reports"
        / "intelligence"
        / "semantic_status_latest.json"
    )
    pipeline = snapshot.get("pipeline")
    artifacts = snapshot.get("artifacts")
    semantic = snapshot.get("semantic")
    if not all(
        isinstance(value, dict)
        for value in (pipeline, artifacts, semantic)
    ):
        return None
    stages = pipeline.get("stages")
    backlog = pipeline.get("backlog")
    artifact_counts = artifacts.get("by_status")
    semantic_counts = semantic.get("by_status")
    decision_counts = semantic.get("decisions")
    if not all(
        isinstance(value, dict)
        for value in (
            stages,
            backlog,
            artifact_counts,
            semantic_counts,
            decision_counts,
        )
    ):
        return None
    return {
        "documents": int(stages.get("catalogued") or 0),
        "stages": {
            "catalogued": int(stages.get("catalogued") or 0),
            "pdf_ready": int(stages.get("pdf_ready") or 0),
            "parsed": int(stages.get("parsed") or 0),
            "semantic_completed": int(
                stages.get("semantic_completed") or 0
            ),
        },
        "artifact_counts": {
            str(key): int(value or 0)
            for key, value in artifact_counts.items()
        },
        "semantic_counts": {
            str(key): int(value or 0)
            for key, value in semantic_counts.items()
        },
        "decision_counts": {
            key: int(decision_counts.get(key) or 0)
            for key in (
                "canonical",
                "no_event",
                "quarantined",
                "failed",
            )
        },
        "backlog": {
            key: int(backlog.get(key) or 0)
            for key in ("download", "parse", "semantic", "total")
        },
        "sources": _snapshot_pipeline_sources(
            pipeline.get("sources")
        ),
        "snapshot_generated_at": snapshot.get("generated_at"),
    }


def _live_pipeline_summary(
    connection: sqlite3.Connection,
    *,
    market: str,
) -> dict[str, Any]:
    stage_row = connection.execute(
        f"""
        WITH artifact_flags AS (
          SELECT
            document_id,
            MAX(
              CASE WHEN artifact_type='pdf'
                AND status IN ('downloaded', 'parsed')
              THEN 1 ELSE 0 END
            ) AS pdf_ready,
            MAX(
              CASE WHEN artifact_type='parsed' AND status='parsed'
              THEN 1 ELSE 0 END
            ) AS parsed
          FROM document_artifacts
          GROUP BY document_id
        ),
        semantic_flags AS (
          SELECT
            document_id,
            MAX(
              CASE WHEN status IN ('succeeded', 'no_event')
              THEN 1 ELSE 0 END
            ) AS completed,
            MAX(
              CASE WHEN status IN (
                'succeeded', 'no_event',
                'failed_retryable', 'failed_terminal'
              )
              THEN 1 ELSE 0 END
            ) AS terminal
          FROM semantic_runs
          GROUP BY document_id
        )
        SELECT
          COUNT(*) AS catalogued,
          COALESCE(SUM(COALESCE(a.pdf_ready, 0)), 0) AS pdf_ready,
          COALESCE(SUM(COALESCE(a.parsed, 0)), 0) AS parsed,
          COALESCE(SUM(COALESCE(s.completed, 0)), 0)
            AS semantic_completed,
          COALESCE(SUM(
            CASE WHEN COALESCE(a.pdf_ready, 0)=0 THEN 1 ELSE 0 END
          ), 0) AS download,
          COALESCE(SUM(
            CASE WHEN COALESCE(a.pdf_ready, 0)=1
              AND COALESCE(a.parsed, 0)=0
            THEN 1 ELSE 0 END
          ), 0) AS parse,
          COALESCE(SUM(
            CASE WHEN COALESCE(a.parsed, 0)=1
              AND COALESCE(s.terminal, 0)=0
            THEN 1 ELSE 0 END
          ), 0) AS semantic
        FROM documents d
        LEFT JOIN artifact_flags a ON a.document_id=d.id
        LEFT JOIN semantic_flags s ON s.document_id=d.id
        WHERE {_document_market_sql()}
        """,
        (market,),
    ).fetchone()
    artifact_counts = _count_by_status(
        connection,
        table="document_artifacts a",
        status_column="a.status",
        join_sql="JOIN documents d ON d.id=a.document_id",
        market=market,
    )
    semantic_counts = _count_by_status(
        connection,
        table="semantic_runs r",
        status_column="r.status",
        join_sql="JOIN documents d ON d.id=r.document_id",
        market=market,
    )
    decision_row = connection.execute(
        f"""
        SELECT
          (
            SELECT COUNT(*)
            FROM event_candidates c
            JOIN documents d ON d.id=c.document_id
            WHERE c.validation_status='canonical'
              AND {_document_market_sql()}
          ) AS canonical,
          (
            SELECT COUNT(*)
            FROM event_candidates c
            JOIN documents d ON d.id=c.document_id
            WHERE c.validation_status='quarantined'
              AND {_document_market_sql()}
          ) AS quarantined,
          (
            SELECT COUNT(*)
            FROM semantic_runs r
            JOIN documents d ON d.id=r.document_id
            WHERE r.status='no_event'
              AND {_document_market_sql()}
          ) AS no_event,
          (
            SELECT COUNT(*)
            FROM semantic_runs r
            JOIN documents d ON d.id=r.document_id
            WHERE r.status IN ('failed_retryable', 'failed_terminal')
              AND {_document_market_sql()}
          ) AS failed
        """,
        (market, market, market, market),
    ).fetchone()
    backlog = {
        key: int(stage_row[key] or 0)
        for key in ("download", "parse", "semantic")
    }
    backlog["total"] = sum(backlog.values())
    return {
        "documents": int(stage_row["catalogued"] or 0),
        "stages": {
            key: int(stage_row[key] or 0)
            for key in (
                "catalogued",
                "pdf_ready",
                "parsed",
                "semantic_completed",
            )
        },
        "artifact_counts": artifact_counts,
        "semantic_counts": semantic_counts,
        "decision_counts": {
            key: int(decision_row[key] or 0)
            for key in (
                "canonical",
                "no_event",
                "quarantined",
                "failed",
            )
        },
        "backlog": backlog,
        "sources": _pipeline_sources(connection, market=market),
        "snapshot_generated_at": None,
    }


def _artifact_worker_summary_unavailable() -> dict[str, Any]:
    statuses = (
        "leased",
        "importing",
        "imported",
        "partial",
        "failed",
        "expired",
    )
    stages = {
        stage: {status: 0 for status in statuses}
        for stage in ("download", "parse")
    }
    return {
        "status": "unavailable",
        "activeLeases": 0,
        "leasedDocuments": 0,
        "completedDocuments": 0,
        "downloadedDocuments": 0,
        "parsedDocuments": 0,
        "latestFinishedAt": None,
        "stages": stages,
    }


def _artifact_worker_summary(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    empty = _artifact_worker_summary_unavailable()
    stages = empty["stages"]
    table_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table'
              AND name IN (
                'artifact_worker_jobs',
                'artifact_worker_items'
              )
            """
        ).fetchone()[0]
    )
    if table_count != 2:
        return empty
    rows = connection.execute(
        """
        SELECT stage, status, COUNT(*) AS count
        FROM artifact_worker_jobs
        GROUP BY stage, status
        """
    ).fetchall()
    for row in rows:
        stage = str(row["stage"])
        status = str(row["status"])
        if stage in stages and status in stages[stage]:
            stages[stage][status] = int(row["count"] or 0)
    now = datetime.now(timezone.utc).isoformat()
    active = connection.execute(
        """
        SELECT
          COUNT(DISTINCT j.job_id) AS active_leases,
          COUNT(i.document_id) AS leased_documents
        FROM artifact_worker_jobs j
        LEFT JOIN artifact_worker_items i ON i.job_id=j.job_id
        WHERE j.status IN ('leased', 'importing') AND j.lease_until>?
        """,
        (now,),
    ).fetchone()
    completed = connection.execute(
        """
        SELECT
          COUNT(
            DISTINCT CASE WHEN j.stage='download' THEN i.document_id END
          ) AS downloaded_documents,
          COUNT(
            DISTINCT CASE WHEN j.stage='parse' THEN i.document_id END
          ) AS parsed_documents,
          MAX(j.finished_at) AS latest_finished_at
        FROM artifact_worker_items i
        JOIN artifact_worker_jobs j ON j.job_id=i.job_id
        WHERE i.status IN ('succeeded', 'reused')
          AND j.status IN ('imported', 'partial')
        """
    ).fetchone()
    parsed_documents = int(completed["parsed_documents"] or 0)
    return {
        "status": "available",
        "activeLeases": int(active["active_leases"] or 0),
        "leasedDocuments": int(active["leased_documents"] or 0),
        "completedDocuments": parsed_documents,
        "downloadedDocuments": int(
            completed["downloaded_documents"] or 0
        ),
        "parsedDocuments": parsed_documents,
        "latestFinishedAt": completed["latest_finished_at"],
        "stages": stages,
    }


def _latest_semantic_batch(
    connection: sqlite3.Connection,
    *,
    market: str,
) -> dict[str, Any] | None:
    latest = connection.execute(
        f"""
        SELECT r.*
        FROM semantic_runs r
        JOIN documents d ON d.id=r.document_id
        WHERE {_document_market_sql()}
        ORDER BY COALESCE(r.finished_at, r.started_at) DESC, r.run_id DESC
        LIMIT 1
        """,
        (market,),
    ).fetchone()
    if latest is None:
        return None
    batch_date = str(latest["finished_at"] or latest["started_at"])[:10]
    identity = (
        str(latest["provider"]),
        str(latest["model"]),
        str(latest["prompt_version"]),
        str(latest["schema_version"]),
        str(latest["taxonomy_version"]),
        str(latest["parser_version"]),
    )
    params = (*identity, batch_date, market)
    aggregate = connection.execute(
        f"""
        SELECT
          COUNT(*) AS runs,
          SUM(CASE WHEN r.status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
          SUM(CASE WHEN r.status='no_event' THEN 1 ELSE 0 END) AS no_event,
          SUM(
            CASE WHEN r.status IN ('failed_retryable', 'failed_terminal')
            THEN 1 ELSE 0 END
          ) AS failed,
          SUM(CASE WHEN r.status='budget_deferred' THEN 1 ELSE 0 END) AS deferred,
          COALESCE(SUM(r.input_tokens), 0) AS input_tokens,
          COALESCE(SUM(r.output_tokens), 0) AS output_tokens,
          COALESCE(SUM(r.cost_microunits), 0) AS cost_microunits,
          MIN(r.started_at) AS started_at,
          MAX(r.finished_at) AS finished_at
        FROM semantic_runs r
        JOIN documents d ON d.id=r.document_id
        WHERE r.provider=? AND r.model=? AND r.prompt_version=?
          AND r.schema_version=? AND r.taxonomy_version=?
          AND r.parser_version=?
          AND substr(COALESCE(r.finished_at, r.started_at), 1, 10)=?
          AND {_document_market_sql()}
        """,
        params,
    ).fetchone()
    quarantined = connection.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM event_candidates c
        JOIN semantic_runs r ON r.run_id=c.run_id
        JOIN documents d ON d.id=r.document_id
        WHERE c.validation_status='quarantined'
          AND r.provider=? AND r.model=? AND r.prompt_version=?
          AND r.schema_version=? AND r.taxonomy_version=?
          AND r.parser_version=?
          AND substr(COALESCE(r.finished_at, r.started_at), 1, 10)=?
          AND {_document_market_sql()}
        """,
        params,
    ).fetchone()
    runs = int(aggregate["runs"] or 0)
    completed = int(aggregate["succeeded"] or 0) + int(aggregate["no_event"] or 0)
    return {
        "batchKey": ":".join((*identity, batch_date)),
        "profileId": "a-share-announcement-v1",
        "provider": identity[0],
        "model": identity[1],
        "promptVersion": identity[2],
        "schemaVersion": identity[3],
        "taxonomyVersion": identity[4],
        "parserVersion": identity[5],
        "batchDate": batch_date,
        "startedAt": aggregate["started_at"],
        "finishedAt": aggregate["finished_at"],
        "runs": runs,
        "succeeded": int(aggregate["succeeded"] or 0),
        "noEvent": int(aggregate["no_event"] or 0),
        "quarantined": int(quarantined["count"] or 0),
        "failed": int(aggregate["failed"] or 0),
        "deferred": int(aggregate["deferred"] or 0),
        "inputTokens": int(aggregate["input_tokens"] or 0),
        "outputTokens": int(aggregate["output_tokens"] or 0),
        "costMicrounits": int(aggregate["cost_microunits"] or 0),
        "successRate": completed / runs if runs else None,
        "remaining": 0,
        "requestCount": runs,
        "validationRepairs": 0,
        "validationRepairFailures": 0,
        "qualityStatus": (
            "degraded"
            if int(aggregate["failed"] or 0) and not completed
            else "partial"
            if int(aggregate["failed"] or 0)
            else "healthy"
        ),
    }


def _latest_semantic_daily_batch(
    root: Path,
    *,
    market: str,
) -> dict[str, Any] | None:
    if market != "a_share":
        return None
    reports = sorted(
        (root / "reports" / "intelligence").glob("semantic_daily_*.json"),
        reverse=True,
    )
    for path in reports:
        report = _read_json_file(path)
        run = report.get("run")
        if not isinstance(run, dict) or not str(run.get("job_id") or "").strip():
            continue
        job_id = str(run["job_id"])
        manifest = _read_json_file(
            root
            / "data"
            / "shared"
            / "intelligence"
            / "extraction_jobs"
            / job_id
            / "job.json"
        )
        imported = report.get("import")
        imported = imported if isinstance(imported, dict) else {}
        executor = run.get("executor")
        executor = executor if isinstance(executor, dict) else {}
        usage = run.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        items = manifest.get("items")
        items = items if isinstance(items, list) else []
        first_item = items[0] if items and isinstance(items[0], dict) else {}
        expected = max(
            0,
            int(
                run.get("expected")
                or (report.get("prepared") or {}).get("documents")
                or len(items)
                or 0
            ),
        )
        succeeded = max(0, int(imported.get("valid") or 0))
        no_event = max(0, int(imported.get("no_event") or 0))
        quarantined = max(0, int(imported.get("quarantined") or 0))
        failed = max(0, int(run.get("failed") or 0))
        finished_at = run.get("finished_at") or report.get("generated_at")
        batch_date = str(finished_at or path.stem.removeprefix("semantic_daily_"))[
            :10
        ]
        completed = succeeded + no_event
        return {
            "batchKey": job_id,
            "profileId": str(
                manifest.get("profile_id")
                or report.get("profile_id")
                or "a-share-announcement-v1"
            ),
            "provider": str(executor.get("provider") or "unknown"),
            "model": str(executor.get("model") or "unknown"),
            "promptVersion": str(manifest.get("prompt_version") or "unknown"),
            "schemaVersion": str(manifest.get("schema_version") or "unknown"),
            "taxonomyVersion": str(
                manifest.get("taxonomy_version") or "unknown"
            ),
            "parserVersion": str(
                first_item.get("parser_version") or "unknown"
            ),
            "batchDate": batch_date,
            "startedAt": run.get("started_at"),
            "finishedAt": finished_at,
            "runs": expected,
            "succeeded": succeeded,
            "noEvent": no_event,
            "quarantined": quarantined,
            "failed": failed,
            "deferred": 0,
            "remaining": max(
                0,
                expected
                - succeeded
                - no_event
                - quarantined
                - failed,
            ),
            "inputTokens": max(0, int(usage.get("input_tokens") or 0)),
            "outputTokens": max(0, int(usage.get("output_tokens") or 0)),
            "costMicrounits": 0,
            "requestCount": max(0, int(usage.get("request_count") or 0)),
            "validationRepairs": max(
                0,
                int(run.get("validation_repairs") or 0),
            ),
            "validationRepairFailures": max(
                0,
                int(run.get("validation_repair_failures") or 0),
            ),
            "successRate": completed / expected if expected else None,
            "qualityStatus": str(
                report.get("quality_status")
                or (
                    "degraded"
                    if int(run.get("failed") or 0) and not completed
                    else "partial"
                    if int(run.get("failed") or 0)
                    else "healthy"
                )
            ),
        }
    return None


def _intelligence_rows(
    connection: sqlite3.Connection,
    *,
    market: str,
    limit: int,
    document_id: int | None = None,
) -> list[dict[str, Any]]:
    document_filter = " AND d.id=?" if document_id is not None else ""
    params: list[object] = []
    for _ in range(4):
        params.append(market)
        if document_id is not None:
            params.append(document_id)
    params.append(max(1, min(DEFAULT_INTELLIGENCE_ROW_LIMIT, int(limit))))
    rows = connection.execute(
        f"""
        SELECT *
        FROM (
          SELECT
            decisions.*,
            ROW_NUMBER() OVER (
              PARTITION BY decisions.decision
              ORDER BY decisions.effective_at DESC, decisions.decision_id
            ) AS _row_rank
          FROM (
          SELECT
            COALESCE(c.canonical_event_id, c.candidate_id) AS decision_id,
            'canonical' AS decision,
            d.id AS document_id,
            c.event_type,
            c.lifecycle,
            COALESCE(x.entity_name, l.name, '') AS issuer_name,
            COALESCE(x.entity_id, l.ts_code, '') AS issuer_code,
            COALESCE(
              (
                SELECT CASE
                  WHEN json_extract(subject.value, '$.entity_id')
                    LIKE 'external:%'
                  THEN substr(
                    json_extract(subject.value, '$.entity_id'),
                    10
                  )
                  ELSE json_extract(subject.value, '$.entity_id')
                END
                FROM json_each(
                  c.payload_json,
                  '$.event.subjects'
                ) AS subject
                WHERE COALESCE(
                  json_extract(subject.value, '$.role'),
                  ''
                )<>'issuer'
                ORDER BY CAST(subject.key AS INTEGER)
                LIMIT 1
              ),
              ''
            ) AS event_subject,
            d.title,
            COALESCE(e.effective_at, d.effective_at) AS effective_at,
            COALESCE(s.direction, e.direction) AS direction,
            s.materiality,
            s.relevance,
            COALESCE(s.novelty, e.novelty) AS novelty,
            COALESCE(s.confidence, e.confidence) AS confidence,
            NULL AS reason
          FROM event_candidates c
          JOIN documents d ON d.id=c.document_id
          LEFT JOIN events e ON e.event_id=c.canonical_event_id
          LEFT JOIN event_scores s ON s.event_id=e.event_id
          LEFT JOIN event_entities x
            ON x.rowid=(
              SELECT MIN(x1.rowid) FROM event_entities x1
              WHERE x1.event_id=e.event_id
            )
          LEFT JOIN document_security_links l
            ON l.rowid=(
              SELECT MIN(l1.rowid) FROM document_security_links l1
              WHERE l1.document_id=d.id
            )
          WHERE c.validation_status='canonical'
            AND {_document_market_sql()}
            {document_filter}

          UNION ALL

          SELECT
            c.candidate_id AS decision_id,
            'quarantined' AS decision,
            d.id AS document_id,
            c.event_type,
            c.lifecycle,
            COALESCE(l.name, '') AS issuer_name,
            COALESCE(l.ts_code, '') AS issuer_code,
            COALESCE(
              (
                SELECT CASE
                  WHEN json_extract(subject.value, '$.entity_id')
                    LIKE 'external:%'
                  THEN substr(
                    json_extract(subject.value, '$.entity_id'),
                    10
                  )
                  ELSE json_extract(subject.value, '$.entity_id')
                END
                FROM json_each(
                  c.payload_json,
                  '$.event.subjects'
                ) AS subject
                WHERE COALESCE(
                  json_extract(subject.value, '$.role'),
                  ''
                )<>'issuer'
                ORDER BY CAST(subject.key AS INTEGER)
                LIMIT 1
              ),
              ''
            ) AS event_subject,
            d.title,
            d.effective_at,
            json_extract(c.payload_json, '$.direction') AS direction,
            json_extract(c.payload_json, '$.materiality') AS materiality,
            json_extract(c.payload_json, '$.relevance') AS relevance,
            json_extract(c.payload_json, '$.novelty') AS novelty,
            json_extract(c.payload_json, '$.confidence') AS confidence,
            COALESCE(
              json_extract(c.validation_errors_json, '$[0]'),
              'validation_failed'
            ) AS reason
          FROM event_candidates c
          JOIN documents d ON d.id=c.document_id
          LEFT JOIN document_security_links l
            ON l.rowid=(
              SELECT MIN(l1.rowid) FROM document_security_links l1
              WHERE l1.document_id=d.id
            )
          WHERE c.validation_status='quarantined'
            AND {_document_market_sql()}
            {document_filter}

          UNION ALL

          SELECT
            r.run_id AS decision_id,
            'no_event' AS decision,
            d.id AS document_id,
            NULL AS event_type,
            NULL AS lifecycle,
            COALESCE(l.name, '') AS issuer_name,
            COALESCE(l.ts_code, '') AS issuer_code,
            '' AS event_subject,
            d.title,
            d.effective_at,
            NULL AS direction,
            NULL AS materiality,
            NULL AS relevance,
            NULL AS novelty,
            NULL AS confidence,
            COALESCE(
              json_extract(d.metadata_json, '$.no_event_reason'),
              'no_material_event'
            ) AS reason
          FROM semantic_runs r
          JOIN documents d ON d.id=r.document_id
          LEFT JOIN document_security_links l
            ON l.rowid=(
              SELECT MIN(l1.rowid) FROM document_security_links l1
              WHERE l1.document_id=d.id
            )
          WHERE r.status='no_event'
            AND {_document_market_sql()}
            {document_filter}

          UNION ALL

          SELECT
            r.run_id AS decision_id,
            'failed' AS decision,
            d.id AS document_id,
            NULL AS event_type,
            NULL AS lifecycle,
            COALESCE(l.name, '') AS issuer_name,
            COALESCE(l.ts_code, '') AS issuer_code,
            '' AS event_subject,
            d.title,
            d.effective_at,
            NULL AS direction,
            NULL AS materiality,
            NULL AS relevance,
            NULL AS novelty,
            NULL AS confidence,
            COALESCE(NULLIF(r.error, ''), r.status) AS reason
          FROM semantic_runs r
          JOIN documents d ON d.id=r.document_id
          LEFT JOIN document_security_links l
            ON l.rowid=(
              SELECT MIN(l1.rowid) FROM document_security_links l1
              WHERE l1.document_id=d.id
            )
          WHERE r.status IN ('failed_retryable', 'failed_terminal')
            AND {_document_market_sql()}
            {document_filter}
          ) AS decisions
        )
        WHERE _row_rank <= ?
        ORDER BY effective_at DESC, decision_id
        """,
        tuple(params),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("_row_rank", None)
        result.append(item)
    return result


def _project_rows(
    rows: list[dict[str, Any]],
    fields: set[str],
    *,
    order: bool = False,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in rows:
        row = {key: value for key, value in raw.items() if key in fields}
        if order and row.get("shares") is None and raw.get("delta_shares") is not None:
            row["shares"] = raw["delta_shares"]
        projected.append(row)
    return projected


def build_dashboard_overview_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return identity, strategy configuration, and the latest account NAV."""

    root, paths = _context(repo_root, market, agent)
    strategy = agg._dashboard_strategy_profile(
        paths,
        root=root,
        market=market,
        agent=agent,
    )
    latest_nav = agg._read_nav_detail(paths.data_dir, market).get("latest")
    payload = {
        **_base(market, agent),
        "market_label": agg.MARKET_LABELS.get(market, market),
        "currency": agg.MARKET_CURRENCY.get(market, ""),
        "strategy": strategy,
        "latest_nav": latest_nav,
    }
    if agent == "model_shadow":
        model_iteration = agg._read_model_iteration_status(root, market)
        payload["model_iteration"] = model_iteration
        payload["model_shadow"] = model_iteration
    return agg._json_safe(payload)


_SYSTEM_ITERATION_FIELDS = (
    "status",
    "as_of",
    "display_version",
    "candidate",
    "champion",
)

_SYSTEM_MODEL_VERSION_FIELDS = (
    "market",
    "horizon",
    "model_version",
    "display_version",
    "status",
    "status_label",
    "champion_model_version",
    "shadow_cycles",
    "shadow_cycles_remaining",
    "registered_at",
    "artifact",
    "account_scope",
    "selected_at",
    "outcome",
    "ended_at",
    "candidate_kind",
    "admission_grade",
    "source_campaign",
    "source_trial_id",
    "promotion_policy",
)


def _system_iteration_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Keep the global overview bounded to its public summary contract."""

    result = {
        key: status[key]
        for key in _SYSTEM_ITERATION_FIELDS
        if key in status
    }
    for key in ("candidate", "champion"):
        value = result.get(key)
        if not isinstance(value, dict):
            continue
        result[key] = {
            field: value[field]
            for field in _SYSTEM_MODEL_VERSION_FIELDS
            if field in value
        }
    return result


def _latest_baseline_first_as_of(root: Path, market: str) -> str | None:
    report_root = root / "reports" / "research"
    paths = sorted(
        report_root.glob("baseline_first_*.json"),
        key=lambda path: path.name,
        reverse=True,
    )[:100]
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("market") != market:
            continue
        as_of = str(payload.get("as_of") or "").strip()
        if len(as_of) == 8 and as_of.isdigit():
            return f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}"
        return as_of or None
    return None


def build_dashboard_system_overview_data(
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return the bounded research-to-strategy decision loop."""

    root = Path(repo_root) if repo_root else Path.cwd()
    errors: list[dict[str, str]] = []
    try:
        summary = agg.build_dashboard_summary_data(
            repo_root=root,
            markets=list(competition.MARKETS),
        )
        markets = summary.get("markets", [])
    except SYSTEM_OVERVIEW_READ_ERRORS:
        markets = []
        errors.append(
            {
                "code": "market_summary_read_unavailable",
                "section": "markets",
                "message": "市场概览暂不可用。",
            }
        )
    models: list[dict[str, Any]] = []
    for market in competition.MARKETS:
        try:
            status = agg._read_model_iteration_status(root, market)
        except SYSTEM_OVERVIEW_READ_ERRORS:
            status = {
                "status": "unavailable",
                "candidate": None,
                "champion": None,
            }
            errors.append(
                {
                    "code": "model_lineage_read_unavailable",
                    "section": "models",
                    "market": market,
                    "message": (
                        f"{agg.MARKET_LABELS.get(market, market)}"
                        "模型采用链暂不可用。"
                    ),
                }
            )
        iteration = _system_iteration_summary(status)
        if not iteration.get("as_of"):
            latest_research_as_of = _latest_baseline_first_as_of(root, market)
            if latest_research_as_of:
                iteration["as_of"] = latest_research_as_of
        models.append(
            {
                "market": market,
                "market_label": agg.MARKET_LABELS.get(market, market),
                "iteration": iteration,
            }
        )
    try:
        intelligence = build_dashboard_intelligence_data(
            repo_root=root,
            market="a_share",
            agent="codex",
            limit=5,
        )
    except SYSTEM_OVERVIEW_READ_ERRORS:
        errors.append(
            {
                "code": "intelligence_read_unavailable",
                "section": "intelligence",
                "message": "情报链路暂不可用。",
            }
        )
        intelligence = {
            "pipeline": {
                "status": "unavailable",
                "documents": 0,
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
                "artifacts": {},
            },
            "extraction": {
                "status": "unavailable",
                "semanticRuns": {},
                "decisions": {},
                "latestBatch": None,
                "contract": {},
            },
            "factorSupply": {
                "status": "unavailable",
                "suppliedFactors": 0,
                "modelEligibleFactors": [],
                "factors": [],
                "factorSets": [],
                "modelEligible": False,
                "lifecycleCounts": {},
                "rows": 0,
            },
            "modelImpact": {
                "status": "unavailable",
                "adopted": False,
                "activeFactors": [],
                "iterationFactors": [],
                "qualifiedHorizons": 0,
                "activation": "unchanged",
                "reason": "情报证据暂不可用。",
                "horizons": [],
            },
            "decisions": {
                "canonical": 0,
                "no_event": 0,
                "quarantined": 0,
                "failed": 0,
            },
            "rowsByDecision": {},
        }
    try:
        strategy_model_usage = _latest_strategy_model_usage(root)
    except SYSTEM_OVERVIEW_READ_ERRORS:
        strategy_model_usage = []
        errors.append(
            {
                "code": "strategy_model_usage_read_unavailable",
                "section": "strategy_model_usage",
                "message": "策略模型采用记录暂不可用。",
            }
        )
    return agg._json_safe(
        {
            "generated_at": _generated_at(),
            "markets": markets,
            "models": models,
            "strategy_model_usage": strategy_model_usage,
            "intelligence": {
                "pipeline": intelligence["pipeline"],
                "extraction": intelligence["extraction"],
                "factorSupply": intelligence["factorSupply"],
                "modelImpact": intelligence["modelImpact"],
                "decisions": intelligence["decisions"],
                "recentEvents": (
                    intelligence.get("rowsByDecision", {})
                    .get("canonical", [])[:5]
                ),
            },
            "errors": errors,
        }
    )


def build_dashboard_intelligence_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = DEFAULT_INTELLIGENCE_ROW_LIMIT,
) -> dict[str, Any]:
    """Return the four bounded layers from corpus intake to model adoption."""

    root, _ = _context(repo_root, market, agent)
    factor_supply = _factor_supply_layer(root, market)
    model_impact = _model_impact_layer(root, market, factor_supply)
    decision_counts = {
        "canonical": 0,
        "no_event": 0,
        "quarantined": 0,
        "failed": 0,
    }
    payload: dict[str, Any] = {
        **_base(market, agent),
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
            "artifactWorkers": _artifact_worker_summary_unavailable(),
        },
        "extraction": {
            "status": "unavailable",
            "semanticRuns": {},
            "decisions": decision_counts,
            "latestBatch": None,
            "contract": _semantic_extraction_contract(root),
        },
        "factorSupply": factor_supply,
        "modelImpact": model_impact,
        "decisions": decision_counts,
        "rowsByDecision": {
            "canonical": [],
            "no_event": [],
            "quarantined": [],
            "failed": [],
        },
    }
    connection = _intelligence_connection(root)
    if connection is None:
        return agg._json_safe(payload)
    try:
        summary = (
            _snapshot_pipeline_summary(root)
            or _live_pipeline_summary(connection, market=market)
        )
        artifact_counts = summary["artifact_counts"]
        semantic_counts = summary["semantic_counts"]
        decision_counts = summary["decision_counts"]
        backlog = summary["backlog"]
        latest_batch = _latest_semantic_daily_batch(
            root,
            market=market,
        ) or _latest_semantic_batch(connection, market=market)
        rows = _intelligence_rows(
            connection,
            market=market,
            limit=min(DEFAULT_INTELLIGENCE_ROW_LIMIT, max(1, int(limit))),
        )
        rows_by_decision = {
            decision: [
                row for row in rows if str(row.get("decision")) == decision
            ]
            for decision in ("canonical", "no_event", "quarantined", "failed")
        }
        payload["pipeline"] = {
            "status": "available",
            "documents": summary["documents"],
            "artifacts": artifact_counts,
            "stages": {
                "catalogued": summary["stages"]["catalogued"],
                "pdfReady": summary["stages"]["pdf_ready"],
                "parsed": summary["stages"]["parsed"],
                "semanticCompleted": (
                    summary["stages"]["semantic_completed"]
                ),
                "canonicalEvents": decision_counts["canonical"],
            },
            "backlog": backlog,
            "sources": summary["sources"],
            "snapshotGeneratedAt": summary["snapshot_generated_at"],
            "artifactWorkers": _artifact_worker_summary(connection),
        }
        payload["extraction"] = {
            "status": "available" if semantic_counts else "empty",
            "semanticRuns": semantic_counts,
            "decisions": decision_counts,
            "latestBatch": latest_batch,
            "contract": payload["extraction"]["contract"],
        }
        payload["decisions"] = decision_counts
        payload["rowsByDecision"] = rows_by_decision
    finally:
        connection.close()
    return agg._json_safe(payload)


def _decision_detail_row(
    connection: sqlite3.Connection,
    *,
    market: str,
    decision_id: str,
) -> tuple[str, sqlite3.Row] | None:
    candidate = connection.execute(
        f"""
        SELECT
          c.*, d.title, d.published_at, d.effective_at,
          d.source, d.source_url, d.metadata_json,
          r.model, r.prompt_version, r.schema_version,
          r.taxonomy_version, r.parser_version, r.status AS run_status,
          e.event_id, e.direction AS event_direction,
          e.confidence AS event_confidence, e.novelty AS event_novelty,
          e.effective_at AS event_effective_at,
          s.relevance, s.novelty AS score_novelty, s.materiality,
          s.direction AS score_direction, s.confidence AS score_confidence,
          s.scoring_version,
          COALESCE(x.entity_name, l.name, '') AS issuer_name,
          COALESCE(x.entity_id, l.ts_code, '') AS issuer_code,
          COALESCE(x.industry, '') AS issuer_industry
        FROM event_candidates c
        JOIN documents d ON d.id=c.document_id
        JOIN semantic_runs r ON r.run_id=c.run_id
        LEFT JOIN events e ON e.event_id=c.canonical_event_id
        LEFT JOIN event_scores s ON s.event_id=e.event_id
        LEFT JOIN event_entities x
          ON x.rowid=(
            SELECT MIN(x1.rowid) FROM event_entities x1
            WHERE x1.event_id=e.event_id
          )
        LEFT JOIN document_security_links l
          ON l.rowid=(
            SELECT MIN(l1.rowid) FROM document_security_links l1
            WHERE l1.document_id=d.id
          )
        WHERE (c.candidate_id=? OR c.canonical_event_id=?)
          AND {_document_market_sql()}
        ORDER BY
          CASE WHEN c.canonical_event_id=? THEN 0 ELSE 1 END,
          c.created_at DESC
        LIMIT 1
        """,
        (decision_id, decision_id, market, decision_id),
    ).fetchone()
    if candidate is not None:
        decision = (
            "canonical"
            if str(candidate["validation_status"]) == "canonical"
            else "quarantined"
        )
        return decision, candidate
    run = connection.execute(
        f"""
        SELECT
          r.*, d.title, d.published_at, d.effective_at,
          d.source, d.source_url, d.metadata_json,
          COALESCE(l.name, '') AS issuer_name,
          COALESCE(l.ts_code, '') AS issuer_code,
          '' AS issuer_industry
        FROM semantic_runs r
        JOIN documents d ON d.id=r.document_id
        LEFT JOIN document_security_links l
          ON l.rowid=(
            SELECT MIN(l1.rowid) FROM document_security_links l1
            WHERE l1.document_id=d.id
          )
        WHERE r.run_id=?
          AND r.status IN ('no_event', 'failed_retryable', 'failed_terminal')
          AND {_document_market_sql()}
        LIMIT 1
        """,
        (decision_id, market),
    ).fetchone()
    if run is None:
        return None
    return ("no_event" if str(run["status"]) == "no_event" else "failed"), run


def build_dashboard_intelligence_event_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    event_id: str,
) -> dict[str, Any]:
    """Return one semantic decision with bounded evidence and fact records."""

    root, _ = _context(repo_root, market, agent)
    decision_id = str(event_id).strip()
    if not decision_id or len(decision_id) > 200:
        raise InvalidDashboardQuery("event_id is required and must be at most 200 characters")
    connection = _intelligence_connection(root)
    if connection is None:
        raise DashboardResourceNotFound(f"intelligence_event_not_found:{decision_id}")
    try:
        resolved = _decision_detail_row(
            connection,
            market=market,
            decision_id=decision_id,
        )
        if resolved is None:
            raise DashboardResourceNotFound(
                f"intelligence_event_not_found:{decision_id}"
            )
        decision, row = resolved
        payload_data = _json_object(
            row["payload_json"] if "payload_json" in row.keys() else None
        )
        errors = _json_list(
            row["validation_errors_json"]
            if "validation_errors_json" in row.keys()
            else None
        )
        canonical_event_id = (
            str(row["canonical_event_id"] or "")
            if "canonical_event_id" in row.keys()
            else ""
        )
        candidate_id = (
            str(row["candidate_id"] or "")
            if "candidate_id" in row.keys()
            else ""
        )
        evidence = []
        if candidate_id:
            evidence = [
                dict(item)
                for item in connection.execute(
                    """
                    SELECT evidence_id, chunk_id, page_number,
                           start_char, end_char, quote
                    FROM event_evidence
                    WHERE candidate_id=?
                    ORDER BY page_number, evidence_id
                    LIMIT ?
                    """,
                    (candidate_id, MAX_INTELLIGENCE_EVIDENCE),
                ).fetchall()
            ]
        facts = []
        if canonical_event_id:
            facts = [
                {
                    **dict(item),
                    "evidence_ids": _json_list(item["evidence_ids_json"]),
                }
                for item in connection.execute(
                    """
                    SELECT fact_name, ordinal, raw_value, numeric_value,
                           text_value, unit, currency, period,
                           evidence_ids_json, provenance
                    FROM event_facts
                    WHERE event_id=?
                    ORDER BY fact_name, ordinal
                    LIMIT ?
                    """,
                    (canonical_event_id, MAX_INTELLIGENCE_FACTS),
                ).fetchall()
            ]
            for item in facts:
                item.pop("evidence_ids_json", None)
        run_status = str(
            row["run_status"]
            if "run_status" in row.keys()
            else row["status"]
        )
        reason = None
        if decision == "quarantined":
            reason = str(errors[0]) if errors else "validation_failed"
        elif decision == "no_event":
            reason = str(
                _json_object(row["metadata_json"]).get("no_event_reason")
                or "no_material_event"
            )
        elif decision == "failed":
            reason = str(row["error"] or run_status)
        scores = {
            "direction": (
                row["score_direction"]
                if "score_direction" in row.keys()
                else payload_data.get("direction")
            ),
            "materiality": (
                row["materiality"]
                if "materiality" in row.keys()
                else payload_data.get("materiality")
            ),
            "relevance": (
                row["relevance"]
                if "relevance" in row.keys()
                else payload_data.get("relevance")
            ),
            "novelty": (
                row["score_novelty"]
                if "score_novelty" in row.keys()
                else payload_data.get("novelty")
            ),
            "confidence": (
                row["score_confidence"]
                if "score_confidence" in row.keys()
                else payload_data.get("confidence")
            ),
        }
        result = {
            **_base(market, agent),
            "decision": decision,
            "reason": reason,
            "event": {
                "event_id": canonical_event_id or decision_id,
                "event_type": (
                    row["event_type"] if "event_type" in row.keys() else None
                ),
                "lifecycle": (
                    row["lifecycle"] if "lifecycle" in row.keys() else None
                ),
                "effective_at": (
                    row["event_effective_at"]
                    if "event_effective_at" in row.keys()
                    and row["event_effective_at"]
                    else row["effective_at"]
                ),
            },
            "issuer": {
                "name": str(row["issuer_name"] or ""),
                "code": str(row["issuer_code"] or ""),
                "industry": str(row["issuer_industry"] or ""),
            },
            "scores": scores,
            "versions": {
                "model": str(row["model"]),
                "prompt_version": str(row["prompt_version"]),
                "schema_version": str(row["schema_version"]),
                "taxonomy_version": str(row["taxonomy_version"]),
                "parser_version": str(row["parser_version"]),
                "scoring_version": (
                    str(row["scoring_version"])
                    if "scoring_version" in row.keys()
                    and row["scoring_version"] is not None
                    else None
                ),
            },
            "evidence": evidence,
            "facts": facts,
            "document": {
                "document_id": int(row["document_id"]),
                "title": str(row["title"]),
                "source": str(row["source"]),
                "source_url": str(row["source_url"]),
                "published_at": str(row["published_at"]),
            },
        }
        return agg._json_safe(result)
    finally:
        connection.close()


def build_dashboard_intelligence_document_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    document_id: str | int,
) -> dict[str, Any]:
    """Return one document's public metadata and bounded processing lineage."""

    root, _ = _context(repo_root, market, agent)
    try:
        normalized_id = int(str(document_id).strip())
    except (TypeError, ValueError) as exc:
        raise InvalidDashboardQuery("document_id must be an integer") from exc
    if normalized_id < 1:
        raise InvalidDashboardQuery("document_id must be positive")
    connection = _intelligence_connection(root)
    if connection is None:
        raise DashboardResourceNotFound(
            f"intelligence_document_not_found:{normalized_id}"
        )
    try:
        document = connection.execute(
            f"""
            SELECT d.id, d.source, d.source_id, d.title,
                   d.published_at, d.first_seen_at, d.effective_at,
                   d.revised_at, d.revision_of, d.source_url,
                   d.mime_type, d.content_hash, d.status
            FROM documents d
            WHERE d.id=? AND {_document_market_sql()}
            """,
            (normalized_id, market),
        ).fetchone()
        if document is None:
            raise DashboardResourceNotFound(
                f"intelligence_document_not_found:{normalized_id}"
            )
        artifacts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT artifact_id, artifact_type, content_hash, mime_type,
                       byte_size, parser_version, status, error,
                       created_at, updated_at
                FROM document_artifacts
                WHERE document_id=?
                ORDER BY updated_at DESC, artifact_id
                LIMIT 50
                """,
                (normalized_id,),
            ).fetchall()
        ]
        links = [
            dict(row)
            for row in connection.execute(
                """
                SELECT ts_code, name, provenance
                FROM document_security_links
                WHERE document_id=?
                ORDER BY ts_code
                LIMIT 50
                """,
                (normalized_id,),
            ).fetchall()
        ]
        decisions = _intelligence_rows(
            connection,
            market=market,
            document_id=normalized_id,
            limit=50,
        )
        public_document = dict(document)
        public_document["document_id"] = public_document.pop("id")
        return agg._json_safe(
            {
                **_base(market, agent),
                "document": public_document,
                "security_links": links,
                "artifacts": artifacts,
                "decisions": decisions,
            }
        )
    finally:
        connection.close()


def build_dashboard_performance_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return NAV and benchmark history for charting."""

    _, paths = _context(repo_root, market, agent)
    return agg._json_safe(
        {**_base(market, agent), "nav": agg._read_nav_detail(paths.data_dir, market)}
    )


def _read_portfolio_exposure(
    root: Path,
    paths: agg.DashboardAgentPaths,
    market: str,
    *,
    name_lookup: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    names = name_lookup if name_lookup is not None else agg._read_fund_name_lookup(root, market)
    orders = enrich_rows(
        market,
        agg._flatten_pending_orders(paths.data_dir, name_lookup=names),
        repo_root=root,
        name_lookup=names,
    )
    positions = enrich_rows(
        market,
        agg._limited_csv_rows(
            paths.data_dir / "positions.csv",
            source="positions",
            required_columns=["account_id", "code", "shares"],
            text_columns=[
                "account_id",
                "code",
                "name",
                "industry",
                "last_buy_date",
                "hold_since",
                "reason",
                "updated_at",
            ],
            numeric_columns=[
                "shares",
                "available_shares",
                "avg_cost",
                "last_price",
                "market_value",
                "unrealized_pnl",
                "score",
            ],
            limit=0,
            sort_by=["account_id", "code"],
        ),
        repo_root=root,
        name_lookup=names,
    )
    return (
        _project_rows(orders, _ORDER_ROW_FIELDS, order=True),
        _project_rows(positions, _POSITION_ROW_FIELDS),
    )


def build_dashboard_portfolio_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = DEFAULT_ROW_LIMIT,
) -> dict[str, Any]:
    """Return current holdings, pending orders, trades, and their timeline."""

    root, paths = _context(repo_root, market, agent)
    names = agg._read_fund_name_lookup(root, market)
    orders_all, positions_all = _read_portfolio_exposure(
        root,
        paths,
        market,
        name_lookup=names,
    )
    trades_all = enrich_rows(
        market,
        agg._limited_csv_rows(
            paths.data_dir / "trades.csv",
            source="trades",
            required_columns=["trade_date", "account_id", "code", "side"],
            text_columns=["trade_date", "account_id", "code", "name", "side", "reason"],
            numeric_columns=[
                "shares",
                "price",
                "gross_amount",
                "commission",
                "stamp_tax",
                "slippage",
                "net_amount",
                "cash_after",
            ],
            limit=0,
            sort_by=["trade_date"],
        ),
        repo_root=root,
        name_lookup=names,
    )
    trades_all = _project_rows(trades_all, _TRADE_ROW_FIELDS)
    activity_all = build_activity(trades_all, orders_all)
    positions = positions_all[-limit:] if limit > 0 else positions_all
    trades = trades_all[-limit:] if limit > 0 else trades_all
    orders = orders_all[:limit] if limit > 0 else orders_all
    activity = activity_all[:limit] if limit > 0 else activity_all
    position_value = sum(
        safe_float(row.get("market_value")) or 0.0 for row in positions_all
    )
    return agg._json_safe(
        {
            **_base(market, agent),
            "activity": {"summary": {"total": len(activity_all)}, "rows": activity},
            "orders": {
                "summary": {
                    "total": len(orders_all),
                    "buy": sum(1 for row in orders_all if row.get("side") == "buy"),
                    "sell": sum(1 for row in orders_all if row.get("side") == "sell"),
                },
                "rows": orders,
            },
            "positions": {
                "summary": {
                    "total": len(positions_all),
                    "market_value": position_value,
                    "market_value_display": agg._format_market_money(position_value, market),
                },
                "rows": positions,
            },
            "trades": {"summary": {"total": len(trades_all)}, "rows": trades},
        }
    )


def build_dashboard_predictions_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit_per_horizon: int | None = DEFAULT_PREDICTION_LIMIT_PER_HORIZON,
) -> dict[str, Any]:
    """Return bounded prediction rows and the evidence needed to interpret them."""

    root, _ = _context(repo_root, market, agent)
    prediction_agent = agg._dashboard_prediction_agent(root, market, agent)
    bounded_limit = None
    if limit_per_horizon is not None:
        bounded_limit = min(
            MAX_PREDICTION_LIMIT_PER_HORIZON,
            max(1, int(limit_per_horizon)),
        )
    summary = agg._read_prediction_summary(
        root,
        market,
        prediction_agent,
        limit_per_horizon=bounded_limit,
        directory=agg._dashboard_prediction_directory(root, market, agent),
    )
    model_health = agg._read_model_health(root, market)
    model_health["accuracy"] = agg._read_prediction_accuracy(
        root,
        market,
        prediction_agent,
    )
    model_health["prediction_diagnostics"] = summary.get("diagnostics") or {
        "invalidated": 0,
        "mean_out_of_distribution_ratio": 0.0,
        "max_out_of_distribution_ratio": 0.0,
        "max_psi": 0.0,
    }
    return agg._json_safe(
        {
            **_base(market, agent),
            "prediction_summary": summary,
            "alerts": agg._prediction_alerts(summary),
            "regimes": agg._read_regime_summary(root, market),
            "model_health": model_health,
            "source_health": agg._read_research_source_health(root, market),
        }
    )


def _lookthrough(
    market: str,
    positions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    if market != "cn_qdii_etf":
        return {}
    source = "positions" if positions else "planned_orders"
    rows = positions or [row for row in orders if str(row.get("side") or "").lower() == "buy"]
    return build_portfolio_lookthrough(rows, source=source)


def build_dashboard_research_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return optional ETF selection, look-through, and research artifacts."""

    root, paths = _context(repo_root, market, agent)
    if market != "cn_qdii_etf":
        return agg._json_safe(
            {
                **_base(market, agent),
                "selection": {},
                "lookthrough": {},
                "research": {},
            }
        )
    orders, positions = _read_portfolio_exposure(root, paths, market)
    return agg._json_safe(
        {
            **_base(market, agent),
            "selection": agg._read_selection_snapshot(paths.data_dir),
            "lookthrough": _lookthrough(market, positions, orders),
            "research": agg._read_qdii_research(
                root,
                agg._dashboard_prediction_agent(root, market, agent),
            ),
        }
    )


def build_dashboard_operations_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
    limit: int = DEFAULT_ROW_LIMIT,
) -> dict[str, Any]:
    """Return bounded execution history and weekly-report metadata."""

    _, paths = _context(repo_root, market, agent)
    rows_all = agg._limited_csv_rows(
        paths.data_dir / "runs.csv",
        source="runs",
        required_columns=["run_id", "command", "started_at", "status"],
        text_columns=[
            "run_id",
            "command",
            "as_of",
            "started_at",
            "finished_at",
            "status",
            "error_summary",
            "config_hash",
            "code_version",
        ],
        numeric_columns=["duration_ms"],
        limit=0,
        sort_by=["started_at"],
    )
    rows_all = agg._collapse_run_transitions(rows_all)
    rows = rows_all[-limit:] if limit > 0 else rows_all
    report_path = paths.reports_dir / "weekly_report.md"
    markdown = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    return agg._json_safe(
        {
            **_base(market, agent),
            "runs": {"summary": {"total": len(rows_all)}, "rows": list(reversed(rows))},
            "weekly_report": {
                "exists": report_path.exists(),
                "href": agg._weekly_report_href(market, agent, paths.reports_dir),
                "markdown": markdown[:12000],
            },
        }
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_optimizer_diagnostics(
    paths: agg.DashboardAgentPaths,
) -> list[dict[str, Any]]:
    try:
        pending = json.loads(
            (paths.data_dir / "pending_orders.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        pending = []
    rows = pending if isinstance(pending, list) else []
    diagnostics: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        payload = raw.get("optimizer_diagnostics")
        if isinstance(payload, dict) and payload:
            diagnostics.append(
                {
                    "account_id": raw.get("account_id"),
                    "scope": raw.get("scope"),
                    **payload,
                }
            )
    selection = _read_json_file(paths.data_dir / "selection_snapshot.json")
    for scope, block in (selection.get("scopes") or {}).items():
        if not isinstance(block, dict):
            continue
        payload = block.get("optimizer_diagnostics")
        if isinstance(payload, dict) and payload:
            diagnostics.append({"scope": scope, **payload})
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        key = str(item.get("account_id") or item.get("scope") or len(deduplicated))
        deduplicated[key] = item
    return list(deduplicated.values())


def _latest_json(paths: list[Path]) -> dict[str, Any]:
    for path in sorted(paths, reverse=True):
        payload = _read_json_file(path)
        if payload:
            return payload
    return {}


def build_dashboard_governance_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Return bounded decision, risk, lineage, and research governance data."""

    from .research.lineage import ResearchLineageStore
    from .strategy_comparison import build_strategy_comparison
    from .strategy_registry import PAIR_SLOTS, load_strategy_registry

    root, paths = _context(repo_root, market, agent)
    effective_agent = agg._dashboard_prediction_agent(root, market, agent)
    lineage_path = root / "data" / "shared" / "research_lineage.sqlite3"
    lineage_payload: dict[str, Any] = {
        "status": "unavailable",
        "counts": {},
        "decision_runs": [],
        "candidates": [],
        "allocations": [],
        "orders": [],
        "fills": [],
        "attributions": [],
        "experiments": [],
    }
    if lineage_path.exists():
        lineage = ResearchLineageStore(lineage_path)
        decisions = [
            row for row in lineage.query("decision_runs")
            if str(row.get("market") or "") == market
            and str(row.get("agent_id") or "") == effective_agent
        ]
        decisions.sort(
            key=lambda row: (
                str(row.get("as_of") or ""),
                str(row.get("decision_run_id") or ""),
            ),
            reverse=True,
        )
        latest_source_run = (
            str(decisions[0].get("source_run_id") or "") if decisions else ""
        )
        latest_decisions = [
            row for row in decisions
            if str(row.get("source_run_id") or "") == latest_source_run
        ] or decisions[:1]
        projections = [
            lineage.project(decision_run_id=str(row["decision_run_id"]))
            for row in latest_decisions
        ]
        decision_ids = {
            str(row.get("decision_run_id") or "")
            for row in decisions
        }
        attributions = [
            row for row in lineage.query("pnl_attributions")
            if str(row.get("decision_run_id") or "") in decision_ids
        ]
        attributions.sort(
            key=lambda row: (
                str(row.get("as_of") or ""),
                str(row.get("pnl_attribution_id") or ""),
            ),
            reverse=True,
        )
        candidates = [
            row for projection in projections
            for row in projection["candidate_evaluations"]
        ]
        rejection_counts: dict[str, int] = {}
        for row in candidates:
            reason = str(row.get("rejection_reason") or "")
            if reason:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
        experiments = [
            row for row in lineage.query("experiment_trials")
            if str(row.get("market") or "") == market
        ]
        experiments.sort(
            key=lambda row: (
                str(row.get("as_of") or ""),
                str(row.get("trial_id") or ""),
            ),
            reverse=True,
        )
        lineage_payload = {
            "status": "available" if decisions else "empty",
            "database_integrity": lineage.integrity_check(),
            "counts": {
                table: lineage.count(table)
                for table in lineage.TABLES
            },
            "decision_runs": latest_decisions[:10],
            "decision_funnel": {
                "evaluated": len(candidates),
                "eligible": sum(bool(row.get("eligible")) for row in candidates),
                "selected": sum(bool(row.get("selected")) for row in candidates),
                "rejection_counts": rejection_counts,
            },
            "candidates": candidates[:30],
            "allocations": [
                row for projection in projections
                for row in projection["target_allocations"]
            ][:30],
            "orders": [
                row for projection in projections for row in projection["orders"]
            ][:30],
            "fills": [
                row for projection in projections for row in projection["fills"]
            ][:30],
            "attributions": attributions[:30],
            "experiments": experiments[:30],
        }

    drift = _latest_json(
        list(
            (
                root / "data" / "research" / "prediction_health" / market
            ).glob(f"*-{effective_agent}.json")
        )
    )
    factor_evidence = _latest_json(
        list(
            (root / "reports" / "intelligence").glob(
                f"factor_validation_{market}_*.json"
            )
        )
    )
    intelligence_quality = _read_json_file(
        root / "reports" / "intelligence" / "quality_latest.json"
    )
    distinctness: dict[str, Any]
    try:
        comparison = build_strategy_comparison(
            market,
            {
                slot: build_dashboard_comparison_input_data(
                    repo_root=root,
                    market=market,
                    agent=slot,
                )
                for slot in PAIR_SLOTS
            },
            registry=load_strategy_registry(root),
        )
        distinctness = dict((comparison.get("pair") or {}).get("distinctness") or {})
        distinctness.setdefault("status", "unavailable")
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        agg.DashboardDataError,
    ):
        distinctness = {"status": "unavailable"}

    risk = _latest_optimizer_diagnostics(paths)
    actions: list[dict[str, str]] = []
    if lineage_payload["status"] != "available":
        actions.append(
            {
                "severity": "warning",
                "title": "决策链路尚未完整落库",
                "detail": "下一次正式日频决策会自动写入候选、权重、订单和归因。",
            }
        )
    for horizon, assessment in (drift.get("drift") or {}).items():
        status = str((assessment or {}).get("status") or "")
        if status in {"warning", "quarantined", "retired"}:
            actions.append(
                {
                    "severity": "critical" if status in {"quarantined", "retired"} else "warning",
                    "title": f"{horizon}日模型漂移: {status}",
                    "detail": "；".join((assessment or {}).get("breaches") or [])
                    or "已触发模型生命周期检查。",
                }
            )
    fallback_accounts = [
        str(item.get("account_id") or item.get("scope") or "组合")
        for item in risk
        if item.get("fallback_reason")
    ]
    if fallback_accounts:
        actions.append(
            {
                "severity": "warning",
                "title": "组合优化使用了降级路径",
                "detail": "、".join(fallback_accounts),
            }
        )
    unavailable_sources = [
        str(item.get("source") or "")
        for item in intelligence_quality.get("sources", []) or []
        if str(item.get("latest_status") or "") not in {"success", "configured"}
    ]
    if unavailable_sources:
        actions.append(
            {
                "severity": "info",
                "title": "部分外部信息源尚不可用",
                "detail": "、".join(unavailable_sources[:6]),
            }
        )
    return agg._json_safe(
        {
            **_base(market, agent),
            "action_state": {
                "status": (
                    "critical"
                    if any(item["severity"] == "critical" for item in actions)
                    else "warning"
                    if any(item["severity"] == "warning" for item in actions)
                    else "healthy"
                ),
                "items": actions,
            },
            "lineage": lineage_payload,
            "risk": {"status": "available" if risk else "unavailable", "portfolios": risk},
            "attribution": {
                "status": (
                    "available"
                    if lineage_payload.get("attributions")
                    else "unavailable"
                ),
                "rows": lineage_payload.get("attributions") or [],
            },
            "drift": drift.get("drift") or {},
            "experiments": lineage_payload.get("experiments") or [],
            "intelligence_evidence": {
                "factor_validation": factor_evidence,
                "quality": intelligence_quality,
            },
            "distinctness": distinctness,
        }
    )


def build_dashboard_comparison_input_data(
    *,
    repo_root: str | Path | None = None,
    market: str,
    agent: str,
) -> dict[str, Any]:
    """Build only fields consumed by ``build_strategy_comparison``."""

    root, paths = _context(repo_root, market, agent)
    try:
        strategy = build_strategy_profile(paths.config_path, repo_root=root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise agg.DashboardDataError("strategy_overlay") from exc
    nav = agg._read_nav_detail(paths.data_dir, market)
    portfolio = build_dashboard_portfolio_data(
        repo_root=root,
        market=market,
        agent=agent,
        limit=0,
    )
    return {
        "strategy": strategy,
        "nav": nav,
        "positions": portfolio["positions"],
        "orders": portfolio["orders"],
        "trades": portfolio["trades"],
        "lookthrough": _lookthrough(
            market,
            portfolio["positions"]["rows"],
            portfolio["orders"]["rows"],
        ),
    }
