from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.canary import (
    CanaryExecution,
    SemanticCanaryError,
    run_provider_canary,
)


class SemanticCanaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = ["st-task-a", "st-task-b"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _job(self, label: str, binding_id: str) -> Path:
        path = self.root / label
        path.mkdir(parents=True)
        manifest = {
            "job_id": f"sj-{label}",
            "execution_contract_version": "semantic-execution-v1",
            "semantic_contract_hash": "c" * 64,
            "binding_id": binding_id,
            "executor_binding": {
                "contract_version": "semantic-execution-v1",
                "executor_mode": "coding_plan" if label == "claude" else "api",
                "provider": "claude-code" if label == "claude" else "openai-compatible",
                "model": "claude-fable-5" if label == "claude" else "deepseek-v4-pro",
                "client_version": (
                    "claude-code-provider-v1"
                    if label == "claude"
                    else "semantic-provider-v1"
                ),
            },
            "items": [
                {
                    "document_id": index + 1,
                    "semantic_task_id": task_id,
                    "execution_job_id": f"sej-{label}-{index}",
                    "binding_id": binding_id,
                }
                for index, task_id in enumerate(self.tasks)
            ],
        }
        (path / "job.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        return path

    def test_two_bindings_share_tasks_but_keep_outputs_separate(self) -> None:
        claude = self._job("claude", "seb-claude")
        deepseek = self._job("deepseek", "seb-deepseek")
        calls: list[Path] = []

        def fake_runner(repo_root, job_path, **kwargs):
            del repo_root, kwargs
            path = Path(job_path)
            calls.append(path)
            manifest = json.loads((path / "job.json").read_text(encoding="utf-8"))
            rows = [
                {
                    "document_id": item["document_id"],
                    "semantic_task_id": item["semantic_task_id"],
                    "execution_job_id": item["execution_job_id"],
                    "binding_id": item["binding_id"],
                }
                for item in manifest["items"]
            ]
            (path / "output.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            return {
                "status": "complete",
                "completed": 2,
                "failed": 0,
                "errors": [],
                "usage": {"total_tokens": 100, "latency_ms": 20},
            }

        report_path = self.root / "reports" / "canary_report.json"
        report = run_provider_canary(
            self.root,
            executions=[
                CanaryExecution(label="claude", job_path=claude, provider=object()),
                CanaryExecution(label="deepseek", job_path=deepseek, provider=object()),
            ],
            report_path=report_path,
            runner=fake_runner,
        )

        self.assertEqual(calls, [claude.resolve(), deepseek.resolve()])
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["production_approved"])
        self.assertFalse(report["imported"])
        self.assertEqual(report["task_count"], 2)
        self.assertNotEqual(
            report["executions"][0]["binding_id"],
            report["executions"][1]["binding_id"],
        )
        self.assertEqual(report["executions"][0]["schema_valid"], 2)
        self.assertEqual(report["executions"][1]["schema_valid"], 2)
        self.assertTrue(report_path.is_file())
        self.assertFalse((claude / "import_report.json").exists())
        self.assertFalse((deepseek / "import_report.json").exists())

    def test_mismatched_task_sets_fail_before_any_executor_call(self) -> None:
        claude = self._job("claude", "seb-claude")
        deepseek = self._job("deepseek", "seb-deepseek")
        manifest = json.loads((deepseek / "job.json").read_text(encoding="utf-8"))
        manifest["items"][1]["semantic_task_id"] = "st-other"
        (deepseek / "job.json").write_text(json.dumps(manifest), encoding="utf-8")
        calls = []

        with self.assertRaises(SemanticCanaryError) as raised:
            run_provider_canary(
                self.root,
                executions=[
                    CanaryExecution("claude", claude, provider=object()),
                    CanaryExecution("deepseek", deepseek, provider=object()),
                ],
                report_path=self.root / "report.json",
                runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )

        self.assertEqual(str(raised.exception), "semantic_canary_task_set_mismatch")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
