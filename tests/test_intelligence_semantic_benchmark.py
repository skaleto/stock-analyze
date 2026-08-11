from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from stock_analyze.intelligence.semantic.benchmark import (
    BenchmarkError,
    BenchmarkFloors,
    BenchmarkManifestCandidate,
    CandidateIdentity,
    FrozenBenchmark,
    PromotionRejected,
    StratificationPolicy,
    canonical_json_hash,
    create_benchmark_report,
    evaluate_predictions,
    materialize_candidate_outputs,
    promote_candidate,
    resolve_production_champion,
    run_frozen_benchmark,
    _adjudicated_prediction,
    _gold_record_from_prediction,
    _prediction_consensus_signature,
    _select_manifest_candidates,
    validate_frozen_benchmark,
    write_immutable_benchmark_report,
    finalize_benchmark_gold,
)
from stock_analyze.intelligence.semantic.provider import (
    SemanticInputBundle,
    SemanticProviderError,
    SemanticProviderIdentity,
    SemanticProviderResponse,
)
from stock_analyze.intelligence.semantic import benchmark as semantic_benchmark


def _sha(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _event(
    event_type: str,
    *,
    entity_id: str,
    numeric_value: int,
    evidence_id: str,
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "lifecycle": "completed",
        "subjects": [
            {
                "entity_id": entity_id,
                "role": "issuer",
                "evidence_ids": [evidence_id],
            }
        ],
        "facts": [
            {
                "name": "amount",
                "numeric_value": numeric_value,
                "unit": "CNY",
                "currency": "CNY",
                "period": None,
                "evidence_ids": [evidence_id],
            }
        ],
        "effective_dates": [],
        "conditions": [],
        "conflicts": [],
    }


def _span(
    evidence_id: str,
    *,
    page_number: int = 1,
    start: int = 0,
    end: int = 8,
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "page_number": page_number,
        "chunk_id": f"chunk-{page_number}",
        "start": start,
        "end": end,
        "content_hash": _sha(f"{evidence_id}:{page_number}:{start}:{end}"),
    }


def _prediction(
    document_id: str,
    *,
    events: list[dict[str, object]],
    evidence: list[dict[str, object]],
    schema_valid: bool = True,
) -> dict[str, object]:
    return {
        "document_id": document_id,
        "schema_valid": schema_valid,
        "events": events,
        "evidence": evidence,
        "no_event_reason": None if events else "no material event",
        "latency_ms": 100,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "cost": 0.01,
        },
    }


