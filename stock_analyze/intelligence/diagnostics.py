"""Coverage and quality reporting for market-intelligence data."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd
import yaml

from .store import IntelligenceStore
from .factors import EVENT_FACTOR_COLUMNS
from .source_registry import load_source_config


SEMANTIC_STATUS_KEYS = (
    "metadata",
    "artifacts",
    "semantic",
    "quality",
    "versions",
    "capacity",
)
SEMANTIC_STATUS_SNAPSHOT_RETENTION = 200


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _load_semantic_config(root: Path) -> Mapping[str, object]:
    path = root / "configs" / "intelligence_semantic.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("intelligence_semantic_config_invalid") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("intelligence_semantic_config_invalid")
    return payload


def _extraction_profile(
    root: Path,
    profile_id: str,
) -> dict[str, str | None]:
    values = {
        "profile": None,
        "prompt": None,
        "schema": None,
        "taxonomy": None,
    }
    profile = None
    profile_root = root / "configs" / "intelligence_extraction_profiles"
    for path in sorted(profile_root.glob("*.json")):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("semantic_extraction_profile_unreadable") from exc
        if not isinstance(candidate, dict):
            raise ValueError("semantic_extraction_profile_invalid")
        if str(candidate.get("profile_id") or "") == profile_id:
            if profile is not None:
                raise ValueError("semantic_extraction_profile_duplicate")
            profile = candidate
    if profile is None:
        return values
    return {
        "profile": str(profile.get("profile_id") or "") or None,
        "prompt": str(profile.get("prompt_version") or "") or None,
        "schema": str(profile.get("schema_version") or "") or None,
        "taxonomy": str(profile.get("taxonomy_version") or "") or None,
    }


def build_semantic_status_report(repo_root: str | Path) -> dict[str, object]:
    """Build one local-only operational snapshot without supplier calls."""

    root = Path(repo_root)
    intelligence_root = root / "data" / "shared" / "intelligence"
    store = IntelligenceStore(intelligence_root)
    config = _load_semantic_config(root)
    parser_config = config.get("parser")
    parser_version = (
        str(parser_config.get("version") or "")
        if isinstance(parser_config, Mapping)
        else ""
    )
    artifact_config = config.get("artifact_store")
    local_root = (
        str(artifact_config.get("local_root") or "")
        if isinstance(artifact_config, Mapping)
        else ""
    )
    local_path = Path(local_root)
    if not local_path.is_absolute():
        local_path = root / local_path
    versions = _extraction_profile(
        root,
        str(
            config.get("production_extraction_profile")
            or "a-share-announcement-mentions-v1"
        ),
    )

    with store.connect() as connection:
        documents = connection.execute(
            """
            SELECT
              (
                SELECT COUNT(*)
                FROM documents
                WHERE source='tushare_announcement'
              ) AS count,
              (
                SELECT MAX(recent.rec_time)
                FROM (
                  SELECT
                    CASE
                      WHEN LOWER(TRIM(
                        COALESCE(
                          json_extract(metadata_json, '$.rec_time'),
                          ''
                        )
                      )) IN ('', 'nan', 'nat', 'none', 'null')
                      THEN NULL
                      ELSE json_extract(metadata_json, '$.rec_time')
                    END AS rec_time
                  FROM documents
                  WHERE source='tushare_announcement'
                  ORDER BY published_at DESC, id DESC
                  LIMIT 10000
                ) AS recent
              ) AS latest_rec_time
            """
        ).fetchone()
        source_rows = connection.execute(
            """
            SELECT
              source,
              COUNT(*) AS documents,
              MAX(published_at) AS latest_published_at
            FROM documents
            GROUP BY source
            ORDER BY documents DESC, source
            """
        ).fetchall()
        latest_runs = {
            str(row["source"]): row
            for row in connection.execute(
                """
                SELECT *
                FROM (
                  SELECT
                    source, status, started_at, finished_at,
                    fetched, inserted, error,
                    ROW_NUMBER() OVER (
                      PARTITION BY source
                      ORDER BY COALESCE(finished_at, started_at) DESC,
                               run_id DESC
                    ) AS row_rank
                  FROM ingestion_runs
                )
                WHERE row_rank=1
                """
            ).fetchall()
        }
        cursors = {
            str(row["source"]): row
            for row in connection.execute(
                """
                SELECT source, cursor, updated_at
                FROM source_cursors
                """
            ).fetchall()
        }
        gaps = connection.execute(
            """
            SELECT partition_start, partition_end, status
            FROM backfill_partitions
            WHERE source='tushare_announcement'
              AND status<>'complete'
            ORDER BY partition_start, partition_end
            LIMIT 200
            """
        ).fetchall()
        artifact_counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM document_artifacts
                GROUP BY status
                """
            ).fetchall()
        }
        missing_pdf = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM documents d
                WHERE d.source='tushare_announcement'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM document_artifacts a
                    WHERE a.document_id=d.id
                      AND a.artifact_type='pdf'
                      AND a.status IN (
                        'downloaded', 'parsed', 'ocr_required', 'ocr_failed'
                      )
                  )
                """
            ).fetchone()[0]
        )
        semantic_counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM semantic_runs
                GROUP BY status
                """
            ).fetchall()
        }
        semantic_ready = int(
            connection.execute(
                """
                SELECT COUNT(DISTINCT d.id)
                FROM documents d
                JOIN document_artifacts a ON a.document_id=d.id
                WHERE a.artifact_type='parsed'
                  AND a.status='parsed'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM semantic_runs s
                    WHERE s.document_id=d.id
                      AND s.status IN ('succeeded', 'no_event')
                  )
                """
            ).fetchone()[0]
        )
        semantic_document_counts = connection.execute(
            """
            SELECT
              COUNT(DISTINCT CASE
                WHEN status IN ('succeeded', 'no_event')
                THEN document_id
              END) AS completed,
              COUNT(DISTINCT CASE
                WHEN status IN (
                  'succeeded', 'no_event',
                  'failed_retryable', 'failed_terminal'
                )
                THEN document_id
              END) AS terminal
            FROM semantic_runs
            """
        ).fetchone()
        canonical = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM event_candidates
                WHERE validation_status='canonical'
                """
            ).fetchone()[0]
        )
        quarantined = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM event_candidates
                WHERE validation_status='quarantined'
                """
            ).fetchone()[0]
        )
        sources: list[dict[str, object]] = []
        for row in source_rows:
            source = str(row["source"])
            latest_run = latest_runs.get(source)
            cursor = cursors.get(source)
            sources.append(
                {
                    "source": source,
                    "documents": int(row["documents"] or 0),
                    "latest_published_at": row["latest_published_at"],
                    "last_ingested_at": (
                        (
                            latest_run["finished_at"]
                            or latest_run["started_at"]
                        )
                        if latest_run is not None
                        else row["latest_published_at"]
                    ),
                    "latest_run_status": (
                        str(latest_run["status"])
                        if latest_run is not None
                        else "unknown"
                    ),
                    "fetched": (
                        int(latest_run["fetched"] or 0)
                        if latest_run is not None
                        else 0
                    ),
                    "inserted": (
                        int(latest_run["inserted"] or 0)
                        if latest_run is not None
                        else 0
                    ),
                    "error": (
                        str(latest_run["error"] or "")
                        if latest_run is not None
                        else ""
                    ),
                    "cursor": (
                        str(cursor["cursor"])
                        if cursor is not None
                        else None
                    ),
                    "cursor_updated_at": (
                        cursor["updated_at"]
                        if cursor is not None
                        else None
                    ),
                }
            )

    document_count = int(documents["count"] or 0)
    total_documents = sum(
        int(row["documents"] or 0)
        for row in source_rows
    )
    pdf_ready = max(0, document_count - missing_pdf)
    parsed = artifact_counts.get("parsed", 0)
    semantic_completed = int(
        semantic_document_counts["completed"] or 0
    )
    semantic_terminal = int(
        semantic_document_counts["terminal"] or 0
    )
    failed = (
        semantic_counts.get("failed_retryable", 0)
        + semantic_counts.get("failed_terminal", 0)
    )
    backlog = {
        "download": missing_pdf,
        "parse": max(0, pdf_ready - parsed),
        "semantic": max(0, parsed - semantic_terminal),
    }
    backlog["total"] = sum(backlog.values())
    decision_counts = {
        "canonical": canonical,
        "no_event": semantic_counts.get("no_event", 0),
        "quarantined": quarantined,
        "failed": failed,
    }
    return {
        "metadata": {
            "documents": document_count,
            "total_documents": total_documents,
            "latest_rec_time": documents["latest_rec_time"],
            "date_gaps": [
                {
                    "start": str(row["partition_start"]),
                    "end": str(row["partition_end"]),
                    "status": str(row["status"]),
                }
                for row in gaps
            ],
        },
        "artifacts": {
            "queued": missing_pdf + artifact_counts.get("queued", 0),
            "downloaded": artifact_counts.get("downloaded", 0),
            "parsed": artifact_counts.get("parsed", 0),
            "ocr_failed": artifact_counts.get("ocr_failed", 0),
            "by_status": artifact_counts,
        },
        "semantic": {
            "queued": semantic_ready,
            "succeeded": semantic_counts.get("succeeded", 0),
            "no_event": semantic_counts.get("no_event", 0),
            "quarantined": quarantined,
            "failed": failed,
            "by_status": semantic_counts,
            "decisions": decision_counts,
        },
        "pipeline": {
            "stages": {
                "catalogued": document_count,
                "pdf_ready": pdf_ready,
                "parsed": parsed,
                "semantic_completed": semantic_completed,
                "canonical_events": canonical,
            },
            "backlog": backlog,
            "sources": sources,
        },
        "quality": {
            "source": "deterministic-validator",
            "gold_benchmark_required": False,
        },
        "versions": {
            "contract": "semantic-extraction-job-v1",
            **versions,
            "parser": parser_version or "announcement-layout-v1",
        },
        "capacity": {
            "sqlite_bytes": (
                store.db_path.stat().st_size
                if store.db_path.exists()
                else 0
            ),
            "local_artifact_bytes": _directory_size(local_path),
            "oss_bytes": None,
        },
    }


