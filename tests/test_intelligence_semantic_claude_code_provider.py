from __future__ import annotations

import json
import subprocess
import unittest

from stock_analyze.intelligence.semantic.claude_code_provider import (
    ClaudeCodeSemanticProvider,
)
from stock_analyze.intelligence.semantic.provider import (
    SemanticInputBundle,
    SemanticProviderError,
)


def _bundle() -> SemanticInputBundle:
    return SemanticInputBundle(
        document_id=17,
        artifact_hash="a" * 64,
        parser_version="parser-v1",
        prompt_version="semantic-mentions-v1",
        schema_version="announcement-mentions-v1-lite",
        taxonomy_version="cn-announcement-taxonomy-v4",
        payload={"document": {"title": "测试公告"}, "chunks": []},
    )


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class ClaudeCodeSemanticProviderTest(unittest.TestCase):
    def test_extract_uses_fresh_toolless_structured_claude_session(self) -> None:
        calls: list[dict[str, object]] = []

        def run(command, **kwargs):
            calls.append({"command": command, **kwargs})
            envelope = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 321,
                "session_id": "session-1",
                "usage": {"input_tokens": 120, "output_tokens": 20},
                "modelUsage": {
                    "claude-fable-5": {
                        "inputTokens": 120,
                        "outputTokens": 20,
                    }
                },
                "structured_output": {"ok": True},
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(envelope),
                stderr="",
            )

        provider = ClaudeCodeSemanticProvider(
            system_prompt="Only JSON.",
            claude_path="/tmp/claude",
            run_command=run,
        )
        response = provider.extract(_bundle(), response_schema=SCHEMA)

        self.assertEqual(response.parsed_output, {"ok": True})
        self.assertEqual(response.response_model, "claude-fable-5")
        self.assertEqual(response.input_tokens, 120)
        self.assertEqual(response.output_tokens, 20)
        self.assertEqual(response.total_tokens, 140)
        self.assertEqual(response.request_id, "session-1")
        command = calls[0]["command"]
        self.assertIn("--safe-mode", command)
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--tools", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(
            command[command.index("--model") + 1],
            "claude-fable-5",
        )
        cli_schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", cli_schema)
        self.assertEqual(calls[0]["input"].count('"document_id":17'), 1)

    def test_quota_limit_is_retryable_and_does_not_become_document_failure(self) -> None:
        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="You've hit your usage limit · resets 5pm",
            )

        provider = ClaudeCodeSemanticProvider(
            system_prompt="Only JSON.",
            run_command=run,
        )
        with self.assertRaises(SemanticProviderError) as caught:
            provider.extract(_bundle(), response_schema=SCHEMA)
        self.assertEqual(caught.exception.code, "claude_code_quota_limited")
        self.assertTrue(caught.exception.retryable)

    def test_rejects_success_from_unexpected_model(self) -> None:
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1,
            "session_id": "session-1",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "modelUsage": {"glm-5.2": {}},
            "structured_output": {"ok": True},
        }

        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(envelope),
                stderr="",
            )

        provider = ClaudeCodeSemanticProvider(
            system_prompt="Only JSON.",
            run_command=run,
        )
        with self.assertRaises(SemanticProviderError) as caught:
            provider.extract(_bundle(), response_schema=SCHEMA)
        self.assertEqual(caught.exception.code, "claude_code_model_mismatch")

    def test_rejects_structured_output_that_fails_local_schema(self) -> None:
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1,
            "session_id": "session-1",
            "usage": {},
            "modelUsage": {"claude-fable-5": {}},
            "structured_output": {"ok": "yes"},
        }

        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(envelope),
                stderr="",
            )

        provider = ClaudeCodeSemanticProvider(
            system_prompt="Only JSON.",
            run_command=run,
        )
        with self.assertRaises(SemanticProviderError) as caught:
            provider.extract(_bundle(), response_schema=SCHEMA)
        self.assertEqual(
            caught.exception.code,
            "semantic_response_schema_invalid",
        )


if __name__ == "__main__":
    unittest.main()
