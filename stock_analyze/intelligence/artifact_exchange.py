"""Leased filesystem exchange for historical PDF download and parsing."""

from __future__ import annotations

from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import asdict
import fcntl
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

from .blob_store import pdf_object_key
from .document_parser import (
    AnnouncementDocumentParser,
    DocumentChunk,
    DocumentPage,
    DocumentParseResult,
    DocumentParserConfig,
    DocumentTable,
    DocumentTableCell,
    DocumentWord,
    _chunk_id,
    _table_id,
    _text_hash,
)
from .operations import (
    DefaultIntelligenceStageRunner,
    _artifact_id,
    _resolve_source_pdf_url,
)
from .pdf_fetcher import (
    PdfFetchError,
    SecurePdfDownloader,
)
from .types import utc_iso


JOB_CONTRACT_VERSION = "artifact-worker-job-v1"
RESULT_CONTRACT_VERSION = "artifact-worker-result-v1"
MAX_JOB_DOCUMENTS = 50
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_RESULT_LINE_BYTES = 256 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PARSED_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PARSED_JSON_BYTES = 24 * 1024 * 1024
MAX_BATCH_OUTPUT_BYTES = 128 * 1024 * 1024
IMPORT_FENCE_SECONDS = 14_400
MAX_RETRYABLE_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3_600
_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESULT_STATUSES = frozenset(
    {"succeeded", "failed_retryable", "failed_terminal"}
)


class ArtifactExchangeError(ValueError):
    """Stable rejection raised at the untrusted job exchange boundary."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(self.code)


def export_artifact_job(
    repo_root: str | Path,
    *,
    stage: str,
    limit: int,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
    runner: DefaultIntelligenceStageRunner | None = None,
) -> dict[str, object]:
    """Lease a bounded historical batch and materialize a portable job."""

    root = Path(repo_root).expanduser().resolve()
    normalized_stage = _stage(stage)
    bounded_limit = _bounded_limit(limit)
    normalized_worker = str(worker_id).strip()
    if not _WORKER_ID.fullmatch(normalized_worker):
        raise ArtifactExchangeError("artifact_job_worker_id_invalid")
    duration = int(lease_seconds)
    if duration < 60 or duration > 86_400:
        raise ArtifactExchangeError("artifact_job_lease_seconds_invalid")
    timestamp = _aware_utc(now)
    lease_until = timestamp + timedelta(seconds=duration)
    active_runner = runner or DefaultIntelligenceStageRunner(root)
    store = active_runner.store
    parser_config = _parser_config(active_runner)
    download_policy = _download_policy(active_runner)
    jobs_root = _jobs_root(root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_id = f"awj-{uuid4().hex}"

    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE artifact_worker_jobs
            SET status='expired', finished_at=?
            WHERE status IN ('leased', 'importing') AND lease_until<=?
            """,
            (_iso(timestamp), _iso(timestamp)),
        )
        rows = _candidate_rows(
            connection,
            stage=normalized_stage,
            parser_version=parser_config.parser_version,
            limit=bounded_limit,
            now=timestamp,
        )
        if not rows:
            connection.commit()
            return {
                "status": "empty",
                "stage": normalized_stage,
                "job_id": None,
                "job_dir": "",
                "leased": 0,
            }
        items = [
            _manifest_item(
                row,
                stage=normalized_stage,
                ordinal=ordinal,
                parser_version=parser_config.parser_version,
            )
            for ordinal, row in enumerate(rows)
        ]
        connection.execute(
            """
            INSERT INTO artifact_worker_jobs(
                job_id, worker_id, stage, status, created_at,
                lease_until, manifest_hash, result_hash, counts_json
            ) VALUES(?, ?, ?, 'leased', ?, ?, '', '', '{}')
            """,
            (
                job_id,
                normalized_worker,
                normalized_stage,
                _iso(timestamp),
                _iso(lease_until),
            ),
        )
        connection.executemany(
            """
            INSERT INTO artifact_worker_items(
                job_id, ordinal, document_id, input_hash,
                status, error, updated_at
            ) VALUES(?, ?, ?, ?, 'leased', '', ?)
            """,
            [
                (
                    job_id,
                    int(item["ordinal"]),
                    int(item["document_id"]),
                    str(item["input_hash"]),
                    _iso(timestamp),
                )
                for item in items
            ],
        )
        connection.commit()

    manifest = {
        "contract_version": JOB_CONTRACT_VERSION,
        "job_id": job_id,
        "worker_id": normalized_worker,
        "stage": normalized_stage,
        "created_at": _iso(timestamp),
        "lease_until": _iso(lease_until),
        "selection_policy": (
            "historical-download-recent-first-v1"
            if normalized_stage == "download"
            else "historical-parse-ready-first-v1"
        ),
        "runner_source_hash": _runner_source_hash(),
        "parser": asdict(parser_config),
        "download_policy": download_policy,
        "items": items,
    }
    job_dir = jobs_root / job_id
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{job_id}.", dir=jobs_root)
    )
    try:
        (temporary / "inputs").mkdir()
        (temporary / "outputs").mkdir()
        if normalized_stage == "parse":
            for item, row in zip(items, rows, strict=True):
                payload = active_runner.blob_store.read(
                    str(row["storage_uri"])
                )
                _verify_bytes(
                    payload,
                    expected_hash=str(item["pdf_hash"]),
                    expected_size=int(item["pdf_bytes"]),
                    code_prefix="artifact_job_input",
                )
                _write_bytes_atomic(
                    temporary / str(item["input_path"]),
                    payload,
                )
        _write_json(temporary / "job.json", manifest)
        os.replace(temporary, job_dir)
        manifest_hash = _file_hash(job_dir / "job.json")
        with store.connect() as connection:
            connection.execute(
                """
                UPDATE artifact_worker_jobs
                SET manifest_hash=?
                WHERE job_id=? AND status='leased'
                """,
                (manifest_hash, job_id),
            )
            connection.commit()
    except Exception as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        with store.connect() as connection:
            connection.execute(
                """
                UPDATE artifact_worker_jobs
                SET status='failed', finished_at=?,
                    counts_json=?
                WHERE job_id=?
                """,
                (
                    utc_iso(),
                    _canonical_json(
                        {
                            "leased": len(items),
                            "error": "artifact_job_export_failed",
                        }
                    ),
                    job_id,
                ),
            )
            connection.commit()
        if isinstance(exc, ArtifactExchangeError):
            raise
        raise ArtifactExchangeError(
            "artifact_job_export_failed",
            detail=type(exc).__name__,
        ) from exc

    return {
        "status": "leased",
        "stage": normalized_stage,
        "job_id": job_id,
        "job_dir": str(job_dir),
        "leased": len(items),
        "lease_until": _iso(lease_until),
        "manifest_hash": manifest_hash,
    }


