from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.benchmark import (
    AnchorGoldEvaluation,
    BenchmarkError,
    canonical_json_hash,
    evaluate_anchor_gold,
    finalize_anchor_gold,
    generate_anchor_disagreements,
    import_anchor_annotations,
    run_anchor_gold_evaluation,
    _constrained_match_grade,
    _event_effective_dates,
    _match_events_constrained,
    _wilson_interval,
)
from stock_analyze.intelligence.store import IntelligenceStore


# ---------------------------------------------------------------------------
# Record helpers (mirror the shapes used by the production candidate flow).
# ---------------------------------------------------------------------------


def _event(
    event_type: str,
    *,
    entity_id: str,
    numeric_value: int,
    evidence_id: str,
    lifecycle: str = "completed",
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "lifecycle": lifecycle,
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


def _evidence(
    evidence_id: str,
    *,
    document_id: int,
    page_number: int = 1,
    quote: str = "回购金额",
) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "page_number": page_number,
        "chunk_id": _chunk_id(document_id),
        "start": 0,
        "end": 4,
        "quote": quote,
    }


def _chunk_id(document_id: int) -> str:
    # document_chunks.chunk_id is a global PRIMARY KEY, so it must be unique
    # across documents even though lookups are keyed by (document_id, chunk_id).
    return f"chunk-{document_id}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prediction(
    document_id: int,
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
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def _gold_record(
    document_id: int,
    *,
    events: list[dict[str, object]],
) -> dict[str, object]:
    return {"document_id": document_id, "events": events}


# ---------------------------------------------------------------------------
# Fixture: a temp repo root with a migrated intelligence store + chunks.
# ---------------------------------------------------------------------------


def _build_repo(tmp: str | Path) -> Path:
    root = Path(tmp)
    # Construction runs the migration, creating intelligence.sqlite3 with the
    # full schema (including document_chunks).
    IntelligenceStore(root / "data" / "shared" / "intelligence")
    return root