def _durable_write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise FileExistsError(
                "semantic_status_report_immutable_conflict"
            ) from None


def _atomic_durable_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def write_semantic_status_report(
    repo_root: str | Path,
    report: Mapping[str, object],
    *,
    now: Callable[[], datetime] | None = None,
) -> tuple[Path, Path]:
    missing = set(SEMANTIC_STATUS_KEYS).difference(report)
    if missing:
        raise ValueError(
            "semantic_status_report_missing:" + ",".join(sorted(missing))
        )
    clock = now or (lambda: datetime.now(timezone.utc))
    generated_at = clock()
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("semantic_status_clock_must_be_aware")
    normalized = generated_at.astimezone(timezone.utc)
    payload = {
        **dict(report),
        "generated_at": normalized.isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
    }
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    reports = Path(repo_root) / "reports" / "intelligence"
    timestamp = normalized.strftime("%Y%m%dT%H%M%S%fZ")
    immutable = reports / f"semantic_status_{timestamp}.json"
    latest = reports / "semantic_status_latest.json"
    _durable_write_once(immutable, serialized)
    _atomic_durable_replace(latest, serialized)
    snapshots = sorted(reports.glob("semantic_status_[0-9]*.json"))
    for stale in snapshots[:-SEMANTIC_STATUS_SNAPSHOT_RETENTION]:
        try:
            stale.unlink()
        except OSError:
            continue
    return immutable, latest