def run_artifact_job(
    repo_root: str | Path,
    job_dir: str | Path,
    *,
    workers: int,
    downloader: object | None = None,
) -> dict[str, object]:
    """Execute one portable job without reading or writing production state."""

    del repo_root
    directory = Path(job_dir).expanduser().resolve()
    manifest = _read_json(directory / "job.json")
    _verify_manifest(manifest, directory)
    if manifest["runner_source_hash"] != _runner_source_hash():
        raise ArtifactExchangeError("artifact_job_runner_source_mismatch")
    worker_count = max(1, min(int(workers), 8))
    if downloader is not None and worker_count != 1:
        raise ArtifactExchangeError(
            "artifact_job_injected_downloader_requires_one_worker"
        )
    lock_path = directory / ".run.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        rows = _execute_items(
            directory,
            manifest,
            workers=worker_count,
            downloader=downloader,
        )
        _write_jsonl(directory / "result.jsonl", rows)
        counts = _result_counts(rows)
        status = (
            "ready_to_import"
            if counts["succeeded"] == len(rows)
            else "partial"
        )
        report = {
            "contract_version": RESULT_CONTRACT_VERSION,
            "job_id": manifest["job_id"],
            "stage": manifest["stage"],
            "status": status,
            "expected": len(manifest["items"]),
            **counts,
            "result_hash": _file_hash(directory / "result.jsonl"),
            "runner_source_hash": manifest["runner_source_hash"],
            "runtime_provenance": _runtime_provenance(),
            "finished_at": utc_iso(),
        }
        _write_json(directory / "run_report.json", report)
    return report


def import_artifact_job(
    repo_root: str | Path,
    job_dir: str | Path,
    *,
    now: datetime | None = None,
    runner: DefaultIntelligenceStageRunner | None = None,
) -> dict[str, object]:
    """Serialize imports for one job before entering the validation boundary."""

    root = Path(repo_root).expanduser().resolve()
    directory = _resolve_authoritative_job_dir(root, job_dir)
    lock_path = directory / ".import.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _import_artifact_job_locked(
            root,
            directory,
            now=now,
            runner=runner,
        )


def _import_artifact_job_locked(
    repo_root: str | Path,
    job_dir: str | Path,
    *,
    now: datetime | None = None,
    runner: DefaultIntelligenceStageRunner | None = None,
) -> dict[str, object]:
    """Verify returned bytes, persist them, and close the authoritative lease."""

    root = Path(repo_root).expanduser().resolve()
    directory = _resolve_authoritative_job_dir(root, job_dir)
    manifest_path = directory / "job.json"
    manifest = _read_json(manifest_path)
    _verify_manifest(manifest, directory)
    active_runner = runner or DefaultIntelligenceStageRunner(root)
    store = active_runner.store
    timestamp = _aware_utc(now)
    job_id = str(manifest["job_id"])
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM artifact_worker_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise ArtifactExchangeError("artifact_job_unknown")
    if str(row["manifest_hash"]) != _file_hash(manifest_path):
        raise ArtifactExchangeError("artifact_job_manifest_hash_mismatch")
    current_status = str(row["status"])
    if current_status in {"imported", "partial"}:
        counts = _json_mapping(row["counts_json"])
        return {
            "status": current_status,
            "job_id": job_id,
            "stage": str(row["stage"]),
            **counts,
            "reused": True,
            "payload_cleanup": _cleanup_job_payloads(directory),
        }
    if current_status not in {"leased", "importing"}:
        raise ArtifactExchangeError(
            f"artifact_job_not_importable:{current_status}"
        )
    if timestamp >= _parse_time(str(row["lease_until"])):
        with store.connect() as connection:
            connection.execute(
                """
                UPDATE artifact_worker_jobs
                SET status='expired', finished_at=?
                WHERE job_id=? AND status IN ('leased', 'importing')
                """,
                (_iso(timestamp), job_id),
            )
            connection.commit()
        _cleanup_job_payloads(directory)
        raise ArtifactExchangeError("artifact_job_lease_expired")

    _verify_returned_inputs(directory, manifest)
    result_path = directory / "result.jsonl"
    raw_result = _read_bounded(result_path, MAX_RESULT_BYTES)
    result_hash = hashlib.sha256(raw_result).hexdigest()
    result_rows = _parse_jsonl(raw_result)
    verified = _verify_result_rows(
        directory,
        manifest,
        result_rows,
    )
    counts = _result_counts(result_rows)
    runtime_provenance = _verify_run_report(
        directory,
        manifest,
        result_hash=result_hash,
        counts=counts,
    )

    fence_until = timestamp + timedelta(seconds=IMPORT_FENCE_SECONDS)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if current_status == "leased":
            changed = connection.execute(
                """
                UPDATE artifact_worker_jobs
                SET status='importing', lease_until=?
                WHERE job_id=? AND status='leased' AND lease_until>?
                """,
                (_iso(fence_until), job_id, _iso(timestamp)),
            ).rowcount
        else:
            changed = connection.execute(
                """
                UPDATE artifact_worker_jobs
                SET lease_until=?
                WHERE job_id=? AND status='importing' AND lease_until>?
                """,
                (_iso(fence_until), job_id, _iso(timestamp)),
            ).rowcount
        if changed != 1:
            connection.rollback()
            raise ArtifactExchangeError("artifact_job_import_fence_lost")
        connection.commit()

    item_statuses: list[tuple[str, str, int]] = []
    for item, result in verified:
        document_id = int(item["document_id"])
        result_status = str(result["status"])
        error = str(result.get("error") or "")[:500]
        if result_status == "succeeded":
            payload = _load_result_payload(
                directory,
                manifest,
                item,
                result,
            )
            if str(manifest["stage"]) == "download":
                assert isinstance(payload, bytes)
                digest = hashlib.sha256(payload).hexdigest()
                uri = active_runner.blob_store.put_if_absent(
                    pdf_object_key(digest),
                    payload,
                    "application/pdf",
                )
                active_runner.store.commit_pdf_artifact(
                    document_id=document_id,
                    content_hash=digest,
                    storage_uri=uri,
                    mime_type="application/pdf",
                    byte_size=len(payload),
                )
            else:
                assert isinstance(payload, DocumentParseResult)
                active_runner._persist_parse_result(
                    document_id=document_id,
                    artifact_id=str(item["parsed_artifact_id"]),
                    result=payload,
                )
            item_statuses.append(("succeeded", "", document_id))
            continue
        if str(manifest["stage"]) == "download":
            active_runner.store.record_pdf_artifact_failure(
                document_id=document_id,
                status=result_status,
                error=error or "artifact_worker_download_failed",
            )
        item_statuses.append((result_status, error, document_id))

    final_status = (
        "imported"
        if counts["succeeded"] == len(result_rows)
        else "partial"
    )
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for item_status, error, document_id in item_statuses:
            connection.execute(
                """
                UPDATE artifact_worker_items
                SET status=?, error=?, updated_at=?
                WHERE job_id=? AND document_id=?
                """,
                (
                    item_status,
                    error,
                    _iso(timestamp),
                    job_id,
                    document_id,
                ),
            )
        changed = connection.execute(
            """
            UPDATE artifact_worker_jobs
            SET status=?, finished_at=?, result_hash=?, counts_json=?
            WHERE job_id=? AND status='importing'
            """,
            (
                final_status,
                _iso(timestamp),
                result_hash,
                _canonical_json(
                    {
                        **counts,
                        "runner_provenance": runtime_provenance,
                    }
                ),
                job_id,
            ),
        ).rowcount
        if changed != 1:
            connection.rollback()
            raise ArtifactExchangeError("artifact_job_import_race")
        connection.commit()
    payload_cleanup = _cleanup_job_payloads(directory)
    return {
        "status": final_status,
        "job_id": job_id,
        "stage": str(manifest["stage"]),
        **counts,
        "reused": False,
        "result_hash": result_hash,
        "payload_cleanup": payload_cleanup,
    }


