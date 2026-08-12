from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import stock_analyze.intelligence.semantic.exchange as semantic_exchange

from stock_analyze.intelligence.semantic.exchange import (
    SemanticExchangeError,
    _grounding_repair_bundle,
    _context_repair_can_be_no_event,
    _context_events_missing_current_transition,
    _prune_ungrounded_optional_facts,
    _bound_payload,
    _bound_v21_payload,
    _no_event_review_signal,
    _mention_templates,
    _missing_routed_event_types,
    _requires_no_event_review,
    _revision_rejection_can_be_no_event,
    _packet_visible_evidence_ids,
    _render_daily_markdown,
    _taxonomy_requirements,
    collect_coding_plan_outputs,
    import_job,
    job_status,
    prepare_job,
    prepare_repair_job,
    run_daily,
    run_job,
)
from stock_analyze.intelligence.semantic.document_ir import build_document_ir
from stock_analyze.intelligence.semantic.contracts import SemanticContractError
from stock_analyze.intelligence.semantic.provider import (
    SemanticInputBundle,
    SemanticProviderError,
    SemanticProviderIdentity,
    SemanticProviderResponse,
)
from stock_analyze.intelligence.semantic.taxonomy import EventTaxonomy
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument
from stock_analyze.research.storage import ResearchStore


ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = "a-share-announcement-v1"


class NoEventProvider:
    identity = SemanticProviderIdentity(
        provider="codex",
        model="codex-artifact-test",
        endpoint_host="local",
    )

    def __init__(self) -> None:
        self.calls = []

    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        result = {
            "document_id": bundle.document_id,
            "schema_version": "announcement-events-v1-lite",
            "events": [],
            "evidence": [],
            "no_event_reason": "no supported event",
        }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id="request-test",
            response_model=self.identity.model,
            input_tokens=120,
            output_tokens=24,
            total_tokens=144,
            latency_ms=9,
        )


class GroundingRepairProvider:
    identity = SemanticProviderIdentity(
        provider="openai-compatible",
        model="deepseek-v4-pro",
        endpoint_host="api.deepseek.com",
    )

    def __init__(self, *, repair_succeeds: bool = True) -> None:
        self.calls = []
        self.repair_succeeds = repair_succeeds

    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        repair_context = bundle.payload.get("repair_context")
        if repair_context and self.repair_succeeds:
            result = {
                "document_id": bundle.document_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no supported event",
            }
        else:
            result = {
                "document_id": bundle.document_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [
                    {
                        "evidence_id": "e1",
                        "chunk_id": f"chunk-{bundle.document_id}",
                        "quote": "原文不存在的句子",
                    }
                ],
                "no_event_reason": "no supported event",
            }
        raw = json.dumps(result, ensure_ascii=False)
        call_number = len(self.calls)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id=f"request-repair-{call_number}",
            response_model=self.identity.model,
            input_tokens=100 * call_number,
            output_tokens=10 * call_number,
            total_tokens=110 * call_number,
            latency_ms=5 * call_number,
        )


class CandidateValidationRepairProvider:
    identity = SemanticProviderIdentity(
        provider="openai-compatible",
        model="deepseek-v4-pro",
        endpoint_host="api.deepseek.com",
    )

    def __init__(self) -> None:
        self.calls = []

    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        if bundle.payload.get("repair_context"):
            result = {
                "document_id": bundle.document_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no supported event",
            }
        else:
            chunk = next(
                item
                for item in bundle.payload["chunks"]
                if "平安银行" in item["text"]
            )
            issuer = bundle.payload["entity_whitelist"][0]
            result = {
                "document_id": bundle.document_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [
                    {
                        "event_type": "buyback",
                        "lifecycle": "approved",
                        "subjects": [
                            {
                                "entity_id": issuer["entity_id"],
                                "role": "issuer",
                                "evidence_ids": ["e1"],
                            }
                        ],
                        "facts": [
                            {
                                "name": "amount_upper",
                                "raw_value": None,
                                "numeric_value": None,
                                "unit": None,
                                "currency": None,
                                "period": None,
                                "evidence_ids": ["e1"],
                            }
                        ],
                        "effective_dates": [],
                        "conditions": [],
                        "conflicts": [],
                        "missing_required_fields": [
                            "amount_upper",
                            "price_cap",
                        ],
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "e1",
                        "chunk_id": chunk["chunk_id"],
                        "quote": "平安银行",
                    }
                ],
                "no_event_reason": None,
            }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id=f"request-candidate-{len(self.calls)}",
            response_model=self.identity.model,
            input_tokens=100,
            output_tokens=10,
            total_tokens=110,
            latency_ms=5,
        )


class MentionProvider:
    identity = SemanticProviderIdentity(
        provider="openai-compatible",
        model="deepseek-v4-pro",
        endpoint_host="api.deepseek.com",
    )

    def __init__(self) -> None:
        self.calls = []

    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        chunk = next(
            item
            for item in bundle.payload["chunks"]
            if "平安银行" in item["text"]
        )
        result = {
            "document_id": bundle.document_id,
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [
                {
                    "mention_id": "buyback-1",
                    "event_type": "buyback",
                    "subjects": [
                        {
                            "role": "issuer",
                            "name": "平安银行",
                            "evidence": [
                                {"chunk_id": chunk["chunk_id"], "quote": "平安银行"}
                            ],
                        }
                    ],
                    "facts": [
                        {
                            "name": "amount_upper",
                            "raw_value": "1亿元",
                            "evidence": [
                                {"chunk_id": chunk["chunk_id"], "quote": "回购金额上限为1亿元"}
                            ],
                        },
                        {
                            "name": "price_cap",
                            "raw_value": "10元/股",
                            "evidence": [
                                {"chunk_id": chunk["chunk_id"], "quote": "回购价格不超过10元/股"}
                            ],
                        },
                    ],
                    "dates": [
                        {
                            "kind": "approval_date",
                            "raw_value": "2026年7月28日",
                            "evidence": [
                                {"chunk_id": chunk["chunk_id"], "quote": "2026年7月28日"}
                            ],
                        }
                    ],
                    "status": {
                        "raw_value": "审议通过",
                        "evidence": [
                            {"chunk_id": chunk["chunk_id"], "quote": "审议通过"}
                        ],
                    },
                }
            ],
            "no_event_reason": None,
        }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id="request-mention-1",
            response_model=self.identity.model,
            input_tokens=80,
            output_tokens=60,
            total_tokens=140,
            latency_ms=5,
        )


class IncrementalFamilyMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        if not bundle.payload.get("repair_context"):
            return super().extract(bundle, response_schema=response_schema)
        self.calls.append((bundle, response_schema))
        chunk = next(
            item
            for item in bundle.payload["chunks"]
            if "股东增持100万股" in str(item.get("text") or "")
        )
        result = {
            "document_id": bundle.document_id,
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [{
                "mention_id": "shareholder-change-1",
                "event_type": "shareholder_change",
                "subjects": [{
                    "role": "issuer",
                    "name": "平安银行",
                    "evidence": [{
                        "chunk_id": chunk["chunk_id"],
                        "quote": "平安银行",
                    }],
                }],
                "facts": [{
                    "name": "action",
                    "raw_value": "增持",
                    "evidence": [{
                        "chunk_id": chunk["chunk_id"],
                        "quote": "增持",
                    }],
                }, {
                    "name": "share_count",
                    "raw_value": "100万股",
                    "evidence": [{
                        "chunk_id": chunk["chunk_id"],
                        "quote": "100万股",
                    }],
                }],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id="request-mention-family-repair",
            response_model=self.identity.model,
            input_tokens=60,
            output_tokens=40,
            total_tokens=100,
            latency_ms=4,
        )


class SegmentMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        del response_schema
        self.calls.append((bundle, None))
        event_chunk = next(
            item
            for item in bundle.payload["chunks"]
            if "回购金额上限为1亿元" in str(item.get("text") or "")
        )
        issuer_chunk = next(
            item
            for item in bundle.payload["chunks"]
            if str(item.get("section") or "") == "document_metadata"
            and str(item.get("chunk_id") or "").endswith("-meta-issuer")
        )
        result = {
            "document_id": bundle.document_id,
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [{
                "mention_id": "buyback-segment-1",
                "event_type": "buyback",
                "subjects": [{
                    "role": "issuer",
                    "name": "平安银行",
                    "evidence": [{
                        "chunk_id": issuer_chunk["chunk_id"],
                        "quote": "平安银行",
                    }],
                }],
                "facts": [{
                    "name": "amount_upper",
                    "raw_value": "1亿元",
                    "evidence": [{
                        "chunk_id": event_chunk["chunk_id"],
                        "quote": "1亿元",
                    }],
                }],
                "dates": [],
                "status": {
                    "raw_value": "审议通过",
                    "evidence": [{
                        "chunk_id": event_chunk["chunk_id"],
                        "quote": "审议通过",
                    }],
                },
            }],
            "no_event_reason": None,
        }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id="request-segment-1",
            response_model=self.identity.model,
            input_tokens=80,
            output_tokens=40,
            total_tokens=120,
            latency_ms=5,
        )


class WholeEventRetryMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        if not self.calls:
            self.calls.append((bundle, response_schema))
            chunk = next(
                item
                for item in bundle.payload["chunks"]
                if "平安银行" in item["text"]
            )
            result = {
                "document_id": bundle.document_id,
                "schema_version": "announcement-mentions-v1-lite",
                "mentions": [
                    {
                        "mention_id": "buyback-incomplete",
                        "event_type": "buyback",
                        "subjects": [
                            {
                                "role": "issuer",
                                "name": "平安银行",
                                "evidence": [
                                    {
                                        "chunk_id": chunk["chunk_id"],
                                        "quote": "平安银行",
                                    }
                                ],
                            }
                        ],
                        "facts": [
                            {
                                "name": "amount_upper",
                                "raw_value": "1亿元",
                                "evidence": [
                                    {
                                        "chunk_id": chunk["chunk_id"],
                                        "quote": "回购金额上限为1亿元",
                                    }
                                ],
                            }
                        ],
                        "dates": [],
                        "status": None,
                    }
                ],
                "no_event_reason": None,
            }
            raw = json.dumps(result, ensure_ascii=False)
            return SemanticProviderResponse(
                identity=self.identity,
                parsed_output=result,
                raw_output=raw,
                input_hash=bundle.artifact_hash,
                output_hash=hashlib.sha256(raw.encode()).hexdigest(),
                request_id="request-incomplete",
                response_model=self.identity.model,
                input_tokens=80,
                output_tokens=30,
                total_tokens=110,
                latency_ms=5,
            )
        return super().extract(bundle, response_schema=response_schema)


class RepairTimeoutMentionProvider(WholeEventRetryMentionProvider):
    def extract(self, bundle, *, response_schema):
        if not self.calls:
            return super().extract(bundle, response_schema=response_schema)
        raise SemanticProviderError("semantic_provider_timeout", retryable=True)


class AlwaysNoEventMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        self.calls.append((bundle, response_schema))
        result = {
            "document_id": bundle.document_id,
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [],
            "no_event_reason": "未发现完整回购事件",
        }
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=bundle.artifact_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id=f"request-no-event-{len(self.calls)}",
            response_model=self.identity.model,
            input_tokens=30,
            output_tokens=10,
            total_tokens=40,
            latency_ms=5,
        )


class OptionalDropMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        response = super().extract(bundle, response_schema=response_schema)
        result = json.loads(json.dumps(response.parsed_output, ensure_ascii=False))
        result["mentions"][0]["facts"].append(
            {
                "name": "purpose",
                "raw_value": "稳定股价",
                "evidence": [
                    {
                        "chunk_id": result["mentions"][0]["subjects"][0]["evidence"][0]["chunk_id"],
                        "quote": "平安银行",
                    }
                ],
            }
        )
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=response.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=response.input_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id=response.request_id,
            response_model=response.response_model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
        )


class MixedValidityMentionProvider(MentionProvider):
    def extract(self, bundle, *, response_schema):
        response = super().extract(bundle, response_schema=response_schema)
        result = json.loads(json.dumps(response.parsed_output, ensure_ascii=False))
        invalid = json.loads(json.dumps(result["mentions"][0], ensure_ascii=False))
        invalid["mention_id"] = "capacity-not-routed"
        invalid["event_type"] = "capacity_project"
        result["mentions"].append(invalid)
        raw = json.dumps(result, ensure_ascii=False)
        return SemanticProviderResponse(
            identity=response.identity,
            parsed_output=result,
            raw_output=raw,
            input_hash=response.input_hash,
            output_hash=hashlib.sha256(raw.encode()).hexdigest(),
            request_id=response.request_id,
            response_model=response.response_model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens,
            latency_ms=response.latency_ms,
        )


