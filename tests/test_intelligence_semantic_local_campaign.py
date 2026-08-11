from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.local_campaign import run_campaign


class _Provider:
    pass


class LocalSemanticCampaignTest(unittest.TestCase):
    def test_retries_quota_after_wait_and_checkpoints_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            job.mkdir()
            calls = []
            sleeps = []
            clock_values = iter([0.0, 1.0, 2.0, 3.0, 4.0])

            def runner(repo_root, job_path, *, provider):
                calls.append((repo_root, job_path, provider))
                if len(calls) == 1:
                    return {
                        "status": "partial",
                        "expected": 2,
                        "failed": 2,
                        "errors": [
                            {
                                "document_id": 1,
                                "error": "claude_code_quota_limited",
                                "retryable": True,
                            },
                            {
                                "document_id": 2,
                                "error": "claude_code_quota_limited",
                                "retryable": True,
                            },
                        ],
                        "mention_compilation": {
                            "accepted": 0,
                            "rejected": 0,
                            "dropped_items": 0,
                        },
                        "usage": {},
                    }
                return {
                    "status": "complete",
                    "expected": 2,
                    "failed": 0,
                    "errors": [],
                    "mention_compilation": {
                        "accepted": 2,
                        "rejected": 0,
                        "dropped_items": 0,
                    },
                    "usage": {"input_tokens": 20, "output_tokens": 4},
                }

            result = run_campaign(
                root,
                job,
                provider=_Provider(),
                duration_seconds=100,
                quota_wait_seconds=15,
                run_job_fn=runner,
                monotonic_clock=lambda: next(clock_values),
                sleep=lambda seconds: sleeps.append(seconds),
            )

            self.assertEqual(result["status"], "awaiting_human_audit")
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(result["quota_waits"], 1)
            self.assertEqual(sleeps, [15])
            saved = json.loads(
                (job / "campaign_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["status"], "awaiting_human_audit")

    def test_stops_on_non_retryable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            job.mkdir()

            def runner(repo_root, job_path, *, provider):
                return {
                    "status": "partial",
                    "expected": 1,
                    "failed": 1,
                    "errors": [
                        {
                            "document_id": 1,
                            "error": "semantic_response_schema_invalid",
                            "retryable": False,
                        }
                    ],
                    "mention_compilation": {
                        "accepted": 0,
                        "rejected": 0,
                        "dropped_items": 0,
                    },
                    "usage": {},
                }

            result = run_campaign(
                root,
                job,
                provider=_Provider(),
                duration_seconds=100,
                run_job_fn=runner,
                monotonic_clock=lambda: 0.0,
                sleep=lambda seconds: None,
            )

            self.assertEqual(result["status"], "quality_gate_failed")
            self.assertFalse(result["quality_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