def artifact_worker_status(
    repo_root: str | Path,
    *,
    now: datetime | None = None,
    runner: DefaultIntelligenceStageRunner | None = None,
) -> dict[str, object]:
    """Return a compact control-plane view for operators and Dashboard."""

    root = Path(repo_root).expanduser().resolve()
    active_runner = runner or DefaultIntelligenceStageRunner(root)
    timestamp = _aware_utc(now)
    with active_runner.store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE artifact_worker_jobs
            SET status='expired', finished_at=?
            WHERE status IN ('leased', 'importing') AND lease_until<=?
            """,
            (_iso(timestamp), _iso(timestamp)),
        )
        rows = connection.execute(
            """
            SELECT stage, status, COUNT(*) AS count,
                   MAX(created_at) AS latest_created_at,
                   MAX(finished_at) AS latest_finished_at
            FROM artifact_worker_jobs
            GROUP BY stage, status
            ORDER BY stage, status
            """
        ).fetchall()
        active = connection.execute(
            """
            SELECT job_id, worker_id, stage, created_at, lease_until
            FROM artifact_worker_jobs
            WHERE status IN ('leased', 'importing')
              AND lease_until>?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (_iso(timestamp),),
        ).fetchall()
        expired_job_ids = [
            str(row["job_id"])
            for row in connection.execute(
                """
                SELECT job_id
                FROM artifact_worker_jobs
                WHERE status='expired'
                """
            ).fetchall()
        ]
        quarantine_rows = connection.execute(
            """
            SELECT stage, COUNT(*) AS documents
            FROM (
              SELECT wj.stage, wi.document_id,
                     MAX(wi.status='failed_terminal') AS terminal,
                     SUM(wi.status='failed_retryable') AS retries
              FROM artifact_worker_items wi
              JOIN artifact_worker_jobs wj ON wj.job_id=wi.job_id
              GROUP BY wj.stage, wi.document_id
            )
            WHERE terminal=1 OR retries>=?
            GROUP BY stage
            """,
            (MAX_RETRYABLE_ATTEMPTS,),
        ).fetchall()
        connection.commit()
    cleaned_expired = sum(
        _cleanup_job_payloads(_jobs_root(root) / job_id)
        for job_id in expired_job_ids
    )
    return {
        "status": "ok",
        "jobs": [dict(row) for row in rows],
        "active_leases": [dict(row) for row in active],
        "quarantined_documents": {
            str(row["stage"]): int(row["documents"])
            for row in quarantine_rows
        },
        "cleaned_expired_payloads": cleaned_expired,
        "generated_at": _iso(timestamp),
    }


