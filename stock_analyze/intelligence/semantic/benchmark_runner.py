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
from .execution_contract import (
    ExecutorBinding,
    execution_job_id,
    semantic_task_id,
)
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


FROZEN_CODING_PLAN_INPUT_VERSION = "semantic-payload-v4"
FROZEN_CODING_PLAN_OUTPUT_VERSION = "semantic-extraction-output-v1"


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


def prepare_frozen_coding_plan_job(
    repo_root: str | Path,
    workbench_root: str | Path,
    *,
    profile_id: str,
    job_dir: str | Path,
    provider: str,
    model: str,
    client_version: str,
    limit: int | None = None,
    document_ids: Sequence[int] | None = None,
) -> dict[str, object]:
    """Export a self-contained blind benchmark package for a Coding Plan."""
    root = Path(repo_root).resolve()
    benchmark_root = Path(workbench_root).resolve()
    target = Path(job_dir).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("semantic_frozen_coding_plan_job_not_empty")
    target.mkdir(parents=True, exist_ok=True)

    profile_path = _profile_path(root, profile_id)
    profile = _read_mapping(profile_path)
    profile_hash = canonical_json_hash(profile)
    binding = ExecutorBinding(
        executor_mode="coding_plan",
        provider=provider,
        model=model,
        client_version=client_version,
    )
    document_roots = _selected_document_roots(
        benchmark_root,
        limit=limit,
        document_ids=document_ids,
    )
    input_rows: list[dict[str, object]] = []
    document_ir_rows: list[dict[str, object]] = []
    manifest_documents: list[dict[str, object]] = []
    for document_root in document_roots:
        benchmark_input = build_frozen_benchmark_input(
            root,
            document_root,
            profile_id=profile_id,
        )
        bundle = benchmark_input.bundle
        input_hash = canonical_json_hash(bundle.payload)
        task_id = semantic_task_id(
            profile_hash=profile_hash,
            document_id=bundle.document_id,
            artifact_hash=bundle.artifact_hash,
            input_hash=input_hash,
        )
        task_execution_job_id = execution_job_id(task_id, binding)
        common = {
            "document_id": int(bundle.document_id),
            "artifact_hash": bundle.artifact_hash,
            "input_hash": input_hash,
            "semantic_task_id": task_id,
            "execution_job_id": task_execution_job_id,
            "binding_id": binding.binding_id,
        }
        requires_execution = bool(benchmark_input.route.requires_deep_extraction)
        manifest_documents.append(
            {
                **common,
                "requires_execution": requires_execution,
                "route": _route_payload(benchmark_input.route),
            }
        )
        if not requires_execution:
            continue
        input_rows.append(
            {
                "contract_version": FROZEN_CODING_PLAN_INPUT_VERSION,
                **common,
                "profile_id": profile_id,
                "profile_hash": profile_hash,
                "executor": {
                    **binding.to_mapping(),
                    "binding_id": binding.binding_id,
                },
                "payload": bundle.payload,
            }
        )
        document_ir_rows.append(
            {
                "contract_version": FROZEN_CODING_PLAN_INPUT_VERSION,
                **common,
                "document_ir": benchmark_input.full_document_ir,
            }
        )

    job_identity = {
        "profile_hash": profile_hash,
        "binding_id": binding.binding_id,
        "document_ids": [item["document_id"] for item in manifest_documents],
    }
    manifest = {
        "contract_version": "semantic-frozen-coding-plan-job-v1",
        "job_id": "sfj-" + canonical_json_hash(job_identity)[:24],
        "status": "prepared",
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "executor": {
            **binding.to_mapping(),
            "binding_id": binding.binding_id,
        },
        "documents": manifest_documents,
        "document_count": len(manifest_documents),
        "input_document_count": len(input_rows),
        "routed_no_event_count": len(manifest_documents) - len(input_rows),
        "output_contract_version": FROZEN_CODING_PLAN_OUTPUT_VERSION,
        "output_path": "output.jsonl",
        "reference_annotations_included": False,
        "production_import": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    taxonomy = _read_mapping(root / str(profile["taxonomy_path"]))
    _write_json(target / "manifest.json", manifest)
    _write_json(target / "profile.json", profile)
    _write_json(target / "schema.json", announcement_mention_lite_schema())
    _write_json(target / "taxonomy.json", taxonomy)
    (target / "prompt.md").write_text(
        load_semantic_prompt(root, str(profile["prompt_version"])).rstrip() + "\n",
        encoding="utf-8",
    )
    _write_jsonl(target / "input.jsonl", input_rows)
    _write_jsonl(target / "document_ir.jsonl", document_ir_rows)
    (target / "README.md").write_text(
        _frozen_coding_plan_readme(manifest),
        encoding="utf-8",
    )
    return {
        "status": "prepared",
        "job_id": manifest["job_id"],
        "job_dir": str(target),
        "documents": len(manifest_documents),
        "input_documents": len(input_rows),
        "routed_no_event": len(manifest_documents) - len(input_rows),
        "production_import": False,
    }


def collect_frozen_coding_plan_job(
    repo_root: str | Path,
    workbench_root: str | Path,
    *,
    job_dir: str | Path,
    predictions_path: str | Path,
    report_path: str | Path,
) -> dict[str, object]:
    """Validate and compile Coding Plan output without importing production data."""
    root = Path(repo_root).resolve()
    benchmark_root = Path(workbench_root).resolve()
    job = Path(job_dir).resolve()
    predictions_target = Path(predictions_path).resolve()
    report_target = Path(report_path).resolve()
    manifest = _read_mapping(job / "manifest.json")
    if manifest.get("contract_version") != "semantic-frozen-coding-plan-job-v1":
        raise ValueError("semantic_frozen_coding_plan_manifest_invalid")
    profile_id = str(manifest.get("profile_id") or "")
    profile = _read_mapping(_profile_path(root, profile_id))
    if canonical_json_hash(profile) != str(manifest.get("profile_hash") or ""):
        raise ValueError("semantic_frozen_coding_plan_profile_hash_mismatch")
    taxonomy = EventTaxonomy.load(root / str(profile["taxonomy_path"]))
    binding_payload = manifest.get("executor")
    if not isinstance(binding_payload, Mapping):
        raise ValueError("semantic_frozen_coding_plan_executor_missing")
    binding = ExecutorBinding.from_mapping(binding_payload)
    if str(binding_payload.get("binding_id") or "") != binding.binding_id:
        raise ValueError("semantic_frozen_coding_plan_binding_mismatch")

    input_rows = _read_jsonl_mappings(job / "input.jsonl")
    inputs = _unique_rows_by_document(input_rows, "semantic_frozen_input_duplicate")
    output_rows = _read_jsonl_mappings(job / "output.jsonl")
    outputs = _unique_rows_by_document(output_rows, "semantic_frozen_output_duplicate")
    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or any(
        not isinstance(item, Mapping) for item in raw_documents
    ):
        raise ValueError("semantic_frozen_coding_plan_documents_invalid")

    rows: list[dict[str, object]] = []
    completed = failed = routed_no_event = 0
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "request_count": 0,
    }
    expected_output_ids: set[int] = set()
    with tempfile.TemporaryDirectory(prefix="semantic-coding-plan-collect-") as tmp:
        store = IntelligenceStore(Path(tmp) / "intelligence")
        for manifest_document in raw_documents:
            document_id = _positive_int(manifest_document.get("document_id"))
            benchmark_input: FrozenBenchmarkInput | None = None
            try:
                benchmark_input = build_frozen_benchmark_input(
                    root,
                    benchmark_root / str(document_id),
                    profile_id=profile_id,
                )
                bundle = benchmark_input.bundle
                if not bool(manifest_document.get("requires_execution")):
                    normalized = {
                        "document_id": document_id,
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
                    provider_result = None
                    executor = None
                    document_usage = None
                    status = "routed_no_event"
                    routed_no_event += 1
                else:
                    expected_output_ids.add(document_id)
                    input_row = inputs.get(document_id)
                    output_row = outputs.get(document_id)
                    if input_row is None:
                        raise ValueError("semantic_frozen_coding_plan_input_missing")
                    _verify_frozen_input_contract(
                        manifest_document,
                        input_row,
                        bundle=bundle,
                        profile_id=profile_id,
                        profile_hash=benchmark_input.profile_hash,
                        binding=binding,
                    )
                    if output_row is None:
                        raise ValueError("semantic_frozen_coding_plan_output_missing")
                    _verify_frozen_output_envelope(input_row, output_row, binding)
                    provider_result = output_row.get("result")
                    if not isinstance(provider_result, Mapping):
                        raise ValueError("semantic_frozen_coding_plan_result_invalid")
                    normalized, _, compilation = _validate_provider_result(
                        provider_result,
                        taxonomy=taxonomy,
                        bundle=bundle,
                        store=store,
                        full_document_ir=benchmark_input.full_document_ir,
                    )
                    missing_event_types = _missing_routed_event_types(normalized, bundle)
                    if missing_event_types:
                        raise SemanticContractError(
                            "semantic_candidate_family_unreviewed",
                            detail=",".join(missing_event_types),
                        )
                    raw_usage = output_row.get("usage")
                    document_usage = (
                        {str(key): int(value or 0) for key, value in raw_usage.items()}
                        if isinstance(raw_usage, Mapping)
                        else {}
                    )
                    document_usage.setdefault("request_count", 1)
                    _accumulate_usage(usage, document_usage)
                    executor = {
                        "provider": binding.provider,
                        "model": binding.model,
                        "client_version": binding.client_version,
                        "endpoint_host": "coding-plan",
                    }
                    status = "complete"
                    completed += 1
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
                            {"provider_result": dict(provider_result)}
                            if provider_result is not None
                            else {}
                        ),
                    }
                )
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                SemanticContractError,
                SemanticExchangeError,
            ) as exc:
                failed += 1
                failed_row: dict[str, object] = {
                    "document_id": document_id,
                    "schema_version": "announcement-event-lite-v1",
                    "events": [],
                    "evidence": [],
                    "no_event_reason": None,
                    "status": "failed",
                    "schema_valid": False,
                    "error": getattr(exc, "code", type(exc).__name__),
                    "error_detail": getattr(exc, "detail", str(exc)),
                    "profile_id": profile_id,
                }
                if benchmark_input is not None:
                    failed_row["route"] = _route_payload(benchmark_input.route)
                    failed_row["source_chunks"] = _source_chunks(
                        benchmark_input.bundle.payload,
                        full_document_ir=benchmark_input.full_document_ir,
                    )
                rows.append(failed_row)
    _write_jsonl(predictions_target, rows)
    unexpected_output_ids = sorted(set(outputs) - expected_output_ids)
    if unexpected_output_ids:
        failed += len(unexpected_output_ids)
    report = {
        "schema_version": 1,
        "status": "complete" if failed == 0 else "partial",
        "profile_id": profile_id,
        "profile_hash": canonical_json_hash(profile),
        "job_id": str(manifest.get("job_id") or ""),
        "executor": {
            "kind": "coding-plan",
            "provider": binding.provider,
            "model": binding.model,
            "client_version": binding.client_version,
        },
        "documents": len(rows),
        "completed": completed,
        "routed_no_event": routed_no_event,
        "failed": failed,
        "unexpected_output_document_ids": unexpected_output_ids,
        "usage": usage,
        "predictions_path": str(predictions_target),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_import": False,
    }
    _write_json(report_target, report)
    return report


