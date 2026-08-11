"""Provider-neutral, fail-closed client for semantic announcement extraction."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


LOGGER = logging.getLogger(__name__)
CLIENT_VERSION = "semantic-provider-v1"


class SemanticProviderError(RuntimeError):
    """A stable, body-safe semantic provider failure."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        raw_output: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.raw_output = raw_output

    def __repr__(self) -> str:
        details = [f"code={self.code!r}", f"retryable={self.retryable!r}"]
        if self.status_code is not None:
            details.append(f"status_code={self.status_code!r}")
        return f"{type(self).__name__}({', '.join(details)})"


class SemanticProviderUnavailable(SemanticProviderError):
    """The provider is intentionally unavailable without usable credentials."""

    def __init__(self) -> None:
        super().__init__("semantic_provider_unavailable")


@dataclass(frozen=True)
class SemanticProviderIdentity:
    """Versioned provider identity without credentials."""

    provider: str
    model: str
    endpoint_host: str
    client_version: str = CLIENT_VERSION


@dataclass(frozen=True)
class SemanticInputBundle:
    """Bounded provider input prepared by the semantic routing layer."""

    document_id: str | int
    artifact_hash: str
    parser_version: str
    prompt_version: str
    schema_version: str
    taxonomy_version: str
    payload: Mapping[str, object] = field(repr=False)
    input_token_estimate: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.document_id, bool)
            or not isinstance(self.document_id, (str, int))
            or not str(self.document_id).strip()
            or (
                isinstance(self.document_id, int)
                and self.document_id < 1
            )
        ):
            raise ValueError("semantic_input_document_id_required")
        for name in (
            "artifact_hash",
            "parser_version",
            "prompt_version",
            "schema_version",
            "taxonomy_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"semantic_input_{name}_required")
        if not isinstance(self.payload, Mapping):
            raise ValueError("semantic_input_payload_invalid")
        if (
            self.input_token_estimate is not None
            and (
                isinstance(self.input_token_estimate, bool)
                or not isinstance(self.input_token_estimate, int)
                or self.input_token_estimate < 0
            )
        ):
            raise ValueError("semantic_input_token_estimate_invalid")


@dataclass(frozen=True)
class SemanticProviderResponse:
    """Locally validated output and actual provider usage, when supplied."""

    identity: SemanticProviderIdentity
    parsed_output: dict[str, object]
    raw_output: str = field(repr=False)
    input_hash: str
    output_hash: str
    request_id: str | None
    response_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int


class SemanticExtractionProvider(Protocol):
    @property
    def identity(self) -> SemanticProviderIdentity: ...

    def extract(
        self,
        bundle: SemanticInputBundle,
        *,
        response_schema: dict[str, object],
    ) -> SemanticProviderResponse: ...


class SemanticHttpTransport(Protocol):
    def post(self, url: str, **kwargs: object) -> object: ...


AuditSink = Callable[[str, Mapping[str, object]], None]


class OpenAICompatibleSemanticProvider:
    """OpenAI-compatible adapter with local schema enforcement.

    A provider's ``json_object`` mode constrains syntax but does not necessarily
    enforce the supplied schema. The schema therefore travels inside the
    bounded request bundle and every successful response is validated locally.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_file: str | Path | None,
        system_prompt: str,
        provider_name: str = "openai-compatible",
        max_output_tokens: int = 8192,
        temperature: float = 0,
        thinking_type: str | None = None,
        service_tier: str | None = None,
        response_format: str = "json_object",
        server_side_json_schema: bool = False,
        local_schema_validation: bool = True,
        request_timeout_seconds: float = 120,
        max_attempts: int = 3,
        schema_repair_attempts: int = 0,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 4.0,
        max_input_characters: int = 180_000,
        max_documents_per_daily_run: int = 500,
        daily_input_token_budget: int = 3_000_000,
        budget_state_path: str | Path | None = None,
        transport: SemanticHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        parsed = urlparse(str(base_url).strip())
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("semantic_provider_endpoint_must_be_https")
        if not local_schema_validation:
            raise ValueError("semantic_local_schema_validation_required")
        normalized_prompt = str(system_prompt).strip()
        if not normalized_prompt:
            raise ValueError("semantic_system_prompt_required")
        if response_format not in {"json_object", "json_schema"}:
            raise ValueError("semantic_response_format_invalid")
        if server_side_json_schema and response_format != "json_schema":
            raise ValueError("semantic_server_schema_format_mismatch")
        normalized_thinking_type = (
            str(thinking_type).strip()
            if thinking_type is not None
            else ""
        )
        if normalized_thinking_type not in {"", "disabled"}:
            raise ValueError(
                "semantic_provider_thinking_type_invalid"
            )
        normalized_service_tier = (
            str(service_tier).strip()
            if service_tier is not None
            else ""
        )
        if normalized_service_tier not in {"", "auto", "default"}:
            raise ValueError(
                "semantic_provider_service_tier_invalid"
            )

        self._base_url = str(base_url).strip().rstrip("/")
        self._chat_url = f"{self._base_url}/chat/completions"
        self._model = str(model).strip()
        self._api_key_file = (
            Path(api_key_file).expanduser()
            if api_key_file is not None and str(api_key_file).strip()
            else None
        )
        self._system_prompt = normalized_prompt
        self._max_output_tokens = max(1, int(max_output_tokens))
        self._temperature = float(temperature)
        self._thinking_type = normalized_thinking_type or None
        self._service_tier = normalized_service_tier or None
        self._response_format = response_format
        self._server_side_json_schema = bool(server_side_json_schema)
        self._timeout_seconds = max(0.1, float(request_timeout_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._schema_repair_attempts = min(
            max(0, int(schema_repair_attempts)),
            2,
        )
        self._backoff_seconds = max(0.0, float(backoff_seconds))
        self._max_backoff_seconds = max(
            self._backoff_seconds,
            float(max_backoff_seconds),
        )
        self._max_input_characters = max(1, int(max_input_characters))
        self._max_documents_per_daily_run = max(
            1,
            int(max_documents_per_daily_run),
        )
        self._daily_input_token_budget = max(
            1,
            int(daily_input_token_budget),
        )
        self._budget_state_path = (
            Path(budget_state_path).expanduser()
            if budget_state_path is not None
            and str(budget_state_path).strip()
            else None
        )
        self._transport = transport or httpx.Client()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._audit_sink = audit_sink or self._default_audit_sink
        self._identity = SemanticProviderIdentity(
            provider=str(provider_name).strip() or "openai-compatible",
            model=self._model,
            endpoint_host=parsed.hostname.casefold(),
        )
        self._budget_lock = threading.Lock()
        self._budget_day: str | None = None
        self._daily_document_count = 0
        self._daily_input_tokens = 0

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, object],
        *,
        profile_name: str,
        system_prompt: str,
        environ: Mapping[str, str] | None = None,
        transport: SemanticHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        audit_sink: AuditSink | None = None,
        budget_state_path: str | Path | None = None,
    ) -> "OpenAICompatibleSemanticProvider":
        environment = os.environ if environ is None else environ
        semantic = cls._mapping(config.get("semantic", config), "semantic")
        provider = cls._mapping(
            semantic.get("provider"),
            "semantic.provider",
        )
        profiles = cls._mapping(
            semantic.get("candidate_profiles"),
            "semantic.candidate_profiles",
        )
        profile = cls._mapping(
            profiles.get(profile_name),
            f"semantic.candidate_profiles.{profile_name}",
        )
        budgets = cls._mapping(
            semantic.get("budgets", {}),
            "semantic.budgets",
        )
        key_file_env = str(provider.get("api_key_file_env", "")).strip()
        model_env = str(profile.get("model_env", "")).strip()
        key_file = environment.get(key_file_env) if key_file_env else None
        literal_model = str(profile.get("model", "")).strip()
        model = (
            environment.get(model_env, "").strip()
            if model_env
            else literal_model
        )
        if not model:
            model = literal_model

        return cls(
            base_url=str(provider.get("base_url", "")),
            model=model,
            api_key_file=key_file,
            system_prompt=system_prompt,
            provider_name=str(provider.get("kind", "openai-compatible")),
            max_output_tokens=int(profile.get("max_output_tokens", 8192)),
            temperature=float(profile.get("temperature", 0)),
            thinking_type=(
                str(profile.get("thinking", "")).strip()
                or None
            ),
            service_tier=(
                str(profile.get("service_tier", "")).strip()
                or None
            ),
            response_format=str(
                provider.get("response_format", "json_object")
            ),
            server_side_json_schema=bool(
                provider.get("server_side_json_schema", False)
            ),
            local_schema_validation=bool(
                provider.get("local_schema_validation", True)
            ),
            request_timeout_seconds=float(
                budgets.get("request_timeout_seconds", 120)
            ),
            max_attempts=int(budgets.get("max_attempts", 3)),
            schema_repair_attempts=int(
                budgets.get("schema_repair_attempts", 0)
            ),
            max_input_characters=int(
                budgets.get("max_input_characters", 180_000)
            ),
            max_documents_per_daily_run=int(
                budgets.get("max_documents_per_daily_run", 500)
            ),
            daily_input_token_budget=int(
                budgets.get("daily_input_token_budget", 3_000_000)
            ),
            budget_state_path=budget_state_path,
            transport=transport,
            clock=clock,
            monotonic_clock=monotonic_clock,
            sleep=sleep,
            audit_sink=audit_sink,
        )

    @classmethod
    def from_executor_config(
        cls,
        config: Mapping[str, object],
        *,
        system_prompt: str,
        environ: Mapping[str, str] | None = None,
        transport: SemanticHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        audit_sink: AuditSink | None = None,
        budget_state_path: str | Path | None = None,
    ) -> "OpenAICompatibleSemanticProvider":
        """Build the production adapter from one flat executor contract."""

        environment = os.environ if environ is None else environ
        executor = cls._mapping(
            config.get("executor", config),
            "executor",
        )
        kind = str(executor.get("kind") or "").strip()
        if kind != "openai-compatible":
            raise ValueError("semantic_executor_kind_unsupported")
        key_env = str(executor.get("api_key_file_env") or "").strip()
        model_env = str(executor.get("model_env") or "").strip()
        model = (
            environment.get(model_env, "").strip()
            if model_env
            else str(executor.get("model") or "").strip()
        )
        return cls(
            base_url=str(executor.get("base_url") or ""),
            model=model,
            api_key_file=environment.get(key_env) if key_env else None,
            system_prompt=system_prompt,
            provider_name=kind,
            max_output_tokens=int(
                executor.get("max_output_tokens", 8192)
            ),
            temperature=float(executor.get("temperature", 0)),
            thinking_type=(
                str(executor.get("thinking") or "").strip() or None
            ),
            service_tier=(
                str(executor.get("service_tier") or "").strip() or None
            ),
            response_format=str(
                executor.get("response_format", "json_object")
            ),
            server_side_json_schema=bool(
                executor.get("server_side_json_schema", False)
            ),
            local_schema_validation=bool(
                executor.get("local_schema_validation", True)
            ),
            request_timeout_seconds=float(
                executor.get("request_timeout_seconds", 120)
            ),
            max_attempts=int(executor.get("max_attempts", 3)),
            schema_repair_attempts=int(
                executor.get("schema_repair_attempts", 0)
            ),
            max_input_characters=int(
                executor.get("max_input_characters", 180_000)
            ),
            max_documents_per_daily_run=int(
                executor.get("max_documents_per_daily_run", 500)
            ),
            daily_input_token_budget=int(
                executor.get("daily_input_token_budget", 3_000_000)
            ),
            budget_state_path=budget_state_path,
            transport=transport,
            clock=clock,
            monotonic_clock=monotonic_clock,
            sleep=sleep,
            audit_sink=audit_sink,
        )

    @property
    def identity(self) -> SemanticProviderIdentity:
        return self._identity

    def extract(
        self,
        bundle: SemanticInputBundle,
        *,
        response_schema: dict[str, object],
    ) -> SemanticProviderResponse:
        api_key = self._read_api_key()
        if not api_key or not self._model:
            raise SemanticProviderUnavailable()
        validator = self._build_validator(response_schema)
        user_content = self._build_user_content(bundle, response_schema)
        input_character_count = len(user_content)
        if input_character_count > self._max_input_characters:
            raise SemanticProviderError("semantic_input_too_large")
        input_token_estimate = (
            bundle.input_token_estimate
            if bundle.input_token_estimate is not None
            else max(1, (input_character_count + 3) // 4)
        )
        repair_context = bundle.payload.get("repair_context")
        is_validation_repair = (
            isinstance(repair_context, Mapping)
            and repair_context.get("attempt") == 1
        )
        self._reserve_daily_budget(
            input_token_estimate,
            document_increment=0 if is_validation_repair else 1,
        )

        input_hash = self._sha256(user_content)
        audit_base = self._audit_base(
            bundle,
            input_hash=input_hash,
            input_character_count=input_character_count,
            input_token_estimate=input_token_estimate,
        )
        self._emit("semantic_provider_request", audit_base)
        request_json = self._request_json(
            user_content,
            response_schema=response_schema,
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error: SemanticProviderError | None = None
        started_at = self._monotonic_clock()
        repair_attempts = 0
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.post(
                    self._chat_url,
                    headers=headers,
                    json=request_json,
                    timeout=self._timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                last_error = SemanticProviderError(
                    "semantic_provider_timeout",
                    retryable=True,
                )
                self._emit_failure(audit_base, last_error, attempt)
                if attempt >= self._max_attempts:
                    raise last_error from exc
                self._sleep_before_retry(attempt)
                continue
            except httpx.TransportError as exc:
                last_error = SemanticProviderError(
                    "semantic_provider_transport_error",
                    retryable=True,
                )
                self._emit_failure(audit_base, last_error, attempt)
                if attempt >= self._max_attempts:
                    raise last_error from exc
                self._sleep_before_retry(attempt)
                continue

            status_code = self._status_code(response)
            if status_code == 402:
                error = SemanticProviderError(
                    "semantic_provider_payment_required",
                    retryable=True,
                    status_code=status_code,
                )
                self._emit_failure(audit_base, error, attempt)
                raise error
            if status_code == 403:
                error = SemanticProviderError(
                    (
                        "semantic_provider_account_overdue"
                        if self._response_reports_account_overdue(
                            response
                        )
                        else "semantic_provider_forbidden"
                    ),
                    retryable=True,
                    status_code=status_code,
                )
                self._emit_failure(audit_base, error, attempt)
                raise error
            if status_code == 429:
                last_error = SemanticProviderError(
                    "semantic_provider_rate_limited",
                    retryable=True,
                    status_code=status_code,
                )
            elif status_code >= 500:
                last_error = SemanticProviderError(
                    "semantic_provider_server_error",
                    retryable=True,
                    status_code=status_code,
                )
            elif status_code < 200 or status_code >= 300:
                error = SemanticProviderError(
                    f"semantic_provider_http_{status_code}",
                    status_code=status_code,
                )
                self._emit_failure(audit_base, error, attempt)
                raise error
            else:
                try:
                    return self._parse_success(
                        response,
                        validator=validator,
                        input_hash=input_hash,
                        audit_base=audit_base,
                        attempt=attempt,
                        started_at=started_at,
                    )
                except SemanticProviderError as exc:
                    if (
                        exc.code
                        not in {
                            "semantic_response_json_invalid",
                            "semantic_response_schema_invalid",
                        }
                        or repair_attempts
                        >= self._schema_repair_attempts
                        or attempt >= self._max_attempts
                    ):
                        raise
                    repair_attempts += 1
                    request_json, repair_characters = (
                        self._repair_request_json(
                            request_json,
                            error=exc,
                            validator=validator,
                        )
                    )
                    self._reserve_additional_daily_tokens(
                        max(
                            1,
                            (repair_characters + 3) // 4,
                        )
                    )
                    self._emit(
                        "semantic_provider_repair",
                        {
                            **audit_base,
                            "attempt": attempt,
                            "error_code": exc.code,
                            "repair_attempt": repair_attempts,
                            "invalid_output_hash": self._sha256(
                                exc.raw_output or ""
                            ),
                        },
                    )
                    continue

            self._emit_failure(audit_base, last_error, attempt)
            if attempt >= self._max_attempts:
                raise last_error
            self._sleep_before_retry(attempt)

        raise last_error or SemanticProviderError(
            "semantic_provider_retry_exhausted",
            retryable=True,
        )

    def _parse_success(
        self,
        response: object,
        *,
        validator: Draft202012Validator,
        input_hash: str,
        audit_base: Mapping[str, object],
        attempt: int,
        started_at: float,
    ) -> SemanticProviderResponse:
        try:
            envelope = response.json()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - provider JSON boundary
            error = SemanticProviderError(
                "semantic_provider_envelope_json_invalid"
            )
            self._emit_failure(audit_base, error, attempt)
            raise error from exc
        if not isinstance(envelope, Mapping):
            raise self._shape_error(audit_base, attempt)
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._shape_error(audit_base, attempt)
        first = choices[0]
        if not isinstance(first, Mapping):
            raise self._shape_error(audit_base, attempt)
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise self._shape_error(audit_base, attempt)
        raw_output = message.get("content")
        if not isinstance(raw_output, str):
            raise self._shape_error(audit_base, attempt)
        try:
            parsed_output = json.loads(raw_output)
        except (TypeError, ValueError) as exc:
            error = SemanticProviderError(
                "semantic_response_json_invalid",
                raw_output=raw_output,
            )
            self._emit_failure(audit_base, error, attempt)
            raise error from exc
        errors = list(validator.iter_errors(parsed_output))
        if errors:
            error = SemanticProviderError(
                "semantic_response_schema_invalid",
                raw_output=raw_output,
            )
            self._emit_failure(audit_base, error, attempt)
            raise error
        if not isinstance(parsed_output, dict):
            error = SemanticProviderError(
                "semantic_response_schema_invalid",
                raw_output=raw_output,
            )
            self._emit_failure(audit_base, error, attempt)
            raise error

        usage = envelope.get("usage")
        usage_mapping = usage if isinstance(usage, Mapping) else {}
        input_tokens = self._usage_integer(
            usage_mapping.get("prompt_tokens")
        )
        output_tokens = self._usage_integer(
            usage_mapping.get("completion_tokens")
        )
        total_tokens = self._usage_integer(
            usage_mapping.get("total_tokens")
        )
        latency_ms = max(
            0,
            int(round((self._monotonic_clock() - started_at) * 1000)),
        )
        output_hash = self._sha256(raw_output)
        success_audit = {
            **audit_base,
            "attempt": attempt,
            "status_code": self._status_code(response),
            "output_hash": output_hash,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "latency_ms": latency_ms,
        }
        self._emit("semantic_provider_response", success_audit)
        return SemanticProviderResponse(
            identity=self._identity,
            parsed_output=parsed_output,
            raw_output=raw_output,
            input_hash=input_hash,
            output_hash=output_hash,
            request_id=self._optional_string(envelope.get("id")),
            response_model=self._optional_string(envelope.get("model")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _request_json(
        self,
        user_content: str,
        *,
        response_schema: dict[str, object],
    ) -> dict[str, object]:
        if self._server_side_json_schema:
            response_format: dict[str, object] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "announcement_events",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        request: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": response_format,
        }
        if self._thinking_type is not None:
            request["thinking"] = {
                "type": self._thinking_type,
            }
        if self._service_tier is not None:
            request["service_tier"] = self._service_tier
        return request

    def _repair_request_json(
        self,
        request_json: Mapping[str, object],
        *,
        error: SemanticProviderError,
        validator: Draft202012Validator,
    ) -> tuple[dict[str, object], int]:
        raw_output = error.raw_output or ""
        validation_paths: list[str] = []
        if error.code == "semantic_response_schema_invalid":
            try:
                parsed = json.loads(raw_output)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                validation_paths = sorted(
                    {
                        "/".join(
                            map(str, failure.absolute_path)
                        )
                        or "$"
                        for failure in validator.iter_errors(
                            parsed
                        )
                    }
                )
        repair_contract = json.dumps(
            {
                "task": "repair_previous_response",
                "validation_error": error.code,
                "validation_paths": validation_paths[:50],
                "rules": [
                    "preserve_facts_and_evidence",
                    "change_structure_only",
                    "return_json_only",
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        messages = request_json.get("messages")
        if not isinstance(messages, list):
            raise SemanticProviderError(
                "semantic_provider_request_shape_invalid"
            )
        repaired = dict(request_json)
        repaired["messages"] = [
            *messages,
            {"role": "assistant", "content": raw_output},
            {"role": "user", "content": repair_contract},
        ]
        character_count = sum(
            len(str(message.get("content") or ""))
            for message in repaired["messages"]
            if isinstance(message, Mapping)
        )
        return repaired, character_count

    def _build_user_content(
        self,
        bundle: SemanticInputBundle,
        response_schema: dict[str, object],
    ) -> str:
        return json.dumps(
            {
                "document": {
                    "document_id": bundle.document_id,
                    "artifact_hash": bundle.artifact_hash,
                    "parser_version": bundle.parser_version,
                    "prompt_version": bundle.prompt_version,
                    "schema_version": bundle.schema_version,
                    "taxonomy_version": bundle.taxonomy_version,
                    "payload": bundle.payload,
                },
                "response_schema": response_schema,
                "server_side_schema_guaranteed": (
                    self._server_side_json_schema
                ),
                "local_schema_validation_required": True,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _reserve_daily_budget(
        self,
        input_token_estimate: int,
        *,
        document_increment: int = 1,
    ) -> None:
        if document_increment not in {0, 1}:
            raise ValueError("semantic_document_increment_invalid")
        now = self._clock()
        if not isinstance(now, datetime):
            raise ValueError("semantic_clock_must_return_datetime")
        day = now.astimezone(timezone.utc).date().isoformat()
        if self._budget_state_path is not None:
            self._reserve_persistent_daily_budget(
                day=day,
                input_token_estimate=input_token_estimate,
                now=now,
                document_increment=document_increment,
            )
            return
        with self._budget_lock:
            if self._budget_day != day:
                self._budget_day = day
                self._daily_document_count = 0
                self._daily_input_tokens = 0
            if (
                self._daily_document_count + document_increment
                > self._max_documents_per_daily_run
            ):
                raise SemanticProviderError(
                    "semantic_daily_document_budget_exhausted"
                )
            if (
                self._daily_input_tokens + input_token_estimate
                > self._daily_input_token_budget
            ):
                raise SemanticProviderError(
                    "semantic_daily_token_budget_exhausted"
                )
            self._daily_document_count += document_increment
            self._daily_input_tokens += input_token_estimate

    def _reserve_additional_daily_tokens(
        self,
        input_token_estimate: int,
    ) -> None:
        now = self._clock()
        if not isinstance(now, datetime):
            raise ValueError("semantic_clock_must_return_datetime")
        day = now.astimezone(timezone.utc).date().isoformat()
        if self._budget_state_path is not None:
            self._reserve_persistent_daily_budget(
                day=day,
                input_token_estimate=input_token_estimate,
                now=now,
                document_increment=0,
            )
            return
        with self._budget_lock:
            if self._budget_day != day:
                self._budget_day = day
                self._daily_document_count = 0
                self._daily_input_tokens = 0
            if (
                self._daily_input_tokens + input_token_estimate
                > self._daily_input_token_budget
            ):
                raise SemanticProviderError(
                    "semantic_daily_token_budget_exhausted"
                )
            self._daily_input_tokens += input_token_estimate

    def _reserve_persistent_daily_budget(
        self,
        *,
        day: str,
        input_token_estimate: int,
        now: datetime,
        document_increment: int = 1,
    ) -> None:
        state_path = self._budget_state_path
        assert state_path is not None
        state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = state_path.with_suffix(
            state_path.suffix + ".lock"
        )
        identity_key = hashlib.sha256(
            (
                f"{self._identity.provider}|"
                f"{self._identity.model}|"
                f"{self._identity.endpoint_host}|"
                f"{self._identity.client_version}"
            ).encode("utf-8")
        ).hexdigest()
        with self._budget_lock:
            with lock_path.open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                if state_path.exists():
                    try:
                        ledger = json.loads(
                            state_path.read_text(
                                encoding="utf-8"
                            )
                        )
                    except (
                        OSError,
                        UnicodeError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise SemanticProviderError(
                            "semantic_daily_budget_state_invalid"
                        ) from exc
                else:
                    ledger = {
                        "schema_version": 1,
                        "days": {},
                    }
                if (
                    not isinstance(ledger, dict)
                    or ledger.get("schema_version") != 1
                    or not isinstance(ledger.get("days"), dict)
                ):
                    raise SemanticProviderError(
                        "semantic_daily_budget_state_invalid"
                    )
                days = dict(ledger["days"])
                raw_day = days.get(day, {})
                if not isinstance(raw_day, dict):
                    raise SemanticProviderError(
                        "semantic_daily_budget_state_invalid"
                    )
                day_usage = dict(raw_day)
                raw_usage = day_usage.get(identity_key, {})
                if not isinstance(raw_usage, dict):
                    raise SemanticProviderError(
                        "semantic_daily_budget_state_invalid"
                    )
                try:
                    documents = int(
                        raw_usage.get("documents", 0)
                    )
                    tokens = int(
                        raw_usage.get("input_tokens", 0)
                    )
                except (TypeError, ValueError) as exc:
                    raise SemanticProviderError(
                        "semantic_daily_budget_state_invalid"
                    ) from exc
                if documents < 0 or tokens < 0:
                    raise SemanticProviderError(
                        "semantic_daily_budget_state_invalid"
                    )
                if (
                    documents + document_increment
                    > self._max_documents_per_daily_run
                ):
                    raise SemanticProviderError(
                        "semantic_daily_document_budget_exhausted"
                    )
                if (
                    tokens + input_token_estimate
                    > self._daily_input_token_budget
                ):
                    raise SemanticProviderError(
                        "semantic_daily_token_budget_exhausted"
                    )
                day_usage[identity_key] = {
                    "provider": self._identity.provider,
                    "model": self._identity.model,
                    "documents": (
                        documents + document_increment
                    ),
                    "input_tokens": (
                        tokens + input_token_estimate
                    ),
                    "updated_at": now.astimezone(
                        timezone.utc
                    ).isoformat(),
                }
                days[day] = day_usage
                # Keep the operational ledger bounded while preserving
                # enough history for weekly diagnostics.
                recent_days = {
                    key: days[key]
                    for key in sorted(days)[-35:]
                }
                self._atomic_write_budget_state(
                    state_path,
                    {
                        "schema_version": 1,
                        "days": recent_days,
                    },
                )

    @staticmethod
    def _atomic_write_budget_state(
        path: Path,
        payload: Mapping[str, object],
    ) -> None:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_api_key(self) -> str | None:
        path = self._api_key_file
        if path is None:
            return None
        try:
            if not path.is_file():
                return None
            if path.stat().st_size > 65_536:
                return None
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        return value or None

    def _build_validator(
        self,
        response_schema: dict[str, object],
    ) -> Draft202012Validator:
        if not isinstance(response_schema, dict):
            raise SemanticProviderError(
                "semantic_response_schema_definition_invalid"
            )
        try:
            Draft202012Validator.check_schema(response_schema)
        except SchemaError as exc:
            raise SemanticProviderError(
                "semantic_response_schema_definition_invalid"
            ) from exc
        return Draft202012Validator(
            response_schema,
            format_checker=FormatChecker(),
        )

    def _audit_base(
        self,
        bundle: SemanticInputBundle,
        *,
        input_hash: str,
        input_character_count: int,
        input_token_estimate: int,
    ) -> dict[str, object]:
        return {
            "provider": self._identity.provider,
            "model": self._identity.model,
            "endpoint_host": self._identity.endpoint_host,
            "client_version": self._identity.client_version,
            "document_id_hash": self._sha256(str(bundle.document_id)),
            "artifact_hash": bundle.artifact_hash,
            "input_hash": input_hash,
            "input_character_count": input_character_count,
            "document_count": 1,
            "input_token_estimate": input_token_estimate,
            "prompt_version": bundle.prompt_version,
            "schema_version": bundle.schema_version,
            "taxonomy_version": bundle.taxonomy_version,
            "parser_version": bundle.parser_version,
        }

    def _emit_failure(
        self,
        audit_base: Mapping[str, object],
        error: SemanticProviderError,
        attempt: int,
    ) -> None:
        fields = {
            **audit_base,
            "attempt": attempt,
            "error_code": error.code,
        }
        if error.status_code is not None:
            fields["status_code"] = error.status_code
        self._emit("semantic_provider_failure", fields)

    def _emit(
        self,
        event: str,
        fields: Mapping[str, object],
    ) -> None:
        try:
            self._audit_sink(event, dict(fields))
        except Exception:  # noqa: BLE001 - telemetry cannot break extraction
            LOGGER.exception("semantic_audit_sink_failed")

    @staticmethod
    def _default_audit_sink(
        event: str,
        fields: Mapping[str, object],
    ) -> None:
        LOGGER.info(
            "%s %s",
            event,
            json.dumps(
                fields,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _sleep_before_retry(self, failed_attempt: int) -> None:
        delay = min(
            self._backoff_seconds * (2 ** (failed_attempt - 1)),
            self._max_backoff_seconds,
        )
        self._sleep(delay)

    def _shape_error(
        self,
        audit_base: Mapping[str, object],
        attempt: int,
    ) -> SemanticProviderError:
        error = SemanticProviderError(
            "semantic_provider_response_shape_invalid"
        )
        self._emit_failure(audit_base, error, attempt)
        return error

    @staticmethod
    def _status_code(response: object) -> int:
        try:
            return int(getattr(response, "status_code"))
        except (TypeError, ValueError) as exc:
            raise SemanticProviderError(
                "semantic_provider_status_invalid"
            ) from exc

    @staticmethod
    def _response_reports_account_overdue(response: object) -> bool:
        try:
            envelope = response.json()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - optional error metadata
            return False
        if not isinstance(envelope, Mapping):
            return False
        error = envelope.get("error")
        if not isinstance(error, Mapping):
            return False
        code = str(error.get("code") or "").strip().casefold()
        message = str(error.get("message") or "").strip().casefold()
        return (
            "accountoverdue" in code
            or "account overdue" in message
            or "overdue balance" in message
        )

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label}_invalid")
        return value

    @staticmethod
    def _usage_integer(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
