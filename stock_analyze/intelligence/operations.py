"""Bounded operational orchestration for announcement intelligence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, urlsplit

import yaml

from .blob_store import (
    BlobStore,
    BlobStoreConfigurationError,
    OssBlobStore,
    build_blob_store,
    parsed_object_key,
)
from .diagnostics import (
    build_semantic_status_report,
    write_semantic_status_report,
)
from .document_parser import (
    AnnouncementDocumentParser,
    DocumentParserConfig,
)
from .pdf_fetcher import (
    AnnouncementPdfFetcher,
    PdfArtifactConflict,
    RetryablePdfFetchError,
    SecurePdfDownloader,
    TerminalPdfFetchError,
)
from .store import IntelligenceStore
from .types import utc_iso


RECONCILE_STAGES = (
    "metadata",
    "enqueue",
    "download",
    "parse",
)
ENRICH_STAGES = (
    "enqueue",
    "download",
    "parse",
)


@dataclass(frozen=True)
class StageResult:
    stage: str
    processed: int = 0
    succeeded: int = 0
    retryable_failures: int = 0
    terminal_failures: int = 0
    next_queue_depth: int = 0
    status: str = "complete"
    details: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "processed": max(0, int(self.processed)),
            "succeeded": max(0, int(self.succeeded)),
            "retryable_failures": max(
                0, int(self.retryable_failures)
            ),
            "terminal_failures": max(
                0, int(self.terminal_failures)
            ),
            "next_queue_depth": max(0, int(self.next_queue_depth)),
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class SourceWideFailure(RuntimeError):
    """A source-level failure for which continuing would be misleading."""

    def __init__(self, category: str, code: str) -> None:
        self.category = str(category)
        self.code = str(code)
        super().__init__(self.code)


class FatalOperationError(RuntimeError):
    """Stable CLI boundary for source-wide or local integrity failures."""

    def __init__(
        self,
        category: str,
        report: Mapping[str, object],
    ) -> None:
        self.category = str(category)
        self.report = dict(report)
        super().__init__(self.category)


class IntelligenceStageRunner(Protocol):
    def reconcile_metadata(
        self, *, lookback_days: int, limit: int
    ) -> StageResult: ...

    def enqueue_missing_artifacts(self, *, limit: int) -> StageResult: ...

    def download(self, *, limit: int) -> StageResult: ...

    def parse(self, *, limit: int) -> StageResult: ...

    def status(self) -> dict[str, object]: ...


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SourceWideFailure("schema", code)
    return value


def _load_config(root: Path) -> Mapping[str, object]:
    path = root / "configs" / "intelligence_semantic.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SourceWideFailure(
            "schema", "intelligence_semantic_config_invalid"
        ) from exc
    return _mapping(payload, "intelligence_semantic_config_invalid")


def _artifact_id(
    document_id: int,
    pdf_hash: str,
    parser_version: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"document:{int(document_id)}:parsed:"
            f"{pdf_hash}:{parser_version}"
        ).encode("utf-8")
    ).hexdigest()
    return f"parsed-{digest}"


def _safe_error_code(error: Exception, fallback: str) -> str:
    code = str(getattr(error, "code", "") or str(error) or fallback)
    if (
        len(code) > 200
        or not code
        or any(
            not character.isascii()
            or (
                not character.isalnum()
                and character not in "_.:-"
            )
            for character in code
        )
    ):
        return fallback
    return code


def _resolve_source_pdf_url(source_url: str) -> str:
    """Resolve Tushare's CNInfo detail link to its immutable PDF URL."""

    value = str(source_url).strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if (
        parsed.scheme.casefold() == "http"
        and parsed.hostname
        in {
            "static.cninfo.com.cn",
            "www.cninfo.com.cn",
            "dataclouds.cninfo.com.cn",
        }
    ):
        parsed = parsed._replace(scheme="https")
        value = parsed.geturl()
    if (
        parsed.hostname == "static.cninfo.com.cn"
        and parsed.path.startswith("/fina/")
    ):
        parsed = parsed._replace(
            path=f"/finalpage/{parsed.path[len('/fina/'):]}"
        )
        value = parsed.geturl()
    if (
        parsed.hostname != "www.cninfo.com.cn"
        or parsed.path.rstrip("/") != "/new/disclosure/detail"
    ):
        return value
    query = parse_qs(parsed.query, keep_blank_values=False)
    announcement_id = str(
        (query.get("announcementId") or [""])[0]
    ).strip()
    announcement_date = str(
        (query.get("announcementTime") or [""])[0]
    ).strip()
    if not announcement_id.isdigit():
        return value
    try:
        normalized_date = datetime.strptime(
            announcement_date,
            "%Y-%m-%d",
        ).date().isoformat()
    except ValueError:
        return value
    return (
        "https://static.cninfo.com.cn/finalpage/"
        f"{normalized_date}/{announcement_id}.PDF"
    )


