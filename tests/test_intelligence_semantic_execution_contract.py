from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.execution_contract import (
    ExecutorBinding,
    SemanticExecutionContractError,
    execution_job_id,
    semantic_task_id,
    verify_executor_identity,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class SemanticExecutionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.tmp.name))
        self.document_id, _ = self.store.insert_document(
            SourceDocument(
                source="tushare_announcement",
                source_id="execution-contract",
                title="执行合同测试公告",
                published_at="2026-08-09T01:00:00+00:00",
                first_seen_at="2026-08-09T01:01:00+00:00",
                effective_at="2026-08-09T01:01:00+00:00",
                source_url="https://example.test/execution.pdf",
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_same_task_has_distinct_immutable_executor_jobs(self) -> None:
        task_id = semantic_task_id(
            profile_hash="a" * 64,
            document_id=self.document_id,
            artifact_hash="b" * 64,
            input_hash="c" * 64,
        )
        claude = ExecutorBinding(
            executor_mode="coding_plan",
            provider="claude-code",
            model="claude-opus-4-8",
            client_version="claude-code-provider-v1",
        )
        deepseek = ExecutorBinding(
            executor_mode="api",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            client_version="semantic-http-provider-v1",
        )

        self.assertEqual(
            task_id,
            semantic_task_id(
                profile_hash="a" * 64,
                document_id=self.document_id,
                artifact_hash="b" * 64,
                input_hash="c" * 64,
            ),
        )
        self.assertNotEqual(
            execution_job_id(task_id, claude),
            execution_job_id(task_id, deepseek),
        )
        self.assertNotEqual(claude.binding_id, deepseek.binding_id)

    def test_runtime_identity_mismatch_fails_closed(self) -> None:
        binding = ExecutorBinding(
            executor_mode="api",
            provider="openai-compatible",
            model="deepseek-v4-pro",
            client_version="semantic-http-provider-v1",
        )

        with self.assertRaises(SemanticExecutionContractError) as caught:
            verify_executor_identity(
                binding,
                {
                    "provider": "claude-code",
                    "model": "claude-opus-4-8",
                    "client_version": "claude-code-provider-v1",
                },
            )
        self.assertEqual(caught.exception.code, "semantic_executor_identity_mismatch")

    def test_store_registers_lineage_and_rejects_illegal_transition(self) -> None:
        profile = self.store.register_semantic_contract_profile(
            profile_id="a-share-announcement-mentions-v21",
            profile_hash="a" * 64,
            status="canary",
        )
        binding = ExecutorBinding(
            executor_mode="coding_plan",
            provider="claude-code",
            model="claude-opus-4-8",
            client_version="claude-code-provider-v1",
        )
        self.store.register_semantic_executor_binding(
            profile_id=str(profile["profile_id"]),
            binding=binding,
            status="compatible",
        )
        task_id = semantic_task_id(
            profile_hash="a" * 64,
            document_id=self.document_id,
            artifact_hash="b" * 64,
            input_hash="c" * 64,
        )
        self.store.register_semantic_task(
            semantic_task_id=task_id,
            document_id=self.document_id,
            profile_id=str(profile["profile_id"]),
            artifact_hash="b" * 64,
            input_hash="c" * 64,
        )
        job_id = execution_job_id(task_id, binding)
        self.store.register_semantic_execution_job(
            execution_job_id=job_id,
            semantic_task_id=task_id,
            binding_id=binding.binding_id,
        )

        running = self.store.transition_semantic_execution_job(
            job_id,
            to_status="running",
        )
        self.assertEqual(running["status"], "running")
        with self.assertRaises(ValueError) as caught:
            self.store.transition_semantic_execution_job(
                job_id,
                to_status="accepted",
            )
        self.assertEqual(str(caught.exception), "semantic_execution_transition_invalid")

if __name__ == "__main__":
    unittest.main()
