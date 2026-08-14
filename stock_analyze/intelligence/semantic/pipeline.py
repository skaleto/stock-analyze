"""Idempotent orchestration for versioned announcement semantic extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from ..blob_store import BlobStore, BlobStoreError
from ..store import IntelligenceStore
from .contracts import (
    SemanticContractError,
    announcement_event_schema,
    parse_semantic_document_result,
)
from .document_ir import DOCUMENT_IR_VERSION, build_document_ir
from .provider import (
    SemanticExtractionProvider,
    SemanticInputBundle,
    SemanticProviderError,
)
from .router import SemanticRoute, route_document
from .taxonomy import EventTaxonomy


_SEMANTIC_DERIVED_SECTIONS = frozenset(
    {"table_cell", "semantic_segment", "document_metadata"}
)


@dataclass(frozen=True)
class SemanticPipelineResult:
    document_id: int
    status: str
    route: SemanticRoute
    run_id: str | None = None
    reused: bool = False
    error: str = ""


class SemanticPipeline:
    """Prepare bounded inputs, call one provider, and persist immutable lineage."""

    def __init__(
        self,
        *,
        store: IntelligenceStore,
        blob_store: BlobStore,
        provider: SemanticExtractionProvider,
        taxonomy: EventTaxonomy,
        prompt_version: str,
        schema_version: str,
        document_ir_version: str = "",
        retriever_version: str = "",
        audit_sample_rate: float = 0.05,
    ) -> None:
        self.store = store
        self.blob_store = blob_store
        self.provider = provider
        self.taxonomy = taxonomy
        self.prompt_version = str(prompt_version).strip()
        self.schema_version = str(schema_version).strip()
        self.document_ir_version = str(document_ir_version).strip()
        self.retriever_version = str(retriever_version).strip()
        self.audit_sample_rate = float(audit_sample_rate)
        if not self.prompt_version:
            raise ValueError("semantic_prompt_version_required")
        if not self.schema_version:
            raise ValueError("semantic_schema_version_required")
        if self.document_ir_version and (
            self.document_ir_version != DOCUMENT_IR_VERSION
        ):
            raise ValueError("semantic_document_ir_version_unknown")
        if bool(self.document_ir_version) != bool(self.retriever_version):
            raise ValueError("semantic_ir_retriever_contract_incomplete")
        if not 0.0 <= self.audit_sample_rate <= 1.0:
            raise ValueError("semantic_audit_sample_rate_invalid")

    def route(self, document_id: int) -> SemanticRoute:
        return self._route_snapshot(
            self.store.semantic_document_snapshot(document_id)
        )

    def build_bundle(
        self,
        document_id: int,
        *,
        route: SemanticRoute | None = None,
    ) -> SemanticInputBundle:
        snapshot = self.store.semantic_document_snapshot(document_id)
        selected_route = route or self._route_snapshot(snapshot)
        artifact = snapshot.get("artifact")
        if (
            selected_route.decision == "blocked_artifact"
            or not isinstance(artifact, Mapping)
        ):
            raise ValueError("semantic_artifact_blocked")

        document = _mapping(snapshot["document"])
        metadata = _json_mapping(document.get("metadata_json"))
        links = [
            _mapping(link)
            for link in _sequence(snapshot.get("security_links"))
        ]
        primary_link = links[0] if links else {}
        ts_code = str(
            primary_link.get("ts_code")
            or metadata.get("ts_code")
            or metadata.get("code")
            or ""
        ).strip()
        name = str(
            primary_link.get("name")
            or metadata.get("name")
            or ""
        ).strip()

        chunks = []
        for item in _sequence(snapshot.get("chunks")):
            chunk = _mapping(item)
            section = str(chunk.get("section") or "")
            if section in _SEMANTIC_DERIVED_SECTIONS:
                continue
            chunks.append(
                {
                    "chunk_id": str(chunk.get("chunk_id") or ""),
                    "page_number": int(chunk.get("page_number") or 0),
                    "section": section,
                    "bbox": _json_value(chunk.get("bbox_json"), fallback=[]),
                    "text": str(chunk.get("text") or ""),
                }
            )
        metadata_chunks = (
            (f"doc{document_id}-meta-title", str(document.get("title") or "")),
            (f"doc{document_id}-meta-issuer", name),
        )
        chunks.extend(
            {
                "chunk_id": chunk_id,
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": text,
            }
            for chunk_id, text in metadata_chunks
            if text
        )
        if self.document_ir_version:
            chunks = _segment_evidence_chunks(chunks)
        tables = [
            {
                "table_id": str(table.get("table_id") or ""),
                "page_number": int(table.get("page_number") or 0),
                "bbox": _json_value(table.get("bbox_json"), fallback=[]),
                "cells": _json_value(table.get("cells_json"), fallback=[]),
            }
            for table in (
                _mapping(item)
                for item in _sequence(snapshot.get("tables"))
            )
        ]
        document_ir = None
        if self.document_ir_version:
            document_ir = build_document_ir(
                document={
                    "id": int(document["id"]),
                    "title": str(document.get("title") or ""),
                    "ts_code": ts_code,
                    "name": name,
                    "published_at": str(document.get("published_at") or ""),
                },
                chunks=chunks,
                tables=tables,
                parser_version=str(artifact["parser_version"]),
            )
        known_chunk_ids = {
            str(chunk.get("chunk_id") or "")
            for chunk in chunks
        }
        for table_index, table in enumerate(tables):
            table_id = str(table.get("table_id") or f"table-{document_id}-{table_index}")
            page_number = int(table.get("page_number") or 0)
            for cell_index, raw_cell in enumerate(_sequence(table.get("cells"))):
                cell = _mapping(raw_cell)
                text = str(cell.get("text") or "").strip()
                if not text:
                    continue
                row_index = int(cell.get("row_index") or 0)
                raw_column_index = cell.get("column_index")
                column_index = int(
                    cell_index
                    if raw_column_index is None
                    else raw_column_index
                )
                chunk_id = f"{table_id}-r{row_index}-c{column_index}"
                if chunk_id in known_chunk_ids:
                    continue
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "page_number": page_number,
                        "section": "table_cell",
                        "bbox": (
                            list(cell.get("bbox"))
                            if isinstance(cell.get("bbox"), (list, tuple))
                            else []
                        ),
                        "text": text,
                    }
                )
                known_chunk_ids.add(chunk_id)
        taxonomy_candidates = list(selected_route.categories)
        if selected_route.requires_deep_extraction and not taxonomy_candidates:
            taxonomy_candidates = sorted(self.taxonomy.event_types)
        entity_whitelist = [
            {
                "entity_id": str(link.get("ts_code") or ""),
                "name": str(link.get("name") or ""),
                "allowed_roles": ["issuer"],
            }
            for link in links
            if str(link.get("ts_code") or "").strip()
        ]
        revision_context = [
            {
                "document_id": int(revision.get("document_id") or 0),
                "title": str(revision.get("title") or ""),
                "published_at": str(revision.get("published_at") or ""),
                "relation": str(revision.get("relation") or ""),
            }
            for revision in (
                _mapping(item)
                for item in _sequence(snapshot.get("revision_context"))
            )
        ]
        payload = {
            "document": {
                "id": int(document["id"]),
                "title": str(document.get("title") or ""),
                "ts_code": ts_code,
                "name": name,
                "published_at": str(document.get("published_at") or ""),
                "rec_time": str(metadata.get("rec_time") or ""),
                "source_url": str(document.get("source_url") or ""),
            },
            "taxonomy_candidates": taxonomy_candidates,
            "entity_whitelist": entity_whitelist,
            "chunks": chunks,
            "tables": tables,
            "revision_context": revision_context,
            "route_context": {
                "document_kind": selected_route.document_kind,
                "extraction_purpose": selected_route.extraction_purpose,
                "difficulty_tags": list(selected_route.difficulty_tags),
                "reason_codes": list(selected_route.reason_codes),
            },
        }
        if document_ir is not None:
            payload["document_ir"] = document_ir
            payload["retriever_version"] = self.retriever_version
        serialized = _canonical_json(payload)
        return SemanticInputBundle(
            document_id=int(document["id"]),
            artifact_hash=str(artifact["content_hash"]),
            parser_version=str(artifact["parser_version"]),
            prompt_version=self.prompt_version,
            schema_version=self.schema_version,
            taxonomy_version=self.taxonomy.taxonomy_version,
            payload=payload,
            input_token_estimate=max(1, (len(serialized) + 3) // 4),
        )

    def process_document(self, document_id: int) -> SemanticPipelineResult:
        snapshot = self.store.semantic_document_snapshot(document_id)
        route = self._route_snapshot(snapshot)
        normalized_id = int(document_id)
        if not route.requires_deep_extraction:
            return SemanticPipelineResult(
                document_id=normalized_id,
                status=route.decision,
                route=route,
            )

        bundle = self.build_bundle(normalized_id, route=route)
        input_hash = self._input_hash(bundle)
        identity = self.provider.identity
        claim = self.store.claim_semantic_run(
            document_id=normalized_id,
            artifact_hash=bundle.artifact_hash,
            provider=identity.provider,
            model=identity.model,
            prompt_version=bundle.prompt_version,
            schema_version=bundle.schema_version,
            taxonomy_version=bundle.taxonomy_version,
            parser_version=bundle.parser_version,
            input_hash=input_hash,
        )
        run_id = str(claim["run_id"])
        if not bool(claim["claimed"]):
            return SemanticPipelineResult(
                document_id=normalized_id,
                status=str(claim["status"]),
                route=route,
                run_id=run_id,
                reused=True,
                error=str(claim["error"] or ""),
            )

        output_hash: str | None = None
        output_uri: str | None = None
        try:
            response = self.provider.extract(
                bundle,
                response_schema=announcement_event_schema(self.taxonomy),
            )
            raw_payload = response.raw_output.encode("utf-8")
            output_hash, output_uri = self._persist_raw_output(
                input_hash=input_hash,
                payload=raw_payload,
                content_type="application/json",
            )
            if response.output_hash != output_hash:
                raise SemanticProviderError(
                    "semantic_provider_output_hash_mismatch"
                )
            if response.identity != identity:
                raise SemanticProviderError(
                    "semantic_provider_identity_mismatch"
                )
            parsed = parse_semantic_document_result(
                response.parsed_output,
                self.taxonomy,
            )
            if parsed.document_id != normalized_id:
                raise SemanticContractError(
                    "semantic_document_id_mismatch"
                )
            status = "no_event" if not parsed.events else "succeeded"
            row = self.store.finish_semantic_run(
                run_id,
                status=status,
                output_hash=output_hash,
                output_uri=output_uri,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                error="",
            )
            return SemanticPipelineResult(
                document_id=normalized_id,
                status=str(row["status"]),
                route=route,
                run_id=run_id,
            )
        except SemanticProviderError as exc:
            if exc.raw_output is not None and output_uri is None:
                try:
                    output_hash, output_uri = self._persist_raw_output(
                        input_hash=input_hash,
                        payload=exc.raw_output.encode("utf-8"),
                        content_type="text/plain",
                    )
                except BlobStoreError:
                    output_hash = None
                    output_uri = None
            status = _provider_failure_status(exc)
            return self._finish_failure(
                run_id=run_id,
                document_id=normalized_id,
                route=route,
                status=status,
                error=exc.code,
                output_hash=output_hash,
                output_uri=output_uri,
            )
        except SemanticContractError as exc:
            return self._finish_failure(
                run_id=run_id,
                document_id=normalized_id,
                route=route,
                status="failed_terminal",
                error=exc.code,
                output_hash=output_hash,
                output_uri=output_uri,
            )
        except BlobStoreError:
            return self._finish_failure(
                run_id=run_id,
                document_id=normalized_id,
                route=route,
                status="failed_retryable",
                error="semantic_output_blob_store_failed",
            )
        except Exception:
            return self._finish_failure(
                run_id=run_id,
                document_id=normalized_id,
                route=route,
                status="failed_retryable",
                error="semantic_pipeline_unexpected",
                output_hash=output_hash,
                output_uri=output_uri,
            )

    def process_ready(self, *, limit: int = 500) -> tuple[SemanticPipelineResult, ...]:
        return tuple(
            self.process_document(document_id)
            for document_id in self.store.semantic_ready_document_ids(
                limit=limit
            )
        )

    def _route_snapshot(
        self,
        snapshot: Mapping[str, object],
    ) -> SemanticRoute:
        document = _mapping(snapshot["document"])
        artifact_value = snapshot.get("artifact")
        artifact = (
            _mapping(artifact_value)
            if isinstance(artifact_value, Mapping)
            else {}
        )
        return route_document(
            document_hash=str(document.get("content_hash") or ""),
            title=str(document.get("title") or ""),
            artifact_status=str(artifact.get("status") or ""),
            chunks=tuple(
                _mapping(item)
                for item in _sequence(snapshot.get("chunks"))
                if str(_mapping(item).get("section") or "")
                not in _SEMANTIC_DERIVED_SECTIONS
            ),
            tables=tuple(
                _mapping(item)
                for item in _sequence(snapshot.get("tables"))
            ),
            rule_event_types=tuple(
                str(value)
                for value in _sequence(snapshot.get("rule_event_types"))
            ),
            revised=bool(
                document.get("revised_at")
                or document.get("revision_of")
                or _sequence(snapshot.get("revision_context"))
            ),
            audit_sample_rate=self.audit_sample_rate,
        )

    def _input_hash(self, bundle: SemanticInputBundle) -> str:
        return hashlib.sha256(
            _canonical_json(
                {
                    "document_id": bundle.document_id,
                    "artifact_hash": bundle.artifact_hash,
                    "parser_version": bundle.parser_version,
                    "prompt_version": bundle.prompt_version,
                    "schema_version": bundle.schema_version,
                    "taxonomy_version": bundle.taxonomy_version,
                    "payload": bundle.payload,
                }
            ).encode("utf-8")
        ).hexdigest()

    def _persist_raw_output(
        self,
        *,
        input_hash: str,
        payload: bytes,
        content_type: str,
    ) -> tuple[str, str]:
        output_hash = hashlib.sha256(payload).hexdigest()
        key = (
            f"announcements/semantic/{input_hash[:2]}/{input_hash}/"
            f"{output_hash}.json"
        )
        uri = self.blob_store.put_if_absent(
            key,
            payload,
            content_type,
        )
        return output_hash, uri

    def _finish_failure(
        self,
        *,
        run_id: str,
        document_id: int,
        route: SemanticRoute,
        status: str,
        error: str,
        output_hash: str | None = None,
        output_uri: str | None = None,
    ) -> SemanticPipelineResult:
        row = self.store.finish_semantic_run(
            run_id,
            status=status,
            output_hash=output_hash,
            output_uri=output_uri,
            error=error,
        )
        return SemanticPipelineResult(
            document_id=document_id,
            status=str(row["status"]),
            route=route,
            run_id=run_id,
            error=str(row["error"] or ""),
        )


def _provider_failure_status(error: SemanticProviderError) -> str:
    if error.code == "semantic_provider_unavailable":
        return "unavailable"
    if error.code in {
        "semantic_daily_document_budget_exhausted",
        "semantic_daily_token_budget_exhausted",
        "semantic_provider_account_overdue",
        "semantic_provider_payment_required",
    }:
        return "budget_deferred"
    return "failed_retryable" if error.retryable else "failed_terminal"


def _segment_evidence_chunks(
    chunks: list[dict[str, object]],
    *,
    max_characters: int = 4_000,
) -> list[dict[str, object]]:
    """Split oversized parser blocks without truncation or overlapping text."""

    segmented: list[dict[str, object]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        if len(text) <= max_characters:
            segmented.append(chunk)
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        for part_index, start in enumerate(
            range(0, len(text), max_characters),
            start=1,
        ):
            segmented.append(
                {
                    **chunk,
                    "chunk_id": f"{chunk_id}-part{part_index:04d}",
                    "section": "semantic_segment",
                    "source_chunk_id": chunk_id,
                    "source_start": start,
                    "source_end": min(len(text), start + max_characters),
                    "text": text[start : start + max_characters],
                }
            )
    return segmented


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise ValueError("semantic_input_mapping_invalid")


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    if value is None:
        return ()
    raise ValueError("semantic_input_sequence_invalid")


def _json_mapping(value: object) -> dict[str, object]:
    parsed = _json_value(value, fallback={})
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: object, *, fallback: object) -> object:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


__all__ = ["SemanticPipeline", "SemanticPipelineResult"]
