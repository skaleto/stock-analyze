from __future__ import annotations

import copy
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from stock_analyze.intelligence.semantic.mention_contracts import (
    MENTION_SCHEMA_VERSION,
    MentionContractError,
    announcement_mention_lite_schema,
    parse_mention_document_result,
)
from stock_analyze.intelligence.semantic.mention_compiler import (
    _infer_lifecycle,
    _normalize_mention_date,
    compile_mentions,
)
from stock_analyze.intelligence.semantic.contracts import (
    parse_lite_semantic_document_result,
)
from stock_analyze.intelligence.semantic.document_ir import build_document_ir
from stock_analyze.intelligence.semantic.taxonomy import EventTaxonomy
from stock_analyze.intelligence.semantic.validation import validate_candidate


ROOT = Path(__file__).resolve().parents[1]


class MentionDateNormalizationTest(unittest.TestCase):
    def test_normalizes_common_slash_and_dot_dates(self) -> None:
        self.assertEqual(_normalize_mention_date("2026/7/28"), "2026-07-28")
        self.assertEqual(_normalize_mention_date("2026.7.28"), "2026-07-28")

    def test_completed_capacity_wording_is_not_treated_as_uncertain(self) -> None:
        self.assertEqual(
            _infer_lifecycle(
                "capacity_project",
                title="关于项目全面建成投产的公告",
                status="建成投产",
                source_text="机组顺利通过168小时满负荷试运。",
            ),
            "completed",
        )


def valid_mention_payload() -> dict:
    return {
        "document_id": 203906,
        "schema_version": MENTION_SCHEMA_VERSION,
        "mentions": [
            {
                "mention_id": "m1",
                "event_type": "dividend",
                "subjects": [
                    {
                        "role": "issuer",
                        "name": "泸州老窖股份有限公司",
                        "evidence": [
                            {
                                "chunk_id": "doc203906-p1-c1",
                                "quote": "泸州老窖股份有限公司",
                            }
                        ],
                    }
                ],
                "facts": [
                    {
                        "name": "distribution_plan",
                        "raw_value": "每10股派0.40元人民币现金",
                        "evidence": [
                            {
                                "chunk_id": "doc203906-p1-c8",
                                "quote": "每10股派0.40元人民币现金",
                            }
                        ],
                    }
                ],
                "dates": [
                    {
                        "kind": "record_date",
                        "raw_value": "2006年7月6日",
                        "evidence": [
                            {
                                "chunk_id": "doc203906-p1-c11",
                                "quote": "股权登记日为2006年7月6日",
                            }
                        ],
                    }
                ],
                "status": {
                    "raw_value": "分红派息",
                    "evidence": [
                        {
                            "chunk_id": "doc203906-p1-c1",
                            "quote": "分红派息公告",
                        }
                    ],
                },
            }
        ],
        "no_event_reason": None,
    }


class MentionContractTest(unittest.TestCase):
    def test_schema_is_strict_and_compact(self) -> None:
        schema = announcement_mention_lite_schema()
        Draft202012Validator.check_schema(schema)

        self.assertFalse(schema["additionalProperties"])
        self.assertLess(len(str(schema)), 10_000)
        evidence = schema["$defs"]["evidence"]
        self.assertEqual(
            evidence["required"],
            ["chunk_id", "quote"],
        )

    def test_valid_payload_parses_to_immutable_records(self) -> None:
        parsed = parse_mention_document_result(valid_mention_payload())

        self.assertEqual(parsed.document_id, 203906)
        self.assertEqual(parsed.mentions[0].event_type, "dividend")
        self.assertEqual(
            parsed.mentions[0].facts[0].raw_value,
            "每10股派0.40元人民币现金",
        )
        self.assertEqual(
            parsed.mentions[0].subjects[0].evidence[0].chunk_id,
            "doc203906-p1-c1",
        )
        with self.assertRaises(FrozenInstanceError):
            parsed.mentions[0].event_type = "buyback"

    def test_mentions_and_no_event_reason_are_mutually_exclusive(self) -> None:
        payload = valid_mention_payload()
        payload["no_event_reason"] = "没有事件"
        with self.assertRaises(MentionContractError) as raised:
            parse_mention_document_result(payload)
        self.assertEqual(raised.exception.code, "mention_schema_invalid")


class MentionCompilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v4.json"
        )

    @staticmethod
    def _compile(payload: dict, chunks: dict[str, dict[str, object]]):
        return compile_mentions(
            parse_mention_document_result(payload),
            taxonomy=MentionCompilerTest.taxonomy,
            chunks=chunks,
            document={
                "id": payload["document_id"],
                "title": "测试公告",
                "ts_code": "000568.SZ",
                "name": "泸州老窖股份有限公司",
                "published_at": "2006-07-01T00:00:00+00:00",
            },
            entity_whitelist=[
                {
                    "entity_id": "000568.SZ",
                    "name": "泸州老窖股份有限公司",
                    "allowed_roles": ["issuer"],
                }
            ],
            taxonomy_candidates=sorted(
                {row["event_type"] for row in payload["mentions"]}
            ),
        )

    def test_dividend_compiles_and_cash_per_share_uses_share_base(self) -> None:
        payload = valid_mention_payload()
        payload["mentions"][0]["facts"].extend(
            [
                {
                    "name": "cash_per_share",
                    "raw_value": "每10股派0.40元人民币现金",
                    "evidence": [
                        {
                            "chunk_id": "doc203906-p1-c8",
                            "quote": "每10股派0.40元人民币现金",
                        }
                    ],
                },
                {
                    "name": "distribution_period",
                    "raw_value": "2005年度",
                    "evidence": [
                        {
                            "chunk_id": "doc203906-p1-c1",
                            "quote": "2005年度",
                        }
                    ],
                },
            ]
        )
        chunks = {
            "doc203906-p1-c1": {
                "page_number": 1,
                "text": "泸州老窖股份有限公司2005年度分红派息公告",
            },
            "doc203906-p1-c8": {
                "page_number": 1,
                "text": "向全体股东每10股派0.40元人民币现金（含税）",
            },
            "doc203906-p1-c11": {
                "page_number": 1,
                "text": "股权登记日为2006年7月6日",
            },
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1)
        self.assertEqual(compiled.rejected_mentions, ())
        parsed = parse_lite_semantic_document_result(
            compiled.result,
            self.taxonomy,
            chunks,
        )
        candidate = validate_candidate(
            parsed.events[0],
            parsed.evidence,
            chunks,
            taxonomy=self.taxonomy,
            issuer_entity_id="000568.SZ",
            entity_whitelist={"000568.SZ": frozenset({"issuer"})},
            document_metadata={"ts_code": "000568.SZ"},
        )
        by_name = {fact.name: fact for fact in candidate.facts}
        self.assertEqual(
            by_name["cash_per_share"].numeric_value,
            Decimal("0.04"),
        )

    def test_unique_truncated_chunk_id_is_restored_before_grounding(self) -> None:
        payload = valid_mention_payload()
        payload["mentions"][0]["facts"].append(
            {
                "name": "distribution_period",
                "raw_value": "2005年度",
                "evidence": [
                    {
                        "chunk_id": "doc203906-p1-c1",
                        "quote": "2005年度",
                    }
                ],
            }
        )
        chunks = {
            "doc203906-p1-c1-a1b2c3": {
                "page_number": 1,
                "text": "泸州老窖股份有限公司2005年度分红派息公告",
            },
            "doc203906-p1-c8-d4e5f6": {
                "page_number": 1,
                "text": "向全体股东每10股派0.40元人民币现金（含税）",
            },
            "doc203906-p1-c11-a7b8c9": {
                "page_number": 1,
                "text": "股权登记日为2006年7月6日",
            },
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1)
        self.assertEqual(compiled.rejected_mentions, ())
        self.assertEqual(
            {row["chunk_id"] for row in compiled.result["evidence"]},
            set(chunks),
        )

    def test_ambiguous_truncated_chunk_id_remains_rejected(self) -> None:
        payload = valid_mention_payload()
        payload["mentions"][0]["facts"].append(
            {
                "name": "distribution_period",
                "raw_value": "2005年度",
                "evidence": [
                    {
                        "chunk_id": "doc203906-p1-c1",
                        "quote": "2005年度",
                    }
                ],
            }
        )
        chunks = {
            "doc203906-p1-c1-a1b2c3": {
                "page_number": 1,
                "text": "泸州老窖股份有限公司2005年度分红派息公告",
            },
            "doc203906-p1-c1-d4e5f6": {
                "page_number": 1,
                "text": "泸州老窖股份有限公司2005年度分红派息公告",
            },
            "doc203906-p1-c8-z1": {
                "page_number": 1,
                "text": "向全体股东每10股派0.40元人民币现金（含税）",
            },
            "doc203906-p1-c11-z2": {
                "page_number": 1,
                "text": "股权登记日为2006年7月6日",
            },
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 0)
        self.assertEqual(
            compiled.rejected_mentions[0].reason_codes,
            ("mention_subject_evidence_missing",),
        )

    def test_lifecycle_ignores_uncited_planning_language_elsewhere(self) -> None:
        payload = {
            "document_id": 1341115,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                {
                    "mention_id": "holder-change-1",
                    "event_type": "shareholder_change",
                    "subjects": [
                        {
                            "role": "issuer",
                            "name": "泸州老窖股份有限公司",
                            "evidence": [{"chunk_id": "issuer", "quote": "泸州老窖股份有限公司"}],
                        },
                        {
                            "role": "holder",
                            "name": "股东甲",
                            "evidence": [{"chunk_id": "holder", "quote": "股东甲"}],
                        },
                    ],
                    "facts": [
                        {"name": "action", "raw_value": "减持", "evidence": [{"chunk_id": "action", "quote": "减持"}]},
                        {"name": "share_count", "raw_value": "2,767,500股", "evidence": [{"chunk_id": "action", "quote": "2,767,500股"}]},
                        {"name": "change_period", "raw_value": "2026年7月30日至2026年8月5日", "evidence": [{"chunk_id": "period", "quote": "2026年7月30日至2026年8月5日"}]},
                    ],
                    "dates": [
                        {
                            "kind": "change_date",
                            "raw_value": "2026年8月5日",
                            "evidence": [
                                {
                                    "chunk_id": "period",
                                    "quote": "2026年8月5日",
                                }
                            ],
                        }
                    ],
                    "status": None,
                }
            ],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "泸州老窖股份有限公司"},
            "holder": {"page_number": 1, "text": "股东甲"},
            "action": {"page_number": 1, "text": "股东甲减持2,767,500股"},
            "period": {"page_number": 1, "text": "2026年7月30日至2026年8月5日"},
            "unrelated": {"page_number": 8, "text": "公司未来计划继续完善经营管理。"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(
            compiled.result["events"][0]["lifecycle"],
            "uncertain",
        )

    def test_ir_lifecycle_requires_separately_cited_status_evidence(self) -> None:
        payload = {
            "document_id": 1341200,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                {
                    "mention_id": "buyback-1",
                    "event_type": "buyback",
                    "subjects": [
                        {
                            "role": "issuer",
                            "name": "平安银行股份有限公司",
                            "evidence": [
                                {"chunk_id": "body", "quote": "平安银行股份有限公司"}
                            ],
                        }
                    ],
                    "facts": [
                        {
                            "name": "price_cap",
                            "raw_value": "10元/股",
                            "evidence": [{"chunk_id": "body", "quote": "10元/股"}],
                        },
                        {
                            "name": "amount_upper",
                            "raw_value": "1亿元",
                            "evidence": [
                                {
                                    "chunk_id": "body",
                                    "quote": "审议通过回购方案，回购金额不超过1亿元",
                                }
                            ],
                        },
                    ],
                    "dates": [
                        {
                            "kind": "approval_date",
                            "raw_value": "2026年8月8日",
                            "evidence": [
                                {"chunk_id": "body", "quote": "2026年8月8日"}
                            ],
                        }
                    ],
                    "status": None,
                }
            ],
            "no_event_reason": None,
        }
        source_chunks = [
            {
                "chunk_id": "body",
                "page_number": 1,
                "section": "body",
                "bbox": [],
                "text": (
                    "平安银行股份有限公司董事会于2026年8月8日审议通过回购方案，"
                    "回购金额不超过1亿元，回购价格不超过10元/股。"
                ),
            }
        ]
        chunks = {
            row["chunk_id"]: {
                "page_number": row["page_number"],
                "text": row["text"],
            }
            for row in source_chunks
        }
        document = {
            "id": 1341200,
            "title": "关于回购公司股份方案的公告",
            "ts_code": "000001.SZ",
            "name": "平安银行股份有限公司",
        }
        ir = build_document_ir(
            document=document,
            chunks=source_chunks,
            tables=[],
            parser_version="announcement-layout-v1",
        )
        common = {
            "taxonomy": self.taxonomy,
            "chunks": chunks,
            "document": document,
            "entity_whitelist": [
                {
                    "entity_id": "000001.SZ",
                    "name": "平安银行股份有限公司",
                    "allowed_roles": ["issuer"],
                }
            ],
            "taxonomy_candidates": ["buyback"],
        }

        legacy = compile_mentions(parse_mention_document_result(payload), **common)
        strict = compile_mentions(
            parse_mention_document_result(payload),
            document_ir=ir,
            **common,
        )
        with_status = copy.deepcopy(payload)
        with_status["mentions"][0]["status"] = {
            "raw_value": "审议通过",
            "evidence": [{"chunk_id": "body", "quote": "审议通过"}],
        }
        strict_with_status = compile_mentions(
            parse_mention_document_result(with_status),
            document_ir=ir,
            **common,
        )

        self.assertEqual(legacy.accepted_mentions, 1, legacy)
        self.assertEqual(strict.accepted_mentions, 1, strict)
        self.assertEqual(strict_with_status.accepted_mentions, 1, strict_with_status)
        self.assertEqual(legacy.result["events"][0]["lifecycle"], "approved")
        self.assertEqual(strict.result["events"][0]["lifecycle"], "uncertain")
        self.assertEqual(
            strict_with_status.result["events"][0]["lifecycle"],
            "approved",
        )
        self.assertTrue(
            any(
                row["quote"] == "审议通过"
                for row in strict_with_status.result["evidence"]
            )
        )

    def test_financing_range_and_chinese_date_compile_without_data_loss(self) -> None:
        payload = {
            "document_id": 111319,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                {
                    "mention_id": "financing-1",
                    "event_type": "equity_financing",
                    "subjects": [
                        {
                            "role": "issuer",
                            "name": "重庆桐君阁股份有限公司",
                            "evidence": [{"chunk_id": "c1", "quote": "重庆桐君阁股份有限公司"}],
                        }
                    ],
                    "facts": [
                        {"name": "financing_method", "raw_value": "配股", "evidence": [{"chunk_id": "c2", "quote": "配股"}]},
                        {"name": "amount", "raw_value": "11,410—17,115万元", "evidence": [{"chunk_id": "c3", "quote": "募集资金11,410—17,115万元"}]},
                        {"name": "use_of_proceeds", "raw_value": "全国药品零售连锁经营网络建设项目", "evidence": [{"chunk_id": "c4", "quote": "全国药品零售连锁经营网络建设项目"}]},
                        {"name": "dilution_ratio", "raw_value": "每10股配3股", "evidence": [{"chunk_id": "c5", "quote": "每10股配3股"}]},
                    ],
                    "dates": [
                        {"kind": "board_approval_date", "raw_value": "二00二年七月十六日", "evidence": [{"chunk_id": "c6", "quote": "二00二年七月十六日"}]}
                    ],
                    "status": {"raw_value": "议案", "evidence": [{"chunk_id": "c2", "quote": "配股的具体方案的议案"}]},
                }
            ],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "重庆桐君阁股份有限公司"},
            "c2": {"page_number": 1, "text": "配股的具体方案的议案"},
            "c3": {"page_number": 1, "text": "本次预计募集资金11,410—17,115万元"},
            "c4": {"page_number": 1, "text": "全国药品零售连锁经营网络建设项目"},
            "c5": {"page_number": 1, "text": "每10股配3股"},
            "c6": {"page_number": 1, "text": "二00二年七月十六日"},
        }
        compiled = compile_mentions(
            parse_mention_document_result(payload),
            taxonomy=self.taxonomy,
            chunks=chunks,
            document={
                "id": 111319,
                "title": "桐君阁：配股的具体方案的议案",
                "ts_code": "000591.SZ",
                "name": "重庆桐君阁股份有限公司",
            },
            entity_whitelist=[
                {"entity_id": "000591.SZ", "name": "重庆桐君阁股份有限公司", "allowed_roles": ["issuer"]}
            ],
            taxonomy_candidates=["equity_financing"],
        )

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        event = compiled.result["events"][0]
        facts = {fact["name"]: fact for fact in event["facts"]}
        self.assertEqual(facts["amount_lower"]["raw_value"], "11,410")
        self.assertEqual(facts["amount_lower"]["unit"], "万元")
        self.assertEqual(facts["amount_upper"]["raw_value"], "17,115")
        self.assertEqual(
            event["effective_dates"][0]["value"],
            "2002-07-16",
        )

    def test_one_invalid_litigation_mention_does_not_drop_valid_sibling(self) -> None:
        def litigation(mention_id: str, counterparty: str, chunk: str) -> dict:
            return {
                "mention_id": mention_id,
                "event_type": "litigation_arbitration",
                "subjects": [
                    {"role": "issuer", "name": "广夏银川实业股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "广夏银川实业股份有限公司"}]},
                    {"role": "counterparty", "name": counterparty, "evidence": [{"chunk_id": chunk, "quote": counterparty}]},
                ],
                "facts": [
                    {"name": "issuer_role", "raw_value": "偿还", "evidence": [{"chunk_id": chunk, "quote": "偿还"}]},
                    {"name": "case_amount", "raw_value": "10,200万元", "evidence": [{"chunk_id": chunk, "quote": "10,200万元"}]},
                    {"name": "case_stage", "raw_value": "判令", "evidence": [{"chunk_id": chunk, "quote": "判令"}]},
                ],
                "dates": [{"kind": "start_date", "raw_value": "2026/7/28", "evidence": [{"chunk_id": "date", "quote": "2026/7/28"}]}],
                "status": {"raw_value": "判令", "evidence": [{"chunk_id": chunk, "quote": "判令"}]},
            }

        payload = {
            "document_id": 106298,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                litigation("bad", "不存在的银行", "bad-chunk"),
                litigation("good", "中国工商银行银川市西城支行", "good-chunk"),
            ],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "广夏银川实业股份有限公司"},
            "bad-chunk": {"page_number": 1, "text": "原文没有该主体"},
            "good-chunk": {"page_number": 1, "text": "中国工商银行银川市西城支行，法院判令本公司偿还10,200万元"},
        }
        compiled = compile_mentions(
            parse_mention_document_result(payload),
            taxonomy=self.taxonomy,
            chunks=chunks,
            document={"id": 106298, "title": "诉讼判决公告", "ts_code": "000557.SZ", "name": "广夏银川实业股份有限公司"},
            entity_whitelist=[{"entity_id": "000557.SZ", "name": "广夏银川实业股份有限公司", "allowed_roles": ["issuer"]}],
            taxonomy_candidates=["litigation_arbitration"],
        )

        self.assertEqual(compiled.accepted_mentions, 1)
        self.assertEqual(len(compiled.rejected_mentions), 1)
        self.assertEqual(compiled.rejected_mentions[0].mention_id, "bad")
        self.assertEqual(len(compiled.result["events"]), 1)

        payload = valid_mention_payload()
        payload["mentions"] = []
        payload["no_event_reason"] = "未发现候选事件"
        parsed = parse_mention_document_result(payload)
        self.assertEqual(parsed.mentions, ())

    def test_capacity_recovers_operation_fact_and_drops_return_rate(self) -> None:
        payload = {
            "document_id": 1329840,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "capacity-1",
                "event_type": "capacity_project",
                "subjects": [{"role": "issuer", "name": "安琪酵母股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "安琪酵母股份有限公司"}]}],
                "facts": [
                    {"name": "project_type", "raw_value": "新建", "evidence": [{"chunk_id": "c2", "quote": "新建"}]},
                    {"name": "capex", "raw_value": "37,045 万元", "evidence": [{"chunk_id": "c3", "quote": "37,045 万元"}]},
                    {"name": "expected_profit", "raw_value": "9.79%", "evidence": [{"chunk_id": "c4", "quote": "项目投资收益率为9.79%"}]},
                ],
                "dates": [{"kind": "expected_operation_date", "raw_value": "2027 年3 月", "evidence": [{"chunk_id": "c5", "quote": "2027 年3 月"}]}],
                "status": {"raw_value": "尚需股东会审议", "evidence": [{"chunk_id": "c6", "quote": "尚需股东会审议"}]},
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "安琪酵母股份有限公司"},
            "c2": {"page_number": 1, "text": "本项目为新建项目"},
            "c3": {"page_number": 1, "text": "项目总投资37,045 万元"},
            "c4": {"page_number": 2, "text": "项目调整后的方案，项目投资收益率为9.79%"},
            "c5": {"page_number": 2, "text": "预计2027 年3 月投产"},
            "c6": {"page_number": 1, "text": "尚需股东会审议"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["expected_operation_date"]["raw_value"], "2027 年3 月")
        self.assertNotIn("expected_profit", facts)
        self.assertEqual(compiled.result["events"][0]["lifecycle"], "planned")

    def test_litigation_uses_principal_not_court_fee(self) -> None:
        payload = {
            "document_id": 106298,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "litigation-1",
                "event_type": "litigation_arbitration",
                "subjects": [
                    {"role": "issuer", "name": "广夏银川实业股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "广夏银川实业股份有限公司"}]},
                    {"role": "counterparty", "name": "中国工商银行银川市西城支行", "evidence": [{"chunk_id": "c2", "quote": "中国工商银行银川市西城支行"}]},
                ],
                "facts": [
                    {"name": "judgment_amount", "raw_value": "借款本金10,200 万元及利息139.8 万元", "evidence": [{"chunk_id": "c4", "quote": "借款本金10,200 万元及利息139.8 万元"}]},
                    {"name": "case_amount", "raw_value": "案件受理费及诉前财产保全费104 万元", "evidence": [{"chunk_id": "c5", "quote": "案件受理费及诉前财产保全费104 万元"}]},
                ],
                "dates": [],
                "status": {"raw_value": "判决", "evidence": [{"chunk_id": "c6", "quote": "诉讼判决"}]},
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "广夏银川实业股份有限公司"},
            "c2": {"page_number": 1, "text": "中国工商银行银川市西城支行"},
            "c3": {"page_number": 1, "text": "判令本公司偿还"},
            "c4": {"page_number": 1, "text": "借款本金10,200 万元及利息139.8 万元"},
            "c5": {"page_number": 1, "text": "案件受理费及诉前财产保全费104 万元"},
            "c6": {"page_number": 1, "text": "诉讼判决"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["case_amount"]["raw_value"], "10,200 万元")
        self.assertEqual(facts["case_stage"]["raw_value"], "判决")
        self.assertEqual(facts["issuer_role"]["raw_value"], "偿还")

    def test_multichunk_text_drops_model_inserted_separator(self) -> None:
        payload = {
            "document_id": 73193,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "contract-1",
                "event_type": "major_contract",
                "subjects": [
                    {"role": "issuer", "name": "中国电力建设股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "中国电力建设股份有限公司"}]},
                    {"role": "counterparty", "name": "伊拉克瑞达公司", "evidence": [{"chunk_id": "c2", "quote": "伊拉克瑞达公司"}]},
                ],
                "facts": [
                    {"name": "contract_amount", "raw_value": "89.25亿元", "evidence": [{"chunk_id": "c3", "quote": "89.25亿元"}]},
                    {"name": "contract_subject", "raw_value": "海水淡化项目输水系统分包合同", "evidence": [{"chunk_id": "c4", "quote": "海水淡化项目输水系统分包合同"}]},
                    {"name": "contract_period", "raw_value": "6个月的有限开工期+1350天的主体工程期，缺陷责任期为24个月", "evidence": [{"chunk_id": "c5", "quote": "6个月的有限开工期+1350天的主体工程期"}, {"chunk_id": "c6", "quote": "缺陷责任期为24个月"}]},
                ],
                "dates": [{"kind": "guarantee_date", "raw_value": "2026/7/30", "evidence": [{"chunk_id": "date", "quote": "2026/7/30"}]}],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "中国电力建设股份有限公司"},
            "c2": {"page_number": 1, "text": "伊拉克瑞达公司"},
            "c3": {"page_number": 1, "text": "合同金额89.25亿元"},
            "c4": {"page_number": 1, "text": "海水淡化项目输水系统分包合同"},
            "c5": {"page_number": 1, "text": "项目工期为6个月的有限开工期+1350天的主体工"},
            "c6": {"page_number": 1, "text": "程期，缺陷责任期为24个月。"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(
            facts["contract_period"]["raw_value"],
            "6个月的有限开工期+1350天的主体工程期，缺陷责任期为24个月",
        )

    def test_text_fact_with_model_punctuation_rewrite_is_dropped(self) -> None:
        payload = {
            "document_id": 1332845,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "holder-change-1",
                "event_type": "shareholder_change",
                "subjects": [
                    {"role": "issuer", "name": "中际旭创股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "中际旭创股份有限公司"}]},
                    {"role": "holder", "name": "山东中际投资控股有限公司", "evidence": [{"chunk_id": "c2", "quote": "山东中际投资控股有限公司"}]},
                ],
                "facts": [
                    {"name": "action", "raw_value": "减持", "evidence": [{"chunk_id": "c3", "quote": "合计减持6,208,552股"}]},
                    {"name": "share_count", "raw_value": "6,208,552", "evidence": [{"chunk_id": "c3", "quote": "6,208,552股"}]},
                    {"name": "change_method", "raw_value": "集中交易、大宗交易、被动稀释", "evidence": [{"chunk_id": "c4", "quote": "集中交易\n√\n大宗交易\n√\n其他 被动稀释\n√"}]},
                ],
                "dates": [{"kind": "change_date", "raw_value": "2026年7月30日", "evidence": [{"chunk_id": "c5", "quote": "2026年7月30日"}]}],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "中际旭创股份有限公司"},
            "c2": {"page_number": 1, "text": "山东中际投资控股有限公司"},
            "c3": {"page_number": 1, "text": "合计减持6,208,552股"},
            "c4": {"page_number": 1, "text": "集中交易\n√\n大宗交易\n√\n其他 被动稀释\n√"},
            "c5": {"page_number": 1, "text": "2026年7月30日"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(compiled.dropped_items, 1)
        facts = {row["name"] for row in compiled.result["events"][0]["facts"]}
        self.assertNotIn("change_method", facts)

    def test_recovers_scalar_split_at_adjacent_pdf_chunk_boundary(self) -> None:
        payload = {
            "document_id": 1329267,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "contract-1",
                "event_type": "major_contract",
                "subjects": [
                    {"role": "issuer", "name": "泸州老窖股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "泸州老窖股份有限公司"}]},
                    {"role": "counterparty", "name": "公司H", "evidence": [{"chunk_id": "c2", "quote": "公司H"}]},
                ],
                "facts": [
                    {"name": "contract_amount", "raw_value": "1,674万欧元", "evidence": [{"chunk_id": "c3", "quote": "1,674万欧元"}]},
                    {"name": "contract_subject", "raw_value": "自动化制造设备", "evidence": [{"chunk_id": "c6", "quote": "自动化制造设备"}]},
                    {"name": "contract_period", "raw_value": "12个月", "evidence": [{"chunk_id": "c5", "quote": "12个月"}]},
                ],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "泸州老窖股份有限公司"},
            "c2": {"page_number": 1, "text": "公司H"},
            "c3": {"page_number": 1, "text": "合同金额为1,674"},
            "c4": {"page_number": 1, "text": "万欧元，不含税"},
            "c5": {"page_number": 1, "text": "合同期限12个月"},
            "c6": {"page_number": 1, "text": "自动化制造设备"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(compiled.dropped_items, 0)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["contract_amount"]["unit"], "万欧元")

    def test_litigation_role_is_scoped_to_each_mention_chunks(self) -> None:
        def mention(mention_id: str, bank: str, chunk_id: str) -> dict:
            return {
                "mention_id": mention_id,
                "event_type": "litigation_arbitration",
                "subjects": [
                    {"role": "issuer", "name": "广夏银川实业股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "广夏银川实业股份有限公司"}]},
                    {"role": "counterparty", "name": bank, "evidence": [{"chunk_id": chunk_id, "quote": bank}]},
                ],
                "facts": [
                    {"name": "case_amount", "raw_value": "1,000万元", "evidence": [{"chunk_id": chunk_id, "quote": "1,000万元"}]},
                    {"name": "case_stage", "raw_value": "判决", "evidence": [{"chunk_id": chunk_id, "quote": "判决"}]},
                ],
                "dates": [],
                "status": {"raw_value": "判决", "evidence": [{"chunk_id": chunk_id, "quote": "判决"}]},
            }

        payload = {
            "document_id": 106298,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                mention("m1", "银行甲", "case-1"),
                mention("m2", "银行乙", "case-2"),
            ],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "广夏银川实业股份有限公司"},
            "case-1": {"page_number": 1, "text": "银行甲，判决本公司偿还1,000万元"},
            "case-2": {"page_number": 1, "text": "银行乙，判决本公司承担连带清偿责任1,000万元"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 2, compiled)
        roles = [
            {fact["name"]: fact["raw_value"] for fact in event["facts"]}["issuer_role"]
            for event in compiled.result["events"]
        ]
        self.assertEqual(roles, ["偿还", "承担连带清偿责任"])

    def test_table_unit_applies_to_bare_earnings_numbers(self) -> None:
        payload = {
            "document_id": 220300,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "flash-1",
                "event_type": "earnings_flash",
                "subjects": [{"role": "issuer", "name": "重庆长安汽车股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "重庆长安汽车股份有限公司"}]}],
                "facts": [
                    {"name": "currency", "raw_value": "万元", "evidence": [{"chunk_id": "unit", "quote": "万元"}]},
                    {"name": "period", "raw_value": "2006年度三季度", "evidence": [{"chunk_id": "period", "quote": "2006年度三季度"}]},
                    {"name": "revenue", "raw_value": "1,822,953", "evidence": [{"chunk_id": "revenue", "quote": "1,822,953"}]},
                    {"name": "net_profit", "raw_value": "53,333", "evidence": [{"chunk_id": "profit", "quote": "53,333"}]},
                ],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "重庆长安汽车股份有限公司"},
            "unit": {"page_number": 1, "text": "单位：万元"},
            "period": {"page_number": 1, "text": "2006年度三季度"},
            "revenue": {"page_number": 1, "text": "营业收入1,822,953"},
            "profit": {"page_number": 1, "text": "净利润53,333"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["revenue"]["unit"], "万元")
        self.assertIn("m1-e2", facts["revenue"]["evidence_ids"])

    def test_ir_semantics_resolve_bare_half_year_report_values(self) -> None:
        document = {
            "id": 1341091,
            "title": "2026年半年度报告",
            "ts_code": "000568.SZ",
            "name": "泸州老窖股份有限公司",
            "published_at": "2026-08-01T00:00:00+00:00",
        }
        source_chunks = [
            {
                "chunk_id": "issuer",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "泸州老窖股份有限公司",
            },
            {
                "chunk_id": "period",
                "page_number": 1,
                "section": "document_metadata",
                "bbox": [],
                "text": "2026年半年度报告",
            },
        ]
        table = {
            "table_id": "financial-summary",
            "page_number": 2,
            "bbox": [],
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "项目", "bbox": []},
                {"row_index": 0, "column_index": 1, "text": "本报告期", "bbox": []},
                {"row_index": 0, "column_index": 2, "text": "上年同期", "bbox": []},
                {"row_index": 1, "column_index": 0, "text": "营业收入（元）", "bbox": []},
                {"row_index": 1, "column_index": 1, "text": "621,408,705.13", "bbox": []},
                {"row_index": 1, "column_index": 2, "text": "415,233,872.26", "bbox": []},
                {"row_index": 2, "column_index": 0, "text": "归属于上市公司股东的净利润（元）", "bbox": []},
                {"row_index": 2, "column_index": 1, "text": "38,544,455.63", "bbox": []},
                {"row_index": 2, "column_index": 2, "text": "21,001,145.88", "bbox": []},
            ],
        }
        ir = build_document_ir(
            document=document,
            chunks=source_chunks,
            tables=[table],
            parser_version="announcement-layout-v1",
        )
        chunks = {
            row["chunk_id"]: {
                "page_number": row["page_number"],
                "text": row["text"],
            }
            for row in source_chunks
        }
        chunks.update(
            {
                f"financial-summary-r{cell['row_index']}-c{cell['column_index']}": {
                    "page_number": 2,
                    "text": cell["text"],
                }
                for cell in table["cells"]
            }
        )
        payload = {
            "document_id": 1341091,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                {
                    "mention_id": "flash-1",
                    "event_type": "earnings_flash",
                    "subjects": [
                        {
                            "role": "issuer",
                            "name": "泸州老窖股份有限公司",
                            "evidence": [
                                {"chunk_id": "issuer", "quote": "泸州老窖股份有限公司"}
                            ],
                        }
                    ],
                    "facts": [
                        {
                            "name": "period",
                            "raw_value": "2026年半年度",
                            "evidence": [
                                {"chunk_id": "period", "quote": "2026年半年度"}
                            ],
                        },
                        {
                            "name": "revenue",
                            "raw_value": "621,408,705.13",
                            "evidence": [
                                {
                                    "chunk_id": "financial-summary-r1-c1",
                                    "quote": "621,408,705.13",
                                }
                            ],
                        },
                        {
                            "name": "net_profit",
                            "raw_value": "38,544,455.63",
                            "evidence": [
                                {
                                    "chunk_id": "financial-summary-r2-c1",
                                    "quote": "38,544,455.63",
                                }
                            ],
                        },
                    ],
                    "dates": [],
                    "status": None,
                }
            ],
            "no_event_reason": None,
        }
        parsed = parse_mention_document_result(payload)
        common = {
            "taxonomy": self.taxonomy,
            "chunks": chunks,
            "document": document,
            "entity_whitelist": [
                {
                    "entity_id": "000568.SZ",
                    "name": "泸州老窖股份有限公司",
                    "allowed_roles": ["issuer"],
                }
            ],
            "taxonomy_candidates": ["earnings_flash"],
        }

        legacy = compile_mentions(parsed, **common)
        compiled = compile_mentions(parsed, document_ir=ir, **common)

        self.assertEqual(legacy.accepted_mentions, 0)
        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {
            row["name"]: row for row in compiled.result["events"][0]["facts"]
        }
        self.assertEqual(facts["revenue"]["unit"], "元")
        self.assertEqual(facts["net_profit"]["unit"], "元")
        self.assertEqual(facts["revenue"]["period"], "本报告期")
        self.assertGreaterEqual(len(facts["revenue"]["evidence_ids"]), 3)

        mixed_payload = copy.deepcopy(payload)
        mixed_profit = mixed_payload["mentions"][0]["facts"][2]
        mixed_profit["raw_value"] = "21,001,145.88"
        mixed_profit["evidence"] = [
            {
                "chunk_id": "financial-summary-r2-c2",
                "quote": "21,001,145.88",
            }
        ]
        mixed = compile_mentions(
            parse_mention_document_result(mixed_payload),
            document_ir=ir,
            **common,
        )
        self.assertEqual(mixed.accepted_mentions, 0)
        self.assertIn(
            "table_semantic_period_mismatch",
            mixed.rejected_mentions[0].reason_codes[0],
        )

    def test_table_scalar_and_unit_in_separate_cells_compile_without_loss(self) -> None:
        payload = {
            "document_id": 1333257,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "pledge-1",
                "event_type": "pledge_freeze",
                "subjects": [
                    {"role": "issuer", "name": "泸州老窖股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "泸州老窖股份有限公司"}]},
                    {"role": "holder", "name": "罗燚栋", "evidence": [{"chunk_id": "holder", "quote": "罗燚栋"}]},
                ],
                "facts": [
                    {"name": "action", "raw_value": "质押", "evidence": [{"chunk_id": "action", "quote": "股份质押"}]},
                    {"name": "share_count", "raw_value": "4,500,000股", "evidence": [{"chunk_id": "count", "quote": "4,500,000股"}]},
                    {"name": "cumulative_share_count", "raw_value": "91,820,000股", "evidence": [{"chunk_id": "total", "quote": "91,820,000"}, {"chunk_id": "unit", "quote": "单位：股"}]},
                ],
                "dates": [
                    {"kind": "start_date", "raw_value": "2026/7/28", "evidence": [{"chunk_id": "date", "quote": "2026/7/28"}]}
                ],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "泸州老窖股份有限公司"},
            "holder": {"page_number": 1, "text": "罗燚栋"},
            "action": {"page_number": 1, "text": "股份质押"},
            "count": {"page_number": 1, "text": "4,500,000股"},
            "total": {"page_number": 2, "text": "91,820,000"},
            "date": {"page_number": 1, "text": "2026/7/28"},
            "unit": {"page_number": 2, "text": "单位：股"},
            "date": {"page_number": 1, "text": "2026/7/28"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(compiled.dropped_items, 0)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["cumulative_share_count"]["raw_value"], "91,820,000")
        self.assertEqual(facts["cumulative_share_count"]["unit"], "股")
        self.assertEqual(len(facts["cumulative_share_count"]["evidence_ids"]), 2)

    def test_bare_table_share_count_uses_unambiguous_schema_unit(self) -> None:
        payload = {
            "document_id": 1333257,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "pledge-1",
                "event_type": "pledge_freeze",
                "subjects": [
                    {"role": "issuer", "name": "合盛硅业股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "合盛硅业股份有限公司"}]},
                    {"role": "holder", "name": "罗烨栋", "evidence": [{"chunk_id": "holder", "quote": "罗烨栋"}]},
                ],
                "facts": [
                    {"name": "action", "raw_value": "质押", "evidence": [{"chunk_id": "action", "quote": "股份质押"}]},
                    {"name": "share_count", "raw_value": "4,500,000股", "evidence": [{"chunk_id": "count", "quote": "4,500,000股"}]},
                    {"name": "cumulative_share_count", "raw_value": "91,820,000", "evidence": [{"chunk_id": "total", "quote": "91,820,000"}]},
                ],
                "dates": [{"kind": "start_date", "raw_value": "2026/7/28", "evidence": [{"chunk_id": "date", "quote": "2026/7/28"}]}],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "合盛硅业股份有限公司"},
            "holder": {"page_number": 1, "text": "罗烨栋"},
            "action": {"page_number": 1, "text": "股份质押"},
            "count": {"page_number": 1, "text": "4,500,000股"},
            "total": {"page_number": 2, "text": "91,820,000"},
            "date": {"page_number": 1, "text": "2026/7/28"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(compiled.dropped_items, 0)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["cumulative_share_count"]["unit"], "股")

    def test_counter_guarantee_does_not_leak_to_another_beneficiary(self) -> None:
        def guarantee(mention_id: str, beneficiary: str, amount: str) -> dict:
            return {
                "mention_id": mention_id,
                "event_type": "guarantee",
                "subjects": [
                    {"role": "issuer", "name": "安徽中鼎密封件股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "安徽中鼎密封件股份有限公司"}]},
                    {"role": "beneficiary", "name": beneficiary, "evidence": [{"chunk_id": mention_id, "quote": beneficiary}]},
                ],
                "facts": [
                    {"name": "guarantee_amount", "raw_value": amount, "evidence": [{"chunk_id": mention_id, "quote": amount}]},
                    {"name": "counter_guarantee", "raw_value": "安徽华创股东王鹍将为上述担保提供反担保", "evidence": [{"chunk_id": "counter", "quote": "安徽华创股东王鹍将为上述担保提供反担保"}]},
                ],
                "dates": [{"kind": "guarantee_date", "raw_value": "2026/7/30", "evidence": [{"chunk_id": "date", "quote": "2026/7/30"}]}],
                "status": None,
            }

        payload = {
            "document_id": 1333568,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [
                guarantee("g1", "安徽华创智能有限公司", "2600万元"),
                guarantee("g2", "东莞华众鑫科技有限公司", "2500万元"),
            ],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "安徽中鼎密封件股份有限公司"},
            "g1": {"page_number": 1, "text": "安徽华创智能有限公司 2600万元"},
            "g2": {"page_number": 1, "text": "东莞华众鑫科技有限公司 2500万元"},
            "counter": {"page_number": 1, "text": "安徽华创股东王鹍将为上述担保提供反担保"},
            "date": {"page_number": 1, "text": "2026/7/30"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(len(compiled.rejected_mentions), 1)
        self.assertIn(
            "mention_counter_guarantee_subject_mismatch",
            compiled.rejected_mentions[0].reason_codes,
        )

    def test_bare_table_ratio_uses_percent_column_header(self) -> None:
        payload = {
            "document_id": 1330676,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "flash-1",
                "event_type": "earnings_flash",
                "subjects": [{"role": "issuer", "name": "环旭电子股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "环旭电子股份有限公司"}]}],
                "facts": [
                    {"name": "period", "raw_value": "2026年上半年", "evidence": [{"chunk_id": "period", "quote": "2026年上半年"}]},
                    {"name": "currency", "raw_value": "人民币元", "evidence": [{"chunk_id": "currency", "quote": "人民币元"}]},
                    {"name": "revenue", "raw_value": "27,336,366,042.06", "evidence": [{"chunk_id": "table-r2-c1", "quote": "27,336,366,042.06"}]},
                    {"name": "net_profit", "raw_value": "822,095,752.12", "evidence": [{"chunk_id": "table-r5-c1", "quote": "822,095,752.12"}]},
                    {"name": "net_profit_yoy", "raw_value": "28.85", "evidence": [{"chunk_id": "table-r5-c4", "quote": "28.85"}]},
                ],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "环旭电子股份有限公司"},
            "period": {"page_number": 1, "text": "2026年上半年"},
            "currency": {"page_number": 1, "text": "单位：人民币元"},
            "table-r0-c4": {"page_number": 1, "text": "增减变动幅（%）"},
            "table-r2-c1": {"page_number": 1, "text": "27,336,366,042.06"},
            "table-r5-c1": {"page_number": 1, "text": "822,095,752.12"},
            "table-r5-c4": {"page_number": 1, "text": "28.85"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        self.assertEqual(compiled.dropped_items, 0)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["net_profit_yoy"]["unit"], "%")
        self.assertEqual(len(facts["net_profit_yoy"]["evidence_ids"]), 2)

    def test_split_range_endpoints_are_grounded_from_range_quote(self) -> None:
        payload = {
            "document_id": 111319,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "financing-1",
                "event_type": "equity_financing",
                "subjects": [{"role": "issuer", "name": "重庆桐君阁股份有限公司", "evidence": [{"chunk_id": "c1", "quote": "重庆桐君阁股份有限公司"}]}],
                "facts": [
                    {"name": "financing_method", "raw_value": "配股", "evidence": [{"chunk_id": "c2", "quote": "配股"}]},
                    {"name": "amount_lower", "raw_value": "11,410万元", "evidence": [{"chunk_id": "c3", "quote": "11,410—17,115 万元"}]},
                    {"name": "amount_upper", "raw_value": "17,115万元", "evidence": [{"chunk_id": "c3", "quote": "11,410—17,115 万元"}]},
                    {"name": "use_of_proceeds", "raw_value": "全国药品零售连锁经营网络建设项目", "evidence": [{"chunk_id": "c4", "quote": "全国药品零售连锁经营网络建设项目"}]},
                ],
                "dates": [{"kind": "board_approval_date", "raw_value": "二00二年七月十六日", "evidence": [{"chunk_id": "c5", "quote": "二00二年七月十六日"}]}],
                "status": {"raw_value": "议案", "evidence": [{"chunk_id": "c2", "quote": "配股议案"}]},
            }],
            "no_event_reason": None,
        }
        chunks = {
            "c1": {"page_number": 1, "text": "重庆桐君阁股份有限公司"},
            "c2": {"page_number": 1, "text": "配股议案"},
            "c3": {"page_number": 1, "text": "募集资金11,410—17,115 万元"},
            "c4": {"page_number": 1, "text": "全国药品零售连锁经营网络建设项目"},
            "c5": {"page_number": 1, "text": "二00二年七月十六日"},
        }

        compiled = self._compile(payload, chunks)

        self.assertEqual(compiled.accepted_mentions, 1, compiled)
        facts = {row["name"]: row for row in compiled.result["events"][0]["facts"]}
        self.assertEqual(facts["amount_lower"]["raw_value"], "11,410")
        self.assertEqual(facts["amount_lower"]["unit"], "万元")

    def test_shareholder_action_rejects_numeric_table_garbage(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs" / "intelligence_event_taxonomy_v7.json"
        )
        payload = {
            "document_id": 1330246,
            "schema_version": MENTION_SCHEMA_VERSION,
            "mentions": [{
                "mention_id": "holder-change-1",
                "event_type": "shareholder_change",
                "subjects": [
                    {"role": "issuer", "name": "东方证券股份有限公司", "evidence": [{"chunk_id": "issuer", "quote": "东方证券股份有限公司"}]},
                    {"role": "holder", "name": "申能（集团）有限公司", "evidence": [{"chunk_id": "holder", "quote": "申能（集团）有限公司"}]},
                ],
                "facts": [
                    {"name": "action", "raw_value": "22,526,840480,008,508", "evidence": [{"chunk_id": "bad-action", "quote": "22,526,840480,008,508"}]},
                    {"name": "share_count", "raw_value": "22,526,840股", "evidence": [{"chunk_id": "count", "quote": "22,526,840股"}]},
                ],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        chunks = {
            "issuer": {"page_number": 1, "text": "东方证券股份有限公司"},
            "holder": {"page_number": 1, "text": "申能（集团）有限公司"},
            "bad-action": {"page_number": 1, "text": "22,526,840480,008,508"},
            "count": {"page_number": 1, "text": "22,526,840股"},
        }

        compiled = compile_mentions(
            parse_mention_document_result(payload),
            taxonomy=taxonomy,
            chunks=chunks,
            document={
                "id": 1330246,
                "title": "关于股东权益变动的提示性公告",
                "ts_code": "600958.SH",
                "name": "东方证券股份有限公司",
                "published_at": "2026-07-31T00:00:00+00:00",
            },
            entity_whitelist=[{
                "entity_id": "600958.SH",
                "name": "东方证券股份有限公司",
                "allowed_roles": ["issuer"],
            }],
            taxonomy_candidates=["shareholder_change"],
        )

        self.assertEqual(compiled.accepted_mentions, 0)
        self.assertIn(
            "mention_shareholder_action_invalid",
            compiled.rejected_mentions[0].reason_codes,
        )
        self.assertEqual(compiled.dropped_items, 0)

    def test_shareholder_action_rejects_generic_title_label(self) -> None:
        from stock_analyze.intelligence.semantic.mention_compiler import (
            _valid_shareholder_action,
        )

        self.assertFalse(_valid_shareholder_action("权益变动"))
        self.assertTrue(_valid_shareholder_action("持股比例下降"))

    def test_unknown_fields_fail_closed(self) -> None:
        payload = copy.deepcopy(valid_mention_payload())
        payload["mentions"][0]["confidence"] = 0.99

        with self.assertRaises(MentionContractError) as raised:
            parse_mention_document_result(payload)
        self.assertEqual(raised.exception.code, "mention_schema_invalid")

if __name__ == "__main__":
    unittest.main()
