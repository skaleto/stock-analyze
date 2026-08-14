from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.benchmark_runner import (
    build_frozen_benchmark_input,
    collect_frozen_coding_plan_job,
    collect_frozen_coding_plan_repair_job,
    prepare_frozen_coding_plan_job,
    prepare_frozen_coding_plan_repair_job,
    run_frozen_benchmark,
)
from stock_analyze.intelligence.semantic.provider import (
    SemanticProviderIdentity,
    SemanticProviderResponse,
)


ROOT = Path(__file__).resolve().parents[1]


class NoEventProvider:
    identity = SemanticProviderIdentity(
        provider="fixture",
        model="no-event-v1",
        endpoint_host="local",
    )

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def extract(self, bundle, *, response_schema):
        del response_schema
        self.payloads.append(dict(bundle.payload))
        parsed = {
            "document_id": int(bundle.document_id),
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [],
            "no_event_reason": "fixture found no current event",
        }
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=parsed,
            raw_output=json.dumps(parsed, ensure_ascii=False),
            input_hash="input",
            output_hash="output",
            request_id="fixture-1",
            response_model=self.identity.model,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=5,
        )


class PledgeCoreProvider(NoEventProvider):
    def extract(self, bundle, *, response_schema):
        del response_schema
        self.payloads.append(dict(bundle.payload))
        parsed = {
            "document_id": int(bundle.document_id),
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [{
                "mention_id": "pledge-1",
                "event_type": "pledge_freeze",
                "subjects": [
                    {
                        "role": "issuer",
                        "name": "英派斯",
                        "evidence": [{"chunk_id": "doc829-meta-issuer", "quote": "英派斯"}],
                    },
                    {
                        "role": "holder",
                        "name": "海南江恒",
                        "evidence": [{
                            "chunk_id": "doc829-p2-c112-a8b499349672",
                            "quote": "公司控股股东海南江恒",
                        }],
                    },
                ],
                "facts": [
                    {
                        "name": "action",
                        "raw_value": "补充质押",
                        "evidence": [{
                            "chunk_id": "doc829-p1-c10-d31eb30ebe27",
                            "quote": "补充质押",
                        }],
                    },
                    {
                        "name": "purpose",
                        "raw_value": "补充质押",
                        "evidence": [{
                            "chunk_id": "doc829-p1-t0-59e17439440db257-r1-c10",
                            "quote": "补充\n质押",
                        }],
                    },
                ],
                "dates": [],
                "status": None,
            }],
            "no_event_reason": None,
        }
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=parsed,
            raw_output=json.dumps(parsed, ensure_ascii=False),
            input_hash="input",
            output_hash="output",
            request_id="fixture-pledge",
            response_model=self.identity.model,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=5,
        )


class RepairingPledgeProvider(PledgeCoreProvider):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def extract(self, bundle, *, response_schema):
        self.attempts += 1
        if self.attempts == 1:
            return NoEventProvider.extract(
                self,
                bundle,
                response_schema=response_schema,
            )
        self.assert_repair_context = dict(bundle.payload.get("repair_context") or {})
        return super().extract(bundle, response_schema=response_schema)