class _SourcePdfDownloader:
    def __init__(self, delegate: SecurePdfDownloader) -> None:
        self.delegate = delegate

    def fetch(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
    ):
        return self.delegate.fetch(
            _resolve_source_pdf_url(url),
            expected_sha256=expected_sha256,
        )


class _AliyunOssBucketCompatibility:
    """Use OSS-native atomic create because OSS rejects If-None-Match."""

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate

    @staticmethod
    def _headers(value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {
            str(key): item
            for key, item in value.items()
            if str(key).casefold() != "if-none-match"
        }

    def put_object(self, *args, **kwargs):
        positional = list(args)
        if len(positional) >= 3:
            positional[2] = self._headers(positional[2])
        elif "headers" in kwargs:
            kwargs = dict(kwargs)
            kwargs["headers"] = self._headers(kwargs["headers"])
        return self.delegate.put_object(*positional, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class DefaultIntelligenceStageRunner:
    """Production runner for metadata and document artifacts."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(repo_root)
        self.store = IntelligenceStore(
            self.root / "data" / "shared" / "intelligence"
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._config: Mapping[str, object] | None = None
        self._blob_store: BlobStore | None = None

    @property
    def config(self) -> Mapping[str, object]:
        if self._config is None:
            self._config = _load_config(self.root)
        return self._config

    def _build_blob_store(self) -> BlobStore:
        try:
            blob_store = build_blob_store(
                self.config,
                production=True,
            )
            if isinstance(blob_store, OssBlobStore):
                blob_store = OssBlobStore(
                    endpoint=blob_store.endpoint,
                    bucket_name=blob_store.bucket_name,
                    key_prefix=blob_store.key_prefix,
                    bucket_client=_AliyunOssBucketCompatibility(
                        blob_store._bucket
                    ),
                )
            return blob_store
        except BlobStoreConfigurationError as exc:
            code = _safe_error_code(
                exc, "intelligence_blob_store_unavailable"
            )
            category = (
                "authorization"
                if any(
                    marker in code
                    for marker in (
                        "credential",
                        "missing_env",
                        "client_initialization",
                    )
                )
                else "schema"
            )
            raise SourceWideFailure(
                category,
                code,
            ) from None

    @property
    def blob_store(self) -> BlobStore:
        if self._blob_store is None:
            self._blob_store = self._build_blob_store()
        return self._blob_store

    def reconcile_metadata(
        self,
        *,
        lookback_days: int,
        limit: int,
    ) -> StageResult:
        del limit
        from .ingestion import IntelligencePipeline

        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SourceWideFailure(
                "schema", "intelligence_clock_must_be_aware"
            )
        since = now - timedelta(days=max(0, int(lookback_days)))
        try:
            result = IntelligencePipeline(self.root).ingest(
                since=since.astimezone(timezone.utc).isoformat(),
                until=now.astimezone(timezone.utc).isoformat(),
                sources={"tushare_announcement"},
            )
        except sqlite3.DatabaseError as exc:
            raise SourceWideFailure(
                "database", "intelligence_database_failure"
            ) from exc
        sources = [
            item
            for item in result.get("sources", [])
            if isinstance(item, Mapping)
        ]
        failed = [
            item for item in sources if item.get("status") == "failed"
        ]
        if failed:
            error = str(failed[0].get("error") or "")
            normalized = error.casefold()
            if any(
                marker in normalized
                for marker in (
                    "token",
                    "permission",
                    "authorization",
                    "权限",
                    "积分",
                    "entitlement",
                )
            ):
                raise SourceWideFailure(
                    "authorization",
                    "tushare_authorization_failed",
                )
            return StageResult(
                stage="metadata",
                processed=len(sources),
                retryable_failures=len(failed),
                next_queue_depth=len(failed),
                status="partial",
            )
        return StageResult(
            stage="metadata",
            processed=len(sources),
            succeeded=len(sources),
        )

    def enqueue_missing_artifacts(self, *, limit: int) -> StageResult:
        bounded = max(1, int(limit))
        timestamp = utc_iso()
        try:
            with self.store.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT d.id
                    FROM documents d
                    WHERE d.source='tushare_announcement'
                      AND d.source_url<>''
                      AND NOT EXISTS (
                        SELECT 1
                        FROM document_artifacts a
                        WHERE a.document_id=d.id
                          AND a.artifact_type='pdf'
                          AND a.status IN (
                            'queued', 'downloaded', 'parsed',
                            'ocr_required', 'ocr_failed',
                            'failed_retryable', 'failed_terminal'
                          )
                      )
                    ORDER BY d.queue_priority DESC,
                             d.live_observed DESC,
                             d.published_at,
                             d.id
                    LIMIT ?
                    """,
                    (bounded,),
                ).fetchall()
                for row in rows:
                    document_id = int(row["id"])
                    artifact_id = self.store._pdf_artifact_id(document_id)
                    connection.execute(
                        """
                        INSERT INTO document_artifacts(
                            artifact_id, document_id, artifact_type,
                            content_hash, storage_uri, mime_type, byte_size,
                            parser_version, status, error,
                            created_at, updated_at
                        ) VALUES(
                            ?, ?, 'pdf', '', '', 'application/pdf', 0,
                            '', 'queued', '', ?, ?
                        )
                        """,
                        (
                            artifact_id,
                            document_id,
                            timestamp,
                            timestamp,
                        ),
                    )
                remaining = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM documents d
                        WHERE d.source='tushare_announcement'
                          AND d.source_url<>''
                          AND NOT EXISTS (
                            SELECT 1
                            FROM document_artifacts a
                            WHERE a.document_id=d.id
                              AND a.artifact_type='pdf'
                          )
                        """
                    ).fetchone()[0]
                )
                connection.commit()
        except sqlite3.DatabaseError as exc:
            raise SourceWideFailure(
                "database", "intelligence_database_failure"
            ) from exc
        return StageResult(
            stage="enqueue",
            processed=len(rows),
            succeeded=len(rows),
            next_queue_depth=remaining,
        )

    def _pdf_fetcher(
        self,
        *,
        blob_store: BlobStore | None = None,
    ) -> AnnouncementPdfFetcher:
        artifact = _mapping(
            self.config.get("artifact_store"),
            "intelligence_artifact_config_invalid",
        )
        downloader = SecurePdfDownloader(
            allowed_hosts=tuple(artifact.get("allowed_hosts") or ()),
            max_bytes=int(artifact.get("max_pdf_bytes") or 52_428_800),
            max_attempts=int(
                artifact.get("download_max_attempts") or 2
            ),
            connect_timeout_seconds=float(
                artifact.get(
                    "download_connect_timeout_seconds",
                    5,
                )
            ),
            read_timeout_seconds=float(
                artifact.get(
                    "download_read_timeout_seconds",
                    15,
                )
            ),
            total_timeout_seconds=float(
                artifact.get(
                    "download_total_timeout_seconds",
                    30,
                )
            ),
            temp_root=(
                self.root
                / "data"
                / "shared"
                / "intelligence"
                / "tmp"
            ),
        )
        return AnnouncementPdfFetcher(
            self.store,
            blob_store or self.blob_store,
            _SourcePdfDownloader(downloader),
        )

    def download(self, *, limit: int) -> StageResult:
        bounded = max(1, int(limit))
        try:
            with self.store.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT d.id
                    FROM documents d
                    JOIN document_artifacts a
                      ON a.document_id=d.id AND a.artifact_type='pdf'
                    WHERE a.status IN ('queued', 'failed_retryable')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM artifact_worker_items wi
                        JOIN artifact_worker_jobs wj
                          ON wj.job_id=wi.job_id
                        WHERE wi.document_id=d.id
                          AND wj.stage='download'
                          AND wj.status IN ('leased', 'importing')
                          AND wj.lease_until>?
                      )
                    ORDER BY d.queue_priority DESC,
                             d.live_observed DESC,
                             d.published_at,
                             d.id
                    LIMIT ?
                    """,
                    (utc_iso(), bounded),
                ).fetchall()
            artifact = _mapping(
                self.config.get("artifact_store"),
                "intelligence_artifact_config_invalid",
            )
            worker_count = min(
                max(1, int(artifact.get("download_workers") or 1)),
                8,
                len(rows) or 1,
            )
            # Resolve and validate production storage before worker threads
            # start. Each OSS worker creates its own SDK client below.
            self.blob_store
        except sqlite3.DatabaseError as exc:
            raise SourceWideFailure(
                "database", "intelligence_database_failure"
            ) from exc
        retryable = 0
        terminal = 0
        succeeded = 0
        thread_state = threading.local()

        def fetch_one(document_id: int) -> str:
            fetcher = getattr(thread_state, "fetcher", None)
            if fetcher is None:
                worker_blob_store = self.blob_store
                if isinstance(worker_blob_store, OssBlobStore):
                    worker_blob_store = self._build_blob_store()
                    fetcher = self._pdf_fetcher(
                        blob_store=worker_blob_store
                    )
                else:
                    fetcher = self._pdf_fetcher()
                thread_state.fetcher = fetcher
            try:
                fetcher.fetch(document_id)
                return "succeeded"
            except RetryablePdfFetchError:
                return "retryable"
            except (TerminalPdfFetchError, PdfArtifactConflict):
                return "terminal"
            except sqlite3.DatabaseError as exc:
                raise SourceWideFailure(
                    "database", "intelligence_database_failure"
                ) from exc
            except Exception:
                return "retryable"

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="intelligence-pdf",
        ) as executor:
            futures = {
                executor.submit(fetch_one, int(row["id"]))
                for row in rows
            }
            for future in as_completed(futures):
                outcome = future.result()
                succeeded += int(outcome == "succeeded")
                retryable += int(outcome == "retryable")
                terminal += int(outcome == "terminal")
        with self.store.connect() as connection:
            remaining = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM document_artifacts
                    WHERE artifact_type='pdf'
                      AND status IN ('queued', 'failed_retryable')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM artifact_worker_items wi
                        JOIN artifact_worker_jobs wj
                          ON wj.job_id=wi.job_id
                        WHERE wi.document_id=
                              document_artifacts.document_id
                          AND wj.stage='download'
                          AND wj.status IN ('leased', 'importing')
                          AND wj.lease_until>?
                      )
                    """,
                    (utc_iso(),),
                ).fetchone()[0]
            )
        return StageResult(
            stage="download",
            processed=len(rows),
            succeeded=succeeded,
            retryable_failures=retryable,
            terminal_failures=terminal,
            next_queue_depth=remaining,
            status="partial" if retryable or terminal else "complete",
        )

    def _parser(self) -> AnnouncementDocumentParser:
        parser = _mapping(
            self.config.get("parser"),
            "intelligence_parser_config_invalid",
        )
        return AnnouncementDocumentParser(
            config=DocumentParserConfig.from_mapping(parser)
        )

    def _persist_parse_result(
        self,
        *,
        document_id: int,
        artifact_id: str,
        result,
    ) -> None:
        raw = json.dumps(
            asdict(result),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(payload).hexdigest()
        uri = self.blob_store.put_if_absent(
            parsed_object_key(result.parser_version, digest),
            payload,
            "application/gzip",
        )
        status = (
            result.status
            if result.status in {"parsed", "ocr_failed"}
            else "failed_terminal"
        )
        error = _safe_error_code(
            RuntimeError(result.error or result.status),
            "document_parse_failed",
        )
        if status == "parsed":
            error = ""
        timestamp = utc_iso()
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT *
                FROM document_artifacts
                WHERE artifact_id=?
                """,
                (artifact_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    int(existing["document_id"]) == int(document_id)
                    and str(existing["content_hash"]) == digest
                    and str(existing["storage_uri"]) == uri
                    and str(existing["parser_version"])
                    == result.parser_version
                )
                if not same:
                    raise SourceWideFailure(
                        "database",
                        "parsed_artifact_immutable_conflict",
                    )
                connection.commit()
                return
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(
                    ?, ?, 'parsed', ?, ?, 'application/gzip', ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    artifact_id,
                    int(document_id),
                    digest,
                    uri,
                    len(payload),
                    result.parser_version,
                    status,
                    error,
                    timestamp,
                    timestamp,
                ),
            )
            for page in result.pages:
                for chunk in page.chunks:
                    connection.execute(
                        """
                        INSERT INTO document_chunks(
                            chunk_id, document_id, artifact_id, sequence_no,
                            page_number, section, bbox_json, text, text_hash,
                            ocr_used, ocr_confidence, parser_version
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.chunk_id,
                            int(document_id),
                            artifact_id,
                            int(chunk.sequence_no),
                            int(chunk.page_number),
                            chunk.section,
                            json.dumps(list(chunk.bbox)),
                            chunk.text,
                            chunk.text_hash,
                            int(chunk.ocr_used),
                            chunk.ocr_confidence,
                            result.parser_version,
                        ),
                    )
            for table in result.tables:
                connection.execute(
                    """
                    INSERT INTO document_tables(
                        table_id, document_id, artifact_id, page_number,
                        sequence_no, bbox_json, cells_json, parser_version
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        table.table_id,
                        int(document_id),
                        artifact_id,
                        int(table.page_number),
                        int(table.sequence_no),
                        json.dumps(list(table.bbox)),
                        json.dumps(
                            [asdict(cell) for cell in table.cells],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        result.parser_version,
                    ),
                )
            connection.commit()

    def parse(self, *, limit: int) -> StageResult:
        bounded = max(1, int(limit))
        parser = self._parser()
        parser_config = _mapping(
            self.config.get("parser"),
            "intelligence_parser_config_invalid",
        )
        artifact_config = _mapping(
            self.config.get("artifact_store"),
            "intelligence_artifact_config_invalid",
        )
        minimum_free_bytes = int(
            artifact_config.get("minimum_ecs_free_bytes")
            or 0
        )
        if (
            minimum_free_bytes > 0
            and shutil.disk_usage(self.root).free
            < minimum_free_bytes
        ):
            raise SourceWideFailure(
                "capacity",
                "intelligence_ecs_free_space_below_floor",
            )
        try:
            with self.store.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT a.*
                    FROM document_artifacts a
                    JOIN documents d ON d.id=a.document_id
                    WHERE a.artifact_type='pdf'
                      AND a.status='downloaded'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM document_artifacts p
                        WHERE p.document_id=a.document_id
                          AND p.artifact_type='parsed'
                          AND p.parser_version=?
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM artifact_worker_items wi
                        JOIN artifact_worker_jobs wj
                          ON wj.job_id=wi.job_id
                        WHERE wi.document_id=a.document_id
                          AND wj.stage='parse'
                          AND wj.status IN ('leased', 'importing')
                          AND wj.lease_until>?
                      )
                    ORDER BY d.queue_priority DESC,
                             d.live_observed DESC,
                             a.updated_at,
                             a.document_id
                    LIMIT ?
                    """,
                    (
                        parser.config.parser_version,
                        utc_iso(),
                        bounded,
                    ),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise SourceWideFailure(
                "database", "intelligence_database_failure"
            ) from exc
        retryable = 0
        terminal = 0
        succeeded = 0
        worker_count = min(
            max(1, int(parser_config.get("workers") or 1)),
            4,
            len(rows) or 1,
        )

        def parse_one(row) -> str:
            document_id = int(row["document_id"])
            artifact_id = _artifact_id(
                document_id,
                str(row["content_hash"]),
                parser.config.parser_version,
            )
            try:
                pdf_bytes = self.blob_store.read(str(row["storage_uri"]))
                result = parser.parse(
                    pdf_bytes,
                    document_id=document_id,
                    artifact_id=artifact_id,
                )
                self._persist_parse_result(
                    document_id=document_id,
                    artifact_id=artifact_id,
                    result=result,
                )
                if result.status in {"parsed", "ocr_failed"}:
                    return "succeeded"
                return "terminal"
            except SourceWideFailure:
                raise
            except sqlite3.DatabaseError as exc:
                raise SourceWideFailure(
                    "database", "intelligence_database_failure"
                ) from exc
            except Exception:
                return "retryable"

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="intelligence-parse",
        ) as executor:
            futures = {
                executor.submit(parse_one, row)
                for row in rows
            }
            for future in as_completed(futures):
                outcome = future.result()
                succeeded += int(outcome == "succeeded")
                retryable += int(outcome == "retryable")
                terminal += int(outcome == "terminal")
        with self.store.connect() as connection:
            remaining = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM document_artifacts a
                    WHERE a.artifact_type='pdf'
                      AND a.status='downloaded'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM document_artifacts p
                        WHERE p.document_id=a.document_id
                          AND p.artifact_type='parsed'
                          AND p.parser_version=?
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM artifact_worker_items wi
                        JOIN artifact_worker_jobs wj
                          ON wj.job_id=wi.job_id
                        WHERE wi.document_id=a.document_id
                          AND wj.stage='parse'
                          AND wj.status IN ('leased', 'importing')
                          AND wj.lease_until>?
                      )
                    """,
                    (parser.config.parser_version, utc_iso()),
                ).fetchone()[0]
            )
        return StageResult(
            stage="parse",
            processed=len(rows),
            succeeded=succeeded,
            retryable_failures=retryable,
            terminal_failures=terminal,
            next_queue_depth=remaining,
            status="partial" if retryable or terminal else "complete",
        )

    def status(self) -> dict[str, object]:
        return build_semantic_status_report(self.root)


