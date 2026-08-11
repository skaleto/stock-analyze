from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from stock_analyze.intelligence.semantic.provider import (
    OpenAICompatibleSemanticProvider,
    SemanticInputBundle,
    SemanticProviderError,
    SemanticProviderUnavailable,
)


VALID_SCHEMA = {
    "type": "object",
    "required": ["document_id", "events"],
    "additionalProperties": False,
    "properties": {
        "document_id": {"type": "string"},
        "events": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
}
VALID_OUTPUT = {"document_id": "doc-1", "events": []}


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class ScriptedTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def provider_payload(
    output: object = VALID_OUTPUT,
    *,
    usage: object = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "chatcmpl-test",
        "model": "deepseek-v4-pro",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def bundle(
    *,
    text: str = "公告正文",
    token_estimate: int = 32,
    document_id: str = "doc-1",
    repair: bool = False,
) -> SemanticInputBundle:
    payload: dict[str, object] = {
        "chunks": [
            {
                "chunk_id": f"{document_id}-p1-c1",
                "page_number": 1,
                "text": text,
            }
        ]
    }
    if repair:
        payload["repair_context"] = {
            "attempt": 1,
            "validation_error": {
                "code": "semantic_candidate_validation_failed",
            },
        }
    return SemanticInputBundle(
        document_id=document_id,
        artifact_hash="a" * 64,
        parser_version="announcement-layout-v1",
        prompt_version="announcement-event-v1",
        schema_version="announcement-events-v1",
        taxonomy_version="cn-announcement-taxonomy-v1",
        payload=payload,
        input_token_estimate=token_estimate,
    )


class SemanticProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.key_path = self.root / "deepseek.key"
        self.key_path.write_text("secret-provider-key\n", encoding="utf-8")

    def make_provider(
        self,
        outcomes: list[object],
        **overrides: object,
    ) -> tuple[OpenAICompatibleSemanticProvider, ScriptedTransport]:
        transport = ScriptedTransport(outcomes)
        kwargs: dict[str, object] = {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "api_key_file": self.key_path,
            "system_prompt": "只抽取有证据支持的结构化事件。",
            "max_output_tokens": 8192,
            "temperature": 0,
            "response_format": "json_object",
            "server_side_json_schema": False,
            "local_schema_validation": True,
            "request_timeout_seconds": 120,
            "max_attempts": 3,
            "backoff_seconds": 0.25,
            "max_backoff_seconds": 1.0,
            "max_input_characters": 180_000,
            "max_documents_per_daily_run": 500,
            "daily_input_token_budget": 3_000_000,
            "transport": transport,
        }
        kwargs.update(overrides)
        return OpenAICompatibleSemanticProvider(**kwargs), transport

    def test_from_config_reads_model_and_key_file_from_environment(self) -> None:
        transport = ScriptedTransport(
            [FakeResponse(200, provider_payload())]
        )
        config = {
            "semantic": {
                "provider": {
                    "kind": "openai-compatible",
                    "base_url": "https://api.deepseek.com",
                    "api_key_file_env": "INTELLIGENCE_LLM_API_KEY_FILE",
                    "response_format": "json_object",
                    "server_side_json_schema": False,
                    "local_schema_validation": True,
                },
                "candidate_profiles": {
                    "candidate-a": {
                        "model_env": "INTELLIGENCE_LLM_MODEL_CANDIDATE_A",
                        "max_output_tokens": 8192,
                        "temperature": 0,
                        "thinking": "disabled",
                        "service_tier": "auto",
                    }
                },
                "budgets": {
                    "request_timeout_seconds": 120,
                    "max_attempts": 3,
                    "max_input_characters": 180_000,
                    "max_documents_per_daily_run": 500,
                    "daily_input_token_budget": 3_000_000,
                },
            }
        }
        provider = OpenAICompatibleSemanticProvider.from_config(
            config,
            profile_name="candidate-a",
            system_prompt="fixed-prompt-v1",
            environ={
                "INTELLIGENCE_LLM_API_KEY_FILE": str(self.key_path),
                "INTELLIGENCE_LLM_MODEL_CANDIDATE_A": "deepseek-v4-pro",
            },
            transport=transport,
        )

        response = provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(response.parsed_output, VALID_OUTPUT)
        self.assertEqual(provider.identity.provider, "openai-compatible")
        self.assertEqual(provider.identity.model, "deepseek-v4-pro")
        self.assertEqual(provider.identity.endpoint_host, "api.deepseek.com")
        self.assertNotIn("secret-provider-key", repr(provider.identity))
        call = transport.calls[0]
        self.assertEqual(
            call["headers"],
            {
                "Authorization": "Bearer secret-provider-key",
                "Content-Type": "application/json",
            },
        )
        self.assertNotIn(
            "secret-provider-key",
            json.dumps(call["json"], ensure_ascii=False),
        )
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(call["json"]["service_tier"], "auto")

    def test_from_config_accepts_a_literal_runtime_model(self) -> None:
        transport = ScriptedTransport(
            [FakeResponse(200, provider_payload())]
        )
        config = {
            "semantic": {
                "provider": {
                    "kind": "openai-compatible",
                    "base_url": "https://api.deepseek.com",
                    "api_key_file_env": "INTELLIGENCE_LLM_API_KEY_FILE",
                },
                "candidate_profiles": {
                    "production": {
                        "model": "deepseek-v4-pro",
                        "max_output_tokens": 8192,
                    }
                },
                "budgets": {},
            }
        }

        provider = OpenAICompatibleSemanticProvider.from_config(
            config,
            profile_name="production",
            system_prompt="fixed-prompt-v1",
            environ={
                "INTELLIGENCE_LLM_API_KEY_FILE": str(self.key_path),
            },
            transport=transport,
        )

        response = provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(response.parsed_output, VALID_OUTPUT)
        self.assertEqual(provider.identity.model, "deepseek-v4-pro")

    def test_json_object_mode_supplies_schema_in_bounded_user_bundle(self) -> None:
        provider, transport = self.make_provider(
            [FakeResponse(200, provider_payload())]
        )

        provider.extract(bundle(), response_schema=VALID_SCHEMA)

        request_json = transport.calls[0]["json"]
        self.assertIsInstance(request_json, dict)
        assert isinstance(request_json, dict)
        self.assertEqual(
            request_json["response_format"],
            {"type": "json_object"},
        )
        self.assertNotEqual(
            request_json["response_format"].get("type"),  # type: ignore[union-attr]
            "json_schema",
        )
        messages = request_json["messages"]
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual(messages[0], {
            "role": "system",
            "content": "只抽取有证据支持的结构化事件。",
        })
        supplied = json.loads(messages[1]["content"])
        self.assertEqual(supplied["response_schema"], VALID_SCHEMA)
        self.assertEqual(supplied["document"]["document_id"], "doc-1")
        self.assertFalse(supplied["server_side_schema_guaranteed"])
        self.assertTrue(supplied["local_schema_validation_required"])

    def test_generation_controls_are_closed_and_sent_to_provider(
        self,
    ) -> None:
        provider, transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            thinking_type="disabled",
            service_tier="auto",
        )

        provider.extract(bundle(), response_schema=VALID_SCHEMA)

        request_json = transport.calls[0]["json"]
        self.assertEqual(
            request_json["thinking"],
            {"type": "disabled"},
        )
        self.assertEqual(request_json["service_tier"], "auto")
        with self.assertRaisesRegex(
            ValueError,
            "semantic_provider_thinking_type_invalid",
        ):
            self.make_provider([], thinking_type="free-form")
        with self.assertRaisesRegex(
            ValueError,
            "semantic_provider_service_tier_invalid",
        ):
            self.make_provider([], service_tier="fast")

    def test_validates_provider_output_locally_and_never_coerces(self) -> None:
        invalid_output = {"document_id": 1, "events": []}
        provider, _ = self.make_provider(
            [FakeResponse(200, provider_payload(invalid_output))]
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_response_schema_invalid",
        )
        self.assertEqual(raised.exception.raw_output, json.dumps(
            invalid_output,
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        self.assertNotIn(raised.exception.raw_output, str(raised.exception))
        self.assertNotIn(raised.exception.raw_output, repr(raised.exception))

    def test_one_bounded_schema_repair_round_can_return_a_valid_response(
        self,
    ) -> None:
        invalid_output = {"document_id": 1, "events": []}
        provider, transport = self.make_provider(
            [
                FakeResponse(
                    200,
                    provider_payload(invalid_output),
                ),
                FakeResponse(200, provider_payload()),
            ],
            schema_repair_attempts=1,
        )

        response = provider.extract(
            bundle(),
            response_schema=VALID_SCHEMA,
        )

        self.assertEqual(response.parsed_output, VALID_OUTPUT)
        self.assertEqual(len(transport.calls), 2)
        repair_request = transport.calls[1]["json"]
        self.assertIsInstance(repair_request, dict)
        assert isinstance(repair_request, dict)
        messages = repair_request["messages"]
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "user"],
        )
        repair_contract = json.loads(messages[-1]["content"])
        self.assertEqual(
            repair_contract["validation_error"],
            "semantic_response_schema_invalid",
        )
        self.assertEqual(
            repair_contract["rules"],
            [
                "preserve_facts_and_evidence",
                "change_structure_only",
                "return_json_only",
            ],
        )

    def test_local_validation_enforces_json_schema_formats(self) -> None:
        dated_schema = {
            "type": "object",
            "required": ["effective_date"],
            "additionalProperties": False,
            "properties": {
                "effective_date": {"type": "string", "format": "date"},
            },
        }
        provider, _ = self.make_provider(
            [
                FakeResponse(
                    200,
                    provider_payload({"effective_date": "2026-99-99"}),
                )
            ]
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=dated_schema)

        self.assertEqual(
            raised.exception.code,
            "semantic_response_schema_invalid",
        )

    def test_numeric_document_id_is_compatible_with_semantic_contracts(
        self,
    ) -> None:
        provider, transport = self.make_provider(
            [FakeResponse(200, provider_payload())]
        )
        numeric_bundle = SemanticInputBundle(
            document_id=17,
            artifact_hash="a" * 64,
            parser_version="announcement-layout-v1",
            prompt_version="announcement-event-v1",
            schema_version="announcement-events-v1",
            taxonomy_version="cn-announcement-taxonomy-v1",
            payload={"chunks": []},
            input_token_estimate=8,
        )

        provider.extract(numeric_bundle, response_schema=VALID_SCHEMA)

        request_json = transport.calls[0]["json"]
        assert isinstance(request_json, dict)
        messages = request_json["messages"]
        assert isinstance(messages, list)
        supplied = json.loads(messages[1]["content"])
        self.assertEqual(supplied["document"]["document_id"], 17)

    def test_invalid_json_returns_typed_failed_response_without_body_in_error(self) -> None:
        payload = provider_payload()
        payload["choices"][0]["message"]["content"] = "{not-json"  # type: ignore[index]
        provider, _ = self.make_provider([FakeResponse(200, payload)])

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_response_json_invalid",
        )
        self.assertEqual(raised.exception.raw_output, "{not-json")
        self.assertEqual(str(raised.exception), "semantic_response_json_invalid")
        self.assertNotIn("{not-json", repr(raised.exception))

    def test_timeout_429_and_5xx_retry_with_bounded_exponential_backoff(
        self,
    ) -> None:
        sleeps: list[float] = []
        provider, transport = self.make_provider(
            [
                httpx.ReadTimeout("slow"),
                FakeResponse(429, {"error": {"message": "limit"}}),
                FakeResponse(503, {"error": {"message": "outage"}}),
                FakeResponse(200, provider_payload()),
            ],
            max_attempts=4,
            backoff_seconds=0.25,
            max_backoff_seconds=0.5,
            sleep=sleeps.append,
        )

        result = provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(result.parsed_output, VALID_OUTPUT)
        self.assertEqual(len(transport.calls), 4)
        self.assertEqual(sleeps, [0.25, 0.5, 0.5])

    def test_terminal_client_statuses_do_not_retry(self) -> None:
        for status in (400, 401):
            with self.subTest(status=status):
                provider, transport = self.make_provider(
                    [
                        FakeResponse(status, {"error": {"message": "secret"}}),
                        FakeResponse(200, provider_payload()),
                    ]
                )

                with self.assertRaises(SemanticProviderError) as raised:
                    provider.extract(bundle(), response_schema=VALID_SCHEMA)

                self.assertEqual(
                    raised.exception.code,
                    f"semantic_provider_http_{status}",
                )
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(len(transport.calls), 1)
                self.assertNotIn("secret", str(raised.exception))

    def test_account_overdue_halts_without_immediate_retry(self) -> None:
        provider, transport = self.make_provider(
            [
                FakeResponse(
                    403,
                    {
                        "error": {
                            "code": "AccountOverdueError",
                            "message": "account overdue",
                        }
                    },
                ),
                FakeResponse(200, provider_payload()),
            ]
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_provider_account_overdue",
        )
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("account overdue", str(raised.exception))

    def test_forbidden_halts_batch_without_immediate_retry(self) -> None:
        provider, transport = self.make_provider(
            [
                FakeResponse(
                    403,
                    {"error": {"message": "model entitlement missing"}},
                ),
                FakeResponse(200, provider_payload()),
            ]
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_provider_forbidden",
        )
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(
            "model entitlement missing",
            str(raised.exception),
        )

    def test_payment_required_is_deferred_without_immediate_retry(
        self,
    ) -> None:
        provider, transport = self.make_provider(
            [
                FakeResponse(
                    402,
                    {"error": {"message": "account balance"}},
                ),
                FakeResponse(200, provider_payload()),
            ]
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_provider_payment_required",
        )
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("account balance", str(raised.exception))

    def test_retry_exhaustion_uses_stable_code_and_attempt_bound(self) -> None:
        sleeps: list[float] = []
        provider, transport = self.make_provider(
            [
                FakeResponse(500, {}),
                FakeResponse(502, {}),
                FakeResponse(503, {}),
            ],
            sleep=sleeps.append,
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_provider_server_error",
        )
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [0.25, 0.5])

    def test_missing_credentials_are_unavailable_without_network(self) -> None:
        transport = ScriptedTransport([FakeResponse(200, provider_payload())])
        provider = OpenAICompatibleSemanticProvider(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            api_key_file=self.root / "missing.key",
            system_prompt="fixed",
            transport=transport,
        )

        with self.assertRaises(SemanticProviderUnavailable) as raised:
            provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_provider_unavailable",
        )
        self.assertEqual(transport.calls, [])

    def test_input_character_budget_fails_before_network(self) -> None:
        provider, transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            max_input_characters=16,
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(
                bundle(text="这是一段远远超过十六字符的公告正文"),
                response_schema=VALID_SCHEMA,
            )

        self.assertEqual(raised.exception.code, "semantic_input_too_large")
        self.assertEqual(transport.calls, [])

    def test_daily_document_budget_fails_before_second_network_call(self) -> None:
        provider, transport = self.make_provider(
            [
                FakeResponse(200, provider_payload()),
                FakeResponse(200, provider_payload()),
            ],
            max_documents_per_daily_run=1,
        )
        provider.extract(bundle(), response_schema=VALID_SCHEMA)

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(bundle(text="第二份公告"), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_daily_document_budget_exhausted",
        )
        self.assertEqual(len(transport.calls), 1)

    def test_validation_repair_does_not_consume_another_document_slot(
        self,
    ) -> None:
        provider, transport = self.make_provider(
            [
                FakeResponse(200, provider_payload()),
                FakeResponse(200, provider_payload()),
            ],
            max_documents_per_daily_run=1,
        )

        provider.extract(bundle(), response_schema=VALID_SCHEMA)
        provider.extract(bundle(repair=True), response_schema=VALID_SCHEMA)

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(
                bundle(document_id="doc-2"),
                response_schema=VALID_SCHEMA,
            )
        self.assertEqual(
            raised.exception.code,
            "semantic_daily_document_budget_exhausted",
        )
        self.assertEqual(len(transport.calls), 2)

    def test_daily_token_budget_fails_before_network_call(self) -> None:
        provider, transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            daily_input_token_budget=31,
        )

        with self.assertRaises(SemanticProviderError) as raised:
            provider.extract(
                bundle(token_estimate=32),
                response_schema=VALID_SCHEMA,
            )

        self.assertEqual(
            raised.exception.code,
            "semantic_daily_token_budget_exhausted",
        )
        self.assertEqual(transport.calls, [])

    def test_daily_budget_resets_when_injected_clock_changes_day(self) -> None:
        now = [datetime(2026, 7, 24, 16, tzinfo=timezone.utc)]
        provider, transport = self.make_provider(
            [
                FakeResponse(200, provider_payload()),
                FakeResponse(200, provider_payload()),
            ],
            max_documents_per_daily_run=1,
            clock=lambda: now[0],
        )
        provider.extract(bundle(), response_schema=VALID_SCHEMA)
        now[0] += timedelta(days=1)

        result = provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(result.parsed_output, VALID_OUTPUT)
        self.assertEqual(len(transport.calls), 2)

    def test_persistent_daily_budget_survives_provider_restart(
        self,
    ) -> None:
        state_path = self.root / "semantic-daily-budget.json"
        now = datetime(2026, 7, 24, 16, tzinfo=timezone.utc)
        first, first_transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            max_documents_per_daily_run=1,
            clock=lambda: now,
            budget_state_path=state_path,
        )
        first.extract(bundle(), response_schema=VALID_SCHEMA)
        second, second_transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            max_documents_per_daily_run=1,
            clock=lambda: now,
            budget_state_path=state_path,
        )

        with self.assertRaises(SemanticProviderError) as raised:
            second.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(
            raised.exception.code,
            "semantic_daily_document_budget_exhausted",
        )
        self.assertEqual(len(first_transport.calls), 1)
        self.assertEqual(second_transport.calls, [])

    def test_persistent_budget_allows_validation_repair_after_restart(
        self,
    ) -> None:
        state_path = self.root / "semantic-daily-budget.json"
        now = datetime(2026, 7, 24, 16, tzinfo=timezone.utc)
        first, first_transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            max_documents_per_daily_run=1,
            clock=lambda: now,
            budget_state_path=state_path,
        )
        first.extract(bundle(), response_schema=VALID_SCHEMA)
        repair, repair_transport = self.make_provider(
            [FakeResponse(200, provider_payload())],
            max_documents_per_daily_run=1,
            clock=lambda: now,
            budget_state_path=state_path,
        )

        result = repair.extract(
            bundle(repair=True),
            response_schema=VALID_SCHEMA,
        )

        self.assertEqual(result.parsed_output, VALID_OUTPUT)
        self.assertEqual(len(first_transport.calls), 1)
        self.assertEqual(len(repair_transport.calls), 1)

    def test_usage_is_actual_when_supplied_and_null_when_absent(self) -> None:
        provider, _ = self.make_provider(
            [
                FakeResponse(
                    200,
                    provider_payload(
                        usage={
                            "prompt_tokens": 101,
                            "completion_tokens": 19,
                            "total_tokens": 120,
                        }
                    ),
                ),
                FakeResponse(200, provider_payload()),
            ]
        )

        with_usage = provider.extract(bundle(), response_schema=VALID_SCHEMA)
        without_usage = provider.extract(bundle(), response_schema=VALID_SCHEMA)

        self.assertEqual(with_usage.input_tokens, 101)
        self.assertEqual(with_usage.output_tokens, 19)
        self.assertEqual(with_usage.total_tokens, 120)
        self.assertIsNone(without_usage.input_tokens)
        self.assertIsNone(without_usage.output_tokens)
        self.assertIsNone(without_usage.total_tokens)

    def test_audit_log_contains_only_safe_hash_count_version_and_usage_fields(
        self,
    ) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        provider, _ = self.make_provider(
            [
                FakeResponse(
                    200,
                    provider_payload(
                        usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        }
                    ),
                )
            ],
            audit_sink=lambda event, fields: events.append(
                (event, dict(fields))
            ),
        )
        secret_body = "未公开重大合同原文-绝不能进入日志"

        provider.extract(
            bundle(text=secret_body),
            response_schema=VALID_SCHEMA,
        )

        rendered = json.dumps(events, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(secret_body, rendered)
        self.assertNotIn("secret-provider-key", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertIn("input_hash", rendered)
        self.assertIn("input_character_count", rendered)
        self.assertIn("prompt_version", rendered)
        self.assertIn("taxonomy_version", rendered)
        self.assertIn("input_tokens", rendered)
        allowed = {
            "provider",
            "model",
            "endpoint_host",
            "client_version",
            "document_id_hash",
            "artifact_hash",
            "input_hash",
            "output_hash",
            "input_character_count",
            "document_count",
            "input_token_estimate",
            "prompt_version",
            "schema_version",
            "taxonomy_version",
            "parser_version",
            "attempt",
            "status_code",
            "error_code",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "latency_ms",
        }
        for _, fields in events:
            self.assertLessEqual(set(fields), allowed)

    def test_local_schema_validation_cannot_be_disabled(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^semantic_local_schema_validation_required$",
        ):
            self.make_provider(
                [FakeResponse(200, provider_payload())],
                local_schema_validation=False,
            )

    def test_non_https_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "^semantic_provider_endpoint_must_be_https$",
        ):
            self.make_provider(
                [FakeResponse(200, provider_payload())],
                base_url="http://api.deepseek.com",
            )


if __name__ == "__main__":
    unittest.main()