class SemanticBenchmarkMetricTest(unittest.TestCase):
    def test_gold_flow_relocates_prediction_before_consensus(
        self,
    ) -> None:
        relocate = getattr(
            semantic_benchmark,
            "_relocate_prediction_evidence",
            None,
        )
        self.assertIsNotNone(relocate)
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE document_chunks (
                document_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO document_chunks VALUES (7, 'chunk-1', 1, '公司拟回购金额')"
        )
        prediction = {
            "document_id": 7,
            "events": [],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "page_number": 1,
                    "chunk_id": "chunk-1",
                    "start": 0,
                    "end": 2,
                    "quote": "回购金额",
                }
            ],
            "no_event_reason": "test",
        }

        relocated = relocate(
            connection,
            prediction=prediction,
            document_id=7,
        )

        self.assertEqual(
            (
                relocated["evidence"][0]["start"],
                relocated["evidence"][0]["end"],
            ),
            (3, 7),
        )

    def test_gold_evidence_quote_must_match_relocatable_span(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE document_chunks (
                document_id INTEGER NOT NULL,
                chunk_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO document_chunks VALUES (7, 'chunk-1', 1, '业绩预告修正')"
        )
        prediction = {
            "document_id": 7,
            "events": [],
            "evidence": [
                {
                    "evidence_id": "manual-1",
                    "page_number": 1,
                    "chunk_id": "chunk-1",
                    "start": 0,
                    "end": 6,
                    "quote": "不匹配内容",
                }
            ],
            "no_event_reason": "administrative notice",
        }

        with self.assertRaisesRegex(
            BenchmarkError,
            "benchmark_gold_evidence_quote_mismatch",
        ):
            _gold_record_from_prediction(
                connection,
                prediction=prediction,
                artifact_hash=_sha("artifact"),
                annotator="reviewer",
                adjudicated_at="2026-07-26T00:00:00+00:00",
            )

    def test_reviewer_adjudication_accepts_schema_valid_correction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                Path("configs/intelligence_event_taxonomy_v1.json"),
                root / "configs/intelligence_event_taxonomy_v1.json",
            )
            evidence = {
                "evidence_id": "manual-1",
                "page_number": 1,
                "chunk_id": "chunk-1",
                "start": 0,
                "end": 8,
                "quote": "业绩预告修正",
            }
            event = {
                "event_type": "earnings_forecast",
                "lifecycle": "revised",
                "subjects": [
                    {
                        "entity_id": "000001.SZ",
                        "role": "issuer",
                        "evidence_ids": ["manual-1"],
                    }
                ],
                "facts": [],
                "effective_dates": [],
                "conditions": [],
                "conflicts": [],
                "missing_required_fields": ["forecast_direction"],
            }

            prediction = _adjudicated_prediction(
                {
                    "events": [event],
                    "evidence": [evidence],
                    "no_event_reason": None,
                },
                document_id=7,
                root=root,
            )

            self.assertEqual(prediction["document_id"], 7)
            self.assertEqual(
                prediction["events"][0]["event_type"],
                "earnings_forecast",
            )

    def test_reviewer_adjudication_rejects_unvalidated_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            shutil.copy(
                Path("configs/intelligence_event_taxonomy_v1.json"),
                root / "configs/intelligence_event_taxonomy_v1.json",
            )

            with self.assertRaisesRegex(
                BenchmarkError,
                "benchmark_adjudication_payload_invalid",
            ):
                _adjudicated_prediction(
                    {
                        "events": [],
                        "evidence": [],
                        "no_event_reason": None,
                    },
                    document_id=7,
                    root=root,
                )

    def test_candidate_consensus_ignores_local_evidence_ids_only(
        self,
    ) -> None:
        left = _prediction(
            "d1",
            events=[
                _event(
                    "buyback",
                    entity_id="000001",
                    numeric_value=100,
                    evidence_id="left",
                )
            ],
            evidence=[_span("left")],
        )
        right = _prediction(
            "d1",
            events=[
                _event(
                    "buyback",
                    entity_id="000001",
                    numeric_value=100,
                    evidence_id="right",
                )
            ],
            evidence=[_span("right")],
        )

        self.assertEqual(
            _prediction_consensus_signature(left),
            _prediction_consensus_signature(right),
        )
        right["evidence"][0]["end"] = 9
        self.assertNotEqual(
            _prediction_consensus_signature(left),
            _prediction_consensus_signature(right),
        )

    def test_six_document_micro_metrics_are_exact(self) -> None:
        gold = [
            {
                "document_id": "d1",
                "events": [_event("buyback", entity_id="000001", numeric_value=100, evidence_id="e1")],
                "evidence_spans": [_span("e1")],
                "no_event_reason": None,
            },
            {
                "document_id": "d2",
                "events": [_event("major_contract", entity_id="000002", numeric_value=200, evidence_id="e2")],
                "evidence_spans": [_span("e2")],
                "no_event_reason": None,
            },
            {
                "document_id": "d3",
                "events": [_event("dividend", entity_id="000003", numeric_value=300, evidence_id="e3")],
                "evidence_spans": [_span("e3")],
                "no_event_reason": None,
            },
            {
                "document_id": "d4",
                "events": [],
                "evidence_spans": [],
                "no_event_reason": "administrative notice",
            },
            {
                "document_id": "d5",
                "events": [],
                "evidence_spans": [],
                "no_event_reason": "administrative notice",
            },
            {
                "document_id": "d6",
                "events": [_event("litigation_arbitration", entity_id="000006", numeric_value=600, evidence_id="e6")],
                "evidence_spans": [_span("e6")],
                "no_event_reason": None,
            },
        ]
        predictions = [
            _prediction(
                "d1",
                events=[_event("buyback", entity_id="000001", numeric_value=100, evidence_id="p1")],
                evidence=[_span("p1")],
            ),
            _prediction(
                "d2",
                events=[_event("major_contract", entity_id="WRONG", numeric_value=200, evidence_id="p2")],
                evidence=[_span("p2")],
            ),
            _prediction("d3", events=[], evidence=[]),
            _prediction(
                "d4",
                events=[_event("buyback", entity_id="000004", numeric_value=400, evidence_id="p4")],
                evidence=[_span("p4")],
            ),
            _prediction("d5", events=[], evidence=[], schema_valid=False),
            _prediction(
                "d6",
                events=[
                    _event(
                        "litigation_arbitration",
                        entity_id="000006",
                        numeric_value=601,
                        evidence_id="p6",
                    )
                ],
                evidence=[_span("p6", page_number=2)],
            ),
        ]

        metrics = evaluate_predictions(gold, predictions)

        self.assertEqual(metrics.document_count, 6)
        self.assertAlmostEqual(metrics.schema_validity, 5 / 6)
        self.assertEqual(metrics.event_true_positive, 3)
        self.assertEqual(metrics.event_false_positive, 1)
        self.assertEqual(metrics.event_false_negative, 1)
        self.assertEqual(metrics.event_precision, 0.75)
        self.assertEqual(metrics.event_recall, 0.75)
        self.assertEqual(metrics.evidence_grounding, 0.5)
        self.assertEqual(metrics.entity_accuracy, 0.4)
        self.assertEqual(metrics.numeric_exact_match, 0.4)
        self.assertEqual(metrics.no_event_false_negative_rate, 0.25)
        self.assertEqual(metrics.no_event_false_negative_count, 1)
        self.assertEqual(metrics.positive_document_count, 4)

    def test_missing_prediction_is_schema_failure_and_positive_false_negative(self) -> None:
        gold = [
            {
                "document_id": "missing",
                "events": [_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")],
                "evidence_spans": [_span("e1")],
                "no_event_reason": None,
            }
        ]

        metrics = evaluate_predictions(gold, [])

        self.assertEqual(metrics.schema_validity, 0.0)
        self.assertEqual(metrics.event_recall, 0.0)
        self.assertEqual(metrics.no_event_false_negative_rate, 1.0)

    def test_duplicate_or_unknown_prediction_document_is_rejected(self) -> None:
        gold = [{"document_id": "d1", "events": [], "evidence_spans": [], "no_event_reason": "none"}]
        duplicate = [
            _prediction("d1", events=[], evidence=[]),
            _prediction("d1", events=[], evidence=[]),
        ]
        with self.assertRaisesRegex(BenchmarkError, "benchmark_prediction_document_duplicate"):
            evaluate_predictions(gold, duplicate)
        with self.assertRaisesRegex(BenchmarkError, "benchmark_prediction_document_unknown"):
            evaluate_predictions(gold, [_prediction("other", events=[], evidence=[])])


class FinalizeBenchmarkGoldDocumentKeyTest(unittest.TestCase):
    """Regression: finalize must key consensus/queue/decision dicts by int.

    ``_materialization_manifest`` returns an int-keyed manifest map, so
    ``ordered_ids`` are ints. The adjudication lookup dicts
    (``consensus_by_id`` / ``queue_by_id`` / ``decision_by_id``) must be
    int-keyed too, or ``document_id in consensus_by_id`` is always False and
    ``queue_by_id[document_id]`` raises ``KeyError`` on the real numeric
    document ids. The str-keyed revision crashed end-to-end finalize; see the
    correction handoff P0.6.
    """

    def _write_numeric_benchmark(self, root: Path) -> Path:
        benchmark_dir = (
            root / "data/shared/intelligence/benchmarks/synthetic-v1"
        )
        benchmark_dir.mkdir(parents=True)
        manifest: list[dict[str, object]] = []
        consensus: list[dict[str, object]] = []
        for index in range(1, 7):
            family = "buyback" if index <= 4 else "no_event"
            document_id = 1000 + index
            artifact_hash = _sha(f"artifact-{index}")
            manifest.append(
                {
                    "document_id": document_id,
                    "document_hash": _sha(f"document-{index}"),
                    "artifact_hash": artifact_hash,
                    "event_family": family,
                    "table_heavy": index in {1, 2},
                    "ocr_required": index == 3,
                    "revision_chain_id": "rev-1" if index == 4 else None,
                    "year": 2024 if index <= 3 else 2025,
                    "exchange": "SSE" if index % 2 else "SZSE",
                    "length_bucket": ("short", "medium", "long")[index % 3],
                    "issuer_industry": (
                        "technology" if index % 2 else "industrial"
                    ),
                }
            )
            events = (
                []
                if family == "no_event"
                else [
                    _event(
                        "buyback",
                        entity_id=f"00000{index}",
                        numeric_value=index,
                        evidence_id=f"e{index}",
                    )
                ]
            )
            annotation = {
                "document_id": document_id,
                "artifact_hash": artifact_hash,
                "annotator": "reviewer-a+reviewer-b/adjudicator",
                "adjudicated_at": "2026-07-25T00:00:00+00:00",
                "events": events,
                "evidence_spans": [] if not events else [_span(f"e{index}")],
                "no_event_reason": (
                    "administrative notice" if not events else None
                ),
            }
            annotation["annotation_hash"] = canonical_json_hash(annotation)
            consensus.append(annotation)
        manifest_path = benchmark_dir / "manifest.jsonl"
        manifest_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest),
            encoding="utf-8",
        )
        (benchmark_dir / "gold_consensus.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in consensus),
            encoding="utf-8",
        )
        (benchmark_dir / "adjudication_queue.jsonl").write_text("", encoding="utf-8")
        decisions_path = benchmark_dir / "decisions.jsonl"
        decisions_path.write_text("", encoding="utf-8")
        return decisions_path

    def test_finalize_matches_consensus_by_int_document_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions_path = self._write_numeric_benchmark(root)
            benchmark_dir = (
                root / "data/shared/intelligence/benchmarks/synthetic-v1"
            )
            gold_path = benchmark_dir / "gold.jsonl"
            frozen = FrozenBenchmark(
                name="synthetic-v1",
                manifest_path=benchmark_dir / "manifest.jsonl",
                gold_path=gold_path,
                manifest_hash=_sha("manifest"),
                gold_hash=_sha("gold"),
                benchmark_hash=_sha("benchmark"),
                document_count=6,
                manifest_records=(),
                gold_records=(),
            )
            # Stratum validation and taxonomy loading are covered by
            # FrozenBenchmarkValidationTest; isolate the document-key
            # matching logic under test here.
            with patch(
                "stock_analyze.intelligence.semantic.benchmark.validate_frozen_benchmark",
                return_value=frozen,
            ), patch(
                "stock_analyze.intelligence.semantic.benchmark._load_event_families",
                return_value=("buyback",),
            ):
                result = finalize_benchmark_gold(
                    root,
                    benchmark_name="synthetic-v1",
                    decisions_path=decisions_path,
                )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["documents"], 6)
            self.assertTrue(gold_path.exists())
            rows = [
                json.loads(line)
                for line in gold_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 6)


