"""Provider-neutral runner for the frozen announcement workbench."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..store import IntelligenceStore
from .contracts import SemanticContractError, load_semantic_prompt
from .document_ir import build_document_ir, ir_nodes_by_id
from .exchange import (
    SemanticExchangeError,
    _bound_v21_payload,
    _context_repair_can_be_no_event,
    _family_repair_targets,
    _grounding_repair_bundle,
    _merge_family_repair_result,
    _missing_routed_event_types,
    _mention_templates,
    _read_executor_config,
    _revision_rejection_can_be_no_event,
    _validate_provider_result,
    canonical_json_hash,
)
from .mention_contracts import announcement_mention_lite_schema
from .pipeline import _segment_evidence_chunks
from .provider import (
    OpenAICompatibleSemanticProvider,
    SemanticExtractionProvider,
    SemanticInputBundle,
    SemanticProviderError,
)
from .router import SemanticRoute, route_document
from .taxonomy import EventTaxonomy


FROZEN_PARSER_VERSION = "anchor-workbench-v1"


@dataclass(frozen=True)
class FrozenBenchmarkInput:
    bundle: SemanticInputBundle
    full_document_ir: Mapping[str, object]
    route: SemanticRoute
    profile_id: str
    profile_hash: str


def build_frozen_benchmark_input(
    repo_root: str | Path,
    document_root: str | Path,
    *,
    profile_id: str,
) -> FrozenBenchmarkInput:
    root = Path(repo_root).resolve()
    workbench = Path(document_root).resolve()
    profile_path = _profile_path(root, profile_id)
    profile = _read_mapping(profile_path)
    if str(profile.get("profile_id") or "") != profile_id:
        raise ValueError("semantic_benchmark_profile_id_mismatch")
    if not str(profile.get("document_ir_version") or ""):
        raise ValueError("semantic_benchmark_document_ir_required")
    taxonomy_path = root / str(profile.get("taxonomy_path") or "")
    taxonomy = EventTaxonomy.load(taxonomy_path)
    if taxonomy.taxonomy_version != str(profile.get("taxonomy_version") or ""):
        raise ValueError("semantic_benchmark_taxonomy_version_mismatch")

    document = _read_mapping(workbench / "document.json")
    chunks = _read_mapping_list(workbench / "chunks.json")
    tables = _read_mapping_list(workbench / "tables.json")
    whitelist = _read_mapping_list(workbench / "entity_whitelist.json")
    revisions = _read_mapping_list(workbench / "revision_context.json")
    document_id = _positive_int(document.get("id"))

    route_hash = _hash_json(
        {
            "document": document,
            "chunks": chunks,
            "tables": tables,
            "revisions": revisions,
        }
    )
    route = route_document(
        document_hash=route_hash,
        title=str(document.get("title") or ""),
        artifact_status="parsed",
        chunks=chunks,
        tables=tables,
        revised=bool(revisions),
        audit_sample_rate=float(profile.get("audit_sample_rate", 0.0)),
    )
    normalized_chunks = [
        {
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "page_number": int(chunk.get("page_number") or 0),
            "section": str(chunk.get("section") or ""),
            "bbox": list(chunk.get("bbox") or []),
            "text": str(chunk.get("text") or ""),
        }
        for chunk in chunks
    ]
    for suffix, value in (
        ("title", str(document.get("title") or "")),
        ("issuer", str(document.get("name") or "")),
    ):
        if value:
            normalized_chunks.append(
                {
                    "chunk_id": f"doc{document_id}-meta-{suffix}",
                    "page_number": 1,
                    "section": "document_metadata",
                    "bbox": [],
                    "text": value,
                }
            )
    normalized_chunks = _segment_evidence_chunks(normalized_chunks)
    normalized_tables = [
        {
            "table_id": str(table.get("table_id") or ""),
            "page_number": int(table.get("page_number") or 0),
            "bbox": list(table.get("bbox") or []),
            "cells": list(table.get("cells") or []),
        }
        for table in tables
    ]
    document_payload = {
        "id": document_id,
        "title": str(document.get("title") or ""),
        "ts_code": str(document.get("ts_code") or ""),
        "name": str(document.get("name") or ""),
        "published_at": str(document.get("published_at") or ""),
        "rec_time": "",
        "source_url": str(document.get("source_url") or ""),
    }
    document_ir = build_document_ir(
        document=document_payload,
        chunks=normalized_chunks,
        tables=normalized_tables,
        parser_version=FROZEN_PARSER_VERSION,
    )
    normalized_chunks = _append_table_cell_chunks(
        normalized_chunks,
        normalized_tables,
    )
    categories = list(route.categories)
    if route.requires_deep_extraction and not categories:
        categories = sorted(taxonomy.event_types)
    payload: dict[str, object] = {
        "document": document_payload,
        "taxonomy_candidates": categories,
        "entity_whitelist": [dict(item) for item in whitelist],
        "chunks": normalized_chunks,
        "tables": normalized_tables,
        "revision_context": [dict(item) for item in revisions],
        "route_context": {
            "document_kind": route.document_kind,
            "extraction_purpose": route.extraction_purpose,
            "difficulty_tags": list(route.difficulty_tags),
            "reason_codes": list(route.reason_codes),
        },
        "document_ir": document_ir,
        "retriever_version": str(profile.get("retriever_version") or ""),
        "mention_templates": _mention_templates(taxonomy, categories),
    }
    max_characters = int(profile.get("max_evidence_packet_chars") or 0)
    if max_characters <= 0:
        raise ValueError("semantic_benchmark_evidence_budget_invalid")
    bounded_payload = _bound_v21_payload(
        payload,
        max_input_characters=max_characters,
    )
    artifact_hash = _hash_json(
        {"document": document, "chunks": chunks, "tables": tables}
    )
    bundle = SemanticInputBundle(
        document_id=document_id,
        artifact_hash=artifact_hash,
        parser_version=FROZEN_PARSER_VERSION,
        prompt_version=str(profile.get("prompt_version") or ""),
        schema_version=str(profile.get("schema_version") or ""),
        taxonomy_version=taxonomy.taxonomy_version,
        payload=bounded_payload,
        input_token_estimate=max(
            1,
            (len(_canonical_json(bounded_payload)) + 3) // 4,
        ),
    )
    return FrozenBenchmarkInput(
        bundle=bundle,
        full_document_ir=document_ir,
        route=route,
        profile_id=profile_id,
        profile_hash=canonical_json_hash(profile),
    )


def run_frozen_benchmark(
    repo_root: str | Path,
    workbench_root: str | Path,
    *,
    profile_id: str,
    predictions_path: str | Path,
    report_path: str | Path,
    executor_config: str | Path | None = None,
    provider: SemanticExtractionProvider | None = None,
    limit: int | None = None,
    document_ids: Sequence[int] | None = None,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    benchmark_root = Path(workbench_root).resolve()
    predictions_target = Path(predictions_path).resolve()
    report_target = Path(report_path).resolve()
    profile = _read_mapping(_profile_path(root, profile_id))
    taxonomy = EventTaxonomy.load(root / str(profile["taxonomy_path"]))
    selected_provider = provider or _provider_from_config(
        root,
        str(profile["prompt_version"]),
        executor_config,
    )
    schema = announcement_mention_lite_schema()
    wanted = {int(value) for value in document_ids or ()}
    document_roots = sorted(
        (
            path
            for path in benchmark_root.iterdir()
            if path.is_dir()
            and path.name.isdigit()
            and (not wanted or int(path.name) in wanted)
        ),
        key=lambda path: int(path.name),
    )
    if limit is not None:
        document_roots = document_roots[: max(0, int(limit))]

    rows: list[dict[str, object]] = []
    completed = failed = routed_no_event = 0
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "request_count": 0,
    }
    validation_repairs = 0
    validation_repair_failures = 0
    with tempfile.TemporaryDirectory(prefix="semantic-benchmark-") as tmp:
        store = IntelligenceStore(Path(tmp) / "intelligence")
        for document_root in document_roots:
            benchmark_input: FrozenBenchmarkInput | None = None
            bundle: SemanticInputBundle | None = None
            response = None
            provider_result: Mapping[str, object] | None = None
            responses = []
            usage_recorded = False
            document_usage: dict[str, int | None] | None = None
            try:
                benchmark_input = build_frozen_benchmark_input(
                    root,
                    document_root,
                    profile_id=profile_id,
                )
                bundle = benchmark_input.bundle
                if not benchmark_input.route.requires_deep_extraction:
                    normalized = {
                        "document_id": int(bundle.document_id),
                        "schema_version": "announcement-event-lite-v1",
                        "events": [],
                        "evidence": [],
                        "no_event_reason": "router:" + benchmark_input.route.decision,
                    }
                    compilation = {
                        "accepted": 0,
                        "rejected": 0,
                        "dropped": 0,
                        "rejected_mentions": [],
                    }
                    status = "routed_no_event"
                    routed_no_event += 1
                    executor = None
                    document_usage = None
                else:
                    response = selected_provider.extract(
                        bundle,
                        response_schema=schema,
                    )
                    responses = [response]
                    provider_result = response.parsed_output
                    try:
                        normalized, _, compilation = _validate_provider_result(
                            provider_result,
                            taxonomy=taxonomy,
                            bundle=bundle,
                            store=store,
                            full_document_ir=benchmark_input.full_document_ir,
                        )
                        missing_event_types = _missing_routed_event_types(
                            normalized,
                            bundle,
                        )
                        if missing_event_types:
                            raise SemanticContractError(
                                "semantic_candidate_family_unreviewed",
                                detail=",".join(missing_event_types),
                            )
                    except SemanticContractError as exc:
                        validation_repairs += 1
                        repair_bundle = _grounding_repair_bundle(
                            bundle,
                            previous_result=provider_result,
                            error=exc,
                        )
                        response = selected_provider.extract(
                            repair_bundle,
                            response_schema=schema,
                        )
                        responses.append(response)
                        if exc.code == "semantic_candidate_family_unreviewed":
                            provider_result = _merge_family_repair_result(
                                provider_result,
                                response.parsed_output,
                                target_event_types=_family_repair_targets(exc),
                            )
                            validation_bundle = bundle
                        else:
                            provider_result = response.parsed_output
                            validation_bundle = repair_bundle
                        try:
                            normalized, _, compilation = _validate_provider_result(
                                provider_result,
                                taxonomy=taxonomy,
                                bundle=validation_bundle,
                                store=store,
                                full_document_ir=benchmark_input.full_document_ir,
                            )
                            missing_event_types = _missing_routed_event_types(
                                normalized,
                                bundle,
                            )
                            if missing_event_types:
                                raise SemanticContractError(
                                    "semantic_candidate_family_unreviewed",
                                    detail=",".join(missing_event_types),
                                )
                        except SemanticContractError as repair_exc:
                            deterministic_no_event = (
                                _revision_rejection_can_be_no_event(repair_exc)
                                or _context_repair_can_be_no_event(repair_exc, bundle)
                            )
                            if not deterministic_no_event:
                                validation_repair_failures += 1
                                raise
                            provider_result = {
                                "document_id": int(bundle.document_id),
                                "schema_version": bundle.schema_version,
                                "mentions": [],
                                "no_event_reason": "deterministic: no current event survived validation",
                            }
                            normalized, _, compilation = _validate_provider_result(
                                provider_result,
                                taxonomy=taxonomy,
                                bundle=repair_bundle,
                                store=store,
                                full_document_ir=benchmark_input.full_document_ir,
                            )
                    document_usage = _response_usage(responses)
                    _accumulate_usage(usage, document_usage)
                    usage_recorded = True
                    status = "complete"
                    completed += 1
                    executor = {
                        "provider": response.identity.provider,
                        "model": response.identity.model,
                        "client_version": response.identity.client_version,
                        "endpoint_host": response.identity.endpoint_host,
                    }
                rows.append(
                    {
                        **normalized,
                        "status": status,
                        "schema_valid": True,
                        "route": _route_payload(benchmark_input.route),
                        "source_chunks": _source_chunks(
                            bundle.payload,
                            full_document_ir=benchmark_input.full_document_ir,
                        ),
                        "compilation": compilation,
                        "executor": executor,
                        "usage": document_usage,
                        "profile_id": profile_id,
                        "profile_hash": benchmark_input.profile_hash,
                        "input_hash": canonical_json_hash(bundle.payload),
                        **(
                            {"provider_result": provider_result}
                            if response is not None
                            else {}
                        ),
                    }
                )
            except (
                OSError,
                ValueError,
                SemanticContractError,
                SemanticExchangeError,
                SemanticProviderError,
            ) as exc:
                failed += 1
                if responses and not usage_recorded:
                    document_usage = _response_usage(responses)
                    _accumulate_usage(usage, document_usage)
                    usage_recorded = True
                failed_row: dict[str, object] = {
                        "document_id": int(document_root.name),
                        "schema_version": "announcement-event-lite-v1",
                        "events": [],
                        "evidence": [],
                        "no_event_reason": None,
                        "status": "failed",
                        "schema_valid": False,
                        "error": getattr(exc, "code", type(exc).__name__),
                        "error_detail": getattr(exc, "detail", str(exc)),
                        "profile_id": profile_id,
                        "usage": document_usage,
                    }
                if response is not None:
                    failed_row["executor"] = {
                        "provider": response.identity.provider,
                        "model": response.identity.model,
                        "client_version": response.identity.client_version,
                        "endpoint_host": response.identity.endpoint_host,
                    }
                    failed_row["provider_result"] = (
                        provider_result or response.parsed_output
                    )
                    failed_row["provider_attempts"] = [
                        {
                            "request_id": str(item.request_id or ""),
                            "output_hash": str(item.output_hash or ""),
                            "result": item.parsed_output,
                        }
                        for item in responses
                    ]
                if benchmark_input is not None:
                    failed_row["route"] = _route_payload(benchmark_input.route)
                if bundle is not None:
                    failed_row["source_chunks"] = _source_chunks(
                        bundle.payload,
                        full_document_ir=(
                            benchmark_input.full_document_ir
                            if benchmark_input is not None
                            else None
                        ),
                    )
                    failed_row["input_hash"] = canonical_json_hash(bundle.payload)
                rows.append(failed_row)
            _write_jsonl(predictions_target, rows)

    report = {
        "schema_version": 1,
        "status": "complete" if failed == 0 else "partial",
        "profile_id": profile_id,
        "profile_hash": canonical_json_hash(profile),
        "executor": {
            "provider": selected_provider.identity.provider,
            "model": selected_provider.identity.model,
            "client_version": selected_provider.identity.client_version,
            "endpoint_host": selected_provider.identity.endpoint_host,
        },
        "documents": len(rows),
        "completed": completed,
        "routed_no_event": routed_no_event,
        "failed": failed,
        "usage": usage,
        "validation_repairs": validation_repairs,
        "validation_repair_failures": validation_repair_failures,
        "predictions_path": str(predictions_target),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_import": False,
    }
    _write_json(report_target, report)
    return report


def _response_usage(responses) -> dict[str, int]:
    return {
        "input_tokens": sum(int(item.input_tokens or 0) for item in responses),
        "output_tokens": sum(int(item.output_tokens or 0) for item in responses),
        "total_tokens": sum(int(item.total_tokens or 0) for item in responses),
        "latency_ms": sum(int(item.latency_ms or 0) for item in responses),
        "request_count": len(responses),
    }


def _accumulate_usage(
    total: dict[str, int],
    document: Mapping[str, int],
) -> None:
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "request_count",
    ):
        total[key] += int(document.get(key) or 0)


def _provider_from_config(
    repo_root: Path,
    prompt_version: str,
    executor_config: str | Path | None,
) -> SemanticExtractionProvider:
    config = _read_executor_config(executor_config)
    prompt = load_semantic_prompt(repo_root, prompt_version)
    try:
        return OpenAICompatibleSemanticProvider.from_executor_config(
            config,
            system_prompt=prompt,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("semantic_benchmark_executor_config_invalid") from exc


def _append_table_cell_chunks(
    chunks: list[dict[str, object]],
    tables: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result = list(chunks)
    known_ids = {str(chunk.get("chunk_id") or "") for chunk in result}
    for table in tables:
        table_id = str(table.get("table_id") or "")
        page_number = int(table.get("page_number") or 0)
        for cell_index, cell in enumerate(
            value for value in table.get("cells", []) if isinstance(value, Mapping)
        ):
            text = str(cell.get("text") or "").strip()
            if not text:
                continue
            row_index = int(cell.get("row_index") or 0)
            column_value = cell.get("column_index")
            column_index = cell_index if column_value is None else int(column_value)
            chunk_id = f"{table_id}-r{row_index}-c{column_index}"
            if chunk_id in known_ids:
                continue
            result.append(
                {
                    "chunk_id": chunk_id,
                    "page_number": page_number,
                    "section": "table_cell",
                    "bbox": list(cell.get("bbox") or []),
                    "text": text,
                }
            )
            known_ids.add(chunk_id)
    return result


def _source_chunks(
    payload: Mapping[str, object],
    *,
    full_document_ir: Mapping[str, object] | None = None,
) -> list[dict[str, str]]:
    values: dict[str, str] = {}
    for chunk in payload.get("chunks", []):
        if isinstance(chunk, Mapping):
            values[str(chunk.get("chunk_id") or "")] = str(chunk.get("text") or "")
    raw_ir = payload.get("document_ir")
    if isinstance(raw_ir, Mapping):
        for node_id, node in ir_nodes_by_id(raw_ir).items():
            text = str(node.get("text") or node.get("raw_value") or "")
            if text:
                values.setdefault(node_id, text)
    if isinstance(full_document_ir, Mapping):
        for node_id, node in ir_nodes_by_id(full_document_ir).items():
            text = str(node.get("text") or node.get("raw_value") or "")
            if text:
                values.setdefault(node_id, text)
    return [
        {"chunk_id": chunk_id, "text": values[chunk_id]}
        for chunk_id in sorted(values)
    ]


def _route_payload(route: SemanticRoute) -> dict[str, object]:
    return {
        "decision": route.decision,
        "categories": list(route.categories),
        "reason_codes": list(route.reason_codes),
        "document_kind": route.document_kind,
        "extraction_purpose": route.extraction_purpose,
        "difficulty_tags": list(route.difficulty_tags),
    }


def _read_mapping(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"semantic_benchmark_mapping_invalid:{path}")
    return dict(value)


def _profile_path(repo_root: Path, profile_id: str) -> Path:
    matches: list[Path] = []
    profile_root = repo_root / "configs" / "intelligence_extraction_profiles"
    for path in sorted(profile_root.glob("*.json")):
        try:
            profile = _read_mapping(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(profile.get("profile_id") or "") == str(profile_id):
            matches.append(path)
    if len(matches) != 1:
        raise ValueError("semantic_benchmark_profile_not_found")
    return matches[0]


def _read_mapping_list(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"semantic_benchmark_list_invalid:{path}")
    return [dict(item) for item in value]


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("semantic_benchmark_document_id_invalid")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic_benchmark_document_id_invalid") from exc
    if parsed <= 0:
        raise ValueError("semantic_benchmark_document_id_invalid")
    return parsed


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(_canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "FrozenBenchmarkInput",
    "build_frozen_benchmark_input",
    "run_frozen_benchmark",
]
