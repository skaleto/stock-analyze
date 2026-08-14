from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.quality import (
    evaluate_files,
    evaluate_rows,
)


def _event(
    event_type: str,
    *,
    entity_id: str,
    amount: float | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, object]:
    facts: list[dict[str, object]] = []
    if amount is not None:
        facts.append(
            {
                "name": "amount",
                "numeric_value": amount,
                "raw_value": str(amount),
                "unit": "yuan",
                "currency": "CNY",
                "evidence_ids": evidence_ids or [],
            }
        )
    return {
        "event_type": event_type,
        "subjects": [
            {
                "entity_id": entity_id,
                "role": "issuer",
                "evidence_ids": evidence_ids or [],
            }
        ],
        "facts": facts,
        "effective_dates": [],
        "conditions": [],
        "conflicts": [],
        "lifecycle": "announced",
    }


class SemanticQualityTest(unittest.TestCase):
    def test_grounding_metric_uses_the_production_width_whitespace_contract(self) -> None:
        result = evaluate_rows(
            [{"document_id": 1, "events": [], "evidence": []}],
            [{
                "document_id": 1,
                "events": [],
                "evidence": [{
                    "evidence_id": "e1",
                    "chunk_id": "c1",
                    "quote": "1,250,000股",
                }],
                "source_chunks": [{
                    "chunk_id": "c1",
                    "text": "本次质押1,250,\n000 股",
                }],
            }],
        )

        self.assertEqual(result["evidence_grounding"]["value"], 1.0)

    def test_numeric_accuracy_normalizes_raw_value_and_source_unit(self) -> None:
        reference_event = _event(
            "guarantee",
            entity_id="000001.SZ",
            amount=10_000_000.0,
        )
        reference_event["facts"][0]["unit"] = "CNY"
        reference_event["facts"][0]["currency"] = "CNY"
        prediction_event = _event(
            "guarantee",
            entity_id="000001.SZ",
        )
        prediction_event["facts"] = [{
            "name": "amount",
            "numeric_value": None,
            "raw_value": "1,000",
            "unit": "万元",
            "currency": None,
            "evidence_ids": [],
        }]

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference_event], "evidence": []}],
            [{"document_id": 1, "events": [prediction_event], "evidence": []}],
        )

        self.assertEqual(result["numeric_exact_match"]["denominator"], 1)
        self.assertEqual(result["numeric_exact_match"]["value"], 1.0)
        self.assertEqual(result["numeric_extracted_precision"]["value"], 1.0)
        self.assertEqual(result["numeric_reference_coverage"]["value"], 1.0)

    def test_numeric_correctness_is_separate_from_reference_coverage(self) -> None:
        reference_event = _event(
            "guarantee",
            entity_id="000001.SZ",
            amount=100.0,
        )
        reference_event["facts"].append({
            "name": "guarantee_ratio",
            "numeric_value": 0.2,
            "raw_value": "20%",
            "unit": "%",
            "currency": None,
            "evidence_ids": [],
        })
        prediction_event = _event(
            "guarantee",
            entity_id="000001.SZ",
            amount=100.0,
        )

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference_event], "evidence": []}],
            [{"document_id": 1, "events": [prediction_event], "evidence": []}],
        )

        self.assertEqual(result["numeric_extracted_precision"]["value"], 1.0)
        self.assertEqual(result["numeric_extracted_precision"]["denominator"], 1)
        self.assertEqual(result["numeric_reference_coverage"]["value"], 0.5)
        self.assertEqual(result["numeric_reference_coverage"]["denominator"], 2)
        self.assertEqual(result["numeric_exact_match"]["value"], 0.5)

    def test_event_metrics_count_document_family_presence_not_mentions(self) -> None:
        reference_event = _event("guarantee", entity_id="000001.SZ")
        result = evaluate_rows(
            [{"document_id": 1, "events": [reference_event], "evidence": []}],
            [{
                "document_id": 1,
                "events": [
                    _event("guarantee", entity_id="000001.SZ"),
                    _event("guarantee", entity_id="000001.SZ"),
                ],
                "evidence": [],
            }],
        )

        self.assertEqual(result["event_counts"], {"tp": 1, "fp": 0, "fn": 0})
        self.assertEqual(
            result["per_family"]["guarantee"],
            {"reference": 1, "predicted": 1, "tp": 1, "fp": 0, "fn": 0},
        )

    def test_numeric_metrics_ignore_numbers_inside_text_facts(self) -> None:
        reference_event = _event(
            "shareholder_change",
            entity_id="000001.SZ",
            amount=100.0,
        )
        reference_event["facts"].append({
            "name": "action",
            "numeric_value": None,
            "raw_value": "2025年员工持股计划所持股票全部出售",
            "unit": None,
            "currency": None,
            "evidence_ids": [],
        })
        prediction_event = _event(
            "shareholder_change",
            entity_id="000001.SZ",
            amount=100.0,
        )
        prediction_event["facts"].append({
            "name": "action",
            "numeric_value": None,
            "raw_value": "出售2025年员工持股计划股票",
            "unit": None,
            "currency": None,
            "evidence_ids": [],
        })

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference_event], "evidence": []}],
            [{"document_id": 1, "events": [prediction_event], "evidence": []}],
        )

        self.assertEqual(result["numeric_exact_match"]["denominator"], 1)
        self.assertEqual(result["numeric_extracted_precision"]["denominator"], 1)
        self.assertEqual(result["numeric_extracted_precision"]["value"], 1.0)

    def test_numeric_metrics_sum_atomic_events_against_one_aggregate_gold(self) -> None:
        reference = _event("pledge_freeze", entity_id="000001.SZ")
        reference["facts"] = [
            {
                "name": "share_count",
                "numeric_value": 9_000_000,
                "raw_value": "9,000,000股",
                "unit": "股",
                "currency": None,
                "evidence_ids": [],
            },
            {
                "name": "share_ratio",
                "numeric_value": 0.1206,
                "raw_value": "12.06%",
                "unit": "%",
                "currency": None,
                "evidence_ids": [],
            },
        ]

        def atomic(count: int, ratio: str) -> dict[str, object]:
            event = _event("pledge_freeze", entity_id="000001.SZ")
            event["facts"] = [
                {
                    "name": "share_count",
                    "numeric_value": None,
                    "raw_value": f"{count:,}",
                    "unit": "股",
                    "currency": None,
                    "evidence_ids": [],
                },
                {
                    "name": "share_ratio",
                    "numeric_value": None,
                    "raw_value": ratio,
                    "unit": "%",
                    "currency": None,
                    "evidence_ids": [],
                },
            ]
            return event

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference], "evidence": []}],
            [{
                "document_id": 1,
                "events": [atomic(6_000_000, "8.04%"), atomic(3_000_000, "4.02%")],
                "evidence": [],
            }],
        )

        self.assertEqual(result["numeric_exact_match"]["value"], 1.0)
        self.assertEqual(result["numeric_extracted_precision"]["denominator"], 2)

    def test_numeric_metrics_understand_per_share_fact_semantics(self) -> None:
        reference = _event("dividend", entity_id="000001.SZ")
        reference["facts"] = [{
            "name": "cash_per_share",
            "numeric_value": 0.15,
            "raw_value": "每10股派发现金红利1.50元",
            "unit": "CNY/share",
            "currency": "CNY",
            "evidence_ids": [],
        }]
        prediction = _event("dividend", entity_id="000001.SZ")
        prediction["facts"] = [{
            "name": "distribution_plan",
            "numeric_value": None,
            "raw_value": "向全体股东每10股派发现金红利人民币1.50元",
            "unit": None,
            "currency": None,
            "evidence_ids": [],
        }, {
            "name": "cash_per_share",
            "numeric_value": None,
            "raw_value": "1.50",
            "unit": "元",
            "currency": None,
            "evidence_ids": [],
        }]

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference], "evidence": []}],
            [{"document_id": 1, "events": [prediction], "evidence": []}],
        )

        self.assertEqual(result["numeric_exact_match"]["value"], 1.0)

    def test_numeric_metrics_treat_issue_price_as_per_share_price(self) -> None:
        reference = _event("merger_restructuring", entity_id="000001.SZ")
        reference["facts"] = [{
            "name": "issue_price",
            "numeric_value": 16.92,
            "raw_value": "16.92元/股",
            "unit": "CNY/share",
            "currency": "CNY",
            "evidence_ids": [],
        }]
        prediction = _event("merger_restructuring", entity_id="000001.SZ")
        prediction["facts"] = [{
            "name": "issue_price",
            "numeric_value": None,
            "raw_value": "16.92元",
            "unit": "元",
            "currency": None,
            "evidence_ids": [],
        }]

        result = evaluate_rows(
            [{"document_id": 1, "events": [reference], "evidence": []}],
            [{"document_id": 1, "events": [prediction], "evidence": []}],
        )

        self.assertEqual(result["numeric_exact_match"]["value"], 1.0)

    def test_reports_event_and_grounding_quality_with_intervals(self) -> None:
        references = [
            {
                "document_id": 1,
                "events": [
                    _event(
                        "capacity_expansion",
                        entity_id="000001.SZ",
                        amount=100.0,
                    )
                ],
                "evidence": [],
                "no_event_reason": None,
            },
            {
                "document_id": 2,
                "events": [],
                "evidence": [],
                "no_event_reason": "not a current event",
            },
            {
                "document_id": 3,
                "events": [
                    _event("dividend", entity_id="000003.SZ")
                ],
                "evidence": [],
                "no_event_reason": None,
            },
        ]
        predictions = [
            {
                "document_id": 1,
                "events": [
                    _event(
                        "capacity_expansion",
                        entity_id="000001.SZ",
                        amount=100.0,
                        evidence_ids=["p1"],
                    ),
                    _event("guarantee", entity_id="000001.SZ"),
                ],
                "evidence": [
                    {
                        "evidence_id": "p1",
                        "chunk_id": "c1",
                        "quote": "项目投资100亿元",
                    }
                ],
                "source_chunks": [
                    {"chunk_id": "c1", "text": "公司拟建设项目投资100亿元。"}
                ],
                "no_event_reason": None,
            },
            {
                "document_id": 2,
                "events": [],
                "evidence": [],
                "source_chunks": [],
                "no_event_reason": "no event",
            },
            {
                "document_id": 3,
                "events": [],
                "evidence": [],
                "source_chunks": [],
                "no_event_reason": "missed",
            },
        ]

        result = evaluate_rows(references, predictions)

        self.assertEqual(result["documents"], 3)
        self.assertEqual(result["event_counts"], {"tp": 1, "fp": 1, "fn": 1})
        self.assertEqual(result["event_precision"]["value"], 0.5)
        self.assertEqual(result["event_recall"]["value"], 0.5)
        self.assertEqual(result["event_document_false_negative_rate"]["value"], 0.5)
        self.assertEqual(result["evidence_grounding"]["value"], 1.0)
        self.assertEqual(result["entity_accuracy"]["value"], 1.0)
        self.assertEqual(result["numeric_exact_match"]["value"], 1.0)
        self.assertEqual(result["per_family"]["capacity_expansion"]["tp"], 1)
        self.assertEqual(result["per_family"]["dividend"]["fn"], 1)
        self.assertEqual(result["per_family"]["guarantee"]["fp"], 1)
        self.assertEqual(len(result["event_precision"]["wilson_95"]), 2)

    def test_evaluate_files_writes_machine_readable_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "reference.jsonl"
            predictions = root / "predictions.jsonl"
            output = root / "quality.json"
            reference.write_text(
                json.dumps(
                    {
                        "document_id": 1,
                        "events": [],
                        "evidence": [],
                        "no_event_reason": "none",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            predictions.write_text(reference.read_text(encoding="utf-8"), encoding="utf-8")

            result = evaluate_files(reference, predictions, output)

            self.assertEqual(result["documents"], 1)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["schema_version"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