def _candidate_rows(
    connection,
    *,
    stage: str,
    parser_version: str,
    limit: int,
    now: datetime,
) -> list:
    retry_cutoff = now - timedelta(seconds=RETRY_BACKOFF_SECONDS)
    active_lease = """
        NOT EXISTS (
          SELECT 1
          FROM artifact_worker_items wi
          JOIN artifact_worker_jobs wj ON wj.job_id=wi.job_id
          WHERE wi.document_id=d.id
            AND wj.stage=?
            AND wj.status IN ('leased', 'importing')
            AND wj.lease_until>?
        )
    """
    retry_eligible = """
        NOT EXISTS (
          SELECT 1
          FROM artifact_worker_items wi
          JOIN artifact_worker_jobs wj ON wj.job_id=wi.job_id
          WHERE wi.document_id=d.id
            AND wj.stage=?
            AND wi.status='failed_terminal'
        )
        AND (
          SELECT COUNT(*)
          FROM artifact_worker_items wi
          JOIN artifact_worker_jobs wj ON wj.job_id=wi.job_id
          WHERE wi.document_id=d.id
            AND wj.stage=?
            AND wi.status='failed_retryable'
        ) < ?
        AND NOT EXISTS (
          SELECT 1
          FROM artifact_worker_items wi
          JOIN artifact_worker_jobs wj ON wj.job_id=wi.job_id
          WHERE wi.document_id=d.id
            AND wj.stage=?
            AND wi.status='failed_retryable'
            AND wi.updated_at>?
        )
    """
    if stage == "download":
        return connection.execute(
            f"""
            SELECT d.id, d.source_url, d.metadata_json,
                   a.artifact_id, a.status AS artifact_status
            FROM documents d
            JOIN document_artifacts a
              ON a.document_id=d.id AND a.artifact_type='pdf'
            WHERE d.source='tushare_announcement'
              AND d.live_observed=0
              AND json_extract(
                    d.metadata_json, '$.ingestion_mode'
                  )='history'
              AND d.source_url<>''
              AND a.status IN ('queued', 'failed_retryable')
              AND {retry_eligible}
              AND {active_lease}
            ORDER BY d.queue_priority DESC,
                     d.published_at DESC,
                     d.id DESC
            LIMIT ?
            """,
            (
                stage,
                stage,
                MAX_RETRYABLE_ATTEMPTS,
                stage,
                _iso(retry_cutoff),
                stage,
                _iso(now),
                limit,
            ),
        ).fetchall()
    return connection.execute(
        f"""
        SELECT d.id, a.artifact_id, a.content_hash, a.storage_uri,
               a.mime_type, a.byte_size
        FROM documents d
        JOIN document_artifacts a
          ON a.document_id=d.id AND a.artifact_type='pdf'
        WHERE d.source='tushare_announcement'
          AND d.live_observed=0
          AND json_extract(
                d.metadata_json, '$.ingestion_mode'
              )='history'
          AND a.status='downloaded'
          AND NOT EXISTS (
            SELECT 1
            FROM document_artifacts p
            WHERE p.document_id=d.id
              AND p.artifact_type='parsed'
              AND p.parser_version=?
          )
          AND {retry_eligible}
          AND {active_lease}
        ORDER BY d.queue_priority DESC, a.updated_at, a.document_id
        LIMIT ?
        """,
        (
            parser_version,
            stage,
            stage,
            MAX_RETRYABLE_ATTEMPTS,
            stage,
            _iso(retry_cutoff),
            stage,
            _iso(now),
            limit,
        ),
    ).fetchall()


def _manifest_item(
    row,
    *,
    stage: str,
    ordinal: int,
    parser_version: str,
) -> dict[str, object]:
    document_id = int(row["id"])
    if stage == "download":
        metadata = _json_mapping(row["metadata_json"])
        expected_hash = next(
            (
                str(metadata.get(key) or "").strip().casefold()
                for key in (
                    "pdf_sha256",
                    "expected_pdf_sha256",
                    "source_pdf_sha256",
                )
                if str(metadata.get(key) or "").strip()
            ),
            "",
        )
        if expected_hash and not _HASH.fullmatch(expected_hash):
            expected_hash = ""
        item = {
            "ordinal": ordinal,
            "document_id": document_id,
            "source_url": _resolve_source_pdf_url(
                str(row["source_url"])
            ),
            "expected_pdf_hash": expected_hash,
            "output_path": f"outputs/{ordinal:06d}.pdf",
        }
    else:
        pdf_hash = str(row["content_hash"])
        item = {
            "ordinal": ordinal,
            "document_id": document_id,
            "pdf_artifact_id": str(row["artifact_id"]),
            "pdf_hash": pdf_hash,
            "pdf_bytes": int(row["byte_size"]),
            "input_path": f"inputs/{ordinal:06d}.pdf",
            "output_path": (
                f"outputs/{ordinal:06d}.parsed.json.gz"
            ),
            "parser_version": parser_version,
            "parsed_artifact_id": _artifact_id(
                document_id,
                pdf_hash,
                parser_version,
            ),
        }
    input_hash = hashlib.sha256(
        _canonical_json(item).encode("utf-8")
    ).hexdigest()
    item["input_hash"] = input_hash
    return item


def _execute_items(
    directory: Path,
    manifest: Mapping[str, object],
    *,
    workers: int,
    downloader: object | None,
) -> list[dict[str, object]]:
    items = [dict(item) for item in manifest["items"]]
    stage = str(manifest["stage"])

    def execute(item: Mapping[str, object]) -> dict[str, object]:
        if stage == "parse":
            return _execute_parse_item(directory, manifest, item)
        return _execute_download_item(
            directory,
            manifest,
            item,
            downloader=downloader,
        )

    if workers == 1:
        return [execute(item) for item in items]
    if stage == "parse":
        rows: list[dict[str, object]] = []
        with ProcessPoolExecutor(
            max_workers=workers,
        ) as executor:
            futures = {
                executor.submit(
                    _execute_parse_item,
                    directory,
                    manifest,
                    item,
                ): int(item["ordinal"])
                for item in items
            }
            for future in as_completed(futures):
                rows.append(future.result())
        return sorted(rows, key=lambda row: int(row["ordinal"]))
    rows: list[dict[str, object]] = []
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"artifact-{stage}",
    ) as executor:
        futures = {
            executor.submit(execute, item): int(item["ordinal"])
            for item in items
        }
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda row: int(row["ordinal"]))