class MultiFamilyRepairProvider(NoEventProvider):
    def __init__(self) -> None:
        super().__init__()
        self.repair_error = ""

    def extract(self, bundle, *, response_schema):
        del response_schema
        self.payloads.append(dict(bundle.payload))
        repair_context = bundle.payload.get("repair_context") or {}
        self.repair_error = str(
            (repair_context.get("validation_error") or {}).get("code") or ""
        )
        issuer = {
            "role": "issuer",
            "name": "神州高铁",
            "evidence": [{
                "chunk_id": "doc114674-meta-issuer",
                "quote": "神州高铁",
            }],
        }
        if repair_context:
            mentions = [{
                "mention_id": "m1",
                "event_type": "earnings_forecast",
                "subjects": [issuer],
                "facts": [{
                    "name": "period",
                    "raw_value": "2002 年度",
                    "evidence": [{
                        "chunk_id": "doc114674-p1-c19-2aeeb6d4c788",
                        "quote": "预计2002 年度全年度仍将亏损",
                    }],
                }, {
                    "name": "forecast_reason",
                    "raw_value": "根据目前的生产经营情况",
                    "evidence": [{
                        "chunk_id": "doc114674-p1-c19-2aeeb6d4c788",
                        "quote": "根据目前的生产经营情况",
                    }],
                }],
                "dates": [],
                "status": None,
            }]
        else:
            mentions = [{
                "mention_id": "m1",
                "event_type": "litigation_arbitration",
                "subjects": [issuer],
                "facts": [{
                    "name": "case_stage",
                    "raw_value": "判决",
                    "evidence": [{
                        "chunk_id": "doc114674-p2-c30-74063de2017b",
                        "quote": "重大诉讼判决公告",
                    }],
                }, {
                    "name": "case_amount",
                    "raw_value": "2300 万元",
                    "evidence": [{
                        "chunk_id": "doc114674-p2-c37-26cca703ee71",
                        "quote": "金额为2300 万元抵押贷款合同逾期诉讼事项",
                    }],
                }],
                "dates": [],
                "status": {
                    "raw_value": "判决",
                    "evidence": [{
                        "chunk_id": "doc114674-p2-c30-74063de2017b",
                        "quote": "重大诉讼判决公告",
                    }],
                },
            }]
        parsed = {
            "document_id": int(bundle.document_id),
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": mentions,
            "no_event_reason": None,
        }
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=parsed,
            raw_output=json.dumps(parsed, ensure_ascii=False),
            input_hash="input",
            output_hash=f"output-{len(self.payloads)}",
            request_id=f"fixture-{len(self.payloads)}",
            response_model=self.identity.model,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=5,
        )


class CorrectedEarningsProvider(NoEventProvider):
    def extract(self, bundle, *, response_schema):
        del response_schema
        self.payloads.append(dict(bundle.payload))
        parsed = {
            "document_id": int(bundle.document_id),
            "schema_version": "announcement-mentions-v1-lite",
            "mentions": [{
                "mention_id": "earnings-flash-current",
                "event_type": "earnings_flash",
                "subjects": [{
                    "role": "issuer",
                    "name": "福能股份",
                    "evidence": [{
                        "chunk_id": "doc328952-meta-issuer",
                        "quote": "福能股份",
                    }],
                }],
                "facts": [{
                    "name": "period",
                    "raw_value": "2007",
                    "evidence": [{
                        "chunk_id": "doc328952-p1-c2-90c99c880fa5",
                        "quote": "2007年度业绩快报更正公告",
                    }],
                }, {
                    "name": "revenue",
                    "raw_value": "105,412.51",
                    "evidence": [{
                        "chunk_id": "doc328952-p1-t0-25780c6b0246a3aa-r3-c1",
                        "quote": "105,412.51",
                    }],
                }, {
                    "name": "net_profit",
                    "raw_value": "-953.39",
                    "evidence": [{
                        "chunk_id": "doc328952-p1-t0-25780c6b0246a3aa-r6-c1",
                        "quote": "-953.39",
                    }],
                }],
                "dates": [],
                "status": {
                    "raw_value": "更正",
                    "evidence": [{
                        "chunk_id": "doc328952-p1-c2-90c99c880fa5",
                        "quote": "更正公告",
                    }],
                },
            }],
            "no_event_reason": None,
        }
        return SemanticProviderResponse(
            identity=self.identity,
            parsed_output=parsed,
            raw_output=json.dumps(parsed, ensure_ascii=False),
            input_hash="input",
            output_hash="output-current",
            request_id="fixture-current",
            response_model=self.identity.model,
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            latency_ms=5,
        )


