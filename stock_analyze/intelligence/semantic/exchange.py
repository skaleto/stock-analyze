"""Provider-neutral filesystem exchange for bounded semantic extraction jobs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from ...utils import write_text_atomic
from ..blob_store import LocalBlobStore
from ..extraction import SemanticEventCanonicalizer
from ..store import IntelligenceStore
from .contracts import (
    LITE_SCHEMA_VERSION,
    SemanticContractError,
    announcement_event_lite_schema,
    load_semantic_prompt,
    parse_lite_semantic_document_result,
)
from .document_ir import (
    DOCUMENT_IR_VERSION,
    DocumentIRPreflightError,
    ir_nodes_by_id,
    preflight_document_ir,
    preflight_evidence_packet,
    project_document_ir,
)
from .execution_contract import (
    EXECUTION_CONTRACT_VERSION,
    ExecutorBinding,
    SemanticExecutionContractError,
    execution_job_id,
    semantic_task_id,
    verify_executor_identity,
)
from .mention_contracts import (
    MENTION_SCHEMA_VERSION,
    MentionContractError,
    announcement_mention_lite_schema,
    parse_mention_document_result,
)
from .mention_compiler import (
    IR_MENTION_COMPILER_VERSION,
    MENTION_COMPILER_VERSION,
    compile_mentions,
)
from .pipeline import SemanticPipeline
from .provider import (
    SemanticExtractionProvider,
    SemanticInputBundle,
    SemanticProviderIdentity,
)
from .router import title_event_categories
from .taxonomy import EventTaxonomy, FactRequirement
from .validation import (
    CandidateValidationError,
    numeric_raw_value_is_ambiguous,
    validate_candidate,
)


JOB_CONTRACT_VERSION = "semantic-extraction-job-v1"
OUTPUT_CONTRACT_VERSION = "semantic-extraction-output-v1"
DEFAULT_PROFILE_ID = "a-share-announcement-mentions-v1"
DEFAULT_LIMIT = 50
DEFAULT_MAX_INPUT_CHARACTERS = 40_000
PAYLOAD_CONTRACT_VERSION = "semantic-payload-v3"
MAX_JOB_FILE_BYTES = 64 * 1024 * 1024
MAX_JOB_LINE_BYTES = 2 * 1024 * 1024
# Full Document IR is a local, immutable job asset rather than provider output.
# Its per-document row may legitimately exceed the bounded exchange-row limit,
# while the existing whole-file cap still bounds memory use.
MAX_DOCUMENT_IR_LINE_BYTES = MAX_JOB_FILE_BYTES
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_TERMINAL_STATUSES = frozenset(
    {"succeeded", "no_event", "failed_terminal"}
)
_SEMANTIC_EVIDENCE_PROJECTION_SECTIONS = frozenset(
    {"table_cell", "semantic_segment", "document_metadata"}
)

# Provider-neutral no_event review gate. When an executor returns no_event for
# a document whose title/body strongly suggests one of these high-signal event
# types, the result must NOT be silently accepted as a final no_event; it is
# routed to an explicit review/quarantine terminal so a human or re-extraction
# can confirm the absence. The heuristic only flags review -- it never creates a
# canonical event. Token tables are shared with router.title_event_categories
# to keep one source of truth.
_REVIEW_GATE_EVENT_TYPES = (
    "investigation_penalty",
    "risk_warning_delisting",
    "merger_restructuring",
)
_REVIEW_GATE_TOKENS = {
    "investigation_penalty": (
        "立案调查",
        "行政处罚",
        "监管措施",
        "证监罚",
        "处罚决定",
    ),
    "risk_warning_delisting": (
        "退市风险警示",
        "风险警示",
        "退市",
        "*ST",
        "暂停上市",
        "终止上市",
    ),
    "merger_restructuring": (
        "重大资产重组",
        "资产重组",
        "资产置换",
        "收购",
        "并购",
    ),
}
_MERGER_PENDING_APPROVAL_TOKENS = (
    "尚待审批",
    "尚需审批",
    "尚待批准",
    "尚需批准",
    "待监管",
    "待股东大会",
)
_NEGATED_PENALTY_CONTEXT_TOKENS = (
    "未被",
    "未受",
    "未曾被",
    "未曾受",
    "不得被",
    "不存在",
)
_NEGATED_RISK_CONTEXT_TOKENS = (
    "√否",
    "☑否",
    "不存在退市风险",
    "不触及退市风险",
    "未被实施退市风险警示",
)

_V21_EVENT_RETRIEVAL_TERMS: dict[str, tuple[str, ...]] = {
    "earnings_forecast": (
        "业绩预告", "预计", "预增", "预减", "扭亏", "亏损", "净利润",
        "营业收入", "同比",
    ),
    "earnings_flash": (
        "业绩快报", "营业收入", "净利润", "总资产", "净资产", "同比",
    ),
    "buyback": (
        "回购", "回购价格", "回购金额", "回购股份", "资金来源", "实施期限",
    ),
    "shareholder_change": (
        "权益变动", "信息披露义务人", "增持", "减持", "股份增加", "股份减少",
        "协议转让", "持股", "总股本", "股",
    ),
    "dividend": (
        "分红", "利润分配", "权益分派", "每10股", "派发", "股权登记日",
        "除权除息日",
    ),
    "major_contract": (
        "重大合同", "中标", "订单", "合同金额", "签订", "交易对方",
    ),
    "merger_restructuring": (
        "吸收合并", "重组", "并购", "收购", "资产置换", "标的", "交易对价",
        "交易作价", "对价支付",
    ),
    "equity_financing": (
        "定向增发", "非公开发行", "配股", "可转债", "发行股份", "募集资金",
        "发行价格",
    ),
    "guarantee": (
        "担保", "被担保", "担保金额", "担保余额", "本次对外担保",
        "连带责任保证", "流动资金贷款", "贷款", "保证",
    ),
    "pledge_freeze": (
        "质押", "解质押", "冻结", "占其所持",
    ),
    "litigation_arbitration": (
        "诉讼", "仲裁", "涉案金额", "原告", "被告", "裁决", "判决",
    ),
    "investigation_penalty": (
        "立案调查", "行政处罚", "监管措施", "罚款", "证监会",
    ),
    "risk_warning_delisting": (
        "风险警示", "退市", "终止上市", "撤销风险警示", "*st",
    ),
    "capacity_project": (
        "扩产", "产能", "项目投资", "投资建设", "建成投产", "建设规模",
    ),
    "control_change": (
        "控制权变更", "实际控制人变更", "控制权", "实际控制人",
    ),
}
_V21_STATUS_RETRIEVAL_TERMS = (
    "审议通过", "签订", "完成", "实施", "修订", "取消", "终止", "立案",
    "尚需", "尚待", "已披露", "发布", "公告",
)
_V21_REVISION_BOUNDARY_TERMS = (
    "更正说明",
    "更正后",
    "修订后",
    "修改后",
    "调整后",
    "现更正",
    "现修改",
    "现补充为",
    "原来披露",
    "原披露",
    "原公告内容",
    "更正前",
    "修订前",
    "修改前",
    "调整前",
)


def _no_event_review_signal(
    title: str,
    chunks: Mapping[str, Mapping[str, object]],
    *,
    taxonomy_requirements: Sequence[Mapping[str, object]] | None = None,
    no_event_reason: str = "",
    review_all_title_categories: bool = False,
) -> str | None:
    """Return the high-signal event_type whose tokens fire on title+body, or
    ``None`` when no strong signal is present.

    A signal fires only when (a) the document's taxonomy_requirements explicitly
    assign one of the gate event types, or the gate event type is among the
    candidates for a generic (all-types) assignment, AND (b) at least one token
    appears in the title OR in any chunk body. The heuristic is deliberately
    conservative: it never asserts an event occurred, only that a no_event
    outcome for such a document warrants review rather than silent acceptance.
    """

    normalized_title = str(title or "")
    normalized_reason = str(no_event_reason or "")
    body_tokens_seen: set[str] = set()
    body_texts: list[str] = []
    for chunk in chunks.values():
        if not isinstance(chunk, Mapping):
            continue
        if str(chunk.get("section") or "") == "document_metadata":
            continue
        text = str(chunk.get("text") or "")
        body_texts.append(text)
        for event_type, tokens in _REVIEW_GATE_TOKENS.items():
            for token in tokens:
                if token in text:
                    body_tokens_seen.add(token)
    assigned_types: set[str] = set()
    if taxonomy_requirements:
        for requirement in taxonomy_requirements:
            if not isinstance(requirement, Mapping):
                continue
            event_type = str(requirement.get("event_type") or "")
            if event_type:
                assigned_types.add(event_type)
    if review_all_title_categories:
        for event_type in title_event_categories(normalized_title):
            if not taxonomy_requirements or event_type in assigned_types:
                return event_type
    for event_type in _REVIEW_GATE_EVENT_TYPES:
        if taxonomy_requirements and event_type not in assigned_types:
            continue
        tokens = _REVIEW_GATE_TOKENS[event_type]
        title_hit = any(token in normalized_title for token in tokens)
        body_hit = any(
            token in body_tokens_seen
            for token in tokens
            if not (
                event_type == "risk_warning_delisting"
                and token == "*ST"
            )
        )
        if event_type == "investigation_penalty" and not title_hit:
            signal_chunks = [
                text
                for text in body_texts
                if any(token in text for token in tokens)
            ]
            if signal_chunks and all(
                any(
                    negative in text
                    for negative in _NEGATED_PENALTY_CONTEXT_TOKENS
                )
                for text in signal_chunks
            ):
                continue
        if event_type == "merger_restructuring" and not title_hit and not any(
            token in normalized_reason
            for token in _MERGER_PENDING_APPROVAL_TOKENS
        ):
            continue
        if event_type == "risk_warning_delisting" and not title_hit:
            signal_chunks = [
                text
                for text in body_texts
                if any(token in text for token in tokens)
            ]
            if signal_chunks and all(
                any(negative in text for negative in _NEGATED_RISK_CONTEXT_TOKENS)
                for text in signal_chunks
            ):
                continue
        if title_hit or body_hit:
            return event_type
    return None


def _requires_no_event_review(payload: Mapping[str, object]) -> bool:
    """Keep the strict no-event guard on primary event filings only.

    Old payloads without route metadata retain the previous fail-closed
    behavior. Supporting legal/research documents may validly mention event
    terms while concluding that no new current event occurred.
    """

    repair_context = payload.get("repair_context")
    if isinstance(repair_context, Mapping):
        validation_error = repair_context.get("validation_error")
        if isinstance(validation_error, Mapping):
            detail = str(validation_error.get("detail") or "")
            if str(validation_error.get("code") or "") == "no_event_review_required":
                return False
            if (
                str(validation_error.get("code") or "")
                == "semantic_mentions_all_rejected"
                and any(
                    code in detail
                    for code in (
                        "mention_revision_uses_superseded_value",
                        "mention_revision_no_changed_fact",
                    )
                )
            ):
                return False
    route_context = payload.get("route_context")
    if not isinstance(route_context, Mapping):
        return True
    if str(route_context.get("extraction_purpose") or "") != "canonical_event":
        return False
    if "legal_current_event" in {
        str(value)
        for value in route_context.get("reason_codes", [])
        if str(value)
    }:
        return True
    return str(route_context.get("document_kind") or "") in {
        "event_announcement",
        "meeting_resolution",
    }


def _revision_rejection_can_be_no_event(error: SemanticContractError) -> bool:
    if error.code != "semantic_mentions_all_rejected":
        return False
    rejection_rows = [
        row.strip() for row in str(error.detail or "").split(";") if row.strip()
    ]
    if not rejection_rows:
        return False
    for row in rejection_rows:
        _, separator, raw_codes = row.partition(":")
        codes = {
            code.strip() for code in raw_codes.split(",") if code.strip()
        }
        if not separator or codes != {"mention_revision_no_changed_fact"}:
            return False
    return True


def _context_repair_can_be_no_event(
    error: SemanticContractError,
    bundle: SemanticInputBundle,
) -> bool:
    if error.code not in {
        "semantic_context_current_transition_missing",
        "semantic_mentions_all_rejected",
    }:
        return False
    route_context = bundle.payload.get("route_context")
    if not isinstance(route_context, Mapping):
        return False
    if str(route_context.get("document_kind") or "") not in {
        "legal_opinion",
        "supplemental_report",
    }:
        return False
    reason_codes = {
        str(value)
        for value in route_context.get("reason_codes", [])
        if str(value)
    }
    return not bool(
        reason_codes & {"legal_current_event", "revision_context_present"}
    )


class SemanticExchangeError(ValueError):
    """A job exchange rejection with a stable machine-readable code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class _BundleOnlyBlobStore:
    def put_if_absent(
        self,
        key: str,
        payload: bytes,
        content_type: str,
    ) -> str:
        del key, payload, content_type
        raise RuntimeError("semantic_bundle_only_blob_store")


class _BundleOnlyProvider:
    identity = SemanticProviderIdentity(
        provider="artifact-exchange",
        model="unselected",
        endpoint_host="local",
    )

    def extract(self, bundle, *, response_schema):
        del bundle, response_schema
        raise RuntimeError("semantic_bundle_only_provider")