def _execute_parse_item(
    directory: Path,
    manifest: Mapping[str, object],
    item: Mapping[str, object],
) -> dict[str, object]:
    try:
        input_path = _safe_job_file(
            directory,
            str(item["input_path"]),
            must_exist=True,
        )
        payload = _read_bounded(
            input_path,
            MAX_PDF_BYTES,
        )
        _verify_bytes(
            payload,
            expected_hash=str(item["pdf_hash"]),
            expected_size=int(item["pdf_bytes"]),
            code_prefix="artifact_job_input",
        )
        parser_config = _manifest_parser_config(manifest["parser"])
        parser = AnnouncementDocumentParser(config=parser_config)
        result = parser.parse(
            payload,
            document_id=int(item["document_id"]),
            artifact_id=str(item["parsed_artifact_id"]),
        )
        raw = _canonical_json(asdict(result)).encode("utf-8")
        if len(raw) > MAX_PARSED_JSON_BYTES:
            raise ArtifactExchangeError(
                "artifact_job_parsed_payload_too_large"
            )
        output = gzip.compress(raw, mtime=0)
        if len(output) > MAX_PARSED_OUTPUT_BYTES:
            raise ArtifactExchangeError(
                "artifact_job_parsed_output_too_large"
            )
        output_path = _safe_job_file(
            directory,
            str(item["output_path"]),
            must_exist=False,
        )
        _write_bytes_atomic(output_path, output)
        return _success_row(
            manifest,
            item,
            output,
            mime_type="application/gzip",
            parse_status=result.status,
        )
    except ArtifactExchangeError as exc:
        return _failure_row(manifest, item, exc.code, retryable=False)
    except Exception:
        return _failure_row(
            manifest,
            item,
            "artifact_worker_parse_failed",
            retryable=True,
        )


def _execute_download_item(
    directory: Path,
    manifest: Mapping[str, object],
    item: Mapping[str, object],
    *,
    downloader: object | None,
) -> dict[str, object]:
    active_downloader = downloader
    try:
        if active_downloader is None:
            policy = _mapping(manifest["download_policy"])
            active_downloader = SecurePdfDownloader(
                allowed_hosts=tuple(policy["allowed_hosts"]),
                max_bytes=int(policy["max_bytes"]),
                max_attempts=int(policy["max_attempts"]),
                max_redirects=int(policy["max_redirects"]),
                connect_timeout_seconds=float(
                    policy["connect_timeout_seconds"]
                ),
                read_timeout_seconds=float(
                    policy["read_timeout_seconds"]
                ),
                total_timeout_seconds=float(
                    policy["total_timeout_seconds"]
                ),
                temp_root=directory / "tmp",
            )
        expected = str(item.get("expected_pdf_hash") or "") or None
        with active_downloader.fetch(
            str(item["source_url"]),
            expected_sha256=expected,
        ) as downloaded:
            payload = downloaded.path.read_bytes()
            _verify_bytes(
                payload,
                expected_hash=downloaded.sha256,
                expected_size=downloaded.byte_size,
                code_prefix="artifact_job_download",
            )
            output_path = _safe_job_file(
                directory,
                str(item["output_path"]),
                must_exist=False,
            )
            _write_bytes_atomic(output_path, payload)
        return _success_row(
            manifest,
            item,
            payload,
            mime_type="application/pdf",
        )
    except PdfFetchError as exc:
        return _failure_row(
            manifest,
            item,
            _safe_error(exc, "pdf_fetch_failed"),
            retryable=bool(exc.retryable),
        )
    except ArtifactExchangeError as exc:
        return _failure_row(manifest, item, exc.code, retryable=False)
    except Exception:
        return _failure_row(
            manifest,
            item,
            "artifact_worker_download_failed",
            retryable=True,
        )


def _success_row(
    manifest: Mapping[str, object],
    item: Mapping[str, object],
    payload: bytes,
    *,
    mime_type: str,
    parse_status: str = "",
) -> dict[str, object]:
    return {
        "contract_version": RESULT_CONTRACT_VERSION,
        "job_id": manifest["job_id"],
        "stage": manifest["stage"],
        "ordinal": int(item["ordinal"]),
        "document_id": int(item["document_id"]),
        "input_hash": item["input_hash"],
        "status": "succeeded",
        "output_path": item["output_path"],
        "output_hash": hashlib.sha256(payload).hexdigest(),
        "output_bytes": len(payload),
        "mime_type": mime_type,
        "parse_status": parse_status,
        "runner_source_hash": manifest["runner_source_hash"],
        "error": "",
    }


def _failure_row(
    manifest: Mapping[str, object],
    item: Mapping[str, object],
    error: str,
    *,
    retryable: bool,
) -> dict[str, object]:
    return {
        "contract_version": RESULT_CONTRACT_VERSION,
        "job_id": manifest["job_id"],
        "stage": manifest["stage"],
        "ordinal": int(item["ordinal"]),
        "document_id": int(item["document_id"]),
        "input_hash": item["input_hash"],
        "status": (
            "failed_retryable" if retryable else "failed_terminal"
        ),
        "output_path": "",
        "output_hash": "",
        "output_bytes": 0,
        "mime_type": "",
        "parse_status": "",
        "runner_source_hash": manifest["runner_source_hash"],
        "error": str(error)[:500],
    }