class FrozenBenchmarkRunnerTest(unittest.TestCase):
    def test_current_revision_table_cells_follow_pdf_document_order(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/328952"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_frozen_benchmark(
                ROOT,
                document_root.parent,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=root / "predictions.jsonl",
                report_path=root / "run.json",
                provider=CorrectedEarningsProvider(),
                document_ids=[328952],
            )
            row = json.loads(
                (root / "predictions.jsonl").read_text(encoding="utf-8")
            )

        self.assertEqual(result["failed"], 0, row)
        facts = {
            fact["name"]: fact["raw_value"]
            for fact in row["events"][0]["facts"]
        }
        self.assertEqual(facts["revenue"], "105,412.51")
        self.assertEqual(facts["net_profit"], "-953.39")

    def test_table_value_retrieval_scores_its_semantic_headers(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/1998"
        )

        benchmark_input = build_frozen_benchmark_input(
            ROOT,
            document_root,
            profile_id="a-share-announcement-mentions-v24",
        )

        chunks = {
            str(chunk["chunk_id"]): str(chunk.get("text") or "")
            for chunk in benchmark_input.bundle.payload["chunks"]
        }
        self.assertEqual(
            chunks["doc1998-p1-t0-dfbc5c4b7739cb27-r1-c2"],
            "16,200,000",
        )
        self.assertEqual(
            chunks["doc1998-p1-t0-dfbc5c4b7739cb27-r1-c8"],
            "10.00%",
        )

    def test_revision_boundary_chunks_survive_v24_packet_bounding(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/322230"
        )

        benchmark_input = build_frozen_benchmark_input(
            ROOT,
            document_root,
            profile_id="a-share-announcement-mentions-v24",
        )

        chunks = {
            str(chunk["chunk_id"]): str(chunk.get("text") or "")
            for chunk in benchmark_input.bundle.payload["chunks"]
        }
        self.assertIn("doc322230-p1-c7-2fe211f0be86", chunks)
        self.assertIn("doc322230-p1-c15-cce6f4ae3fa4", chunks)
        self.assertIn("业绩更正说明", chunks["doc322230-p1-c7-2fe211f0be86"])
        self.assertIn("原来披露", chunks["doc322230-p1-c15-cce6f4ae3fa4"])

    def test_revision_packet_excludes_superseded_body_values(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/328952"
        )

        benchmark_input = build_frozen_benchmark_input(
            ROOT,
            document_root,
            profile_id="a-share-announcement-mentions-v24",
        )

        chunks = {
            str(chunk["chunk_id"]): str(chunk.get("text") or "")
            for chunk in benchmark_input.bundle.payload["chunks"]
        }
        self.assertIn("doc328952-p1-c9-0891f2b55efd", chunks)
        self.assertIn("doc328952-p1-c10-7b19511a3291", chunks)
        self.assertIn("doc328952-p1-c18-bec075be26e0", chunks)
        self.assertNotIn("doc328952-p1-c20-5c5769486ce9", chunks)
        self.assertNotIn(
            "doc328952-p1-t2-ab46cbd56e61b01c-r1-c1",
            chunks,
        )

    def test_guarantee_packet_prefers_current_terms_over_historical_balance(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/260415"
        )

        benchmark_input = build_frozen_benchmark_input(
            ROOT,
            document_root,
            profile_id="a-share-announcement-mentions-v25",
        )

        chunks = {
            str(chunk["chunk_id"]): str(chunk.get("text") or "")
            for chunk in benchmark_input.bundle.payload["chunks"]
        }
        self.assertIn("doc260415-p4-c99-02ea6dae06cd", chunks)
        self.assertIn("3000 万元的流动资金贷款", chunks["doc260415-p4-c99-02ea6dae06cd"])
        self.assertIn("doc260415-p4-c100-ca060bf4ce3b", chunks)
        self.assertIn(
            "提供连带责任保证",
            chunks["doc260415-p4-c100-ca060bf4ce3b"],
        )

    def test_runner_repairs_once_when_one_routed_family_is_omitted(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/114674"
        )
        provider = MultiFamilyRepairProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_frozen_benchmark(
                ROOT,
                document_root.parent,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=root / "predictions.jsonl",
                report_path=root / "run.json",
                provider=provider,
                document_ids=[114674],
            )
            row = json.loads(
                (root / "predictions.jsonl").read_text(encoding="utf-8")
            )

        self.assertEqual(result["failed"], 0, result)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["usage"]["request_count"], 2)
        self.assertEqual(provider.repair_error, "semantic_candidate_family_unreviewed")
        self.assertEqual(
            {event["event_type"] for event in row["events"]},
            {"earnings_forecast", "litigation_arbitration"},
        )

    def test_reviewed_no_event_still_reports_all_provider_usage(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/829"
        )
        provider = NoEventProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_frozen_benchmark(
                ROOT,
                document_root.parent,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=root / "predictions.jsonl",
                report_path=root / "run.json",
                provider=provider,
                document_ids=[829],
            )
            row = json.loads(
                (root / "predictions.jsonl").read_text(encoding="utf-8")
            )

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["validation_repair_failures"], 0)
        self.assertEqual(result["usage"]["request_count"], 2)
        self.assertEqual(result["usage"]["total_tokens"], 240)
        self.assertEqual(len(provider.payloads), 2)
        self.assertEqual(row["usage"]["request_count"], 2)

    def test_runner_allows_one_bounded_validation_repair(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/829"
        )
        provider = RepairingPledgeProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_frozen_benchmark(
                ROOT,
                document_root.parent,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=root / "predictions.jsonl",
                report_path=root / "run.json",
                provider=provider,
                document_ids=[829],
            )

        self.assertEqual(result["failed"], 0, result)
        self.assertEqual(result["validation_repairs"], 1)
        self.assertEqual(result["validation_repair_failures"], 0)
        self.assertEqual(result["usage"]["request_count"], 2)
        self.assertEqual(
            provider.assert_repair_context["validation_error"]["code"],
            "no_event_review_required",
        )

    def test_compiler_recovers_unemitted_same_row_core_from_ir(self) -> None:
        document_root = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/829"
        )
        benchmark_input = build_frozen_benchmark_input(
            ROOT,
            document_root,
            profile_id="a-share-announcement-mentions-v24",
        )
        companion_id = "doc829-p1-t0-59e17439440db257-r1-c2"
        self.assertIn(
            companion_id,
            {node["node_id"] for node in benchmark_input.full_document_ir["nodes"]},
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_frozen_benchmark(
                ROOT,
                document_root.parent,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=root / "predictions.jsonl",
                report_path=root / "run.json",
                provider=PledgeCoreProvider(),
                document_ids=[829],
            )
            row = json.loads(
                (root / "predictions.jsonl").read_text(encoding="utf-8")
            )

        self.assertEqual(result["failed"], 0, row)
        self.assertEqual(row["events"][0]["effective_dates"][0]["value"], "2026-07-21")
        self.assertIn("share_count", {fact["name"] for fact in row["events"][0]["facts"]})
        self.assertIn(
            companion_id,
            {chunk["chunk_id"] for chunk in row["source_chunks"]},
        )

    def _write_workbench(self, root: Path, *, document_id: int = 7) -> Path:
        document_root = root / str(document_id)
        document_root.mkdir(parents=True)
        values = {
            "document.json": {
                "id": document_id,
                "title": "关于公司经营事项的公告",
                "ts_code": "000001.SZ",
                "name": "测试股份",
                "published_at": "2026-07-28T00:00:00+00:00",
                "source_url": "https://example.test/7",
            },
            "chunks.json": [
                {
                    "chunk_id": "doc7-p1-c1",
                    "page_number": 1,
                    "section": "body",
                    "bbox": [],
                    "text": "公司拟投资100亿元建设新能源项目。",
                }
            ],
            "tables.json": [],
            "entity_whitelist.json": [
                {
                    "entity_id": "000001.SZ",
                    "name": "测试股份",
                    "allowed_roles": ["issuer"],
                }
            ],
            "revision_context.json": [
                {
                    "document_id": 6,
                    "title": "关于公司经营事项的公告",
                    "published_at": "2026-07-27T00:00:00+00:00",
                    "relation": "same_source_record",
                }
            ],
            "reference.json": {"answer": "GOLD_SECRET_MUST_NOT_LEAK"},
        }
        for name, value in values.items():
            (document_root / name).write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
        return document_root

    def test_builds_v24_payload_without_reading_reference_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            document_root = self._write_workbench(Path(tmp))

            benchmark_input = build_frozen_benchmark_input(
                ROOT,
                document_root,
                profile_id="a-share-announcement-mentions-v24",
            )

        payload = benchmark_input.bundle.payload
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("GOLD_SECRET_MUST_NOT_LEAK", serialized)
        self.assertEqual(
            benchmark_input.bundle.prompt_version,
            "semantic-mentions-v17",
        )
        self.assertEqual(
            benchmark_input.bundle.taxonomy_version,
            "cn-announcement-taxonomy-v11",
        )
        self.assertEqual(
            payload["document_ir"]["ir_version"],
            "announcement-document-ir-v1",
        )
        self.assertEqual(
            payload["route_context"]["extraction_purpose"],
            "canonical_event",
        )
        self.assertTrue(payload["mention_templates"])
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, sort_keys=True)), 24_000)

    def test_runner_writes_predictions_and_usage_without_production_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbench = root / "workbench"
            self._write_workbench(workbench)
            predictions = root / "predictions.jsonl"
            report = root / "run.json"
            provider = NoEventProvider()

            result = run_frozen_benchmark(
                ROOT,
                workbench,
                profile_id="a-share-announcement-mentions-v24",
                predictions_path=predictions,
                report_path=report,
                provider=provider,
            )

            rows = [
                json.loads(line)
                for line in predictions.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            saved_report = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["usage"]["total_tokens"], 120)
        self.assertEqual(saved_report["profile_id"], "a-share-announcement-mentions-v24")
        self.assertEqual(rows[0]["status"], "complete")
        self.assertEqual(rows[0]["events"], [])
        self.assertEqual(
            rows[0]["provider_result"]["no_event_reason"],
            "fixture found no current event",
        )
        self.assertEqual(len(provider.payloads), 1)

    def test_prepares_blind_coding_plan_package_without_reference_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbench = root / "workbench"
            self._write_workbench(workbench)
            job = root / "coding-plan-job"

            result = prepare_frozen_coding_plan_job(
                ROOT,
                workbench,
                profile_id="a-share-announcement-mentions-v27",
                job_dir=job,
                provider="codex",
                model="gpt-5.6",
                client_version="coding-plan-v1",
            )

            manifest = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
            input_row = json.loads((job / "input.jsonl").read_text(encoding="utf-8"))
            exported = "\n".join(
                path.read_text(encoding="utf-8")
                for path in job.iterdir()
                if path.is_file()
            )

        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["documents"], 1)
        self.assertEqual(result["production_import"], False)
        self.assertEqual(manifest["executor"]["executor_mode"], "coding_plan")
        self.assertEqual(input_row["contract_version"], "semantic-payload-v4")
        self.assertTrue(input_row["semantic_task_id"].startswith("st-"))
        self.assertTrue(input_row["execution_job_id"].startswith("sej-"))
        self.assertEqual(input_row["binding_id"], manifest["executor"]["binding_id"])
        self.assertNotIn("GOLD_SECRET_MUST_NOT_LEAK", exported)
        self.assertNotIn("reference.json", exported)

    def test_collects_coding_plan_output_through_frozen_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbench = root / "workbench"
            self._write_workbench(workbench)
            job = root / "coding-plan-job"
            prepare_frozen_coding_plan_job(
                ROOT,
                workbench,
                profile_id="a-share-announcement-mentions-v24",
                job_dir=job,
                provider="codex",
                model="gpt-5.6",
                client_version="coding-plan-v1",
            )
            input_row = json.loads((job / "input.jsonl").read_text(encoding="utf-8"))
            output_row = {
                "contract_version": "semantic-extraction-output-v1",
                "document_id": input_row["document_id"],
                "artifact_hash": input_row["artifact_hash"],
                "input_hash": input_row["input_hash"],
                "semantic_task_id": input_row["semantic_task_id"],
                "execution_job_id": input_row["execution_job_id"],
                "binding_id": input_row["binding_id"],
                "executor": {
                    "kind": "coding-plan",
                    "provider": "codex",
                    "model": "gpt-5.6",
                    "client_version": "coding-plan-v1",
                },
                "usage": {"total_tokens": 321},
                "result": {
                    "document_id": input_row["document_id"],
                    "schema_version": "announcement-mentions-v1-lite",
                    "mentions": [],
                    "no_event_reason": "未发现满足合同的当前事件",
                },
            }
            (job / "output.jsonl").write_text(
                json.dumps(output_row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            predictions = root / "predictions.jsonl"
            report = root / "report.json"

            result = collect_frozen_coding_plan_job(
                ROOT,
                workbench,
                job_dir=job,
                predictions_path=predictions,
                report_path=report,
            )
            prediction = json.loads(predictions.read_text(encoding="utf-8"))

            input_row["payload"]["document"]["title"] = "tampered title"
            (job / "input.jsonl").write_text(
                json.dumps(input_row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tampered_result = collect_frozen_coding_plan_job(
                ROOT,
                workbench,
                job_dir=job,
                predictions_path=root / "tampered-predictions.jsonl",
                report_path=root / "tampered-report.json",
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["production_import"], False)
        self.assertEqual(prediction["status"], "complete")
        self.assertEqual(prediction["provider_result"], output_row["result"])
        self.assertEqual(prediction["executor"]["provider"], "codex")
        self.assertEqual(tampered_result["status"], "partial")
        self.assertEqual(tampered_result["failed"], 1)

    def test_external_coding_plan_gets_one_bounded_repair_round(self) -> None:
        workbench = (
            ROOT
            / "data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_job = root / "source-job"
            prepare_frozen_coding_plan_job(
                ROOT,
                workbench,
                profile_id="a-share-announcement-mentions-v27",
                job_dir=source_job,
                provider="claude",
                model="claude-fable-5",
                client_version="claude-code-2.1.215",
                document_ids=[829],
            )
            source_input = json.loads(
                (source_job / "input.jsonl").read_text(encoding="utf-8")
            )
            source_output = self._output_envelope(
                source_input,
                result={
                    "document_id": 829,
                    "schema_version": "announcement-mentions-v1-lite",
                    "mentions": [],
                    "no_event_reason": "未发现当前事件",
                },
            )
            (source_job / "output.jsonl").write_text(
                json.dumps(source_output, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            source_predictions = root / "source-predictions.jsonl"
            source_report = root / "source-report.json"
            first = collect_frozen_coding_plan_job(
                ROOT,
                workbench,
                job_dir=source_job,
                predictions_path=source_predictions,
                report_path=source_report,
            )
            self.assertEqual(first["failed"], 1)

            repair_job = root / "repair-job"
            prepared = prepare_frozen_coding_plan_repair_job(
                ROOT,
                workbench,
                source_job_dir=source_job,
                source_predictions_path=source_predictions,
                repair_job_dir=repair_job,
                provider="claude",
                model="claude-fable-5",
                client_version="claude-code-2.1.215",
            )
            repair_input = json.loads(
                (repair_job / "input.jsonl").read_text(encoding="utf-8")
            )
            repair_context = repair_input["payload"]["repair_context"]
            pledge_result = PledgeCoreProvider().extract(
                build_frozen_benchmark_input(
                    ROOT,
                    workbench / "829",
                    profile_id="a-share-announcement-mentions-v27",
                ).bundle,
                response_schema={},
            ).parsed_output
            repair_output = self._output_envelope(
                repair_input,
                result=pledge_result,
            )
            (repair_job / "output.jsonl").write_text(
                json.dumps(repair_output, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            merged_predictions = root / "merged-predictions.jsonl"
            repair_report = root / "repair-report.json"
            repaired = collect_frozen_coding_plan_repair_job(
                ROOT,
                workbench,
                source_job_dir=source_job,
                source_predictions_path=source_predictions,
                repair_job_dir=repair_job,
                predictions_path=merged_predictions,
                report_path=repair_report,
            )
            merged = json.loads(merged_predictions.read_text(encoding="utf-8"))
            tampered_source = json.loads(
                source_predictions.read_text(encoding="utf-8")
            )
            tampered_source["status"] = "complete"
            source_predictions.write_text(
                json.dumps(tampered_source, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "semantic_frozen_repair_source_predictions_changed",
            ):
                collect_frozen_coding_plan_repair_job(
                    ROOT,
                    workbench,
                    source_job_dir=source_job,
                    source_predictions_path=source_predictions,
                    repair_job_dir=repair_job,
                    predictions_path=root / "tampered-merged.jsonl",
                    report_path=root / "tampered-report.json",
                )

        self.assertEqual(prepared["documents"], 1)
        self.assertEqual(repair_context["attempt"], 1)
        self.assertEqual(
            repair_context["validation_error"]["code"],
            "no_event_review_required",
        )
        self.assertEqual(
            repair_context["previous_output"]["no_event_reason"],
            "未发现当前事件",
        )
        self.assertEqual(repaired["status"], "complete")
        self.assertEqual(repaired["repaired"], 1)
        self.assertEqual(repaired["failed"], 0)
        self.assertFalse(repaired["production_import"])
        self.assertEqual(merged["events"][0]["event_type"], "pledge_freeze")

    @staticmethod
    def _output_envelope(
        input_row: dict[str, object],
        *,
        result: dict[str, object],
    ) -> dict[str, object]:
        executor = input_row["executor"]
        assert isinstance(executor, dict)
        return {
            "contract_version": "semantic-extraction-output-v1",
            "document_id": input_row["document_id"],
            "artifact_hash": input_row["artifact_hash"],
            "input_hash": input_row["input_hash"],
            "semantic_task_id": input_row["semantic_task_id"],
            "execution_job_id": input_row["execution_job_id"],
            "binding_id": input_row["binding_id"],
            "executor": {
                "kind": "coding-plan",
                "provider": executor["provider"],
                "model": executor["model"],
                "client_version": executor["client_version"],
            },
            "usage": {},
            "result": result,
        }


if __name__ == "__main__":
    unittest.main()