def _validate_stages(
    stages: Sequence[str],
    *,
    allowed: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(str(stage).strip() for stage in stages)
    if not normalized or any(stage not in allowed for stage in normalized):
        raise ValueError("intelligence_operation_stage_invalid")
    if len(set(normalized)) != len(normalized):
        raise ValueError("intelligence_operation_stage_duplicate")
    positions = [allowed.index(stage) for stage in normalized]
    if positions != sorted(positions):
        raise ValueError("intelligence_operation_stage_order_invalid")
    return normalized


def _execute(
    repo_root: str | Path,
    *,
    runner: IntelligenceStageRunner,
    stages: Sequence[str],
    limit: int,
    lookback_days: int | None,
    now: Callable[[], datetime] | None,
) -> dict[str, object]:
    if int(limit) <= 0:
        raise ValueError("intelligence_operation_limit_invalid")
    started = time.monotonic()
    counts: dict[str, dict[str, object]] = {}
    fatal: SourceWideFailure | None = None
    for stage in stages:
        try:
            if stage == "metadata":
                result = runner.reconcile_metadata(
                    lookback_days=max(0, int(lookback_days or 0)),
                    limit=int(limit),
                )
            elif stage == "enqueue":
                result = runner.enqueue_missing_artifacts(
                    limit=int(limit)
                )
            else:
                result = getattr(runner, stage)(limit=int(limit))
            counts[stage] = result.to_dict()
        except SourceWideFailure as exc:
            fatal = exc
            counts[stage] = StageResult(
                stage=stage,
                terminal_failures=1,
                status="failed",
                details={"error": exc.category},
            ).to_dict()
            break
        except sqlite3.DatabaseError:
            fatal = SourceWideFailure(
                "database", "intelligence_database_failure"
            )
            counts[stage] = StageResult(
                stage=stage,
                terminal_failures=1,
                status="failed",
                details={"error": "database"},
            ).to_dict()
            break
        except (ValueError, json.JSONDecodeError, yaml.YAMLError):
            fatal = SourceWideFailure(
                "schema", "intelligence_schema_failure"
            )
            counts[stage] = StageResult(
                stage=stage,
                terminal_failures=1,
                status="failed",
                details={"error": "schema"},
            ).to_dict()
            break

    try:
        status_report = runner.status()
        immutable, latest = write_semantic_status_report(
            repo_root,
            status_report,
            now=now,
        )
    except sqlite3.DatabaseError as exc:
        fatal = fatal or SourceWideFailure(
            "database", "intelligence_database_failure"
        )
        immutable = Path("")
        latest = Path("")
        status_report = {}
        if fatal is None:
            raise SourceWideFailure(
                "database", "intelligence_database_failure"
            ) from exc
    retryable = sum(
        int(value.get("retryable_failures", 0))
        for value in counts.values()
    )
    terminal = sum(
        int(value.get("terminal_failures", 0))
        for value in counts.values()
    )
    next_depth = max(
        (
            int(value.get("next_queue_depth", 0))
            for value in counts.values()
        ),
        default=0,
    )
    operation_status = (
        "failed"
        if fatal
        else "partial"
        if retryable or terminal
        else "complete"
    )
    report: dict[str, object] = {
        "status": operation_status,
        "counts": counts,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "retryable_failures": retryable,
        "terminal_failures": terminal,
        "next_queue_depth": next_depth,
        "report_path": str(immutable) if str(immutable) else None,
        "latest_report_path": str(latest) if str(latest) else None,
    }
    if fatal:
        report["error"] = fatal.category
        raise FatalOperationError(fatal.category, report)
    return report


def run_intelligence_reconcile(
    repo_root: str | Path,
    *,
    lookback_days: int = 2,
    limit: int = 500,
    stages: Sequence[str] = RECONCILE_STAGES,
    runner: IntelligenceStageRunner | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    if int(lookback_days) < 0:
        raise ValueError("intelligence_lookback_days_invalid")
    normalized = _validate_stages(stages, allowed=RECONCILE_STAGES)
    selected = runner or DefaultIntelligenceStageRunner(repo_root)
    return _execute(
        repo_root,
        runner=selected,
        stages=normalized,
        limit=limit,
        lookback_days=lookback_days,
        now=now,
    )


def run_intelligence_enrich(
    repo_root: str | Path,
    *,
    limit: int = 500,
    stages: Sequence[str] = ENRICH_STAGES,
    runner: IntelligenceStageRunner | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    normalized = _validate_stages(stages, allowed=ENRICH_STAGES)
    selected = runner or DefaultIntelligenceStageRunner(repo_root)
    return _execute(
        repo_root,
        runner=selected,
        stages=normalized,
        limit=limit,
        lookback_days=None,
        now=now,
    )


def run_semantic_status(
    repo_root: str | Path,
) -> dict[str, object]:
    report = build_semantic_status_report(repo_root)
    immutable, latest = write_semantic_status_report(
        repo_root,
        report,
    )
    return {
        **report,
        "report_path": str(immutable),
        "latest_report_path": str(latest),
    }


def run_intelligence_prune_raw(
    repo_root: str | Path,
    *,
    source: str,
) -> dict[str, object]:
    root = Path(repo_root)
    store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    return store.prune_unreferenced_raw_files(source=source)


__all__ = [
    "DefaultIntelligenceStageRunner",
    "ENRICH_STAGES",
    "FatalOperationError",
    "IntelligenceStageRunner",
    "RECONCILE_STAGES",
    "SourceWideFailure",
    "StageResult",
    "run_intelligence_enrich",
    "run_intelligence_prune_raw",
    "run_intelligence_reconcile",
    "run_semantic_status",
]