def prepare_frozen_coding_plan_repair_job(
    repo_root: str | Path,
    workbench_root: str | Path,
    *,
    source_job_dir: str | Path,
    source_predictions_path: str | Path,
    repair_job_dir: str | Path,
    provider: str,
    model: str,
    client_version: str,
) -> dict[str, object]:
    """Export one bounded repair round for failed external benchmark rows."""
    root = Path(repo_root).resolve()
    benchmark_root = Path(workbench_root).resolve()
    source_job = Path(source_job_dir).resolve()
    target = Path(repair_job_dir).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("semantic_frozen_repair_job_not_empty")
    target.mkdir(parents=True, exist_ok=True)

    source_manifest = _read_mapping(source_job / "manifest.json")
    if source_manifest.get("contract_version") != "semantic-frozen-coding-plan-job-v1":
        raise ValueError("semantic_frozen_repair_source_manifest_invalid")
    profile_id = str(source_manifest.get("profile_id") or "")
    profile = _read_mapping(_profile_path(root, profile_id))
    profile_hash = canonical_json_hash(profile)
    if profile_hash != str(source_manifest.get("profile_hash") or ""):
        raise ValueError("semantic_frozen_repair_profile_hash_mismatch")
    source_binding = _binding_from_manifest(source_manifest)
    binding = ExecutorBinding(
        executor_mode="coding_plan",
        provider=provider,
        model=model,
        client_version=client_version,
    )
    source_inputs = _unique_rows_by_document(
        _read_jsonl_mappings(source_job / "input.jsonl"),
        "semantic_frozen_repair_source_input_duplicate",
    )
    source_outputs = _unique_rows_by_document(
        _read_jsonl_mappings(source_job / "output.jsonl"),
        "semantic_frozen_repair_source_output_duplicate",
    )
    source_predictions_file = Path(source_predictions_path).resolve()
    source_predictions = _unique_rows_by_document(
        _read_jsonl_mappings(source_predictions_file),
        "semantic_frozen_repair_source_prediction_duplicate",
    )
    failed_rows = [
        row
        for row in source_predictions.values()
        if str(row.get("status") or "") == "failed"
    ]

    input_rows: list[dict[str, object]] = []
    document_ir_rows: list[dict[str, object]] = []
    manifest_documents: list[dict[str, object]] = []
    for failed_row in sorted(failed_rows, key=lambda item: int(item["document_id"])):
        document_id = _positive_int(failed_row.get("document_id"))
        source_input = source_inputs.get(document_id)
        source_output = source_outputs.get(document_id)
        if source_input is None or source_output is None:
            raise ValueError("semantic_frozen_repair_source_row_missing")
        benchmark_input = build_frozen_benchmark_input(
            root,
            benchmark_root / str(document_id),
            profile_id=profile_id,
        )
        _verify_frozen_input_contract(
            _manifest_document(source_manifest, document_id),
            source_input,
            bundle=benchmark_input.bundle,
            profile_id=profile_id,
            profile_hash=profile_hash,
            binding=source_binding,
        )
        _verify_frozen_output_envelope(source_input, source_output, source_binding)
        previous_result = source_output.get("result")
        if not isinstance(previous_result, Mapping):
            raise ValueError("semantic_frozen_repair_previous_result_invalid")
        error = SemanticContractError(
            str(failed_row.get("error") or "semantic_validation_failed"),
            detail=str(failed_row.get("error_detail") or ""),
        )
        repair_bundle = _grounding_repair_bundle(
            benchmark_input.bundle,
            previous_result=previous_result,
            error=error,
        )
        input_hash = canonical_json_hash(repair_bundle.payload)
        task_id = semantic_task_id(
            profile_hash=profile_hash,
            document_id=document_id,
            artifact_hash=repair_bundle.artifact_hash,
            input_hash=input_hash,
        )
        bound_execution_job_id = execution_job_id(task_id, binding)
        common = {
            "document_id": document_id,
            "artifact_hash": repair_bundle.artifact_hash,
            "input_hash": input_hash,
            "semantic_task_id": task_id,
            "execution_job_id": bound_execution_job_id,
            "binding_id": binding.binding_id,
        }
        input_rows.append(
            {
                "contract_version": FROZEN_CODING_PLAN_INPUT_VERSION,
                **common,
                "profile_id": profile_id,
                "profile_hash": profile_hash,
                "executor": {
                    **binding.to_mapping(),
                    "binding_id": binding.binding_id,
                },
                "payload": repair_bundle.payload,
            }
        )
        document_ir_rows.append(
            {
                "contract_version": FROZEN_CODING_PLAN_INPUT_VERSION,
                **common,
                "document_ir": benchmark_input.full_document_ir,
            }
        )
        manifest_documents.append(
            {
                **common,
                "source_error": error.code,
                "source_error_detail": error.detail,
                "source_semantic_task_id": str(
                    source_input.get("semantic_task_id") or ""
                ),
            }
        )

    identity = {
        "source_job_id": str(source_manifest.get("job_id") or ""),
        "source_output_hash": _file_hash(source_job / "output.jsonl"),
        "source_predictions_hash": _file_hash(source_predictions_file),
        "binding_id": binding.binding_id,
        "document_ids": [item["document_id"] for item in manifest_documents],
        "attempt": 1,
    }
    manifest = {
        "contract_version": "semantic-frozen-coding-plan-repair-v1",
        "job_id": "sfr-" + canonical_json_hash(identity)[:24],
        "status": "prepared",
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "source_job_id": identity["source_job_id"],
        "source_output_hash": identity["source_output_hash"],
        "source_predictions_hash": identity["source_predictions_hash"],
        "repair_attempt": 1,
        "executor": {
            **binding.to_mapping(),
            "binding_id": binding.binding_id,
        },
        "documents": manifest_documents,
        "document_count": len(manifest_documents),
        "input_document_count": len(input_rows),
        "output_contract_version": FROZEN_CODING_PLAN_OUTPUT_VERSION,
        "output_path": "output.jsonl",
        "reference_annotations_included": False,
        "production_import": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    taxonomy = _read_mapping(root / str(profile["taxonomy_path"]))
    _write_json(target / "manifest.json", manifest)
    _write_json(target / "profile.json", profile)
    _write_json(target / "schema.json", announcement_mention_lite_schema())
    _write_json(target / "taxonomy.json", taxonomy)
    (target / "prompt.md").write_text(
        load_semantic_prompt(root, str(profile["prompt_version"])).rstrip() + "\n",
        encoding="utf-8",
    )
    _write_jsonl(target / "input.jsonl", input_rows)
    _write_jsonl(target / "document_ir.jsonl", document_ir_rows)
    (target / "README.md").write_text(
        _frozen_coding_plan_repair_readme(manifest),
        encoding="utf-8",
    )
    return {
        "status": "prepared" if input_rows else "not_needed",
        "job_id": manifest["job_id"],
        "job_dir": str(target),
        "documents": len(input_rows),
        "repair_attempt": 1,
        "production_import": False,
    }


def collect_frozen_coding_plan_repair_job(
    repo_root: str | Path,
    workbench_root: str | Path,
    *,
    source_job_dir: str | Path,
    source_predictions_path: str | Path,
    repair_job_dir: str | Path,
    predictions_path: str | Path,
    report_path: str | Path,
) -> dict[str, object]:
    """Merge one external repair round and re-run deterministic validation."""
    root = Path(repo_root).resolve()
    benchmark_root = Path(workbench_root).resolve()
    source_job = Path(source_job_dir).resolve()
    repair_job = Path(repair_job_dir).resolve()
    repair_manifest = _read_mapping(repair_job / "manifest.json")
    if repair_manifest.get("contract_version") != "semantic-frozen-coding-plan-repair-v1":
        raise ValueError("semantic_frozen_repair_manifest_invalid")
    if int(repair_manifest.get("repair_attempt") or 0) != 1:
        raise ValueError("semantic_frozen_repair_attempt_invalid")
    if _file_hash(source_job / "output.jsonl") != str(
        repair_manifest.get("source_output_hash") or ""
    ):
        raise ValueError("semantic_frozen_repair_source_output_changed")
    source_predictions_file = Path(source_predictions_path).resolve()
    if _file_hash(source_predictions_file) != str(
        repair_manifest.get("source_predictions_hash") or ""
    ):
        raise ValueError("semantic_frozen_repair_source_predictions_changed")
    source_manifest = _read_mapping(source_job / "manifest.json")
    if str(source_manifest.get("job_id") or "") != str(
        repair_manifest.get("source_job_id") or ""
    ):
        raise ValueError("semantic_frozen_repair_source_job_mismatch")
    profile_id = str(repair_manifest.get("profile_id") or "")
    profile = _read_mapping(_profile_path(root, profile_id))
    profile_hash = canonical_json_hash(profile)
    if profile_hash != str(repair_manifest.get("profile_hash") or ""):
        raise ValueError("semantic_frozen_repair_profile_hash_mismatch")
    taxonomy = EventTaxonomy.load(root / str(profile["taxonomy_path"]))
    binding = _binding_from_manifest(repair_manifest)
    repair_inputs = _unique_rows_by_document(
        _read_jsonl_mappings(repair_job / "input.jsonl"),
        "semantic_frozen_repair_input_duplicate",
    )
    repair_outputs = _unique_rows_by_document(
        _read_jsonl_mappings(repair_job / "output.jsonl"),
        "semantic_frozen_repair_output_duplicate",
    )
    source_outputs = _unique_rows_by_document(
        _read_jsonl_mappings(source_job / "output.jsonl"),
        "semantic_frozen_repair_source_output_duplicate",
    )
    source_rows = _read_jsonl_mappings(source_predictions_file)
    merged = _unique_rows_by_document(
        source_rows,
        "semantic_frozen_repair_source_prediction_duplicate",
    )
    raw_documents = repair_manifest.get("documents")
    if not isinstance(raw_documents, list) or any(
        not isinstance(item, Mapping) for item in raw_documents
    ):
        raise ValueError("semantic_frozen_repair_documents_invalid")
    expected_document_ids = {
        _positive_int(item.get("document_id")) for item in raw_documents
    }
    if set(repair_inputs) != expected_document_ids:
        raise ValueError("semantic_frozen_repair_input_set_mismatch")
    if set(repair_outputs) != expected_document_ids:
        raise ValueError("semantic_frozen_repair_output_set_mismatch")

    repaired = repair_failed = 0
    with tempfile.TemporaryDirectory(prefix="semantic-coding-plan-repair-") as tmp:
        store = IntelligenceStore(Path(tmp) / "intelligence")
        for manifest_document in raw_documents:
            document_id = _positive_int(manifest_document.get("document_id"))
            source_row = merged.get(document_id)
            source_output = source_outputs.get(document_id)
            repair_input = repair_inputs.get(document_id)
            repair_output = repair_outputs.get(document_id)
            if not all((source_row, source_output, repair_input, repair_output)):
                raise ValueError("semantic_frozen_repair_row_missing")
            assert source_row is not None
            assert source_output is not None
            assert repair_input is not None
            assert repair_output is not None
            benchmark_input = build_frozen_benchmark_input(
                root,
                benchmark_root / str(document_id),
                profile_id=profile_id,
            )
            previous_result = source_output.get("result")
            if not isinstance(previous_result, Mapping):
                raise ValueError("semantic_frozen_repair_previous_result_invalid")
            source_error = SemanticContractError(
                str(manifest_document.get("source_error") or ""),
                detail=str(manifest_document.get("source_error_detail") or ""),
            )
            repair_bundle = _grounding_repair_bundle(
                benchmark_input.bundle,
                previous_result=previous_result,
                error=source_error,
            )
            try:
                _verify_frozen_input_contract(
                    manifest_document,
                    repair_input,
                    bundle=repair_bundle,
                    profile_id=profile_id,
                    profile_hash=profile_hash,
                    binding=binding,
                )
                _verify_frozen_output_envelope(repair_input, repair_output, binding)
                repair_result = repair_output.get("result")
                if not isinstance(repair_result, Mapping):
                    raise ValueError("semantic_frozen_repair_result_invalid")
                if source_error.code == "semantic_candidate_family_unreviewed":
                    provider_result = _merge_family_repair_result(
                        previous_result,
                        repair_result,
                        target_event_types=_family_repair_targets(source_error),
                    )
                    validation_bundle = benchmark_input.bundle
                else:
                    provider_result = dict(repair_result)
                    validation_bundle = repair_bundle
                try:
                    normalized, _, compilation = _validate_provider_result(
                        provider_result,
                        taxonomy=taxonomy,
                        bundle=validation_bundle,
                        store=store,
                        full_document_ir=benchmark_input.full_document_ir,
                    )
                    missing = _missing_routed_event_types(
                        normalized,
                        benchmark_input.bundle,
                    )
                    if missing:
                        raise SemanticContractError(
                            "semantic_candidate_family_unreviewed",
                            detail=",".join(missing),
                        )
                except SemanticContractError as exc:
                    if not (
                        _revision_rejection_can_be_no_event(exc)
                        or _context_repair_can_be_no_event(
                            exc,
                            benchmark_input.bundle,
                        )
                    ):
                        raise
                    provider_result = {
                        "document_id": document_id,
                        "schema_version": benchmark_input.bundle.schema_version,
                        "mentions": [],
                        "no_event_reason": (
                            "deterministic: no current event survived validation"
                        ),
                    }
                    normalized, _, compilation = _validate_provider_result(
                        provider_result,
                        taxonomy=taxonomy,
                        bundle=repair_bundle,
                        store=store,
                        full_document_ir=benchmark_input.full_document_ir,
                    )
                raw_usage = repair_output.get("usage")
                usage = dict(raw_usage) if isinstance(raw_usage, Mapping) else {}
                usage.setdefault("request_count", 1)
                merged[document_id] = {
                    **normalized,
                    "status": "complete",
                    "schema_valid": True,
                    "route": _route_payload(benchmark_input.route),
                    "source_chunks": _source_chunks(
                        benchmark_input.bundle.payload,
                        full_document_ir=benchmark_input.full_document_ir,
                    ),
                    "compilation": compilation,
                    "executor": {
                        "provider": binding.provider,
                        "model": binding.model,
                        "client_version": binding.client_version,
                        "endpoint_host": "coding-plan",
                    },
                    "usage": usage,
                    "profile_id": profile_id,
                    "profile_hash": profile_hash,
                    "input_hash": canonical_json_hash(
                        benchmark_input.bundle.payload
                    ),
                    "provider_result": provider_result,
                    "repair_attempt": 1,
                    "source_error": source_error.code,
                }
                repaired += 1
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                SemanticContractError,
                SemanticExchangeError,
            ) as exc:
                failed_row = dict(source_row)
                failed_row.update(
                    {
                        "status": "failed",
                        "schema_valid": False,
                        "repair_attempt": 1,
                        "repair_error": getattr(exc, "code", type(exc).__name__),
                        "repair_error_detail": getattr(exc, "detail", str(exc)),
                    }
                )
                merged[document_id] = failed_row
                repair_failed += 1

    ordered = [merged[int(row["document_id"])] for row in source_rows]
    _write_jsonl(Path(predictions_path).resolve(), ordered)
    total_failed = sum(
        str(row.get("status") or "") == "failed" for row in ordered
    )
    report = {
        "schema_version": 1,
        "status": "complete" if total_failed == 0 else "partial",
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "source_job_id": str(repair_manifest.get("source_job_id") or ""),
        "repair_job_id": str(repair_manifest.get("job_id") or ""),
        "repair_attempt": 1,
        "documents": len(ordered),
        "repair_documents": len(raw_documents),
        "repaired": repaired,
        "repair_failed": repair_failed,
        "failed": total_failed,
        "predictions_path": str(Path(predictions_path).resolve()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_import": False,
    }
    _write_json(Path(report_path).resolve(), report)
    return report


def _response_usage(responses) -> dict[str, int]:
    return {
        "input_tokens": sum(int(item.input_tokens or 0) for item in responses),
        "output_tokens": sum(int(item.output_tokens or 0) for item in responses),
        "total_tokens": sum(int(item.total_tokens or 0) for item in responses),
        "latency_ms": sum(int(item.latency_ms or 0) for item in responses),
        "request_count": len(responses),
    }


def _selected_document_roots(
    workbench_root: Path,
    *,
    limit: int | None,
    document_ids: Sequence[int] | None,
) -> list[Path]:
    wanted = {int(value) for value in document_ids or ()}
    roots = sorted(
        (
            path
            for path in workbench_root.iterdir()
            if path.is_dir()
            and path.name.isdigit()
            and (not wanted or int(path.name) in wanted)
        ),
        key=lambda path: int(path.name),
    )
    if limit is not None:
        roots = roots[: max(0, int(limit))]
    return roots


def _verify_frozen_output_envelope(
    input_row: Mapping[str, object],
    output_row: Mapping[str, object],
    binding: ExecutorBinding,
) -> None:
    if output_row.get("contract_version") != FROZEN_CODING_PLAN_OUTPUT_VERSION:
        raise ValueError("semantic_frozen_output_contract_invalid")
    for field in (
        "document_id",
        "artifact_hash",
        "input_hash",
        "semantic_task_id",
        "execution_job_id",
        "binding_id",
    ):
        if output_row.get(field) != input_row.get(field):
            raise ValueError(f"semantic_frozen_output_{field}_mismatch")
    executor = output_row.get("executor")
    if not isinstance(executor, Mapping):
        raise ValueError("semantic_frozen_output_executor_missing")
    expected = {
        "kind": "coding-plan",
        "provider": binding.provider,
        "model": binding.model,
        "client_version": binding.client_version,
    }
    actual = {key: str(executor.get(key) or "") for key in expected}
    if actual != expected:
        raise ValueError("semantic_frozen_output_executor_mismatch")


def _binding_from_manifest(manifest: Mapping[str, object]) -> ExecutorBinding:
    raw = manifest.get("executor")
    if not isinstance(raw, Mapping):
        raise ValueError("semantic_frozen_executor_missing")
    binding = ExecutorBinding.from_mapping(raw)
    if str(raw.get("binding_id") or "") != binding.binding_id:
        raise ValueError("semantic_frozen_binding_mismatch")
    return binding


def _manifest_document(
    manifest: Mapping[str, object],
    document_id: int,
) -> Mapping[str, object]:
    raw = manifest.get("documents")
    if not isinstance(raw, list):
        raise ValueError("semantic_frozen_documents_invalid")
    matches = [
        item
        for item in raw
        if isinstance(item, Mapping)
        and _positive_int(item.get("document_id")) == int(document_id)
    ]
    if len(matches) != 1:
        raise ValueError("semantic_frozen_manifest_document_missing")
    return matches[0]


def _verify_frozen_input_contract(
    manifest_document: Mapping[str, object],
    input_row: Mapping[str, object],
    *,
    bundle: SemanticInputBundle,
    profile_id: str,
    profile_hash: str,
    binding: ExecutorBinding,
) -> None:
    if input_row.get("contract_version") != FROZEN_CODING_PLAN_INPUT_VERSION:
        raise ValueError("semantic_frozen_input_contract_invalid")
    for field in (
        "document_id",
        "artifact_hash",
        "input_hash",
        "semantic_task_id",
        "execution_job_id",
        "binding_id",
    ):
        if input_row.get(field) != manifest_document.get(field):
            raise ValueError(f"semantic_frozen_input_{field}_mismatch")
    if _positive_int(input_row.get("document_id")) != int(bundle.document_id):
        raise ValueError("semantic_frozen_input_document_id_mismatch")
    if str(input_row.get("artifact_hash") or "") != bundle.artifact_hash:
        raise ValueError("semantic_frozen_input_artifact_hash_mismatch")
    if str(input_row.get("profile_id") or "") != profile_id:
        raise ValueError("semantic_frozen_input_profile_id_mismatch")
    if str(input_row.get("profile_hash") or "") != profile_hash:
        raise ValueError("semantic_frozen_input_profile_hash_mismatch")
    payload = input_row.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("semantic_frozen_input_payload_invalid")
    expected_input_hash = canonical_json_hash(bundle.payload)
    if canonical_json_hash(payload) != expected_input_hash:
        raise ValueError("semantic_frozen_input_payload_hash_mismatch")
    if str(input_row.get("input_hash") or "") != expected_input_hash:
        raise ValueError("semantic_frozen_input_input_hash_mismatch")
    expected_task_id = semantic_task_id(
        profile_hash=profile_hash,
        document_id=bundle.document_id,
        artifact_hash=bundle.artifact_hash,
        input_hash=expected_input_hash,
    )
    if str(input_row.get("semantic_task_id") or "") != expected_task_id:
        raise ValueError("semantic_frozen_input_semantic_task_id_mismatch")
    if str(input_row.get("execution_job_id") or "") != execution_job_id(
        expected_task_id,
        binding,
    ):
        raise ValueError("semantic_frozen_input_execution_job_id_mismatch")
    executor = input_row.get("executor")
    if not isinstance(executor, Mapping):
        raise ValueError("semantic_frozen_input_executor_missing")
    if ExecutorBinding.from_mapping(executor) != binding:
        raise ValueError("semantic_frozen_input_executor_mismatch")
    if str(executor.get("binding_id") or "") != binding.binding_id:
        raise ValueError("semantic_frozen_input_binding_mismatch")


def _read_jsonl_mappings(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, Mapping):
            raise ValueError(f"semantic_frozen_jsonl_row_invalid:{path}:{line_number}")
        rows.append(dict(value))
    return rows


def _unique_rows_by_document(
    rows: Sequence[Mapping[str, object]],
    duplicate_error: str,
) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for row in rows:
        document_id = _positive_int(row.get("document_id"))
        if document_id in result:
            raise ValueError(duplicate_error)
        result[document_id] = dict(row)
    return result


def _frozen_coding_plan_readme(manifest: Mapping[str, object]) -> str:
    executor = manifest.get("executor")
    binding = executor if isinstance(executor, Mapping) else {}
    return f"""# Frozen Coding Plan semantic extraction job

This is a blind, non-production qualification package. Do not search for or
read reference annotations, Gold labels, prior predictions, acceptance reports,
or production semantic outputs.

Read `prompt.md`, `profile.json`, `schema.json`, `taxonomy.json`, and each row
of `input.jsonl`. Use `document_ir.jsonl` only as the full frozen evidence
source. Write exactly one output row for every input row to `output.jsonl.tmp`,
then atomically rename it to `output.jsonl` after all rows are complete.

The outer output envelope must copy all immutable identity fields from the
input row and use:

```json
{{
  "contract_version": "{FROZEN_CODING_PLAN_OUTPUT_VERSION}",
  "document_id": 123,
  "artifact_hash": "copy from input",
  "input_hash": "copy from input",
  "semantic_task_id": "copy from input",
  "execution_job_id": "copy from input",
  "binding_id": "copy from input",
  "executor": {{
    "kind": "coding-plan",
    "provider": "{binding.get('provider', '')}",
    "model": "{binding.get('model', '')}",
    "client_version": "{binding.get('client_version', '')}"
  }},
  "usage": {{}},
  "result": {{}}
}}
```

`result` must satisfy `schema.json`. Every evidence item is an exact
`chunk_id + quote` pair from the frozen input. Never add outside knowledge,
scores, forecasts, trading opinions, or recommendations. Do not modify source
code, configuration, databases, benchmark inputs, or any file other than the
temporary/final output JSONL.

Expected input rows: {manifest.get('input_document_count', 0)}.
Production import: forbidden.
"""


def _frozen_coding_plan_repair_readme(manifest: Mapping[str, object]) -> str:
    executor = manifest.get("executor")
    binding = executor if isinstance(executor, Mapping) else {}
    return f"""# Frozen Coding Plan semantic repair job

This package is the single allowed repair attempt for rows rejected by the
deterministic compiler. It contains no Gold labels or reference annotations.
Read `prompt.md`, then process every row in `input.jsonl`. The row's
`payload.repair_context` names the exact validation error and includes the
previous output. Return the complete result object again, never a field patch.

Only write `output.jsonl.tmp`, then atomically rename it to `output.jsonl`.
Copy every immutable envelope field from the repair input. Use executor:

```json
{{
  "kind": "coding-plan",
  "provider": "{binding.get('provider', '')}",
  "model": "{binding.get('model', '')}",
  "client_version": "{binding.get('client_version', '')}"
}}
```

Do not read reference annotations, prior acceptance reports, or production
semantic outputs. Do not use outside knowledge, modify code/configuration, run
imports, or connect to ECS. Every quote must be an exact contiguous substring
of its cited frozen chunk. This is attempt 1 and no second repair is allowed.

Expected repair rows: {manifest.get('input_document_count', 0)}.
Production import: forbidden.
"""


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    "collect_frozen_coding_plan_job",
    "collect_frozen_coding_plan_repair_job",
    "prepare_frozen_coding_plan_job",
    "prepare_frozen_coding_plan_repair_job",
    "run_frozen_benchmark",
]
