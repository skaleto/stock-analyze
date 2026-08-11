from __future__ import annotations

import json
import io
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from urllib import error, request
from unittest.mock import Mock, patch

import pandas as pd

from stock_analyze.cli import (
    _DashboardHTTPServer,
    _DashboardRequestHandler,
    main,
)
from stock_analyze.dashboard_http import DashboardResourceNotFound
from stock_analyze.intelligence.sources.base import FetchBatch
from stock_analyze.intelligence.semantic.research_cli import (
    main as semantic_research_main,
)
from stock_analyze.intelligence.schema import SCHEMA_VERSION
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument
from stock_analyze.research.storage import ResearchStore


class FakeAdapter:
    source = "fake_policy"

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        return FetchBatch((SourceDocument(
            source=self.source, source_id="1", title="关于支持发展人工智能产业的行动方案",
            published_at="2026-07-18T00:00:00Z", first_seen_at="2026-07-18T00:01:00Z",
            effective_at="2026-07-18T00:00:00Z", source_url="https://x.test/1",
            content="支持发展人工智能产业".encode(),
        ),), until)


class IntelligenceCliTest(unittest.TestCase):
    def test_semantic_repair_commands_are_explicit_and_auditable(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.exchange.prepare_repair_job",
                return_value={
                    "status": "prepared",
                    "job_id": "sj-repair",
                    "documents": 2,
                },
            ) as prepare,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-repair-prepare",
                "--repo-root", "/tmp/repo",
                "--document-id", "190713",
                "--document-id", "258827",
                "--reason", "quality-remediation",
                "--profile", "a-share-announcement-remediation-v1",
            ])

        self.assertEqual(exit_code, 0)
        prepare.assert_called_once_with(
            Path("/tmp/repo"),
            document_ids=[190713, 258827],
            reason="quality-remediation",
            profile_id="a-share-announcement-remediation-v1",
            max_input_characters=40_000,
        )
        self.assertEqual(json.loads(output.getvalue())["job_id"], "sj-repair")

        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.exchange.rollback_repair",
                return_value={"status": "rolled_back", "rolled_back": 2},
            ) as rollback,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-repair-rollback",
                "--repo-root", "/tmp/repo",
                "--repair-id", "repair-test",
            ])
        self.assertEqual(exit_code, 0)
        rollback.assert_called_once_with(Path("/tmp/repo"), "repair-test")

    def test_semantic_exchange_commands_keep_executor_optional(
        self,
    ) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.exchange.prepare_job",
                return_value={
                    "status": "prepared",
                    "job_id": "sj-test",
                    "job_dir": "/tmp/repo/job",
                    "documents": 2,
                },
            ) as prepare,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-prepare",
                "--repo-root", "/tmp/repo",
                "--profile", "a-share-announcement-v1",
                "--limit", "2",
                "--max-input-characters", "12000",
            ])

        self.assertEqual(exit_code, 0)
        prepare.assert_called_once_with(
            Path("/tmp/repo"),
            profile_id="a-share-announcement-v1",
            limit=2,
            max_input_characters=12000,
        )
        self.assertEqual(json.loads(output.getvalue())["job_id"], "sj-test")

    def test_semantic_run_accepts_executor_config_path(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.exchange.run_job",
                return_value={
                    "status": "complete",
                    "completed": 1,
                    "failed": 0,
                },
            ) as run,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-run",
                "--repo-root", "/tmp/repo",
                "--job", "sj-test",
                "--executor-config", "/etc/stock-analyze/executor.yaml",
            ])

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(
            Path("/tmp/repo"),
            "sj-test",
            executor_config="/etc/stock-analyze/executor.yaml",
        )

    def test_semantic_prepare_accepts_an_immutable_executor_binding(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.exchange.prepare_job",
                return_value={
                    "status": "prepared",
                    "job_id": "sj-v21",
                    "job_dir": "/tmp/repo/job",
                    "documents": 1,
                },
            ) as prepare,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-prepare",
                "--repo-root", "/tmp/repo",
                "--profile", "a-share-announcement-mentions-v21",
                "--limit", "1",
                "--max-input-characters", "24000",
                "--executor-mode", "api",
                "--provider", "openai-compatible",
                "--model", "deepseek-v4-pro",
                "--client-version", "semantic-provider-v1",
            ])

        self.assertEqual(exit_code, 0)
        prepare.assert_called_once_with(
            Path("/tmp/repo"),
            profile_id="a-share-announcement-mentions-v21",
            limit=1,
            max_input_characters=24_000,
            executor_mode="api",
            executor_provider="openai-compatible",
            executor_model="deepseek-v4-pro",
            executor_client_version="semantic-provider-v1",
        )
        self.assertEqual(json.loads(output.getvalue())["job_id"], "sj-v21")

    def test_model_effect_command_is_research_only(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.research.intelligence_effect."
                "evaluate_latest_intelligence_effect",
                return_value={
                    "status": "insufficient_support",
                    "market": "a_share",
                    "activation": "unchanged",
                },
            ) as evaluate,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-model-effect",
                "--repo-root", "/tmp/repo",
                "--market", "a_share",
                "--as-of", "2026-07-28",
            ])

        self.assertEqual(exit_code, 3)
        evaluate.assert_called_once_with(
            Path("/tmp/repo"),
            market="a_share",
            as_of="2026-07-28",
        )

    def test_dashboard_serves_lazy_intelligence_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            server = _DashboardHTTPServer(
                ("127.0.0.1", 0),
                partial(
                    _DashboardRequestHandler,
                    directory=str(reports),
                ),
            )
            thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with patch(
                    "stock_analyze.dashboard_api."
                    "build_dashboard_intelligence_data",
                    return_value={"documents": 3, "rows": []},
                ) as summary:
                    with request.urlopen(
                        base
                        + "/api/dashboard/intelligence.json"
                        "?market=a_share&agent=codex",
                        timeout=5,
                    ) as response:
                        payload = json.load(response)
                    self.assertEqual(payload["documents"], 3)
                    summary.assert_called_once_with(
                        repo_root=root.resolve(),
                        market="a_share",
                        agent="codex",
                    )

                with patch(
                    "stock_analyze.dashboard_api."
                    "build_dashboard_intelligence_event_data",
                    side_effect=DashboardResourceNotFound("missing"),
                ):
                    with self.assertRaises(error.HTTPError) as context:
                        request.urlopen(
                            base
                            + "/api/dashboard/intelligence-event.json"
                            "?market=a_share&agent=codex&event_id=missing",
                            timeout=5,
                        )
                    self.assertEqual(context.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_semantic_freeze_manifest_prints_stratified_result(
        self,
    ) -> None:
        output = io.StringIO()
        expected = {
            "status": "complete",
            "benchmark": "announcement-v1",
            "documents": 240,
            "manifest_hash": "a" * 64,
        }
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark."
                "freeze_benchmark_manifest",
                return_value=expected,
            ) as freeze,
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-freeze-manifest",
                "--repo-root", "/tmp/repo",
                "--benchmark", "announcement-v1",
            ])

        self.assertEqual(exit_code, 0)
        freeze.assert_called_once_with(
            Path("/tmp/repo"),
            benchmark_name="announcement-v1",
        )
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_semantic_draft_gold_uses_resume_exit_for_disagreements(
        self,
    ) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark."
                "draft_benchmark_gold",
                return_value={
                    "status": "needs_adjudication",
                    "documents": 240,
                    "consensus": 220,
                    "disagreements": 20,
                },
            ) as draft,
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-draft-gold",
                "--repo-root", "/tmp/repo",
                "--benchmark", "announcement-v1",
            ])

        self.assertEqual(exit_code, 3)
        draft.assert_called_once_with(
            Path("/tmp/repo"),
            benchmark_name="announcement-v1",
        )

    def test_semantic_finalize_gold_requires_decision_file(
        self,
    ) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark."
                "finalize_benchmark_gold",
                return_value={
                    "status": "complete",
                    "documents": 240,
                    "benchmark_hash": "b" * 64,
                },
            ) as finalize,
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-finalize-gold",
                "--repo-root", "/tmp/repo",
                "--benchmark", "announcement-v1",
                "--decisions", "/tmp/decisions.jsonl",
            ])

        self.assertEqual(exit_code, 0)
        finalize.assert_called_once_with(
            Path("/tmp/repo"),
            benchmark_name="announcement-v1",
            decisions_path=Path("/tmp/decisions.jsonl"),
        )

    def test_semantic_benchmark_command_prints_immutable_run(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark.run_frozen_benchmark",
                return_value={
                    "run_id": "semantic-run-1",
                    "passed": True,
                    "failed_metrics": [],
                    "report_path": "reports/intelligence/semantic_benchmark_semantic-run-1.json",
                },
            ) as runner,
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-benchmark",
                "--repo-root", "/tmp/repo",
                "--benchmark", "announcement-v1",
                "--provider-config", "candidate-a",
            ])

        self.assertEqual(exit_code, 0)
        runner.assert_called_once_with(
            Path("/tmp/repo"),
            benchmark_name="announcement-v1",
            provider_config="candidate-a",
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "failed_metrics": [],
                "passed": True,
                "report_path": "reports/intelligence/semantic_benchmark_semantic-run-1.json",
                "run_id": "semantic-run-1",
            },
        )

    def test_semantic_materialize_command_runs_one_bounded_resume_batch(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark."
                "materialize_candidate_outputs",
                return_value={
                    "status": "partial",
                    "processed": 7,
                    "succeeded": 7,
                    "failed": 0,
                    "remaining": 233,
                    "output_path": (
                        "data/shared/intelligence/benchmarks/announcement-v1/"
                        "candidate_outputs/candidate-a.jsonl"
                    ),
                },
            ) as materialize,
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-materialize",
                "--repo-root", "/tmp/repo",
                "--benchmark", "announcement-v1",
                "--provider-config", "candidate-a",
                "--limit", "7",
            ])

        self.assertEqual(exit_code, 2)
        materialize.assert_called_once_with(
            Path("/tmp/repo"),
            benchmark_name="announcement-v1",
            provider_config="candidate-a",
            limit=7,
        )
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "failed": 0,
                "output_path": (
                    "data/shared/intelligence/benchmarks/announcement-v1/"
                    "candidate_outputs/candidate-a.jsonl"
                ),
                "processed": 7,
                "remaining": 233,
                "status": "partial",
                "succeeded": 7,
            },
        )

    def test_semantic_promote_exits_two_and_prints_failed_metrics(self) -> None:
        from stock_analyze.intelligence.semantic.benchmark import PromotionRejected

        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark.promote_candidate",
                side_effect=PromotionRejected(("event_recall", "numeric_exact_match")),
            ),
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-promote",
                "--repo-root", "/tmp/repo",
                "--benchmark-run-id", "failed-run",
            ])

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "benchmark_run_id": "failed-run",
                "failed_metrics": ["event_recall", "numeric_exact_match"],
                "status": "rejected",
            },
        )

    def test_semantic_promote_prints_pinned_champion(self) -> None:
        from stock_analyze.intelligence.semantic.benchmark import ChampionIdentity

        champion = ChampionIdentity(
            benchmark_run_id="passing-run",
            provider_config="candidate-a",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            generation_config_hash="1" * 64,
            prompt_version="announcement-event-v1",
            schema_version="announcement-events-v1",
            taxonomy_version="cn-announcement-taxonomy-v1",
            parser_version="announcement-layout-v1",
            benchmark_name="announcement-v1",
            benchmark_hash="a" * 64,
            promoted_at="2026-07-25T02:00:00+00:00",
        )
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.semantic.benchmark.promote_candidate",
                return_value=champion,
            ),
            redirect_stdout(output),
        ):
            exit_code = semantic_research_main([
                "intelligence-semantic-promote",
                "--repo-root", "/tmp/repo",
                "--benchmark-run-id", "passing-run",
            ])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "champion")
        self.assertEqual(payload["champion"]["model"], "deepseek-v4-pro")
        self.assertEqual(payload["champion"]["benchmark_run_id"], "passing-run")

    def test_reconcile_cli_prints_json_and_preserves_arguments(self) -> None:
        expected = {
            "status": "complete",
            "counts": {},
            "elapsed_seconds": 1.25,
            "retryable_failures": 0,
            "terminal_failures": 0,
            "next_queue_depth": 0,
        }
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.operations.run_intelligence_reconcile",
                return_value=expected,
            ) as runner,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-reconcile",
                "--repo-root", "/tmp/repo",
                "--lookback-days", "3",
                "--limit", "77",
                "--stages", "metadata", "enqueue", "download",
            ])

        self.assertEqual(exit_code, 0)
        runner.assert_called_once_with(
            Path("/tmp/repo"),
            lookback_days=3,
            limit=77,
            stages=("metadata", "enqueue", "download"),
        )
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_main_cli_rejects_retired_semantic_research_commands(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main([
                "intelligence-semantic-benchmark",
                "--repo-root", "/tmp/repo",
                "--provider-config", "candidate-a",
            ])
        self.assertEqual(raised.exception.code, 2)

    def test_operation_defaults_keep_legacy_semantic_out_of_mainline(
        self,
    ) -> None:
        expected = {
            "status": "complete",
            "counts": {},
            "elapsed_seconds": 0.1,
            "retryable_failures": 0,
            "terminal_failures": 0,
            "next_queue_depth": 0,
        }
        with (
            patch(
                "stock_analyze.intelligence.operations.run_intelligence_enrich",
                return_value=expected,
            ) as enrich,
            patch(
                "stock_analyze.intelligence.operations.run_intelligence_reconcile",
                return_value=expected,
            ) as reconcile,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                main(
                    [
                        "intelligence-enrich",
                        "--repo-root",
                        "/tmp/repo",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "intelligence-reconcile",
                        "--repo-root",
                        "/tmp/repo",
                    ]
                ),
                0,
            )

        enrich.assert_called_once_with(
            Path("/tmp/repo"),
            limit=500,
            stages=("enqueue", "download", "parse"),
        )
        reconcile.assert_called_once_with(
            Path("/tmp/repo"),
            lookback_days=2,
            limit=500,
            stages=("metadata", "enqueue", "download", "parse"),
        )

    def test_semantic_status_cli_prints_snapshot(self) -> None:
        expected = {
            "metadata": {"documents": 0},
            "artifacts": {"queued": 0},
            "semantic": {"queued": 0},
        }
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.operations.run_semantic_status",
                return_value=expected,
            ) as runner,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-semantic-status",
                "--repo-root", "/tmp/repo",
            ])

        self.assertEqual(exit_code, 0)
        runner.assert_called_once_with(Path("/tmp/repo"))
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_prune_raw_cli_prints_bounded_cleanup_result(self) -> None:
        expected = {
            "status": "complete",
            "source": "tushare_announcement",
            "scanned_files": 12,
            "deleted_files": 10,
            "deleted_bytes": 40960,
            "retained_files": 2,
        }
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.operations."
                "run_intelligence_prune_raw",
                return_value=expected,
            ) as runner,
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-prune-raw",
                "--repo-root", "/tmp/repo",
                "--source", "tushare_announcement",
            ])

        self.assertEqual(exit_code, 0)
        runner.assert_called_once_with(
            Path("/tmp/repo"),
            source="tushare_announcement",
        )
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_fatal_operation_cli_returns_two_without_secret_text(self) -> None:
        from stock_analyze.intelligence.operations import FatalOperationError

        error = FatalOperationError(
            "database",
            {
                "status": "failed",
                "error": "database",
                "counts": {},
                "retryable_failures": 0,
                "terminal_failures": 1,
                "next_queue_depth": 0,
            },
        )
        output = io.StringIO()
        with (
            patch(
                "stock_analyze.intelligence.operations.run_intelligence_reconcile",
                side_effect=error,
            ),
            patch.dict("os.environ", {"TUSHARE_TOKEN": "never-print-this-token"}),
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-reconcile",
                "--repo-root", "/tmp/repo",
            ])

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "database")
        self.assertNotIn("never-print-this-token", output.getvalue())

    def test_ingest_extract_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "configs" / "intelligence_sources.yaml").write_text("schema_version: 1\nsources: {}\n")
            with patch("stock_analyze.intelligence.ingestion.build_adapters", return_value=(FakeAdapter(),)):
                self.assertEqual(main(["intelligence-ingest", "--repo-root", str(root), "--until", "2026-07-19T00:00:00Z"]), 0)
            self.assertEqual(main(["intelligence-extract", "--repo-root", str(root)]), 0)
            self.assertEqual(main(["intelligence-status", "--repo-root", str(root)]), 0)
            report = json.loads((root / "reports" / "intelligence" / "quality_latest.json").read_text())
            self.assertEqual(report["documents"], 1)
            self.assertEqual(report["events"], 1)
            self.assertEqual(report["schema_version"], SCHEMA_VERSION)
            self.assertEqual(report["point_in_time_quality"]["negative_ingestion_delay_rows"], 0)
            self.assertIsNone(report["cross_source_audit"]["latest"])
            self.assertAlmostEqual(
                report["sources"][0]["median_ingestion_delay_minutes"],
                1.0,
            )

    def test_status_includes_latest_cross_source_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (
                root / "configs" / "intelligence_sources.yaml"
            ).write_text(
                "schema_version: 1\nsources: {}\n",
                encoding="utf-8",
            )
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            store.record_source_audit(
                run_id="audit-1",
                as_of_date="2026-07-24",
                dataset_scope="announcement",
                primary_source="tushare",
                secondary_source="ifind",
                status="success",
                supplement_enabled=True,
                metrics={"datasets": {"announcement": {}}},
                items=[
                    {
                        "dataset": "announcement",
                        "item_key": "000001.SZ|回购",
                        "comparison_status": "matched",
                    },
                    {
                        "dataset": "announcement",
                        "item_key": "000002.SZ|监管函",
                        "comparison_status": "supplemented",
                    },
                ],
                started_at="2026-07-24T10:00:00+00:00",
                finished_at="2026-07-24T10:01:00+00:00",
            )

            self.assertEqual(
                main([
                    "intelligence-status",
                    "--repo-root",
                    str(root),
                ]),
                0,
            )
            report = json.loads(
                (
                    root
                    / "reports"
                    / "intelligence"
                    / "quality_latest.json"
                ).read_text()
            )

            latest = report["cross_source_audit"]["latest"]
            self.assertEqual(latest["as_of"], "2026-07-24")
            self.assertEqual(latest["status"], "success")
            self.assertEqual(
                latest["counts"]["announcement"],
                {"matched": 1, "supplemented": 1},
            )

    def test_evaluate_command_uses_latest_common_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchStore(root / "data" / "research")
            features = pd.DataFrame([
                {
                    "code": f"00000{index}", "trade_date": "20260710",
                    "event_positive_decay_5d": float(index),
                }
                for index in range(1, 7)
            ])
            labels = pd.DataFrame([
                {
                    "code": f"00000{index}", "trade_date": "20260710", "horizon": 5,
                    "excess_return": float(index) / 100,
                }
                for index in range(1, 7)
            ])
            store.write_feature_snapshot("a_share", "20260718", features)
            store.write_label_snapshot("a_share", "20260718", labels)
            self.assertEqual(main([
                "intelligence-evaluate", "--repo-root", str(root), "--market", "a_share",
            ]), 0)
            self.assertTrue(
                (root / "reports" / "intelligence" / "factor_validation_a_share_20260718.json").exists()
            )

    def test_backfill_command_uses_isolated_history_contract(self) -> None:
        expected = {
            "status": "complete",
            "source": "tushare_announcement",
            "partitions_complete": 2,
            "partitions_failed": 0,
            "fetched": 10,
            "inserted": 8,
            "b_share_filtered": 2,
            "live_cursor_unchanged": True,
        }
        pipeline = Mock()
        pipeline.backfill.return_value = expected
        output = io.StringIO()

        with (
            patch(
                "stock_analyze.intelligence.ingestion.IntelligencePipeline",
                return_value=pipeline,
            ),
            patch.dict("os.environ", {"TUSHARE_TOKEN": "never-print-this-token"}),
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-backfill",
                "--repo-root", "/tmp/repo",
                "--source", "tushare_announcement",
                "--start-date", "1990-12-19",
                "--end-date", "2026-07-24",
                "--max-partitions", "5",
                "--resume",
            ])

        self.assertEqual(exit_code, 0)
        pipeline.backfill.assert_called_once_with(
            source="tushare_announcement",
            start_date="1990-12-19",
            end_date="2026-07-24",
            max_partitions=5,
            resume=True,
        )
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertNotIn("never-print-this-token", output.getvalue())

    def test_backfill_partial_uses_distinct_resume_exit_code(self) -> None:
        pipeline = Mock()
        pipeline.backfill.return_value = {
            "status": "partial",
            "source": "tushare_announcement",
            "partitions_remaining": 8,
            "live_cursor_unchanged": True,
        }
        output = io.StringIO()

        with (
            patch(
                "stock_analyze.intelligence.ingestion.IntelligencePipeline",
                return_value=pipeline,
            ),
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-backfill",
                "--repo-root", "/tmp/repo",
                "--source", "tushare_announcement",
                "--start-date", "1990-12-19",
                "--end-date", "2026-07-24",
                "--max-partitions", "5",
                "--resume",
            ])

        self.assertEqual(exit_code, 3)
        self.assertEqual(
            json.loads(output.getvalue())["status"],
            "partial",
        )

    def test_backfill_failure_is_json_and_never_echoes_token(self) -> None:
        pipeline = Mock()
        pipeline.backfill.side_effect = RuntimeError(
            "provider request exposed never-print-this-token"
        )
        output = io.StringIO()

        with (
            patch(
                "stock_analyze.intelligence.ingestion.IntelligencePipeline",
                return_value=pipeline,
            ),
            patch.dict(
                "os.environ",
                {"TUSHARE_TOKEN": "never-print-this-token"},
            ),
            redirect_stdout(output),
        ):
            exit_code = main([
                "intelligence-backfill",
                "--repo-root", "/tmp/repo",
                "--source", "tushare_announcement",
                "--start-date", "1990-12-19",
                "--end-date", "2026-07-24",
                "--max-partitions", "5",
                "--resume",
            ])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["source"], "tushare_announcement")
        self.assertEqual(payload["error"], "RuntimeError")
        self.assertNotIn("never-print-this-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