def _verify_result_rows(
    directory: Path,
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    items = {
        int(item["document_id"]): item
        for item in manifest["items"]
    }
    if len(rows) != len(items):
        raise ArtifactExchangeError(
            "artifact_job_result_count_mismatch"
        )
    seen: set[int] = set()
    verified = []
    batch_output_bytes = 0
    for result in rows:
        document_id = _positive_int(result.get("document_id"))
        if document_id in seen or document_id not in items:
            raise ArtifactExchangeError(
                "artifact_job_result_document_mismatch"
            )
        seen.add(document_id)
        item = items[document_id]
        if (
            result.get("contract_version") != RESULT_CONTRACT_VERSION
            or result.get("job_id") != manifest["job_id"]
            or result.get("stage") != manifest["stage"]
            or result.get("input_hash") != item["input_hash"]
            or result.get("runner_source_hash")
            != manifest["runner_source_hash"]
            or int(result.get("ordinal", -1)) != int(item["ordinal"])
        ):
            raise ArtifactExchangeError(
                "artifact_job_result_contract_mismatch"
            )
        result_status = str(result.get("status") or "")
        if result_status not in _RESULT_STATUSES:
            raise ArtifactExchangeError(
                "artifact_job_result_status_invalid"
            )
        if result_status == "succeeded":
            if result.get("output_path") != item["output_path"]:
                raise ArtifactExchangeError(
                    "artifact_job_output_path_mismatch"
                )
            batch_output_bytes += int(result.get("output_bytes") or -1)
            if (
                batch_output_bytes < 0
                or batch_output_bytes > MAX_BATCH_OUTPUT_BYTES
            ):
                raise ArtifactExchangeError(
                    "artifact_job_batch_output_too_large"
                )
            _load_result_payload(directory, manifest, item, result)
        verified.append((item, result))
    return verified


def _load_result_payload(
    directory: Path,
    manifest: Mapping[str, object],
    item: Mapping[str, object],
    result: Mapping[str, object],
) -> bytes | DocumentParseResult:
    output_path = _safe_job_file(
        directory,
        str(result.get("output_path") or ""),
        must_exist=True,
    )
    output_limit = (
        MAX_PDF_BYTES
        if str(manifest["stage"]) == "download"
        else MAX_PARSED_OUTPUT_BYTES
    )
    raw = _read_bounded(output_path, output_limit)
    _verify_bytes(
        raw,
        expected_hash=str(result.get("output_hash") or ""),
        expected_size=int(result.get("output_bytes") or -1),
        code_prefix="artifact_job_output",
    )
    if str(manifest["stage"]) == "download":
        if result.get("mime_type") != "application/pdf":
            raise ArtifactExchangeError(
                "artifact_job_output_mime_mismatch"
            )
        if not raw.startswith(b"%PDF-"):
            raise ArtifactExchangeError("artifact_job_output_not_pdf")
        return raw
    if result.get("mime_type") != "application/gzip":
        raise ArtifactExchangeError("artifact_job_output_mime_mismatch")
    parsed = _decode_parse_result(raw, item)
    if result.get("parse_status") != parsed.status:
        raise ArtifactExchangeError(
            "artifact_job_parse_status_mismatch"
        )
    return parsed


def _verify_run_report(
    directory: Path,
    manifest: Mapping[str, object],
    *,
    result_hash: str,
    counts: Mapping[str, int],
) -> dict[str, object]:
    report = _read_json(directory / "run_report.json")
    if (
        report.get("contract_version") != RESULT_CONTRACT_VERSION
        or report.get("job_id") != manifest["job_id"]
        or report.get("stage") != manifest["stage"]
        or report.get("runner_source_hash")
        != manifest["runner_source_hash"]
        or report.get("result_hash") != result_hash
        or int(report.get("expected") or -1)
        != len(manifest["items"])
        or any(
            int(report.get(key) or 0) != int(value)
            for key, value in counts.items()
        )
    ):
        raise ArtifactExchangeError(
            "artifact_job_run_report_mismatch"
        )
    provenance = _mapping(report.get("runtime_provenance"))
    return dict(provenance)


def _decode_parse_result(
    payload: bytes,
    item: Mapping[str, object],
) -> DocumentParseResult:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
            raw = archive.read(MAX_PARSED_JSON_BYTES + 1)
        if len(raw) > MAX_PARSED_JSON_BYTES:
            raise ArtifactExchangeError(
                "artifact_job_parsed_payload_too_large"
            )
        value = json.loads(raw.decode("utf-8"))
    except ArtifactExchangeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactExchangeError(
            "artifact_job_parsed_payload_invalid"
        ) from exc
    try:
        root = _mapping(value)
        pages = tuple(
            DocumentPage(
                page_number=int(page["page_number"]),
                width=float(page["width"]),
                height=float(page["height"]),
                text=str(page["text"]),
                chunks=tuple(
                    DocumentChunk(
                        chunk_id=str(chunk["chunk_id"]),
                        page_number=int(chunk["page_number"]),
                        sequence_no=int(chunk["sequence_no"]),
                        section=str(chunk["section"]),
                        bbox=_bbox(chunk["bbox"]),
                        text=str(chunk["text"]),
                        text_hash=str(chunk["text_hash"]),
                        ocr_used=bool(chunk["ocr_used"]),
                        ocr_confidence=(
                            None
                            if chunk.get("ocr_confidence") is None
                            else float(chunk["ocr_confidence"])
                        ),
                    )
                    for chunk in _sequence(page["chunks"])
                ),
                words=tuple(
                    DocumentWord(
                        page_number=int(word["page_number"]),
                        sequence_no=int(word["sequence_no"]),
                        bbox=_bbox(word["bbox"]),
                        text=str(word["text"]),
                        confidence=(
                            None
                            if word.get("confidence") is None
                            else float(word["confidence"])
                        ),
                        ocr_used=bool(word["ocr_used"]),
                    )
                    for word in _sequence(page["words"])
                ),
                ocr_used=bool(page["ocr_used"]),
                status=str(page["status"]),
                error=str(page.get("error") or ""),
            )
            for page in _sequence(root["pages"])
        )
        tables = tuple(
            DocumentTable(
                table_id=str(table["table_id"]),
                page_number=int(table["page_number"]),
                sequence_no=int(table["sequence_no"]),
                bbox=_bbox(table["bbox"]),
                cells=tuple(
                    DocumentTableCell(
                        row_index=int(cell["row_index"]),
                        column_index=int(cell["column_index"]),
                        bbox=_bbox(cell["bbox"]),
                        text=str(cell["text"]),
                    )
                    for cell in _sequence(table["cells"])
                ),
            )
            for table in _sequence(root["tables"])
        )
        result = DocumentParseResult(
            parser_version=str(root["parser_version"]),
            status=str(root["status"]),
            content_hash=str(root["content_hash"]),
            pages=pages,
            tables=tables,
            error=str(root.get("error") or ""),
        )
    except (ArtifactExchangeError, KeyError, TypeError, ValueError) as exc:
        raise ArtifactExchangeError(
            "artifact_job_parsed_payload_invalid"
        ) from exc
    if (
        result.parser_version != str(item["parser_version"])
        or result.content_hash != str(item["pdf_hash"])
        or result.status not in {"parsed", "ocr_failed"}
        or not _parse_result_lineage_is_valid(result, item)
    ):
        raise ArtifactExchangeError(
            "artifact_job_parsed_identity_mismatch"
        )
    return result


def _parse_result_lineage_is_valid(
    result: DocumentParseResult,
    item: Mapping[str, object],
) -> bool:
    document_id = int(item["document_id"])
    artifact_id = str(item["parsed_artifact_id"])
    chunk_ids: set[str] = set()
    table_ids: set[str] = set()
    for page in result.pages:
        if page.page_number <= 0 or page.width <= 0 or page.height <= 0:
            return False
        for chunk in page.chunks:
            if (
                chunk.page_number != page.page_number
                or chunk.sequence_no < 0
                or chunk.text_hash != _text_hash(chunk.text)
            ):
                return False
            expected_id = _chunk_id(
                document_id=document_id,
                artifact_id=artifact_id,
                parser_version=result.parser_version,
                page_number=chunk.page_number,
                sequence_no=chunk.sequence_no,
                text_hash=chunk.text_hash,
            )
            if chunk.chunk_id != expected_id or chunk.chunk_id in chunk_ids:
                return False
            chunk_ids.add(chunk.chunk_id)
    for table in result.tables:
        if table.page_number <= 0 or table.sequence_no < 0:
            return False
        expected_id = _table_id(
            document_id=document_id,
            artifact_id=artifact_id,
            parser_version=result.parser_version,
            page_number=table.page_number,
            sequence_no=table.sequence_no,
            cells=table.cells,
        )
        if table.table_id != expected_id or table.table_id in table_ids:
            return False
        table_ids.add(table.table_id)
    return True


def _verify_returned_inputs(
    directory: Path,
    manifest: Mapping[str, object],
) -> None:
    if manifest["stage"] != "parse":
        return
    for item in manifest["items"]:
        input_path = _safe_job_file(
            directory,
            str(item["input_path"]),
            must_exist=True,
        )
        payload = _read_bounded(
            input_path,
            MAX_PDF_BYTES,
        )
        _verify_bytes(
            payload,
            expected_hash=str(item["pdf_hash"]),
            expected_size=int(item["pdf_bytes"]),
            code_prefix="artifact_job_input",
        )


def _verify_manifest(
    manifest: Mapping[str, object],
    directory: Path,
) -> None:
    if manifest.get("contract_version") != JOB_CONTRACT_VERSION:
        raise ArtifactExchangeError(
            "artifact_job_contract_version_invalid"
        )
    job_id = str(manifest.get("job_id") or "")
    if directory.name != job_id or not job_id.startswith("awj-"):
        raise ArtifactExchangeError("artifact_job_id_mismatch")
    _stage(str(manifest.get("stage") or ""))
    if not _HASH.fullmatch(
        str(manifest.get("runner_source_hash") or "")
    ):
        raise ArtifactExchangeError("artifact_job_runner_source_invalid")
    items = manifest.get("items")
    if (
        not isinstance(items, list)
        or not items
        or len(items) > MAX_JOB_DOCUMENTS
    ):
        raise ArtifactExchangeError("artifact_job_items_invalid")
    seen_documents: set[int] = set()
    for ordinal, raw_item in enumerate(items):
        item = _mapping(raw_item)
        document_id = _positive_int(item.get("document_id"))
        if (
            document_id in seen_documents
            or int(item.get("ordinal", -1)) != ordinal
            or not _HASH.fullmatch(str(item.get("input_hash") or ""))
        ):
            raise ArtifactExchangeError("artifact_job_item_invalid")
        seen_documents.add(document_id)


def _parser_config(
    runner: DefaultIntelligenceStageRunner,
) -> DocumentParserConfig:
    return runner._parser().config


def _manifest_parser_config(
    value: object,
) -> DocumentParserConfig:
    parser = _mapping(value)
    return DocumentParserConfig(
        parser_version=str(parser["parser_version"]),
        min_text_characters_per_page=int(
            parser["min_text_characters_per_page"]
        ),
        ocr_languages=str(parser["ocr_languages"]),
        ocr_render_dpi=int(parser["ocr_render_dpi"]),
        extract_tables=bool(parser["extract_tables"]),
    )


def _download_policy(
    runner: DefaultIntelligenceStageRunner,
) -> dict[str, object]:
    artifact = _mapping(runner.config.get("artifact_store"))
    hosts = sorted(
        {
            str(value).strip().casefold()
            for value in _sequence(artifact.get("allowed_hosts") or [])
            if str(value).strip()
        }
    )
    if not hosts:
        raise ArtifactExchangeError(
            "artifact_job_download_allowlist_empty"
        )
    return {
        "allowed_hosts": hosts,
        "max_bytes": int(
            artifact.get("max_pdf_bytes") or 52_428_800
        ),
        "max_attempts": int(
            artifact.get("download_max_attempts") or 2
        ),
        "max_redirects": int(
            artifact.get("download_max_redirects") or 5
        ),
        "connect_timeout_seconds": float(
            artifact.get("download_connect_timeout_seconds") or 5
        ),
        "read_timeout_seconds": float(
            artifact.get("download_read_timeout_seconds") or 15
        ),
        "total_timeout_seconds": float(
            artifact.get("download_total_timeout_seconds") or 30
        ),
    }


def _resolve_authoritative_job_dir(
    root: Path,
    value: str | Path,
) -> Path:
    jobs_root = _jobs_root(root).resolve()
    supplied = Path(value).expanduser()
    candidate = (
        supplied.resolve()
        if supplied.is_absolute()
        else (jobs_root / supplied).resolve()
    )
    try:
        relative = candidate.relative_to(jobs_root)
    except ValueError as exc:
        raise ArtifactExchangeError(
            "artifact_job_path_outside_control_plane"
        ) from exc
    if len(relative.parts) != 1:
        raise ArtifactExchangeError("artifact_job_path_invalid")
    return candidate


def _safe_job_file(
    directory: Path,
    relative_path: str,
    *,
    must_exist: bool,
) -> Path:
    raw = str(relative_path).strip()
    if (
        not raw
        or raw.startswith("/")
        or "\\" in raw
        or any(part in {"", ".", ".."} for part in Path(raw).parts)
    ):
        raise ArtifactExchangeError("artifact_job_file_path_invalid")
    candidate = (directory / raw).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError as exc:
        raise ArtifactExchangeError(
            "artifact_job_file_path_invalid"
        ) from exc
    if must_exist and (
        not candidate.is_file()
        or candidate.is_symlink()
        or any(parent.is_symlink() for parent in candidate.parents if parent != directory)
    ):
        raise ArtifactExchangeError("artifact_job_file_missing")
    return candidate


def _cleanup_job_payloads(directory: Path) -> bool:
    """Remove transferable bytes while preserving the compact audit envelope."""

    cleaned = True
    for name in ("inputs", "outputs", "tmp"):
        candidate = directory / name
        try:
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink(missing_ok=True)
            elif candidate.exists():
                shutil.rmtree(candidate)
        except OSError:
            cleaned = False
    try:
        (directory / ".run.lock").unlink(missing_ok=True)
    except OSError:
        cleaned = False
    return cleaned


def _runner_source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(
        {
            Path(__file__).resolve(),
            Path(sys.modules[AnnouncementDocumentParser.__module__].__file__).resolve(),
        },
        key=str,
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_provenance() -> dict[str, object]:
    packages = {}
    for distribution in (
        "PyMuPDF",
        "pdfplumber",
        "pypdf",
        "pytesseract",
    ):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = "missing"
    try:
        tesseract = subprocess.run(
            ["tesseract", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        tesseract_version = (
            (tesseract.stdout or tesseract.stderr).splitlines()[0].strip()
            if tesseract.returncode == 0
            else "unavailable"
        )
    except (OSError, subprocess.SubprocessError):
        tesseract_version = "unavailable"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "tesseract": tesseract_version,
    }


def _result_counts(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    return {
        "processed": len(rows),
        "succeeded": sum(
            str(row.get("status")) == "succeeded" for row in rows
        ),
        "failed_retryable": sum(
            str(row.get("status")) == "failed_retryable"
            for row in rows
        ),
        "failed_terminal": sum(
            str(row.get("status")) == "failed_terminal"
            for row in rows
        ),
    }


def _parse_jsonl(payload: bytes) -> list[dict[str, object]]:
    rows = []
    for raw_line in payload.splitlines():
        if not raw_line.strip():
            continue
        if len(raw_line) > MAX_RESULT_LINE_BYTES:
            raise ArtifactExchangeError(
                "artifact_job_result_line_too_large"
            )
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactExchangeError(
                "artifact_job_result_json_invalid"
            ) from exc
        rows.append(dict(_mapping(value)))
    return rows


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            _read_bounded(path, MAX_RESULT_BYTES).decode("utf-8")
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactExchangeError(
            "artifact_job_json_invalid"
        ) from exc
    return dict(_mapping(value))


def _read_bounded(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ArtifactExchangeError("artifact_job_file_missing")
    if path.stat().st_size > int(limit):
        raise ArtifactExchangeError("artifact_job_file_too_large")
    return path.read_bytes()


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _write_bytes_atomic(
        path,
        (_canonical_json(value) + "\n").encode("utf-8"),
    )


def _write_jsonl(
    path: Path,
    rows: Sequence[Mapping[str, object]],
) -> None:
    payload = "".join(
        f"{_canonical_json(row)}\n" for row in rows
    ).encode("utf-8")
    _write_bytes_atomic(path, payload)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_bytes(
    payload: bytes,
    *,
    expected_hash: str,
    expected_size: int,
    code_prefix: str,
) -> None:
    if (
        not _HASH.fullmatch(str(expected_hash))
        or hashlib.sha256(payload).hexdigest() != expected_hash
    ):
        raise ArtifactExchangeError(f"{code_prefix}_hash_mismatch")
    if len(payload) != int(expected_size):
        raise ArtifactExchangeError(f"{code_prefix}_size_mismatch")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ArtifactExchangeError("artifact_job_mapping_invalid")
    return value


def _sequence(value: object) -> Sequence:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ArtifactExchangeError("artifact_job_sequence_invalid")
    return value


def _json_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _bbox(value: object) -> tuple[float, float, float, float]:
    values = tuple(float(item) for item in _sequence(value))
    if len(values) != 4:
        raise ArtifactExchangeError("artifact_job_bbox_invalid")
    return values


def _positive_int(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactExchangeError(
            "artifact_job_positive_int_invalid"
        ) from exc
    if result <= 0:
        raise ArtifactExchangeError(
            "artifact_job_positive_int_invalid"
        )
    return result


def _stage(value: str) -> str:
    normalized = str(value).strip().casefold()
    if normalized not in {"download", "parse"}:
        raise ArtifactExchangeError("artifact_job_stage_invalid")
    return normalized


def _bounded_limit(value: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactExchangeError(
            "artifact_job_limit_invalid"
        ) from exc
    if result <= 0:
        raise ArtifactExchangeError("artifact_job_limit_invalid")
    return min(result, MAX_JOB_DOCUMENTS)


def _aware_utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ArtifactExchangeError("artifact_job_time_must_be_aware")
    return result.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactExchangeError("artifact_job_time_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtifactExchangeError("artifact_job_time_invalid")
    return parsed.astimezone(timezone.utc)


def _jobs_root(root: Path) -> Path:
    return (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "artifact_jobs"
    )


def _safe_error(error: Exception, fallback: str) -> str:
    value = str(error).strip()
    if (
        not value
        or len(value) > 500
        or any(
            not character.isascii()
            or (
                not character.isalnum()
                and character not in "_.:-"
            )
            for character in value
        )
    ):
        return fallback
    return value