def configured_source_statuses(repo_root: str | Path) -> dict[str, str]:
    config_path = Path(repo_root) / "configs" / "intelligence_sources.yaml"
    if not config_path.exists():
        return {}
    sources = load_source_config(config_path).get("sources") or {}
    statuses = {}
    for source, spec in sources.items():
        if not spec.get("enabled", False):
            statuses[str(source)] = "disabled"
        elif spec.get("type") == "contract_only":
            statuses[str(source)] = str(
                spec.get("unavailable_reason") or "adapter_not_implemented"
            )
        elif spec.get("type") == "tushare_announcement" and not spec.get("entitled", False):
            statuses[str(source)] = "entitlement_required"
        else:
            statuses[str(source)] = "configured"
    return statuses


def build_quality_report(repo_root: str | Path) -> dict:
    root = Path(repo_root)
    intelligence_root = root / "data" / "shared" / "intelligence"
    store = IntelligenceStore(intelligence_root)
    configured = configured_source_statuses(root)
    with store.connect() as connection:
        document_rows = connection.execute(
            """
            SELECT
                source,
                COUNT(*) AS documents,
                MIN(published_at) AS min_published_at,
                MAX(published_at) AS max_published_at,
                SUM(
                  CASE WHEN julianday(first_seen_at) IS NOT NULL
                       THEN 1 ELSE 0 END
                ) AS documents_with_first_seen,
                SUM(
                  CASE
                    WHEN (
                      julianday(first_seen_at)
                      - julianday(published_at)
                    ) < 0
                    THEN 1 ELSE 0
                  END
                ) AS invalid_negative_delay_rows
            FROM documents
            GROUP BY source
            """
        ).fetchall()
        health_rows = connection.execute(
            """
            SELECT r.*
            FROM ingestion_runs r
            JOIN (
                SELECT source, MAX(started_at) AS started_at
                FROM ingestion_runs
                GROUP BY source
            ) latest
              ON latest.source=r.source
             AND latest.started_at=r.started_at
            ORDER BY r.source
            """
        ).fetchall()
        event_summary = connection.execute(
            """
            SELECT
              COUNT(*) AS events,
              COALESCE(
                AVG(
                  CASE WHEN EXISTS (
                    SELECT 1
                    FROM event_entities entity
                    WHERE entity.event_id=events.event_id
                  ) THEN 1.0 ELSE 0.0 END
                ),
                0.0
              ) AS linked_event_ratio
            FROM events
            """
        ).fetchone()
        event_type_rows = connection.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM events
            GROUP BY event_type
            ORDER BY count DESC, event_type
            """,
        ).fetchall()
        latest_audit = connection.execute(
            """
            SELECT *
            FROM source_audit_runs
            ORDER BY finished_at DESC, started_at DESC
            LIMIT 1
            """
        ).fetchone()
        latest_audit_counts = []
        if latest_audit is not None:
            latest_audit_counts = connection.execute(
                """
                SELECT dataset, comparison_status, COUNT(*) AS count
                FROM source_audit_items
                WHERE run_id=?
                GROUP BY dataset, comparison_status
                ORDER BY dataset, comparison_status
                """,
                (latest_audit["run_id"],),
            ).fetchall()

        document_by_source = {
            str(row["source"]): row
            for row in document_rows
        }
        health_by_source = {
            str(row["source"]): row
            for row in health_rows
        }
        sources: list[dict[str, object]] = []
        for source in sorted(
            set(document_by_source)
            .union(health_by_source)
            .union(configured)
        ):
            document = document_by_source.get(source)
            health = health_by_source.get(source)
            delay = connection.execute(
                """
                WITH sampled AS (
                  SELECT first_seen_at, published_at
                  FROM documents
                  WHERE source=?
                  ORDER BY published_at DESC, id DESC
                  LIMIT 10000
                ),
                valid AS (
                  SELECT
                    (
                      julianday(first_seen_at)
                      - julianday(published_at)
                    ) * 1440.0 AS minutes
                  FROM sampled
                  WHERE julianday(first_seen_at) IS NOT NULL
                    AND julianday(published_at) IS NOT NULL
                    AND julianday(first_seen_at)
                        >= julianday(published_at)
                ),
                ranked AS (
                  SELECT
                    minutes,
                    ROW_NUMBER() OVER (ORDER BY minutes) AS row_number,
                    COUNT(*) OVER () AS row_count
                  FROM valid
                )
                SELECT
                  AVG(
                    CASE
                      WHEN row_number IN (
                        (row_count + 1) / 2,
                        (row_count + 2) / 2
                      )
                      THEN minutes
                    END
                  ) AS median_minutes,
                  MAX(
                    CASE
                      WHEN row_number=(95 * row_count + 99) / 100
                      THEN minutes
                    END
                  ) AS p95_minutes
                FROM ranked
                """,
                (source,),
            ).fetchone()
            sources.append({
                "source": source,
                "documents": (
                    int(document["documents"])
                    if document is not None
                    else 0
                ),
                "min_published_at": (
                    document["min_published_at"]
                    if document is not None
                    else None
                ),
                "max_published_at": (
                    document["max_published_at"]
                    if document is not None
                    else None
                ),
                "latest_status": (
                    str(health["status"])
                    if health is not None
                    else configured.get(source, "unknown")
                ),
                "latest_error": (
                    str(health["error"])
                    if health is not None
                    else None
                ),
                "median_ingestion_delay_minutes": (
                    round(float(delay["median_minutes"]), 6)
                    if delay["median_minutes"] is not None
                    else None
                ),
                "p95_ingestion_delay_minutes": (
                    round(float(delay["p95_minutes"]), 6)
                    if delay["p95_minutes"] is not None
                    else None
                ),
                "invalid_negative_delay_rows": (
                    int(document["invalid_negative_delay_rows"] or 0)
                    if document is not None
                    else 0
                ),
            })

    total_documents = sum(
        int(row["documents"])
        for row in document_rows
    )
    documents_with_first_seen = sum(
        int(row["documents_with_first_seen"] or 0)
        for row in document_rows
    )
    negative_delay_rows = sum(
        int(row["invalid_negative_delay_rows"] or 0)
        for row in document_rows
    )
    payload = {
        "schema_version": store.schema_version(),
        "integrity": store.quick_integrity_check(),
        "documents": total_documents,
        "events": int(event_summary["events"] or 0),
        "linked_event_ratio": float(
            event_summary["linked_event_ratio"] or 0.0
        ),
        "event_types": {
            str(row["event_type"]): int(row["count"])
            for row in event_type_rows
        },
        "point_in_time_quality": {
            "documents_with_first_seen":
                documents_with_first_seen,
            "negative_ingestion_delay_rows": negative_delay_rows,
        },
        "cross_source_audit": {
            "latest": (
                {
                    "run_id": str(latest_audit["run_id"]),
                    "as_of": str(latest_audit["as_of_date"]),
                    "datasets": str(latest_audit["dataset_scope"]).split(","),
                    "primary_source": str(latest_audit["primary_source"]),
                    "secondary_source": str(latest_audit["secondary_source"]),
                    "status": str(latest_audit["status"]),
                    "supplement_enabled": bool(
                        latest_audit["supplement_enabled"]
                    ),
                    "started_at": str(latest_audit["started_at"]),
                    "finished_at": str(latest_audit["finished_at"]),
                    "counts": {
                        str(row["dataset"]): {}
                        for row in latest_audit_counts
                    },
                    "metrics": json.loads(
                        str(latest_audit["metrics_json"])
                    ),
                }
                if latest_audit is not None
                else None
            ),
        },
        "sources": sources,
    }
    latest_summary = payload["cross_source_audit"]["latest"]
    if isinstance(latest_summary, dict):
        counts = latest_summary["counts"]
        for row in latest_audit_counts:
            counts[str(row["dataset"])][
                str(row["comparison_status"])
            ] = int(row["count"])
    reports = root / "reports" / "intelligence"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "quality_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def evaluate_event_factors(features: pd.DataFrame, labels: pd.DataFrame) -> dict:
    if features.empty or labels.empty:
        return {"status": "insufficient_data", "factors": {}}
    required = {"code", "trade_date", "excess_return"}
    if {"code", "trade_date"}.difference(features.columns) or required.difference(labels.columns):
        raise ValueError("intelligence_factor_evaluation_missing_columns")
    label_frame = labels.copy()
    if "horizon" not in label_frame.columns:
        label_frame["horizon"] = 5
    label_frame["horizon"] = pd.to_numeric(label_frame["horizon"], errors="coerce")
    horizons = sorted(label_frame["horizon"].dropna().astype(int).unique())
    preferred_horizon = 5 if 5 in horizons else (horizons[0] if horizons else None)
    rows: dict[str, dict] = {}
    directional = {
        "event_positive_decay_5d": 1.0,
        "event_negative_decay_5d": -1.0,
        "event_net_strength_5d": 1.0,
        "event_net_materiality_20d": 1.0,
        "policy_industry_exposure_20d": 1.0,
        "event_price_volume_confirmation": 1.0,
    }
    preferred_rows = 0
    for factor in EVENT_FACTOR_COLUMNS:
        if factor not in features:
            continue
        values = pd.to_numeric(features[factor], errors="coerce")
        non_null_coverage = float(values.notna().mean())
        signal_activation_rate = float(
            values.fillna(0.0).abs().gt(1e-12).mean()
        )
        if "event_data_coverage" in features.columns:
            source_values = pd.to_numeric(
                features["event_data_coverage"],
                errors="coerce",
            )
            source_coverage = float(
                source_values.fillna(0.0).gt(0.0).mean()
            )
        else:
            source_coverage = non_null_coverage
        coverage = source_coverage
        orientation = directional.get(factor)
        horizon_rank_ic: dict[str, float | None] = {}
        horizon_ic_counts: dict[str, int] = {}
        horizon_long_short: dict[str, float | None] = {}
        preferred_daily_ics: list[float] = []
        preferred_spreads: list[float] = []
        false_positive_rate: float | None = None
        preferred_joined_rows = 0
        label_coverage: float | None = None
        for horizon in horizons:
            label_slice = label_frame.loc[label_frame["horizon"].eq(horizon)]
            merged = features[["code", "trade_date", factor]].merge(
                label_slice[["code", "trade_date", "excess_return"]],
                on=["code", "trade_date"],
                how="inner",
            )
            merged["_factor"] = pd.to_numeric(merged[factor], errors="coerce")
            if orientation is not None:
                merged["_factor"] *= orientation
            merged["_target"] = pd.to_numeric(merged["excess_return"], errors="coerce")
            daily_ics: list[float] = []
            daily_spreads: list[float] = []
            for _, group in merged.groupby(merged["trade_date"].astype(str), sort=True):
                usable = group[["_factor", "_target"]].dropna()
                if (
                    len(usable) >= 5
                    and usable["_factor"].nunique() > 1
                    and usable["_target"].nunique() > 1
                ):
                    daily_ics.append(
                        float(usable["_factor"].corr(usable["_target"], method="spearman"))
                    )
                    tail = max(1, int(len(usable) * 0.20))
                    ranked = usable.sort_values("_factor", kind="stable")
                    daily_spreads.append(
                        float(ranked.tail(tail)["_target"].mean())
                        - float(ranked.head(tail)["_target"].mean())
                    )
            key = str(int(horizon))
            horizon_rank_ic[key] = (
                float(pd.Series(daily_ics, dtype=float).mean()) if daily_ics else None
            )
            horizon_ic_counts[key] = len(daily_ics)
            horizon_long_short[key] = (
                float(pd.Series(daily_spreads, dtype=float).mean())
                if daily_spreads
                else None
            )
            if horizon == preferred_horizon:
                preferred_daily_ics = daily_ics
                preferred_spreads = daily_spreads
                preferred_joined_rows = len(merged)
                label_coverage = (
                    float(
                        merged[["_factor", "_target"]]
                        .dropna()
                        .shape[0]
                        / len(merged)
                    )
                    if len(merged)
                    else None
                )
                if orientation is not None:
                    directional_rows = merged[["_factor", "_target"]].dropna()
                    directional_rows = directional_rows.loc[
                        directional_rows["_factor"].abs().gt(0.0)
                        & directional_rows["_target"].abs().gt(0.0)
                    ]
                    if not directional_rows.empty:
                        false_positive_rate = float(
                            (
                                directional_rows["_factor"].map(lambda value: 1 if value > 0 else -1)
                                != directional_rows["_target"].map(
                                    lambda value: 1 if value > 0 else -1
                                )
                            ).mean()
                        )
        preferred_rows = max(preferred_rows, preferred_joined_rows)
        daily_ic_series = pd.Series(preferred_daily_ics, dtype=float)
        mean_ic = float(daily_ic_series.mean()) if preferred_daily_ics else None
        icir = (
            float(daily_ic_series.mean() / daily_ic_series.std(ddof=1))
            if len(daily_ic_series) >= 3 and daily_ic_series.std(ddof=1) > 0
            else None
        )
        ic_sign_stability = (
            float(daily_ic_series.gt(0.0).mean())
            if preferred_daily_ics
            else None
        )
        subperiod_means = [
            float(part.mean())
            for _, part in pd.Series(preferred_daily_ics, dtype=float)
            .groupby(pd.RangeIndex(len(preferred_daily_ics)) * 4 // max(len(preferred_daily_ics), 1))
            if not part.empty
        ]
        subperiod_stability = (
            float(pd.Series(subperiod_means).gt(0.0).mean())
            if subperiod_means
            else None
        )
        ablation_spread = (
            float(pd.Series(preferred_spreads, dtype=float).mean())
            if preferred_spreads
            else None
        )
        decay_ratio = None
        if horizon_rank_ic.get("5") is not None and horizon_rank_ic.get("20") is not None:
            denominator = abs(float(horizon_rank_ic["5"]))
            if denominator > 1e-12:
                decay_ratio = abs(float(horizon_rank_ic["20"])) / denominator
        gate_reasons: list[str] = []
        if orientation is None:
            gate_reasons.append("direction_not_declared")
        if coverage < 0.55:
            gate_reasons.append("coverage_below_floor")
        if signal_activation_rate <= 0.0:
            gate_reasons.append("signal_never_activated")
        if len(preferred_daily_ics) < 20:
            gate_reasons.append("daily_ic_count_below_floor")
        if mean_ic is None or mean_ic < 0.01:
            gate_reasons.append("mean_rank_ic_below_floor")
        if ic_sign_stability is None or ic_sign_stability < 0.60:
            gate_reasons.append("ic_sign_stability_below_floor")
        if subperiod_stability is None or subperiod_stability < 0.50:
            gate_reasons.append("subperiod_stability_below_floor")
        if ablation_spread is None or ablation_spread <= 0.0:
            gate_reasons.append("ablation_spread_not_positive")
        if false_positive_rate is not None and false_positive_rate > 0.55:
            gate_reasons.append("false_positive_rate_above_ceiling")
        recommendation = "model_iteration" if not gate_reasons else "observe"
        rows[factor] = {
            "coverage": coverage,
            "non_null_coverage": non_null_coverage,
            "source_coverage": source_coverage,
            "semantic_coverage": source_coverage,
            "signal_activation_rate": signal_activation_rate,
            "label_coverage": label_coverage,
            "preferred_horizon": preferred_horizon,
            "daily_ic_count": len(preferred_daily_ics),
            "mean_rank_ic": mean_ic,
            "icir": icir,
            "ic_sign_stability": ic_sign_stability,
            "subperiod_stability": subperiod_stability,
            "ablation_long_short_spread": ablation_spread,
            "false_positive_rate": false_positive_rate,
            "horizon_rank_ic": horizon_rank_ic,
            "horizon_ic_counts": horizon_ic_counts,
            "horizon_long_short_spread": horizon_long_short,
            "signal_decay_ratio_20d_to_5d": decay_ratio,
            "recommendation": recommendation,
            "gate_reasons": gate_reasons,
        }
    return {
        "status": "complete",
        "rows": preferred_rows,
        "preferred_horizon": preferred_horizon,
        "evaluation_policy": {
            "minimum_coverage": 0.55,
            "minimum_daily_ic_count": 20,
            "minimum_mean_rank_ic": 0.01,
            "minimum_ic_sign_stability": 0.60,
            "minimum_subperiod_stability": 0.50,
            "maximum_false_positive_rate": 0.55,
            "requires_positive_ablation_spread": True,
        },
        "factors": rows,
    }