class FrozenBenchmarkValidationTest(unittest.TestCase):
    event_families = ("buyback", "dividend")
    policy = StratificationPolicy(
        events_per_family=2,
        no_event_documents=2,
        minimum_table_heavy_ratio=0.25,
        minimum_ocr_ratio=0.10,
        minimum_revision_chain_ratio=0.15,
    )

    def _write_frozen_files(self, root: Path) -> tuple[Path, Path]:
        benchmark_dir = root / "data/shared/intelligence/benchmarks/synthetic-v1"
        benchmark_dir.mkdir(parents=True)
        manifest_path = benchmark_dir / "manifest.jsonl"
        gold_path = benchmark_dir / "gold.jsonl"
        families = ["buyback", "buyback", "dividend", "dividend", "no_event", "no_event"]
        manifest: list[dict[str, object]] = []
        gold: list[dict[str, object]] = []
        for index, family in enumerate(families, start=1):
            document_id = f"d{index}"
            artifact_hash = _sha(f"artifact-{index}")
            row = {
                "document_id": document_id,
                "document_hash": _sha(f"document-{index}"),
                "artifact_hash": artifact_hash,
                "event_family": family,
                "table_heavy": index in {1, 2},
                "ocr_required": index == 3,
                "revision_chain_id": "revision-1" if index == 4 else None,
                "year": 2024 if index <= 3 else 2025,
                "exchange": "SSE" if index % 2 else "SZSE",
                "length_bucket": ("short", "medium", "long")[index % 3],
                "issuer_industry": "technology" if index % 2 else "industrial",
            }
            manifest.append(row)
            events = (
                []
                if family == "no_event"
                else [_event(family, entity_id=f"00000{index}", numeric_value=index, evidence_id=f"e{index}")]
            )
            annotation = {
                "document_id": document_id,
                "artifact_hash": artifact_hash,
                "annotator": "reviewer-a+reviewer-b/adjudicator",
                "adjudicated_at": "2026-07-25T00:00:00+00:00",
                "events": events,
                "evidence_spans": [] if not events else [_span(f"e{index}")],
                "no_event_reason": "administrative notice" if not events else None,
            }
            annotation["annotation_hash"] = canonical_json_hash(annotation)
            gold.append(annotation)
        manifest_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in manifest),
            encoding="utf-8",
        )
        gold_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in gold),
            encoding="utf-8",
        )
        return manifest_path, gold_path

    def test_production_policy_is_exactly_240_documents(self) -> None:
        policy = StratificationPolicy.production(
            tuple(f"family-{index}" for index in range(15))
        )
        self.assertEqual(policy.event_family_count, 15)
        self.assertEqual(policy.events_per_family, 12)
        self.assertEqual(policy.no_event_documents, 60)
        self.assertEqual(policy.expected_document_count, 15 * 12 + 60)

    def test_manifest_and_gold_are_hashed_and_machine_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, gold_path = self._write_frozen_files(root)

            frozen = validate_frozen_benchmark(
                manifest_path,
                gold_path,
                event_families=self.event_families,
                policy=self.policy,
                benchmark_name="synthetic-v1",
            )

            self.assertEqual(frozen.document_count, 6)
            self.assertEqual(len(frozen.manifest_hash), 64)
            self.assertEqual(len(frozen.gold_hash), 64)
            self.assertEqual(len(frozen.benchmark_hash), 64)
            self.assertNotEqual(frozen.manifest_hash, frozen.gold_hash)

    def test_manifest_rejects_copied_pdf_text_and_bad_strata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, gold_path = self._write_frozen_files(root)
            rows = [json.loads(line) for line in manifest_path.read_text().splitlines()]
            rows[0]["pdf_text"] = "forbidden body"
            manifest_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BenchmarkError, "benchmark_manifest_copies_pdf_text"):
                validate_frozen_benchmark(
                    manifest_path,
                    gold_path,
                    event_families=self.event_families,
                    policy=self.policy,
                    benchmark_name="synthetic-v1",
                )

            manifest_path, gold_path = self._write_frozen_files(root / "second")
            rows = [json.loads(line) for line in manifest_path.read_text().splitlines()]
            for row in rows:
                row["ocr_required"] = False
            manifest_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BenchmarkError, "benchmark_manifest_ocr_coverage"):
                validate_frozen_benchmark(
                    manifest_path,
                    gold_path,
                    event_families=self.event_families,
                    policy=self.policy,
                    benchmark_name="synthetic-v1",
                )

    def test_gold_requires_adjudication_hash_and_hash_only_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, gold_path = self._write_frozen_files(root)
            rows = [json.loads(line) for line in gold_path.read_text().splitlines()]
            rows[0]["evidence_spans"][0]["quote"] = "copied from PDF"
            rows[0]["annotation_hash"] = canonical_json_hash(
                {key: value for key, value in rows[0].items() if key != "annotation_hash"}
            )
            gold_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BenchmarkError, "benchmark_gold_copies_pdf_text"):
                validate_frozen_benchmark(
                    manifest_path,
                    gold_path,
                    event_families=self.event_families,
                    policy=self.policy,
                    benchmark_name="synthetic-v1",
                )

    def test_gold_must_match_manifest_event_stratum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, gold_path = self._write_frozen_files(root)
            rows = [
                json.loads(line)
                for line in gold_path.read_text().splitlines()
            ]
            rows[0]["events"] = []
            rows[0]["evidence_spans"] = []
            rows[0]["no_event_reason"] = "wrong stratum"
            rows[0]["annotation_hash"] = canonical_json_hash(
                {
                    key: value
                    for key, value in rows[0].items()
                    if key != "annotation_hash"
                }
            )
            gold_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                BenchmarkError,
                "benchmark_gold_event_family_stratum_mismatch",
            ):
                validate_frozen_benchmark(
                    manifest_path,
                    gold_path,
                    event_families=self.event_families,
                    policy=self.policy,
                    benchmark_name="synthetic-v1",
                )