class SemanticExchangeTest(unittest.TestCase):
    def test_packet_ir_nodes_are_visible_but_full_ir_only_nodes_are_not(self) -> None:
        packet_ir = {
            "nodes": [
                {"node_id": "packet-table-r1-c1", "node_type": "table_cell"},
            ]
        }

        visible = _packet_visible_evidence_ids({
            "chunks": [{"chunk_id": "packet-text"}],
            "document_ir": packet_ir,
        })

        self.assertEqual(
            visible,
            frozenset({"packet-text", "packet-table-r1-c1"}),
        )

    def test_v10_dividend_dedupe_does_not_require_future_record_date(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v10.json"
        )

        dividend = taxonomy.event("dividend")

        self.assertEqual(
            dividend.dedupe_fields,
            ("subject:issuer", "fact:distribution_period"),
        )
        self.assertEqual(
            dict(dividend.lifecycle_requirements)["completed"].required_dates,
            ("record_date",),
        )

    def test_v21_packet_prioritizes_late_event_evidence_over_early_filler(self) -> None:
        chunks = [
            {
                "chunk_id": "meta-title",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "简式权益变动报告书",
            },
            {
                "chunk_id": "meta-issuer",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "鲁商福瑞达医药股份有限公司",
            },
        ]
        chunks.extend(
            {
                "chunk_id": f"filler-{index}",
                "page_number": 1 + index // 10,
                "section": "body",
                "bbox": [],
                "text": f"声明与释义占位内容第{index}段，本段不包含交易事实。",
            }
            for index in range(80)
        )
        chunks.extend(
            [
                {
                    "chunk_id": "holder-context",
                    "page_number": 9,
                    "section": "body",
                    "bbox": [],
                    "text": "信息披露义务人为山东省国有资产投资控股有限公司。",
                },
                {
                    "chunk_id": "event-fact",
                    "page_number": 9,
                    "section": "body",
                    "bbox": [],
                    "text": "商业集团以协议转让方式转让50,828,500股，占公司总股本的5%。",
                },
                {
                    "chunk_id": "event-status",
                    "page_number": 9,
                    "section": "body",
                    "bbox": [],
                    "text": "本次权益变动尚需履行合规性确认程序。",
                },
            ]
        )
        document = {
            "id": 1,
            "title": "简式权益变动报告书",
            "ts_code": "600223.SH",
            "name": "鲁商福瑞达医药股份有限公司",
            "published_at": "2026-08-09T00:00:00+00:00",
        }
        payload = {
            "document": document,
            "taxonomy_candidates": ["shareholder_change"],
            "entity_whitelist": [],
            "chunks": chunks,
            "tables": [],
            "revision_context": [],
            "mention_templates": [],
            "document_ir": build_document_ir(
                document=document,
                chunks=chunks,
                tables=[],
                parser_version="announcement-layout-v1",
            ),
            "retriever_version": "deterministic-evidence-v1",
        }

        bounded = _bound_v21_payload(payload, max_input_characters=8_000)

        retained = {row["chunk_id"] for row in bounded["chunks"]}
        self.assertIn("holder-context", retained)
        self.assertIn("event-fact", retained)
        self.assertIn("event-status", retained)

    def test_mention_templates_map_capacity_alias_and_expose_requirements(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v7.json"
        )

        capacity = _mention_templates(taxonomy, ["capacity_expansion"])
        shareholder = _mention_templates(taxonomy, ["shareholder_change"])

        self.assertEqual([row["event_type"] for row in capacity], ["capacity_project"])
        self.assertEqual(shareholder[0]["required_all_of"], ["action"])
        self.assertEqual(
            shareholder[0]["required_one_of_sets"],
            [["share_count"], ["share_ratio"]],
        )
        self.assertEqual(
            capacity[0]["requirements_by_lifecycle"]["completed"]["all_of"],
            [],
        )
        self.assertEqual(
            capacity[0]["default_requirements"]["all_of"],
            ["project_type", "expected_operation_date"],
        )
        self.assertNotIn("date:change_date", shareholder[0]["dedupe_fields"])

    def test_v8_taxonomy_accepts_standalone_completed_capacity(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v8.json"
        )
        completed = dict(
            taxonomy.event("capacity_project").lifecycle_requirements
        )["completed"]

        self.assertEqual(completed.all_of, ("project_type",))
        self.assertEqual(completed.inherit_prior, "never")
        self.assertEqual(completed.unmatched_fallback, "not_applicable")
        self.assertIn(
            "交易价格",
            taxonomy.event("merger_restructuring")
            .fact_specs["consideration"]
            .evidence_terms_any,
        )
        self.assertEqual(
            taxonomy.event("merger_restructuring")
            .fact_specs["share_consideration"]
            .allowed_unit_kinds,
            ("currency",),
        )

    def test_completed_capacity_title_cannot_pass_local_no_event_validation(self) -> None:
        requirements = []
        chunks = {
            "c1": {
                "page_number": 1,
                "text": "江陵电厂二期项目已全面建成投产。",
            }
        }

        self.assertEqual(
            _no_event_review_signal(
                "关于江陵电厂二期项目全面建成投产的公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="缺少预计投产日期",
                review_all_title_categories=True,
            ),
            "capacity_project",
        )

    def test_taxonomy_requirements_ignore_coarse_router_labels(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v1.json"
        )

        requirements = _taxonomy_requirements(
            taxonomy,
            ["investigation_penalty", "penalty"],
        )

        self.assertEqual(
            [item["event_type"] for item in requirements],
            ["investigation_penalty"],
        )

    def test_penalty_signal_cannot_finalize_as_no_event(self) -> None:
        requirements = [{"event_type": "investigation_penalty"}]
        chunks = {
            "c1": {
                "page_number": 1,
                "text": "公司收到中国证券监督管理委员会行政处罚决定书。",
            }
        }

        self.assertEqual(
            _no_event_review_signal(
                "关于收到行政处罚决定书的公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="未抽取到完整事件",
            ),
            "investigation_penalty",
        )

    def test_governance_rule_negative_penalty_clause_is_not_an_event_signal(self) -> None:
        requirements = [{"event_type": "investigation_penalty"}]
        chunks = {
            "c1": {
                "page_number": 1,
                "text": "董事会秘书应当具备任职资格，未被中国证监会行政处罚。",
            }
        }

        self.assertIsNone(
            _no_event_review_signal(
                "董事会秘书工作细则（2026年8月修订）",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="仅为内部治理制度的任职资格条款",
            )
        )

    def test_director_qualification_negative_investigation_is_not_a_signal(self) -> None:
        requirements = [{"event_type": "investigation_penalty"}]
        chunks = {
            "c1": {
                "page_number": 1,
                "text": "不存在涉嫌违法违规被中国证监会立案调查的情形，未曾受到行政处罚。",
            }
        }

        self.assertIsNone(
            _no_event_review_signal(
                "关于选举职工代表董事的公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="仅为董事候选人任职资格的否定说明",
            )
        )

    def test_negative_delisting_checkbox_is_not_an_event_signal(self) -> None:
        requirements = [{"event_type": "risk_warning_delisting"}]
        chunks = {
            "c1": {
                "page_number": 4,
                "text": "【重大风险提示】是否存在退市风险 □是 √否",
            }
        }

        self.assertIsNone(
            _no_event_review_signal(
                "2026年半年度报告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="报告明确勾选不存在退市风险",
            )
        )

    def test_star_st_security_name_alone_is_not_a_delisting_signal(self) -> None:
        requirements = [{"event_type": "risk_warning_delisting"}]
        chunks = {
            "meta": {
                "page_number": 1,
                "section": "document_metadata",
                "text": "*ST岭南",
            },
            "header": {
                "page_number": 1,
                "section": "body",
                "text": "证券代码：002717 证券简称：*ST 岭南",
            },
            "body": {
                "page_number": 1,
                "section": "body",
                "text": "本公告仅说明可转债偿付安排。",
            },
        }

        self.assertIsNone(
            _no_event_review_signal(
                "关于岭南转债第四期偿付的提示公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="仅为可转债偿付安排",
            )
        )

    def test_pending_approval_alone_cannot_finalize_merger_no_event(self) -> None:
        requirements = [{"event_type": "merger_restructuring"}]
        chunks = {
            "c1": {
                "page_number": 1,
                "text": "公司拟收购两个标的，交易尚待监管部门审批。",
            }
        }
        self.assertEqual(
            _no_event_review_signal(
                "重大资产重组公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="交易尚待审批，因此未抽取事件",
            ),
            "merger_restructuring",
        )
        self.assertEqual(
            _no_event_review_signal(
                "重大资产重组公告",
                chunks,
                taxonomy_requirements=requirements,
                no_event_reason="仅披露组合对价，缺少逐目标对价",
                review_all_title_categories=True,
            ),
            "merger_restructuring",
        )

    def test_all_title_taxonomy_signals_require_no_event_review(self) -> None:
        self.assertEqual(
            _no_event_review_signal(
                "2025年年度权益分派实施公告",
                {"c1": {"page_number": 1, "text": "每10股派0.40元"}},
                taxonomy_requirements=[{"event_type": "dividend"}],
                no_event_reason="缺少股权登记日",
                review_all_title_categories=True,
            ),
            "dividend",
        )

    def test_no_event_review_gate_applies_only_to_primary_event_documents(self) -> None:
        self.assertTrue(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "event_announcement",
                    "extraction_purpose": "canonical_event",
                }
            })
        )
        self.assertFalse(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                }
            })
        )
        self.assertTrue(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match", "legal_current_event"],
                }
            })
        )
        self.assertFalse(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "supplemental_report",
                    "extraction_purpose": "none",
                }
            })
        )
        self.assertFalse(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "event_announcement",
                    "extraction_purpose": "canonical_event",
                },
                "repair_context": {
                    "validation_error": {
                        "code": "semantic_mentions_all_rejected",
                        "detail": "m1:mention_revision_no_changed_fact",
                    }
                },
            })
        )
        self.assertFalse(
            _requires_no_event_review({
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match", "legal_current_event"],
                },
                "repair_context": {
                    "validation_error": {
                        "code": "no_event_review_required",
                        "detail": "buyback",
                    }
                },
            })
        )
        self.assertTrue(_requires_no_event_review({}))

    def test_only_unchanged_revision_rejections_can_become_no_event(self) -> None:
        self.assertTrue(
            _revision_rejection_can_be_no_event(
                SemanticContractError(
                    "semantic_mentions_all_rejected",
                    detail="m1:mention_revision_no_changed_fact",
                )
            )
        )
        self.assertFalse(
            _revision_rejection_can_be_no_event(
                SemanticContractError(
                    "semantic_mentions_all_rejected",
                    detail="m1:mention_revision_no_changed_fact,table_semantic_label_mismatch",
                )
            )
        )

    def test_nonexplicit_context_repair_can_fail_closed_to_no_event(self) -> None:
        bundle = SemanticInputBundle(
            document_id=73165,
            artifact_hash="a" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v12",
            payload={
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match"],
                }
            },
            input_token_estimate=100,
        )
        explicit_bundle = SemanticInputBundle(
            document_id=bundle.document_id,
            artifact_hash=bundle.artifact_hash,
            parser_version=bundle.parser_version,
            prompt_version=bundle.prompt_version,
            schema_version=bundle.schema_version,
            taxonomy_version=bundle.taxonomy_version,
            payload={
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match", "legal_current_event"],
                }
            },
            input_token_estimate=100,
        )
        context_error = SemanticContractError(
            "semantic_context_current_transition_missing",
            detail="dividend",
        )
        rejected_error = SemanticContractError(
            "semantic_mentions_all_rejected",
            detail="m1:mention_candidate_required_fact_missing",
        )

        self.assertTrue(_context_repair_can_be_no_event(context_error, bundle))
        self.assertTrue(_context_repair_can_be_no_event(rejected_error, bundle))
        self.assertFalse(
            _context_repair_can_be_no_event(context_error, explicit_bundle)
        )
        self.assertFalse(
            _context_repair_can_be_no_event(
                SemanticContractError("semantic_evidence_quote_missing"),
                bundle,
            )
        )
        self.assertFalse(
            _revision_rejection_can_be_no_event(
                SemanticContractError(
                    "semantic_context_current_transition_missing",
                    detail="shareholder_change",
                )
            )
        )

    def test_daily_markdown_counts_current_and_resumed_imports(self) -> None:
        markdown = _render_daily_markdown(
            {
                "prepared": {"job_id": "sj-1", "documents": 3},
                "execution": "partial",
                "quality_status": "partial",
                "imported_existing": [{"valid": 1, "no_event": 1}],
                "import": {"valid": 2, "no_event": 1},
            }
        )

        self.assertIn("已导入返回结果：`5`", markdown)

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._copy_contract_files()
        self.store = IntelligenceStore(
            self.root / "data" / "shared" / "intelligence"
        )
        self.a_share_id = self._seed_document("000001.SZ", "a-share")
        self.b_share_id = self._seed_document("200001.SZ", "b-share")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _copy_contract_files(self) -> None:
        profile_dir = self.root / "configs" / "intelligence_extraction_profiles"
        profile_dir.mkdir(parents=True)
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v1.json",
            self.root / "configs" / "intelligence_event_taxonomy_v1.json",
        )
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v2.json",
            self.root / "configs" / "intelligence_event_taxonomy_v2.json",
        )
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v3.json",
            self.root / "configs" / "intelligence_event_taxonomy_v3.json",
        )
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v4.json",
            self.root / "configs" / "intelligence_event_taxonomy_v4.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_v1.json",
            profile_dir / "a_share_announcement_v1.json",
        )
        prompt_dir = (
            self.root
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
        )
        prompt_dir.mkdir(parents=True)
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v1.md",
            prompt_dir / "semantic_extract_v1.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v2.md",
            prompt_dir / "semantic_extract_v2.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v3.md",
            prompt_dir / "semantic_extract_v3.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v4.md",
            prompt_dir / "semantic_extract_v4.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v5.md",
            prompt_dir / "semantic_extract_v5.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v6.md",
            prompt_dir / "semantic_extract_v6.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v7.md",
            prompt_dir / "semantic_extract_v7.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v8.md",
            prompt_dir / "semantic_extract_v8.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v9.md",
            prompt_dir / "semantic_extract_v9.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v10.md",
            prompt_dir / "semantic_extract_v10.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v11.md",
            prompt_dir / "semantic_extract_v11.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v12.md",
            prompt_dir / "semantic_extract_v12.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_extract_v13.md",
            prompt_dir / "semantic_extract_v13.md",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_mentions_v1.md",
            prompt_dir / "semantic_mentions_v1.md",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v1.json",
            profile_dir / "a_share_announcement_remediation_v1.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v2.json",
            profile_dir / "a_share_announcement_remediation_v2.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v3.json",
            profile_dir / "a_share_announcement_remediation_v3.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v4.json",
            profile_dir / "a_share_announcement_remediation_v4.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v5.json",
            profile_dir / "a_share_announcement_remediation_v5.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v6.json",
            profile_dir / "a_share_announcement_remediation_v6.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v7.json",
            profile_dir / "a_share_announcement_remediation_v7.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_remediation_v8.json",
            profile_dir / "a_share_announcement_remediation_v8.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v1.json",
            profile_dir / "a_share_announcement_mentions_v1.json",
        )
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v9.json",
            self.root / "configs" / "intelligence_event_taxonomy_v9.json",
        )
        shutil.copy(
            ROOT / "configs" / "intelligence_event_taxonomy_v11.json",
            self.root / "configs" / "intelligence_event_taxonomy_v11.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v21.json",
            profile_dir / "a_share_announcement_mentions_v21.json",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v22.json",
            profile_dir / "a_share_announcement_mentions_v22.json",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_mentions_v16.md",
            prompt_dir / "semantic_mentions_v16.md",
        )
        shutil.copy(
            ROOT
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v24.json",
            profile_dir / "a_share_announcement_mentions_v24.json",
        )
        shutil.copy(
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "prompts"
            / "semantic_mentions_v17.md",
            prompt_dir / "semantic_mentions_v17.md",
        )

    def test_profile_controls_routing_audit_sampling(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE documents SET title='其他公告' WHERE id=?",
                (self.a_share_id,),
            )
        profile_path = (
            self.root
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v24.json"
        )
        base = json.loads(profile_path.read_text(encoding="utf-8"))
        for profile_id, rate in (("test-audit-off", 0), ("test-audit-on", 1)):
            payload = {**base, "profile_id": profile_id, "audit_sample_rate": rate}
            (profile_path.parent / f"{profile_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

        off = prepare_job(
            self.root,
            profile_id="test-audit-off",
            limit=1,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-audit-off",
        )
        on = prepare_job(
            self.root,
            profile_id="test-audit-on",
            limit=1,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-audit-on",
        )

        self.assertEqual(off["documents"], 0)
        self.assertEqual(on["documents"], 1)
        manifest = json.loads(
            (Path(on["job_dir"]) / "job.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["items"][0]["route"], "audit_extraction")

    def test_v21_freezes_ir_evidence_and_executor_lineage(self) -> None:
        first = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="coding_plan",
            executor_provider="claude-code",
            executor_model="claude-fable-5",
            executor_client_version="claude-code-provider-v1",
        )
        second = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )

        first_dir = Path(first["job_dir"])
        second_dir = Path(second["job_dir"])
        first_manifest = json.loads(
            (first_dir / "job.json").read_text(encoding="utf-8")
        )
        second_manifest = json.loads(
            (second_dir / "job.json").read_text(encoding="utf-8")
        )
        first_input = json.loads(
            (first_dir / "input.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        second_input = json.loads(
            (second_dir / "input.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )

        self.assertTrue((first_dir / "document_ir.jsonl").is_file())
        self.assertTrue((first_dir / "evidence_packets.jsonl").is_file())
        self.assertEqual(
            first_manifest["document_ir_version"],
            "announcement-document-ir-v1",
        )
        self.assertEqual(
            first_manifest["retriever_version"],
            "deterministic-evidence-v1",
        )
        self.assertEqual(
            first_manifest["compiler_version"],
            "mention-compiler-v3-ir",
        )
        self.assertEqual(
            first_input["semantic_task_id"],
            second_input["semantic_task_id"],
        )
        self.assertNotEqual(
            first_input["execution_job_id"],
            second_input["execution_job_id"],
        )
        self.assertNotEqual(first_manifest["job_id"], second_manifest["job_id"])
        self.assertLessEqual(
            len(
                json.dumps(
                    first_input["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            24_000,
        )
        self.assertEqual(
            first_manifest["executor_binding"]["provider"],
            "claude-code",
        )
        with self.store.connect() as connection:
            task_count = connection.execute(
                "SELECT COUNT(*) FROM semantic_tasks"
            ).fetchone()[0]
            execution_count = connection.execute(
                "SELECT COUNT(*) FROM semantic_execution_jobs"
            ).fetchone()[0]
        self.assertEqual(task_count, 1)
        self.assertEqual(execution_count, 2)

    def test_coding_plan_job_writes_bounded_execution_shards(self) -> None:
        second_document_id = self._seed_document(
            "000002.SZ",
            "a-share-second-shard",
        )
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=20,
            max_input_characters=24_000,
            executor_mode="coding_plan",
            executor_provider="claude",
            executor_model="claude-fable-5",
            executor_client_version="claude-code-test",
        )
        job_dir = Path(prepared["job_dir"])
        shards = json.loads(
            (job_dir / "coding_plan" / "shards.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(shards["contract_version"], "semantic-coding-plan-shards-v1")
        self.assertEqual(shards["total_documents"], 2)
        self.assertEqual(
            sorted(shards["shards"][0]["document_ids"]),
            sorted([self.a_share_id, second_document_id]),
        )
        self.assertTrue(
            (job_dir / "coding_plan" / "input_parts" / "part-0001.jsonl").is_file()
        )
        self.assertTrue(
            (job_dir / "coding_plan" / "document_ir_parts" / "part-0001.jsonl").is_file()
        )
        self.assertTrue((job_dir / "CODING_PLAN.md").is_file())

    def test_coding_plan_collect_validates_before_import_and_allows_one_repair(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=1,
            max_input_characters=24_000,
            executor_mode="coding_plan",
            executor_provider="claude",
            executor_model="claude-fable-5",
            executor_client_version="claude-code-test",
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        item = manifest["items"][0]
        output_dir = job_dir / "coding_plan" / "output_parts"
        output_dir.mkdir(parents=True, exist_ok=True)

        def envelope(result: dict) -> dict:
            return {
                "contract_version": "semantic-extraction-output-v1",
                "document_id": item["document_id"],
                "artifact_hash": item["artifact_hash"],
                "input_hash": item["input_hash"],
                "semantic_task_id": item["semantic_task_id"],
                "execution_job_id": item["execution_job_id"],
                "binding_id": item["binding_id"],
                "executor": {
                    "kind": "coding-plan",
                    "provider": "claude",
                    "model": "claude-fable-5",
                    "client_version": "claude-code-test",
                },
                "usage": {},
                "result": result,
            }

        first_result = {
            "document_id": self.a_share_id,
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [],
            "no_event_reason": "未发现当前事件",
        }
        (output_dir / "part-0001.jsonl").write_text(
            json.dumps(envelope(first_result), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        first = collect_coding_plan_outputs(self.root, job_dir)

        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["failed"], 1)
        self.assertEqual(first["validation_attempt"], 1)
        with self.store.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM semantic_runs").fetchone()[0],
                0,
            )

        valid_result = MentionProvider().extract(
            SemanticInputBundle(
                document_id=self.a_share_id,
                artifact_hash=item["artifact_hash"],
                parser_version=item["parser_version"],
                prompt_version=manifest["prompt_version"],
                schema_version=manifest["schema_version"],
                taxonomy_version=manifest["taxonomy_version"],
                payload=json.loads(
                    (job_dir / "input.jsonl").read_text(encoding="utf-8")
                )["payload"],
            ),
            response_schema={},
        ).parsed_output
        (output_dir / "part-0001.jsonl").write_text(
            json.dumps(envelope(valid_result), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        second = collect_coding_plan_outputs(self.root, job_dir)

        self.assertEqual(second["status"], "ready_to_import", second)
        self.assertEqual(second["valid"], 1)
        self.assertEqual(second["failed"], 0)
        self.assertEqual(second["validation_attempt"], 2)
        compiled = json.loads(
            (job_dir / "output.jsonl").read_text(encoding="utf-8")
        )
        self.assertEqual(compiled["result"]["events"][0]["event_type"], "buyback")

        third_result = dict(first_result)
        third_result["no_event_reason"] = "第三种不同输出"
        (output_dir / "part-0001.jsonl").write_text(
            json.dumps(envelope(third_result), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            SemanticExchangeError,
            "semantic_coding_plan_repair_limit_exceeded",
        ):
            collect_coding_plan_outputs(self.root, job_dir)

        imported = import_job(self.root, job_dir)
        self.assertEqual(imported["valid"], 1, imported)
        with self.store.connect() as connection:
            semantic_run = connection.execute(
                "SELECT provider, model, status FROM semantic_runs"
            ).fetchone()
        self.assertEqual(
            tuple(semantic_run),
            ("claude", "claude-fable-5", "succeeded"),
        )

    def test_v21_rejects_runtime_executor_mismatch_before_call(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="coding_plan",
            executor_provider="claude-code",
            executor_model="claude-fable-5",
            executor_client_version="claude-code-provider-v1",
        )
        provider = MentionProvider()

        with self.assertRaises(SemanticExchangeError) as raised:
            run_job(self.root, prepared["job_dir"], provider=provider)

        self.assertEqual(
            str(raised.exception),
            "semantic_executor_identity_mismatch",
        )
        self.assertEqual(provider.calls, [])

    def test_v21_runner_persists_bound_ids_and_accepts_validated_job(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )

        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=MentionProvider(),
        )

        self.assertEqual(report["status"], "complete", report)
        output = json.loads(
            (Path(prepared["job_dir"]) / "output.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertTrue(output["semantic_task_id"].startswith("st-"))
        self.assertTrue(output["execution_job_id"].startswith("sej-"))
        self.assertEqual(output["binding_id"], report["executor"]["binding_id"])
        with self.store.connect() as connection:
            state = connection.execute(
                """
                SELECT status FROM semantic_execution_jobs
                WHERE execution_job_id=?
                """,
                (output["execution_job_id"],),
            ).fetchone()[0]
        self.assertEqual(state, "accepted")
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["valid"], 1, imported)

    def test_v21_retry_reemits_the_complete_event_candidate(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )
        provider = WholeEventRetryMentionProvider()

        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(report["validation_repairs"], 1)
        self.assertEqual(len(provider.calls), 2)
        repair = provider.calls[1][0].payload["repair_context"]
        self.assertEqual(repair["repair_scope"], "complete_event_candidate")
        self.assertEqual(
            repair["previous_output"]["mentions"][0]["mention_id"],
            "buyback-incomplete",
        )
        self.assertIn("complete JSON object", repair["instruction"])

    def test_v21_retryable_repair_failure_can_resume_same_execution(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )
        job_dir = Path(prepared["job_dir"])
        execution_id = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )["items"][0]["execution_job_id"]

        first = run_job(
            self.root,
            job_dir,
            provider=RepairTimeoutMentionProvider(),
        )

        self.assertEqual(first["status"], "partial")
        with self.store.connect() as connection:
            first_state = connection.execute(
                "SELECT status FROM semantic_execution_jobs WHERE execution_job_id=?",
                (execution_id,),
            ).fetchone()[0]
        self.assertEqual(first_state, "retry_wait")
        self.assertFalse((job_dir / "quarantine.jsonl").exists())

        resumed = run_job(self.root, job_dir, provider=MentionProvider())

        self.assertEqual(resumed["status"], "complete")
        with self.store.connect() as connection:
            final_state = connection.execute(
                "SELECT status FROM semantic_execution_jobs WHERE execution_job_id=?",
                (execution_id,),
            ).fetchone()[0]
        self.assertEqual(final_state, "accepted")

    def test_v21_resume_reuses_bounded_reviewed_no_event(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )
        job_dir = Path(prepared["job_dir"])
        first_provider = AlwaysNoEventMentionProvider()
        first = run_job(self.root, job_dir, provider=first_provider)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(len(first_provider.calls), 2)
        self.assertFalse((job_dir / "quarantine.jsonl").exists())

        resumed_provider = AlwaysNoEventMentionProvider()
        resumed = run_job(self.root, job_dir, provider=resumed_provider)

        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["reused"], 1)
        self.assertEqual(resumed_provider.calls, [])

    def test_v21_accepts_valid_core_after_deterministic_optional_prune(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )

        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=OptionalDropMentionProvider(),
        )

        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(report["deterministic_optional_fact_prunes"], 1)
        self.assertEqual(report["mention_compilation"]["accepted"], 1)
        self.assertEqual(report["mention_compilation"]["dropped_items"], 1)

    def test_v21_keeps_valid_mentions_when_a_sibling_is_rejected(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )

        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=MixedValidityMentionProvider(),
        )

        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(report["validation_repairs"], 0)
        self.assertEqual(report["mention_compilation"]["accepted"], 1)
        self.assertEqual(report["mention_compilation"]["rejected"], 1)
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["valid"], 1, imported)

    def test_v7_remediation_profile_is_repair_only(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v7")
        self.assertTrue(profile["repair_only"])

    def test_v8_remediation_profile_is_repair_only(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v3.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v8")
        self.assertTrue(profile["repair_only"])

    def test_v9_remediation_profile_is_repair_only(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v4.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v9")
        self.assertTrue(profile["repair_only"])

    def test_v10_remediation_profile_is_repair_only(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v5.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v10")
        self.assertTrue(profile["repair_only"])

    def test_v11_remediation_profile_carries_typed_fact_contracts(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v6.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v11")
        self.assertEqual(
            profile["taxonomy_version"],
            "cn-announcement-taxonomy-v2",
        )
        self.assertTrue(profile["repair_only"])

        taxonomy = EventTaxonomy.load(
            self.root / "configs" / "intelligence_event_taxonomy_v2.json"
        )
        requirement = next(
            row
            for row in _taxonomy_requirements(
                taxonomy,
                ["capacity_project"],
            )
            if row["event_type"] == "capacity_project"
        )
        self.assertEqual(
            requirement["fact_specs"]["expected_revenue"],
            {
                "value_type": "number",
                "allowed_unit_kinds": ["currency"],
                "evidence_terms_any": ["营业收入", "销售收入", "收入"],
            },
        )

    def test_v12_remediation_profile_versions_multichunk_contract(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v7.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v12")
        self.assertEqual(
            profile["taxonomy_version"],
            "cn-announcement-taxonomy-v3",
        )
        self.assertEqual(
            profile["evidence_contract"],
            "typed-multichunk-verbatim-v3",
        )
        self.assertTrue(profile["repair_only"])

        taxonomy = EventTaxonomy.load(
            self.root / "configs" / "intelligence_event_taxonomy_v3.json"
        )
        litigation = taxonomy.event("litigation_arbitration")
        self.assertEqual(
            litigation.dedupe_fields,
            (
                "subject:issuer",
                "subject:counterparty",
                "fact:case_amount",
            ),
        )
        dividend = taxonomy.event("dividend")
        self.assertEqual(
            dividend.fact_specs["stock_per_share"].value_type,
            "ratio",
        )

    def test_v13_remediation_profile_enforces_scalar_economics(self) -> None:
        profile = json.loads(
            (
                self.root
                / "configs"
                / "intelligence_extraction_profiles"
                / "a_share_announcement_remediation_v8.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-extract-v13")
        self.assertEqual(
            profile["taxonomy_version"],
            "cn-announcement-taxonomy-v3",
        )
        self.assertEqual(
            profile["evidence_contract"],
            "typed-scalar-multichunk-verbatim-v4",
        )
        self.assertTrue(profile["repair_only"])

    def test_mention_profile_prepares_compact_compiler_input(self) -> None:
        prepared = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="mention compiler canary",
            profile_id="a-share-announcement-mentions-v1",
        )
        job_dir = Path(prepared["job_dir"])
        schema = json.loads((job_dir / "schema.json").read_text(encoding="utf-8"))
        row = json.loads(
            (job_dir / "input.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )

        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "announcement-mentions-v1-lite",
        )
        self.assertNotIn("taxonomy_requirements", row["payload"])
        templates = row["payload"]["mention_templates"]
        self.assertEqual([item["event_type"] for item in templates], ["buyback"])
        self.assertIn("issuer", templates[0]["subject_roles"])
        self.assertIn("amount_lower", templates[0]["fact_names"])
        self.assertIn("approval_date", templates[0]["date_kinds"])
        self.assertLess(len(json.dumps(templates, ensure_ascii=False)), 5_000)

    def test_mention_job_records_prompt_and_compiler_versions(self) -> None:
        prepared = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="mention compiler canary",
            profile_id="a-share-announcement-mentions-v1",
        )
        manifest = json.loads(
            (Path(prepared["job_dir"]) / "job.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["prompt_version"], "semantic-mentions-v1")
        self.assertEqual(manifest["compiler_version"], "mention-compiler-v1")

    def test_mention_profile_rejects_unavailable_compiler_version(self) -> None:
        path = (
            self.root
            / "configs"
            / "intelligence_extraction_profiles"
            / "a_share_announcement_mentions_v1.json"
        )
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["compiler_version"] = "mention-compiler-retired"
        path.write_text(json.dumps(profile), encoding="utf-8")

        with self.assertRaises(SemanticExchangeError) as raised:
            prepare_repair_job(
                self.root,
                document_ids=[self.a_share_id],
                reason="compiler mismatch",
                profile_id="a-share-announcement-mentions-v1",
            )
        self.assertEqual(
            str(raised.exception),
            "semantic_profile_compiler_version_mismatch",
        )

    def test_mention_runner_archives_source_and_emits_importable_events(self) -> None:
        prepared = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="mention compiler canary",
            profile_id="a-share-announcement-mentions-v1",
        )
        job_dir = Path(prepared["job_dir"])

        report = run_job(self.root, job_dir, provider=MentionProvider())

        self.assertEqual(report["status"], "complete", report)
        output = json.loads(
            (job_dir / "output.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(
            output["result"]["schema_version"],
            "announcement-events-v1-lite",
        )
        self.assertEqual(output["result"]["events"][0]["event_type"], "buyback")
        source = json.loads(
            (job_dir / "mention_output.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[0]
        )
        self.assertEqual(source["result"]["mentions"][0]["mention_id"], "buyback-1")
        imported = import_job(self.root, job_dir)
        self.assertEqual(imported["valid"], 1, imported)

    def test_v24_family_repair_merges_with_first_valid_mention(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE documents SET title=? WHERE id=?",
                (
                    "关于回购股份暨股东增持的公告",
                    self.a_share_id,
                ),
            )
            row = connection.execute(
                "SELECT text FROM document_chunks WHERE chunk_id=?",
                (f"chunk-{self.a_share_id}",),
            ).fetchone()
            text = str(row[0]) + "股东增持100万股。"
            connection.execute(
                "UPDATE document_chunks SET text=?, text_hash=? WHERE chunk_id=?",
                (
                    text,
                    hashlib.sha256(text.encode()).hexdigest(),
                    f"chunk-{self.a_share_id}",
                ),
            )
        provider = IncrementalFamilyMentionProvider()
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=1,
            executor_mode="api",
            executor_provider=provider.identity.provider,
            executor_model=provider.identity.model,
            executor_client_version=provider.identity.client_version,
        )
        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(report["failed"], 0, report)
        self.assertEqual(report["validation_repairs"], 1)
        output = json.loads(
            (Path(prepared["job_dir"]) / "output.jsonl").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {event["event_type"] for event in output["result"]["events"]},
            {"buyback", "shareholder_change"},
        )
        source = json.loads(
            (Path(prepared["job_dir"]) / "mention_output.jsonl").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {mention["event_type"] for mention in source["result"]["mentions"]},
            {"buyback", "shareholder_change"},
        )
        self.assertEqual(len(source["provider_attempts"]), 2)
        self.assertEqual(
            {
                mention["event_type"]
                for attempt in source["provider_attempts"]
                for mention in attempt["result"]["mentions"]
            },
            {"buyback", "shareholder_change"},
        )

    def test_failed_explicit_mention_canary_does_not_write_semantic_runs(self) -> None:
        prepared = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="isolated mention canary",
            profile_id="a-share-announcement-mentions-v1",
        )

        report = run_job(
            self.root,
            prepared["job_dir"],
            provider=GroundingRepairProvider(repair_succeeds=False),
        )

        self.assertEqual(report["status"], "partial", report)
        with self.store.connect() as connection:
            run_count = connection.execute(
                "SELECT COUNT(*) FROM semantic_runs"
            ).fetchone()[0]
        self.assertEqual(run_count, 0)

    def test_runner_prunes_only_ungrounded_optional_facts(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v1.json"
        )
        result = {
            "document_id": 258827,
            "schema_version": "announcement-events-v1-lite",
            "events": [
                {
                    "event_type": "investigation_penalty",
                    "lifecycle": "completed",
                    "subjects": [
                        {
                            "entity_id": "600378.SH",
                            "role": "issuer",
                            "evidence_ids": ["e1"],
                        },
                        {
                            "entity_id": "external:中国证券监督管理委员会",
                            "role": "authority",
                            "evidence_ids": ["e2"],
                        },
                    ],
                    "facts": [
                        {
                            "name": "action_type",
                            "raw_value": "罚款",
                            "numeric_value": None,
                            "unit": None,
                            "currency": None,
                            "period": None,
                            "evidence_ids": ["e3"],
                        },
                        {
                            "name": "document_number",
                            "raw_value": "证监罚字〔2007〕12号",
                            "numeric_value": None,
                            "unit": None,
                            "currency": None,
                            "period": None,
                            "evidence_ids": ["e4"],
                        },
                        {
                            "name": "reason",
                            "raw_value": "跨块重建的句子",
                            "numeric_value": None,
                            "unit": None,
                            "currency": None,
                            "period": None,
                            "evidence_ids": ["e5"],
                        },
                    ],
                    "effective_dates": [],
                    "conditions": [],
                    "conflicts": [],
                    "missing_required_fields": [],
                }
            ],
            "evidence": [
                {"evidence_id": "e1", "chunk_id": "c1", "quote": "天科股份"},
                {
                    "evidence_id": "e2",
                    "chunk_id": "c2",
                    "quote": "中国证券监督管理委员会",
                },
                {"evidence_id": "e3", "chunk_id": "c3", "quote": "罚款"},
                {
                    "evidence_id": "e4",
                    "chunk_id": "c4",
                    "quote": "证监罚字〔2007〕12号",
                },
                {
                    "evidence_id": "e5",
                    "chunk_id": "c5",
                    "quote": "跨块重建的句子",
                },
            ],
            "no_event_reason": None,
        }
        normalized, pruned = _prune_ungrounded_optional_facts(
            result,
            taxonomy=taxonomy,
            chunks={
                "c1": "天科股份",
                "c2": "中国证券监督管理委员会",
                "c3": "罚款",
                "c4": "证监罚字〔2007〕12号",
                "c5": "原始句子分散在两个块中",
            },
        )
        self.assertEqual(pruned, 1)
        self.assertEqual(
            [fact["name"] for fact in normalized["events"][0]["facts"]],
            ["action_type", "document_number"],
        )
        self.assertNotIn(
            "e5",
            [item["evidence_id"] for item in normalized["evidence"]],
        )

    def test_runner_prunes_ambiguous_optional_numeric_fact(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v3.json"
        )
        result = {
            "document_id": 106298,
            "schema_version": "announcement-events-v1-lite",
            "events": [
                {
                    "event_type": "litigation_arbitration",
                    "lifecycle": "completed",
                    "subjects": [
                        {"entity_id": "000557.SZ", "role": "issuer", "evidence_ids": ["e1"]},
                        {"entity_id": "external:中国工商银行银川市西城支行", "role": "counterparty", "evidence_ids": ["e2"]},
                    ],
                    "facts": [
                        {"name": "issuer_role", "raw_value": "偿还", "numeric_value": None, "unit": None, "currency": None, "period": None, "evidence_ids": ["e3"]},
                        {"name": "case_amount", "raw_value": "10,200万元", "numeric_value": None, "unit": "万元", "currency": None, "period": None, "evidence_ids": ["e4"]},
                        {"name": "case_stage", "raw_value": "判令", "numeric_value": None, "unit": None, "currency": None, "period": None, "evidence_ids": ["e5"]},
                        {"name": "judgment_amount", "raw_value": "10,200万元及利息139.8万元", "numeric_value": None, "unit": "万元", "currency": None, "period": None, "evidence_ids": ["e6"]},
                    ],
                    "effective_dates": [],
                    "conditions": [],
                    "conflicts": [],
                    "missing_required_fields": [],
                }
            ],
            "evidence": [
                {"evidence_id": "e1", "chunk_id": "c1", "quote": "广夏银川实业股份有限公司"},
                {"evidence_id": "e2", "chunk_id": "c2", "quote": "中国工商银行银川市西城支行"},
                {"evidence_id": "e3", "chunk_id": "c3", "quote": "偿还"},
                {"evidence_id": "e4", "chunk_id": "c4", "quote": "10,200万元"},
                {"evidence_id": "e5", "chunk_id": "c5", "quote": "判令"},
                {"evidence_id": "e6", "chunk_id": "c6", "quote": "10,200万元及利息139.8万元"},
            ],
            "no_event_reason": None,
        }
        normalized, pruned = _prune_ungrounded_optional_facts(
            result,
            taxonomy=taxonomy,
            chunks={f"c{index}": item["quote"] for index, item in enumerate(result["evidence"], start=1)},
        )

        self.assertEqual(pruned, 1)
        self.assertEqual(
            [fact["name"] for fact in normalized["events"][0]["facts"]],
            ["issuer_role", "case_amount", "case_stage"],
        )

    def test_ambiguous_quote_repair_requires_unique_context(self) -> None:
        bundle = SemanticInputBundle(
            document_id=111079,
            artifact_hash="a" * 64,
            parser_version="announcement-layout-v1",
            prompt_version="semantic-extract-v9",
            schema_version="announcement-events-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v1",
            payload={
                "chunks": [
                    {
                        "chunk_id": "c1",
                        "page_number": 1,
                        "text": "2001年度分红方案；2001年度派息日期。",
                    }
                ]
            },
            input_token_estimate=100,
        )
        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 111079},
            error=SemanticContractError(
                "semantic_evidence_quote_ambiguous",
                detail="c1",
            ),
        )
        instruction = repaired.payload["repair_context"]["instruction"]
        self.assertIn("appears more than once", instruction)
        self.assertIn("expand the quote", instruction)

    def test_explicit_legal_current_repair_accepts_implementation_progress(self) -> None:
        bundle = SemanticInputBundle(
            document_id=72776,
            artifact_hash="b" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v12",
            payload={
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match", "legal_current_event"],
                },
                "chunks": [],
            },
            input_token_estimate=100,
        )
        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 72776},
            error=SemanticContractError(
                "no_event_review_required",
                detail="buyback",
            ),
        )

        instruction = repaired.payload["repair_context"]["instruction"]
        self.assertIn("explicit current-action signal", instruction)
        self.assertIn("does not require a new program", instruction)

    def test_lossy_mention_repair_restates_subject_and_table_contracts(self) -> None:
        bundle = SemanticInputBundle(
            document_id=73850,
            artifact_hash="b" * 64,
            parser_version="announcement-layout-v1",
            prompt_version="semantic-mentions-v11",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v8",
            payload={"chunks": [], "document": {"name": "华电新能"}},
            input_token_estimate=100,
        )

        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 73850},
            error=SemanticContractError(
                "semantic_mentions_lossy_compilation",
                detail="m1:mention_candidate_entity_not_whitelisted",
            ),
        )
        instruction = repaired.payload["repair_context"]["instruction"]

        self.assertIn("may be a security abbreviation", instruction)
        self.assertIn("exact full legal company name", instruction)
        self.assertIn("exact-name-only", instruction)
        self.assertIn("subject_roles", instruction)
        self.assertIn("fact_names", instruction)
        self.assertIn("incomplete secondary mention", instruction)

    def test_revision_repair_points_executor_to_current_section(self) -> None:
        bundle = SemanticInputBundle(
            document_id=322230,
            artifact_hash="c" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v11",
            payload={"chunks": [], "document": {"name": "氯碱化工"}},
            input_token_estimate=100,
        )

        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 322230},
            error=SemanticContractError(
                "semantic_mentions_all_rejected",
                detail="m1:mention_revision_uses_superseded_value",
            ),
        )
        instruction = repaired.payload["repair_context"]["instruction"]

        self.assertIn("原来披露", instruction)
        self.assertIn("更正后", instruction)
        self.assertIn("只输出更正后的值", instruction)

    def test_multi_family_result_gets_one_bounded_coverage_repair(self) -> None:
        bundle = SemanticInputBundle(
            document_id=114674,
            artifact_hash="d" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v11",
            payload={
                "taxonomy_candidates": [
                    "earnings_forecast",
                    "litigation_arbitration",
                ],
                "route_context": {
                    "document_kind": "event_announcement",
                    "extraction_purpose": "canonical_event",
                },
            },
            input_token_estimate=100,
        )
        result = {
            "events": [{"event_type": "litigation_arbitration"}],
        }

        self.assertEqual(
            _missing_routed_event_types(result, bundle),
            ("earnings_forecast",),
        )
        legal_bundle = SemanticInputBundle(
            document_id=bundle.document_id,
            artifact_hash=bundle.artifact_hash,
            parser_version=bundle.parser_version,
            prompt_version=bundle.prompt_version,
            schema_version=bundle.schema_version,
            taxonomy_version=bundle.taxonomy_version,
            payload={
                **bundle.payload,
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                },
            },
            input_token_estimate=bundle.input_token_estimate,
        )
        self.assertEqual(
            _missing_routed_event_types(result, legal_bundle),
            ("earnings_forecast",),
        )
        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 114674},
            error=SemanticContractError(
                "semantic_candidate_family_unreviewed",
                detail="earnings_forecast",
            ),
        )
        instruction = repaired.payload["repair_context"]["instruction"]
        self.assertIn("earnings_forecast", instruction)
        self.assertIn("Do not fabricate", instruction)

    def test_context_merger_requires_current_transition_not_generic_title(self) -> None:
        bundle = SemanticInputBundle(
            document_id=224790,
            artifact_hash="e" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v12",
            payload={
                "route_context": {
                    "document_kind": "supplemental_report",
                    "extraction_purpose": "canonical_event",
                },
            },
            input_token_estimate=100,
        )
        generic = {
            "events": [{
                "event_type": "merger_restructuring",
                "lifecycle": "uncertain",
                "facts": [{"name": "transaction_type", "raw_value": "资产置换"}],
                "effective_dates": [],
            }],
        }
        revised = {
            "events": [{
                **generic["events"][0],
                "lifecycle": "revised",
            }],
        }

        self.assertEqual(
            _context_events_missing_current_transition(generic, bundle),
            ("merger_restructuring",),
        )
        self.assertEqual(
            _context_events_missing_current_transition(revised, bundle),
            (),
        )
        repaired = _grounding_repair_bundle(
            bundle,
            previous_result={"document_id": 224790},
            error=SemanticContractError(
                "semantic_context_current_transition_missing",
                detail="merger_restructuring",
            ),
        )
        instruction = repaired.payload["repair_context"]["instruction"]
        self.assertIn("current transition", instruction)
        self.assertIn("return no_event", instruction)

    def test_context_documents_require_revision_or_explicit_current_route(self) -> None:
        base_bundle = SemanticInputBundle(
            document_id=73165,
            artifact_hash="f" * 64,
            parser_version="anchor-workbench-v1",
            prompt_version="semantic-mentions-v17",
            schema_version="announcement-mentions-v1-lite",
            taxonomy_version="cn-announcement-taxonomy-v12",
            payload={
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match"],
                },
            },
            input_token_estimate=100,
        )
        repeated_dividend = {
            "events": [{
                "event_type": "dividend",
                "lifecycle": "uncertain",
                "facts": [{"name": "cash_per_share", "raw_value": "0.10元/股"}],
                "effective_dates": [],
            }],
        }
        historical_completion = {
            "events": [{
                "event_type": "merger_restructuring",
                "lifecycle": "completed",
                "facts": [{"name": "transaction_type", "raw_value": "资产置换"}],
                "effective_dates": [],
            }],
        }
        explicit_bundle = SemanticInputBundle(
            document_id=72776,
            artifact_hash=base_bundle.artifact_hash,
            parser_version=base_bundle.parser_version,
            prompt_version=base_bundle.prompt_version,
            schema_version=base_bundle.schema_version,
            taxonomy_version=base_bundle.taxonomy_version,
            payload={
                "route_context": {
                    "document_kind": "legal_opinion",
                    "extraction_purpose": "canonical_event",
                    "reason_codes": ["title_taxonomy_match", "legal_current_event"],
                },
            },
            input_token_estimate=100,
        )

        self.assertEqual(
            _context_events_missing_current_transition(repeated_dividend, base_bundle),
            ("dividend",),
        )
        self.assertEqual(
            _context_events_missing_current_transition(historical_completion, base_bundle),
            ("merger_restructuring",),
        )
        self.assertEqual(
            _context_events_missing_current_transition(repeated_dividend, explicit_bundle),
            (),
        )

    def test_repair_job_is_explicit_and_carries_prior_run_provenance(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        item = next(
            row for row in manifest["items"]
            if row["document_id"] == self.a_share_id
        )
        self._write_output(
            job_dir,
            item,
            {
                "document_id": self.a_share_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no supported event",
            },
        )
        imported = import_job(self.root, job_dir)
        self.assertEqual(imported["no_event"], 1, imported)

        repaired = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="regulatory-quality-remediation-2026-08-01",
            profile_id="a-share-announcement-remediation-v1",
        )
        repair_dir = Path(repaired["job_dir"])
        repair_manifest = json.loads(
            (repair_dir / "job.json").read_text(encoding="utf-8")
        )
        repair_input = json.loads(
            (repair_dir / "input.jsonl").read_text(encoding="utf-8")
        )

        self.assertEqual(repair_manifest["selection_policy"], "explicit-document-ids-v1")
        self.assertEqual(repair_manifest["repair_contract_version"], "semantic-repair-v1")
        self.assertEqual(
            [row["document_id"] for row in repair_manifest["items"]],
            [self.a_share_id],
        )
        context = repair_input["payload"]["repair_context"]
        self.assertEqual(context["repair_id"], repair_manifest["repair_id"])
        self.assertEqual(
            context["reason"],
            "regulatory-quality-remediation-2026-08-01",
        )
        self.assertEqual(len(context["superseded_runs"]), 1)
        self.assertEqual(context["superseded_runs"][0]["status"], "no_event")

        repair_item = repair_manifest["items"][0]
        self._write_output(
            repair_dir,
            repair_item,
            {
                "document_id": self.a_share_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "reviewed against the full document",
            },
        )
        repaired_import = import_job(self.root, repair_dir)
        self.assertEqual(repaired_import["no_event"], 1, repaired_import)
        self.assertEqual(repaired_import["repairs_activated"], 1, repaired_import)
        with self.store.connect() as connection:
            replacement = connection.execute(
                "SELECT * FROM semantic_run_replacements"
            ).fetchone()
        self.assertEqual(replacement["repair_id"], repair_manifest["repair_id"])
        self.assertEqual(replacement["status"], "active")

    def test_late_stale_repair_is_quarantined_without_blocking_import(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        initial_dir = Path(prepared["job_dir"])
        initial_item = next(
            row
            for row in json.loads(
                (initial_dir / "job.json").read_text(encoding="utf-8")
            )["items"]
            if row["document_id"] == self.a_share_id
        )
        no_event = {
            "document_id": self.a_share_id,
            "schema_version": "announcement-events-v1-lite",
            "events": [],
            "evidence": [],
            "no_event_reason": "no supported event",
        }
        self._write_output(initial_dir, initial_item, no_event)
        self.assertEqual(import_job(self.root, initial_dir)["no_event"], 1)

        first = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="newer-reviewed-repair",
            profile_id="a-share-announcement-remediation-v1",
        )
        stale = prepare_repair_job(
            self.root,
            document_ids=[self.a_share_id],
            reason="older-job-imported-late",
            profile_id="a-share-announcement-remediation-v1",
        )
        first_dir = Path(first["job_dir"])
        stale_dir = Path(stale["job_dir"])
        for job_dir in (first_dir, stale_dir):
            item = json.loads(
                (job_dir / "job.json").read_text(encoding="utf-8")
            )["items"][0]
            self._write_output(job_dir, item, no_event)

        self.assertEqual(import_job(self.root, first_dir)["repairs_activated"], 1)
        stale_report = import_job(self.root, stale_dir)

        self.assertEqual(stale_report["quarantined"], 1)
        self.assertEqual(stale_report["errors"][0]["error"], "semantic_repair_superseded")
        self.assertEqual(job_status(self.root, stale_dir)["status"], "quarantined")

    def _seed_document(self, ts_code: str, source_id: str) -> int:
        document_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id=source_id,
                title="关于以集中竞价方式回购股份的公告",
                published_at="2026-07-28T09:00:00+08:00",
                first_seen_at="2026-07-28T09:01:00+08:00",
                effective_at="2026-07-28T09:01:00+08:00",
                source_url="https://static.cninfo.com.cn/example.pdf",
                content=f"metadata:{source_id}".encode(),
                metadata={
                    "ts_code": ts_code,
                    "name": source_id,
                    "security_links": [
                        {
                            "ts_code": ts_code,
                            "name": source_id,
                            "provenance": "anns_d",
                        }
                    ],
                },
            )
        )
        artifact_hash = hashlib.sha256(
            f"artifact:{document_id}".encode()
        ).hexdigest()
        text = (
            "平安银行董事会于2026年7月28日审议通过回购方案，"
            "回购金额上限为1亿元，"
            "回购价格不超过10元/股。"
        )
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(?, ?, 'parsed', ?, ?, 'application/json', 100,
                         'announcement-layout-v1', 'parsed', '', ?, ?)
                """,
                (
                    f"parsed-{document_id}",
                    document_id,
                    artifact_hash,
                    f"localblob://parsed/{artifact_hash}",
                    "2026-07-28T01:02:00+00:00",
                    "2026-07-28T01:02:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no,
                    page_number, section, bbox_json, text, text_hash,
                    ocr_used, ocr_confidence, parser_version
                ) VALUES(?, ?, ?, 0, 1, 'body', '[]', ?, ?, 0, NULL,
                         'announcement-layout-v1')
                """,
                (
                    f"chunk-{document_id}",
                    document_id,
                    f"parsed-{document_id}",
                    text,
                    hashlib.sha256(text.encode()).hexdigest(),
                ),
            )
            connection.execute(
                """
                INSERT INTO document_tables(
                    table_id, document_id, artifact_id, page_number,
                    sequence_no, bbox_json, cells_json, parser_version
                ) VALUES(?, ?, ?, 1, 0, '[]', ?, 'announcement-layout-v1')
                """,
                (
                    f"table-{document_id}",
                    document_id,
                    f"parsed-{document_id}",
                    json.dumps(
                        [
                            {
                                "row_index": 0,
                                "column_index": 0,
                                "text": "2026年7月28日",
                                "bbox": [0, 0, 10, 10],
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
        return document_id

    def _seed_risk_warning_document(self) -> int:
        document_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id="risk-warning",
                title="关于公司股票实行退市风险警示的公告",
                published_at="2026-05-08T09:00:00+08:00",
                first_seen_at="2026-05-08T09:01:00+08:00",
                effective_at="2026-05-08T09:01:00+08:00",
                source_url="https://static.cninfo.com.cn/risk.pdf",
                content=b"metadata:risk-warning",
                metadata={
                    "ts_code": "000001.SZ",
                    "name": "示例退市风险",
                    "security_links": [
                        {
                            "ts_code": "000001.SZ",
                            "name": "示例退市风险",
                            "provenance": "anns_d",
                        }
                    ],
                },
            )
        )
        artifact_hash = hashlib.sha256(
            f"artifact:{document_id}:risk".encode()
        ).hexdigest()
        text = (
            "公司股票交易自2026年5月8日起实行退市风险警示的特别处理，"
            "股票简称变更为*ST示例。"
        )
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO document_artifacts(
                    artifact_id, document_id, artifact_type, content_hash,
                    storage_uri, mime_type, byte_size, parser_version,
                    status, error, created_at, updated_at
                ) VALUES(?, ?, 'parsed', ?, ?, 'application/json', 100,
                         'announcement-layout-v1', 'parsed', '', ?, ?)
                """,
                (
                    f"parsed-{document_id}-risk",
                    document_id,
                    artifact_hash,
                    f"localblob://parsed/{artifact_hash}",
                    "2026-05-08T01:02:00+00:00",
                    "2026-05-08T01:02:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no,
                    page_number, section, bbox_json, text, text_hash,
                    ocr_used, ocr_confidence, parser_version
                ) VALUES(?, ?, ?, 0, 1, 'body', '[]', ?, ?, 0, NULL,
                         'announcement-layout-v1')
                """,
                (
                    f"chunk-{document_id}-risk",
                    document_id,
                    f"parsed-{document_id}-risk",
                    text,
                    hashlib.sha256(text.encode()).hexdigest(),
                ),
            )
        return document_id

    def test_prepare_is_idempotent_and_excludes_b_share(self) -> None:
        first = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        second = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["status"], "prepared")
        self.assertEqual(first["documents"], 1)
        job_dir = Path(first["job_dir"])
        self.assertTrue((job_dir / "job.json").exists())
        self.assertTrue((job_dir / "prompt.md").exists())
        self.assertTrue((job_dir / "profile.json").exists())
        self.assertTrue((job_dir / "schema.json").exists())
        self.assertTrue((job_dir / "taxonomy.json").exists())
        rows = [
            json.loads(line)
            for line in (job_dir / "input.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual([row["document_id"] for row in rows], [self.a_share_id])
        self.assertEqual(
            rows[0]["payload_contract_version"],
            "semantic-payload-v3",
        )
        self.assertEqual(rows[0]["payload"]["tables"], [])
        requirements = rows[0]["payload"]["taxonomy_requirements"]
        self.assertEqual(
            [item["event_type"] for item in requirements],
            ["buyback"],
        )
        self.assertEqual(
            requirements[0]["required_subject_roles"],
            ["issuer"],
        )
        self.assertIn(
            "default",
            requirements[0]["required_facts"],
        )
        self.assertIn(
            "by_lifecycle",
            requirements[0]["required_facts"],
        )
        self.assertEqual(
            requirements[0]["dedupe_fields"],
            [
                "subject:issuer",
                "date:approval_date",
                "fact:price_cap",
            ],
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    rows[0]["payload"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            40_000,
        )
        self.assertNotIn(
            self.b_share_id,
            [row["document_id"] for row in rows],
        )

    def test_prepare_prioritizes_latest_model_universe(self) -> None:
        older_priority_id = self._seed_document(
            "600001.SH",
            "older-priority-a-share",
        )
        priority_id = self._seed_document("600000.SH", "priority-a-share")
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE documents SET published_at=? WHERE id=?",
                ("2020-01-01T01:00:00+00:00", older_priority_id),
            )
        research = ResearchStore(self.root / "data" / "research")
        research.write_feature_snapshot(
            "a_share",
            "20260728",
            pd.DataFrame(
                {
                    "code": ["600000", "600001"],
                    "trade_date": ["20260728", "20260728"],
                }
            ),
        )
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=1,
        )
        rows = [
            json.loads(line)
            for line in (
                Path(prepared["job_dir"]) / "input.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual(
            [row["document_id"] for row in rows],
            [priority_id],
        )

    def test_external_no_event_output_imports_without_provider_config(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = manifest["items"][0]
        output = {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": item["document_id"],
            "artifact_hash": item["artifact_hash"],
            "input_hash": item["input_hash"],
            "executor": {
                "kind": "coding-plan",
                "provider": "codex",
                "model": "codex-test",
            },
            "usage": {},
            "result": {
                "document_id": item["document_id"],
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no supported event",
            },
        }
        (job_dir / "output.jsonl").write_text(
            json.dumps(output, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = import_job(self.root, job_dir)
        repeated = import_job(self.root, job_dir)

        self.assertEqual(result["no_event"], 1)
        self.assertEqual(result["quarantined"], 0)
        self.assertEqual(repeated["reused"], 1)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT provider, model, status FROM semantic_runs"
            ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("declared:codex", "declared:codex-test", "no_event")],
        )
        status = job_status(self.root, job_dir)
        self.assertEqual(status["status"], "imported")
        self.assertEqual(status["expected"], 1)
        self.assertEqual(status["outputs"], 1)

    def test_no_event_with_risk_warning_signal_is_routed_to_review(
        self,
    ) -> None:
        # 190713-class case: a no_event output for a document whose title/body
        # strongly signal risk_warning_delisting must NOT be silently accepted.
        # The import routes it to an explicit review terminal (quarantined)
        # so re-extraction or a human can confirm the absence.
        document_id = self._seed_risk_warning_document()
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = next(
            entry
            for entry in manifest["items"]
            if entry["document_id"] == document_id
        )
        output = {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": document_id,
            "artifact_hash": item["artifact_hash"],
            "input_hash": item["input_hash"],
            "executor": {
                "kind": "coding-plan",
                "provider": "claude",
                "model": "claude-test",
            },
            "usage": {},
            "result": {
                "document_id": document_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no event extracted",
            },
        }
        (job_dir / "output.jsonl").write_text(
            json.dumps(output, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = import_job(self.root, job_dir)

        self.assertEqual(result["no_event"], 0)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            result["errors"][0]["error"], "no_event_review_required"
        )
        self.assertEqual(
            result["errors"][0]["detail"], "risk_warning_delisting"
        )
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT status, error FROM semantic_runs"
            ).fetchone()
        self.assertEqual(row["status"], "failed_terminal")
        self.assertEqual(
            row["error"],
            "no_event_review_required:risk_warning_delisting",
        )
        production_next = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
            _allow_terminal_retry=False,
        )
        production_items = json.loads(
            (
                Path(production_next["job_dir"]) / "job.json"
            ).read_text(encoding="utf-8")
        )["items"]
        self.assertNotIn(
            document_id,
            {item["document_id"] for item in production_items},
        )
        provider = NoEventProvider()
        rerun = run_job(
            self.root,
            job_dir,
            provider=provider,
            _retry_import_errors=False,
        )
        self.assertEqual(rerun["reused"], 1)
        self.assertNotIn(
            document_id,
            {bundle.document_id for bundle, _schema in provider.calls},
        )

    def test_no_event_without_signal_imports_silently(
        self,
    ) -> None:
        # A no_event output for a low-signal document (no risk/penalty tokens)
        # is accepted as a normal no_event, proving the gate is conservative.
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = manifest["items"][0]
        output = {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": item["document_id"],
            "artifact_hash": item["artifact_hash"],
            "input_hash": item["input_hash"],
            "executor": {
                "kind": "coding-plan",
                "provider": "claude",
                "model": "claude-test",
            },
            "usage": {},
            "result": {
                "document_id": item["document_id"],
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "procedural disclosure",
            },
        }
        (job_dir / "output.jsonl").write_text(
            json.dumps(output, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        result = import_job(self.root, job_dir)

        self.assertEqual(result["no_event"], 1)
        self.assertEqual(result["quarantined"], 0)

    def test_status_is_awaiting_executor_before_output_exists(self) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        status = job_status(self.root, prepared["job_dir"])
        self.assertEqual(status["status"], "awaiting_executor")
        self.assertEqual(status["expected"], 1)
        self.assertEqual(status["outputs"], 0)

    def test_partial_import_waits_until_the_output_file_grows(self) -> None:
        second_document_id = self._seed_document("000002.SZ", "a-share-second")
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        items = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )["items"]
        second = next(
            item for item in items if item["document_id"] == second_document_id
        )
        first = next(
            item for item in items if item["document_id"] != second_document_id
        )
        no_event = {
            "document_id": first["document_id"],
            "schema_version": "announcement-events-v1-lite",
            "events": [],
            "evidence": [],
            "no_event_reason": "no supported event",
        }
        self._write_output(job_dir, first, no_event)

        report = import_job(self.root, job_dir)

        self.assertEqual(report["awaiting"], 1)
        self.assertEqual(job_status(self.root, job_dir)["status"], "awaiting_executor")

        envelope = json.loads(
            (job_dir / "output.jsonl").read_text(encoding="utf-8")
        )
        envelope["document_id"] = second_document_id
        envelope["artifact_hash"] = second["artifact_hash"]
        envelope["input_hash"] = second["input_hash"]
        envelope["result"] = {**no_event, "document_id": second_document_id}
        with (job_dir / "output.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, ensure_ascii=False) + "\n")

        self.assertEqual(job_status(self.root, job_dir)["status"], "ready_to_import")

    def test_run_job_writes_provider_neutral_output_and_is_resumable(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        provider = NoEventProvider()

        first = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )
        second = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(first["completed"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(second["completed"], 0)
        self.assertEqual(second["reused"], 1)
        self.assertEqual(len(provider.calls), 1)
        rows = [
            json.loads(line)
            for line in (
                Path(prepared["job_dir"]) / "output.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            rows[0]["contract_version"],
            "semantic-extraction-output-v1",
        )
        self.assertEqual(rows[0]["executor"]["provider"], "codex")
        self.assertEqual(rows[0]["usage"]["input_tokens"], 120)
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["no_event"], 1)

    def test_run_job_repairs_grounding_once_before_persisting_output(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        provider = GroundingRepairProvider()

        result = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["validation_repair_failures"], 0)
        self.assertEqual(
            result["usage"],
            {
                "input_tokens": 300,
                "output_tokens": 30,
                "total_tokens": 330,
                "latency_ms": 15,
                "request_count": 2,
            },
        )
        self.assertEqual(len(provider.calls), 2)
        repair_bundle = provider.calls[1][0]
        repair_context = repair_bundle.payload["repair_context"]
        self.assertEqual(
            repair_context["validation_error"]["code"],
            "semantic_evidence_quote_missing",
        )
        self.assertEqual(
            repair_context["failing_chunk"],
            {
                "chunk_id": f"chunk-{self.a_share_id}",
                "page_number": 1,
                "text": (
                    "平安银行董事会于2026年7月28日审议通过回购方案，"
                    "回购金额上限为1亿元，回购价格不超过10元/股。"
                ),
            },
        )
        output = json.loads(
            (
                Path(prepared["job_dir"]) / "output.jsonl"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(output["usage"]["input_tokens"], 300)
        self.assertEqual(output["usage"]["output_tokens"], 30)
        self.assertEqual(output["usage"]["total_tokens"], 330)
        self.assertEqual(output["usage"]["latency_ms"], 15)
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["no_event"], 1, imported)
        self.assertEqual(imported["quarantined"], 0, imported)

    def test_run_job_does_not_persist_a_failed_grounding_repair(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        provider = GroundingRepairProvider(repair_succeeds=False)

        result = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(result["status"], "partial", result)
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["validation_repair_failures"], 1)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["usage"]["input_tokens"], 300)
        self.assertEqual(result["usage"]["output_tokens"], 30)
        self.assertEqual(result["usage"]["request_count"], 2)
        self.assertEqual(
            result["errors"][0]["error"],
            "semantic_evidence_quote_missing",
        )
        self.assertFalse(result["errors"][0]["retryable"])
        self.assertTrue(result["errors"][0]["terminal"])
        self.assertFalse(
            (Path(prepared["job_dir"]) / "output.jsonl").exists()
        )
        quarantined = json.loads(
            (
                Path(prepared["job_dir"]) / "quarantine.jsonl"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(quarantined["document_id"], self.a_share_id)
        self.assertEqual(
            quarantined["validation_error"]["code"],
            "semantic_evidence_quote_missing",
        )
        self.assertEqual(
            quarantined["result"]["evidence"][0]["quote"],
            "原文不存在的句子",
        )
        self.assertEqual(
            quarantined["executor"]["identity_trust"],
            "runner-configured",
        )
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["awaiting"], 0)
        self.assertEqual(imported["failed"], 1)
        self.assertEqual(imported["status"], "partial")
        self.assertEqual(
            job_status(self.root, prepared["job_dir"])["status"],
            "partial",
        )
        with self.store.connect() as connection:
            terminal = connection.execute(
                """
                SELECT status, error, input_tokens, output_tokens
                FROM semantic_runs
                """
            ).fetchone()
        self.assertEqual(terminal["status"], "failed_terminal")
        self.assertEqual(
            terminal["error"],
            "preflight_terminal:semantic_evidence_quote_missing",
        )
        self.assertEqual(terminal["input_tokens"], 300)
        self.assertEqual(terminal["output_tokens"], 30)
        next_batch = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        self.assertEqual(next_batch["documents"], 0)

    def test_run_job_repairs_full_candidate_validation_before_persistence(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        provider = CandidateValidationRepairProvider()

        result = run_job(
            self.root,
            prepared["job_dir"],
            provider=provider,
        )

        self.assertEqual(result["status"], "complete", result)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["validation_repair_failures"], 0)
        self.assertEqual(len(provider.calls), 2)
        repair_error = provider.calls[1][0].payload["repair_context"][
            "validation_error"
        ]
        self.assertEqual(
            repair_error["code"],
            "semantic_candidate_validation_failed",
        )
        self.assertIn(
            "numeric_raw_value_mismatch",
            repair_error["detail"],
        )
        self.assertIn(
            "remove that incomplete event",
            provider.calls[1][0].payload["repair_context"]["instruction"],
        )
        imported = import_job(self.root, prepared["job_dir"])
        self.assertEqual(imported["no_event"], 1, imported)
        self.assertEqual(imported["quarantined"], 0, imported)

    def test_daily_prepare_succeeds_without_executor_credentials(
        self,
    ) -> None:
        result = run_daily(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["execution"], "awaiting_executor")
        self.assertEqual(result["quality_status"], "awaiting_executor")
        self.assertEqual(result["prepared"]["documents"], 1)
        self.assertTrue(Path(result["report_path"]).is_file())

    def test_daily_ir_profile_binds_the_configured_executor_before_prepare(
        self,
    ) -> None:
        executor_config = self.root / "executor.yaml"
        executor_config.write_text(
            """executor:
  kind: openai-compatible
  base_url: https://api.deepseek.com
  model: deepseek-v4-pro
  max_output_tokens: 1024
""",
            encoding="utf-8",
        )
        fake_report = {
            "status": "partial",
            "completed": 0,
            "reused": 0,
            "failed": 0,
            "mention_compilation": {"accepted": 0, "rejected": 0},
        }

        with mock.patch.object(
            semantic_exchange,
            "run_job",
            return_value=fake_report,
        ):
            result = run_daily(
                self.root,
                profile_id="a-share-announcement-mentions-v24",
                limit=1,
                executor_config=executor_config,
            )

        self.assertEqual(result["quality_status"], "partial")

        manifest = json.loads(
            (
                Path(result["prepared"]["job_dir"]) / "job.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["executor_binding"],
            {
                "contract_version": "semantic-execution-v1",
                "executor_mode": "api",
                "provider": "openai-compatible",
                "model": "deepseek-v4-pro",
                "client_version": "semantic-provider-v1",
            },
        )
        self.assertTrue(manifest["binding_id"].startswith("seb-"))

    def test_valid_lite_event_persists_candidate_event_and_score(
        self,
    ) -> None:
        research = ResearchStore(self.root / "data" / "research")
        research.write_feature_snapshot(
            "a_share",
            "20260728",
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "trade_date": ["20260728"],
                }
            ),
        )
        research.write_label_snapshot(
            "a_share",
            "20260728",
            pd.DataFrame(
                {
                    "code": ["000001"],
                    "trade_date": ["20260728"],
                }
            ),
        )
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = manifest["items"][0]
        chunk_id = f"chunk-{self.a_share_id}"
        result = {
            "document_id": self.a_share_id,
            "schema_version": "announcement-events-v1-lite",
            "events": [
                {
                    "event_type": "buyback",
                    "lifecycle": "approved",
                    "subjects": [
                        {
                            "entity_id": "000001.SZ",
                            "role": "issuer",
                            "evidence_ids": ["e1"],
                        }
                    ],
                    "facts": [
                        {
                            "name": "amount_lower",
                            "raw_value": "1亿元",
                            "numeric_value": 100000000,
                            "unit": "元",
                            "currency": "CNY",
                            "period": None,
                            "evidence_ids": ["e2"],
                        },
                        {
                            "name": "amount_upper",
                            "raw_value": "1亿元",
                            "numeric_value": 100000000,
                            "unit": "元",
                            "currency": "CNY",
                            "period": None,
                            "evidence_ids": ["e2"],
                        },
                        {
                            "name": "price_cap",
                            "raw_value": "不超过10元/股",
                            "numeric_value": 10,
                            "unit": "元/股",
                            "currency": "CNY",
                            "period": None,
                            "evidence_ids": ["e3"],
                        },
                    ],
                    "effective_dates": [
                        {
                            "kind": "approval_date",
                            "value": "2026-07-28",
                            "evidence_ids": ["e4"],
                        }
                    ],
                    "conditions": [],
                    "conflicts": [],
                    "missing_required_fields": [],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "chunk_id": chunk_id,
                    "quote": "平安银行",
                },
                {
                    "evidence_id": "e2",
                    "chunk_id": chunk_id,
                    "quote": "回购金额上限为1亿元",
                },
                {
                    "evidence_id": "e3",
                    "chunk_id": chunk_id,
                    "quote": "回购价格不超过10元/股",
                },
                {
                    "evidence_id": "e4",
                    "chunk_id": f"table-{self.a_share_id}-r0-c0",
                    "quote": "2026年7月28日",
                },
            ],
            "no_event_reason": None,
        }
        self._write_output(job_dir, item, result)

        imported = import_job(
            self.root,
            job_dir,
            refresh_features=True,
        )

        self.assertEqual(imported["valid"], 1, imported)
        self.assertEqual(imported["quarantined"], 0)
        self.assertEqual(
            imported["feature_refresh"]["status"],
            "complete",
        )
        refreshed = research.read_feature_snapshot(
            "a_share",
            "20260728",
        )
        self.assertEqual(
            refreshed.loc[0, "event_data_coverage"],
            1.0,
        )
        self.assertGreater(
            refreshed.loc[0, "event_relevance_20d"],
            0.0,
        )
        repeated = import_job(
            self.root,
            job_dir,
            refresh_features=True,
        )
        self.assertEqual(repeated["newly_persisted"], 0)
        self.assertIsNone(repeated["feature_refresh"])
        with self.store.connect() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "semantic_runs",
                    "event_candidates",
                    "events",
                    "event_scores",
                )
            }
        self.assertEqual(
            counts,
            {
                "semantic_runs": 1,
                "event_candidates": 1,
                "events": 1,
                "event_scores": 1,
            },
        )

    def test_v24_import_materializes_segment_and_metadata_evidence(self) -> None:
        prefix = "历史背景。" * 900
        event_text = (
            "平安银行董事会审议通过回购方案，"
            "回购金额上限为1亿元。"
        )
        source_text = prefix + event_text
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE document_chunks
                SET text=?, text_hash=?
                WHERE chunk_id=?
                """,
                (
                    source_text,
                    hashlib.sha256(source_text.encode()).hexdigest(),
                    f"chunk-{self.a_share_id}",
                ),
            )
            connection.execute(
                "UPDATE document_security_links SET name='平安银行' WHERE document_id=?",
                (self.a_share_id,),
            )
        provider = SegmentMentionProvider()
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=1,
            executor_mode="api",
            executor_provider=provider.identity.provider,
            executor_model=provider.identity.model,
            executor_client_version=provider.identity.client_version,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        item = manifest["items"][0]
        input_row = json.loads(
            (job_dir / "input.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        payload_chunks = input_row["payload"]["chunks"]
        event_chunk = next(
            chunk
            for chunk in payload_chunks
            if "回购金额上限为1亿元" in str(chunk.get("text") or "")
        )
        self.assertEqual(event_chunk["section"], "semantic_segment")
        issuer_chunk_id = f"doc{self.a_share_id}-meta-issuer"
        run_report = run_job(self.root, job_dir, provider=provider)
        self.assertEqual(run_report["failed"], 0, run_report)

        imported = import_job(self.root, job_dir)

        self.assertEqual(imported["valid"], 1, imported)
        self.assertEqual(imported["quarantined"], 0, imported)
        with self.store.connect() as connection:
            sections = dict(
                connection.execute(
                    """
                    SELECT chunk_id, section
                    FROM document_chunks
                    WHERE chunk_id IN (?, ?)
                    """,
                    (event_chunk["chunk_id"], issuer_chunk_id),
                ).fetchall()
            )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            evidence_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT chunk_id FROM event_evidence"
                ).fetchall()
            }
        self.assertEqual(sections[event_chunk["chunk_id"]], "semantic_segment")
        self.assertEqual(sections[issuer_chunk_id], "document_metadata")
        self.assertIn(event_chunk["chunk_id"], evidence_ids)
        self.assertIn(issuer_chunk_id, evidence_ids)
        self.assertEqual(foreign_keys, [])

    def test_v24_import_validates_evidence_against_the_frozen_full_ir(self) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE document_security_links SET name='平安银行' WHERE document_id=?",
                (self.a_share_id,),
            )
            connection.executemany(
                """
                INSERT INTO document_chunks(
                    chunk_id, document_id, artifact_id, sequence_no,
                    page_number, section, bbox_json, text, text_hash,
                    ocr_used, ocr_confidence, parser_version
                ) VALUES(?, ?, ?, ?, ?, 'body', '[]', ?, ?, 0, NULL,
                         'announcement-layout-v1')
                """,
                [
                    (
                        f"frozen-full-ir-{index}",
                        self.a_share_id,
                        f"parsed-{self.a_share_id}",
                        index + 1,
                        2 + index // 25,
                        text := (
                            f"平安银行历史背景第{index}段。" + "背景材料。" * 60
                        ),
                        hashlib.sha256(text.encode()).hexdigest(),
                    )
                    for index in range(160)
                ],
            )
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=1,
            max_input_characters=24_000,
            executor_mode="coding_plan",
            executor_provider="codex",
            executor_model="codex-test",
            executor_client_version="semantic-provider-v1",
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        item = manifest["items"][0]
        input_row = json.loads(
            (job_dir / "input.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        visible_ids = {
            str(chunk["chunk_id"])
            for chunk in input_row["payload"]["chunks"]
        }
        ir_row = json.loads(
            (job_dir / "document_ir.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        full_only = next(
            node
            for node in ir_row["document_ir"]["nodes"]
            if str(node.get("node_id") or "") not in visible_ids
            and "平安银行" in str(node.get("text") or "")
        )
        chunk_id = f"chunk-{self.a_share_id}"
        result = {
            "document_id": self.a_share_id,
            "schema_version": "announcement-events-v1-lite",
            "events": [{
                "event_type": "buyback",
                "lifecycle": "approved",
                "subjects": [{
                    "entity_id": "000001.SZ",
                    "role": "issuer",
                    "evidence_ids": ["e1"],
                }],
                "facts": [{
                    "name": "amount_upper",
                    "raw_value": "1亿元",
                    "numeric_value": 100000000,
                    "unit": "元",
                    "currency": "CNY",
                    "period": None,
                    "evidence_ids": ["e2"],
                }, {
                    "name": "price_cap",
                    "raw_value": "不超过10元/股",
                    "numeric_value": 10,
                    "unit": "元/股",
                    "currency": "CNY",
                    "period": None,
                    "evidence_ids": ["e3"],
                }],
                "effective_dates": [{
                    "kind": "approval_date",
                    "value": "2026-07-28",
                    "evidence_ids": ["e4"],
                }],
                "conditions": [],
                "conflicts": [],
                "missing_required_fields": [],
            }],
            "evidence": [{
                "evidence_id": "e1",
                "chunk_id": full_only["node_id"],
                "quote": "平安银行",
            }, {
                "evidence_id": "e2",
                "chunk_id": chunk_id,
                "quote": "回购金额上限为1亿元",
            }, {
                "evidence_id": "e3",
                "chunk_id": chunk_id,
                "quote": "回购价格不超过10元/股",
            }, {
                "evidence_id": "e4",
                "chunk_id": f"table-{self.a_share_id}-r0-c0",
                "quote": "2026年7月28日",
            }],
            "no_event_reason": None,
        }
        envelope = {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": item["document_id"],
            "artifact_hash": item["artifact_hash"],
            "input_hash": item["input_hash"],
            "semantic_task_id": item["semantic_task_id"],
            "execution_job_id": item["execution_job_id"],
            "binding_id": item["binding_id"],
            "executor": {
                "kind": "coding-plan",
                "provider": "codex",
                "model": "codex-test",
                "client_version": "semantic-provider-v1",
            },
            "usage": {},
            "result": result,
        }
        (job_dir / "output.jsonl").write_text(
            json.dumps(envelope, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        imported = import_job(self.root, job_dir)

        self.assertEqual(imported["valid"], 1, imported)
        self.assertEqual(imported["quarantined"], 0, imported)

    def test_v24_run_accepts_a_large_frozen_full_ir_line(self) -> None:
        source_text = (
            "平安银行董事会于2026年7月28日审议通过回购方案，"
            "回购金额上限为1亿元，回购价格不超过10元/股。"
            + "历史背景。" * 180_000
        )
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE document_chunks
                SET text=?, text_hash=?
                WHERE chunk_id=?
                """,
                (
                    source_text,
                    hashlib.sha256(source_text.encode()).hexdigest(),
                    f"chunk-{self.a_share_id}",
                ),
            )
            connection.execute(
                "UPDATE document_security_links SET name='平安银行' WHERE document_id=?",
                (self.a_share_id,),
            )
        provider = MentionProvider()
        prepared = prepare_job(
            self.root,
            profile_id="a-share-announcement-mentions-v24",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider=provider.identity.provider,
            executor_model=provider.identity.model,
            executor_client_version=provider.identity.client_version,
        )
        job_dir = Path(prepared["job_dir"])
        ir_line = (
            job_dir / "document_ir.jsonl"
        ).read_bytes().splitlines()[0]
        self.assertGreater(len(ir_line), semantic_exchange.MAX_JOB_LINE_BYTES)

        report = run_job(self.root, job_dir, provider=provider)

        self.assertEqual(report["completed"], 1, report)
        self.assertEqual(report["failed"], 0, report)
        self.assertEqual(len(provider.calls), 1)

    def test_invalid_quote_is_terminal_and_never_becomes_event(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = manifest["items"][0]
        result = {
            "document_id": self.a_share_id,
            "schema_version": "announcement-events-v1-lite",
            "events": [],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "chunk_id": f"chunk-{self.a_share_id}",
                    "quote": "原文不存在的句子",
                }
            ],
            "no_event_reason": "no supported event",
        }
        self._write_output(job_dir, item, result)

        imported = import_job(self.root, job_dir)

        self.assertEqual(imported["quarantined"], 1)
        self.assertEqual(
            imported["errors"][0]["error"],
            "semantic_evidence_quote_missing",
        )
        with self.store.connect() as connection:
            run = connection.execute(
                "SELECT status FROM semantic_runs"
            ).fetchone()
            event_count = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
        self.assertEqual(run[0], "failed_terminal")
        self.assertEqual(event_count, 0)

        repeated = import_job(self.root, job_dir)
        self.assertEqual(repeated["status"], "partial")
        self.assertEqual(repeated["quarantined"], 1)
        self.assertEqual(repeated["reused"], 0)

        prepared_again = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        retry_provider = NoEventProvider()
        retried = run_job(
            self.root,
            prepared_again["job_dir"],
            provider=retry_provider,
        )
        self.assertEqual(retried["completed"], 1, retried)
        self.assertEqual(retried["reused"], 0)

    def test_retry_does_not_overwrite_succeeded_quarantined_run(
        self,
    ) -> None:
        prepared = prepare_job(
            self.root,
            profile_id=PROFILE_ID,
            limit=20,
        )
        job_dir = Path(prepared["job_dir"])
        manifest = json.loads(
            (job_dir / "job.json").read_text(encoding="utf-8")
        )
        item = manifest["items"][0]
        claim = self.store.claim_semantic_run(
            document_id=self.a_share_id,
            artifact_hash=item["artifact_hash"],
            provider="declared:codex",
            model="declared:codex-test",
            prompt_version=manifest["prompt_version"],
            schema_version=manifest["schema_version"],
            taxonomy_version=manifest["taxonomy_version"],
            parser_version=item["parser_version"],
            input_hash=item["input_hash"],
        )
        self.store.finish_semantic_run(
            claim["run_id"],
            status="succeeded",
            output_hash="a" * 64,
            output_uri="localblob://semantic/output.json",
        )
        self.store.persist_semantic_candidate_decision(
            run_id=claim["run_id"],
            document_id=self.a_share_id,
            event_index=0,
            event_type="buyback",
            lifecycle="approved",
            payload={"event_type": "buyback"},
            validation_errors=("required_fact_missing",),
        )
        self._write_output(
            job_dir,
            item,
            {
                "document_id": self.a_share_id,
                "schema_version": "announcement-events-v1-lite",
                "events": [],
                "evidence": [],
                "no_event_reason": "no supported event",
            },
        )

        imported = import_job(self.root, job_dir)

        self.assertEqual(imported["quarantined"], 1)
        self.assertEqual(
            imported["errors"][0]["error"],
            "semantic_existing_noncanonical_run",
        )

    def test_bound_payload_never_exceeds_exact_character_budget(self) -> None:
        payload = {
            "document_id": self.a_share_id,
            "tables": [{"cells": ["重复表格"] * 500}],
            "chunks": [
                {
                    "chunk_id": f"chunk-{self.a_share_id}",
                    "page_number": 1,
                    "section": "body",
                    "text": "公告正文" * 20_000,
                }
            ],
        }

        bounded = _bound_payload(
            payload,
            max_input_characters=40_000,
        )
        serialized = json.dumps(
            bounded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertLessEqual(len(serialized), 40_000)
        self.assertEqual(bounded["tables"], [])

    def test_bound_payload_preserves_document_metadata_before_long_body(self) -> None:
        payload = {
            "document": {
                "id": self.a_share_id,
                "title": "2026年半年度报告摘要",
                "name": "驰诚股份",
            },
            "tables": [],
            "chunks": [
                {
                    "chunk_id": f"chunk-{self.a_share_id}",
                    "page_number": 1,
                    "section": "body",
                    "text": "公告正文" * 20_000,
                },
                {
                    "chunk_id": f"doc{self.a_share_id}-meta-title",
                    "page_number": 0,
                    "section": "document_metadata",
                    "text": "2026年半年度报告摘要",
                },
                {
                    "chunk_id": f"doc{self.a_share_id}-meta-issuer",
                    "page_number": 0,
                    "section": "document_metadata",
                    "text": "驰诚股份",
                },
            ],
        }

        bounded = _bound_payload(payload, max_input_characters=40_000)
        retained = {row["chunk_id"]: row for row in bounded["chunks"]}
        serialized = json.dumps(
            bounded,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertLessEqual(len(serialized), 40_000)
        self.assertEqual(
            retained[f"doc{self.a_share_id}-meta-title"]["text"],
            "2026年半年度报告摘要",
        )
        self.assertEqual(
            retained[f"doc{self.a_share_id}-meta-issuer"]["text"],
            "驰诚股份",
        )

    def _write_output(
        self,
        job_dir: Path,
        item: dict,
        result: dict,
    ) -> None:
        output = {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": item["document_id"],
            "artifact_hash": item["artifact_hash"],
            "input_hash": item["input_hash"],
            "executor": {
                "kind": "coding-plan",
                "provider": "codex",
                "model": "codex-test",
            },
            "usage": {},
            "result": result,
        }
        (job_dir / "output.jsonl").write_text(
            json.dumps(output, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