def prepare_job(
    repo_root: str | Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    limit: int = DEFAULT_LIMIT,
    max_input_characters: int = DEFAULT_MAX_INPUT_CHARACTERS,
    executor_mode: str | None = None,
    executor_provider: str | None = None,
    executor_model: str | None = None,
    executor_client_version: str | None = None,
    _document_ids: Sequence[int] | None = None,
    _repair_reason: str = "",
    _allow_terminal_retry: bool = True,
) -> dict[str, object]:
    """Write one immutable bounded job without requiring provider credentials."""

    root = Path(repo_root).resolve()
    bounded_limit = max(1, min(int(limit), 500))
    character_budget = max(1_000, int(max_input_characters))
    profile, profile_path = _load_profile(root, profile_id)
    taxonomy_path = _rooted_path(root, str(profile["taxonomy_path"]))
    taxonomy = EventTaxonomy.load(taxonomy_path)
    if taxonomy.taxonomy_version != str(profile["taxonomy_version"]):
        raise SemanticExchangeError("semantic_profile_taxonomy_version_mismatch")
    prompt_version = str(profile["prompt_version"])
    prompt = load_semantic_prompt(root, prompt_version)
    schema_version = str(profile["schema_version"])
    document_ir_version = str(profile.get("document_ir_version") or "")
    retriever_version = str(profile.get("retriever_version") or "")
    uses_v21_contract = bool(document_ir_version)
    binding: ExecutorBinding | None = None
    if uses_v21_contract:
        if document_ir_version != DOCUMENT_IR_VERSION:
            raise SemanticExchangeError(
                "semantic_profile_document_ir_version_mismatch"
            )
        profile_budget = int(
            profile.get("max_evidence_packet_chars") or 0
        )
        if profile_budget <= 0:
            raise SemanticExchangeError(
                "semantic_profile_evidence_budget_invalid"
            )
        character_budget = min(character_budget, profile_budget)
        try:
            binding = ExecutorBinding(
                executor_mode=str(executor_mode or ""),
                provider=str(executor_provider or ""),
                model=str(executor_model or ""),
                client_version=str(executor_client_version or ""),
            )
        except SemanticExecutionContractError as exc:
            raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
    if schema_version == LITE_SCHEMA_VERSION:
        schema = announcement_event_lite_schema(taxonomy)
    elif schema_version == MENTION_SCHEMA_VERSION:
        schema = announcement_mention_lite_schema()
        expected_compiler = (
            IR_MENTION_COMPILER_VERSION
            if uses_v21_contract
            else MENTION_COMPILER_VERSION
        )
        if str(profile.get("compiler_version") or "") != expected_compiler:
            raise SemanticExchangeError(
                "semantic_profile_compiler_version_mismatch"
            )
    else:
        raise SemanticExchangeError("semantic_profile_schema_version_mismatch")

    store = IntelligenceStore(root / "data" / "shared" / "intelligence")
    pipeline = SemanticPipeline(
        store=store,
        blob_store=_BundleOnlyBlobStore(),
        provider=_BundleOnlyProvider(),
        taxonomy=taxonomy,
        prompt_version=prompt_version,
        schema_version=schema_version,
        document_ir_version=document_ir_version,
        retriever_version=retriever_version,
        audit_sample_rate=float(profile.get("audit_sample_rate", 0.05)),
    )
    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_hash = canonical_json_hash(profile_payload)
    prompt_hash = _text_hash(prompt)
    schema_hash = canonical_json_hash(schema)
    taxonomy_hash = _file_hash(taxonomy_path)
    semantic_contract_hash = canonical_json_hash(
        {
            "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            "profile_hash": profile_hash,
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "taxonomy_hash": taxonomy_hash,
            "document_ir_version": document_ir_version,
            "retriever_version": retriever_version,
            "compiler_version": str(profile.get("compiler_version") or ""),
            "max_evidence_packet_chars": (
                character_budget if uses_v21_contract else None
            ),
        }
    )
    inputs: list[dict[str, object]] = []
    items: list[dict[str, object]] = []
    document_ir_rows: list[dict[str, object]] = []
    evidence_packet_rows: list[dict[str, object]] = []
    priority_codes = _latest_research_universe(
        root,
        market=str(profile.get("market") or ""),
    )
    explicit_ids = tuple(
        dict.fromkeys(int(document_id) for document_id in (_document_ids or ()))
    )
    repair_reason = str(_repair_reason).strip()
    repair_id = ""
    if explicit_ids:
        if any(document_id <= 0 for document_id in explicit_ids):
            raise SemanticExchangeError("semantic_repair_document_id_invalid")
        if not repair_reason:
            raise SemanticExchangeError("semantic_repair_reason_required")
        candidate_ids = list(explicit_ids)
        repair_id = "repair-" + canonical_json_hash(
            {
                "profile_id": profile_id,
                "document_ids": explicit_ids,
                "reason": repair_reason,
            }
        )[:24]
    else:
        if bool(profile.get("repair_only")):
            raise SemanticExchangeError(
                "semantic_repair_explicit_documents_required"
            )
        candidate_ids = _exchange_candidate_ids(
            store,
            profile=profile,
            prompt_version=prompt_version,
            schema_version=schema_version,
            taxonomy_version=taxonomy.taxonomy_version,
            limit=max(bounded_limit * 20, 2_000),
            priority_codes=priority_codes,
            allow_terminal_retry=_allow_terminal_retry,
        )
    for document_id in candidate_ids:
        if len(inputs) >= bounded_limit:
            break
        snapshot = store.semantic_document_snapshot(document_id)
        if not _profile_allows_snapshot(profile, snapshot):
            continue
        route = pipeline.route(document_id)
        if not repair_id and not route.requires_deep_extraction:
            continue
        bundle = pipeline.build_bundle(document_id, route=route)
        if _already_terminal(
            store,
            document_id=document_id,
            artifact_hash=bundle.artifact_hash,
            prompt_version=prompt_version,
            schema_version=schema_version,
            taxonomy_version=taxonomy.taxonomy_version,
            parser_version=bundle.parser_version,
            allow_terminal_retry=_allow_terminal_retry,
        ):
            continue
        extraction_payload = dict(bundle.payload)
        if repair_id:
            extraction_payload["repair_context"] = {
                "contract_version": "semantic-repair-v1",
                "repair_id": repair_id,
                "reason": repair_reason,
                "superseded_runs": _repair_prior_runs(
                    store,
                    document_id=document_id,
                    replacement_prompt_version=prompt_version,
                ),
            }
        if schema_version == MENTION_SCHEMA_VERSION:
            extraction_payload["mention_templates"] = _mention_templates(
                taxonomy,
                extraction_payload.get("taxonomy_candidates"),
            )
        else:
            extraction_payload["taxonomy_requirements"] = (
                _taxonomy_requirements(
                    taxonomy,
                    extraction_payload.get("taxonomy_candidates"),
                )
            )
        if uses_v21_contract:
            bounded_payload = _bound_v21_payload(
                extraction_payload,
                max_input_characters=character_budget,
            )
            semantic_input = {
                "payload_contract_version": "semantic-payload-v4",
                "document_id": bundle.document_id,
                "artifact_hash": bundle.artifact_hash,
                "parser_version": bundle.parser_version,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "taxonomy_version": taxonomy.taxonomy_version,
                "profile_id": str(profile["profile_id"]),
                "semantic_contract_hash": semantic_contract_hash,
                "payload": bounded_payload,
            }
            input_hash = canonical_json_hash(semantic_input)
            task_id = semantic_task_id(
                profile_hash=semantic_contract_hash,
                document_id=bundle.document_id,
                artifact_hash=bundle.artifact_hash,
                input_hash=input_hash,
            )
            assert binding is not None
            bound_execution_job_id = execution_job_id(task_id, binding)
            input_row = {
                "contract_version": JOB_CONTRACT_VERSION,
                **semantic_input,
                "semantic_task_id": task_id,
                "execution_job_id": bound_execution_job_id,
                "binding_id": binding.binding_id,
                "input_hash": input_hash,
            }
            full_ir = _mapping(
                extraction_payload.get("document_ir"),
                "semantic_document_ir_invalid",
            )
            document_ir_rows.append(
                {
                    "semantic_task_id": task_id,
                    "document_id": bundle.document_id,
                    "artifact_hash": bundle.artifact_hash,
                    "ir_hash": str(full_ir.get("ir_hash") or ""),
                    "document_ir": full_ir,
                }
            )
            evidence_packet_rows.append(
                {
                    "semantic_task_id": task_id,
                    "execution_job_id": bound_execution_job_id,
                    "document_id": bundle.document_id,
                    "packet_hash": canonical_json_hash(bounded_payload),
                    "payload": bounded_payload,
                }
            )
        else:
            bounded_payload = _bound_payload(
                extraction_payload,
                max_input_characters=character_budget,
            )
            input_row = {
                "contract_version": JOB_CONTRACT_VERSION,
                "payload_contract_version": PAYLOAD_CONTRACT_VERSION,
                "document_id": bundle.document_id,
                "artifact_hash": bundle.artifact_hash,
                "parser_version": bundle.parser_version,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "taxonomy_version": taxonomy.taxonomy_version,
                "profile_id": str(profile["profile_id"]),
                "payload": bounded_payload,
            }
            input_hash = canonical_json_hash(input_row)
            input_row["input_hash"] = input_hash
        inputs.append(input_row)
        item = {
            "ordinal": len(items),
            "document_id": bundle.document_id,
            "artifact_hash": bundle.artifact_hash,
            "parser_version": bundle.parser_version,
            "input_hash": input_hash,
            "input_token_estimate": max(
                1,
                (len(_canonical_json(input_row)) + 3) // 4,
            ),
            "route": route.decision,
            "categories": list(route.categories),
        }
        if uses_v21_contract:
            item.update(
                {
                    "semantic_task_id": input_row["semantic_task_id"],
                    "execution_job_id": input_row["execution_job_id"],
                    "binding_id": input_row["binding_id"],
                    "packet_hash": canonical_json_hash(bounded_payload),
                    "ir_hash": str(
                        _mapping(
                            extraction_payload.get("document_ir"),
                            "semantic_document_ir_invalid",
                        ).get("ir_hash")
                        or ""
                    ),
                }
            )
        items.append(item)

    contract = {
        "contract_version": JOB_CONTRACT_VERSION,
        "profile_id": str(profile["profile_id"]),
        "profile_hash": profile_hash,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "schema_version": schema_version,
        "schema_hash": schema_hash,
        "taxonomy_version": taxonomy.taxonomy_version,
        "taxonomy_hash": taxonomy_hash,
        "selection_policy": (
            "explicit-document-ids-v1"
            if repair_id
            else "priority-model-universe-live-a-share-v2"
        ),
        "budgets": {
            "max_documents": bounded_limit,
            "max_input_characters_per_document": character_budget,
        },
        "items": items,
    }
    if uses_v21_contract:
        assert binding is not None
        contract.update(
            {
                "execution_contract_version": EXECUTION_CONTRACT_VERSION,
                "semantic_contract_hash": semantic_contract_hash,
                "executor_binding": binding.to_mapping(),
                "binding_id": binding.binding_id,
                "document_ir_version": document_ir_version,
                "document_ir_hash": canonical_json_hash(document_ir_rows),
                "retriever_version": retriever_version,
                "evidence_packets_hash": canonical_json_hash(
                    evidence_packet_rows
                ),
            }
        )
    if schema_version == MENTION_SCHEMA_VERSION:
        contract["compiler_version"] = str(
            profile.get("compiler_version") or MENTION_COMPILER_VERSION
        )
    if repair_id:
        contract.update(
            {
                "repair_contract_version": "semantic-repair-v1",
                "repair_id": repair_id,
                "repair_reason": repair_reason,
            }
        )
    job_id = f"sj-{canonical_json_hash(contract)[:24]}"
    jobs_root = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "extraction_jobs"
    )
    jobs_root.mkdir(parents=True, exist_ok=True)
    job_dir = jobs_root / job_id
    manifest_path = job_dir / "job.json"
    manifest = {
        **contract,
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "prepared",
    }
    lock_root = jobs_root / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    with (lock_root / f"{job_id}.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_path.exists():
            existing = _read_json(manifest_path)
            _verify_manifest(
                existing,
                job_dir=job_dir,
                expected_job_id=job_id,
            )
            if uses_v21_contract:
                _register_v21_lineage(
                    store,
                    manifest=existing,
                    inputs=inputs,
                )
            return _prepare_summary(existing, job_dir, reused=True)
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".{job_id}.",
                dir=jobs_root,
            )
        )
        try:
            _write_json(temporary / "job.json", manifest)
            write_text_atomic(
                temporary / "prompt.md",
                prompt,
                encoding="utf-8",
            )
            _write_json(temporary / "profile.json", profile_payload)
            _write_json(temporary / "schema.json", schema)
            write_text_atomic(
                temporary / "taxonomy.json",
                taxonomy_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            _write_jsonl(temporary / "input.jsonl", inputs)
            if uses_v21_contract:
                _write_jsonl(
                    temporary / "document_ir.jsonl",
                    document_ir_rows,
                )
                _write_jsonl(
                    temporary / "evidence_packets.jsonl",
                    evidence_packet_rows,
                )
            os.replace(temporary, job_dir)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if uses_v21_contract:
        _register_v21_lineage(store, manifest=manifest, inputs=inputs)
    return _prepare_summary(manifest, job_dir, reused=False)


def prepare_repair_job(
    repo_root: str | Path,
    *,
    document_ids: Sequence[int],
    reason: str,
    profile_id: str = "a-share-announcement-remediation-v1",
    max_input_characters: int = DEFAULT_MAX_INPUT_CHARACTERS,
) -> dict[str, object]:
    """Prepare one explicit, versioned remediation batch."""

    normalized_ids = tuple(dict.fromkeys(int(value) for value in document_ids))
    if not normalized_ids:
        raise SemanticExchangeError(
            "semantic_repair_explicit_documents_required"
        )
    return prepare_job(
        repo_root,
        profile_id=profile_id,
        limit=len(normalized_ids),
        max_input_characters=max_input_characters,
        _document_ids=normalized_ids,
        _repair_reason=reason,
    )


def _register_v21_lineage(
    store: IntelligenceStore,
    *,
    manifest: Mapping[str, object],
    inputs: Sequence[Mapping[str, object]],
) -> None:
    try:
        binding = ExecutorBinding.from_mapping(
            _mapping(
                manifest.get("executor_binding"),
                "semantic_executor_binding_invalid",
            )
        )
    except SemanticExecutionContractError as exc:
        raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
    profile_id = str(manifest.get("profile_id") or "")
    store.register_semantic_contract_profile(
        profile_id=profile_id,
        profile_hash=str(manifest.get("semantic_contract_hash") or ""),
        status="shadow",
    )
    store.register_semantic_executor_binding(
        profile_id=profile_id,
        binding=binding,
        status="untested",
    )
    for input_row in inputs:
        task_id = str(input_row.get("semantic_task_id") or "")
        store.register_semantic_task(
            semantic_task_id=task_id,
            document_id=_positive_int(input_row.get("document_id")),
            profile_id=profile_id,
            artifact_hash=str(input_row.get("artifact_hash") or ""),
            input_hash=str(input_row.get("input_hash") or ""),
        )
        store.register_semantic_execution_job(
            execution_job_id=str(input_row.get("execution_job_id") or ""),
            semantic_task_id=task_id,
            binding_id=binding.binding_id,
        )


def rollback_repair(
    repo_root: str | Path,
    repair_id: str,
) -> dict[str, object]:
    store = IntelligenceStore(
        Path(repo_root).resolve() / "data" / "shared" / "intelligence"
    )
    result = store.rollback_semantic_repair(repair_id)
    return {"status": "rolled_back", **result}


def _job_document_ir_by_task(
    job_dir: Path,
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    if manifest.get("execution_contract_version") is None:
        return {}
    items = _sequence(
        manifest.get("items"),
        "semantic_job_items_invalid",
    )
    rows = _read_jsonl(
        job_dir / "document_ir.jsonl",
        max_rows=len(items),
        max_line_bytes=MAX_DOCUMENT_IR_LINE_BYTES,
    )
    by_task: dict[str, Mapping[str, object]] = {}
    for row in rows:
        task_id = str(row.get("semantic_task_id") or "")
        if not task_id or task_id in by_task:
            raise SemanticExchangeError(
                "semantic_document_ir_identity_invalid"
            )
        document_ir = _mapping(
            row.get("document_ir"),
            "semantic_document_ir_invalid",
        )
        try:
            preflight_document_ir(document_ir)
        except DocumentIRPreflightError as exc:
            raise SemanticExchangeError(
                "semantic_document_ir_invalid",
                detail=exc.code,
            ) from exc
        by_task[task_id] = document_ir
    expected = {
        str(item.get("semantic_task_id") or "")
        for item in items
        if isinstance(item, Mapping)
    }
    if set(by_task) != expected:
        raise SemanticExchangeError(
            "semantic_document_ir_identity_invalid"
        )
    return by_task


def _materializable_evidence_chunks(
    input_payload: Mapping[str, object],
    *,
    full_document_ir: Mapping[str, object] | None,
    referenced_chunk_ids: set[str],
) -> tuple[Mapping[str, object], ...]:
    rows: dict[str, Mapping[str, object]] = {}
    for row in _sequence(
        input_payload.get("chunks"),
        "semantic_job_chunks_invalid",
    ):
        if not isinstance(row, Mapping):
            continue
        chunk_id = str(row.get("chunk_id") or "")
        if (
            chunk_id in referenced_chunk_ids
            and str(row.get("section") or "")
            in _SEMANTIC_EVIDENCE_PROJECTION_SECTIONS
        ):
            rows[chunk_id] = row
    if full_document_ir is None:
        return tuple(rows.values())
    try:
        ir_nodes = ir_nodes_by_id(full_document_ir)
    except DocumentIRPreflightError as exc:
        raise SemanticExchangeError(
            "semantic_document_ir_invalid",
            detail=exc.code,
        ) from exc
    for chunk_id in sorted(referenced_chunk_ids):
        if chunk_id in rows:
            continue
        node = ir_nodes.get(chunk_id)
        if node is None:
            continue
        node_type = str(node.get("node_type") or "")
        original_section = str(node.get("section") or "")
        if node_type == "table_cell":
            section = "table_cell"
        elif original_section in _SEMANTIC_EVIDENCE_PROJECTION_SECTIONS:
            section = original_section
        else:
            # Ordinary body nodes are parser chunks that already exist in the
            # store. They are added to the validation map but need no projection.
            continue
        text = str(node.get("text") or node.get("raw_value") or "")
        if not text:
            continue
        rows[chunk_id] = {
            "chunk_id": chunk_id,
            "page_number": int(node.get("page_number") or 0),
            "section": section,
            "bbox": list(node.get("bbox") or []),
            "text": text,
        }
    return tuple(rows[key] for key in sorted(rows))


def import_job(
    repo_root: str | Path,
    job_path: str | Path,
    *,
    refresh_features: bool = False,
) -> dict[str, object]:
    """Validate ready output rows and persist their existing v13 lineage."""

    root = Path(repo_root).resolve()
    job_dir = _resolve_job_dir(root, job_path)
    manifest = _read_json(job_dir / "job.json")
    _verify_manifest(manifest, job_dir=job_dir)
    verified_inputs = _verified_inputs(job_dir, manifest)
    full_document_ir_by_task = _job_document_ir_by_task(
        job_dir,
        manifest,
    )
    input_by_id = {
        _positive_int(row.get("document_id")): row
        for row in verified_inputs
    }
    run_report = (
        _read_json(job_dir / "run_report.json")
        if (job_dir / "run_report.json").exists()
        else {}
    )
    raw_run_errors = run_report.get("errors", [])
    run_errors = (
        [dict(value) for value in raw_run_errors if isinstance(value, Mapping)]
        if isinstance(raw_run_errors, list)
        else []
    )
    attempted_failures = {
        _positive_int(value.get("document_id")): value
        for value in run_errors
        if value.get("document_id") is not None
    }
    output_path = job_dir / "output.jsonl"
    if not output_path.exists():
        failed = len(attempted_failures)
        awaiting = max(0, len(manifest["items"]) - failed)
        report = {
            "status": "partial" if failed else "awaiting_executor",
            "job_id": manifest["job_id"],
            "expected": len(manifest["items"]),
            "outputs": 0,
            "valid": 0,
            "no_event": 0,
            "quarantined": 0,
            "reused": 0,
            "failed": failed,
            "awaiting": awaiting,
            "errors": run_errors,
        }
        _write_json(job_dir / "import_report.json", report)
        return report

    outputs = _read_jsonl(
        output_path,
        max_rows=len(manifest["items"]),
    )
    output_by_id: dict[int, dict[str, object]] = {}
    duplicate_ids: set[int] = set()
    for row in outputs:
        document_id = _positive_int(row.get("document_id"))
        if document_id in output_by_id:
            duplicate_ids.add(document_id)
        output_by_id[document_id] = row
    if duplicate_ids:
        raise SemanticExchangeError(
            "semantic_job_duplicate_output",
            detail=",".join(str(value) for value in sorted(duplicate_ids)),
        )

    profile, _ = _load_profile(root, str(manifest["profile_id"]))
    taxonomy = EventTaxonomy.load(
        _rooted_path(root, str(profile["taxonomy_path"]))
    )
    store = IntelligenceStore(root / "data" / "shared" / "intelligence")
    raw_store = LocalBlobStore(
        root / "data" / "shared" / "intelligence" / "artifacts",
        key_prefix="announcements",
    )
    canonicalizer = SemanticEventCanonicalizer(store, taxonomy)
    valid = 0
    no_event = 0
    quarantined = 0
    reused = 0
    repairs_activated = 0
    failed = 0
    awaiting = 0
    errors: list[dict[str, object]] = []
    expected_ids: set[int] = set()
    for item_value in manifest["items"]:
        item = _mapping(item_value, "semantic_job_item_invalid")
        document_id = _positive_int(item.get("document_id"))
        expected_ids.add(document_id)
        envelope = output_by_id.get(document_id)
        if envelope is None:
            run_error = attempted_failures.get(document_id)
            if run_error is not None:
                failed += 1
                errors.append(dict(run_error))
                continue
            awaiting += 1
            continue
        try:
            executor = _executor_identity(
                envelope.get("executor"),
                trusted_executor=run_report.get("executor"),
            )
            _verify_output_identity(envelope, item)
            result_payload = envelope.get("result")
            if not isinstance(result_payload, Mapping):
                raise SemanticExchangeError(
                    "semantic_job_result_invalid"
                )
            snapshot = store.semantic_document_snapshot(document_id)
            input_payload = _mapping(
                input_by_id[document_id].get("payload"),
                "semantic_job_payload_invalid",
            )
            artifact = _mapping(
                snapshot.get("artifact"),
                "semantic_job_artifact_missing",
            )
            if str(artifact.get("content_hash") or "") != str(
                item["artifact_hash"]
            ):
                raise SemanticExchangeError("semantic_job_artifact_stale")
            raw_result = _canonical_json(result_payload)
            output_hash = _text_hash(raw_result)
            output_uri = raw_store.put_if_absent(
                (
                    "announcements/semantic-exchange/"
                    f"{output_hash[:2]}/{output_hash}.json"
                ),
                raw_result.encode("utf-8"),
                "application/json",
            )
            claim = store.claim_semantic_run(
                document_id=document_id,
                artifact_hash=str(item["artifact_hash"]),
                provider=executor["provider"],
                model=executor["model"],
                prompt_version=str(manifest["prompt_version"]),
                schema_version=str(manifest["schema_version"]),
                taxonomy_version=str(manifest["taxonomy_version"]),
                parser_version=str(item["parser_version"]),
                input_hash=str(item["input_hash"]),
            )
            run_id = str(claim["run_id"])
            if not bool(claim["claimed"]):
                existing_status = str(claim.get("status") or "")
                if existing_status == "no_event":
                    repairs_activated += _activate_repair_context(
                        store,
                        input_payload=input_payload,
                        document_id=document_id,
                        replacement_run_id=run_id,
                    )
                    reused += 1
                    no_event += 1
                    continue
                if existing_status == "failed_terminal":
                    quarantined += 1
                    errors.append(
                        {
                            "document_id": document_id,
                            "error": str(
                                claim.get("error")
                                or "semantic_existing_terminal_failure"
                            ),
                        }
                    )
                    continue
                if existing_status == "succeeded":
                    if _run_has_only_canonical_candidates(
                        store,
                        run_id,
                    ):
                        repairs_activated += _activate_repair_context(
                            store,
                            input_payload=input_payload,
                            document_id=document_id,
                            replacement_run_id=run_id,
                        )
                        reused += 1
                        valid += 1
                    else:
                        quarantined += 1
                        errors.append(
                            {
                                "document_id": document_id,
                                "error": (
                                    "semantic_existing_noncanonical_run"
                                ),
                            }
                        )
                    continue
                if existing_status != "succeeded":
                    awaiting += 1
                    errors.append(
                        {
                            "document_id": document_id,
                            "error": "semantic_existing_run_in_progress",
                        }
                    )
                    continue
            chunks = {
                str(row["chunk_id"]): {
                    "page_number": int(row["page_number"]),
                    "text": str(row["text"]),
                }
                for row in _sequence(
                    input_payload.get("chunks"),
                    "semantic_job_chunks_invalid",
                )
                if isinstance(row, Mapping)
            }
            full_document_ir = full_document_ir_by_task.get(
                str(input_by_id[document_id].get("semantic_task_id") or "")
            )
            if full_document_ir is not None:
                try:
                    for node_id, node in ir_nodes_by_id(
                        full_document_ir
                    ).items():
                        text = str(
                            node.get("text") or node.get("raw_value") or ""
                        )
                        if text and node_id not in chunks:
                            chunks[node_id] = {
                                "page_number": int(
                                    node.get("page_number") or 0
                                ),
                                "text": text,
                            }
                except DocumentIRPreflightError as exc:
                    raise SemanticExchangeError(
                        "semantic_document_ir_invalid",
                        detail=exc.code,
                    ) from exc
            try:
                parsed = parse_lite_semantic_document_result(
                    dict(result_payload),
                    taxonomy,
                    chunks,
                )
                if parsed.document_id != document_id:
                    raise SemanticContractError(
                        "semantic_document_id_mismatch"
                    )
            except SemanticContractError as exc:
                if bool(claim["claimed"]):
                    store.finish_semantic_run(
                        run_id,
                        status="failed_terminal",
                        output_hash=output_hash,
                        output_uri=output_uri,
                        error=exc.code,
                    )
                quarantined += 1
                errors.append(
                    {
                        "document_id": document_id,
                        "error": exc.code,
                        "detail": exc.detail,
                    }
                )
                continue
            status = "no_event" if not parsed.events else "succeeded"
            usage = _mapping(
                envelope.get("usage") or {},
                "semantic_job_usage_invalid",
            )
            if status == "no_event" and _requires_no_event_review(input_payload):
                review_event_type = _no_event_review_signal(
                    str(input_payload.get("document", {}).get("title") or ""),
                    chunks,
                    taxonomy_requirements=(
                        input_payload.get("taxonomy_requirements")
                        or input_payload.get("mention_templates")
                    ),
                    no_event_reason=str(parsed.no_event_reason or ""),
                    review_all_title_categories=str(
                        input_by_id[document_id].get("profile_id") or ""
                    ).startswith("a-share-announcement-mentions-"),
                )
                if review_event_type is not None:
                    # High-signal event type with a no_event outcome: route to
                    # an explicit review terminal instead of silent acceptance.
                    if bool(claim["claimed"]):
                        store.finish_semantic_run(
                            run_id,
                            status="failed_terminal",
                            output_hash=output_hash,
                            output_uri=output_uri,
                            input_tokens=_optional_non_negative_int(
                                usage.get("input_tokens")
                            ),
                            output_tokens=_optional_non_negative_int(
                                usage.get("output_tokens")
                            ),
                            latency_ms=_optional_non_negative_int(
                                usage.get("latency_ms")
                            ),
                            cost_microunits=_optional_non_negative_int(
                                usage.get("cost_microunits")
                            ),
                            error=f"no_event_review_required:{review_event_type}",
                        )
                    quarantined += 1
                    errors.append(
                        {
                            "document_id": document_id,
                            "error": "no_event_review_required",
                            "detail": review_event_type,
                        }
                    )
                    continue
            if status == "succeeded":
                referenced_chunk_ids = {
                    evidence.chunk_id
                    for evidence in parsed.evidence
                }
                try:
                    store.ensure_semantic_evidence_chunks(
                        document_id=document_id,
                        artifact_id=str(artifact["artifact_id"]),
                        parser_version=str(item["parser_version"]),
                        chunks=_materializable_evidence_chunks(
                            input_payload,
                            full_document_ir=full_document_ir,
                            referenced_chunk_ids=referenced_chunk_ids,
                        ),
                    )
                except (TypeError, ValueError) as exc:
                    if bool(claim["claimed"]):
                        store.finish_semantic_run(
                            run_id,
                            status="failed_terminal",
                            output_hash=output_hash,
                            output_uri=output_uri,
                            error="semantic_evidence_materialization_failed",
                        )
                    raise SemanticExchangeError(
                        "semantic_evidence_materialization_failed",
                        detail=str(exc),
                    ) from exc
            if bool(claim["claimed"]):
                store.finish_semantic_run(
                    run_id,
                    status=status,
                    output_hash=output_hash,
                    output_uri=output_uri,
                    input_tokens=_optional_non_negative_int(
                        usage.get("input_tokens")
                    ),
                    output_tokens=_optional_non_negative_int(
                        usage.get("output_tokens")
                    ),
                    latency_ms=_optional_non_negative_int(
                        usage.get("latency_ms")
                    ),
                    cost_microunits=_optional_non_negative_int(
                        usage.get("cost_microunits")
                    ),
                    error="",
                )
            if status == "no_event":
                repairs_activated += _activate_repair_context(
                    store,
                    input_payload=input_payload,
                    document_id=document_id,
                    replacement_run_id=run_id,
                )
                no_event += 1
                continue
            outcomes = canonicalizer.canonicalize(
                run_id,
                parsed,
                evidence_chunks=chunks,
            )
            if outcomes and all(
                outcome.status == "canonical" for outcome in outcomes
            ):
                repairs_activated += _activate_repair_context(
                    store,
                    input_payload=input_payload,
                    document_id=document_id,
                    replacement_run_id=run_id,
                )
                valid += 1
            else:
                quarantined += 1
                errors.append(
                    {
                        "document_id": document_id,
                        "error": "semantic_candidate_quarantined",
                        "reason_codes": sorted(
                            {
                                code
                                for outcome in outcomes
                                for code in outcome.reason_codes
                            }
                        ),
                    }
                )
        except (KeyError, TypeError, ValueError, SemanticExchangeError) as exc:
            quarantined += 1
            errors.append(
                {
                    "document_id": document_id,
                    "error": getattr(
                        exc,
                        "code",
                        "semantic_job_output_invalid",
                    ),
                    "detail": str(getattr(exc, "detail", "") or ""),
                }
            )

    unexpected = sorted(set(output_by_id).difference(expected_ids))
    for document_id in unexpected:
        quarantined += 1
        errors.append(
            {
                "document_id": document_id,
                "error": "semantic_job_output_unexpected",
            }
        )
    status = (
        "imported"
        if awaiting == 0 and quarantined == 0 and failed == 0
        else "partial"
    )
    newly_persisted = max(0, valid + no_event - reused)
    report = {
        "status": status,
        "job_id": manifest["job_id"],
        "expected": len(manifest["items"]),
        "outputs": len(outputs),
        "valid": valid,
        "no_event": no_event,
        "quarantined": quarantined,
        "reused": reused,
        "repairs_activated": repairs_activated,
        "newly_persisted": newly_persisted,
        "failed": failed,
        "awaiting": awaiting,
        "errors": errors,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    if refresh_features:
        report["feature_refresh"] = _refresh_feature_overlay(
            root,
            imported_count=newly_persisted,
        )
    _write_json(job_dir / "import_report.json", report)
    return report


def _activate_repair_context(
    store: IntelligenceStore,
    *,
    input_payload: Mapping[str, object],
    document_id: int,
    replacement_run_id: str,
) -> int:
    context_value = input_payload.get("repair_context")
    if context_value is None:
        return 0
    context = _mapping(context_value, "semantic_repair_context_invalid")
    if context.get("contract_version") != "semantic-repair-v1":
        raise SemanticExchangeError("semantic_repair_context_invalid")
    prior_values = _sequence(
        context.get("superseded_runs"),
        "semantic_repair_context_invalid",
    )
    superseded_run_ids = []
    for value in prior_values:
        prior = _mapping(value, "semantic_repair_context_invalid")
        run_id = str(prior.get("run_id") or "").strip()
        if not run_id:
            raise SemanticExchangeError("semantic_repair_context_invalid")
        superseded_run_ids.append(run_id)
    result = store.activate_semantic_repair(
        repair_id=str(context.get("repair_id") or ""),
        document_id=document_id,
        replacement_run_id=replacement_run_id,
        superseded_run_ids=superseded_run_ids,
        reason=str(context.get("reason") or ""),
    )
    if int(result.get("conflicted") or 0):
        raise SemanticExchangeError(
            "semantic_repair_superseded",
            detail=str(result.get("repair_id") or ""),
        )
    return int(result["activated"])


def _run_has_only_canonical_candidates(
    store: IntelligenceStore,
    run_id: str,
) -> bool:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT
              SUM(CASE WHEN validation_status='canonical' THEN 1 ELSE 0 END)
                AS canonical_count,
              SUM(CASE WHEN validation_status<>'canonical' THEN 1 ELSE 0 END)
                AS noncanonical_count
            FROM event_candidates
            WHERE run_id=?
            """,
            (str(run_id),),
        ).fetchone()
    return bool(
        row is not None
        and int(row["canonical_count"] or 0) > 0
        and int(row["noncanonical_count"] or 0) == 0
    )


def run_job(
    repo_root: str | Path,
    job_path: str | Path,
    *,
    executor_config: str | Path | None = None,
    provider: SemanticExtractionProvider | None = None,
    _retry_import_errors: bool = True,
) -> dict[str, object]:
    """Execute missing rows and checkpoint each successful output atomically."""

    root = Path(repo_root).resolve()
    job_dir = _resolve_job_dir(root, job_path)
    manifest = _read_json(job_dir / "job.json")
    _verify_manifest(manifest, job_dir=job_dir)
    inputs = _verified_inputs(job_dir, manifest)
    full_document_ir_by_task = _job_document_ir_by_task(
        job_dir,
        manifest,
    )
    schema = _read_json(job_dir / "schema.json")
    taxonomy = EventTaxonomy.load(job_dir / "taxonomy.json")
    semantic_provider = provider or _load_executor(
        job_dir,
        executor_config=executor_config,
    )
    bound_binding: ExecutorBinding | None = None
    if manifest.get("execution_contract_version") is not None:
        try:
            bound_binding = ExecutorBinding.from_mapping(
                _mapping(
                    manifest.get("executor_binding"),
                    "semantic_executor_binding_invalid",
                )
            )
            verify_executor_identity(
                bound_binding,
                semantic_provider.identity,
            )
        except SemanticExecutionContractError as exc:
            raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
    validation_store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    output_path = job_dir / "output.jsonl"
    quarantine_path = job_dir / "quarantine.jsonl"
    existing = (
        _read_jsonl(
            output_path,
            max_rows=len(manifest["items"]),
        )
        if output_path.exists()
        else []
    )
    prior_import = (
        _read_json(job_dir / "import_report.json")
        if (job_dir / "import_report.json").exists()
        else {}
    )
    retry_ids = {
        _positive_int(row.get("document_id"))
        for row in _sequence(
            prior_import.get("errors", []),
            "semantic_import_report_invalid",
        )
        if isinstance(row, Mapping)
        and row.get("document_id") is not None
        and (
            _retry_import_errors
            or row.get("retryable") is True
        )
    }
    if retry_ids:
        existing = [
            row
            for row in existing
            if _positive_int(row.get("document_id")) not in retry_ids
        ]
    output_by_id = {
        _positive_int(row.get("document_id")): row for row in existing
    }
    terminal_quarantine_ids = {
        _positive_int(row.get("document_id"))
        for row in (
            _read_jsonl(
                quarantine_path,
                max_rows=len(manifest["items"]),
            )
            if quarantine_path.exists()
            else []
        )
        if row.get("document_id") is not None
    }
    completed = 0
    reused = 0
    validation_repairs = 0
    validation_repair_failures = 0
    deterministic_optional_fact_prunes = 0
    compiled_mentions_accepted = 0
    compiled_mentions_rejected = 0
    compiled_mention_items_dropped = 0
    provider_responses = []
    errors: list[dict[str, object]] = []
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    for input_row in inputs:
        document_id = _positive_int(input_row.get("document_id"))
        if document_id in output_by_id:
            reused += 1
            continue
        if document_id in terminal_quarantine_ids:
            reused += 1
            continue
        bundle = SemanticInputBundle(
            document_id=document_id,
            artifact_hash=str(input_row["artifact_hash"]),
            parser_version=str(input_row["parser_version"]),
            prompt_version=str(input_row["prompt_version"]),
            schema_version=str(input_row["schema_version"]),
            taxonomy_version=str(input_row["taxonomy_version"]),
            payload=_mapping(
                input_row.get("payload"),
                "semantic_job_payload_invalid",
            ),
            input_token_estimate=max(
                1,
                (len(_canonical_json(input_row.get("payload"))) + 3) // 4,
            ),
        )
        responses = []
        bound_execution_id = str(input_row.get("execution_job_id") or "")
        if bound_binding is not None:
            validation_store.transition_semantic_execution_job(
                bound_execution_id,
                to_status="running",
            )
        try:
            response = semantic_provider.extract(bundle, response_schema=schema)
            responses.append(response)
            provider_responses.append(response)
            provider_result = response.parsed_output
            if bound_binding is not None:
                validation_store.transition_semantic_execution_job(
                    bound_execution_id,
                    to_status="produced",
                    output_hash=response.output_hash,
                )
                validation_store.transition_semantic_execution_job(
                    bound_execution_id,
                    to_status="validating",
                    output_hash=response.output_hash,
                )
            validated_result, pruned, compilation = _validate_provider_result(
                provider_result,
                taxonomy=taxonomy,
                bundle=bundle,
                store=validation_store,
                full_document_ir=full_document_ir_by_task.get(
                    str(input_row.get("semantic_task_id") or "")
                ),
            )
            missing_event_types = _missing_routed_event_types(
                validated_result,
                bundle,
            )
            if missing_event_types:
                raise SemanticContractError(
                    "semantic_candidate_family_unreviewed",
                    detail=",".join(missing_event_types),
                )
            deterministic_optional_fact_prunes += pruned
            compiled_mentions_accepted += int(compilation.get("accepted", 0))
            compiled_mentions_rejected += int(compilation.get("rejected", 0))
            compiled_mention_items_dropped += int(compilation.get("dropped", 0))
        except SemanticContractError as exc:
            validation_repairs += 1
            if bound_binding is not None:
                validation_store.transition_semantic_execution_job(
                    bound_execution_id,
                    to_status="retrying_event",
                    error=exc.code,
                )
            repair_bundle = _grounding_repair_bundle(
                bundle,
                previous_result=provider_result,
                error=exc,
            )
            try:
                response = semantic_provider.extract(
                    repair_bundle,
                    response_schema=schema,
                )
                responses.append(response)
                provider_responses.append(response)
                if bound_binding is not None:
                    validation_store.transition_semantic_execution_job(
                        bound_execution_id,
                        to_status="validating",
                        output_hash=response.output_hash,
                    )
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
                validated_result, pruned, compilation = _validate_provider_result(
                    provider_result,
                    taxonomy=taxonomy,
                    bundle=validation_bundle,
                    store=validation_store,
                    full_document_ir=full_document_ir_by_task.get(
                        str(input_row.get("semantic_task_id") or "")
                    ),
                )
                missing_event_types = _missing_routed_event_types(
                    validated_result,
                    bundle,
                )
                if missing_event_types:
                    raise SemanticContractError(
                        "semantic_candidate_family_unreviewed",
                        detail=",".join(missing_event_types),
                    )
                deterministic_optional_fact_prunes += pruned
                compiled_mentions_accepted += int(compilation.get("accepted", 0))
                compiled_mentions_rejected += int(compilation.get("rejected", 0))
                compiled_mention_items_dropped += int(compilation.get("dropped", 0))
            except SemanticContractError as repair_exc:
                deterministic_no_event = (
                    _revision_rejection_can_be_no_event(repair_exc)
                    or _context_repair_can_be_no_event(repair_exc, bundle)
                )
                if deterministic_no_event:
                    provider_result = {
                        "document_id": document_id,
                        "schema_version": bundle.schema_version,
                        "mentions": [],
                        "no_event_reason": (
                            "deterministic: no current event survived validation"
                        ),
                    }
                    validated_result, pruned, compilation = _validate_provider_result(
                        provider_result,
                        taxonomy=taxonomy,
                        bundle=repair_bundle,
                        store=validation_store,
                        full_document_ir=full_document_ir_by_task.get(
                            str(input_row.get("semantic_task_id") or "")
                        ),
                    )
                    deterministic_optional_fact_prunes += pruned
                    compiled_mentions_accepted += int(
                        compilation.get("accepted", 0)
                    )
                    compiled_mentions_rejected += int(
                        compilation.get("rejected", 0)
                    )
                    compiled_mention_items_dropped += int(
                        compilation.get("dropped", 0)
                    )
                else:
                    validation_repair_failures += 1
                    if bound_binding is not None:
                        validation_store.transition_semantic_execution_job(
                            bound_execution_id,
                            to_status="quarantined",
                            output_hash=response.output_hash,
                            error=repair_exc.code,
                        )
                    if not bundle.payload.get("repair_context"):
                        _record_terminal_validation_failure(
                            validation_store,
                            bundle=bundle,
                            input_hash=str(input_row["input_hash"]),
                            response=response,
                            responses=responses,
                            error=repair_exc,
                        )
                    _write_validation_quarantine(
                        quarantine_path,
                        input_row=input_row,
                        response=response,
                        responses=responses,
                        error=repair_exc,
                    )
                    errors.append(
                        {
                            "document_id": document_id,
                            "error": repair_exc.code,
                            "detail": repair_exc.detail,
                            "retryable": False,
                            "terminal": True,
                            "validation_repair_attempted": True,
                        }
                    )
                    continue
            except Exception as repair_exc:
                validation_repair_failures += 1
                retryable = bool(getattr(repair_exc, "retryable", False))
                if bound_binding is not None:
                    validation_store.transition_semantic_execution_job(
                        bound_execution_id,
                        to_status=("retry_wait" if retryable else "quarantined"),
                        error=str(
                            getattr(
                                repair_exc,
                                "code",
                                type(repair_exc).__name__,
                            )
                        ),
                    )
                errors.append(
                    {
                        "document_id": document_id,
                        "error": str(
                            getattr(
                                repair_exc,
                                "code",
                                type(repair_exc).__name__,
                            )
                        ),
                        "retryable": bool(
                            retryable
                        ),
                        "terminal": False,
                        "validation_repair_attempted": True,
                    }
                )
                continue
        except Exception as exc:
            if bound_binding is not None:
                validation_store.transition_semantic_execution_job(
                    bound_execution_id,
                    to_status=(
                        "retry_wait"
                        if bool(getattr(exc, "retryable", False))
                        else "abandoned"
                    ),
                    error=str(getattr(exc, "code", type(exc).__name__)),
                )
            errors.append(
                {
                    "document_id": document_id,
                    "error": str(
                        getattr(exc, "code", type(exc).__name__)
                    ),
                    "retryable": bool(
                        getattr(exc, "retryable", False)
                    ),
                    "terminal": False,
                }
            )
            continue
        if bound_binding is not None:
            validation_store.transition_semantic_execution_job(
                bound_execution_id,
                to_status="accepted",
                output_hash=response.output_hash,
            )
        envelope = {
            "contract_version": OUTPUT_CONTRACT_VERSION,
            "document_id": document_id,
            "artifact_hash": str(input_row["artifact_hash"]),
            "input_hash": str(input_row["input_hash"]),
            "executor": {
                "kind": "provider-adapter",
                "provider": response.identity.provider,
                "model": response.identity.model,
                "identity_trust": "runner-configured",
                "client_version": response.identity.client_version,
                "endpoint_host": response.identity.endpoint_host,
            },
            "usage": {
                "input_tokens": _sum_optional_usage(
                    item.input_tokens for item in responses
                ),
                "output_tokens": _sum_optional_usage(
                    item.output_tokens for item in responses
                ),
                "total_tokens": _sum_optional_usage(
                    item.total_tokens for item in responses
                ),
                "latency_ms": sum(item.latency_ms for item in responses),
                "cost_microunits": None,
                "request_id": response.request_id,
                "request_count": len(responses),
            },
            "result": validated_result,
        }
        if bound_binding is not None:
            envelope.update(
                {
                    "semantic_task_id": str(input_row["semantic_task_id"]),
                    "execution_job_id": bound_execution_id,
                    "binding_id": bound_binding.binding_id,
                }
            )
        if bundle.schema_version == MENTION_SCHEMA_VERSION:
            _write_mention_source_output(
                job_dir / "mention_output.jsonl",
                input_row=input_row,
                response=response,
                responses=responses,
                result=provider_result,
                compilation=compilation,
            )
        output_by_id[document_id] = envelope
        _write_jsonl(
            output_path,
            [
                output_by_id[key]
                for key in sorted(output_by_id)
            ],
        )
        completed += 1
    expected = len(inputs)
    status = (
        "complete"
        if len(output_by_id) >= expected
        else "partial"
    )
    report = {
        "status": status,
        "job_id": manifest["job_id"],
        "executor": {
            "executor_mode": (
                bound_binding.executor_mode if bound_binding else "legacy"
            ),
            "provider": semantic_provider.identity.provider,
            "model": semantic_provider.identity.model,
            "client_version": semantic_provider.identity.client_version,
            "binding_id": (
                bound_binding.binding_id if bound_binding else ""
            ),
        },
        "expected": expected,
        "completed": completed,
        "reused": reused,
        "failed": len(errors),
        "validation_repairs": validation_repairs,
        "validation_repair_failures": validation_repair_failures,
        "deterministic_optional_fact_prunes": (
            deterministic_optional_fact_prunes
        ),
        "mention_compilation": {
            "accepted": compiled_mentions_accepted,
            "rejected": compiled_mentions_rejected,
            "dropped_items": compiled_mention_items_dropped,
        },
        "usage": {
            "input_tokens": _sum_optional_usage(
                item.input_tokens for item in provider_responses
            ),
            "output_tokens": _sum_optional_usage(
                item.output_tokens for item in provider_responses
            ),
            "total_tokens": _sum_optional_usage(
                item.total_tokens for item in provider_responses
            ),
            "latency_ms": sum(
                item.latency_ms for item in provider_responses
            ),
            "request_count": len(provider_responses),
        },
        "remaining": max(
            0,
            expected
            - len(output_by_id)
            - len(
                {
                    _positive_int(error.get("document_id"))
                    for error in errors
                    if error.get("document_id") is not None
                }
            ),
        ),
        "errors": errors,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": int((time.monotonic() - started) * 1_000),
    }
    _write_json(job_dir / "run_report.json", report)
    return report


def _write_validation_quarantine(
    path: Path,
    *,
    input_row: Mapping[str, object],
    response,
    responses: Sequence[object],
    error: SemanticContractError,
) -> None:
    """Persist rejected provider content outside the importable output path."""

    existing = _read_jsonl(path) if path.exists() else []
    document_id = _positive_int(input_row.get("document_id"))
    by_document = {
        _positive_int(row.get("document_id")): row
        for row in existing
        if isinstance(row, Mapping) and row.get("document_id") is not None
    }
    by_document[document_id] = {
        "contract_version": "semantic-extraction-quarantine-v1",
        "document_id": document_id,
        "artifact_hash": str(input_row["artifact_hash"]),
        "input_hash": str(input_row["input_hash"]),
        "executor": {
            "kind": "provider-adapter",
            "provider": response.identity.provider,
            "model": response.identity.model,
            "identity_trust": "runner-configured",
            "client_version": response.identity.client_version,
            "endpoint_host": response.identity.endpoint_host,
        },
        "usage": {
            "input_tokens": _sum_optional_usage(
                item.input_tokens for item in responses
            ),
            "output_tokens": _sum_optional_usage(
                item.output_tokens for item in responses
            ),
            "total_tokens": _sum_optional_usage(
                item.total_tokens for item in responses
            ),
            "latency_ms": sum(item.latency_ms for item in responses),
            "request_count": len(responses),
        },
        "validation_error": {
            "code": error.code,
            "detail": error.detail,
        },
        "result": response.parsed_output,
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_jsonl(
        path,
        [by_document[key] for key in sorted(by_document)],
    )


def _write_mention_source_output(
    path: Path,
    *,
    input_row: Mapping[str, object],
    response,
    responses: Sequence[object],
    result: Mapping[str, object],
    compilation: Mapping[str, object],
) -> None:
    existing = _read_jsonl(path) if path.exists() else []
    document_id = _positive_int(input_row.get("document_id"))
    by_document = {
        _positive_int(row.get("document_id")): row
        for row in existing
        if isinstance(row, Mapping) and row.get("document_id") is not None
    }
    by_document[document_id] = {
        "contract_version": "semantic-mention-source-v1",
        "document_id": document_id,
        "artifact_hash": str(input_row["artifact_hash"]),
        "input_hash": str(input_row["input_hash"]),
        "executor": {
            "provider": response.identity.provider,
            "model": response.identity.model,
            "endpoint_host": response.identity.endpoint_host,
        },
        "compilation": dict(compilation),
        "provider_attempts": [
            {
                "request_id": str(item.request_id or ""),
                "output_hash": str(item.output_hash or ""),
                "result": deepcopy(item.parsed_output),
            }
            for item in responses
        ],
        "result": deepcopy(result),
    }
    _write_jsonl(path, [by_document[key] for key in sorted(by_document)])


def _validate_provider_result(
    result: Mapping[str, object],
    *,
    taxonomy: EventTaxonomy,
    bundle: SemanticInputBundle,
    store: IntelligenceStore,
    full_document_ir: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], int, dict[str, object]]:
    chunks = {
        str(row["chunk_id"]): {
            "page_number": int(row["page_number"]),
            "text": str(row["text"]),
        }
        for row in _sequence(
            bundle.payload.get("chunks"),
            "semantic_job_chunks_invalid",
        )
        if isinstance(row, Mapping)
    }
    packet_document_ir = (
        bundle.payload.get("document_ir")
        if isinstance(bundle.payload.get("document_ir"), Mapping)
        else None
    )
    try:
        visible_chunk_ids = _packet_visible_evidence_ids(bundle.payload)
    except DocumentIRPreflightError as exc:
        raise SemanticContractError(
            "semantic_document_ir_invalid",
            detail=exc.code,
        ) from exc
    document_ir = full_document_ir or packet_document_ir
    if isinstance(document_ir, Mapping):
        try:
            for node_id, node in ir_nodes_by_id(document_ir).items():
                text = str(node.get("text") or node.get("raw_value") or "")
                if text and node_id not in chunks:
                    chunks[node_id] = {
                        "page_number": int(node.get("page_number") or 0),
                        "text": text,
                    }
        except DocumentIRPreflightError as exc:
            raise SemanticContractError(
                "semantic_document_ir_invalid",
                detail=exc.code,
            ) from exc
    if bundle.schema_version == MENTION_SCHEMA_VERSION:
        try:
            mentions = parse_mention_document_result(result)
        except MentionContractError as exc:
            raise SemanticContractError(exc.code, detail=exc.detail) from exc
        if full_document_ir is not None:
            mentions = _filter_mentions_to_packet(
                mentions,
                visible_chunk_ids,
            )
        document = _mapping(
            bundle.payload.get("document"),
            "semantic_job_document_invalid",
        )
        if not mentions.mentions and _requires_no_event_review(bundle.payload):
            review_event_type = _no_event_review_signal(
                str(document.get("title") or ""),
                chunks,
                taxonomy_requirements=_sequence(
                    bundle.payload.get("taxonomy_requirements", []),
                    "semantic_job_taxonomy_requirements_invalid",
                ),
                no_event_reason=str(mentions.no_event_reason or ""),
                review_all_title_categories=True,
            )
            if review_event_type is not None:
                raise SemanticContractError(
                    "no_event_review_required",
                    detail=review_event_type,
                )
        compilation = compile_mentions(
            mentions,
            taxonomy=taxonomy,
            chunks=chunks,
            document=document,
            entity_whitelist=_sequence(
                bundle.payload.get("entity_whitelist"),
                "semantic_job_entity_whitelist_invalid",
            ),
            taxonomy_candidates=_sequence(
                bundle.payload.get("taxonomy_candidates"),
                "semantic_job_taxonomy_candidates_invalid",
            ),
            document_ir=document_ir,
        )
        if mentions.mentions and not compilation.accepted_mentions:
            detail = ";".join(
                f"{item.mention_id}:{','.join(item.reason_codes)}"
                for item in compilation.rejected_mentions
            )
            raise SemanticContractError(
                "semantic_mentions_all_rejected",
                detail=detail,
            )
        normalized = compilation.result
        pruned = compilation.dropped_items
        compilation_report = {
            "accepted": compilation.accepted_mentions,
            "rejected": len(compilation.rejected_mentions),
            "dropped": compilation.dropped_items,
            "rejected_mentions": [
                {
                    "mention_id": item.mention_id,
                    "reason_codes": list(item.reason_codes),
                }
                for item in compilation.rejected_mentions
            ],
        }
    else:
        normalized, pruned = _prune_ungrounded_optional_facts(
            result,
            taxonomy=taxonomy,
            chunks={chunk_id: str(chunk["text"]) for chunk_id, chunk in chunks.items()},
        )
        compilation_report = {
            "accepted": 0,
            "rejected": 0,
            "dropped": 0,
            "rejected_mentions": [],
        }
    missing_current_transition = _context_events_missing_current_transition(
        normalized,
        bundle,
    )
    if missing_current_transition:
        raise SemanticContractError(
            "semantic_context_current_transition_missing",
            detail=",".join(missing_current_transition),
        )
    parsed = parse_lite_semantic_document_result(
        normalized,
        taxonomy,
        chunks,
    )
    if parsed.document_id != _positive_int(bundle.document_id):
        raise SemanticContractError("semantic_document_id_mismatch")
    document = _mapping(
        bundle.payload.get("document"),
        "semantic_job_document_invalid",
    )
    issuer_entity_id = str(document.get("ts_code") or "").strip()
    entity_whitelist: dict[str, frozenset[str]] = {}
    for raw in _sequence(
        bundle.payload.get("entity_whitelist"),
        "semantic_job_entity_whitelist_invalid",
    ):
        row = _mapping(raw, "semantic_job_entity_whitelist_invalid")
        entity_id = str(row.get("entity_id") or "").strip()
        roles = _sequence(
            row.get("allowed_roles"),
            "semantic_job_entity_whitelist_invalid",
        )
        if entity_id:
            entity_whitelist[entity_id] = frozenset(
                str(role).strip() for role in roles if str(role).strip()
            )
    failures: list[str] = []
    for event_index, event in enumerate(parsed.events):
        try:
            validate_candidate(
                event,
                parsed.evidence,
                chunks,
                taxonomy=taxonomy,
                issuer_entity_id=issuer_entity_id,
                entity_whitelist=entity_whitelist,
                document_metadata=document,
                prior_events=store.semantic_prior_events(
                    document_id=parsed.document_id,
                    event_type=event.event_type,
                ),
            )
        except CandidateValidationError as exc:
            failure = f"event[{event_index}]:{exc.code}"
            if exc.detail:
                failure = f"{failure}:{exc.detail}"
            failures.append(failure)
    if failures:
        raise SemanticContractError(
            "semantic_candidate_validation_failed",
            detail=";".join(failures),
        )
    return normalized, pruned, compilation_report


def _packet_visible_evidence_ids(
    payload: Mapping[str, object],
) -> frozenset[str]:
    visible = {
        str(row.get("chunk_id") or "")
        for row in payload.get("chunks", [])
        if isinstance(row, Mapping) and str(row.get("chunk_id") or "")
    }
    packet_document_ir = payload.get("document_ir")
    if isinstance(packet_document_ir, Mapping):
        visible.update(ir_nodes_by_id(packet_document_ir))
    return frozenset(visible)


def _filter_mentions_to_packet(document_result, visible_chunk_ids):
    def visible(evidence) -> bool:
        chunk_id = str(evidence.chunk_id)
        if chunk_id in visible_chunk_ids:
            return True
        return sum(
            candidate.startswith(f"{chunk_id}-")
            for candidate in visible_chunk_ids
        ) == 1

    mentions = []
    for mention in document_result.mentions:
        subjects = tuple(
            replace(
                subject,
                evidence=tuple(
                    item for item in subject.evidence if visible(item)
                ),
            )
            for subject in mention.subjects
        )
        facts = tuple(
            replace(
                fact,
                evidence=tuple(
                    item for item in fact.evidence if visible(item)
                ),
            )
            for fact in mention.facts
        )
        dates = tuple(
            replace(
                date_item,
                evidence=tuple(
                    item for item in date_item.evidence if visible(item)
                ),
            )
            for date_item in mention.dates
        )
        status = (
            replace(
                mention.status,
                evidence=tuple(
                    item
                    for item in mention.status.evidence
                    if visible(item)
                ),
            )
            if mention.status is not None
            else None
        )
        mentions.append(
            replace(
                mention,
                subjects=subjects,
                facts=facts,
                dates=dates,
                status=status,
            )
        )
    return replace(document_result, mentions=tuple(mentions))


def _prune_ungrounded_optional_facts(
    result: Mapping[str, object],
    *,
    taxonomy: EventTaxonomy,
    chunks: Mapping[str, str],
) -> tuple[dict[str, object], int]:
    """Drop only optional facts whose cited quote is absent from its chunk."""

    normalized = deepcopy(dict(result))
    raw_events = normalized.get("events")
    raw_evidence = normalized.get("evidence")
    if not isinstance(raw_events, list) or not isinstance(raw_evidence, list):
        return normalized, 0

    missing_evidence_ids: set[str] = set()
    for raw_item in raw_evidence:
        if not isinstance(raw_item, Mapping):
            continue
        evidence_id = str(raw_item.get("evidence_id") or "")
        chunk_id = str(raw_item.get("chunk_id") or "")
        quote = str(raw_item.get("quote") or "")
        chunk_text = chunks.get(chunk_id)
        if (
            evidence_id
            and chunk_text is not None
            and not _lite_quote_occurs(chunk_text, quote)
        ):
            missing_evidence_ids.add(evidence_id)
    pruned = 0
    pruned_evidence_ids: set[str] = set()
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue
        try:
            taxonomy_event = taxonomy.event(str(raw_event.get("event_type") or ""))
        except KeyError:
            continue
        lifecycle = str(raw_event.get("lifecycle") or "")
        requirements = taxonomy_event.requirements_for(lifecycle)
        protected_facts = set(requirements.facts)
        protected_facts.update(
            field.split(":", 1)[1]
            for field in taxonomy_event.dedupe_fields
            if field.startswith("fact:")
        )
        facts = raw_event.get("facts")
        if not isinstance(facts, list):
            continue
        retained = []
        for fact in facts:
            if not isinstance(fact, Mapping):
                retained.append(fact)
                continue
            name = str(fact.get("name") or "")
            evidence_ids = {
                str(value)
                for value in fact.get("evidence_ids", [])
                if str(value)
            } if isinstance(fact.get("evidence_ids"), list) else set()
            fact_spec = taxonomy_event.fact_specs.get(name)
            ambiguous_optional_numeric = bool(
                fact_spec is not None
                and fact_spec.value_type in {"number", "ratio"}
                and isinstance(fact.get("raw_value"), str)
                and numeric_raw_value_is_ambiguous(
                    str(fact["raw_value"]),
                    name,
                )
            )
            can_prune = (
                name in taxonomy_event.optional_facts
                and name not in protected_facts
                and (
                    bool(evidence_ids & missing_evidence_ids)
                    or ambiguous_optional_numeric
                )
            )
            if can_prune:
                pruned += 1
                pruned_evidence_ids.update(evidence_ids)
            else:
                retained.append(fact)
        raw_event["facts"] = retained

    referenced: set[str] = set()
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            continue
        for collection_name in (
            "subjects",
            "facts",
            "effective_dates",
            "conditions",
            "conflicts",
        ):
            collection = raw_event.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, Mapping):
                    continue
                evidence_ids = item.get("evidence_ids")
                if isinstance(evidence_ids, list):
                    referenced.update(str(value) for value in evidence_ids)
    normalized["evidence"] = [
        item
        for item in raw_evidence
        if not isinstance(item, Mapping)
        or str(item.get("evidence_id") or "") not in pruned_evidence_ids
        or str(item.get("evidence_id") or "") in referenced
    ]
    return normalized, pruned


def _lite_quote_occurs(text: str, quote: str) -> bool:
    if quote and quote in text:
        return True
    compact_text = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(text))
        if not character.isspace()
    )
    compact_quote = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(quote))
        if not character.isspace()
    )
    return bool(compact_quote and compact_quote in compact_text)


def _record_terminal_validation_failure(
    store: IntelligenceStore,
    *,
    bundle: SemanticInputBundle,
    input_hash: str,
    response,
    responses: Sequence[object],
    error: SemanticContractError,
) -> None:
    claim = store.claim_semantic_run(
        document_id=bundle.document_id,
        artifact_hash=bundle.artifact_hash,
        provider=response.identity.provider,
        model=response.identity.model,
        prompt_version=bundle.prompt_version,
        schema_version=bundle.schema_version,
        taxonomy_version=bundle.taxonomy_version,
        parser_version=bundle.parser_version,
        input_hash=input_hash,
    )
    if not bool(claim["claimed"]):
        return
    store.finish_semantic_run(
        str(claim["run_id"]),
        status="failed_terminal",
        input_tokens=_sum_optional_usage(
            item.input_tokens for item in responses
        ),
        output_tokens=_sum_optional_usage(
            item.output_tokens for item in responses
        ),
        latency_ms=sum(item.latency_ms for item in responses),
        error=f"preflight_terminal:{error.code}",
    )


def _grounding_repair_bundle(
    bundle: SemanticInputBundle,
    *,
    previous_result: Mapping[str, object],
    error: SemanticContractError,
) -> SemanticInputBundle:
    previous_json = _canonical_json(previous_result)
    instruction = (
        "Correct all validation errors below. Re-read the supplied "
        "chunks and return the complete JSON object again."
    )
    if error.code == "semantic_candidate_validation_failed":
        instruction += (
            " If an event cannot satisfy every taxonomy requirement, remove "
            "that incomplete event. If no complete event remains, return an "
            "empty events array with a grounded no_event_reason. For an "
            "external subject, cite a dedicated exact-name quote or remove "
            "the unsupported event."
        )
    elif error.code == "semantic_evidence_quote_missing":
        instruction += (
            " The failing_chunk below is authoritative. Copy any replacement "
            "quote by selecting one exact contiguous substring from its text, "
            "not by retyping it from memory. For table facts, cite the exact "
            "label chunk and the exact value chunk separately. If the claim "
            "cannot be supported verbatim, remove the claim or event."
        )
    elif error.code == "semantic_evidence_quote_ambiguous":
        instruction += (
            " The prior quote appears more than once in the failing_chunk. "
            "Copy adjacent exact source characters to expand the quote until "
            "it occurs exactly once; copy the entire failing chunk when that "
            "is the smallest reliable unique selection. Keep the fact "
            "raw_value unchanged. If no unique supporting quote exists, "
            "remove the optional claim or the incomplete event."
        )
    elif error.code in {
        "semantic_mentions_lossy_compilation",
        "semantic_mentions_all_rejected",
    }:
        instruction += (
            " payload.document.name identifies the issuer but may be a security "
            "abbreviation. Use the exact full legal company name found in a "
            "source chunk and cite the name-only substring; never output the "
            "abbreviation as the legal issuer name. For every non-issuer subject, "
            "the evidence quote "
            "must be exact-name-only, without an alias clause or description. "
            "Names listed in subject_roles belong only in subjects; names listed "
            "in fact_names belong only in facts. For an aggregate holder action, "
            "emit each explicitly named holder as a separate subject in the same "
            "mention when the aggregate facts apply to all of them. "
            "Copy fact raw values verbatim; never append a unit absent from "
            "the cited value cell. Remove any incomplete secondary mention "
            "instead of retaining it. Preserve all source parentheses in a "
            "required text fact."
        )
        if "mention_revision_uses_superseded_value" in str(error.detail):
            instruction += (
                " This is a correction or revision filing. Values under "
                "headings such as 原来披露, 原披露, 更正前, or 修改前 "
                "are superseded and must not be emitted. Locate the current "
                "section labelled 更正后, 更正说明, 修改后, or equivalent, "
                "and 只输出更正后的值 with uniquely locating verbatim "
                "quotes. If the filing changes no event fact, return no_event."
            )
    elif error.code == "no_event_review_required":
        instruction += (
            " The primary event filing has a deterministic review signal for "
            f"{error.detail or 'the routed event family'}. Re-check whether the "
            "current filing announces, completes, revises, cancels, or corrects "
            "that event. For a correction, emit only the exact corrected delta; "
            "it may be the sole fact in a revised mention. Return no_event only "
            "when the cited source truly contains no current transition or "
            "corrected event fact."
        )
        route_context = bundle.payload.get("route_context")
        if isinstance(route_context, Mapping) and "legal_current_event" in {
            str(value)
            for value in route_context.get("reason_codes", [])
            if str(value)
        }:
            instruction += (
                " The supporting document title carries an explicit "
                "current-action signal. A newly disclosed implementation "
                "step, application for cancellation or transfer, expected "
                "completion date, or implementation result is a current "
                "event and does not require a new program or transaction."
            )
    elif error.code == "semantic_candidate_family_unreviewed":
        instruction += (
            " Review every routed taxonomy candidate, especially: "
            f"{error.detail}. Emit each independently grounded current event "
            "that is present, even when another family was already emitted. "
            "Do not fabricate a missing family: omit it when the source only "
            "contains historical or background text for that candidate."
        )
    elif error.code == "semantic_context_current_transition_missing":
        instruction += (
            " This supporting legal or supplemental document only yielded a "
            "generic event label. Find a current transition such as approval, "
            "completion, cancellation, implementation, or a revised economic "
            "fact, and cite that transition separately. Historical transaction "
            "descriptions and cover-page titles are background. If the document "
            "does not disclose a new current transition, return no_event."
        )
    repair_context: dict[str, object] = {
        "attempt": 1,
        "repair_scope": "complete_event_candidate",
        "instruction": instruction,
        "validation_error": {
            "code": error.code,
            "detail": error.detail,
        },
    }
    if len(previous_json) <= 24_000:
        repair_context["previous_output"] = dict(previous_result)
    else:
        repair_context["previous_output_json_excerpt"] = previous_json[:24_000]
        repair_context["previous_output_truncated"] = True
    failing_chunk_id = str(error.detail or "").strip()
    if failing_chunk_id:
        chunks = bundle.payload.get("chunks")
        if isinstance(chunks, list):
            for raw_chunk in chunks:
                if not isinstance(raw_chunk, Mapping):
                    continue
                if str(raw_chunk.get("chunk_id") or "") != failing_chunk_id:
                    continue
                repair_context["failing_chunk"] = {
                    "chunk_id": failing_chunk_id,
                    "page_number": int(raw_chunk.get("page_number") or 0),
                    "text": str(raw_chunk.get("text") or ""),
                }
                break
    payload = dict(bundle.payload)
    payload["repair_context"] = repair_context
    return SemanticInputBundle(
        document_id=bundle.document_id,
        artifact_hash=bundle.artifact_hash,
        parser_version=bundle.parser_version,
        prompt_version=bundle.prompt_version,
        schema_version=bundle.schema_version,
        taxonomy_version=bundle.taxonomy_version,
        payload=payload,
        input_token_estimate=max(
            1,
            (len(_canonical_json(payload)) + 3) // 4,
        ),
    )


def _family_repair_targets(
    error: SemanticContractError,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value.strip()
                for value in str(error.detail or "").split(",")
                if value.strip()
            }
        )
    )


def _context_events_missing_current_transition(
    result: Mapping[str, object],
    bundle: SemanticInputBundle,
) -> tuple[str, ...]:
    route_context = bundle.payload.get("route_context")
    if not isinstance(route_context, Mapping):
        return ()
    if str(route_context.get("document_kind") or "") not in {
        "legal_opinion",
        "supplemental_report",
    }:
        return ()
    reason_codes = {
        str(value)
        for value in route_context.get("reason_codes", [])
        if str(value)
    }
    explicit_current_route = bool(
        reason_codes & {"legal_current_event", "revision_context_present"}
    )
    missing: set[str] = set()
    for event in result.get("events", []):
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("event_type") or "")
        lifecycle = str(event.get("lifecycle") or "")
        if explicit_current_route or lifecycle in {"revised", "cancelled"}:
            continue
        if event_type:
            missing.add(event_type)
    return tuple(sorted(missing))


def _merge_family_repair_result(
    previous_result: Mapping[str, object],
    repair_result: Mapping[str, object],
    *,
    target_event_types: Sequence[str],
) -> dict[str, object]:
    targets = {
        str(event_type).strip()
        for event_type in target_event_types
        if str(event_type).strip()
    }
    if not targets:
        raise SemanticContractError("semantic_family_repair_targets_missing")
    for field in ("document_id", "schema_version"):
        if previous_result.get(field) != repair_result.get(field):
            raise SemanticContractError(
                "semantic_family_repair_contract_mismatch",
                detail=field,
            )
    previous_mentions = previous_result.get("mentions")
    repair_mentions = repair_result.get("mentions")
    if not isinstance(previous_mentions, list) or not isinstance(
        repair_mentions,
        list,
    ):
        raise SemanticContractError(
            "semantic_family_repair_contract_mismatch",
            detail="mentions",
        )
    retained = [
        deepcopy(mention)
        for mention in previous_mentions
        if isinstance(mention, Mapping)
        and str(mention.get("event_type") or "") not in targets
    ]
    additions = [
        deepcopy(mention)
        for mention in repair_mentions
        if isinstance(mention, Mapping)
        and str(mention.get("event_type") or "") in targets
    ]
    used_ids = {
        str(mention.get("mention_id") or "")
        for mention in retained
        if str(mention.get("mention_id") or "")
    }
    for index, mention in enumerate(additions, start=1):
        mention_id = str(mention.get("mention_id") or "").strip()
        if not mention_id or mention_id in used_ids:
            base = mention_id or "mention"
            candidate = f"{base}-repair-{index}"
            suffix = index
            while candidate in used_ids:
                suffix += 1
                candidate = f"{base}-repair-{suffix}"
            mention["mention_id"] = candidate
            mention_id = candidate
        used_ids.add(mention_id)
    merged = deepcopy(dict(previous_result))
    merged["mentions"] = retained + additions
    merged["no_event_reason"] = None if merged["mentions"] else (
        repair_result.get("no_event_reason")
        or previous_result.get("no_event_reason")
    )
    return merged


def _missing_routed_event_types(
    result: Mapping[str, object],
    bundle: SemanticInputBundle,
) -> tuple[str, ...]:
    route_context = bundle.payload.get("route_context")
    if not isinstance(route_context, Mapping):
        return ()
    if str(route_context.get("extraction_purpose") or "") != "canonical_event":
        return ()
    candidates = tuple(
        sorted(
            {
                str(value).strip()
                for value in bundle.payload.get("taxonomy_candidates", [])
                if str(value).strip()
            }
        )
    )
    if not 2 <= len(candidates) <= 4:
        return ()
    observed = {
        str(event.get("event_type") or "")
        for event in result.get("events", [])
        if isinstance(event, Mapping)
    }
    return tuple(
        event_type
        for event_type in candidates
        if event_type not in observed
    )


def _sum_optional_usage(values) -> int | None:
    supplied = [int(value) for value in values if value is not None]
    return sum(supplied) if supplied else None


def run_daily(
    repo_root: str | Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    limit: int = DEFAULT_LIMIT,
    max_input_characters: int = DEFAULT_MAX_INPUT_CHARACTERS,
    executor_config: str | Path | None = None,
) -> dict[str, object]:
    """Import returned artifacts, prepare one batch, and optionally execute it."""

    root = Path(repo_root).resolve()
    jobs_root = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "extraction_jobs"
    )
    imported_existing: list[dict[str, object]] = []
    if jobs_root.exists():
        for path in sorted(jobs_root.glob("sj-*")):
            if not path.is_dir():
                continue
            state = job_status(root, path)
            if state["status"] == "ready_to_import":
                imported_existing.append(import_job(root, path))
    profile, _ = _load_profile(root, profile_id)
    executor_identity: SemanticProviderIdentity | None = None
    if str(profile.get("document_ir_version") or ""):
        executor_identity = _executor_identity_from_config(executor_config)
    prepared = prepare_job(
        root,
        profile_id=profile_id,
        limit=limit,
        max_input_characters=max_input_characters,
        executor_mode=("api" if executor_identity is not None else None),
        executor_provider=(
            executor_identity.provider if executor_identity is not None else None
        ),
        executor_model=(
            executor_identity.model if executor_identity is not None else None
        ),
        executor_client_version=(
            executor_identity.client_version
            if executor_identity is not None
            else None
        ),
        _allow_terminal_retry=False,
    )
    execution = "empty"
    run_report: dict[str, object] | None = None
    import_report: dict[str, object] | None = None
    if int(prepared["documents"]) > 0:
        if executor_config is None or not str(executor_config).strip():
            execution = "awaiting_executor"
        else:
            run_report = run_job(
                root,
                str(prepared["job_dir"]),
                executor_config=executor_config,
                _retry_import_errors=False,
            )
            execution = str(run_report["status"])
            if int(run_report.get("completed") or 0) or int(
                run_report.get("reused") or 0
            ):
                import_report = import_job(
                    root,
                    str(prepared["job_dir"]),
                )
    imported_count = sum(
        int(item.get("valid") or 0)
        + int(item.get("no_event") or 0)
        for item in imported_existing
    )
    if import_report is not None:
        imported_count += int(import_report.get("valid") or 0)
        imported_count += int(import_report.get("no_event") or 0)
    if execution == "empty":
        quality_status = "idle"
    elif execution == "awaiting_executor":
        quality_status = "awaiting_executor"
    elif run_report is None:
        quality_status = "degraded"
    else:
        completed = int(run_report.get("completed") or 0) + int(
            run_report.get("reused") or 0
        )
        failed = int(run_report.get("failed") or 0)
        compilation = run_report.get("mention_compilation")
        rejected_mentions = (
            int(compilation.get("rejected") or 0)
            if isinstance(compilation, Mapping)
            else 0
        )
        if failed and completed == 0:
            quality_status = "degraded"
        elif (
            failed
            or rejected_mentions
            or str(run_report.get("status") or "") == "partial"
        ):
            quality_status = "partial"
        else:
            quality_status = "healthy"
    feature_refresh = _refresh_feature_overlay(
        root,
        imported_count=imported_count,
    )
    report = {
        "schema_version": 1,
        "status": "complete",
        "profile_id": profile_id,
        "execution": execution,
        "quality_status": quality_status,
        "prepared": prepared,
        "imported_existing": imported_existing,
        "run": run_report,
        "import": import_report,
        "feature_refresh": feature_refresh,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_trading_only": True,
    }
    reports = root / "reports" / "intelligence"
    reports.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    report_path = reports / f"semantic_daily_{day}.json"
    _write_json(report_path, report)
    write_text_atomic(
        reports / f"semantic_daily_{day}.md",
        _render_daily_markdown(report),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def _refresh_feature_overlay(
    repo_root: Path,
    *,
    imported_count: int,
) -> dict[str, object] | None:
    if int(imported_count) <= 0:
        return None
    try:
        from ...research.intelligence_effect import (
            refresh_latest_intelligence_features,
        )

        return refresh_latest_intelligence_features(
            repo_root,
            market="a_share",
        )
    except FileNotFoundError:
        return {
            "status": "skipped",
            "reason": "research_snapshot_missing",
        }


def job_status(
    repo_root: str | Path,
    job_path: str | Path,
) -> dict[str, object]:
    """Return derived state without requiring a mutable batch ledger."""

    root = Path(repo_root).resolve()
    job_dir = _resolve_job_dir(root, job_path)
    manifest = _read_json(job_dir / "job.json")
    _verify_manifest(manifest, job_dir=job_dir)
    expected = len(manifest["items"])
    output_path = job_dir / "output.jsonl"
    outputs = (
        len(
            _read_jsonl(
                output_path,
                max_rows=expected,
            )
        )
        if output_path.exists()
        else 0
    )
    report_path = job_dir / "import_report.json"
    if report_path.exists():
        report = _read_json(report_path)
        reported_outputs = int(report.get("outputs") or 0)
        if (
            str(report.get("status") or "") == "imported"
            and int(report.get("awaiting") or 0) == 0
        ):
            status = "imported"
        elif int(report.get("quarantined") or 0):
            status = "quarantined"
        elif int(report.get("failed") or 0):
            status = "partial"
        elif outputs > reported_outputs:
            status = "ready_to_import"
        elif int(report.get("awaiting") or 0):
            status = "awaiting_executor"
        elif outputs:
            status = "ready_to_import"
        else:
            status = "awaiting_executor"
    else:
        status = "ready_to_import" if outputs else "awaiting_executor"
    return {
        "status": status,
        "job_id": manifest["job_id"],
        "job_dir": str(job_dir),
        "expected": expected,
        "outputs": outputs,
        "import_report": str(report_path) if report_path.exists() else None,
    }


def _verified_inputs(
    job_dir: Path,
    manifest: Mapping[str, object],
) -> list[dict[str, object]]:
    rows = _read_jsonl(job_dir / "input.jsonl")
    items = [
        _mapping(value, "semantic_job_item_invalid")
        for value in manifest["items"]
    ]
    if len(rows) != len(items):
        raise SemanticExchangeError("semantic_job_input_count_mismatch")
    for row, item in zip(rows, items, strict=True):
        supplied_hash = str(row.get("input_hash") or "")
        unsigned = dict(row)
        unsigned.pop("input_hash", None)
        if row.get("semantic_task_id") is not None:
            unsigned.pop("contract_version", None)
            unsigned.pop("semantic_task_id", None)
            unsigned.pop("execution_job_id", None)
            unsigned.pop("binding_id", None)
        if canonical_json_hash(unsigned) != supplied_hash:
            raise SemanticExchangeError("semantic_job_input_tampered")
        if _positive_int(row.get("document_id")) != _positive_int(
            item.get("document_id")
        ):
            raise SemanticExchangeError("semantic_job_input_order_mismatch")
        _verify_artifact_input_identity(row, item)
        for key in (
            "semantic_task_id",
            "execution_job_id",
            "binding_id",
        ):
            if key in item and str(row.get(key) or "") != str(item[key]):
                raise SemanticExchangeError("semantic_job_input_identity_mismatch")
    return rows


def _load_executor(
    job_dir: Path,
    *,
    executor_config: str | Path | None,
) -> SemanticExtractionProvider:
    from .provider import OpenAICompatibleSemanticProvider

    config = _read_executor_config(executor_config)
    prompt = (job_dir / "prompt.md").read_text(encoding="utf-8")
    try:
        return OpenAICompatibleSemanticProvider.from_executor_config(
            config,
            system_prompt=prompt,
            budget_state_path=job_dir / "executor_budget.json",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticExchangeError(
            "semantic_executor_config_invalid"
        ) from exc


def _executor_identity_from_config(
    executor_config: str | Path | None,
) -> SemanticProviderIdentity:
    from .provider import OpenAICompatibleSemanticProvider

    config = _read_executor_config(executor_config)
    try:
        provider = OpenAICompatibleSemanticProvider.from_executor_config(
            config,
            system_prompt="executor identity binding",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticExchangeError(
            "semantic_executor_config_invalid"
        ) from exc
    return provider.identity


def _read_executor_config(
    executor_config: str | Path | None,
) -> dict[str, object]:
    if executor_config is None or not str(executor_config).strip():
        raise SemanticExchangeError("semantic_executor_config_required")
    supplied_path = Path(str(executor_config).strip()).expanduser()
    if not supplied_path.is_file():
        raise SemanticExchangeError("semantic_executor_config_invalid")
    try:
        import yaml

        payload = yaml.safe_load(
            supplied_path.resolve().read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise SemanticExchangeError(
            "semantic_executor_config_invalid"
        ) from exc
    return dict(_mapping(payload, "semantic_executor_config_invalid"))


def canonical_json_hash(value: object) -> str:
    return _text_hash(_canonical_json(value))


def _load_profile(
    root: Path,
    profile_id: str,
) -> tuple[dict[str, object], Path]:
    normalized = str(profile_id).strip()
    if not _PROFILE_ID.fullmatch(normalized):
        raise SemanticExchangeError("semantic_profile_id_invalid")
    profile_root = (
        root / "configs" / "intelligence_extraction_profiles"
    )
    matches: list[tuple[dict[str, object], Path]] = []
    for path in sorted(profile_root.glob("*.json")):
        payload = _read_json(path)
        if str(payload.get("profile_id") or "") == normalized:
            matches.append((payload, path))
    if not matches:
        raise SemanticExchangeError("semantic_profile_not_found")
    if len(matches) != 1:
        raise SemanticExchangeError("semantic_profile_duplicate")
    profile, path = matches[0]
    required = {
        "profile_id",
        "prompt_version",
        "schema_version",
        "taxonomy_version",
        "taxonomy_path",
        "included_security_suffixes",
        "excluded_security_prefixes",
    }
    if required.difference(profile):
        raise SemanticExchangeError("semantic_profile_invalid")
    return profile, path


def _profile_allows_snapshot(
    profile: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> bool:
    links = snapshot.get("security_links")
    if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
        return False
    codes = [
        str(link.get("ts_code") or "").strip().upper()
        for link in links
        if isinstance(link, Mapping)
    ]
    suffixes = tuple(
        str(value).upper()
        for value in profile["included_security_suffixes"]
    )
    excluded = tuple(
        str(value)
        for value in profile["excluded_security_prefixes"]
    )
    return any(
        code.endswith(suffixes)
        and not code.split(".", 1)[0].startswith(excluded)
        for code in codes
    )


def _repair_prior_runs(
    store: IntelligenceStore,
    *,
    document_id: int,
    replacement_prompt_version: str,
) -> list[dict[str, object]]:
    """Return immutable terminal lineage that a repair result may supersede."""

    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT s.run_id, s.provider, s.model, s.prompt_version,
                   s.schema_version, s.taxonomy_version, s.parser_version,
                   s.input_hash, s.output_hash, s.status, s.finished_at
            FROM semantic_runs s
            WHERE s.document_id=?
              AND s.prompt_version<>?
              AND s.status IN ('succeeded', 'no_event')
            ORDER BY s.finished_at, s.run_id
            """,
            (int(document_id), str(replacement_prompt_version)),
        ).fetchall()
    return [dict(row) for row in rows]


def _exchange_candidate_ids(
    store: IntelligenceStore,
    *,
    profile: Mapping[str, object],
    prompt_version: str,
    schema_version: str,
    taxonomy_version: str,
    limit: int,
    priority_codes: Sequence[str] = (),
    allow_terminal_retry: bool = True,
) -> list[int]:
    suffixes = [
        str(value).upper()
        for value in _sequence(
            profile.get("included_security_suffixes"),
            "semantic_profile_invalid",
        )
    ]
    excluded = [
        str(value)
        for value in _sequence(
            profile.get("excluded_security_prefixes"),
            "semantic_profile_invalid",
        )
    ]
    suffix_sql = " OR ".join(
        "UPPER(l.ts_code) LIKE ?" for _ in suffixes
    )
    excluded_sql = " AND ".join(
        "l.ts_code NOT LIKE ?" for _ in excluded
    )
    link_sql = suffix_sql
    if excluded_sql:
        link_sql = f"({suffix_sql}) AND {excluded_sql}"
    parameters: list[object] = [
        *(f"%{suffix}" for suffix in suffixes),
        *(f"{prefix}%" for prefix in excluded),
        str(prompt_version),
        str(schema_version),
        str(taxonomy_version),
        max(1, min(int(limit), 100_000)),
    ]
    normalized_priority = sorted(
        {
            str(code).split(".", 1)[0].strip().upper()
            for code in priority_codes
            if str(code).strip()
        }
    )
    priority_sql = "CASE WHEN 1=1 THEN 0 END"
    terminal_failure_sql = (
        "(s.status='failed_terminal' "
        "AND s.error LIKE 'preflight_terminal:%')"
        if allow_terminal_retry
        else "s.status='failed_terminal'"
    )
    with store.connect() as connection:
        if normalized_priority:
            connection.execute(
                """
                CREATE TEMP TABLE semantic_priority_codes(
                    code TEXT PRIMARY KEY
                ) WITHOUT ROWID
                """
            )
            connection.executemany(
                "INSERT INTO semantic_priority_codes(code) VALUES(?)",
                ((code,) for code in normalized_priority),
            )
            priority_sql = """
                EXISTS (
                    SELECT 1
                    FROM document_security_links priority_link
                    JOIN semantic_priority_codes priority
                      ON priority.code=UPPER(
                        CASE
                          WHEN INSTR(priority_link.ts_code, '.')>0
                          THEN SUBSTR(
                            priority_link.ts_code,
                            1,
                            INSTR(priority_link.ts_code, '.')-1
                          )
                          ELSE priority_link.ts_code
                        END
                      )
                    WHERE priority_link.document_id=d.id
                )
            """
        rows = connection.execute(
            f"""
            SELECT d.id
            FROM documents d
            JOIN document_artifacts a
              ON a.artifact_id=(
                SELECT a2.artifact_id
                FROM document_artifacts a2
                WHERE a2.document_id=d.id
                  AND a2.artifact_type='parsed'
                  AND a2.status IN ('parsed', 'ocr_failed')
                ORDER BY a2.updated_at DESC, a2.artifact_id DESC
                LIMIT 1
              )
            WHERE EXISTS (
                SELECT 1
                FROM document_security_links l
                WHERE l.document_id=d.id
                  AND {link_sql}
            )
              AND NOT EXISTS (
                SELECT 1
                FROM semantic_runs s
                WHERE s.document_id=d.id
                  AND s.artifact_hash=a.content_hash
                  AND s.prompt_version=?
                  AND s.schema_version=?
                  AND s.taxonomy_version=?
                  AND s.parser_version=a.parser_version
                  AND (
                    s.status='no_event'
                    OR ({terminal_failure_sql})
                    OR (
                      s.status='succeeded'
                      AND EXISTS (
                        SELECT 1
                        FROM event_candidates c
                        WHERE c.run_id=s.run_id
                          AND c.validation_status='canonical'
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM event_candidates c
                        WHERE c.run_id=s.run_id
                          AND c.validation_status<>'canonical'
                      )
                    )
                  )
              )
            ORDER BY ({priority_sql}) DESC,
                     d.live_observed DESC,
                     d.published_at DESC,
                     d.queue_priority DESC,
                     d.id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _latest_research_universe(
    root: Path,
    *,
    market: str,
) -> tuple[str, ...]:
    feature_root = root / "data" / "research" / "features" / market
    snapshots = sorted(
        path
        for path in feature_root.glob("*.parquet")
        if len(path.stem) == 8 and path.stem.isdigit()
    )
    if not snapshots:
        return ()
    try:
        frame = pd.read_parquet(snapshots[-1], columns=["code"])
    except (OSError, ValueError, KeyError):
        return ()
    return tuple(
        sorted(
            {
                str(value).split(".", 1)[0].strip().upper()
                for value in frame["code"].dropna()
                if str(value).strip()
            }
        )
    )


def _already_terminal(
    store: IntelligenceStore,
    *,
    document_id: int,
    artifact_hash: str,
    prompt_version: str,
    schema_version: str,
    taxonomy_version: str,
    parser_version: str,
    allow_terminal_retry: bool = True,
) -> bool:
    terminal_failure_sql = (
        "(s.status='failed_terminal' "
        "AND s.error LIKE 'preflight_terminal:%')"
        if allow_terminal_retry
        else "s.status='failed_terminal'"
    )
    with store.connect() as connection:
        row = connection.execute(
            f"""
            SELECT 1
            FROM semantic_runs s
            WHERE s.document_id=? AND s.artifact_hash=?
              AND s.prompt_version=? AND s.schema_version=?
              AND s.taxonomy_version=? AND s.parser_version=?
              AND (
                s.status='no_event'
                OR ({terminal_failure_sql})
                OR (
                  s.status='succeeded'
                  AND EXISTS (
                    SELECT 1
                    FROM event_candidates c
                    WHERE c.run_id=s.run_id
                      AND c.validation_status='canonical'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM event_candidates c
                    WHERE c.run_id=s.run_id
                      AND c.validation_status<>'canonical'
                  )
                )
              )
            LIMIT 1
            """,
            (
                int(document_id),
                str(artifact_hash),
                str(prompt_version),
                str(schema_version),
                str(taxonomy_version),
                str(parser_version),
            ),
        ).fetchone()
    return row is not None


def _bound_payload(
    payload: Mapping[str, object],
    *,
    max_input_characters: int,
) -> dict[str, object]:
    bounded = json.loads(_canonical_json(payload))
    chunks = bounded.get("chunks")
    if not isinstance(chunks, list):
        return bounded
    # Table cells are already represented as evidence-capable table_cell
    # chunks. Keeping cells here duplicates the largest part of many PDFs.
    bounded["tables"] = []
    bounded["chunks"] = []
    base_size = len(_canonical_json(bounded))
    remaining_budget = max(0, max_input_characters - base_size)
    retained: list[dict[str, object]] = []
    prioritized_chunks = [
        value
        for value in chunks
        if isinstance(value, dict)
        and str(value.get("section") or "") == "document_metadata"
    ] + [
        value
        for value in chunks
        if not (
            isinstance(value, dict)
            and str(value.get("section") or "") == "document_metadata"
        )
    ]
    for value in prioritized_chunks:
        if not isinstance(value, dict):
            continue
        serialized_size = len(_canonical_json(value)) + 1
        if serialized_size <= remaining_budget:
            retained.append(value)
            remaining_budget -= serialized_size
            continue
        empty = {**value, "text": ""}
        overhead = len(_canonical_json(empty)) + 1
        if overhead >= remaining_budget:
            break
        text_budget = remaining_budget - overhead
        retained.append(
            {
                **value,
                "text": str(value.get("text") or "")[:text_budget],
            }
        )
        remaining_budget = 0
        break
    bounded["chunks"] = retained
    while retained and len(_canonical_json(bounded)) > max_input_characters:
        overflow = len(_canonical_json(bounded)) - max_input_characters
        last = retained[-1]
        text = str(last.get("text") or "")
        if not text:
            retained.pop()
            continue
        last["text"] = text[: max(0, len(text) - overflow)]
    return bounded


def _bound_v21_payload(
    payload: Mapping[str, object],
    *,
    max_input_characters: int,
) -> dict[str, object]:
    """Build one bounded packet from complete, relation-closed IR nodes."""

    bounded = json.loads(_canonical_json(payload))
    raw_ir = _mapping(
        bounded.get("document_ir"),
        "semantic_document_ir_invalid",
    )
    try:
        ir_nodes = ir_nodes_by_id(raw_ir)
        empty_ir = project_document_ir(raw_ir, [])
    except DocumentIRPreflightError as exc:
        raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
    raw_chunks = _sequence(
        bounded.get("chunks"),
        "semantic_job_chunks_invalid",
    )
    chunks = [dict(value) for value in raw_chunks if isinstance(value, Mapping)]
    bounded["tables"] = []
    bounded["chunks"] = []
    bounded["document_ir"] = empty_ir
    if len(_canonical_json(bounded)) > max_input_characters:
        raise SemanticExchangeError("semantic_evidence_packet_contract_oversized")

    document = _mapping(
        bounded.get("document"),
        "semantic_job_document_invalid",
    )
    title_categories = title_event_categories(str(document.get("title") or ""))
    routed_categories = tuple(
        str(value)
        for value in _sequence(
            bounded.get("taxonomy_candidates"),
            "semantic_job_taxonomy_candidates_invalid",
        )
        if str(value)
    )
    focused_categories = (
        title_categories
        if title_categories
        else routed_categories if 0 < len(routed_categories) <= 4 else ()
    )
    retrieval_terms = tuple(
        dict.fromkeys(
            term.casefold()
            for event_type in focused_categories
            for term in _V21_EVENT_RETRIEVAL_TERMS.get(event_type, ())
        )
    )
    lexical_scores = [
        _v21_lexical_score(str(chunk.get("text") or ""), retrieval_terms)
        for chunk in chunks
    ]
    superseded_body_ids = _v21_superseded_body_ids(chunks)

    body_ordinal = 0
    prioritized: list[tuple[int, int, int, dict[str, object]]] = []
    for ordinal, chunk in enumerate(chunks):
        section = str(chunk.get("section") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        node = ir_nodes.get(chunk_id)
        if section == "document_metadata":
            priority = 0
            score = 0
        elif _v21_revision_boundary_chunk(str(chunk.get("text") or "")):
            priority = 1
            score = lexical_scores[ordinal]
        elif section != "table_cell":
            priority = 2
            score = lexical_scores[ordinal]
            if score == 0 and any(
                lexical_scores[index] > 0
                for index in (ordinal - 1, ordinal + 1)
                if 0 <= index < len(lexical_scores)
            ):
                score = 40
            if body_ordinal < 2:
                score += 20
            body_ordinal += 1
        elif _usable_ir_value_node(node):
            priority = 2
            score = (
                80
                + lexical_scores[ordinal]
                + _v21_lexical_score(
                    _v21_ir_context_text(node, ir_nodes),
                    retrieval_terms,
                )
            )
            if _v21_zero_numeric_value(str(node.get("raw_value") or "")):
                score -= 250
        else:
            priority = 2
            score = lexical_scores[ordinal]
        prioritized.append((priority, -score, ordinal, chunk))

    retained: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    selected_value_ids: set[str] = set()
    for _, _, _, chunk in sorted(prioritized, key=lambda value: value[:3]):
        chunk_id = str(chunk.get("chunk_id") or "")
        if chunk_id in superseded_body_ids:
            continue
        node = ir_nodes.get(chunk_id)
        if (
            isinstance(node, Mapping)
            and node.get("node_type") == "table_cell"
            and node.get("semantic_role") == "value"
            and not _usable_ir_value_node(node)
        ):
            continue
        trial_ids = set(selected_ids)
        if chunk_id in ir_nodes:
            trial_ids.add(chunk_id)
        try:
            projected = project_document_ir(raw_ir, sorted(trial_ids))
        except DocumentIRPreflightError as exc:
            raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
        trial = {
            **bounded,
            "chunks": [*retained, chunk],
            "document_ir": projected,
        }
        if len(_canonical_json(trial)) > max_input_characters:
            continue
        retained.append(chunk)
        selected_ids = trial_ids
        bounded = trial
        if _usable_ir_value_node(node):
            selected_value_ids.add(chunk_id)

    if not retained:
        raise SemanticExchangeError("semantic_evidence_packet_empty")
    try:
        preflight_evidence_packet(
            _mapping(
                bounded.get("document_ir"),
                "semantic_document_ir_invalid",
            ),
            sorted(selected_value_ids),
        )
    except DocumentIRPreflightError as exc:
        raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
    if len(_canonical_json(bounded)) > max_input_characters:
        raise SemanticExchangeError("semantic_evidence_packet_contract_oversized")
    return bounded


def _v21_revision_boundary_chunk(text: str) -> bool:
    normalized = str(text).casefold()
    return any(term.casefold() in normalized for term in _V21_REVISION_BOUNDARY_TERMS)


def _v21_superseded_body_ids(
    chunks: Sequence[Mapping[str, object]],
) -> set[str]:
    state = "neutral"
    superseded: set[str] = set()
    current_terms = tuple(
        term.casefold()
        for term in _V21_REVISION_BOUNDARY_TERMS[:8]
    )
    old_terms = tuple(
        term.casefold()
        for term in _V21_REVISION_BOUNDARY_TERMS[8:]
    )
    indexed_chunks = list(enumerate(chunks))

    def document_order(item):
        index, chunk = item
        bbox = chunk.get("bbox")
        top = (
            float(bbox[1])
            if isinstance(bbox, list) and len(bbox) == 4
            else float(index + 10_000)
        )
        return (int(chunk.get("page_number") or 0), top, index)

    for _, chunk in sorted(indexed_chunks, key=document_order):
        section = str(chunk.get("section") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        if section == "document_metadata":
            continue
        text = str(chunk.get("text") or "").casefold()
        current_position = max(
            (text.rfind(term) for term in current_terms),
            default=-1,
        )
        old_position = max(
            (text.rfind(term) for term in old_terms),
            default=-1,
        )
        if current_position >= 0 or old_position >= 0:
            state = (
                "current"
                if current_position > old_position
                else "superseded"
            )
            continue
        if state == "superseded" and chunk_id:
            superseded.add(chunk_id)
    return superseded


def _v21_ir_context_text(
    node: Mapping[str, object],
    nodes: Mapping[str, Mapping[str, object]],
) -> str:
    values: list[str] = []
    for path_name in ("row_header_path", "column_header_path"):
        path = node.get(path_name)
        if not isinstance(path, list):
            continue
        for item in path:
            if not isinstance(item, Mapping):
                continue
            related = nodes.get(str(item.get("node_id") or ""))
            if related is not None:
                values.append(str(related.get("text") or ""))
    return " ".join(values)


def _v21_zero_numeric_value(raw_value: str) -> bool:
    normalized = re.sub(r"[,%％，\s]", "", str(raw_value))
    return bool(re.fullmatch(r"[-+]?0+(?:\.0+)?", normalized))


def _v21_lexical_score(text: str, retrieval_terms: Sequence[str]) -> int:
    normalized = str(text).casefold()
    score = 100 * sum(term in normalized for term in retrieval_terms)
    score += 25 * sum(
        term.casefold() in normalized
        for term in _V21_STATUS_RETRIEVAL_TERMS
    )
    if retrieval_terms and re.search(r"\d[\d,.]*\s*(?:%|％|股|元)", normalized):
        score += 20
    return score


def _usable_ir_value_node(node: object) -> bool:
    if not isinstance(node, Mapping):
        return False
    if (
        node.get("node_type") != "table_cell"
        or node.get("semantic_role") != "value"
    ):
        return False
    if not node.get("row_header_path") or not node.get("column_header_path"):
        return False
    resolution = node.get("unit_resolution")
    return bool(
        isinstance(resolution, Mapping)
        and resolution.get("value") is not None
        and not resolution.get("conflicts")
    )


def _taxonomy_requirements(
    taxonomy: EventTaxonomy,
    raw_candidates: object,
) -> list[dict[str, object]]:
    if not isinstance(raw_candidates, list) or not raw_candidates:
        candidate_types = set(taxonomy.event_types)
    else:
        candidate_types = _canonical_taxonomy_candidates(raw_candidates)
    # Router labels may mix taxonomy event types with coarse classifier labels
    # such as ``penalty``. Only taxonomy event types belong in the extraction
    # contract; dropping coarse labels preserves the established router boundary
    # without weakening schema validation of the executor output.
    candidate_types = candidate_types & taxonomy.event_types

    requirements: list[dict[str, object]] = []
    for event in taxonomy.events:
        if event.event_type not in candidate_types:
            continue
        requirements.append(
            {
                "event_type": event.event_type,
                "allowed_lifecycle": list(event.allowed_lifecycle),
                "required_subject_roles": list(
                    event.required_subject_roles
                ),
                "required_facts": {
                    "default": _fact_requirement_payload(
                        event.default_requirements
                    ),
                    "by_lifecycle": {
                        lifecycle: _fact_requirement_payload(requirement)
                        for lifecycle, requirement in (
                            event.lifecycle_requirements
                        )
                    },
                },
                "optional_facts": list(event.optional_facts),
                "fact_specs": {
                    name: {
                        "value_type": spec.value_type,
                        "allowed_unit_kinds": list(
                            spec.allowed_unit_kinds
                        ),
                        "evidence_terms_any": list(
                            spec.evidence_terms_any
                        ),
                    }
                    for name, spec in event.fact_specs.items()
                },
                "dedupe_fields": list(event.dedupe_fields),
            }
        )
    return requirements


def _mention_templates(
    taxonomy: EventTaxonomy,
    raw_candidates: object,
) -> list[dict[str, object]]:
    """Expose only source labels the executor may copy; local code owns rules."""

    if not isinstance(raw_candidates, list) or not raw_candidates:
        candidate_types = set(taxonomy.event_types)
    else:
        candidate_types = (
            _canonical_taxonomy_candidates(raw_candidates)
            & taxonomy.event_types
        )
    templates: list[dict[str, object]] = []
    for event in taxonomy.events:
        if event.event_type not in candidate_types:
            continue
        subject_roles = set(event.required_subject_roles)
        date_kinds = set(event.default_requirements.required_dates)
        for _, requirement in event.lifecycle_requirements:
            date_kinds.update(requirement.required_dates)
        for field in event.dedupe_fields:
            source, name = field.split(":", 1)
            if source == "subject":
                subject_roles.add(name)
            elif source == "date":
                date_kinds.add(name)
        templates.append(
            {
                "event_type": event.event_type,
                "subject_roles": sorted(subject_roles),
                "fact_names": sorted(event.declared_facts),
                "date_kinds": sorted(date_kinds),
                "required_all_of": list(
                    event.default_requirements.all_of
                ),
                "required_one_of_sets": [
                    list(group)
                    for group in event.default_requirements.one_of_sets
                ],
                "default_requirements": _fact_requirement_payload(
                    event.default_requirements
                ),
                "requirements_by_lifecycle": {
                    lifecycle: _fact_requirement_payload(requirement)
                    for lifecycle, requirement in event.lifecycle_requirements
                },
                "dedupe_fields": list(event.dedupe_fields),
            }
        )
    return templates


_TAXONOMY_CANDIDATE_ALIASES = {
    "capacity_expansion": "capacity_project",
}


def _canonical_taxonomy_candidates(raw_candidates: object) -> set[str]:
    if not isinstance(raw_candidates, list):
        return set()
    values: set[str] = set()
    for raw_value in raw_candidates:
        value = str(raw_value).strip()
        if not value:
            continue
        values.add(_TAXONOMY_CANDIDATE_ALIASES.get(value, value))
    return values


def _fact_requirement_payload(
    requirement: FactRequirement,
) -> dict[str, object]:
    return {
        "all_of": list(requirement.all_of),
        "one_of_sets": [
            list(group) for group in requirement.one_of_sets
        ],
        "required_dates": list(requirement.required_dates),
        "inherit_prior": requirement.inherit_prior,
        "unmatched_fallback": requirement.unmatched_fallback,
    }


def _verify_manifest(
    manifest: Mapping[str, object],
    *,
    job_dir: Path,
    expected_job_id: str | None = None,
) -> None:
    if manifest.get("contract_version") != JOB_CONTRACT_VERSION:
        raise SemanticExchangeError("semantic_job_contract_invalid")
    job_id = str(manifest.get("job_id") or "")
    if expected_job_id is not None and job_id != expected_job_id:
        raise SemanticExchangeError("semantic_job_id_mismatch")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise SemanticExchangeError("semantic_job_items_invalid")
    contract_keys = (
        "contract_version",
        "profile_id",
        "profile_hash",
        "prompt_version",
        "prompt_hash",
        "schema_version",
        "schema_hash",
        "taxonomy_version",
        "taxonomy_hash",
        "selection_policy",
        "budgets",
        "items",
    )
    if manifest.get("compiler_version") is not None:
        contract_keys += ("compiler_version",)
    if manifest.get("execution_contract_version") is not None:
        contract_keys += (
            "execution_contract_version",
            "semantic_contract_hash",
            "executor_binding",
            "binding_id",
            "document_ir_version",
            "document_ir_hash",
            "retriever_version",
            "evidence_packets_hash",
        )
    if manifest.get("repair_contract_version") is not None:
        contract_keys += (
            "repair_contract_version",
            "repair_id",
            "repair_reason",
        )
    if any(key not in manifest for key in contract_keys):
        raise SemanticExchangeError("semantic_job_manifest_invalid")
    contract_payload = {
        key: manifest[key] for key in contract_keys
    }
    derived_job_id = (
        f"sj-{canonical_json_hash(contract_payload)[:24]}"
    )
    if job_id != derived_job_id:
        raise SemanticExchangeError("semantic_job_manifest_tampered")
    try:
        prompt = (job_dir / "prompt.md").read_text(encoding="utf-8")
        profile = _read_json(job_dir / "profile.json")
        schema = _read_json(job_dir / "schema.json")
        taxonomy_hash = _file_hash(job_dir / "taxonomy.json")
    except OSError as exc:
        raise SemanticExchangeError(
            "semantic_job_asset_missing"
        ) from exc
    if _text_hash(prompt) != str(manifest["prompt_hash"]):
        raise SemanticExchangeError("semantic_job_prompt_tampered")
    if canonical_json_hash(profile) != str(manifest["profile_hash"]):
        raise SemanticExchangeError("semantic_job_profile_tampered")
    if canonical_json_hash(schema) != str(manifest["schema_hash"]):
        raise SemanticExchangeError("semantic_job_schema_tampered")
    if taxonomy_hash != str(manifest["taxonomy_hash"]):
        raise SemanticExchangeError("semantic_job_taxonomy_tampered")
    if manifest.get("execution_contract_version") is not None:
        if manifest.get("execution_contract_version") != EXECUTION_CONTRACT_VERSION:
            raise SemanticExchangeError("semantic_execution_contract_invalid")
        try:
            binding = ExecutorBinding.from_mapping(
                _mapping(
                    manifest.get("executor_binding"),
                    "semantic_executor_binding_invalid",
                )
            )
        except SemanticExecutionContractError as exc:
            raise SemanticExchangeError(exc.code, detail=exc.detail) from exc
        if binding.binding_id != str(manifest.get("binding_id") or ""):
            raise SemanticExchangeError("semantic_executor_binding_tampered")
        document_ir_rows = _read_jsonl(
            job_dir / "document_ir.jsonl",
            max_rows=len(items),
            max_line_bytes=MAX_DOCUMENT_IR_LINE_BYTES,
        )
        evidence_packet_rows = _read_jsonl(
            job_dir / "evidence_packets.jsonl",
            max_rows=len(items),
        )
        if canonical_json_hash(document_ir_rows) != str(
            manifest.get("document_ir_hash") or ""
        ):
            raise SemanticExchangeError("semantic_job_document_ir_tampered")
        if canonical_json_hash(evidence_packet_rows) != str(
            manifest.get("evidence_packets_hash") or ""
        ):
            raise SemanticExchangeError("semantic_job_evidence_packets_tampered")


def _verify_output_identity(
    envelope: Mapping[str, object],
    item: Mapping[str, object],
) -> None:
    if (
        envelope.get("contract_version")
        != OUTPUT_CONTRACT_VERSION
    ):
        raise SemanticExchangeError("semantic_output_contract_invalid")
    _verify_artifact_input_identity(envelope, item)
    for key in ("semantic_task_id", "execution_job_id", "binding_id"):
        if key in item and str(envelope.get(key) or "") != str(item[key]):
            raise SemanticExchangeError("semantic_output_identity_mismatch")


def _verify_artifact_input_identity(
    value: Mapping[str, object],
    item: Mapping[str, object],
) -> None:
    if str(value.get("artifact_hash") or "") != str(
        item["artifact_hash"]
    ):
        raise SemanticExchangeError("semantic_job_artifact_hash_mismatch")
    if str(value.get("input_hash") or "") != str(item["input_hash"]):
        raise SemanticExchangeError("semantic_job_input_hash_mismatch")


def _executor_identity(
    value: object,
    *,
    trusted_executor: object = None,
) -> dict[str, str]:
    mapping = _mapping(value, "semantic_job_executor_invalid")
    identity = {
        "kind": str(mapping.get("kind") or "").strip(),
        "provider": str(mapping.get("provider") or "").strip(),
        "model": str(mapping.get("model") or "").strip(),
    }
    if any(not value for value in identity.values()):
        raise SemanticExchangeError("semantic_job_executor_invalid")
    trust = str(mapping.get("identity_trust") or "").strip()
    if trust == "runner-configured":
        trusted = _mapping(
            trusted_executor,
            "semantic_job_executor_provenance_invalid",
        )
        if (
            str(trusted.get("provider") or "") != identity["provider"]
            or str(trusted.get("model") or "") != identity["model"]
        ):
            raise SemanticExchangeError(
                "semantic_job_executor_provenance_invalid"
            )
    else:
        identity["provider"] = f"declared:{identity['provider']}"
        identity["model"] = f"declared:{identity['model']}"
    return identity


def _resolve_job_dir(root: Path, job_path: str | Path) -> Path:
    jobs_root = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "extraction_jobs"
    ).resolve()
    supplied = Path(job_path)
    candidate = (
        supplied.resolve()
        if supplied.is_absolute()
        else (jobs_root / supplied).resolve()
    )
    if not candidate.is_relative_to(jobs_root):
        raise SemanticExchangeError("semantic_job_path_invalid")
    if not candidate.is_dir():
        raise SemanticExchangeError("semantic_job_not_found")
    return candidate


def _rooted_path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root):
        raise SemanticExchangeError("semantic_profile_path_invalid")
    return candidate


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticExchangeError(code)
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, code: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes),
    ):
        raise SemanticExchangeError(code)
    return list(value)


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        raise SemanticExchangeError("semantic_job_document_id_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SemanticExchangeError(
            "semantic_job_document_id_invalid"
        ) from exc
    if result < 1:
        raise SemanticExchangeError("semantic_job_document_id_invalid")
    return result


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SemanticExchangeError("semantic_job_usage_invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SemanticExchangeError("semantic_job_usage_invalid") from exc
    if result < 0:
        raise SemanticExchangeError("semantic_job_usage_invalid")
    return result


def _prepare_summary(
    manifest: Mapping[str, object],
    job_dir: Path,
    *,
    reused: bool,
) -> dict[str, object]:
    return {
        "status": "prepared" if manifest["items"] else "empty",
        "job_id": manifest["job_id"],
        "job_dir": str(job_dir),
        "documents": len(manifest["items"]),
        "reused": reused,
    }


def _render_daily_markdown(report: Mapping[str, object]) -> str:
    prepared = _mapping(
        report.get("prepared"),
        "semantic_daily_report_invalid",
    )
    imported = _sequence(
        report.get("imported_existing", []),
        "semantic_daily_report_invalid",
    )
    imported_valid = sum(
        int(item.get("valid") or 0)
        + int(item.get("no_event") or 0)
        for item in imported
        if isinstance(item, Mapping)
    )
    current_import = report.get("import")
    if isinstance(current_import, Mapping):
        imported_valid += int(current_import.get("valid") or 0)
        imported_valid += int(current_import.get("no_event") or 0)
    return "\n".join(
        (
            "# 公告语义抽取日报",
            "",
            f"- 批次：`{prepared.get('job_id', '')}`",
            f"- 待抽取文档：`{prepared.get('documents', 0)}`",
            f"- 执行状态：`{report.get('execution', '')}`",
            f"- 质量状态：`{report.get('quality_status', '')}`",
            f"- 已导入返回结果：`{imported_valid}`",
            "- 使用边界：研究因子与模拟交易，不触发真实交易",
            "",
        )
    )


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticExchangeError("semantic_job_json_invalid") from exc
    return _mapping(payload, "semantic_job_json_invalid")


def _read_jsonl(
    path: Path,
    *,
    max_rows: int | None = None,
    max_line_bytes: int = MAX_JOB_LINE_BYTES,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        size = path.stat().st_size
        if size > MAX_JOB_FILE_BYTES:
            raise SemanticExchangeError("semantic_job_jsonl_too_large")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SemanticExchangeError("semantic_job_jsonl_unreadable") from exc
    for ordinal, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > max(0, int(max_line_bytes)):
            raise SemanticExchangeError(
                "semantic_job_jsonl_line_too_large",
                detail=str(ordinal),
            )
        if max_rows is not None and len(rows) >= max(0, int(max_rows)):
            raise SemanticExchangeError(
                "semantic_job_jsonl_row_limit",
                detail=str(ordinal),
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SemanticExchangeError(
                "semantic_job_jsonl_invalid",
                detail=str(ordinal),
            ) from exc
        rows.append(_mapping(payload, "semantic_job_jsonl_invalid"))
    return rows


def _write_json(path: Path, value: object) -> None:
    write_text_atomic(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(
    path: Path,
    rows: Sequence[Mapping[str, object]],
) -> None:
    text = "".join(
        _canonical_json(row) + "\n"
        for row in rows
    )
    write_text_atomic(path, text, encoding="utf-8")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "DEFAULT_PROFILE_ID",
    "JOB_CONTRACT_VERSION",
    "OUTPUT_CONTRACT_VERSION",
    "SemanticExchangeError",
    "canonical_json_hash",
    "import_job",
    "job_status",
    "prepare_job",
    "run_daily",
    "run_job",
]