class BenchmarkManifestSelectionTest(unittest.TestCase):
    def test_selector_enforces_family_and_operational_strata(self) -> None:
        families = ("buyback", "dividend")
        policy = StratificationPolicy(
            events_per_family=2,
            no_event_documents=2,
            minimum_table_heavy_ratio=0.25,
            minimum_ocr_ratio=0.10,
            minimum_revision_chain_ratio=0.15,
        )
        candidates: list[BenchmarkManifestCandidate] = []
        document_id = 0
        for family in (*families, "no_event"):
            for variant in range(4):
                document_id += 1
                candidates.append(
                    BenchmarkManifestCandidate(
                        document_id=document_id,
                        document_hash=_sha(
                            f"document-{document_id}"
                        ),
                        artifact_hash=_sha(
                            f"artifact-{document_id}"
                        ),
                        event_family=family,
                        table_heavy=variant in {0, 1},
                        ocr_required=variant == 0,
                        revision_chain_id=(
                            f"revision-{family}"
                            if variant == 1
                            else None
                        ),
                        year=2024 + (variant % 2),
                        exchange=(
                            "SSE" if variant % 2 else "SZSE"
                        ),
                        length_bucket=(
                            "short",
                            "medium",
                            "long",
                            "medium",
                        )[variant],
                        issuer_industry=(
                            "technology"
                            if variant % 2
                            else "industrial"
                        ),
                    )
                )

        selected = _select_manifest_candidates(
            candidates,
            event_families=families,
            policy=policy,
        )

        self.assertEqual(len(selected), 6)
        self.assertEqual(
            Counter(row.event_family for row in selected),
            Counter(
                {"buyback": 2, "dividend": 2, "no_event": 2}
            ),
        )
        self.assertGreaterEqual(
            sum(row.table_heavy for row in selected),
            2,
        )
        self.assertGreaterEqual(
            sum(row.ocr_required for row in selected),
            1,
        )
        self.assertGreaterEqual(
            sum(
                row.revision_chain_id is not None
                for row in selected
            ),
            1,
        )


