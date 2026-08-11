"""Transactional point-in-time store for market intelligence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from .schema import MIGRATIONS, PERFORMANCE_INDEXES, SCHEMA_VERSION
from .semantic.execution_contract import ExecutorBinding
from .types import MarketEvent, SourceDocument, utc_iso

AvailabilityPolicy = Literal["observed", "research"]
NextMarketOpenResolver = Callable[[str], str | datetime]

DEFAULT_HISTORICAL_CUTOFF = "2026-07-17T23:59:59+08:00"
RECONSTRUCTED_PROVENANCE = {
    "reconstructed_rec_time",
    "reconstructed_next_open",
}
BACKFILL_PARTITION_STATUSES = {
    "pending",
    "running",
    "complete",
    "failed_retryable",
    "failed_terminal",
    "failed_overflow",
}
BACKFILL_COMPLETION_STRATEGY_VERSION = 3
HISTORY_QUEUE_PRIORITY = 10
LIVE_QUEUE_PRIORITY = 100


class BackfillLeaseBusy(RuntimeError):
    pass


class BackfillGenerationConflict(RuntimeError):
    pass


class BackfillProgressRegression(RuntimeError):
    pass


class BackfillConfigurationConflict(RuntimeError):
    pass


class IngestionLeaseBusy(RuntimeError):
    pass


class IngestionGenerationConflict(RuntimeError):
    pass


class DocumentArtifactConflict(RuntimeError):
    pass


class SemanticRunConflict(RuntimeError):
    pass


SEMANTIC_EXECUTION_TRANSITIONS = {
    "assigned": frozenset({"running", "abandoned"}),
    "running": frozenset({"retry_wait", "produced", "abandoned"}),
    "retry_wait": frozenset({"running", "abandoned"}),
    "produced": frozenset({"validating"}),
    "validating": frozenset({"retrying_event", "accepted", "quarantined"}),
    "retrying_event": frozenset({"validating", "quarantined"}),
    "accepted": frozenset(),
    "quarantined": frozenset(),
    "abandoned": frozenset(),
}


@dataclass(frozen=True)
class BackfillDocumentWrite:
    document: SourceDocument
    availability_provenance: str = "observed"
    source_recorded_at: str | datetime | None = None
    research_available_at: str | datetime | None = None


@dataclass(frozen=True)
class BackfillUniverseMember:
    ts_code: str
    security_type: str = "stock"
    list_date: str = ""
    delist_date: str = ""
    listing_status: str = ""


V1_TIMESTAMP_COLUMNS = {
    "schema_meta": ("applied_at",),
    "documents": (
        "published_at",
        "first_seen_at",
        "effective_at",
        "revised_at",
    ),
    "ingestion_runs": ("started_at", "finished_at"),
    "source_cursors": ("updated_at",),
    "events": ("published_at", "effective_at"),
    "quality_results": ("measured_at",),
}


def _strict_utc_iso(value: str | datetime, *, field: str) -> str:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"intelligence_invalid_timestamp:{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"intelligence_naive_timestamp:{field}")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


class IntelligenceStore:
    def __init__(
        self,
        root: str | Path,
        *,
        historical_cutoff: str | datetime = DEFAULT_HISTORICAL_CUTOFF,
        next_market_open_resolver: NextMarketOpenResolver | None = None,
    ) -> None:
        self._requested_historical_cutoff = _strict_utc_iso(
            historical_cutoff,
            field="historical_cutoff",
        )
        self._historical_cutoff = self._requested_historical_cutoff
        self._next_market_open_resolver = next_market_open_resolver
        self.root = Path(root)
        self.db_path = self.root / "intelligence.sqlite3"
        self.raw_root = self.root / "raw"
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @property
    def historical_cutoff(self) -> str:
        return self._historical_cutoff

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _migrate(self) -> None:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current_schema_version(connection)
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"intelligence_schema_too_new:{current}>{SCHEMA_VERSION}"
                )
            migrated_v2 = False
            for version in range(current + 1, SCHEMA_VERSION + 1):
                if version == 2:
                    self._normalize_v1_timestamps(connection)
                for statement_index, statement in enumerate(
                    self._migration_statements(MIGRATIONS[version]),
                    start=1,
                ):
                    connection.execute(statement)
                    self._after_migration_statement(
                        version,
                        statement_index,
                        statement,
                    )
                if version == 2:
                    self._bind_historical_cutoff_setting(
                        connection,
                        initialize=True,
                    )
                    self._backfill_observed_availability(connection)
                    migrated_v2 = True
                if version == 8:
                    self._initialize_announcement_catalog_state(
                        connection
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta(version, applied_at) VALUES(?, ?)",
                    (version, utc_iso()),
                )
            if current >= 2 and not migrated_v2:
                self._bind_historical_cutoff_setting(
                    connection,
                    initialize=False,
                )
            for statement in self._migration_statements(
                PERFORMANCE_INDEXES
            ):
                connection.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _migration_statements(script: str) -> Iterable[str]:
        pending = ""
        for character in script:
            pending += character
            if character == ";" and sqlite3.complete_statement(pending):
                statement = pending.strip()
                if statement:
                    yield statement
                pending = ""
        if pending.strip():
            raise RuntimeError("intelligence_migration_incomplete_statement")

    def _after_migration_statement(
        self,
        version: int,
        statement_index: int,
        statement: str,
    ) -> None:
        del version, statement_index, statement

    def _backfill_observed_availability(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO document_availability(
                document_id, source_recorded_at, research_available_at,
                availability_provenance, historical_cutoff, created_at
            )
            SELECT id, NULL, first_seen_at, 'observed', ?, ?
            FROM documents
            """,
            (self._historical_cutoff, utc_iso()),
        )

    def _bind_historical_cutoff_setting(
        self,
        connection: sqlite3.Connection,
        *,
        initialize: bool,
    ) -> None:
        if initialize:
            connection.execute(
                """
                INSERT OR IGNORE INTO intelligence_settings(
                    key, value, created_at
                ) VALUES('historical_cutoff', ?, ?)
                """,
                (self._requested_historical_cutoff, utc_iso()),
            )
        row = connection.execute(
            """
            SELECT value FROM intelligence_settings
            WHERE key='historical_cutoff'
            """
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "intelligence_historical_cutoff_setting_missing"
            )
        try:
            stored_cutoff = _strict_utc_iso(
                str(row["value"]),
                field="stored_historical_cutoff",
            )
        except ValueError as exc:
            raise RuntimeError(
                "intelligence_historical_cutoff_setting_invalid"
            ) from exc
        if stored_cutoff != self._requested_historical_cutoff:
            raise RuntimeError(
                "intelligence_historical_cutoff_mismatch:"
                f"requested={self._requested_historical_cutoff}:"
                f"stored={stored_cutoff}"
            )
        self._historical_cutoff = stored_cutoff

    @staticmethod
    def _normalize_v1_timestamps(
        connection: sqlite3.Connection,
    ) -> None:
        naive_count = 0
        invalid_count = 0
        updates: list[tuple[str, str, int, str]] = []
        for table, columns in V1_TIMESTAMP_COLUMNS.items():
            table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name=?
                """,
                (table,),
            ).fetchone()
            if table_exists is None:
                continue
            rows = connection.execute(
                f"""
                SELECT rowid AS _migration_rowid, {', '.join(columns)}
                FROM {table}
                """
            ).fetchall()
            for row in rows:
                for column in columns:
                    value = row[column]
                    if value is None:
                        continue
                    try:
                        normalized = _strict_utc_iso(
                            str(value),
                            field=f"v1.{table}.{column}",
                        )
                    except ValueError as exc:
                        if str(exc).startswith(
                            "intelligence_naive_timestamp:"
                        ):
                            naive_count += 1
                        else:
                            invalid_count += 1
                        continue
                    if normalized != str(value):
                        updates.append(
                            (
                                table,
                                column,
                                int(row["_migration_rowid"]),
                                normalized,
                            )
                        )
        if naive_count:
            raise ValueError(
                f"intelligence_v1_naive_timestamp:{naive_count}"
            )
        if invalid_count:
            raise ValueError(
                f"intelligence_v1_invalid_timestamp:{invalid_count}"
            )
        for table, column, rowid, normalized in updates:
            connection.execute(
                f"UPDATE {table} SET {column}=? WHERE rowid=?",
                (normalized, rowid),
            )

    @staticmethod
    def _current_schema_version(connection: sqlite3.Connection) -> int:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='schema_meta'
            """
        ).fetchone()
        if table is None:
            return 0
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_meta"
        ).fetchone()
        return int(row["version"])

    def schema_version(self) -> int:
        with self.connect() as connection:
            return self._current_schema_version(connection)

    def integrity_check(self) -> str:
        with self.connect() as connection:
            integrity_rows = connection.execute(
                "PRAGMA integrity_check"
            ).fetchall()
            integrity = [str(row[0]) for row in integrity_rows]
            if integrity != ["ok"]:
                return f"sqlite_integrity:{'|'.join(integrity)}"
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                return (
                    "foreign_key_violation:"
                    f"{len(foreign_key_violations)}"
                )
            return "ok"

    def quick_integrity_check(self) -> str:
        with self.connect() as connection:
            integrity_rows = connection.execute(
                "PRAGMA quick_check(1)"
            ).fetchall()
            integrity = [str(row[0]) for row in integrity_rows]
            if integrity != ["ok"]:
                return (
                    "sqlite_quick_integrity:"
                    f"{'|'.join(integrity)}"
                )
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                return (
                    "foreign_key_violation:"
                    f"{len(foreign_key_violations)}"
                )
            return "ok"

    def insert_document(self, document: SourceDocument) -> tuple[int, bool]:
        published_at = _strict_utc_iso(
            document.published_at,
            field="document.published_at",
        )
        first_seen_at = _strict_utc_iso(
            document.first_seen_at,
            field="document.first_seen_at",
        )
        effective_at = _strict_utc_iso(
            document.effective_at,
            field="document.effective_at",
        )
        revised_at = (
            _strict_utc_iso(document.revised_at, field="document.revised_at")
            if document.revised_at
            else None
        )
        payload = bytes(document.content)
        content_hash = hashlib.sha256(payload).hexdigest()
        raw_path = self._raw_path_value(
            document,
            published_at=published_at,
            content_hash=content_hash,
            payload=payload,
        )
        queue_priority, live_observed = self._document_queue_values(
            document
        )
        values = (
            document.source,
            document.source_id,
            document.title,
            published_at,
            first_seen_at,
            effective_at,
            revised_at,
            document.revision_of,
            document.source_url,
            document.mime_type,
            content_hash,
            raw_path,
            json.dumps(document.metadata, ensure_ascii=False, sort_keys=True),
            queue_priority,
            live_observed,
        )
        with self.connect() as connection:
            if (
                str(
                    document.metadata.get("content_scope") or ""
                ).strip()
                == "title_metadata"
            ):
                existing = connection.execute(
                    """
                    SELECT id, metadata_json
                    FROM documents
                    WHERE source=? AND source_id=?
                    ORDER BY live_observed DESC, id
                    LIMIT 1
                    """,
                    (document.source, document.source_id),
                ).fetchone()
                if existing is not None:
                    try:
                        prior_metadata = json.loads(
                            str(existing["metadata_json"] or "{}")
                        )
                    except json.JSONDecodeError:
                        prior_metadata = {}
                    if not isinstance(prior_metadata, dict):
                        prior_metadata = {}
                    merged_metadata = dict(prior_metadata)
                    for key, value in document.metadata.items():
                        if value not in (None, "", [], {}):
                            merged_metadata[str(key)] = value
                    document_id = int(existing["id"])
                    connection.execute(
                        """
                        UPDATE documents
                        SET title=CASE WHEN ?<>'' THEN ? ELSE title END,
                            source_url=CASE
                              WHEN ?<>'' THEN ?
                              ELSE source_url
                            END,
                            metadata_json=?,
                            queue_priority=MAX(queue_priority, ?),
                            live_observed=MAX(live_observed, ?)
                        WHERE id=?
                        """,
                        (
                            document.title,
                            document.title,
                            document.source_url,
                            document.source_url,
                            json.dumps(
                                merged_metadata,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            queue_priority,
                            live_observed,
                            document_id,
                        ),
                    )
                    self._upsert_document_security_links(
                        connection,
                        document_id=document_id,
                        document=document,
                    )
                    return document_id, False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO documents(
                    source, source_id, title, published_at, first_seen_at,
                    effective_at, revised_at, revision_of, source_url, mime_type,
                    content_hash, raw_path, metadata_json,
                    queue_priority, live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            inserted = cursor.rowcount == 1
            row = connection.execute(
                "SELECT id FROM documents WHERE source=? AND source_id=? AND content_hash=?",
                (document.source, document.source_id, content_hash),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "intelligence_document_upsert_missing"
                )
            document_id = int(row["id"])
            connection.execute(
                """
                UPDATE documents
                SET title=CASE
                      WHEN title='' THEN ?
                      WHEN ?='' THEN title
                      WHEN ? < title THEN ?
                      ELSE title
                    END
                WHERE id=?
                """,
                (
                    document.title,
                    document.title,
                    document.title,
                    document.title,
                    document_id,
                ),
            )
            if live_observed:
                connection.execute(
                    """
                    UPDATE documents
                    SET queue_priority=MAX(queue_priority, ?),
                        live_observed=1
                    WHERE id=?
                    """,
                    (LIVE_QUEUE_PRIORITY, document_id),
                )
            self._upsert_document_security_links(
                connection,
                document_id=document_id,
                document=document,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO document_availability(
                    document_id, source_recorded_at, research_available_at,
                    availability_provenance, historical_cutoff, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    document_id,
                    None,
                    first_seen_at,
                    "observed",
                    self._historical_cutoff,
                    utc_iso(),
                ),
            )
        return document_id, inserted

    @staticmethod
    def _document_queue_values(
        document: SourceDocument,
    ) -> tuple[int, int]:
        ingestion_mode = str(
            document.metadata.get("ingestion_mode") or ""
        ).strip().casefold()
        if ingestion_mode == "history":
            return HISTORY_QUEUE_PRIORITY, 0
        return (
            LIVE_QUEUE_PRIORITY,
            int(ingestion_mode == "live"),
        )

    @staticmethod
    def _document_security_link_values(
        document: SourceDocument,
    ) -> tuple[tuple[str, str, str], ...]:
        def preferred(*values: str) -> str:
            normalized = sorted({
                str(value).strip()
                for value in values
                if str(value).strip()
            })
            return normalized[0] if normalized else ""

        metadata = document.metadata
        links: dict[str, tuple[str, str]] = {}
        raw_links = metadata.get("security_links")
        if isinstance(raw_links, (list, tuple)):
            for raw_link in raw_links:
                if not isinstance(raw_link, dict):
                    continue
                code = str(
                    raw_link.get("ts_code")
                    or raw_link.get("code")
                    or ""
                ).strip().upper()
                if not code:
                    continue
                name = str(raw_link.get("name") or "").strip()
                provenance = str(
                    raw_link.get("provenance")
                    or metadata.get("provider")
                    or "document_metadata"
                ).strip()
                existing = links.get(code, ("", ""))
                links[code] = (
                    preferred(existing[0], name),
                    preferred(existing[1], provenance),
                )

        raw_codes = metadata.get("security_codes")
        if isinstance(raw_codes, (list, tuple)):
            for raw_code in raw_codes:
                code = str(raw_code or "").strip().upper()
                if code:
                    existing = links.get(code, ("", ""))
                    links[code] = (
                        existing[0],
                        preferred(
                            existing[1],
                            str(
                                metadata.get("provider")
                                or "document_metadata"
                            ),
                        ),
                    )

        fallback_code = str(
            metadata.get("ts_code")
            or metadata.get("code")
            or ""
        ).strip().upper()
        if fallback_code:
            existing = links.get(fallback_code, ("", ""))
            links[fallback_code] = (
                preferred(
                    existing[0],
                    str(metadata.get("name") or ""),
                ),
                preferred(
                    existing[1],
                    str(
                        metadata.get("provider")
                        or "document_metadata"
                    ),
                ),
            )
        return tuple(
            (code, *links[code])
            for code in sorted(links)
        )

    def _upsert_document_security_links(
        self,
        connection: sqlite3.Connection,
        *,
        document_id: int,
        document: SourceDocument,
    ) -> int:
        observed_at = _strict_utc_iso(
            document.first_seen_at,
            field="document_security_link.first_seen_at",
        )
        added = 0
        for code, name, provenance in (
            self._document_security_link_values(document)
        ):
            existing = connection.execute(
                """
                SELECT 1 FROM document_security_links
                WHERE document_id=? AND ts_code=?
                """,
                (int(document_id), code),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO document_security_links(
                    document_id, ts_code, name, provenance,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(document_id, ts_code) DO UPDATE SET
                    name=CASE
                      WHEN document_security_links.name=''
                      THEN excluded.name
                      WHEN excluded.name=''
                      THEN document_security_links.name
                      WHEN excluded.name
                           < document_security_links.name
                      THEN excluded.name
                      ELSE document_security_links.name
                    END,
                    provenance=CASE
                      WHEN excluded.provenance
                           < document_security_links.provenance
                      THEN excluded.provenance
                      ELSE document_security_links.provenance
                    END,
                    created_at=min(
                      document_security_links.created_at,
                      excluded.created_at
                    ),
                    updated_at=max(
                      document_security_links.updated_at,
                      excluded.updated_at
                    )
                """,
                (
                    int(document_id),
                    code,
                    name,
                    provenance or "document_metadata",
                    observed_at,
                    observed_at,
                ),
            )
            added += int(existing is None)
            if document.source == "tushare_announcement":
                self._upsert_announcement_catalog(
                    connection,
                    source=document.source,
                    ts_code=code,
                    name=name,
                    provenance=provenance or "document_metadata",
                    observed_at=observed_at,
                )
        if added:
            connection.execute(
                """
                UPDATE documents
                SET link_revision=link_revision+?,
                    status=CASE
                      WHEN status IN ('processed', 'no_event')
                      THEN 'collected'
                      ELSE status
                    END
                WHERE id=?
                """,
                (added, int(document_id)),
            )
        return added

    @staticmethod
    def _upsert_announcement_catalog(
        connection: sqlite3.Connection,
        *,
        source: str,
        ts_code: str,
        name: str,
        provenance: str,
        observed_at: str,
    ) -> bool:
        existing = connection.execute(
            """
            SELECT 1
            FROM announcement_security_catalog
            WHERE source=? AND ts_code=?
            """,
            (source, ts_code),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO announcement_security_catalog(
                source, ts_code, name, provenance,
                first_seen_at, last_seen_at, observations
            ) VALUES(?,?,?,?,?,?,1)
            ON CONFLICT(source, ts_code) DO UPDATE SET
                name=CASE
                  WHEN announcement_security_catalog.name=''
                  THEN excluded.name
                  WHEN excluded.name=''
                  THEN announcement_security_catalog.name
                  WHEN excluded.name
                       < announcement_security_catalog.name
                  THEN excluded.name
                  ELSE announcement_security_catalog.name
                END,
                provenance=CASE
                  WHEN excluded.provenance
                       < announcement_security_catalog.provenance
                  THEN excluded.provenance
                  ELSE announcement_security_catalog.provenance
                END,
                first_seen_at=min(
                  announcement_security_catalog.first_seen_at,
                  excluded.first_seen_at
                ),
                last_seen_at=max(
                  announcement_security_catalog.last_seen_at,
                  excluded.last_seen_at
                ),
                observations=
                  announcement_security_catalog.observations+1
            """,
            (
                source,
                ts_code,
                name,
                provenance,
                observed_at,
                observed_at,
            ),
        )
        inserted = existing is None
        if inserted:
            IntelligenceStore._advance_announcement_catalog_state(
                connection,
                source=source,
                observed_at=observed_at,
            )
        return inserted

    @staticmethod
    def _catalog_hash_and_count(
        connection: sqlite3.Connection,
        *,
        source: str,
    ) -> tuple[str, int]:
        codes = [
            str(row["ts_code"])
            for row in connection.execute(
                """
                SELECT ts_code
                FROM announcement_security_catalog
                WHERE source=?
                ORDER BY ts_code
                """,
                (source,),
            )
        ]
        content_hash = hashlib.sha256(
            json.dumps(
                codes,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return content_hash, len(codes)

    @staticmethod
    def _advance_announcement_catalog_state(
        connection: sqlite3.Connection,
        *,
        source: str,
        observed_at: str,
    ) -> None:
        content_hash, security_count = (
            IntelligenceStore._catalog_hash_and_count(
                connection,
                source=source,
            )
        )
        connection.execute(
            """
            INSERT INTO announcement_catalog_state(
                source, revision, content_hash,
                security_count, updated_at
            ) VALUES(?,1,?,?,?)
            ON CONFLICT(source) DO UPDATE SET
                revision=announcement_catalog_state.revision+1,
                content_hash=excluded.content_hash,
                security_count=excluded.security_count,
                updated_at=excluded.updated_at
            """,
            (
                source,
                content_hash,
                security_count,
                observed_at,
            ),
        )

    def _initialize_announcement_catalog_state(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        sources = {
            str(row["source"])
            for row in connection.execute(
                """
                SELECT source FROM announcement_security_catalog
                UNION
                SELECT source FROM announcement_catalog_state
                """
            )
        }
        for source in sorted(sources):
            content_hash, security_count = (
                self._catalog_hash_and_count(
                    connection,
                    source=source,
                )
            )
            connection.execute(
                """
                INSERT INTO announcement_catalog_state(
                    source, revision, content_hash,
                    security_count, updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(source) DO UPDATE SET
                    revision=MAX(
                      announcement_catalog_state.revision,
                      excluded.revision
                    ),
                    content_hash=excluded.content_hash,
                    security_count=excluded.security_count,
                    updated_at=excluded.updated_at
                """,
                (
                    source,
                    security_count,
                    content_hash,
                    security_count,
                    utc_iso(),
                ),
            )

    def document_security_links(
        self,
        document_id: int,
    ) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT document_id, ts_code, name, provenance,
                       created_at, updated_at
                FROM document_security_links
                WHERE document_id=?
                ORDER BY ts_code
                """,
                (int(document_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_reconstructed_availability(
        self,
        document_id: int,
        *,
        source_recorded_at: str | datetime | None,
        research_available_at: str | datetime | None,
        provenance: str,
    ) -> dict[str, object]:
        if provenance not in RECONSTRUCTED_PROVENANCE:
            raise ValueError(f"unknown_availability_provenance:{provenance}")
        if (
            provenance == "reconstructed_rec_time"
            and source_recorded_at is None
        ):
            raise ValueError(
                "reconstructed_rec_time_source_recorded_at_required"
            )
        normalized_source_recorded_at = (
            _strict_utc_iso(
                source_recorded_at,
                field="source_recorded_at",
            )
            if source_recorded_at is not None
            else None
        )
        read_connection = self.connect()
        try:
            self._bind_historical_cutoff_setting(
                read_connection,
                initialize=False,
            )
            document = read_connection.execute(
                """
                SELECT published_at, first_seen_at
                FROM documents WHERE id=?
                """,
                (int(document_id),),
            ).fetchone()
        finally:
            read_connection.close()
        if document is None:
            raise KeyError(
                f"intelligence_document_not_found:{document_id}"
            )
        published_at = _strict_utc_iso(
            str(document["published_at"]),
            field="stored_document.published_at",
        )
        first_seen_at = _strict_utc_iso(
            str(document["first_seen_at"]),
            field="stored_document.first_seen_at",
        )
        if published_at > self._historical_cutoff:
            return self.document_availability(document_id)

        if provenance == "reconstructed_rec_time":
            if research_available_at is None:
                raise ValueError(
                    "reconstructed_rec_time_research_available_at_required"
                )
            normalized_research_at = _strict_utc_iso(
                research_available_at,
                field="research_available_at",
            )
            if normalized_source_recorded_at != normalized_research_at:
                raise ValueError(
                    "reconstructed_rec_time_timestamp_mismatch"
                )
        else:
            if self._next_market_open_resolver is None:
                raise ValueError(
                    "reconstructed_next_open_resolver_required"
                )
            resolved_research_at = _strict_utc_iso(
                self._next_market_open_resolver(published_at),
                field="next_market_open_resolver",
            )
            if research_available_at is not None:
                supplied_research_at = _strict_utc_iso(
                    research_available_at,
                    field="research_available_at",
                )
                if supplied_research_at != resolved_research_at:
                    raise ValueError(
                        "reconstructed_next_open_timestamp_mismatch"
                    )
            normalized_research_at = resolved_research_at

        reconstructed_times = [
            normalized_research_at,
            *(
                [normalized_source_recorded_at]
                if normalized_source_recorded_at is not None
                else []
            ),
        ]
        if any(
            timestamp < published_at or timestamp > first_seen_at
            for timestamp in reconstructed_times
        ):
            raise ValueError("reconstructed_availability_out_of_bounds")

        with self.connect() as connection:
            self._upsert_reconstructed_availability_row(
                connection,
                document_id=int(document_id),
                source_recorded_at=normalized_source_recorded_at,
                research_available_at=normalized_research_at,
                provenance=provenance,
            )
        return self.document_availability(document_id)

    def _upsert_reconstructed_availability_row(
        self,
        connection: sqlite3.Connection,
        *,
        document_id: int,
        source_recorded_at: str | None,
        research_available_at: str,
        provenance: str,
    ) -> None:
        connection.execute(
            """
                INSERT INTO document_availability(
                    document_id, source_recorded_at, research_available_at,
                    availability_provenance, historical_cutoff, created_at
                ) VALUES(
                    ?, ?, ?, ?,
                    (
                        SELECT value FROM intelligence_settings
                        WHERE key='historical_cutoff'
                    ),
                    ?
                )
                ON CONFLICT(document_id) DO UPDATE SET
                    source_recorded_at=excluded.source_recorded_at,
                    research_available_at=excluded.research_available_at,
                    availability_provenance=excluded.availability_provenance,
                    historical_cutoff=excluded.historical_cutoff
                WHERE
                    (SELECT published_at FROM documents
                     WHERE id=excluded.document_id)
                        <= excluded.historical_cutoff
                    AND excluded.research_available_at >=
                        (SELECT published_at FROM documents
                         WHERE id=excluded.document_id)
                    AND excluded.research_available_at <=
                        (SELECT first_seen_at FROM documents
                         WHERE id=excluded.document_id)
                    AND (
                        excluded.source_recorded_at IS NULL
                        OR (
                            excluded.source_recorded_at >=
                                (SELECT published_at FROM documents
                                 WHERE id=excluded.document_id)
                            AND excluded.source_recorded_at <=
                                (SELECT first_seen_at FROM documents
                                 WHERE id=excluded.document_id)
                        )
                    )
                    AND (
                        CASE excluded.availability_provenance
                            WHEN 'reconstructed_rec_time' THEN 2
                            WHEN 'reconstructed_next_open' THEN 1
                            ELSE 0
                        END
                        >
                        CASE document_availability.availability_provenance
                            WHEN 'reconstructed_rec_time' THEN 2
                            WHEN 'reconstructed_next_open' THEN 1
                            ELSE 0
                        END
                        OR (
                            excluded.availability_provenance
                                = document_availability.availability_provenance
                            AND (
                                excluded.research_available_at
                                    < document_availability.research_available_at
                                OR (
                                    excluded.research_available_at
                                        = document_availability.research_available_at
                                    AND COALESCE(
                                        excluded.source_recorded_at,
                                        '9999-12-31T23:59:59+00:00'
                                    ) < COALESCE(
                                        document_availability.source_recorded_at,
                                        '9999-12-31T23:59:59+00:00'
                                    )
                                )
                            )
                        )
                    )
            """,
            (
                int(document_id),
                source_recorded_at,
                research_available_at,
                provenance,
                utc_iso(),
            ),
        )

    def document_availability(self, document_id: int) -> dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_availability WHERE document_id=?",
                (int(document_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"intelligence_document_not_found:{document_id}")
        return dict(row)

    def insert_event(self, event: MarketEvent) -> bool:
        published_at = _strict_utc_iso(
            event.published_at,
            field="event.published_at",
        )
        effective_at = _strict_utc_iso(
            event.effective_at,
            field="event.effective_at",
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_id, document_id, event_type, direction, strength,
                    confidence, novelty, horizon_days, published_at, effective_at,
                    evidence, extraction_method, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id, event.document_id, event.event_type,
                    float(event.direction), float(event.strength), float(event.confidence),
                    float(event.novelty), int(event.horizon_days),
                    published_at, effective_at,
                    event.evidence, event.extraction_method,
                    json.dumps(event.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )
            related_event_ids = {
                str(row["event_id"])
                for row in connection.execute(
                    """
                    SELECT event_id FROM events
                    WHERE document_id=? AND event_type=?
                    """,
                    (int(event.document_id), event.event_type),
                )
            }
            for event_id in related_event_ids:
                for entity in event.entities:
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO event_entities(
                            event_id, entity_type, entity_id,
                            entity_name, industry, confidence
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            event_id,
                            str(
                                entity.get("entity_type")
                                or "security"
                            ),
                            str(entity.get("entity_id") or ""),
                            str(entity.get("entity_name") or ""),
                            str(entity.get("industry") or ""),
                            float(entity.get("confidence") or 0.0),
                        ),
                    )
            return cursor.rowcount == 1

    def start_run(
        self,
        run_id: str,
        source: str,
        cursor: str | None = None,
        *,
        owner: str = "",
        lease_seconds: int = 300,
        now: str | datetime | None = None,
        provisional_retry_day: str | None = None,
    ) -> dict[str, object]:
        now_iso = _strict_utc_iso(
            now or datetime.now(timezone.utc),
            field="ingestion_claim_now",
        )
        lease_until = _strict_utc_iso(
            datetime.fromisoformat(now_iso)
            + timedelta(seconds=max(1, int(lease_seconds))),
            field="ingestion_lease_until",
        )
        normalized_owner = str(owner or run_id).strip()
        if not normalized_owner:
            raise ValueError("intelligence_ingestion_owner_missing")
        normalized_floor = self._normalize_retry_day(
            provisional_retry_day
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                """
                SELECT generation, owner, lease_until
                FROM source_ingestion_leases
                WHERE source=?
                """,
                (source,),
            ).fetchone()
            if (
                lease is not None
                and str(lease["lease_until"]) > now_iso
                and str(lease["owner"]) != normalized_owner
            ):
                raise IngestionLeaseBusy(
                    f"intelligence_ingestion_lease_busy:{source}"
                )
            generation = (
                int(lease["generation"]) + 1
                if lease is not None
                else 1
            )
            connection.execute(
                """
                INSERT INTO source_ingestion_leases(
                    source, generation, owner, lease_until, updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(source) DO UPDATE SET
                    generation=excluded.generation,
                    owner=excluded.owner,
                    lease_until=excluded.lease_until,
                    updated_at=excluded.updated_at
                """,
                (
                    source,
                    generation,
                    normalized_owner,
                    lease_until,
                    now_iso,
                ),
            )
            cursor_row = connection.execute(
                """
                SELECT cursor
                FROM source_cursors
                WHERE source=?
                """,
                (source,),
            ).fetchone()
            retry_row = connection.execute(
                """
                SELECT unresolved_day, reason
                FROM source_retry_windows
                WHERE source=?
                """,
                (source,),
            ).fetchone()
            effective_cursor = str(
                cursor
                or (
                    cursor_row["cursor"]
                    if cursor_row is not None
                    else ""
                )
            )
            retry_unresolved_day = ""
            retry_reason = ""
            if retry_row is not None:
                retry_unresolved_day = str(
                    retry_row["unresolved_day"]
                )
                retry_reason = str(retry_row["reason"])
                effective_cursor = self._retry_day_cursor(
                    retry_unresolved_day
                )
                self._upsert_source_retry_window(
                    connection,
                    source=source,
                    unresolved_day=retry_unresolved_day,
                    reason=retry_reason,
                    generation=generation,
                    owner=normalized_owner,
                    observed_at=now_iso,
                    preserve_earliest=True,
                )
            elif not effective_cursor and normalized_floor:
                retry_unresolved_day = normalized_floor
                retry_reason = "provisional_scan_floor"
                effective_cursor = self._retry_day_cursor(
                    retry_unresolved_day
                )
                self._upsert_source_retry_window(
                    connection,
                    source=source,
                    unresolved_day=retry_unresolved_day,
                    reason=retry_reason,
                    generation=generation,
                    owner=normalized_owner,
                    observed_at=now_iso,
                    preserve_earliest=True,
                )
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, source, started_at, status, cursor_in,
                    generation, owner
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    source,
                    now_iso,
                    "running",
                    effective_cursor,
                    generation,
                    normalized_owner,
                ),
            )
        return {
            "run_id": run_id,
            "source": source,
            "generation": generation,
            "owner": normalized_owner,
            "lease_until": lease_until,
            "cursor": effective_cursor,
            "retry_unresolved_day": retry_unresolved_day,
            "retry_reason": retry_reason,
        }

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        cursor: str = "",
        fetched: int = 0,
        inserted: int = 0,
        error: str = "",
        retry_unresolved_day: str | None = None,
        retry_reason: str = "",
        retry_window_scanned: bool = False,
        retry_covered_floor: str | None = None,
        generation: int | None = None,
        owner: str | None = None,
    ) -> None:
        normalized_retry_day = self._normalize_retry_day(
            retry_unresolved_day
        )
        normalized_covered_floor = self._normalize_retry_day(
            retry_covered_floor
        )
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT source, status, generation, owner
                FROM ingestion_runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(
                    f"intelligence_ingestion_run_missing:{run_id}"
                )
            source = str(run["source"])
            expected_generation = (
                int(run["generation"])
                if generation is None
                else int(generation)
            )
            expected_owner = str(
                run["owner"] if owner is None else owner
            )
            lease = connection.execute(
                """
                SELECT generation, owner
                FROM source_ingestion_leases
                WHERE source=?
                """,
                (source,),
            ).fetchone()
            if (
                str(run["status"]) != "running"
                or int(run["generation"]) != expected_generation
                or str(run["owner"]) != expected_owner
                or lease is None
                or int(lease["generation"]) != expected_generation
                or str(lease["owner"]) != expected_owner
            ):
                raise IngestionGenerationConflict(
                    "intelligence_ingestion_generation_conflict:"
                    f"{source}:{run_id}"
                )
            updated = connection.execute(
                """
                UPDATE ingestion_runs SET finished_at=?, status=?, cursor_out=?,
                    fetched=?, inserted=?, error=? WHERE run_id=?
                    AND status='running' AND generation=? AND owner=?
                """,
                (
                    now,
                    status,
                    cursor,
                    fetched,
                    inserted,
                    error[:500],
                    run_id,
                    expected_generation,
                    expected_owner,
                ),
            )
            if updated.rowcount != 1:
                raise IngestionGenerationConflict(
                    "intelligence_ingestion_generation_conflict:"
                    f"{source}:{run_id}"
                )
            if normalized_retry_day:
                self._upsert_source_retry_window(
                    connection,
                    source=source,
                    unresolved_day=normalized_retry_day,
                    reason=str(
                        retry_reason or "source_incomplete"
                    )[:500],
                    generation=expected_generation,
                    owner=expected_owner,
                    observed_at=now,
                    preserve_earliest=False,
                )
            elif retry_window_scanned and status == "success":
                retry = connection.execute(
                    """
                    SELECT unresolved_day, generation, owner
                    FROM source_retry_windows
                    WHERE source=?
                    """,
                    (source,),
                ).fetchone()
                if retry is not None and (
                    not normalized_covered_floor
                    or str(retry["unresolved_day"])
                    != normalized_covered_floor
                    or int(retry["generation"])
                    != expected_generation
                    or str(retry["owner"]) != expected_owner
                ):
                    raise IngestionGenerationConflict(
                        "intelligence_ingestion_retry_clear_conflict:"
                        f"{source}:{run_id}"
                    )
                deleted = connection.execute(
                    """
                    DELETE FROM source_retry_windows
                    WHERE source=? AND generation=? AND owner=?
                      AND unresolved_day=?
                    """,
                    (
                        source,
                        expected_generation,
                        expected_owner,
                        normalized_covered_floor,
                    ),
                )
                if (
                    retry is not None
                    and deleted.rowcount != 1
                ):
                    raise IngestionGenerationConflict(
                        "intelligence_ingestion_retry_clear_conflict"
                    )
            remaining_retry = connection.execute(
                """
                SELECT 1
                FROM source_retry_windows
                WHERE source=?
                """,
                (source,),
            ).fetchone()
            if (
                status == "success"
                and fetched > 0
                and cursor
                and remaining_retry is not None
            ):
                raise IngestionGenerationConflict(
                    "intelligence_ingestion_retry_unresolved:"
                    f"{source}:{run_id}"
                )
            if status == "success" and fetched > 0 and cursor:
                connection.execute(
                    """
                    INSERT INTO source_cursors(source, cursor, updated_at)
                    SELECT source, ?, ? FROM ingestion_runs WHERE run_id=?
                    ON CONFLICT(source) DO UPDATE SET
                        cursor=CASE
                            WHEN excluded.cursor > source_cursors.cursor
                            THEN excluded.cursor
                            ELSE source_cursors.cursor
                        END,
                        updated_at=CASE
                            WHEN excluded.cursor > source_cursors.cursor
                            THEN excluded.updated_at
                            ELSE source_cursors.updated_at
                        END
                    """,
                    (cursor, now, run_id),
                )
            released = connection.execute(
                """
                UPDATE source_ingestion_leases
                SET lease_until=?, updated_at=?
                WHERE source=? AND generation=? AND owner=?
                """,
                (
                    now,
                    now,
                    source,
                    expected_generation,
                    expected_owner,
                ),
            )
            if released.rowcount != 1:
                raise IngestionGenerationConflict(
                    "intelligence_ingestion_generation_conflict:"
                    f"{source}:{run_id}"
                )

    @staticmethod
    def _normalize_retry_day(value: str | None) -> str:
        if not value:
            return ""
        try:
            return datetime.strptime(
                str(value),
                "%Y-%m-%d",
            ).date().isoformat()
        except ValueError as exc:
            raise ValueError(
                "intelligence_source_retry_day_invalid"
            ) from exc

    @staticmethod
    def _retry_day_cursor(value: str) -> str:
        retry_day = datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
        return datetime.combine(
            retry_day,
            datetime.min.time(),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).isoformat(timespec="seconds")

    @staticmethod
    def _upsert_source_retry_window(
        connection: sqlite3.Connection,
        *,
        source: str,
        unresolved_day: str,
        reason: str,
        generation: int,
        owner: str,
        observed_at: str,
        preserve_earliest: bool,
    ) -> None:
        connection.execute(
            """
            INSERT INTO source_retry_windows(
                source, unresolved_day, reason,
                first_seen_at, last_seen_at, attempts,
                generation, owner
            ) VALUES(?,?,?,?,?,1,?,?)
            ON CONFLICT(source) DO UPDATE SET
                unresolved_day=CASE
                  WHEN ?=1
                       AND source_retry_windows.unresolved_day
                           < excluded.unresolved_day
                  THEN source_retry_windows.unresolved_day
                  ELSE excluded.unresolved_day
                END,
                reason=CASE
                  WHEN ?=1
                       AND source_retry_windows.unresolved_day
                           < excluded.unresolved_day
                  THEN source_retry_windows.reason
                  ELSE excluded.reason
                END,
                first_seen_at=min(
                  source_retry_windows.first_seen_at,
                  excluded.first_seen_at
                ),
                last_seen_at=max(
                  source_retry_windows.last_seen_at,
                  excluded.last_seen_at
                ),
                attempts=source_retry_windows.attempts+1,
                generation=excluded.generation,
                owner=excluded.owner
            """,
            (
                source,
                unresolved_day,
                reason,
                observed_at,
                observed_at,
                int(generation),
                owner,
                int(preserve_earliest),
                int(preserve_earliest),
            ),
        )

    def cursor(self, source: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM source_cursors WHERE source=?", (source,)
            ).fetchone()
        return str(row["cursor"]) if row else ""

    def source_retry_window(
        self,
        source: str,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT source, unresolved_day, reason, first_seen_at,
                       last_seen_at, attempts, generation, owner
                FROM source_retry_windows
                WHERE source=?
                """,
                (source,),
            ).fetchone()
        return dict(row) if row is not None else None

    def backfill_partition(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _load_backfill_partition_job(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        source: str,
        partition_start: str,
        partition_end: str,
        generation: int | None = None,
    ) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT source, start_date, end_date, config_hash,
                   exact_config_hash, compatibility_hash,
                   request_limit, completion_strategy_version,
                   generation
            FROM backfill_jobs
            WHERE job_id=?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"intelligence_backfill_job_missing:{job_id}"
            )
        if str(row["source"]) != source:
            raise ValueError(
                "intelligence_backfill_job_source_mismatch"
            )
        if (
            partition_start < str(row["start_date"])
            or partition_end > str(row["end_date"])
        ):
            raise ValueError(
                "intelligence_backfill_partition_outside_job_range"
            )
        if (
            int(row["completion_strategy_version"])
            != BACKFILL_COMPLETION_STRATEGY_VERSION
        ):
            raise BackfillConfigurationConflict(
                "intelligence_backfill_job_strategy_conflict"
            )
        if (
            generation is not None
            and int(row["generation"]) != int(generation)
        ):
            raise BackfillGenerationConflict(
                "intelligence_backfill_job_generation_conflict"
            )
        job = dict(row)
        job["exact_config_hash"] = str(
            row["exact_config_hash"] or row["config_hash"]
        )
        job["compatibility_hash"] = str(
            row["compatibility_hash"]
            or job["exact_config_hash"]
        )
        return job

    @staticmethod
    def _partition_evidence_status(
        partition: sqlite3.Row,
        job: dict[str, object],
    ) -> str:
        evidence_hash = str(
            partition["evidence_config_hash"] or ""
        )
        if not evidence_hash:
            return "exact"
        if evidence_hash == str(job["exact_config_hash"]):
            return "exact"
        evidence_compatibility_hash = str(
            partition["evidence_compatibility_hash"] or ""
        )
        evidence_request_limit = int(
            partition["evidence_request_limit"] or 0
        )
        if (
            evidence_compatibility_hash
            and evidence_compatibility_hash
            == str(job["compatibility_hash"])
            and evidence_request_limit > 0
            and int(job["request_limit"]) > evidence_request_limit
        ):
            return "compatible_limit_upgrade"
        return "needs_revalidation"

    def _record_backfill_partition_reference(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        source: str,
        partition_start: str,
        partition_end: str,
        job: dict[str, object],
        partition: sqlite3.Row,
        observed_at: str,
    ) -> tuple[str, sqlite3.Row]:
        if not str(partition["evidence_config_hash"] or ""):
            connection.execute(
                """
                UPDATE backfill_partitions
                SET evidence_config_hash=?,
                    evidence_compatibility_hash=?,
                    evidence_request_limit=?,
                    job_id=''
                WHERE source=? AND partition_start=?
                  AND partition_end=? AND evidence_config_hash=''
                """,
                (
                    str(job["exact_config_hash"]),
                    str(job["compatibility_hash"]),
                    int(job["request_limit"]),
                    source,
                    partition_start,
                    partition_end,
                ),
            )
            partition = connection.execute(
                """
                SELECT *
                FROM backfill_partitions
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
        evidence_status = self._partition_evidence_status(
            partition,
            job,
        )
        connection.execute(
            """
            INSERT INTO backfill_job_partition_refs(
                job_id, source, partition_start, partition_end,
                created_at, evidence_status, association_provenance
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(
              job_id, source, partition_start, partition_end
            ) DO UPDATE SET
                evidence_status=excluded.evidence_status,
                association_provenance=CASE
                  WHEN backfill_job_partition_refs
                         .association_provenance='range_inferred'
                  THEN excluded.association_provenance
                  ELSE backfill_job_partition_refs
                         .association_provenance
                END
            """,
            (
                job_id,
                source,
                partition_start,
                partition_end,
                observed_at,
                evidence_status,
                "runtime_verified",
            ),
        )
        has_incompatible_reference = connection.execute(
            """
            SELECT EXISTS(
              SELECT 1
              FROM backfill_job_partition_refs ref
              JOIN backfill_partitions partition
                ON partition.source=ref.source
               AND partition.partition_start=ref.partition_start
               AND partition.partition_end=ref.partition_end
              WHERE ref.job_id=?
                AND (
                  ref.evidence_status='needs_revalidation'
                  OR partition.status<>'complete'
                  OR partition.completion_strategy_version<>?
                )
            ) AS present
            """,
            (
                job_id,
                BACKFILL_COMPLETION_STRATEGY_VERSION,
            ),
        ).fetchone()["present"]
        connection.execute(
            """
            UPDATE backfill_jobs
            SET evidence_status=?,
                status=CASE
                  WHEN ?=1 THEN 'partial'
                  WHEN status='partial' THEN 'running'
                  ELSE status
                END,
                updated_at=?
            WHERE job_id=?
            """,
            (
                (
                    "needs_revalidation"
                    if has_incompatible_reference
                    else "current"
                ),
                int(bool(has_incompatible_reference)),
                observed_at,
                job_id,
            ),
        )
        return evidence_status, partition

    @staticmethod
    def _catalog_state_in_transaction(
        connection: sqlite3.Connection,
        *,
        source: str,
    ) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT revision, content_hash, security_count, updated_at
            FROM announcement_catalog_state
            WHERE source=?
            """,
            (source,),
        ).fetchone()
        if row is not None:
            return dict(row)
        return {
            "revision": 0,
            "content_hash": hashlib.sha256(b"[]").hexdigest(),
            "security_count": 0,
            "updated_at": "",
        }

    def _reopen_split_ancestors_for_invalid_probe_descendants(
        self,
        connection: sqlite3.Connection,
        *,
        source: str,
        partition_start: str,
        partition_end: str,
        partition: sqlite3.Row,
        observed_at: str,
    ) -> sqlite3.Row:
        invalid_descendants = connection.execute(
            """
            SELECT *
            FROM backfill_partitions
            WHERE source=?
              AND partition_start>=? AND partition_end<=?
              AND probe_manifest_version>=1
              AND (
                status<>'complete'
                OR completion_strategy_version<>?
              )
            ORDER BY partition_start, partition_end
            """,
            (
                source,
                partition_start,
                partition_end,
                BACKFILL_COMPLETION_STRATEGY_VERSION,
            ),
        ).fetchall()
        if not invalid_descendants:
            return partition

        affected: set[tuple[str, str]] = set()
        for descendant in invalid_descendants:
            descendant_start = str(descendant["partition_start"])
            descendant_end = str(descendant["partition_end"])
            ancestors = connection.execute(
                """
                SELECT partition_start, partition_end
                FROM backfill_partitions
                WHERE source=?
                  AND partition_start<=? AND partition_end>=?
                  AND (
                    partition_start<>? OR partition_end<>?
                  )
                  AND status='complete'
                  AND completion_basis='split_children'
                ORDER BY partition_start, partition_end DESC
                """,
                (
                    source,
                    descendant_start,
                    descendant_end,
                    descendant_start,
                    descendant_end,
                ),
            ).fetchall()
            if not ancestors:
                continue
            affected.add((descendant_start, descendant_end))
            connection.execute(
                """
                UPDATE backfill_partition_verification_state
                SET stable_rounds=0, last_probe_hash='',
                    last_new_documents=0, last_new_security_codes=0,
                    updated_at=?
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (
                    observed_at,
                    source,
                    descendant_start,
                    descendant_end,
                ),
            )
            for ancestor in ancestors:
                ancestor_start = str(ancestor["partition_start"])
                ancestor_end = str(ancestor["partition_end"])
                cursor = connection.execute(
                    """
                    UPDATE backfill_partitions
                    SET status='failed_overflow',
                        error='split_descendant_revalidation',
                        completion_strategy_version=0,
                        completion_basis='split_children_revalidation',
                        updated_at=?
                    WHERE source=? AND partition_start=?
                      AND partition_end=? AND status='complete'
                      AND completion_basis='split_children'
                    """,
                    (
                        observed_at,
                        source,
                        ancestor_start,
                        ancestor_end,
                    ),
                )
                if cursor.rowcount == 1:
                    affected.add((ancestor_start, ancestor_end))

        if not affected:
            return partition
        for affected_start, affected_end in affected:
            connection.execute(
                """
                UPDATE backfill_job_partition_refs
                SET evidence_status='needs_revalidation'
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, affected_start, affected_end),
            )
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status='partial',
                evidence_status='needs_revalidation',
                updated_at=?
            WHERE EXISTS (
              SELECT 1
              FROM backfill_job_partition_refs ref
              WHERE ref.job_id=backfill_jobs.job_id
                AND ref.evidence_status='needs_revalidation'
            )
            """,
            (observed_at,),
        )
        return connection.execute(
            """
            SELECT *
            FROM backfill_partitions
            WHERE source=? AND partition_start=? AND partition_end=?
            """,
            (source, partition_start, partition_end),
        ).fetchone()

    def _reopen_saturated_partition_for_catalog_growth(
        self,
        connection: sqlite3.Connection,
        *,
        source: str,
        partition_start: str,
        partition_end: str,
        partition: sqlite3.Row,
        request_limit: int,
        observed_at: str,
    ) -> sqlite3.Row:
        if str(partition["status"]) not in {
            "complete",
            "failed_overflow",
        }:
            return partition
        catalog = self._catalog_state_in_transaction(
            connection,
            source=source,
        )
        current_revision = int(catalog["revision"])
        current_hash = str(catalog["content_hash"])
        stale_leaves = connection.execute(
            """
            SELECT *
            FROM backfill_partitions
            WHERE source=?
              AND partition_start>=? AND partition_end<=?
              AND probe_manifest_version>=1
              AND (
                catalog_revision<>?
                OR catalog_hash<>?
              )
            ORDER BY partition_start, partition_end
            """,
            (
                source,
                partition_start,
                partition_end,
                current_revision,
                current_hash,
            ),
        ).fetchall()
        if not stale_leaves:
            return partition

        affected: set[tuple[str, str]] = set()
        for leaf in stale_leaves:
            leaf_start = str(leaf["partition_start"])
            leaf_end = str(leaf["partition_end"])
            leaf_revision = int(leaf["catalog_revision"] or 0)
            leaf_hash = str(leaf["catalog_hash"] or "")
            ancestors = connection.execute(
                """
                SELECT partition_start, partition_end, status,
                       completion_basis
                FROM backfill_partitions
                WHERE source=?
                  AND partition_start<=? AND partition_end>=?
                  AND (
                    partition_start<>? OR partition_end<>?
                  )
                  AND completion_basis IN (
                    'split_children',
                    'split_children_revalidation'
                  )
                ORDER BY partition_start, partition_end DESC
                """,
                (
                    source,
                    leaf_start,
                    leaf_end,
                    leaf_start,
                    leaf_end,
                ),
            ).fetchall()
            leaf_was_complete = str(leaf["status"]) == "complete"
            masked_ancestor = any(
                str(ancestor["status"]) != "failed_overflow"
                or str(ancestor["completion_basis"])
                != "split_children_revalidation"
                for ancestor in ancestors
            )
            if not leaf_was_complete and not masked_ancestor:
                continue
            if leaf_was_complete:
                cursor = connection.execute(
                    """
                    UPDATE backfill_partitions
                    SET status='failed_overflow',
                        error='catalog_growth_revalidation',
                        request_limit=CASE
                          WHEN request_limit > ? THEN request_limit ELSE ? END,
                        completion_strategy_version=0,
                        completion_basis=
                          'saturated_catalog_revalidation',
                        updated_at=?
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                      AND status='complete'
                      AND probe_manifest_version>=1
                    """,
                    (
                        max(1, int(request_limit)),
                        max(1, int(request_limit)),
                        observed_at,
                        source,
                        leaf_start,
                        leaf_end,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
            affected.add((leaf_start, leaf_end))
            self._expand_partition_universe_from_catalog(
                connection,
                source=source,
                partition_start=leaf_start,
                partition_end=leaf_end,
            )
            connection.execute(
                """
                UPDATE backfill_partition_verification_state
                SET stable_rounds=0, last_probe_hash='',
                    last_new_documents=0, last_new_security_codes=0,
                    updated_at=?
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (
                    observed_at,
                    source,
                    leaf_start,
                    leaf_end,
                ),
            )
            for ancestor in ancestors:
                ancestor_start = str(ancestor["partition_start"])
                ancestor_end = str(ancestor["partition_end"])
                affected.add((ancestor_start, ancestor_end))
                connection.execute(
                    """
                    UPDATE backfill_partitions
                    SET status='failed_overflow',
                        error='split_descendant_catalog_growth',
                        completion_strategy_version=0,
                        completion_basis='split_children_revalidation',
                        catalog_hash=CASE
                          WHEN (
                            catalog_revision=0 OR ?<catalog_revision
                          ) AND ?<>'' THEN ?
                          WHEN ?=catalog_revision
                            AND catalog_hash='' AND ?<>'' THEN ?
                          ELSE catalog_hash
                        END,
                        catalog_revision=CASE
                          WHEN catalog_revision=0 THEN ?
                          ELSE min(catalog_revision, ?)
                        END,
                        updated_at=?
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                    """,
                    (
                        leaf_revision,
                        leaf_hash,
                        leaf_hash,
                        leaf_revision,
                        leaf_hash,
                        leaf_hash,
                        leaf_revision,
                        leaf_revision,
                        observed_at,
                        source,
                        ancestor_start,
                        ancestor_end,
                    ),
                )

        for affected_start, affected_end in affected:
            connection.execute(
                """
                UPDATE backfill_job_partition_refs
                SET evidence_status='needs_revalidation'
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, affected_start, affected_end),
            )
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status='partial',
                evidence_status='needs_revalidation',
                updated_at=?
            WHERE EXISTS (
              SELECT 1
              FROM backfill_job_partition_refs ref
              WHERE ref.job_id=backfill_jobs.job_id
                AND ref.evidence_status='needs_revalidation'
            )
            """,
            (observed_at,),
        )
        return connection.execute(
            """
            SELECT *
            FROM backfill_partitions
            WHERE source=? AND partition_start=? AND partition_end=?
            """,
            (source, partition_start, partition_end),
        ).fetchone()

    def _revalidate_backfill_partition_tree(
        self,
        connection: sqlite3.Connection,
        *,
        source: str,
        partition_start: str,
        partition_end: str,
        partition: sqlite3.Row,
        request_limit: int,
        observed_at: str,
    ) -> sqlite3.Row:
        partition = self._reopen_saturated_partition_for_catalog_growth(
            connection,
            source=source,
            partition_start=partition_start,
            partition_end=partition_end,
            partition=partition,
            request_limit=request_limit,
            observed_at=observed_at,
        )
        return (
            self._reopen_split_ancestors_for_invalid_probe_descendants(
                connection,
                source=source,
                partition_start=partition_start,
                partition_end=partition_end,
                partition=partition,
                observed_at=observed_at,
            )
        )

    def reference_backfill_partition(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        job_id: str,
        job_generation: int | None = None,
    ) -> dict[str, object]:
        normalized_job_id = str(job_id).strip()
        if not normalized_job_id:
            raise ValueError("intelligence_backfill_job_missing")
        conflict = False
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._load_backfill_partition_job(
                connection,
                job_id=normalized_job_id,
                source=source,
                partition_start=partition_start,
                partition_end=partition_end,
                generation=job_generation,
            )
            row = connection.execute(
                """
                SELECT *
                FROM backfill_partitions
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if row is None:
                raise KeyError(
                    "intelligence_backfill_parent_partition_missing"
                )
            now = utc_iso()
            evidence_status, row = (
                self._record_backfill_partition_reference(
                    connection,
                    job_id=normalized_job_id,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                    job=job,
                    partition=row,
                    observed_at=now,
                )
            )
            conflict = evidence_status == "needs_revalidation"
            if not conflict:
                row = (
                    self._revalidate_backfill_partition_tree(
                        connection,
                        source=source,
                        partition_start=partition_start,
                        partition_end=partition_end,
                        partition=row,
                        request_limit=int(job["request_limit"]),
                        observed_at=now,
                    )
                )
            result = dict(row)
        if conflict:
            raise BackfillConfigurationConflict(
                "intelligence_backfill_evidence_revalidation_required:"
                f"{source}:{partition_start}:{partition_end}"
            )
        return result

    def start_backfill_partition(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        resume: bool,
        request_limit: int = 0,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
        job_id: str = "",
        job_generation: int | None = None,
    ) -> dict[str, object]:
        now_iso = _strict_utc_iso(
            now or datetime.now(timezone.utc),
            field="backfill_claim_now",
        )
        normalized_job_id = str(job_id).strip()
        conflict = False
        result: dict[str, object] | None = None
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job: dict[str, object] | None = None
            evidence_config_hash = ""
            evidence_compatibility_hash = ""
            evidence_request_limit = 0
            if normalized_job_id:
                job = self._load_backfill_partition_job(
                    connection,
                    job_id=normalized_job_id,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                    generation=job_generation,
                )
                if int(job["request_limit"]) != max(
                    0,
                    int(request_limit),
                ):
                    raise BackfillConfigurationConflict(
                        "intelligence_backfill_job_request_limit_conflict"
                    )
                evidence_config_hash = str(
                    job["exact_config_hash"]
                )
                evidence_compatibility_hash = str(
                    job["compatibility_hash"]
                )
                evidence_request_limit = int(
                    job["request_limit"]
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO backfill_partitions(
                    source, partition_start, partition_end, request_limit,
                    status, updated_at, job_id, evidence_config_hash,
                    evidence_compatibility_hash, evidence_request_limit
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source,
                    partition_start,
                    partition_end,
                    max(0, int(request_limit)),
                    "pending",
                    now_iso,
                    "",
                    evidence_config_hash,
                    evidence_compatibility_hash,
                    evidence_request_limit,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if normalized_job_id and job is not None:
                evidence_status, row = (
                    self._record_backfill_partition_reference(
                        connection,
                        job_id=normalized_job_id,
                        source=source,
                        partition_start=partition_start,
                        partition_end=partition_end,
                        job=job,
                        partition=row,
                        observed_at=now_iso,
                    )
                )
                conflict = evidence_status == "needs_revalidation"
                if not conflict:
                    row = (
                        self
                        ._revalidate_backfill_partition_tree(
                            connection,
                            source=source,
                            partition_start=partition_start,
                            partition_end=partition_end,
                            partition=row,
                            request_limit=int(job["request_limit"]),
                            observed_at=now_iso,
                        )
                    )
            if conflict:
                result = dict(row)
            elif (
                row["status"] == "complete"
                and int(row["completion_strategy_version"])
                == BACKFILL_COMPLETION_STRATEGY_VERSION
            ):
                result = dict(row)
                result["generation"] = int(row["attempts"])
            else:
                self._assert_claimable(
                    row,
                    now_iso=now_iso,
                    lease_seconds=lease_seconds,
                    resource=(
                        f"partition:{source}:{partition_start}:"
                        f"{partition_end}"
                    ),
                )
                reference_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM backfill_job_partition_refs
                    WHERE source=? AND partition_start=? AND partition_end=?
                    """,
                    (source, partition_start, partition_end),
                ).fetchone()["count"]
                should_reset = (
                    (
                        not resume
                        and int(reference_count or 0) <= 1
                    )
                    or (
                        row["status"] == "complete"
                        and int(row["completion_strategy_version"])
                        != BACKFILL_COMPLETION_STRATEGY_VERSION
                    )
                    or (
                        row["status"] == "failed_overflow"
                        and int(row["request_limit"])
                        != max(0, int(request_limit))
                        and int(row["probe_manifest_version"]) < 1
                    )
                )
                if should_reset:
                    connection.execute(
                        """
                        UPDATE backfill_partitions
                        SET next_offset=0, fetched=0, inserted=0,
                            b_share_filtered=0, status='pending', error='',
                            request_limit=?, updated_at=?,
                            completion_strategy_version=0,
                            probe_manifest_version=0,
                            job_id=''
                        WHERE source=? AND partition_start=?
                          AND partition_end=?
                        """,
                        (
                            max(0, int(request_limit)),
                            now_iso,
                            source,
                            partition_start,
                            partition_end,
                        ),
                    )
                cursor = connection.execute(
                    """
                    UPDATE backfill_partitions
                    SET status='running', attempts=attempts+1, error='',
                        request_limit=?, updated_at=?,
                        job_id=''
                    WHERE source=? AND partition_start=?
                      AND partition_end=? AND attempts=?
                    """,
                    (
                        max(0, int(request_limit)),
                        now_iso,
                        source,
                        partition_start,
                        partition_end,
                        int(row["attempts"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise BackfillGenerationConflict(
                        "intelligence_backfill_partition_claim_conflict"
                    )
                row = connection.execute(
                    """
                    SELECT * FROM backfill_partitions
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                    """,
                    (source, partition_start, partition_end),
                ).fetchone()
                result = dict(row)
                result["generation"] = int(row["attempts"])
        if conflict:
            raise BackfillConfigurationConflict(
                "intelligence_backfill_evidence_revalidation_required:"
                f"{source}:{partition_start}:{partition_end}"
            )
        if result is None:
            raise RuntimeError(
                "intelligence_backfill_partition_claim_missing"
            )
        return result

    def record_backfill_page(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        next_offset: int,
        fetched: int,
        inserted: int,
        b_share_filtered: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, attempts, next_offset
                FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            self._assert_generation(
                row,
                generation=generation,
                resource="partition",
            )
            if int(next_offset) < int(row["next_offset"]):
                raise BackfillProgressRegression(
                    "intelligence_backfill_partition_progress_regression"
                )
            cursor = connection.execute(
                """
                UPDATE backfill_partitions
                SET next_offset=?, fetched=fetched+?, inserted=inserted+?,
                    b_share_filtered=b_share_filtered+?, updated_at=?
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND status='running' AND attempts=?
                """,
                (
                    max(0, int(next_offset)),
                    max(0, int(fetched)),
                    max(0, int(inserted)),
                    max(0, int(b_share_filtered)),
                    utc_iso(),
                    source,
                    partition_start,
                    partition_end,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_partition_record_conflict"
                )

    def finish_backfill_partition(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        status: str,
        error: str = "",
    ) -> None:
        if status not in BACKFILL_PARTITION_STATUSES - {"pending", "running"}:
            raise ValueError(
                f"intelligence_backfill_status_invalid:{status}"
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            catalog = self._catalog_state_in_transaction(
                connection,
                source=source,
            )
            cursor = connection.execute(
                """
                UPDATE backfill_partitions
                SET status=?, error=?, updated_at=?,
                    completion_strategy_version=?,
                    completion_basis=CASE
                      WHEN ?<>'complete' THEN completion_basis
                      WHEN ?='split_complete' THEN 'split_children'
                      ELSE 'direct_complete'
                    END,
                    catalog_revision=CASE
                      WHEN ?='complete' AND ?='split_complete' THEN ?
                      ELSE catalog_revision
                    END,
                    catalog_hash=CASE
                      WHEN ?='complete' AND ?='split_complete' THEN ?
                      ELSE catalog_hash
                    END
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND status='running' AND attempts=?
                """,
                (
                    status,
                    str(error)[:500],
                    utc_iso(),
                    (
                        BACKFILL_COMPLETION_STRATEGY_VERSION
                        if status == "complete"
                        else 0
                    ),
                    status,
                    str(error)[:500],
                    status,
                    str(error)[:500],
                    int(catalog["revision"]),
                    status,
                    str(error)[:500],
                    str(catalog["content_hash"]),
                    source,
                    partition_start,
                    partition_end,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_partition_finish_conflict"
                )

    @staticmethod
    def _assert_claimable(
        row: sqlite3.Row,
        *,
        now_iso: str,
        lease_seconds: int,
        resource: str,
    ) -> None:
        if row["status"] != "running":
            return
        updated_at = datetime.fromisoformat(
            str(row["updated_at"]).replace("Z", "+00:00")
        )
        claim_time = datetime.fromisoformat(now_iso)
        if updated_at > claim_time - timedelta(
            seconds=max(1, int(lease_seconds))
        ):
            raise BackfillLeaseBusy(
                f"intelligence_backfill_lease_busy:{resource}"
            )

    @staticmethod
    def _assert_generation(
        row: sqlite3.Row | None,
        *,
        generation: int,
        resource: str,
    ) -> None:
        if (
            row is None
            or row["status"] != "running"
            or int(row["attempts"]) != int(generation)
        ):
            raise BackfillGenerationConflict(
                f"intelligence_backfill_generation_conflict:{resource}"
            )

    @staticmethod
    def _assert_backfill_job_reference(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> None:
        if not job_id:
            return
        row = connection.execute(
            """
            SELECT evidence_status
            FROM backfill_job_partition_refs
            WHERE job_id=? AND source=?
              AND partition_start=? AND partition_end=?
            """,
            (
                job_id,
                source,
                partition_start,
                partition_end,
            ),
        ).fetchone()
        if (
            row is None
            or str(row["evidence_status"]) == "needs_revalidation"
        ):
            raise BackfillGenerationConflict(
                "intelligence_backfill_partition_job_conflict"
            )

    def backfill_partition_count(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            if status not in BACKFILL_PARTITION_STATUSES:
                raise ValueError(
                    f"intelligence_backfill_status_invalid:{status}"
                )
            clauses.append("status=?")
            params.append(status)
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        query = "SELECT COUNT(*) AS count FROM backfill_partitions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return int(row["count"])

    def ensure_backfill_job(
        self,
        source: str,
        *,
        start_date: str,
        end_date: str,
        config_hash: str,
        compatibility_hash: str | None = None,
        config_json: str | None = None,
        request_limit: int,
        verification_required: int,
    ) -> dict[str, object]:
        if start_date > end_date:
            raise ValueError(
                "intelligence_backfill_job_range_invalid"
            )
        if int(request_limit) < 1:
            raise ValueError(
                "intelligence_backfill_job_request_limit_invalid"
            )
        if int(verification_required) < 1:
            raise ValueError(
                "intelligence_backfill_verification_required_invalid"
            )
        exact_config_hash = str(config_hash).strip()
        normalized_compatibility_hash = str(
            compatibility_hash or exact_config_hash
        ).strip()
        if not exact_config_hash or not normalized_compatibility_hash:
            raise ValueError(
                "intelligence_backfill_config_hash_missing"
            )
        normalized_config_json = str(config_json or "{}")
        try:
            parsed_config = json.loads(normalized_config_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "intelligence_backfill_config_json_invalid"
            ) from exc
        if not isinstance(parsed_config, dict):
            raise ValueError(
                "intelligence_backfill_config_json_invalid"
            )
        normalized_config_json = json.dumps(
            parsed_config,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = json.dumps(
            {
                "source": source,
                "start_date": start_date,
                "end_date": end_date,
                "completion_strategy_version":
                    BACKFILL_COMPLETION_STRATEGY_VERSION,
                "config_hash": exact_config_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        job_id = (
            "backfill-job-"
            + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        )
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO backfill_jobs(
                    job_id, source, start_date, end_date,
                    completion_strategy_version, config_hash,
                    request_limit, verification_required,
                    status, created_at, updated_at,
                    exact_config_hash, compatibility_hash,
                    config_json, evidence_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    source,
                    start_date,
                    end_date,
                    BACKFILL_COMPLETION_STRATEGY_VERSION,
                    exact_config_hash,
                    int(request_limit),
                    int(verification_required),
                    "running",
                    now,
                    now,
                    exact_config_hash,
                    normalized_compatibility_hash,
                    normalized_config_json,
                    "current",
                ),
            )
            connection.execute(
                """
                UPDATE backfill_jobs
                SET generation=generation+1,
                    status=CASE
                      WHEN evidence_status='needs_revalidation'
                      THEN 'partial'
                      ELSE 'running'
                    END,
                    updated_at=?
                WHERE job_id=?
                """,
                (now, job_id),
            )
            row = connection.execute(
                """
                SELECT * FROM backfill_jobs WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(
                "intelligence_backfill_job_missing_after_upsert"
            )
        return dict(row)

    def finish_backfill_job(
        self,
        job_id: str,
        *,
        generation: int,
        status: str,
    ) -> str:
        if status not in {
            "running",
            "partial",
            "complete",
            "failed",
        }:
            raise ValueError(
                f"intelligence_backfill_job_status_invalid:{status}"
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                """
                SELECT generation
                FROM backfill_jobs
                WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(
                    f"intelligence_backfill_job_missing:{job_id}"
                )
            if int(job["generation"]) != int(generation):
                raise BackfillGenerationConflict(
                    "intelligence_backfill_job_finish_conflict"
                )
            connection.execute(
                """
                UPDATE backfill_job_partition_refs
                SET evidence_status='needs_revalidation'
                WHERE job_id=?
                  AND EXISTS (
                    SELECT 1
                    FROM backfill_partitions partition
                    WHERE partition.source=
                          backfill_job_partition_refs.source
                      AND partition.partition_start=
                          backfill_job_partition_refs.partition_start
                      AND partition.partition_end=
                          backfill_job_partition_refs.partition_end
                      AND (
                        partition.status<>'complete'
                        OR partition.completion_strategy_version<>?
                      )
                  )
                """,
                (
                    job_id,
                    BACKFILL_COMPLETION_STRATEGY_VERSION,
                ),
            )
            evidence = connection.execute(
                """
                SELECT
                  count(*) AS partitions_total,
                  coalesce(sum(
                    CASE
                      WHEN ref.evidence_status='needs_revalidation'
                        OR partition.status<>'complete'
                        OR partition.completion_strategy_version<>?
                      THEN 1 ELSE 0
                    END
                  ), 0) AS partitions_invalid
                FROM backfill_job_partition_refs ref
                JOIN backfill_partitions partition
                  ON partition.source=ref.source
                 AND partition.partition_start=ref.partition_start
                 AND partition.partition_end=ref.partition_end
                WHERE ref.job_id=?
                """,
                (
                    BACKFILL_COMPLETION_STRATEGY_VERSION,
                    job_id,
                ),
            ).fetchone()
            invalid = int(evidence["partitions_invalid"] or 0)
            total = int(evidence["partitions_total"] or 0)
            if status == "failed":
                derived_status = "failed"
            elif status == "complete" and total > 0 and invalid == 0:
                derived_status = "complete"
            else:
                derived_status = "partial"
            evidence_status = (
                "current" if invalid == 0 else "needs_revalidation"
            )
            cursor = connection.execute(
                """
                UPDATE backfill_jobs
                SET status=?,
                    evidence_status=?,
                    updated_at=?
                WHERE job_id=? AND generation=?
                """,
                (
                    derived_status,
                    evidence_status,
                    utc_iso(),
                    job_id,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_job_finish_conflict"
                )
        return derived_status

    def backfill_job_universe(
        self,
        job_id: str,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, b.created_at AS frozen_at
                FROM backfill_job_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.job_id=?
                """,
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def announcement_security_catalog(
        self,
        source: str = "tushare_announcement",
    ) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source, ts_code, name, provenance,
                       first_seen_at, last_seen_at, observations
                FROM announcement_security_catalog
                WHERE source=?
                ORDER BY ts_code
                """,
                (source,),
            ).fetchall()
        return [dict(row) for row in rows]

    def announcement_catalog_state(
        self,
        source: str = "tushare_announcement",
    ) -> dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT source, revision, content_hash,
                       security_count, updated_at
                FROM announcement_catalog_state
                WHERE source=?
                """,
                (source,),
            ).fetchone()
        if row is not None:
            return dict(row)
        empty_hash = hashlib.sha256(b"[]").hexdigest()
        return {
            "source": source,
            "revision": 0,
            "content_hash": empty_hash,
            "security_count": 0,
            "updated_at": "",
        }

    def expand_backfill_job_from_catalog(
        self,
        job_id: str,
    ) -> tuple[str, ...]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._expand_job_universe_from_catalog(
                connection,
                job_id=job_id,
            )

    def _expand_job_universe_from_catalog(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
    ) -> tuple[str, ...]:
        binding = connection.execute(
            """
            SELECT j.source, j.request_limit, b.snapshot_id
            FROM backfill_jobs j
            JOIN backfill_job_universes b
              ON b.job_id=j.job_id
            WHERE j.job_id=?
            """,
            (job_id,),
        ).fetchone()
        if binding is None:
            return ()
        return self._expand_snapshot_from_catalog(
            connection,
            source=str(binding["source"]),
            snapshot_id=str(binding["snapshot_id"]),
            request_limit=int(binding["request_limit"]),
        )

    def _expand_partition_universe_from_catalog(
        self,
        connection: sqlite3.Connection,
        *,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> tuple[str, ...]:
        binding = connection.execute(
            """
            SELECT b.snapshot_id, p.request_limit
            FROM backfill_partition_universes b
            JOIN backfill_partitions p
              ON p.source=b.source
             AND p.partition_start=b.partition_start
             AND p.partition_end=b.partition_end
            WHERE b.source=? AND b.partition_start=?
              AND b.partition_end=?
            """,
            (source, partition_start, partition_end),
        ).fetchone()
        if binding is None:
            return ()
        return self._expand_snapshot_from_catalog(
            connection,
            source=source,
            snapshot_id=str(binding["snapshot_id"]),
            request_limit=max(1, int(binding["request_limit"])),
        )

    @staticmethod
    def _expand_snapshot_from_catalog(
        connection: sqlite3.Connection,
        *,
        source: str,
        snapshot_id: str,
        request_limit: int,
    ) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT c.ts_code
            FROM announcement_security_catalog c
            LEFT JOIN backfill_universe_members m
              ON m.snapshot_id=? AND m.ts_code=c.ts_code
            WHERE c.source=? AND m.ts_code IS NULL
              AND substr(c.ts_code, 1, 3) NOT IN ('200', '900')
            ORDER BY c.ts_code
            """,
            (snapshot_id, source),
        ).fetchall()
        new_codes = tuple(str(row["ts_code"]) for row in rows)
        if not new_codes:
            return ()
        ordinal_row = connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal
            FROM backfill_universe_members
            WHERE snapshot_id=?
            """,
            (snapshot_id,),
        ).fetchone()
        next_ordinal = int(ordinal_row["max_ordinal"]) + 1
        connection.executemany(
            """
            INSERT INTO backfill_universe_members(
                snapshot_id, ordinal, ts_code, security_type,
                list_date, delist_date, listing_status
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    snapshot_id,
                    next_ordinal + offset,
                    code,
                    "discovered",
                    "",
                    "",
                    "CATALOG",
                )
                for offset, code in enumerate(new_codes)
            ],
        )
        all_codes = [
            str(row["ts_code"])
            for row in connection.execute(
                """
                SELECT ts_code
                FROM backfill_universe_members
                WHERE snapshot_id=?
                ORDER BY ts_code
                """,
                (snapshot_id,),
            )
        ]
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "security_codes": all_codes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            UPDATE backfill_universe_snapshots
            SET security_count=?, content_hash=?
            WHERE snapshot_id=?
            """,
            (len(all_codes), content_hash, snapshot_id),
        )
        now = utc_iso()
        connection.executemany(
            """
            INSERT OR IGNORE INTO backfill_partition_items(
                source, partition_start, partition_end, snapshot_id,
                ts_code, request_limit, status, updated_at
            )
            SELECT
                b.source, b.partition_start, b.partition_end,
                b.snapshot_id, ?,
                CASE
                  WHEN p.request_limit > 0 THEN p.request_limit
                  ELSE ?
                END,
                'pending', ?
            FROM backfill_partition_universes b
            JOIN backfill_partitions p
              ON p.source=b.source
             AND p.partition_start=b.partition_start
             AND p.partition_end=b.partition_end
            WHERE b.snapshot_id=?
            """,
            [
                (
                    code,
                    max(1, int(request_limit)),
                    now,
                    snapshot_id,
                )
                for code in new_codes
            ],
        )
        return new_codes

    def bind_backfill_universe(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        security_codes: Iterable[str] = (),
        security_members: Iterable[BackfillUniverseMember] = (),
        request_limit: int,
        list_statuses: tuple[str, ...] = ("L", "D", "P"),
        freeze_source: bool = False,
        job_id: str = "",
        job_generation: int | None = None,
    ) -> dict[str, object]:
        normalized_members: dict[str, BackfillUniverseMember] = {}
        for member in security_members:
            code = str(member.ts_code).strip().upper()
            if not code:
                continue
            normalized = BackfillUniverseMember(
                ts_code=code,
                security_type=str(
                    member.security_type or "stock"
                ).strip().casefold(),
                list_date=str(member.list_date or "").strip(),
                delist_date=str(member.delist_date or "").strip(),
                listing_status=str(
                    member.listing_status or ""
                ).strip().upper(),
            )
            existing_member = normalized_members.get(code)
            if (
                existing_member is not None
                and existing_member != normalized
            ):
                raise ValueError(
                    "intelligence_backfill_universe_member_conflict:"
                    f"{code}"
                )
            normalized_members[code] = normalized
        for value in security_codes:
            code = str(value).strip().upper()
            if code and code not in normalized_members:
                normalized_members[code] = BackfillUniverseMember(code)
        if job_id:
            with self.connect() as connection:
                job = connection.execute(
                    """
                    SELECT source, generation
                    FROM backfill_jobs
                    WHERE job_id=?
                    """,
                    (job_id,),
                ).fetchone()
                if job is None:
                    raise KeyError(
                        f"intelligence_backfill_job_missing:{job_id}"
                    )
                if str(job["source"]) != source:
                    raise ValueError(
                        "intelligence_backfill_job_source_mismatch"
                    )
                if (
                    job_generation is not None
                    and int(job["generation"])
                    != int(job_generation)
                ):
                    raise BackfillGenerationConflict(
                        "intelligence_backfill_job_generation_conflict"
                    )
                catalog_rows = connection.execute(
                    """
                    SELECT ts_code
                    FROM announcement_security_catalog
                    WHERE source=?
                    ORDER BY ts_code
                    """,
                    (source,),
                ).fetchall()
            for row in catalog_rows:
                code = str(row["ts_code"]).strip().upper()
                if code and code not in normalized_members:
                    normalized_members[code] = (
                        BackfillUniverseMember(
                            code,
                            security_type="discovered",
                            listing_status="CATALOG",
                        )
                    )
        members = tuple(
            normalized_members[code]
            for code in sorted(normalized_members)
        )
        provided_codes = tuple(member.ts_code for member in members)
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "source": source,
                    "job_id": str(job_id),
                    "list_statuses": list(list_statuses),
                    "members": [
                        {
                            "ts_code": member.ts_code,
                            "security_type": member.security_type,
                            "list_date": member.list_date,
                            "delist_date": member.delist_date,
                            "listing_status": member.listing_status,
                        }
                        for member in members
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provided_snapshot_id = f"universe-{content_hash[:24]}"
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if job_id:
                reference = connection.execute(
                    """
                    SELECT 1
                    FROM backfill_job_partition_refs
                    WHERE job_id=? AND source=?
                      AND partition_start=? AND partition_end=?
                    """,
                    (
                        job_id,
                        source,
                        partition_start,
                        partition_end,
                    ),
                ).fetchone()
                if reference is None:
                    raise ValueError(
                        "intelligence_backfill_partition_job_mismatch"
                    )
            existing = connection.execute(
                """
                SELECT s.*, b.created_at AS bound_at
                FROM backfill_partition_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.source=? AND b.partition_start=?
                  AND b.partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            parent = connection.execute(
                """
                SELECT 1 FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if parent is None:
                raise KeyError(
                    "intelligence_backfill_parent_partition_missing"
                )
            frozen = None
            if job_id:
                frozen = connection.execute(
                    """
                    SELECT s.*, b.created_at AS frozen_at
                    FROM backfill_job_universes b
                    JOIN backfill_universe_snapshots s
                      ON s.snapshot_id=b.snapshot_id
                    WHERE b.job_id=?
                    """,
                    (job_id,),
                ).fetchone()
            elif freeze_source:
                frozen = connection.execute(
                    """
                    SELECT s.*, b.created_at AS frozen_at
                    FROM backfill_source_universes b
                    JOIN backfill_universe_snapshots s
                      ON s.snapshot_id=b.snapshot_id
                    WHERE b.source=?
                    """,
                    (source,),
                ).fetchone()
            if frozen is not None:
                if job_id:
                    self._expand_job_universe_from_catalog(
                        connection,
                        job_id=job_id,
                    )
                if (
                    not job_id
                    and
                    provided_codes
                    and str(frozen["content_hash"]) != content_hash
                ):
                    raise ValueError(
                        "intelligence_backfill_frozen_universe_conflict:"
                        f"{source}"
                    )
                snapshot_id = str(frozen["snapshot_id"])
                frozen_member_rows = connection.execute(
                    """
                    SELECT ts_code
                    FROM backfill_universe_members
                    WHERE snapshot_id=?
                    ORDER BY ordinal
                    """,
                    (snapshot_id,),
                ).fetchall()
                codes = tuple(
                    str(row["ts_code"])
                    for row in frozen_member_rows
                )
            else:
                if (freeze_source or job_id) and not members:
                    raise ValueError(
                        "intelligence_backfill_frozen_universe_empty"
                    )
                snapshot_id = provided_snapshot_id
                codes = provided_codes
                connection.execute(
                    """
                    INSERT OR IGNORE INTO backfill_universe_snapshots(
                        snapshot_id, source, content_hash,
                        security_count, list_statuses, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        snapshot_id,
                        source,
                        content_hash,
                        len(codes),
                        json.dumps(list_statuses),
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO backfill_universe_members(
                        snapshot_id, ordinal, ts_code, security_type,
                        list_date, delist_date, listing_status
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            snapshot_id,
                            ordinal,
                            member.ts_code,
                            member.security_type,
                            member.list_date,
                            member.delist_date,
                            member.listing_status,
                        )
                        for ordinal, member in enumerate(members)
                    ],
                )
                if job_id:
                    connection.execute(
                        """
                        INSERT INTO backfill_job_universes(
                            job_id, snapshot_id, created_at
                        ) VALUES(?,?,?)
                        """,
                        (job_id, snapshot_id, now),
                    )
                elif freeze_source:
                    connection.execute(
                        """
                        INSERT INTO backfill_source_universes(
                            source, snapshot_id, created_at
                        ) VALUES(?,?,?)
                        """,
                        (source, snapshot_id, now),
                    )
            connection.execute(
                """
                INSERT INTO backfill_partition_universes(
                    source, partition_start, partition_end,
                    snapshot_id, created_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    source,
                    partition_start,
                    partition_end,
                    snapshot_id,
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT INTO backfill_partition_items(
                    source, partition_start, partition_end, snapshot_id,
                    ts_code, request_limit, status, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        source,
                        partition_start,
                        partition_end,
                        snapshot_id,
                        code,
                        max(1, int(request_limit)),
                        "pending",
                        now,
                    )
                    for code in codes
                ],
            )
            row = connection.execute(
                """
                SELECT s.*, b.created_at AS bound_at
                FROM backfill_partition_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.source=? AND b.partition_start=?
                  AND b.partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "intelligence_backfill_universe_binding_missing"
                )
        return dict(row)

    def backfill_source_universe(
        self,
        source: str,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, b.created_at AS frozen_at
                FROM backfill_source_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.source=?
                """,
                (source,),
            ).fetchone()
        return dict(row) if row is not None else None

    def backfill_universe_for_partition(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, b.created_at AS bound_at
                FROM backfill_partition_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.source=? AND b.partition_start=?
                  AND b.partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
        return dict(row) if row is not None else None

    def backfill_partition_items(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*
                FROM backfill_partition_items i
                JOIN backfill_universe_members m
                  ON m.snapshot_id=i.snapshot_id
                 AND m.ts_code=i.ts_code
                WHERE i.source=? AND i.partition_start=?
                  AND i.partition_end=?
                ORDER BY m.ordinal
                """,
                (source, partition_start, partition_end),
            ).fetchall()
        return [dict(row) for row in rows]

    def backfill_probe_documents(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM backfill_partition_probe_documents
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                ORDER BY source_id, content_hash
                """,
                (source, partition_start, partition_end),
            ).fetchall()
        return [dict(row) for row in rows]

    def backfill_probe_manifest_exists(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT probe_manifest_version
                FROM backfill_partitions
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
        return bool(
            row is not None
            and int(row["probe_manifest_version"]) >= 1
        )

    def backfill_probe_codes_missing_from_universe(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> tuple[str, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT p.ts_code
                FROM backfill_partition_probe_documents p
                LEFT JOIN backfill_partition_universes b
                  ON b.source=p.source
                 AND b.partition_start=p.partition_start
                 AND b.partition_end=p.partition_end
                LEFT JOIN backfill_universe_members m
                  ON m.snapshot_id=b.snapshot_id
                 AND m.ts_code=p.ts_code
                WHERE p.source=? AND p.partition_start=?
                  AND p.partition_end=? AND m.ts_code IS NULL
                ORDER BY p.ts_code
                """,
                (source, partition_start, partition_end),
            ).fetchall()
        return tuple(str(row["ts_code"]) for row in rows)

    def start_backfill_item(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        ts_code: str,
        *,
        resume: bool,
        request_limit: int,
        lease_seconds: int = 300,
        now: str | datetime | None = None,
    ) -> dict[str, object]:
        now_iso = _strict_utc_iso(
            now or datetime.now(timezone.utc),
            field="backfill_item_claim_now",
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM backfill_partition_items
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND ts_code=?
                """,
                (source, partition_start, partition_end, ts_code),
            ).fetchone()
            if row is None:
                raise KeyError(
                    "intelligence_backfill_item_not_found:"
                    f"{source}:{partition_start}:{partition_end}:{ts_code}"
                )
            if row["status"] == "complete":
                result = dict(row)
                result["generation"] = int(row["attempts"])
                return result
            self._assert_claimable(
                row,
                now_iso=now_iso,
                lease_seconds=lease_seconds,
                resource=(
                    f"item:{source}:{partition_start}:"
                    f"{partition_end}:{ts_code}"
                ),
            )
            should_reset = (
                not resume
                or (
                    row["status"] == "failed_overflow"
                    and int(row["request_limit"])
                    != max(1, int(request_limit))
                )
            )
            if should_reset:
                connection.execute(
                    """
                    UPDATE backfill_partition_items
                    SET next_offset=0, fetched=0, inserted=0,
                        b_share_filtered=0, status='pending', error='',
                        request_limit=?, updated_at=?
                    WHERE source=? AND partition_start=? AND partition_end=?
                      AND ts_code=? AND status <> 'complete'
                    """,
                    (
                        max(1, int(request_limit)),
                        now_iso,
                        source,
                        partition_start,
                        partition_end,
                        ts_code,
                    ),
                )
            cursor = connection.execute(
                """
                UPDATE backfill_partition_items
                SET status='running', attempts=attempts+1, error='',
                    request_limit=?, updated_at=?
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND ts_code=? AND status <> 'complete'
                  AND attempts=?
                """,
                (
                    max(1, int(request_limit)),
                    now_iso,
                    source,
                    partition_start,
                    partition_end,
                    ts_code,
                    int(row["attempts"]),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_item_claim_conflict"
                )
            claimed = connection.execute(
                """
                SELECT * FROM backfill_partition_items
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND ts_code=?
                """,
                (source, partition_start, partition_end, ts_code),
            ).fetchone()
        result = dict(claimed)
        result["generation"] = int(claimed["attempts"])
        return result

    def finish_backfill_item(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        ts_code: str,
        *,
        generation: int,
        status: str,
        error: str = "",
    ) -> None:
        if status not in BACKFILL_PARTITION_STATUSES - {"pending", "running"}:
            raise ValueError(
                f"intelligence_backfill_status_invalid:{status}"
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE backfill_partition_items
                SET status=?, error=?, updated_at=?
                WHERE source=? AND partition_start=? AND partition_end=?
                  AND ts_code=? AND status='running' AND attempts=?
                """,
                (
                    status,
                    str(error)[:500],
                    utc_iso(),
                    source,
                    partition_start,
                    partition_end,
                    ts_code,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_item_finish_conflict"
                )

    def resolve_next_market_open(
        self,
        published_at: str | datetime,
    ) -> str:
        if self._next_market_open_resolver is None:
            raise ValueError(
                "reconstructed_next_open_resolver_required"
            )
        return _strict_utc_iso(
            self._next_market_open_resolver(
                _strict_utc_iso(
                    published_at,
                    field="published_at",
                )
            ),
            field="next_market_open_resolver",
        )

    def commit_backfill_partition_leaf(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        writes: Iterable[BackfillDocumentWrite],
        fetched: int,
        b_share_filtered: int,
    ) -> int:
        return self._commit_backfill_leaf(
            source,
            partition_start,
            partition_end,
            generation=generation,
            writes=tuple(writes),
            fetched=fetched,
            b_share_filtered=b_share_filtered,
            ts_code=None,
        )

    def commit_backfill_item_leaf(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        ts_code: str,
        *,
        generation: int,
        writes: Iterable[BackfillDocumentWrite],
        fetched: int,
        b_share_filtered: int,
    ) -> int:
        return self._commit_backfill_leaf(
            source,
            partition_start,
            partition_end,
            generation=generation,
            writes=tuple(writes),
            fetched=fetched,
            b_share_filtered=b_share_filtered,
            ts_code=ts_code,
        )

    def commit_backfill_partition_probe(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        writes: Iterable[BackfillDocumentWrite],
        probe_security_pairs: Iterable[tuple[str, str]] = (),
        fetched: int,
        b_share_filtered: int,
        job_id: str = "",
    ) -> int:
        writes_tuple = tuple(writes)
        security_pairs = tuple(probe_security_pairs)
        prepared = [
            self._prepare_backfill_write(write)
            for write in writes_tuple
        ]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                """
                SELECT status, attempts, fetched, inserted,
                       b_share_filtered, probe_manifest_version
                FROM backfill_partitions
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            self._assert_generation(
                target,
                generation=generation,
                resource="partition_probe",
            )
            inserted, records = (
                self._insert_prepared_backfill_writes(
                    connection,
                    prepared,
                )
            )
            now = utc_iso()
            records_by_source_id = {
                write.document.source_id: (
                    write,
                    document_id,
                    content_hash,
                )
                for write, document_id, content_hash in records
            }
            if not security_pairs:
                security_pairs = tuple(
                    (
                        write.document.source_id,
                        str(
                            write.document.metadata.get("ts_code")
                            or ""
                        ),
                    )
                    for write in writes_tuple
                )
            for source_id_value, ts_code_value in security_pairs:
                source_id = str(source_id_value).strip()
                ts_code = str(ts_code_value).strip().upper()
                record = records_by_source_id.get(source_id)
                if record is None:
                    raise ValueError(
                        "intelligence_backfill_probe_document_missing:"
                        f"{source_id}"
                    )
                _, document_id, content_hash = record
                if not ts_code:
                    raise ValueError(
                        "intelligence_backfill_probe_ts_code_missing:"
                        f"{source_id}"
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                      backfill_partition_probe_documents(
                        source, partition_start, partition_end,
                        source_id, content_hash, ts_code,
                        document_id, created_at
                      ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        source,
                        partition_start,
                        partition_end,
                        source_id,
                        content_hash,
                        ts_code,
                        int(document_id),
                        now,
                    ),
                )
            if job_id:
                self._assert_backfill_job_reference(
                    connection,
                    job_id=job_id,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                )
                self._expand_partition_universe_from_catalog(
                    connection,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                )
            self._before_backfill_probe_checkpoint(
                connection,
                tuple(write.document for write in writes_tuple),
            )
            cursor = connection.execute(
                """
                UPDATE backfill_partitions
                SET next_offset=CASE
                      WHEN next_offset > ? THEN next_offset ELSE ? END,
                    fetched=fetched+?, inserted=inserted+?,
                    b_share_filtered=b_share_filtered+?,
                    status='failed_overflow', error='',
                    probe_manifest_version=1,
                    completion_strategy_version=0,
                    updated_at=?
                WHERE source=? AND partition_start=?
                  AND partition_end=? AND status='running'
                  AND attempts=?
                """,
                (
                    max(0, int(fetched)),
                    max(0, int(fetched)),
                    max(0, int(fetched)),
                    inserted,
                    max(0, int(b_share_filtered)),
                    now,
                    source,
                    partition_start,
                    partition_end,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_probe_checkpoint_conflict"
                )
        return inserted

    def commit_backfill_verification_round(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        job_id: str,
        generation: int,
        writes: Iterable[BackfillDocumentWrite],
        probe_security_pairs: Iterable[tuple[str, str]],
        fetched: int,
        b_share_filtered: int,
    ) -> dict[str, object]:
        writes_tuple = tuple(writes)
        security_pairs = tuple(sorted(set(
            (
                str(source_id).strip(),
                str(ts_code).strip().upper(),
            )
            for source_id, ts_code in probe_security_pairs
            if str(source_id).strip()
            and str(ts_code).strip()
        )))
        prepared = [
            self._prepare_backfill_write(write)
            for write in writes_tuple
        ]
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                """
                SELECT status, attempts
                FROM backfill_partitions
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            self._assert_generation(
                parent,
                generation=generation,
                resource="partition_verification",
            )
            self._assert_backfill_job_reference(
                connection,
                job_id=job_id,
                source=source,
                partition_start=partition_start,
                partition_end=partition_end,
            )
            existing_documents = {
                (
                    str(row["source_id"]),
                    str(row["content_hash"]),
                )
                for row in connection.execute(
                    """
                    SELECT DISTINCT source_id, content_hash
                    FROM backfill_partition_probe_documents
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                    """,
                    (source, partition_start, partition_end),
                )
            }
            inserted, records = (
                self._insert_prepared_backfill_writes(
                    connection,
                    prepared,
                )
            )
            records_by_source_id = {
                write.document.source_id: (
                    write,
                    document_id,
                    content_hash,
                )
                for write, document_id, content_hash in records
            }
            now = utc_iso()
            round_entries: list[tuple[str, str, str]] = []
            new_document_keys: set[tuple[str, str]] = set()
            for source_id, ts_code in security_pairs:
                record = records_by_source_id.get(source_id)
                if record is None:
                    raise ValueError(
                        "intelligence_backfill_probe_document_missing:"
                        f"{source_id}"
                    )
                _, document_id, content_hash = record
                document_key = (source_id, content_hash)
                if document_key not in existing_documents:
                    new_document_keys.add(document_key)
                round_entries.append(
                    (source_id, content_hash, ts_code)
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO
                      backfill_partition_probe_documents(
                        source, partition_start, partition_end,
                        source_id, content_hash, ts_code,
                        document_id, created_at
                      ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        source,
                        partition_start,
                        partition_end,
                        source_id,
                        content_hash,
                        ts_code,
                        int(document_id),
                        now,
                    ),
                )
            new_codes = self._expand_partition_universe_from_catalog(
                connection,
                source=source,
                partition_start=partition_start,
                partition_end=partition_end,
            )
            prior = connection.execute(
                """
                SELECT rounds_total, stable_rounds
                FROM backfill_partition_verification_state
                WHERE source=? AND partition_start=?
                  AND partition_end=?
                """,
                (
                    source,
                    partition_start,
                    partition_end,
                ),
            ).fetchone()
            round_no = (
                int(prior["rounds_total"]) + 1
                if prior is not None
                else 1
            )
            new_documents = len(new_document_keys)
            stable_rounds = (
                int(prior["stable_rounds"]) + 1
                if (
                    prior is not None
                    and new_documents == 0
                    and not new_codes
                )
                else int(
                    new_documents == 0 and not new_codes
                )
            )
            probe_hash = hashlib.sha256(
                json.dumps(
                    round_entries,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO backfill_verification_rounds(
                    source, partition_start, partition_end, round_no,
                    probe_hash, probe_documents,
                    probe_security_codes, new_documents,
                    new_security_codes, stable_rounds, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source,
                    partition_start,
                    partition_end,
                    round_no,
                    probe_hash,
                    len({
                        (source_id, content_hash)
                        for source_id, content_hash, _ in round_entries
                    }),
                    len({
                        ts_code
                        for _, _, ts_code in round_entries
                    }),
                    new_documents,
                    len(new_codes),
                    stable_rounds,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO backfill_partition_verification_state(
                    source, partition_start, partition_end, rounds_total,
                    stable_rounds, last_probe_hash,
                    last_new_documents, last_new_security_codes,
                    updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(
                  source, partition_start, partition_end
                ) DO UPDATE SET
                    rounds_total=excluded.rounds_total,
                    stable_rounds=excluded.stable_rounds,
                    last_probe_hash=excluded.last_probe_hash,
                    last_new_documents=excluded.last_new_documents,
                    last_new_security_codes=
                      excluded.last_new_security_codes,
                    updated_at=excluded.updated_at
                """,
                (
                    source,
                    partition_start,
                    partition_end,
                    round_no,
                    stable_rounds,
                    probe_hash,
                    new_documents,
                    len(new_codes),
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE backfill_partitions
                SET fetched=fetched+?, inserted=inserted+?,
                    b_share_filtered=b_share_filtered+?,
                    status='failed_overflow', error='',
                    probe_manifest_version=1,
                    completion_strategy_version=0,
                    updated_at=?
                WHERE source=? AND partition_start=?
                  AND partition_end=? AND status='running'
                  AND attempts=?
                """,
                (
                    max(0, int(fetched)),
                    inserted,
                    max(0, int(b_share_filtered)),
                    now,
                    source,
                    partition_start,
                    partition_end,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_verification_conflict"
                )
        return {
            "round_no": round_no,
            "probe_hash": probe_hash,
            "new_documents": new_documents,
            "new_security_codes": len(new_codes),
            "stable_rounds": stable_rounds,
            "inserted": inserted,
        }

    def _commit_backfill_leaf(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        writes: tuple[BackfillDocumentWrite, ...],
        fetched: int,
        b_share_filtered: int,
        ts_code: str | None,
    ) -> int:
        prepared = [
            self._prepare_backfill_write(write)
            for write in writes
        ]
        table = (
            "backfill_partition_items"
            if ts_code is not None
            else "backfill_partitions"
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if ts_code is None:
                target = connection.execute(
                    """
                    SELECT status, attempts, next_offset
                    FROM backfill_partitions
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                    """,
                    (source, partition_start, partition_end),
                ).fetchone()
            else:
                target = connection.execute(
                    """
                    SELECT status, attempts, next_offset
                    FROM backfill_partition_items
                    WHERE source=? AND partition_start=?
                      AND partition_end=? AND ts_code=?
                    """,
                    (
                        source,
                        partition_start,
                        partition_end,
                        ts_code,
                    ),
                ).fetchone()
            self._assert_generation(
                target,
                generation=generation,
                resource=table,
            )
            inserted, _ = self._insert_prepared_backfill_writes(
                connection,
                prepared,
            )

            self._before_backfill_leaf_checkpoint(
                connection,
                tuple(write.document for write in writes),
            )
            if ts_code is None:
                cursor = connection.execute(
                    """
                    UPDATE backfill_partitions
                    SET next_offset=CASE
                          WHEN next_offset > ? THEN next_offset ELSE ? END,
                        fetched=fetched+?, inserted=inserted+?,
                        b_share_filtered=b_share_filtered+?,
                        status='complete', error='', updated_at=?,
                        completion_strategy_version=?,
                        probe_manifest_version=0,
                        catalog_revision=0, catalog_hash='',
                        completion_basis='short_page'
                    WHERE source=? AND partition_start=?
                      AND partition_end=? AND status='running'
                      AND attempts=?
                    """,
                    (
                        max(0, int(fetched)),
                        max(0, int(fetched)),
                        max(0, int(fetched)),
                        inserted,
                        max(0, int(b_share_filtered)),
                        utc_iso(),
                        BACKFILL_COMPLETION_STRATEGY_VERSION,
                        source,
                        partition_start,
                        partition_end,
                        int(generation),
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE backfill_partition_items
                    SET next_offset=CASE
                          WHEN next_offset > ? THEN next_offset ELSE ? END,
                        fetched=fetched+?, inserted=inserted+?,
                        b_share_filtered=b_share_filtered+?,
                        status='complete', error='', updated_at=?
                    WHERE source=? AND partition_start=?
                      AND partition_end=? AND ts_code=?
                      AND status='running' AND attempts=?
                    """,
                    (
                        max(0, int(fetched)),
                        max(0, int(fetched)),
                        max(0, int(fetched)),
                        inserted,
                        max(0, int(b_share_filtered)),
                        utc_iso(),
                        source,
                        partition_start,
                        partition_end,
                        ts_code,
                        int(generation),
                    ),
                )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_leaf_checkpoint_conflict"
                )
        return inserted

    def _insert_prepared_backfill_writes(
        self,
        connection: sqlite3.Connection,
        prepared: Iterable[
            tuple[BackfillDocumentWrite, tuple[object, ...], str]
        ],
    ) -> tuple[
        int,
        list[tuple[BackfillDocumentWrite, int, str]],
    ]:
        inserted = 0
        records: list[
            tuple[BackfillDocumentWrite, int, str]
        ] = []
        for write, values, content_hash in prepared:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO documents(
                    source, source_id, title, published_at,
                    first_seen_at, effective_at, revised_at,
                    revision_of, source_url, mime_type, content_hash,
                    raw_path, metadata_json, queue_priority,
                    live_observed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            inserted += int(cursor.rowcount == 1)
            document = write.document
            row = connection.execute(
                """
                SELECT id FROM documents
                WHERE source=? AND source_id=? AND content_hash=?
                """,
                (
                    document.source,
                    document.source_id,
                    content_hash,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "intelligence_document_upsert_missing"
                )
            document_id = int(row["id"])
            published_at = str(values[3])
            first_seen_at = str(values[4])
            self._upsert_document_security_links(
                connection,
                document_id=document_id,
                document=document,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO document_availability(
                    document_id, source_recorded_at,
                    research_available_at, availability_provenance,
                    historical_cutoff, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    document_id,
                    None,
                    first_seen_at,
                    "observed",
                    self._historical_cutoff,
                    utc_iso(),
                ),
            )
            availability = self._normalize_backfill_availability(
                write,
                published_at=published_at,
                first_seen_at=first_seen_at,
            )
            if availability is not None:
                self._upsert_reconstructed_availability_row(
                    connection,
                    document_id=document_id,
                    source_recorded_at=availability[0],
                    research_available_at=availability[1],
                    provenance=availability[2],
                )
            records.append((write, document_id, content_hash))
        return inserted, records

    def _prepare_backfill_write(
        self,
        write: BackfillDocumentWrite,
    ) -> tuple[BackfillDocumentWrite, tuple[object, ...], str]:
        document = write.document
        published_at = _strict_utc_iso(
            document.published_at,
            field="document.published_at",
        )
        first_seen_at = _strict_utc_iso(
            document.first_seen_at,
            field="document.first_seen_at",
        )
        effective_at = _strict_utc_iso(
            document.effective_at,
            field="document.effective_at",
        )
        revised_at = (
            _strict_utc_iso(
                document.revised_at,
                field="document.revised_at",
            )
            if document.revised_at
            else None
        )
        payload = bytes(document.content)
        content_hash = hashlib.sha256(payload).hexdigest()
        raw_path = self._raw_path_value(
            document,
            published_at=published_at,
            content_hash=content_hash,
            payload=payload,
        )
        queue_priority, live_observed = self._document_queue_values(
            document
        )
        values: tuple[object, ...] = (
            document.source,
            document.source_id,
            document.title,
            published_at,
            first_seen_at,
            effective_at,
            revised_at,
            document.revision_of,
            document.source_url,
            document.mime_type,
            content_hash,
            raw_path,
            json.dumps(
                document.metadata,
                ensure_ascii=False,
                sort_keys=True,
            ),
            queue_priority,
            live_observed,
        )
        return write, values, content_hash

    def _normalize_backfill_availability(
        self,
        write: BackfillDocumentWrite,
        *,
        published_at: str,
        first_seen_at: str,
    ) -> tuple[str | None, str, str] | None:
        provenance = write.availability_provenance
        if provenance == "observed":
            return None
        if provenance not in RECONSTRUCTED_PROVENANCE:
            raise ValueError(
                f"unknown_availability_provenance:{provenance}"
            )
        if published_at > self._historical_cutoff:
            raise ValueError(
                "post_cutoff_reconstruction_forbidden"
            )
        source_recorded_at = (
            _strict_utc_iso(
                write.source_recorded_at,
                field="source_recorded_at",
            )
            if write.source_recorded_at is not None
            else None
        )
        if provenance == "reconstructed_rec_time":
            if (
                source_recorded_at is None
                or write.research_available_at is None
            ):
                raise ValueError(
                    "reconstructed_rec_time_timestamp_required"
                )
            research_at = _strict_utc_iso(
                write.research_available_at,
                field="research_available_at",
            )
            if source_recorded_at != research_at:
                raise ValueError(
                    "reconstructed_rec_time_timestamp_mismatch"
                )
        else:
            if source_recorded_at is not None:
                raise ValueError(
                    "reconstructed_next_open_source_time_forbidden"
                )
            resolved = self.resolve_next_market_open(published_at)
            if write.research_available_at is not None:
                supplied = _strict_utc_iso(
                    write.research_available_at,
                    field="research_available_at",
                )
                if supplied != resolved:
                    raise ValueError(
                        "reconstructed_next_open_timestamp_mismatch"
                    )
            research_at = resolved
        if any(
            timestamp < published_at or timestamp > first_seen_at
            for timestamp in (
                research_at,
                *(
                    (source_recorded_at,)
                    if source_recorded_at is not None
                    else ()
                ),
            )
        ):
            raise ValueError("reconstructed_availability_out_of_bounds")
        return source_recorded_at, research_at, provenance

    def _before_backfill_leaf_checkpoint(
        self,
        connection: sqlite3.Connection,
        documents: tuple[SourceDocument, ...],
    ) -> None:
        del connection, documents

    def _before_backfill_probe_checkpoint(
        self,
        connection: sqlite3.Connection,
        documents: tuple[SourceDocument, ...],
    ) -> None:
        del connection, documents

    def complete_backfill_partition_from_items(
        self,
        source: str,
        partition_start: str,
        partition_end: str,
        *,
        generation: int,
        job_id: str = "",
    ) -> bool:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parent = connection.execute(
                """
                SELECT status, attempts, fetched, inserted,
                       b_share_filtered, probe_manifest_version,
                       evidence_config_hash
                FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            self._assert_generation(
                parent,
                generation=generation,
                resource="partition",
            )
            verification_required = 0
            verification_state = None
            if job_id:
                self._assert_backfill_job_reference(
                    connection,
                    job_id=job_id,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                )
                self._expand_partition_universe_from_catalog(
                    connection,
                    source=source,
                    partition_start=partition_start,
                    partition_end=partition_end,
                )
                job = connection.execute(
                    """
                    SELECT verification_required
                    FROM backfill_jobs WHERE job_id=?
                    """,
                    (job_id,),
                ).fetchone()
                if job is None:
                    raise KeyError(
                        f"intelligence_backfill_job_missing:{job_id}"
                    )
                verification_required = int(
                    job["verification_required"]
                )
                verification_state = connection.execute(
                    """
                    SELECT stable_rounds
                    FROM backfill_partition_verification_state
                    WHERE source=? AND partition_start=?
                      AND partition_end=?
                    """,
                    (
                        source,
                        partition_start,
                        partition_end,
                    ),
                ).fetchone()
            summary = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END)
                      AS complete_count,
                    COALESCE(SUM(fetched), 0) AS fetched,
                    COALESCE(SUM(inserted), 0) AS inserted,
                    COALESCE(SUM(b_share_filtered), 0)
                      AS b_share_filtered
                FROM backfill_partition_items
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            binding = connection.execute(
                """
                SELECT s.security_count
                FROM backfill_partition_universes b
                JOIN backfill_universe_snapshots s
                  ON s.snapshot_id=b.snapshot_id
                WHERE b.source=? AND b.partition_start=?
                  AND b.partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            probe_summary = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(
                    CASE
                      WHEN d.id IS NOT NULL
                       AND d.source=p.source
                       AND d.source_id=p.source_id
                       AND d.content_hash=p.content_hash
                      THEN 1 ELSE 0
                    END
                  ) AS landed,
                  SUM(
                    CASE WHEN m.ts_code IS NULL THEN 1 ELSE 0 END
                  ) AS unknown_security
                FROM backfill_partition_probe_documents p
                LEFT JOIN documents d
                  ON d.id=p.document_id
                LEFT JOIN backfill_partition_universes b
                  ON b.source=p.source
                 AND b.partition_start=p.partition_start
                 AND b.partition_end=p.partition_end
                LEFT JOIN backfill_universe_members m
                  ON m.snapshot_id=b.snapshot_id
                 AND m.ts_code=p.ts_code
                WHERE p.source=? AND p.partition_start=?
                  AND p.partition_end=?
                """,
                (source, partition_start, partition_end),
            ).fetchone()
            if (
                binding is None
                or int(parent["probe_manifest_version"]) < 1
                or int(summary["total"])
                != int(binding["security_count"])
                or int(summary["complete_count"] or 0)
                != int(summary["total"])
                or int(probe_summary["landed"] or 0)
                != int(probe_summary["total"])
                or int(probe_summary["unknown_security"] or 0) != 0
                or (
                    job_id
                    and (
                        verification_state is None
                        or int(
                            verification_state["stable_rounds"]
                        ) < verification_required
                    )
                )
            ):
                return False
            catalog = self._catalog_state_in_transaction(
                connection,
                source=source,
            )
            cursor = connection.execute(
                """
                UPDATE backfill_partitions
                SET status='complete', next_offset=0,
                    fetched=?, inserted=?, b_share_filtered=?,
                    error='', updated_at=?,
                    completion_strategy_version=?,
                    catalog_revision=?, catalog_hash=?,
                    completion_basis='saturated_catalog_convergence'
                WHERE source=? AND partition_start=?
                  AND partition_end=? AND status='running'
                  AND attempts=?
                """,
                (
                    int(parent["fetched"]) + int(summary["fetched"]),
                    int(parent["inserted"]) + int(summary["inserted"]),
                    int(parent["b_share_filtered"])
                    + int(summary["b_share_filtered"]),
                    utc_iso(),
                    BACKFILL_COMPLETION_STRATEGY_VERSION,
                    int(catalog["revision"]),
                    str(catalog["content_hash"]),
                    source,
                    partition_start,
                    partition_end,
                    int(generation),
                ),
            )
            if cursor.rowcount != 1:
                raise BackfillGenerationConflict(
                    "intelligence_backfill_parent_complete_conflict"
                )
            return True

    def backfill_verification_state(
        self,
        job_id: str,
        source: str,
        partition_start: str,
        partition_end: str,
    ) -> dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT v.*
                FROM backfill_partition_verification_state v
                JOIN backfill_job_partition_refs r
                  ON r.source=v.source
                 AND r.partition_start=v.partition_start
                 AND r.partition_end=v.partition_end
                WHERE r.job_id=? AND v.source=?
                  AND v.partition_start=? AND v.partition_end=?
                  AND r.evidence_status<>'needs_revalidation'
                """,
                (
                    job_id,
                    source,
                    partition_start,
                    partition_end,
                ),
            ).fetchone()
        if row is None:
            return {
                "job_id": job_id,
                "source": source,
                "partition_start": partition_start,
                "partition_end": partition_end,
                "rounds_total": 0,
                "stable_rounds": 0,
                "last_probe_hash": "",
                "last_new_documents": 0,
                "last_new_security_codes": 0,
            }
        result = dict(row)
        result["job_id"] = job_id
        return result

    def backfill_job_progress(
        self,
        job_id: str,
    ) -> dict[str, object]:
        with self.connect() as connection:
            job = connection.execute(
                """
                SELECT * FROM backfill_jobs WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(
                    f"intelligence_backfill_job_missing:{job_id}"
                )
            partitions = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(
                    CASE
                      WHEN p.status='complete'
                       AND r.evidence_status<>'needs_revalidation'
                      THEN 1 ELSE 0
                    END
                  )
                    AS complete_count,
                  SUM(
                    CASE
                      WHEN p.status LIKE 'failed_%'
                       AND r.evidence_status<>'needs_revalidation'
                      THEN 1 ELSE 0
                    END
                  ) AS failed_count,
                  SUM(
                    CASE
                      WHEN r.evidence_status='needs_revalidation'
                      THEN 1 ELSE 0
                    END
                  ) AS needs_revalidation_count
                FROM backfill_job_partition_refs r
                JOIN backfill_partitions p
                  ON p.source=r.source
                 AND p.partition_start=r.partition_start
                 AND p.partition_end=r.partition_end
                WHERE r.job_id=?
                """,
                (job_id,),
            ).fetchone()
            items = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN i.status='complete' THEN 1 ELSE 0 END)
                    AS complete_count,
                  SUM(
                    CASE WHEN i.status LIKE 'failed_%' THEN 1 ELSE 0 END
                  ) AS failed_count
                FROM backfill_job_partition_refs r
                JOIN backfill_partition_items i
                  ON i.source=r.source
                 AND i.partition_start=r.partition_start
                 AND i.partition_end=r.partition_end
                WHERE r.job_id=?
                """,
                (job_id,),
            ).fetchone()
            verification = connection.execute(
                """
                SELECT
                  COALESCE(SUM(rounds_total), 0) AS rounds_total,
                  COALESCE(MAX(stable_rounds), 0) AS max_stable_rounds
                FROM backfill_job_partition_refs r
                JOIN backfill_partition_verification_state v
                  ON v.source=r.source
                 AND v.partition_start=r.partition_start
                 AND v.partition_end=r.partition_end
                WHERE r.job_id=?
                  AND r.evidence_status<>'needs_revalidation'
                """,
                (job_id,),
            ).fetchone()
            verification_partitions = connection.execute(
                """
                SELECT v.partition_start, v.partition_end,
                       v.rounds_total, v.stable_rounds,
                       v.last_probe_hash, v.last_new_documents,
                       v.last_new_security_codes
                FROM backfill_job_partition_refs r
                JOIN backfill_partition_verification_state v
                  ON v.source=r.source
                 AND v.partition_start=r.partition_start
                 AND v.partition_end=r.partition_end
                WHERE r.job_id=?
                  AND r.evidence_status<>'needs_revalidation'
                ORDER BY v.partition_start, v.partition_end
                """,
                (job_id,),
            ).fetchall()
        partition_total = int(partitions["total"] or 0)
        partition_complete = int(
            partitions["complete_count"] or 0
        )
        item_total = int(items["total"] or 0)
        item_complete = int(items["complete_count"] or 0)
        return {
            "partitions_total": partition_total,
            "partitions_complete": partition_complete,
            "partitions_remaining":
                partition_total - partition_complete,
            "partitions_failed": int(
                partitions["failed_count"] or 0
            ),
            "partitions_needs_revalidation": int(
                partitions["needs_revalidation_count"] or 0
            ),
            "items_total": item_total,
            "items_complete": item_complete,
            "items_remaining": item_total - item_complete,
            "items_failed": int(items["failed_count"] or 0),
            "verification": {
                "required_stable_rounds": int(
                    job["verification_required"]
                ),
                "rounds_total": int(
                    verification["rounds_total"] or 0
                ),
                "max_stable_rounds": int(
                    verification["max_stable_rounds"] or 0
                ),
                "partitions": [
                    dict(row)
                    for row in verification_partitions
                ],
            },
        }

    @staticmethod
    def _pdf_artifact_id(document_id: int) -> str:
        digest = hashlib.sha256(
            f"document:{int(document_id)}:pdf".encode("utf-8")
        ).hexdigest()
        return f"pdf-{digest}"

    @staticmethod
    def _artifact_row(row: sqlite3.Row | None) -> dict[str, object] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _active_pdf_artifact_rows(
        connection: sqlite3.Connection,
        document_id: int,
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT *
                FROM document_artifacts
                WHERE document_id=?
                  AND artifact_type='pdf'
                  AND status IN (
                    'downloaded', 'parsed', 'ocr_required', 'ocr_failed'
                  )
                ORDER BY created_at, artifact_id
                """,
                (int(document_id),),
            ).fetchall()
        )

    @staticmethod
    def _one_active_pdf_artifact(
        connection: sqlite3.Connection,
        document_id: int,
    ) -> sqlite3.Row | None:
        rows = IntelligenceStore._active_pdf_artifact_rows(
            connection,
            document_id,
        )
        if len(rows) > 1:
            raise DocumentArtifactConflict(
                "intelligence_pdf_artifact_conflict:multiple_active"
            )
        return rows[0] if rows else None

    def document_for_pdf_fetch(
        self,
        document_id: int,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id=?",
                (int(document_id),),
            ).fetchone()
        return self._artifact_row(row)

    def current_pdf_artifact(
        self,
        document_id: int,
    ) -> dict[str, object] | None:
        with self.connect() as connection:
            row = self._one_active_pdf_artifact(
                connection,
                int(document_id),
            )
        return self._artifact_row(row)

    def commit_pdf_artifact(
        self,
        *,
        document_id: int,
        content_hash: str,
        storage_uri: str,
        mime_type: str,
        byte_size: int,
    ) -> dict[str, object]:
        normalized_hash = str(content_hash).strip().casefold()
        if (
            len(normalized_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in normalized_hash
            )
        ):
            raise ValueError("intelligence_pdf_artifact_hash_invalid")
        normalized_uri = str(storage_uri).strip()
        if not normalized_uri:
            raise ValueError("intelligence_pdf_artifact_uri_invalid")
        normalized_mime = str(mime_type).strip().casefold()
        if not normalized_mime:
            raise ValueError("intelligence_pdf_artifact_mime_invalid")
        normalized_size = int(byte_size)
        if normalized_size < 0:
            raise ValueError("intelligence_pdf_artifact_size_invalid")
        artifact_id = self._pdf_artifact_id(document_id)
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute(
                "SELECT id FROM documents WHERE id=?",
                (int(document_id),),
            ).fetchone()
            if document is None:
                raise KeyError("intelligence_document_not_found")
            active = self._one_active_pdf_artifact(
                connection,
                int(document_id),
            )
            if active is not None:
                same_artifact = (
                    str(active["content_hash"]) == normalized_hash
                    and str(active["storage_uri"]) == normalized_uri
                    and str(active["mime_type"]).casefold()
                    == normalized_mime
                    and int(active["byte_size"]) == normalized_size
                )
                if not same_artifact:
                    raise DocumentArtifactConflict(
                        "intelligence_pdf_artifact_conflict:"
                        "different_active"
                    )
                connection.commit()
                return dict(active)
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(?, ?, 'pdf', ?, ?, ?, ?, '', 'downloaded', '', ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    storage_uri=excluded.storage_uri,
                    mime_type=excluded.mime_type,
                    byte_size=excluded.byte_size,
                    parser_version='',
                    status='downloaded',
                    error='',
                    updated_at=excluded.updated_at
                WHERE document_artifacts.document_id=excluded.document_id
                  AND document_artifacts.artifact_type='pdf'
                  AND document_artifacts.status IN (
                    'queued', 'failed_retryable', 'failed_terminal'
                  )
                """,
                (
                    artifact_id,
                    int(document_id),
                    normalized_hash,
                    normalized_uri,
                    normalized_mime,
                    normalized_size,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM document_artifacts
                WHERE artifact_id=? AND document_id=?
                """,
                (artifact_id, int(document_id)),
            ).fetchone()
            if row is None or str(row["status"]) != "downloaded":
                raise DocumentArtifactConflict(
                    "intelligence_pdf_artifact_conflict:cas_failed"
                )
            connection.commit()
            return dict(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_pdf_artifact_failure(
        self,
        *,
        document_id: int,
        status: str,
        error: str,
    ) -> dict[str, object] | None:
        if status not in {"failed_retryable", "failed_terminal"}:
            raise ValueError("intelligence_pdf_artifact_status_invalid")
        raw_error = str(error)
        if (
            not raw_error
            or len(raw_error) > 500
            or any(
                not character.isascii()
                or (
                    not character.isalnum()
                    and character not in "_.:-"
                )
                for character in raw_error
            )
        ):
            normalized_error = "pdf_fetch_failed"
        else:
            normalized_error = raw_error
        artifact_id = self._pdf_artifact_id(document_id)
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            document = connection.execute(
                "SELECT id FROM documents WHERE id=?",
                (int(document_id),),
            ).fetchone()
            if document is None:
                connection.rollback()
                return None
            active = self._one_active_pdf_artifact(
                connection,
                int(document_id),
            )
            if active is not None:
                connection.commit()
                return dict(active)
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(?, ?, 'pdf', '', '', 'application/pdf', 0, '',
                         ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    status=excluded.status,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                WHERE document_artifacts.document_id=excluded.document_id
                  AND document_artifacts.artifact_type='pdf'
                  AND document_artifacts.status IN (
                    'queued', 'failed_retryable', 'failed_terminal'
                  )
                """,
                (
                    artifact_id,
                    int(document_id),
                    status,
                    normalized_error,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM document_artifacts
                WHERE artifact_id=? AND document_id=?
                """,
                (artifact_id, int(document_id)),
            ).fetchone()
            connection.commit()
            return self._artifact_row(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def semantic_document_snapshot(
        self,
        document_id: int,
    ) -> dict[str, object]:
        """Load the bounded, point-in-time inputs for one semantic decision."""

        normalized_id = int(document_id)
        with self.connect() as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE id=?",
                (normalized_id,),
            ).fetchone()
            if document is None:
                raise KeyError("intelligence_document_not_found")
            artifact = connection.execute(
                """
                SELECT *
                FROM document_artifacts
                WHERE document_id=?
                  AND artifact_type='parsed'
                ORDER BY
                  updated_at DESC,
                  CASE status
                    WHEN 'parsed' THEN 0
                    WHEN 'ocr_failed' THEN 1
                    ELSE 2
                  END,
                  artifact_id DESC
                LIMIT 1
                """,
                (normalized_id,),
            ).fetchone()
            chunks: list[sqlite3.Row] = []
            tables: list[sqlite3.Row] = []
            if artifact is not None:
                chunks = list(
                    connection.execute(
                        """
                        SELECT *
                        FROM document_chunks
                        WHERE document_id=? AND artifact_id=?
                        ORDER BY sequence_no, chunk_id
                        """,
                        (normalized_id, str(artifact["artifact_id"])),
                    ).fetchall()
                )
                tables = list(
                    connection.execute(
                        """
                        SELECT *
                        FROM document_tables
                        WHERE document_id=? AND artifact_id=?
                        ORDER BY page_number, sequence_no, table_id
                        """,
                        (normalized_id, str(artifact["artifact_id"])),
                    ).fetchall()
                )
            links = list(
                connection.execute(
                    """
                    SELECT ts_code, name, provenance
                    FROM document_security_links
                    WHERE document_id=?
                    ORDER BY ts_code
                    """,
                    (normalized_id,),
                ).fetchall()
            )
            rule_events = list(
                connection.execute(
                    """
                    SELECT DISTINCT event_type
                    FROM events
                    WHERE document_id=?
                      AND extraction_method LIKE 'rules-%'
                    ORDER BY event_type
                    """,
                    (normalized_id,),
                ).fetchall()
            )
            current_source_id = str(document["source_id"])
            revision_of = str(document["revision_of"] or "")
            source_ids = tuple(
                dict.fromkeys(
                    value
                    for value in (current_source_id, revision_of)
                    if value
                )
            )
            revisions: list[sqlite3.Row] = []
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                revisions = list(
                    connection.execute(
                        f"""
                        SELECT id, title, published_at, source_id, revision_of
                        FROM documents
                        WHERE source=?
                          AND source_id IN ({placeholders})
                          AND id<>?
                          AND published_at<=?
                        ORDER BY published_at, id
                        """,
                        (
                            str(document["source"]),
                            *source_ids,
                            normalized_id,
                            str(document["published_at"]),
                        ),
                    ).fetchall()
                )
        revision_context = [
            {
                "document_id": int(row["id"]),
                "title": str(row["title"]),
                "published_at": str(row["published_at"]),
                "relation": (
                    "prior_revision"
                    if revision_of and str(row["source_id"]) == revision_of
                    else "same_source_record"
                ),
            }
            for row in revisions
        ]
        return {
            "document": dict(document),
            "artifact": dict(artifact) if artifact is not None else None,
            "chunks": [dict(row) for row in chunks],
            "tables": [dict(row) for row in tables],
            "security_links": [dict(row) for row in links],
            "rule_event_types": [
                str(row["event_type"])
                for row in rule_events
            ],
            "revision_context": revision_context,
        }

    def ensure_semantic_evidence_chunks(
        self,
        *,
        document_id: int,
        artifact_id: str,
        parser_version: str,
        chunks: Iterable[Mapping[str, object]],
    ) -> int:
        """Materialize deterministic table cells only when evidence cites them."""

        values = tuple(chunks)
        if not values:
            return 0
        inserted = 0
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), -1) + 1
                    FROM document_chunks
                    WHERE artifact_id=? AND parser_version=?
                    """,
                    (str(artifact_id), str(parser_version)),
                ).fetchone()[0]
            )
            for value in values:
                chunk_id = str(value.get("chunk_id") or "").strip()
                text = str(value.get("text") or "")
                section = str(value.get("section") or "")
                if not chunk_id or not text or section != "table_cell":
                    raise ValueError("semantic_evidence_chunk_invalid")
                page_number = int(value.get("page_number") or 0)
                bbox = value.get("bbox")
                bbox_json = json.dumps(
                    list(bbox) if isinstance(bbox, (list, tuple)) else [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    """
                    SELECT document_id, artifact_id, page_number, section,
                           bbox_json, text, text_hash, parser_version
                    FROM document_chunks
                    WHERE chunk_id=?
                    """,
                    (chunk_id,),
                ).fetchone()
                if existing is not None:
                    same = (
                        int(existing["document_id"]) == int(document_id)
                        and str(existing["artifact_id"]) == str(artifact_id)
                        and int(existing["page_number"]) == page_number
                        and str(existing["section"]) == section
                        and json.loads(str(existing["bbox_json"]))
                        == json.loads(bbox_json)
                        and str(existing["text"]) == text
                        and str(existing["text_hash"]) == text_hash
                        and str(existing["parser_version"])
                        == str(parser_version)
                    )
                    if not same:
                        raise ValueError(
                            "semantic_evidence_chunk_immutable_conflict"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO document_chunks(
                        chunk_id, document_id, artifact_id, sequence_no,
                        page_number, section, bbox_json, text, text_hash,
                        ocr_used, ocr_confidence, parser_version
                    ) VALUES(?,?,?,?,?,?,?,?,?,0,NULL,?)
                    """,
                    (
                        chunk_id,
                        int(document_id),
                        str(artifact_id),
                        next_sequence,
                        page_number,
                        section,
                        bbox_json,
                        text,
                        text_hash,
                        str(parser_version),
                    ),
                )
                next_sequence += 1
                inserted += 1
            connection.commit()
        return inserted

    def semantic_ready_document_ids(self, *, limit: int = 500) -> list[int]:
        """Return parsed artifact work independently of documents.status."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.id
                FROM documents d
                WHERE EXISTS (
                    SELECT 1
                    FROM document_artifacts a
                    WHERE a.document_id=d.id
                      AND a.artifact_type='parsed'
                      AND a.status IN ('parsed', 'ocr_failed')
                )
                ORDER BY d.queue_priority DESC,
                         d.live_observed DESC,
                         d.published_at,
                         d.id
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def register_semantic_contract_profile(
        self,
        *,
        profile_id: str,
        profile_hash: str,
        status: str = "draft",
    ) -> dict[str, object]:
        normalized_id = str(profile_id).strip()
        normalized_hash = self._semantic_hash(
            profile_hash,
            field="profile_hash",
        )
        if not normalized_id:
            raise ValueError("semantic_profile_id_required")
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM semantic_contract_profiles WHERE profile_id=?",
                (normalized_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO semantic_contract_profiles(
                      profile_id, profile_hash, status, created_at, updated_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (normalized_id, normalized_hash, str(status), now, now),
                )
            elif str(existing["profile_hash"]) != normalized_hash:
                connection.rollback()
                raise ValueError("semantic_profile_immutable_conflict")
            row = connection.execute(
                "SELECT * FROM semantic_contract_profiles WHERE profile_id=?",
                (normalized_id,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return dict(row)

    def register_semantic_executor_binding(
        self,
        *,
        profile_id: str,
        binding: ExecutorBinding,
        status: str = "untested",
    ) -> dict[str, object]:
        normalized_profile = str(profile_id).strip()
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM semantic_executor_bindings WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()
            values = (
                normalized_profile,
                binding.executor_mode,
                binding.provider,
                binding.model,
                binding.client_version,
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO semantic_executor_bindings(
                      binding_id, profile_id, executor_mode, provider, model,
                      client_version, status, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (binding.binding_id, *values, str(status), now, now),
                )
            elif tuple(
                str(existing[key])
                for key in (
                    "profile_id",
                    "executor_mode",
                    "provider",
                    "model",
                    "client_version",
                )
            ) != values:
                connection.rollback()
                raise ValueError("semantic_executor_binding_immutable_conflict")
            row = connection.execute(
                "SELECT * FROM semantic_executor_bindings WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return dict(row)

    def register_semantic_task(
        self,
        *,
        semantic_task_id: str,
        document_id: int,
        profile_id: str,
        artifact_hash: str,
        input_hash: str,
    ) -> dict[str, object]:
        normalized_task = str(semantic_task_id).strip()
        normalized_artifact = self._semantic_hash(
            artifact_hash,
            field="artifact_hash",
        )
        normalized_input = self._semantic_hash(
            input_hash,
            field="input_hash",
        )
        identity = (
            int(document_id),
            str(profile_id).strip(),
            normalized_artifact,
            normalized_input,
        )
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM semantic_tasks WHERE semantic_task_id=?",
                (normalized_task,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO semantic_tasks(
                      semantic_task_id, document_id, profile_id,
                      artifact_hash, input_hash, status, created_at, updated_at
                    ) VALUES(?,?,?,?,?,'prepared',?,?)
                    """,
                    (normalized_task, *identity, now, now),
                )
            elif (
                int(existing["document_id"]),
                str(existing["profile_id"]),
                str(existing["artifact_hash"]),
                str(existing["input_hash"]),
            ) != identity:
                connection.rollback()
                raise ValueError("semantic_task_immutable_conflict")
            row = connection.execute(
                "SELECT * FROM semantic_tasks WHERE semantic_task_id=?",
                (normalized_task,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return dict(row)

    def register_semantic_execution_job(
        self,
        *,
        execution_job_id: str,
        semantic_task_id: str,
        binding_id: str,
    ) -> dict[str, object]:
        identity = (str(semantic_task_id).strip(), str(binding_id).strip())
        normalized_job = str(execution_job_id).strip()
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM semantic_execution_jobs WHERE execution_job_id=?",
                (normalized_job,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO semantic_execution_jobs(
                      execution_job_id, semantic_task_id, binding_id, status,
                      created_at, updated_at
                    ) VALUES(?,?,?,'assigned',?,?)
                    """,
                    (normalized_job, *identity, now, now),
                )
                connection.execute(
                    """
                    UPDATE semantic_tasks
                    SET status='assigned', updated_at=?
                    WHERE semantic_task_id=? AND status='prepared'
                    """,
                    (now, identity[0]),
                )
            elif (
                str(existing["semantic_task_id"]),
                str(existing["binding_id"]),
            ) != identity:
                connection.rollback()
                raise ValueError("semantic_execution_job_immutable_conflict")
            row = connection.execute(
                "SELECT * FROM semantic_execution_jobs WHERE execution_job_id=?",
                (normalized_job,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return dict(row)

    def transition_semantic_execution_job(
        self,
        execution_job_id: str,
        *,
        to_status: str,
        output_hash: str = "",
        error: str = "",
    ) -> dict[str, object]:
        normalized_job = str(execution_job_id).strip()
        target = str(to_status).strip()
        now = utc_iso()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM semantic_execution_jobs WHERE execution_job_id=?",
                (normalized_job,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise KeyError("semantic_execution_job_not_found")
            status = str(current["status"])
            if target not in SEMANTIC_EXECUTION_TRANSITIONS.get(status, frozenset()):
                connection.rollback()
                raise ValueError("semantic_execution_transition_invalid")
            terminal = target in {"accepted", "quarantined", "abandoned"}
            connection.execute(
                """
                UPDATE semantic_execution_jobs
                SET status=?, output_hash=?, error=?, updated_at=?, finished_at=?
                WHERE execution_job_id=?
                """,
                (
                    target,
                    str(output_hash),
                    str(error),
                    now,
                    now if terminal else None,
                    normalized_job,
                ),
            )
            connection.execute(
                """
                UPDATE semantic_tasks
                SET status=?, error=?, updated_at=?
                WHERE semantic_task_id=?
                """,
                (target, str(error), now, str(current["semantic_task_id"])),
            )
            row = connection.execute(
                "SELECT * FROM semantic_execution_jobs WHERE execution_job_id=?",
                (normalized_job,),
            ).fetchone()
            connection.commit()
        assert row is not None
        return dict(row)

    @staticmethod
    def _semantic_run_id(values: tuple[object, ...]) -> str:
        payload = json.dumps(
            values,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"semantic-{hashlib.sha256(payload).hexdigest()}"

    @staticmethod
    def _semantic_hash(value: str, *, field: str) -> str:
        normalized = str(value).strip().casefold()
        if (
            len(normalized) != 64
            or any(
                character not in "0123456789abcdef"
                for character in normalized
            )
        ):
            raise ValueError(f"intelligence_semantic_{field}_invalid")
        return normalized

    def claim_semantic_run(
        self,
        *,
        document_id: int,
        artifact_hash: str,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        taxonomy_version: str,
        parser_version: str,
        input_hash: str,
    ) -> dict[str, object]:
        """Insert running lineage before provider I/O or reclaim retryable work."""

        normalized_id = int(document_id)
        normalized_artifact_hash = self._semantic_hash(
            artifact_hash,
            field="artifact_hash",
        )
        normalized_input_hash = self._semantic_hash(
            input_hash,
            field="input_hash",
        )
        identity = tuple(
            str(value).strip()
            for value in (
                provider,
                model,
                prompt_version,
                schema_version,
                taxonomy_version,
                parser_version,
            )
        )
        if any(not value for value in identity):
            raise ValueError("intelligence_semantic_identity_invalid")
        key_values: tuple[object, ...] = (
            normalized_id,
            normalized_artifact_hash,
            *identity,
            normalized_input_hash,
        )
        run_id = self._semantic_run_id(key_values)
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM documents WHERE id=?",
                (normalized_id,),
            ).fetchone() is None:
                raise KeyError("intelligence_document_not_found")
            row = connection.execute(
                """
                SELECT *
                FROM semantic_runs
                WHERE document_id=? AND artifact_hash=?
                  AND provider=? AND model=? AND prompt_version=?
                  AND schema_version=? AND taxonomy_version=?
                  AND parser_version=? AND input_hash=?
                """,
                key_values,
            ).fetchone()
            claimed = False
            if row is None:
                connection.execute(
                    """
                    INSERT INTO semantic_runs(
                        run_id, document_id, artifact_hash, provider, model,
                        prompt_version, schema_version, taxonomy_version,
                        parser_version, input_hash, status, error, started_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,'running','',?)
                    """,
                    (run_id, *key_values, timestamp),
                )
                claimed = True
            elif str(row["status"]) in {
                "failed_retryable",
                "budget_deferred",
                "unavailable",
            }:
                connection.execute(
                    """
                    UPDATE semantic_runs
                    SET output_hash=NULL, output_uri=NULL, status='running',
                        input_tokens=NULL, output_tokens=NULL, latency_ms=NULL,
                        cost_microunits=NULL, error='', started_at=?,
                        finished_at=NULL
                    WHERE run_id=?
                    """,
                    (timestamp, str(row["run_id"])),
                )
                run_id = str(row["run_id"])
                claimed = True
            else:
                run_id = str(row["run_id"])
            current = connection.execute(
                "SELECT * FROM semantic_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise SemanticRunConflict(
                    "intelligence_semantic_run_claim_missing"
                )
            connection.commit()
            result = dict(current)
            result["claimed"] = claimed
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_semantic_run(
        self,
        run_id: str,
        *,
        status: str,
        output_hash: str | None = None,
        output_uri: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        cost_microunits: int | None = None,
        error: str = "",
    ) -> dict[str, object]:
        allowed_statuses = {
            "succeeded",
            "no_event",
            "failed_retryable",
            "failed_terminal",
            "budget_deferred",
            "unavailable",
        }
        if status not in allowed_statuses:
            raise ValueError("intelligence_semantic_status_invalid")
        normalized_hash = (
            self._semantic_hash(output_hash, field="output_hash")
            if output_hash is not None
            else None
        )
        normalized_uri = str(output_uri or "").strip() or None
        if (normalized_hash is None) != (normalized_uri is None):
            raise ValueError("intelligence_semantic_output_lineage_incomplete")
        if status in {"succeeded", "no_event"} and normalized_hash is None:
            raise ValueError("intelligence_semantic_output_required")
        normalized_error = self._bounded_semantic_error(error)
        metrics = (
            self._optional_nonnegative(input_tokens, "input_tokens"),
            self._optional_nonnegative(output_tokens, "output_tokens"),
            self._optional_nonnegative(latency_ms, "latency_ms"),
            self._optional_nonnegative(cost_microunits, "cost_microunits"),
        )
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE semantic_runs
                SET output_hash=?, output_uri=?, status=?,
                    input_tokens=?, output_tokens=?, latency_ms=?,
                    cost_microunits=?, error=?, finished_at=?
                WHERE run_id=? AND status='running'
                """,
                (
                    normalized_hash,
                    normalized_uri,
                    status,
                    *metrics,
                    normalized_error,
                    timestamp,
                    str(run_id),
                ),
            )
            row = connection.execute(
                "SELECT * FROM semantic_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
            if row is None:
                raise KeyError("intelligence_semantic_run_not_found")
            if cursor.rowcount != 1:
                same_result = (
                    str(row["status"]) == status
                    and row["output_hash"] == normalized_hash
                    and row["output_uri"] == normalized_uri
                    and str(row["error"]) == normalized_error
                )
                if not same_result:
                    raise SemanticRunConflict(
                        "intelligence_semantic_run_finish_conflict"
                    )
            connection.commit()
            return dict(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def semantic_run(self, run_id: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM semantic_runs WHERE run_id=?",
                (str(run_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def activate_semantic_repair(
        self,
        *,
        repair_id: str,
        document_id: int,
        replacement_run_id: str,
        superseded_run_ids: Iterable[str],
        reason: str,
    ) -> dict[str, object]:
        """Activate an auditable run replacement without deleting lineage."""

        normalized_repair_id = str(repair_id).strip()
        normalized_reason = str(reason).strip()
        normalized_replacement = str(replacement_run_id).strip()
        normalized_superseded = tuple(
            dict.fromkeys(
                str(run_id).strip()
                for run_id in superseded_run_ids
                if str(run_id).strip()
            )
        )
        if not normalized_repair_id:
            raise ValueError("intelligence_semantic_repair_id_required")
        if not normalized_reason:
            raise ValueError("intelligence_semantic_repair_reason_required")
        if not normalized_replacement:
            raise ValueError("intelligence_semantic_replacement_run_required")
        if normalized_replacement in normalized_superseded:
            raise ValueError("intelligence_semantic_repair_cycle")
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replacement = connection.execute(
                "SELECT document_id, status FROM semantic_runs WHERE run_id=?",
                (normalized_replacement,),
            ).fetchone()
            if replacement is None:
                raise KeyError("intelligence_semantic_run_not_found")
            if int(replacement["document_id"]) != int(document_id):
                raise SemanticRunConflict(
                    "intelligence_semantic_repair_document_conflict"
                )
            if str(replacement["status"]) not in {"succeeded", "no_event"}:
                raise SemanticRunConflict(
                    "intelligence_semantic_replacement_not_terminal"
                )
            activated = 0
            conflicted = 0
            for superseded_run_id in normalized_superseded:
                superseded = connection.execute(
                    "SELECT document_id, status FROM semantic_runs WHERE run_id=?",
                    (superseded_run_id,),
                ).fetchone()
                if superseded is None:
                    raise KeyError("intelligence_semantic_run_not_found")
                if int(superseded["document_id"]) != int(document_id):
                    raise SemanticRunConflict(
                        "intelligence_semantic_repair_document_conflict"
                    )
                if str(superseded["status"]) not in {"succeeded", "no_event"}:
                    raise SemanticRunConflict(
                        "intelligence_semantic_superseded_not_terminal"
                    )
                active = connection.execute(
                    """
                    SELECT repair_id, replacement_run_id, document_id, reason
                    FROM semantic_run_replacements
                    WHERE superseded_run_id=? AND status='active'
                    """,
                    (superseded_run_id,),
                ).fetchone()
                if (
                    active is not None
                    and str(active["repair_id"]) != normalized_repair_id
                ):
                    if (
                        str(active["replacement_run_id"])
                        == normalized_replacement
                        and int(active["document_id"]) == int(document_id)
                    ):
                        continue
                    existing_inactive = connection.execute(
                        """
                        SELECT replacement_run_id, document_id, reason, status
                        FROM semantic_run_replacements
                        WHERE repair_id=? AND superseded_run_id=?
                        """,
                        (normalized_repair_id, superseded_run_id),
                    ).fetchone()
                    if existing_inactive is None:
                        connection.execute(
                            """
                            INSERT INTO semantic_run_replacements(
                                repair_id, document_id, superseded_run_id,
                                replacement_run_id, reason, status,
                                created_at, updated_at
                            ) VALUES(?,?,?,?,?,'rolled_back',?,?)
                            """,
                            (
                                normalized_repair_id,
                                int(document_id),
                                superseded_run_id,
                                normalized_replacement,
                                normalized_reason,
                                timestamp,
                                timestamp,
                            ),
                        )
                    else:
                        same = (
                            str(existing_inactive["replacement_run_id"])
                            == normalized_replacement
                            and int(existing_inactive["document_id"])
                            == int(document_id)
                            and str(existing_inactive["reason"])
                            == normalized_reason
                            and str(existing_inactive["status"])
                            == "rolled_back"
                        )
                        if not same:
                            raise SemanticRunConflict(
                                "intelligence_semantic_repair_conflict"
                            )
                    conflicted += 1
                    continue
                existing = connection.execute(
                    """
                    SELECT replacement_run_id, document_id, reason, status
                    FROM semantic_run_replacements
                    WHERE repair_id=? AND superseded_run_id=?
                    """,
                    (normalized_repair_id, superseded_run_id),
                ).fetchone()
                if existing is not None:
                    same = (
                        str(existing["replacement_run_id"])
                        == normalized_replacement
                        and int(existing["document_id"]) == int(document_id)
                        and str(existing["reason"]) == normalized_reason
                    )
                    if not same:
                        raise SemanticRunConflict(
                            "intelligence_semantic_repair_conflict"
                        )
                    if str(existing["status"]) != "active":
                        connection.execute(
                            """
                            UPDATE semantic_run_replacements
                            SET status='active', updated_at=?
                            WHERE repair_id=? AND superseded_run_id=?
                            """,
                            (timestamp, normalized_repair_id, superseded_run_id),
                        )
                        activated += 1
                    continue
                try:
                    connection.execute(
                        """
                        INSERT INTO semantic_run_replacements(
                            repair_id, document_id, superseded_run_id,
                            replacement_run_id, reason, status,
                            created_at, updated_at
                        ) VALUES(?,?,?,?,?,'active',?,?)
                        """,
                        (
                            normalized_repair_id,
                            int(document_id),
                            superseded_run_id,
                            normalized_replacement,
                            normalized_reason,
                            timestamp,
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise SemanticRunConflict(
                        "intelligence_semantic_repair_conflict"
                    ) from exc
                activated += 1
            connection.commit()
            return {
                "repair_id": normalized_repair_id,
                "replacement_run_id": normalized_replacement,
                "activated": activated,
                "conflicted": conflicted,
                "superseded": len(normalized_superseded),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def rollback_semantic_repair(self, repair_id: str) -> dict[str, object]:
        """Roll back one repair by switching its replacement visibility."""

        normalized_repair_id = str(repair_id).strip()
        if not normalized_repair_id:
            raise ValueError("intelligence_semantic_repair_id_required")
        timestamp = utc_iso()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            present = connection.execute(
                "SELECT COUNT(*) FROM semantic_run_replacements WHERE repair_id=?",
                (normalized_repair_id,),
            ).fetchone()[0]
            if int(present) == 0:
                raise KeyError("intelligence_semantic_repair_not_found")
            cursor = connection.execute(
                """
                UPDATE semantic_run_replacements
                SET status='rolled_back', updated_at=?
                WHERE repair_id=? AND status='active'
                """,
                (timestamp, normalized_repair_id),
            )
            connection.commit()
            return {
                "repair_id": normalized_repair_id,
                "rolled_back": int(cursor.rowcount),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def semantic_candidate_id(run_id: str, event_index: int) -> str:
        payload = f"{str(run_id)}|{int(event_index)}".encode("utf-8")
        return f"candidate-{hashlib.sha256(payload).hexdigest()}"

    def semantic_prior_events(
        self,
        *,
        document_id: int,
        event_type: str,
    ) -> list[dict[str, object]]:
        """Return only earlier observed semantic events for revision matching."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, d.first_seen_at,
                       json_extract(
                           e.metadata_json,
                           '$.canonical_key'
                       ) AS canonical_key,
                       c.lifecycle
                FROM events e
                JOIN documents d ON d.id=e.document_id
                LEFT JOIN event_candidates c
                  ON c.canonical_event_id=e.event_id
                 AND c.validation_status='canonical'
                WHERE e.event_type=?
                  AND e.extraction_method='semantic-v1-validated'
                  AND e.document_id<>?
                  AND d.first_seen_at<=(
                      SELECT first_seen_at
                      FROM documents
                      WHERE id=?
                  )
                ORDER BY d.first_seen_at, e.event_id
                """,
                (str(event_type), int(document_id), int(document_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def persist_semantic_candidate_decision(
        self,
        *,
        run_id: str,
        document_id: int,
        event_index: int,
        event_type: str,
        lifecycle: str,
        payload: dict[str, object],
        validation_errors: Iterable[str] = (),
        evidence_rows: Iterable[dict[str, object]] = (),
        canonical_event: MarketEvent | None = None,
        fact_rows: Iterable[dict[str, object]] = (),
        score_row: dict[str, object] | None = None,
        relation_rows: Iterable[dict[str, object]] = (),
    ) -> dict[str, object]:
        """Persist one immutable quarantine/canonical decision atomically."""

        candidate_id = self.semantic_candidate_id(run_id, event_index)
        normalized_errors = tuple(
            dict.fromkeys(
                str(code).strip()
                for code in validation_errors
                if str(code).strip()
            )
        )
        status = "canonical" if canonical_event is not None else "quarantined"
        if status == "canonical" and normalized_errors:
            raise ValueError("intelligence_semantic_canonical_has_errors")
        if status == "quarantined" and not normalized_errors:
            raise ValueError("intelligence_semantic_quarantine_reason_required")
        canonical_event_id = (
            canonical_event.event_id
            if canonical_event is not None
            else None
        )
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        errors_json = json.dumps(
            normalized_errors,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        timestamp = utc_iso()
        evidence_values = tuple(evidence_rows)
        fact_values = tuple(fact_rows)
        relation_values = tuple(relation_rows)

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT document_id, status
                FROM semantic_runs
                WHERE run_id=?
                """,
                (str(run_id),),
            ).fetchone()
            if run is None:
                raise KeyError("intelligence_semantic_run_not_found")
            if int(run["document_id"]) != int(document_id):
                raise SemanticRunConflict(
                    "intelligence_semantic_candidate_document_conflict"
                )
            if str(run["status"]) != "succeeded":
                raise SemanticRunConflict(
                    "intelligence_semantic_candidate_run_not_succeeded"
                )
            existing = connection.execute(
                "SELECT * FROM event_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    int(existing["document_id"]) == int(document_id)
                    and int(existing["event_index"]) == int(event_index)
                    and str(existing["event_type"]) == str(event_type)
                    and str(existing["lifecycle"]) == str(lifecycle)
                    and str(existing["payload_json"]) == payload_json
                    and str(existing["validation_status"]) == status
                    and str(existing["validation_errors_json"]) == errors_json
                    and existing["canonical_event_id"] == canonical_event_id
                )
                if not same:
                    raise SemanticRunConflict(
                        "intelligence_semantic_candidate_decision_conflict"
                    )
                connection.commit()
                return dict(existing)

            if canonical_event is not None:
                self._insert_market_event_row(connection, canonical_event)
            connection.execute(
                """
                INSERT INTO event_candidates(
                    candidate_id, run_id, document_id, event_index,
                    event_type, lifecycle, payload_json, validation_status,
                    validation_errors_json, canonical_event_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    str(run_id),
                    int(document_id),
                    int(event_index),
                    str(event_type),
                    str(lifecycle),
                    payload_json,
                    status,
                    errors_json,
                    canonical_event_id,
                    timestamp,
                ),
            )
            for evidence in evidence_values:
                connection.execute(
                    """
                    INSERT INTO event_evidence(
                        candidate_id, document_id, evidence_id, chunk_id,
                        page_number, start_char, end_char, quote,
                        normalized_quote_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate_id,
                        int(document_id),
                        str(evidence["evidence_id"]),
                        str(evidence["chunk_id"]),
                        int(evidence["page_number"]),
                        int(evidence["start"]),
                        int(evidence["end"]),
                        str(evidence["quote"]),
                        str(evidence["normalized_quote_hash"]),
                    ),
                )
            if canonical_event is not None:
                for ordinal, fact in enumerate(fact_values):
                    connection.execute(
                        """
                        INSERT INTO event_facts(
                            event_id, fact_name, ordinal, raw_value,
                            numeric_value, text_value, unit, currency,
                            period, evidence_ids_json, provenance
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            canonical_event.event_id,
                            str(fact["name"]),
                            int(fact.get("ordinal", ordinal)),
                            fact.get("raw_value"),
                            fact.get("numeric_value"),
                            fact.get("text_value"),
                            fact.get("unit"),
                            fact.get("currency"),
                            fact.get("period"),
                            json.dumps(
                                tuple(fact.get("evidence_ids", ())),
                                ensure_ascii=True,
                                separators=(",", ":"),
                            ),
                            str(
                                fact.get(
                                    "provenance",
                                    "semantic-v1-validated",
                                )
                            ),
                        ),
                    )
                if score_row is None:
                    raise ValueError(
                        "intelligence_semantic_score_required"
                    )
                connection.execute(
                    """
                    INSERT INTO event_scores(
                        event_id, relevance, novelty, materiality,
                        certainty, source_credibility, direction,
                        confidence, scoring_version, inputs_json, scored_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        canonical_event.event_id,
                        float(score_row["relevance"]),
                        float(score_row["novelty"]),
                        (
                            None
                            if score_row.get("materiality") is None
                            else float(score_row["materiality"])
                        ),
                        float(score_row["certainty"]),
                        float(score_row["source_credibility"]),
                        float(score_row["direction"]),
                        float(score_row["confidence"]),
                        str(score_row["scoring_version"]),
                        json.dumps(
                            score_row.get("inputs", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        timestamp,
                    ),
                )
                for relation in relation_values:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO event_relations(
                            source_event_id, target_event_id,
                            relation_type, available_at
                        ) VALUES(?,?,?,?)
                        """,
                        (
                            canonical_event.event_id,
                            str(relation["target_event_id"]),
                            str(relation["relation_type"]),
                            _strict_utc_iso(
                                relation["available_at"],
                                field="event_relation.available_at",
                            ),
                        ),
                    )
            result = connection.execute(
                "SELECT * FROM event_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            connection.commit()
            if result is None:
                raise SemanticRunConflict(
                    "intelligence_semantic_candidate_write_missing"
                )
            return dict(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _insert_market_event_row(
        connection: sqlite3.Connection,
        event: MarketEvent,
    ) -> None:
        published_at = _strict_utc_iso(
            event.published_at,
            field="event.published_at",
        )
        effective_at = _strict_utc_iso(
            event.effective_at,
            field="event.effective_at",
        )
        connection.execute(
            """
            INSERT INTO events(
                event_id, document_id, event_type, direction, strength,
                confidence, novelty, horizon_days, published_at,
                effective_at, evidence, extraction_method, metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                int(event.document_id),
                event.event_type,
                float(event.direction),
                float(event.strength),
                float(event.confidence),
                float(event.novelty),
                int(event.horizon_days),
                published_at,
                effective_at,
                event.evidence,
                event.extraction_method,
                json.dumps(
                    event.metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        for entity in event.entities:
            connection.execute(
                """
                INSERT INTO event_entities(
                    event_id, entity_type, entity_id,
                    entity_name, industry, confidence
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    str(entity.get("entity_type") or "security"),
                    str(entity.get("entity_id") or ""),
                    str(entity.get("entity_name") or ""),
                    str(entity.get("industry") or ""),
                    float(entity.get("confidence") or 0.0),
                ),
            )

    @staticmethod
    def _optional_nonnegative(
        value: int | None,
        field: str,
    ) -> int | None:
        if value is None:
            return None
        normalized = int(value)
        if normalized < 0:
            raise ValueError(
                f"intelligence_semantic_{field}_invalid"
            )
        return normalized

    @staticmethod
    def _bounded_semantic_error(error: str) -> str:
        raw = str(error).strip()
        if not raw:
            return ""
        if (
            len(raw) > 200
            or any(
                not character.isascii()
                or (
                    not character.isalnum()
                    and character not in "_.:-"
                )
                for character in raw
            )
        ):
            return "semantic_run_failed"
        return raw

    def documents(self, *, source: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM documents"
        params: tuple[str, ...] = ()
        if source:
            query += " WHERE source=?"
            params = (source,)
        query += " ORDER BY published_at, id"
        with self.connect() as connection:
            return pd.read_sql_query(query, connection, params=params)

    def pending_documents(self, limit: int = 500) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM documents
                    WHERE status='collected'
                    ORDER BY queue_priority DESC,
                             live_observed DESC,
                             published_at,
                             id
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            )

    def document_content(self, row: sqlite3.Row) -> bytes:
        raw_path = str(row["raw_path"] or "")
        if raw_path:
            return (self.root / raw_path).read_bytes()
        if str(row["source"]) == "tushare_announcement":
            return b""
        raise FileNotFoundError("intelligence_document_raw_path_missing")

    def prune_unreferenced_raw_files(
        self,
        *,
        source: str,
    ) -> dict[str, object]:
        normalized_source = str(source).strip()
        if normalized_source != "tushare_announcement":
            raise ValueError("intelligence_raw_prune_source_invalid")
        with self.connect() as connection:
            referenced = {
                Path(str(row["raw_path"])).as_posix()
                for row in connection.execute(
                    """
                    SELECT raw_path
                    FROM documents
                    WHERE source=? AND raw_path<>''
                    """,
                    (normalized_source,),
                ).fetchall()
            }

        source_root = self.raw_root / normalized_source
        scanned_files = 0
        deleted_files = 0
        deleted_bytes = 0
        retained_files = 0
        if source_root.exists():
            for path in source_root.rglob("*"):
                if not path.is_file():
                    continue
                scanned_files += 1
                relative = path.relative_to(self.root).as_posix()
                if relative in referenced:
                    retained_files += 1
                    continue
                deleted_bytes += path.stat().st_size
                path.unlink()
                deleted_files += 1
            for directory in sorted(
                (
                    path
                    for path in source_root.rglob("*")
                    if path.is_dir()
                ),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            try:
                source_root.rmdir()
            except OSError:
                pass
        return {
            "status": "complete",
            "source": normalized_source,
            "scanned_files": scanned_files,
            "deleted_files": deleted_files,
            "deleted_bytes": deleted_bytes,
            "retained_files": retained_files,
        }

    def known_fingerprints(self, *, limit: int = 100_000) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    json_extract(metadata_json, '$.document_fingerprint') AS document_fingerprint,
                    json_extract(metadata_json, '$.event_fingerprint') AS event_fingerprint
                FROM events
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return {
            str(value)
            for row in rows
            for value in (row["document_fingerprint"], row["event_fingerprint"])
            if str(value or "")
        }

    def mark_document(self, document_id: int, status: str) -> None:
        if status not in {"collected", "processed", "no_event", "parse_failed"}:
            raise ValueError("intelligence_document_status_invalid")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET status=?,
                    extracted_link_revision=CASE
                      WHEN ? IN ('processed', 'no_event')
                      THEN link_revision
                      ELSE extracted_link_revision
                    END
                WHERE id=?
                """,
                (status, status, document_id),
            )

    def events_as_of(
        self,
        as_of: str,
        *,
        market: str | None = None,
        availability_policy: AvailabilityPolicy = "observed",
    ) -> pd.DataFrame:
        if availability_policy not in {"observed", "research"}:
            raise ValueError(
                f"unknown_availability_policy:{availability_policy}"
            )
        availability_expression = "d.first_seen_at"
        if availability_policy == "research":
            availability_expression = """
                CASE
                    WHEN d.published_at <= (
                           SELECT value FROM intelligence_settings
                           WHERE key='historical_cutoff'
                         )
                     AND a.availability_provenance IN (
                         'reconstructed_rec_time',
                         'reconstructed_next_open'
                     )
                     AND a.research_available_at >= d.published_at
                     AND a.research_available_at <= d.first_seen_at
                     AND (
                         a.source_recorded_at IS NULL
                         OR (
                             a.source_recorded_at >= d.published_at
                             AND a.source_recorded_at <= d.first_seen_at
                         )
                     )
                    THEN a.research_available_at
                    ELSE d.first_seen_at
                END
            """
        query = f"""
            SELECT e.*, x.entity_type, x.entity_id, x.entity_name, x.industry,
                   x.confidence AS entity_confidence, d.source, d.title, d.source_url,
                   CASE
                       WHEN e.effective_at >= {availability_expression}
                       THEN e.effective_at
                       ELSE {availability_expression}
                   END AS available_at
            FROM events e
            JOIN documents d ON d.id=e.document_id
            LEFT JOIN document_availability a ON a.document_id=d.id
            LEFT JOIN event_entities x ON x.event_id=e.event_id
            WHERE e.effective_at <= :as_of
              AND {availability_expression} <= :as_of
              AND NOT EXISTS (
                  SELECT 1
                  FROM event_candidates repair_candidate
                  JOIN semantic_run_replacements replacement
                    ON (
                      replacement.status='active'
                      AND replacement.superseded_run_id=
                          repair_candidate.run_id
                    ) OR (
                      replacement.status='rolled_back'
                      AND replacement.replacement_run_id=
                          repair_candidate.run_id
                    )
                  WHERE repair_candidate.canonical_event_id=e.event_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM event_relations r
                  WHERE r.target_event_id=e.event_id
                    AND r.relation_type IN (
                        'revises', 'cancels', 'supersedes', 'duplicates'
                    )
                    AND r.available_at<=:as_of
              )
        """
        normalized_as_of = _strict_utc_iso(as_of, field="as_of")
        params: dict[str, str] = {"as_of": normalized_as_of}
        if market:
            query += (
                " AND json_extract(e.metadata_json, '$.market')"
                " IN (:market, 'all')"
            )
            params["market"] = market
        query += " ORDER BY e.effective_at, e.event_id"
        with self.connect() as connection:
            return pd.read_sql_query(query, connection, params=params)

    def latest_health(self) -> pd.DataFrame:
        with self.connect() as connection:
            return pd.read_sql_query(
                """
                SELECT r.* FROM ingestion_runs r
                JOIN (
                    SELECT source, MAX(started_at) AS started_at
                    FROM ingestion_runs GROUP BY source
                ) latest ON latest.source=r.source AND latest.started_at=r.started_at
                ORDER BY r.source
                """,
                connection,
            )

    def record_source_audit(
        self,
        *,
        run_id: str,
        as_of_date: str,
        dataset_scope: str,
        primary_source: str,
        secondary_source: str,
        status: str,
        supplement_enabled: bool,
        metrics: dict,
        items: Iterable[dict],
        started_at: str,
        finished_at: str,
    ) -> None:
        allowed_statuses = {
            "success",
            "degraded",
            "failed",
        }
        if status not in allowed_statuses:
            raise ValueError("source_audit_status_invalid")
        normalized_started = _strict_utc_iso(
            started_at,
            field="source_audit.started_at",
        )
        normalized_finished = _strict_utc_iso(
            finished_at,
            field="source_audit.finished_at",
        )
        metrics_json = json.dumps(
            metrics,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            default=str,
        )
        normalized_items = []
        allowed_item_statuses = {
            "matched",
            "mismatch",
            "primary_only",
            "secondary_only",
            "supplemented",
        }
        for item in items:
            comparison_status = str(
                item.get("comparison_status") or ""
            )
            if comparison_status not in allowed_item_statuses:
                raise ValueError(
                    "source_audit_item_status_invalid"
                )
            normalized_items.append(
                (
                    str(run_id),
                    str(item.get("dataset") or ""),
                    str(item.get("item_key") or ""),
                    comparison_status,
                    str(item.get("primary_id") or ""),
                    str(item.get("secondary_id") or ""),
                    json.dumps(
                        item.get("detail") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                        default=str,
                    ),
                )
            )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO source_audit_runs(
                    run_id, as_of_date, dataset_scope,
                    primary_source, secondary_source, status,
                    supplement_enabled, metrics_json,
                    started_at, finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(run_id),
                    str(as_of_date),
                    str(dataset_scope),
                    str(primary_source),
                    str(secondary_source),
                    status,
                    int(bool(supplement_enabled)),
                    metrics_json,
                    normalized_started,
                    normalized_finished,
                ),
            )
            connection.executemany(
                """
                INSERT INTO source_audit_items(
                    run_id, dataset, item_key, comparison_status,
                    primary_id, secondary_id, detail_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                normalized_items,
            )

    def _raw_path_value(
        self,
        document: SourceDocument,
        *,
        published_at: str,
        content_hash: str,
        payload: bytes,
    ) -> str:
        if (
            str(document.metadata.get("content_scope") or "").strip()
            == "title_metadata"
        ):
            return ""
        raw_path = self._write_raw(
            document.source,
            published_at,
            content_hash,
            payload,
        )
        return str(raw_path.relative_to(self.root))

    def _write_raw(self, source: str, published_at: str, content_hash: str, payload: bytes) -> Path:
        year_month = utc_iso(published_at)[:7].replace("-", "/")
        destination = self.raw_root / source / year_month / content_hash
        if destination.exists():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{content_hash}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return destination

    def record_quality(self, run_id: str, source: str, metrics: Iterable[tuple[str, float, str]]) -> None:
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO quality_results(run_id, source, metric, value, detail, measured_at) VALUES(?,?,?,?,?,?)",
                [(run_id, source, name, float(value), detail, utc_iso()) for name, value, detail in metrics],
            )