def _insert_chunk(
    store: IntelligenceStore,
    *,
    document_id: int,
    page_number: int,
    text: str,
) -> None:
    """Insert a document_chunks row with FK enforcement disabled.

    evaluate_anchor_gold only ever SELECTs from document_chunks, so the
    missing documents/document_artifacts parents are irrelevant; disabling
    FKs for the fixture insert avoids having to materialise the full
    ingestion pipeline.
    """
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """
            INSERT INTO document_chunks(
                chunk_id, document_id, artifact_id, sequence_no, page_number,
                section, bbox_json, text, text_hash, ocr_used, ocr_confidence,
                parser_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _chunk_id(document_id),
                document_id,
                f"artifact-{document_id}",
                0,
                page_number,
                "",
                "[]",
                text,
                "hash",
                0,
                None,
                "test-1.0",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _store(root: Path) -> IntelligenceStore:
    return IntelligenceStore(root / "data" / "shared" / "intelligence")


# ===========================================================================
# 1. Pure helper tests (no DB, no files).
# ===========================================================================


class ConstrainedMatchGradeTest(unittest.TestCase):
    def test_full_match_when_all_constraints_agree(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        pred = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "full")
        self.assertEqual(failed, ())

    def test_none_when_event_type_differs(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        pred = _event("dividend", entity_id="000001", numeric_value=5, evidence_id="e1")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "none")
        self.assertEqual(failed, ())

    def test_partial_when_lifecycle_differs(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1", lifecycle="completed")
        pred = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1", lifecycle="revised")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "partial")
        self.assertIn("lifecycle", failed)

    def test_partial_when_subjects_differ(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        pred = _event("buyback", entity_id="000002", numeric_value=5, evidence_id="e1")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "partial")
        self.assertIn("subjects", failed)

    def test_partial_when_only_one_of_multiple_subjects_matches(self) -> None:
        gold = _event(
            "shareholder_change",
            entity_id="000001",
            numeric_value=5,
            evidence_id="e1",
        )
        gold["subjects"].append(
            {
                "entity_id": "holder-a",
                "role": "holder",
                "evidence_ids": ["e1"],
            }
        )
        pred = _event(
            "shareholder_change",
            entity_id="000001",
            numeric_value=5,
            evidence_id="e1",
        )
        pred["subjects"].append(
            {
                "entity_id": "holder-b",
                "role": "holder",
                "evidence_ids": ["e1"],
            }
        )
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "partial")
        self.assertIn("subjects", failed)

    def test_partial_when_numeric_facts_differ(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        pred = _event("buyback", entity_id="000001", numeric_value=9, evidence_id="e1")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "partial")
        self.assertIn("facts", failed)

    def test_partial_when_effective_dates_differ(self) -> None:
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        gold["effective_dates"] = [{"kind": "announcement", "value": "2026-01-01"}]
        pred = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        pred["effective_dates"] = [{"kind": "announcement", "value": "2026-02-02"}]
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "partial")
        self.assertIn("time", failed)

    def test_gold_without_subjects_does_not_fail_subjects(self) -> None:
        # Asymmetric: gold has no subjects -> subjects constraint is vacuous.
        gold = _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")
        gold["subjects"] = []
        pred = _event("buyback", entity_id="000002", numeric_value=5, evidence_id="e1")
        grade, failed = _constrained_match_grade(gold, pred)
        self.assertEqual(grade, "full")
        self.assertNotIn("subjects", failed)


class EventEffectiveDatesTest(unittest.TestCase):
    def test_dedupes_equivalent_date_pairs(self) -> None:
        event = {
            "effective_dates": [
                {"kind": "announcement", "value": "2026-01-01"},
                {"kind": "announcement", "value": "2026-01-01"},
                {"kind": "effective", "value": "2026-02-02"},
            ]
        }
        result = _event_effective_dates(event)
        self.assertEqual(
            result,
            frozenset(
                {
                    ("announcement", "2026-01-01"),
                    ("effective", "2026-02-02"),
                }
            ),
        )

    def test_rejects_non_list_dates(self) -> None:
        with self.assertRaisesRegex(BenchmarkError, "benchmark_event_dates_invalid"):
            _event_effective_dates({"effective_dates": "not-a-list"})


class MatchEventsConstrainedTest(unittest.TestCase):
    def test_greedy_full_then_partial_no_double_use(self) -> None:
        gold = [
            _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1"),
            _event("buyback", entity_id="000002", numeric_value=6, evidence_id="e2"),
        ]
        pred = [
            _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1"),  # full
            _event("buyback", entity_id="000002", numeric_value=9, evidence_id="e2"),  # partial (facts)
        ]
        full, partial, ug, up = _match_events_constrained(gold, pred)
        self.assertEqual(len(full), 1)
        self.assertEqual(len(partial), 1)
        self.assertEqual(len(ug), 0)
        self.assertEqual(len(up), 0)
        # The full match consumed gold[0]/pred[0]; partial consumed gold[1]/pred[1].
        self.assertEqual(full[0][0], gold[0])
        self.assertEqual(partial[0][0], gold[1])

    def test_unmatched_gold_and_predicted_tracked(self) -> None:
        gold = [_event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")]
        pred = [_event("dividend", entity_id="000001", numeric_value=5, evidence_id="e1")]
        full, partial, ug, up = _match_events_constrained(gold, pred)
        self.assertEqual(len(full), 0)
        self.assertEqual(len(partial), 0)
        self.assertEqual(len(ug), 1)
        self.assertEqual(len(up), 1)

    def test_full_match_preferred_over_partial_for_same_gold(self) -> None:
        # gold[0] could partial-match pred[0] (different value) or full-match pred[1].
        gold = [_event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1")]
        pred = [
            _event("buyback", entity_id="000001", numeric_value=9, evidence_id="e1"),  # partial
            _event("buyback", entity_id="000001", numeric_value=5, evidence_id="e1"),  # full
        ]
        full, partial, ug, up = _match_events_constrained(gold, pred)
        self.assertEqual(len(full), 1)
        self.assertEqual(len(partial), 0)
        self.assertEqual(len(up), 1)  # the partial-only pred is leftover


class WilsonIntervalTest(unittest.TestCase):
    def test_zero_total_returns_zeros(self) -> None:
        m = _wilson_interval(0, 0)
        self.assertEqual(m.passes, 0)
        self.assertEqual(m.total, 0)
        self.assertEqual(m.rate, 0.0)
        self.assertEqual(m.wilson_lower, 0.0)
        self.assertEqual(m.wilson_upper, 0.0)

    def test_all_pass_rate_one_with_ci_in_unit_interval(self) -> None:
        m = _wilson_interval(10, 10)
        self.assertEqual(m.rate, 1.0)
        self.assertLessEqual(m.wilson_lower, m.rate)
        self.assertLessEqual(m.rate, m.wilson_upper)
        self.assertGreaterEqual(m.wilson_lower, 0.0)
        self.assertLessEqual(m.wilson_upper, 1.0)
        # 10/10 is not a degenerate CI: lower bound < 1.
        self.assertLess(m.wilson_lower, 1.0)

    def test_half_pass_centered_near_half(self) -> None:
        m = _wilson_interval(5, 10)
        self.assertAlmostEqual(m.rate, 0.5)
        self.assertLess(m.wilson_lower, 0.5)
        self.assertGreater(m.wilson_upper, 0.5)

    def test_bounds_always_in_unit_interval(self) -> None:
        for passes in range(0, 21):
            m = _wilson_interval(passes, 20)
            self.assertGreaterEqual(m.wilson_lower, 0.0)
            self.assertLessEqual(m.wilson_upper, 1.0)
            self.assertLessEqual(m.wilson_lower, m.wilson_upper)


# ===========================================================================
# 2. evaluate_anchor_gold integrated test (temp store + document_chunks).
# ===========================================================================


class EvaluateAnchorGoldTest(unittest.TestCase):
    @staticmethod
    def _three_doc_fixture(root: Path) -> tuple[list, list, dict]:
        """Full / partial / none-match scenario across three documents."""
        store = _store(root)
        for did in (1001, 1002, 1003):
            _insert_chunk(
                store,
                document_id=did,
                page_number=1,
                text="公司拟回购金额以实施员工持股计划",
            )
        gold_records = [
            _gold_record(1001, events=[_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")]),
            _gold_record(1002, events=[_event("buyback", entity_id="000002", numeric_value=1, evidence_id="e2")]),
            _gold_record(1003, events=[_event("buyback", entity_id="000003", numeric_value=1, evidence_id="e3")]),
        ]
        prediction_records = [
            _prediction(
                1001,
                events=[_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")],
                evidence=[_evidence("e1", document_id=1001)],  # full match
            ),
            _prediction(
                1002,
                events=[_event("buyback", entity_id="000002", numeric_value=2, evidence_id="e2")],
                evidence=[_evidence("e2", document_id=1002)],  # partial: facts differ
            ),
            _prediction(
                1003,
                events=[_event("dividend", entity_id="000003", numeric_value=1, evidence_id="e3")],
                evidence=[_evidence("e3", document_id=1003)],  # none: type differs
            ),
        ]
        document_audit = {
            1001: {"event_family": "buyback", "is_legal_opinion": True},
            1002: {"event_family": "buyback", "ocr_required": True},
            1003: {"event_family": "buyback"},
        }
        return gold_records, prediction_records, document_audit

    def test_constrained_counts_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            gold_records, prediction_records, audit = self._three_doc_fixture(root)
            evaluation = evaluate_anchor_gold(
                root, gold_records, prediction_records, document_audit=audit
            )
            self.assertIsInstance(evaluation, AnchorGoldEvaluation)
            self.assertEqual(evaluation.document_count, 3)

            # Constrained: 1 full (doc1001), 1 partial (doc1002), 1 none
            # (doc1003). A partial is both a false positive and false negative.
            self.assertEqual(evaluation.constrained_tp, 1)
            self.assertEqual(evaluation.constrained_fp, 2)
            self.assertEqual(evaluation.constrained_fn, 2)
            self.assertEqual(evaluation.partial_matches, 1)
            self.assertAlmostEqual(evaluation.constrained_precision, 1 / 3)
            self.assertAlmostEqual(evaluation.constrained_recall, 1 / 3)
            self.assertAlmostEqual(evaluation.constrained_f1, 1 / 3)

            metrics = {m.name: m for m in evaluation.overall}
            # quote_in_text: all 3 evidence grounded in their chunks.
            self.assertEqual(metrics["quote_in_text"].passes, 3)
            self.assertEqual(metrics["quote_in_text"].total, 3)
            self.assertAlmostEqual(metrics["quote_in_text"].rate, 1.0)
            # event_identity: 2 of 3 gold events have a matching type+lifecycle.
            self.assertEqual(metrics["event_identity"].passes, 2)
            self.assertEqual(metrics["event_identity"].total, 3)
            # entity_temporal_numeric: only the full-match doc's gold fact matches.
            self.assertEqual(metrics["entity_temporal_numeric"].passes, 1)
            self.assertEqual(metrics["entity_temporal_numeric"].total, 3)
            # no_event_false_negative: no no-event gold docs.
            self.assertEqual(metrics["no_event_false_negative"].total, 0)

            # Wilson bounds respect the unit interval.
            for metric in evaluation.overall:
                self.assertGreaterEqual(metric.wilson_lower, 0.0)
                self.assertLessEqual(metric.wilson_upper, 1.0)
                self.assertLessEqual(metric.wilson_lower, metric.rate)

    def test_family_and_hard_case_breakdowns_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            gold_records, prediction_records, audit = self._three_doc_fixture(root)
            evaluation = evaluate_anchor_gold(
                root, gold_records, prediction_records, document_audit=audit
            )
            families = {
                family.subset: family
                for family in evaluation.by_family
            }
            self.assertEqual(set(families), {"buyback", "dividend"})
            family = families["buyback"]
            self.assertEqual(family.document_count, 3)
            self.assertEqual(family.event_tp, 1)
            self.assertEqual(family.event_fp, 1)
            self.assertEqual(family.event_fn, 2)
            self.assertEqual(family.partial_matches, 1)
            self.assertEqual(families["dividend"].document_count, 1)
            self.assertEqual(families["dividend"].event_fp, 1)

            # Hard-case subsets: legal_opinion (doc1001) and ocr (doc1002).
            hard = {h.subset: h for h in evaluation.hard_cases}
            self.assertIn("legal_opinion", hard)
            self.assertIn("ocr", hard)
            self.assertEqual(hard["legal_opinion"].document_count, 1)
            self.assertEqual(hard["ocr"].document_count, 1)

    def test_partial_match_counts_as_false_positive_and_false_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            _insert_chunk(
                _store(root),
                document_id=1001,
                page_number=1,
                text="公司拟回购金额以实施员工持股计划",
            )
            evaluation = evaluate_anchor_gold(
                root,
                [
                    _gold_record(
                        1001,
                        events=[
                            _event(
                                "buyback",
                                entity_id="000001",
                                numeric_value=1,
                                evidence_id="gold-evidence",
                            )
                        ],
                    )
                ],
                [
                    _prediction(
                        1001,
                        events=[
                            _event(
                                "buyback",
                                entity_id="000001",
                                numeric_value=2,
                                evidence_id="prediction-evidence",
                            )
                        ],
                        evidence=[
                            _evidence(
                                "prediction-evidence",
                                document_id=1001,
                            )
                        ],
                    )
                ],
            )
            self.assertEqual(evaluation.constrained_tp, 0)
            self.assertEqual(evaluation.constrained_fp, 1)
            self.assertEqual(evaluation.constrained_fn, 1)
            self.assertEqual(evaluation.partial_matches, 1)

    def test_family_breakdown_uses_event_types_not_weak_strata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            _insert_chunk(
                _store(root),
                document_id=1001,
                page_number=1,
                text="公司拟回购金额以实施员工持股计划",
            )
            evaluation = evaluate_anchor_gold(
                root,
                [
                    _gold_record(
                        1001,
                        events=[
                            _event(
                                "buyback",
                                entity_id="000001",
                                numeric_value=1,
                                evidence_id="gold-evidence",
                            )
                        ],
                    )
                ],
                [
                    _prediction(
                        1001,
                        events=[
                            _event(
                                "dividend",
                                entity_id="000001",
                                numeric_value=1,
                                evidence_id="prediction-evidence",
                            )
                        ],
                        evidence=[
                            _evidence(
                                "prediction-evidence",
                                document_id=1001,
                            )
                        ],
                    )
                ],
                document_audit={
                    1001: {"event_family": "earnings_forecast"}
                },
            )
            families = {
                item.subset: item for item in evaluation.by_family
            }
            self.assertEqual(set(families), {"buyback", "dividend"})
            self.assertEqual(families["buyback"].event_fn, 1)
            self.assertEqual(families["dividend"].event_fp, 1)

    def test_quote_support_does_not_depend_on_evidence_id_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            _insert_chunk(
                _store(root),
                document_id=1001,
                page_number=1,
                text="公司拟回购金额以实施员工持股计划",
            )
            gold_records = [
                _gold_record(
                    1001,
                    events=[
                        _event(
                            "buyback",
                            entity_id="000001",
                            numeric_value=1,
                            evidence_id="gold-evidence",
                        )
                    ],
                )
            ]
            prediction_records = [
                _prediction(
                    1001,
                    events=[
                        _event(
                            "buyback",
                            entity_id="000001",
                            numeric_value=1,
                            evidence_id="prediction-evidence",
                        )
                    ],
                    evidence=[
                        _evidence(
                            "prediction-evidence",
                            document_id=1001,
                        )
                    ],
                )
            ]
            evaluation = evaluate_anchor_gold(
                root,
                gold_records,
                prediction_records,
            )
            metric = {
                item.name: item for item in evaluation.overall
            }["quote_supports_fact"]
            self.assertEqual(metric.passes, 1)
            self.assertEqual(metric.total, 1)

    def test_failure_samples_emitted_on_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            gold_records, prediction_records, audit = self._three_doc_fixture(root)
            evaluation = evaluate_anchor_gold(
                root, gold_records, prediction_records, document_audit=audit
            )
            # event_identity fails for doc1003; entity_temporal_numeric fails
            # for doc1002 and doc1003.
            metrics_in_samples = {s["metric"] for s in evaluation.failure_samples}
            self.assertIn("event_identity", metrics_in_samples)
            self.assertIn("entity_temporal_numeric", metrics_in_samples)

    def test_unknown_prediction_document_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            gold_records = [_gold_record(1001, events=[])]
            prediction_records = [
                _prediction(9999, events=[], evidence=[])  # not in gold
            ]
            with self.assertRaisesRegex(
                BenchmarkError, "benchmark_prediction_document_unknown"
            ):
                evaluate_anchor_gold(root, gold_records, prediction_records)

    def test_no_event_gold_with_matching_prediction_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            _insert_chunk(
                _store(root), document_id=2001,
                page_number=1, text="无实质性事件",
            )
            gold_records = [_gold_record(2001, events=[])]
            prediction_records = [_prediction(2001, events=[], evidence=[])]
            evaluation = evaluate_anchor_gold(root, gold_records, prediction_records)
            metrics = {m.name: m for m in evaluation.overall}
            self.assertEqual(metrics["no_event_false_negative"].passes, 1)
            self.assertEqual(metrics["no_event_false_negative"].total, 1)
            self.assertEqual(evaluation.constrained_tp, 0)
            self.assertEqual(evaluation.constrained_fp, 0)
            self.assertEqual(evaluation.constrained_fn, 0)


# ===========================================================================
# 3. Annotation workflow round-trip (disagreements / finalize / evaluate).
# ===========================================================================


class AnchorAnnotationWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _build_repo(self._tmp.name)
        store = _store(self.root)
        for did in (3001, 3002):
            _insert_chunk(
                store, document_id=did,
                page_number=1, text="公司拟回购金额以实施员工持股计划",
            )
        self.benchmark_dir = (
            self.root / "data" / "shared" / "intelligence" / "benchmarks" / "anchor-v1"
        )
        self.benchmark_dir.mkdir(parents=True)
        self._write_manifest(self.benchmark_dir, [3001, 3002])
        self._write_annotators()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _annotator_record(
        document_id: int,
        *,
        numeric_value: int = 1,
        evidence_id: str = "e1",
    ) -> dict[str, object]:
        event = _event(
            "buyback", entity_id="000001",
            numeric_value=numeric_value, evidence_id=evidence_id,
        )
        return {
            "document_id": document_id,
            "artifact_hash": f"artifact-{document_id}",
            "annotator": "annotator-x",
            "adjudicated_at": "2026-07-26T00:00:00+00:00",
            "events": [event],
            "evidence_spans": [_evidence(evidence_id, document_id=document_id)],
            "evidence": [_evidence(evidence_id, document_id=document_id)],
            "no_event_reason": None,
            "annotation_basis": "manual review of source PDF",
            "annotation_hash": "",
        }

    @staticmethod
    def _write_manifest(benchmark_dir: Path, doc_ids: list[int]) -> None:
        rows = [
            {
                "document_id": did,
                "document_hash": _sha256(f"doc-{did}"),
                "artifact_hash": _sha256(f"artifact-{did}"),
                "event_family": "buyback",
            }
            for did in doc_ids
        ]
        (benchmark_dir / "manifest.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
            encoding="utf-8",
        )

    @staticmethod
    def _recompute_hash(record: dict[str, object]) -> None:
        record["annotation_hash"] = canonical_json_hash(
            {k: v for k, v in record.items() if k != "annotation_hash"}
        )

    def _write_annotators(self) -> None:
        # Consensus on 3001; disagreement on 3002 (annotator-a amount=1, b amount=2).
        a_3001 = self._annotator_record(3001, numeric_value=1)
        a_3002 = self._annotator_record(3002, numeric_value=1)
        b_3001 = self._annotator_record(3001, numeric_value=1)
        b_3002 = self._annotator_record(3002, numeric_value=2)
        for rec in (a_3001, a_3002, b_3001, b_3002):
            self._recompute_hash(rec)
        (self.benchmark_dir / "anchor_annotator_a.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in (a_3001, a_3002)),
            encoding="utf-8",
        )
        (self.benchmark_dir / "anchor_annotator_b.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in (b_3001, b_3002)),
            encoding="utf-8",
        )

    def _limit_anchor_scope_to_existing_annotators(self) -> None:
        self._write_manifest(self.benchmark_dir, [3001, 3002, 3003])
        (self.benchmark_dir / "anchor_sample.jsonl").write_text(
            "".join(
                json.dumps({"document_id": did}) + "\n"
                for did in (3001, 3002)
            ),
            encoding="utf-8",
        )

    def _write_adjudication(self, *, choice: str = "annotator-a", reason: str = "stable") -> Path:
        ad_path = self.benchmark_dir / "adjudications.jsonl"
        ad_path.write_text(
            json.dumps(
                {
                    "document_id": 3002,
                    "choice": choice,
                    "reviewer": "operator-1",
                    "adjudication_reason": reason,
                }
            )
                + "\n",
            encoding="utf-8",
        )
        return ad_path

    def test_generate_disagreements_detects_partial_mismatch(self) -> None:
        result = generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["common_documents"], 2)
        self.assertEqual(result["disagreements"], 1)
        queue_path = self.root / result["queue_path"]
        rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["document_id"], 3002)
        self.assertIn("partial_matches", "".join(rows[0]["reasons"]))

    def test_finalize_freezes_consensus_and_adjudication(self) -> None:
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        ad_path = self._write_adjudication(reason="annotator-a amount matches source")
        result = finalize_anchor_gold(
            self.root, benchmark="anchor-v1", adjudications_path=ad_path
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["documents"], 2)
        self.assertEqual(result["consensus"], 1)
        self.assertEqual(result["adjudicated"], 1)
        gold_path = self.benchmark_dir / "anchor_gold.jsonl"
        self.assertTrue(gold_path.exists())
        rows = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines()]
        consensus = rows[0]
        adjudicated = rows[1]
        self.assertEqual(consensus["annotator"], "annotator-a+annotator-b/consensus")
        self.assertIn("selected-annotator-a", adjudicated["annotator"])
        self.assertEqual(adjudicated["adjudication_reason"], "annotator-a amount matches source")

    def test_finalize_uses_anchor_sample_instead_of_full_manifest(self) -> None:
        self._limit_anchor_scope_to_existing_annotators()
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        result = finalize_anchor_gold(
            self.root,
            benchmark="anchor-v1",
            adjudications_path=self._write_adjudication(),
        )
        self.assertEqual(result["documents"], 2)
        gold_path = self.benchmark_dir / "anchor_gold.jsonl"
        rows = [
            json.loads(line)
            for line in gold_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [row["document_id"] for row in rows],
            [3001, 3002],
        )

    def test_finalize_is_immutable_on_identical_rerun(self) -> None:
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        ad_path = self._write_adjudication()
        first = finalize_anchor_gold(
            self.root, benchmark="anchor-v1", adjudications_path=ad_path
        )
        # Re-running with the same adjudication must succeed (idempotent).
        second = finalize_anchor_gold(
            self.root, benchmark="anchor-v1", adjudications_path=ad_path
        )
        self.assertEqual(first["anchor_gold_hash"], second["anchor_gold_hash"])

    def test_finalize_rejects_adjudication_set_mismatch(self) -> None:
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        # Empty adjudications -> missing the disputed 3002 -> set mismatch.
        ad_path = self.benchmark_dir / "adjudications.jsonl"
        ad_path.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(
            BenchmarkError, "anchor_adjudication_document_set_mismatch"
        ):
            finalize_anchor_gold(
                self.root, benchmark="anchor-v1", adjudications_path=ad_path
            )

    def test_run_anchor_gold_evaluation_writes_report(self) -> None:
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        ad_path = self._write_adjudication(reason="annotator-a amount matches source")
        finalize_anchor_gold(
            self.root, benchmark="anchor-v1", adjudications_path=ad_path
        )
        # Candidate output for both docs (full match on both).
        candidate_dir = self.benchmark_dir / "candidate_outputs"
        candidate_dir.mkdir(parents=True)
        candidate_dir.joinpath("deepseek-prod.jsonl").write_text(
            "".join(
                json.dumps(r, sort_keys=True) + "\n"
                for r in (
                    _prediction(
                        3001,
                        events=[_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")],
                        evidence=[_evidence("e1", document_id=3001)],
                    ),
                    _prediction(
                        3002,
                        events=[_event("buyback", entity_id="000001", numeric_value=1, evidence_id="e1")],
                        evidence=[_evidence("e1", document_id=3002)],
                    ),
                )
            ),
            encoding="utf-8",
        )
        result = run_anchor_gold_evaluation(
            self.root, benchmark="anchor-v1", provider_config="deepseek-prod"
        )
        report = Path(result["report_path"])
        if not report.is_absolute():
            report = self.root / report
        self.assertTrue(report.exists())
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["benchmark"], "anchor-v1")
        self.assertEqual(payload["provider_config"], "deepseek-prod")
        self.assertEqual(payload["document_count"], 2)
        self.assertIn("constrained", payload)
        self.assertIn("by_family", payload)

    def test_run_evaluation_filters_full_candidate_to_anchor_scope(self) -> None:
        self._limit_anchor_scope_to_existing_annotators()
        generate_anchor_disagreements(self.root, benchmark="anchor-v1")
        finalize_anchor_gold(
            self.root,
            benchmark="anchor-v1",
            adjudications_path=self._write_adjudication(),
        )
        candidate_dir = self.benchmark_dir / "candidate_outputs"
        candidate_dir.mkdir(parents=True)
        candidate_dir.joinpath("deepseek-prod.jsonl").write_text(
            "".join(
                json.dumps(r, sort_keys=True) + "\n"
                for r in (
                    _prediction(
                        did,
                        events=[
                            _event(
                                "buyback",
                                entity_id="000001",
                                numeric_value=1,
                                evidence_id="e1",
                            )
                        ],
                        evidence=[_evidence("e1", document_id=did)],
                    )
                    for did in (3001, 3002, 3003)
                )
            ),
            encoding="utf-8",
        )
        result = run_anchor_gold_evaluation(
            self.root,
            benchmark="anchor-v1",
            provider_config="deepseek-prod",
        )
        self.assertEqual(result["document_count"], 2)


# ===========================================================================
# 4. import_anchor_annotations schema-validation path (proven-valid event).
# ===========================================================================


class ImportAnchorAnnotationsTest(unittest.TestCase):
    def test_import_normalizes_two_annotator_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _build_repo(tmp)
            _insert_chunk(
                _store(root), document_id=4001,
                page_number=1, text="业绩预告修正",
            )
            (root / "configs").mkdir()
            shutil.copy(
                Path("configs/intelligence_event_taxonomy_v1.json"),
                root / "configs" / "intelligence_event_taxonomy_v1.json",
            )
            benchmark_dir = (
                root / "data" / "shared" / "intelligence" / "benchmarks" / "anchor-v1"
            )
            benchmark_dir.mkdir(parents=True)
            self._write_manifest(benchmark_dir, [4001])

            # Proven-valid earnings_forecast event (matches the existing
            # adjudication test's accepted payload shape).
            evidence = {
                "evidence_id": "manual-1",
                "page_number": 1,
                "chunk_id": _chunk_id(4001),
                "start": 0,
                "end": 6,
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
            annotator_row = {
                "document_id": 4001,
                "artifact_hash": "artifact-4001",
                "annotator": "reviewer-a",
                "adjudicated_at": "2026-07-26T00:00:00+00:00",
                "events": [event],
                "evidence": [evidence],
                "no_event_reason": None,
                "annotation_basis": "manual review",
            }
            a_path = root / "annotator_a.jsonl"
            b_path = root / "annotator_b.jsonl"
            a_path.write_text(json.dumps(annotator_row) + "\n", encoding="utf-8")
            b_path.write_text(json.dumps(annotator_row) + "\n", encoding="utf-8")

            result = import_anchor_annotations(
                root,
                benchmark="anchor-v1",
                annotator_a_path=a_path,
                annotator_b_path=b_path,
                annotator_a_label="reviewer-a",
                annotator_b_label="reviewer-b",
            )
            self.assertEqual(result["annotator_a"]["documents"], 1)
            self.assertEqual(result["annotator_b"]["documents"], 1)
            out_a = benchmark_dir / "anchor_annotator_a.jsonl"
            self.assertTrue(out_a.exists())
            rows = [json.loads(line) for line in out_a.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["document_id"], 4001)
            self.assertIn("evidence_spans", rows[0])
            self.assertTrue(rows[0]["annotation_hash"])

    @staticmethod
    def _write_manifest(benchmark_dir: Path, doc_ids: list[int]) -> None:
        rows = [
            {
                "document_id": did,
                "document_hash": _sha256(f"doc-{did}"),
                "artifact_hash": _sha256(f"artifact-{did}"),
                "event_family": "earnings_forecast",
            }
            for did in doc_ids
        ]
        (benchmark_dir / "manifest.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