class CandidateMaterializationTest(unittest.TestCase):
    class FakeProvider:
        identity = SemanticProviderIdentity(
            provider="openai-compatible",
            model="deepseek-v4-pro",
            endpoint_host="api.deepseek.com",
        )

        def __init__(self) -> None:
            self.document_ids: list[int] = []

        def extract(self, bundle, *, response_schema):
            del response_schema
            document_id = int(bundle.document_id)
            self.document_ids.append(document_id)
            parsed_output = {
                "document_id": document_id,
                "schema_version": "announcement-events-v1",
                "events": [],
                "evidence": [],
                "no_event_reason": "no material event",
            }
            return SemanticProviderResponse(
                identity=self.identity,
                parsed_output=parsed_output,
                raw_output=json.dumps(parsed_output),
                input_hash=_sha(f"input-{document_id}"),
                output_hash=_sha(f"output-{document_id}"),
                request_id=f"request-{document_id}",
                response_model=self.identity.model,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=100,
            )

    def _root(self, tmp: str) -> tuple[Path, list[dict[str, object]]]:
        root = Path(tmp)
        (root / "configs" / "research").mkdir(parents=True)
        shutil.copy(
            Path("configs/research/intelligence_semantic_benchmark.yaml"),
            root / "configs/research/intelligence_semantic_benchmark.yaml",
        )
        shutil.copy(
            Path("configs/intelligence_event_taxonomy_v1.json"),
            root / "configs/intelligence_event_taxonomy_v1.json",
        )
        benchmark_dir = (
            root
            / "data/shared/intelligence/benchmarks/announcement-v1"
        )
        benchmark_dir.mkdir(parents=True)
        manifest = [
            {
                "document_id": index,
                "document_hash": _sha(f"document-{index}"),
                "artifact_hash": _sha(f"artifact-{index}"),
            }
            for index in (1, 2)
        ]
        (benchmark_dir / "manifest.jsonl").write_text(
            "".join(
                json.dumps(row, sort_keys=True) + "\n"
                for row in manifest
            ),
            encoding="utf-8",
        )
        return root, manifest

    @staticmethod
    def _bundle_builder(
        manifest: list[dict[str, object]],
    ):
        by_id = {int(row["document_id"]): row for row in manifest}

        def build(document_id: int) -> SemanticInputBundle:
            row = by_id[document_id]
            return SemanticInputBundle(
                document_id=document_id,
                artifact_hash=str(row["artifact_hash"]),
                parser_version="announcement-layout-v1",
                prompt_version="announcement-event-v2",
                schema_version="announcement-events-v1",
                taxonomy_version="cn-announcement-taxonomy-v1",
                payload={
                    "document": {"id": document_id},
                    "taxonomy_candidates": [],
                    "entity_whitelist": [],
                    "chunks": [],
                    "tables": [],
                    "revision_context": [],
                },
            )

        return build

    def test_materialization_is_bounded_resumable_and_identity_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            provider = self.FakeProvider()
            environment = {
                **os.environ,
                "INTELLIGENCE_LLM_MODEL_CANDIDATE_A": "deepseek-v4-pro",
            }
            with patch.dict(os.environ, environment, clear=True):
                first = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=1,
                    provider=provider,
                    bundle_builder=self._bundle_builder(manifest),
                )
                second = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=2,
                    provider=provider,
                    bundle_builder=self._bundle_builder(manifest),
                )

            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["remaining"], 1)
            self.assertEqual(second["status"], "complete")
            self.assertEqual(second["remaining"], 0)
            self.assertEqual(provider.document_ids, [1, 2])
            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            rows = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [row["document_id"] for row in rows],
                [1, 2],
            )
            self.assertTrue(all(row["schema_valid"] for row in rows))
            self.assertTrue(
                all(
                    row["candidate"]["model"] == "deepseek-v4-pro"
                    for row in rows
                )
            )
            self.assertTrue(
                all(
                    len(
                        row["candidate"][
                            "generation_config_hash"
                        ]
                    )
                    == 64
                    for row in rows
                )
            )
            self.assertEqual(
                [row["artifact_hash"] for row in rows],
                [row["artifact_hash"] for row in manifest],
            )

    def test_materialization_relocates_one_exact_quote_in_named_chunk(
        self,
    ) -> None:
        class EvidenceProvider(self.FakeProvider):
            def extract(self, bundle, *, response_schema):
                response = super().extract(
                    bundle,
                    response_schema=response_schema,
                )
                payload = {
                    **response.parsed_output,
                    "evidence": [
                        {
                            "evidence_id": "e1",
                            "page_number": 1,
                            "chunk_id": "chunk-1",
                            "start": 0,
                            "end": 2,
                            "quote": "回购金额",
                        }
                    ],
                }
                return replace(
                    response,
                    parsed_output=payload,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            base_builder = self._bundle_builder(manifest)

            def build(document_id: int) -> SemanticInputBundle:
                bundle = base_builder(document_id)
                return replace(
                    bundle,
                    payload={
                        **bundle.payload,
                        "chunks": [
                            {
                                "chunk_id": "chunk-1",
                                "page_number": 1,
                                "text": "公司拟回购金额不超过一亿元。",
                            }
                        ],
                    },
                )

            with patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                    "deepseek-v4-pro",
                },
            ):
                materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=1,
                    provider=EvidenceProvider(),
                    bundle_builder=build,
                )

            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            row = json.loads(
                output.read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(
                (
                    row["evidence"][0]["start"],
                    row["evidence"][0]["end"],
                ),
                (3, 7),
            )

    def test_materialization_runs_bounded_calls_concurrently_but_writes_manifest_order(
        self,
    ) -> None:
        class ConcurrentProvider(self.FakeProvider):
            def __init__(self) -> None:
                super().__init__()
                self.barrier = threading.Barrier(2)
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0

            def extract(self, bundle, *, response_schema):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                self.barrier.wait(timeout=1)
                if int(bundle.document_id) == 1:
                    time.sleep(0.05)
                try:
                    return super().extract(
                        bundle,
                        response_schema=response_schema,
                    )
                finally:
                    with self.lock:
                        self.active -= 1

        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            config_path = (
                root
                / "configs/research/intelligence_semantic_benchmark.yaml"
            )
            config = yaml.safe_load(
                config_path.read_text(encoding="utf-8")
            )
            config["benchmark"]["materialization_workers"] = 2
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )
            provider = ConcurrentProvider()
            with patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                    "deepseek-v4-pro",
                },
            ):
                result = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=2,
                    provider=provider,
                    bundle_builder=self._bundle_builder(manifest),
                )

            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            rows = [
                json.loads(line)
                for line in output.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(result["status"], "complete")
            self.assertEqual(provider.max_active, 2)
            self.assertEqual(
                [row["document_id"] for row in rows],
                [1, 2],
            )

    def test_materialization_rejects_provider_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            provider = self.FakeProvider()
            provider.identity = replace(
                provider.identity,
                model="different-model",
            )
            with patch.dict(
                os.environ,
                {"INTELLIGENCE_LLM_MODEL_CANDIDATE_A": "deepseek-v4-pro"},
            ):
                with self.assertRaisesRegex(
                    BenchmarkError,
                    "benchmark_candidate_identity_mismatch",
                ):
                    materialize_candidate_outputs(
                        root,
                        benchmark_name="announcement-v1",
                        provider_config="candidate-a",
                        limit=1,
                        provider=provider,
                        bundle_builder=self._bundle_builder(manifest),
                    )

    def test_materialization_budget_survives_process_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            config_path = (
                root
                / "configs/research/intelligence_semantic_benchmark.yaml"
            )
            config = yaml.safe_load(
                config_path.read_text(encoding="utf-8")
            )
            config["semantic"]["budgets"][
                "max_documents_per_daily_run"
            ] = 1
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                    "deepseek-v4-pro",
            }

            with patch.dict(os.environ, environment, clear=True):
                first = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=1,
                    provider=self.FakeProvider(),
                    bundle_builder=self._bundle_builder(manifest),
                )
                restarted_provider = self.FakeProvider()
                second = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=2,
                    provider=restarted_provider,
                    bundle_builder=self._bundle_builder(manifest),
                )

            self.assertEqual(first["processed"], 1)
            self.assertEqual(second["status"], "partial")
            self.assertEqual(second["processed"], 0)
            self.assertEqual(second["remaining"], 1)
            self.assertEqual(restarted_provider.document_ids, [])
            ledger = json.loads(
                (
                    root
                    / "data/shared/intelligence/benchmarks/"
                    "announcement-v1/daily_budget.json"
                ).read_text(encoding="utf-8")
            )
            usage = next(iter(ledger["days"].values()))
            self.assertEqual(
                next(iter(usage.values()))["documents"],
                1,
            )

    def test_payment_required_halts_without_materializing_failure(
        self,
    ) -> None:
        class PaymentRequiredProvider(self.FakeProvider):
            def extract(self, bundle, *, response_schema):
                del bundle, response_schema
                raise SemanticProviderError(
                    "semantic_provider_payment_required",
                    retryable=True,
                    status_code=402,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            with patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                    "deepseek-v4-pro",
                },
            ):
                result = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=2,
                    provider=PaymentRequiredProvider(),
                    bundle_builder=self._bundle_builder(manifest),
                )

            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["processed"], 0)
            self.assertEqual(result["remaining"], 2)
            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            self.assertTrue(output.exists())
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_account_overdue_halts_without_materializing_failure(
        self,
    ) -> None:
        class AccountOverdueProvider(self.FakeProvider):
            def extract(self, bundle, *, response_schema):
                del bundle, response_schema
                raise SemanticProviderError(
                    "semantic_provider_account_overdue",
                    retryable=True,
                    status_code=403,
                )

        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            with patch.dict(
                os.environ,
                {
                    "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                    "deepseek-v4-pro",
                },
            ):
                result = materialize_candidate_outputs(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                    limit=2,
                    provider=AccountOverdueProvider(),
                    bundle_builder=self._bundle_builder(manifest),
                )

            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["processed"], 0)
            self.assertEqual(result["remaining"], 2)
            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            self.assertTrue(output.exists())
            self.assertEqual(output.read_text(encoding="utf-8"), "")

    def test_scoring_rejects_an_incomplete_materialized_prefix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self._root(tmp)
            config = yaml.safe_load(
                (
                    root
                    / "configs/research/intelligence_semantic_benchmark.yaml"
                ).read_text(encoding="utf-8")
            )
            candidate = CandidateIdentity(
                provider_config="candidate-a",
                provider="openai-compatible",
                model="deepseek-v4-pro",
                generation_config_hash=canonical_json_hash(
                    {
                        "provider": config["semantic"][
                            "provider"
                        ],
                        "profile": config["semantic"][
                            "candidate_profiles"
                        ]["candidate-a"],
                        "schema_repair_attempts": config[
                            "semantic"
                        ]["budgets"][
                            "schema_repair_attempts"
                        ],
                        "grounding_alignment_version": config[
                            "semantic"
                        ][
                            "grounding_alignment_version"
                        ],
                    }
                ),
                prompt_version=config["semantic"][
                    "prompt_version"
                ],
                schema_version="announcement-events-v1",
                taxonomy_version="cn-announcement-taxonomy-v1",
                parser_version="announcement-layout-v1",
            )
            output = (
                root
                / "data/shared/intelligence/benchmarks/announcement-v1/"
                "candidate_outputs/candidate-a.jsonl"
            )
            output.parent.mkdir(parents=True)
            output.write_text(
                json.dumps(
                    {
                        **_prediction("1", events=[], evidence=[]),
                        "artifact_hash": manifest[0]["artifact_hash"],
                        "candidate": candidate.to_dict(),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            frozen = FrozenBenchmark(
                name="announcement-v1",
                manifest_path=output.parent.parent / "manifest.jsonl",
                gold_path=output.parent.parent / "gold.jsonl",
                manifest_hash=_sha("manifest"),
                gold_hash=_sha("gold"),
                benchmark_hash=_sha("benchmark"),
                document_count=2,
                manifest_records=tuple(manifest),
                gold_records=(),
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        "INTELLIGENCE_LLM_MODEL_CANDIDATE_A":
                        "deepseek-v4-pro"
                    },
                ),
                patch(
                    "stock_analyze.intelligence.semantic.benchmark."
                    "validate_frozen_benchmark",
                    return_value=frozen,
                ),
                self.assertRaisesRegex(
                    BenchmarkError,
                    "benchmark_candidate_output_incomplete",
                ),
            ):
                run_frozen_benchmark(
                    root,
                    benchmark_name="announcement-v1",
                    provider_config="candidate-a",
                )


class ChampionRegistryTest(FrozenBenchmarkValidationTest):
    floors = BenchmarkFloors(
        schema_validity_floor=1.0,
        event_precision_floor=0.90,
        event_recall_floor=0.85,
        evidence_grounding_floor=0.98,
        entity_accuracy_floor=0.995,
        numeric_exact_match_floor=0.98,
        no_event_false_negative_ceiling=0.10,
    )
    candidate = CandidateIdentity(
        provider_config="candidate-a",
        provider="openai-compatible",
        model="deepseek-v4-pro",
        generation_config_hash=_sha("generation-config"),
        prompt_version="announcement-event-v1",
        schema_version="announcement-events-v1",
        taxonomy_version="cn-announcement-taxonomy-v1",
        parser_version="announcement-layout-v1",
    )

    def _benchmark(self, root: Path):
        manifest_path, gold_path = self._write_frozen_files(root)
        return validate_frozen_benchmark(
            manifest_path,
            gold_path,
            event_families=self.event_families,
            policy=self.policy,
            benchmark_name="synthetic-v1",
        )

    def _passing_metrics(self):
        gold = [
            {
                "document_id": "d1",
                "events": [_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")],
                "evidence_spans": [_span("e1")],
                "no_event_reason": None,
            },
            *[
                {
                    "document_id": f"d{index}",
                    "events": [],
                    "evidence_spans": [],
                    "no_event_reason": "none",
                }
                for index in range(2, 7)
            ],
        ]
        predictions = [
            _prediction(
                "d1",
                events=[_event("buyback", entity_id="000001", numeric_value=1, evidence_id="p1")],
                evidence=[_span("p1")],
            ),
            *[
                _prediction(f"d{index}", events=[], evidence=[])
                for index in range(2, 7)
            ],
        ]
        return evaluate_predictions(gold, predictions)

    def _write_report(self, root: Path, frozen, *, run_id: str, metrics=None):
        report = create_benchmark_report(
            run_id=run_id,
            created_at="2026-07-25T01:00:00+00:00",
            frozen_benchmark=frozen,
            candidate=self.candidate,
            metrics=metrics or self._passing_metrics(),
            floors=self.floors,
            usage={"documents": 6, "latency_ms_total": 600},
        )
        write_immutable_benchmark_report(root, report)
        return report

    def test_candidate_below_any_floor_cannot_become_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = self._benchmark(root)
            failing = replace(self._passing_metrics(), event_recall=0.84)
            self._write_report(root, frozen, run_id="failed-run", metrics=failing)

            with self.assertRaises(PromotionRejected) as caught:
                promote_candidate(
                    root,
                    "failed-run",
                    event_families=self.event_families,
                    policy=self.policy,
                    now=lambda: datetime(2026, 7, 25, tzinfo=timezone.utc),
                )

            self.assertEqual(caught.exception.failed_metrics, ("event_recall",))
            self.assertFalse(
                (root / "data/shared/intelligence/semantic_registry.json").exists()
            )

    def test_passing_candidate_is_promoted_with_every_pinned_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = self._benchmark(root)
            self._write_report(root, frozen, run_id="passing-run")

            champion = promote_candidate(
                root,
                "passing-run",
                event_families=self.event_families,
                policy=self.policy,
                now=lambda: datetime(2026, 7, 25, 2, tzinfo=timezone.utc),
            )
            resolved = resolve_production_champion(root)

            self.assertEqual(champion, resolved)
            self.assertEqual(champion.provider, "openai-compatible")
            self.assertEqual(champion.model, "deepseek-v4-pro")
            self.assertEqual(champion.prompt_version, "announcement-event-v1")
            self.assertEqual(champion.schema_version, "announcement-events-v1")
            self.assertEqual(champion.taxonomy_version, "cn-announcement-taxonomy-v1")
            self.assertEqual(champion.parser_version, "announcement-layout-v1")
            self.assertEqual(champion.benchmark_name, "synthetic-v1")
            self.assertEqual(champion.benchmark_hash, frozen.benchmark_hash)
            self.assertEqual(champion.promoted_at, "2026-07-25T02:00:00+00:00")

    def test_changed_frozen_benchmark_hash_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = self._benchmark(root)
            self._write_report(root, frozen, run_id="stale-run")
            manifest_path = root / "data/shared/intelligence/benchmarks/synthetic-v1/manifest.jsonl"
            manifest_path.write_text(
                manifest_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BenchmarkError, "benchmark_hash_not_frozen"):
                promote_candidate(
                    root,
                    "stale-run",
                    event_families=self.event_families,
                    policy=self.policy,
                )

    def test_report_is_immutable_and_production_never_resolves_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = self._benchmark(root)
            report = self._write_report(root, frozen, run_id="immutable-run")
            with self.assertRaisesRegex(BenchmarkError, "benchmark_report_exists"):
                write_immutable_benchmark_report(root, report)
            with self.assertRaisesRegex(BenchmarkError, "semantic_champion_missing"):
                resolve_production_champion(root)


if __name__ == "__main__":
    unittest.main()
