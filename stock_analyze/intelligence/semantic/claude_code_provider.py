"""Fresh-session Claude Code adapter for provider-neutral semantic jobs."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .provider import (
    SemanticInputBundle,
    SemanticProviderError,
    SemanticProviderIdentity,
    SemanticProviderResponse,
)


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
_QUOTA_MARKERS = (
    "usage limit",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "resets ",
)


class ClaudeCodeSemanticProvider:
    """Run one isolated, tool-free Claude Code process per document."""

    def __init__(
        self,
        *,
        system_prompt: str,
        claude_path: str | Path = "$HOME/.local/bin/claude",
        model: str = "claude-fable-5",
        effort: str = "high",
        timeout_seconds: float = 900,
        cwd: str | Path | None = None,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        prompt = str(system_prompt).strip()
        if not prompt:
            raise ValueError("claude_code_system_prompt_required")
        self._system_prompt = prompt
        self._claude_path = str(Path(claude_path).expanduser())
        self._model = str(model).strip()
        if not self._model.startswith("claude-"):
            raise ValueError("claude_code_model_must_be_claude")
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("claude_code_effort_invalid")
        self._effort = effort
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._cwd = str(Path(cwd).resolve()) if cwd is not None else None
        self._run_command = run_command
        self._identity = SemanticProviderIdentity(
            provider="claude-code",
            model=self._model,
            endpoint_host="local-oauth",
            client_version="claude-code-provider-v1",
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
        validator = self._validator(response_schema)
        user_content = self._user_content(bundle, response_schema)
        # Claude Code's structured-output parser does not register the Draft
        # 2020-12 metaschema URI. Local validation still uses the full schema;
        # only the redundant top-level declaration is removed for the CLI.
        cli_schema = {
            key: value
            for key, value in response_schema.items()
            if key != "$schema"
        }
        schema_json = self._canonical_json(cli_schema)
        command: list[str] = [
            self._claude_path,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--model",
            self._model,
            "--effort",
            self._effort,
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--system-prompt",
            self._system_prompt,
        ]
        started = time.monotonic()
        try:
            completed = self._run_command(
                command,
                input=user_content,
                text=True,
                capture_output=True,
                cwd=self._cwd,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SemanticProviderError(
                "claude_code_timeout",
                retryable=True,
            ) from exc
        combined_error = "\n".join(
            [str(completed.stdout or ""), str(completed.stderr or "")]
        )
        if completed.returncode != 0:
            if self._is_quota_error(combined_error):
                raise SemanticProviderError(
                    "claude_code_quota_limited",
                    retryable=True,
                )
            raise SemanticProviderError(
                "claude_code_process_failed",
                retryable=False,
                raw_output=combined_error[-8_000:],
            )
        try:
            envelope = json.loads(str(completed.stdout or ""))
        except (TypeError, ValueError) as exc:
            raise SemanticProviderError(
                "claude_code_envelope_json_invalid",
                raw_output=str(completed.stdout or "")[-8_000:],
            ) from exc
        if not isinstance(envelope, Mapping):
            raise SemanticProviderError("claude_code_envelope_invalid")
        if bool(envelope.get("is_error")):
            message = self._canonical_json(envelope)
            if self._is_quota_error(message):
                raise SemanticProviderError(
                    "claude_code_quota_limited",
                    retryable=True,
                )
            raise SemanticProviderError(
                "claude_code_result_error",
                raw_output=message[-8_000:],
            )
        model_usage = envelope.get("modelUsage")
        if not isinstance(model_usage, Mapping) or self._model not in model_usage:
            raise SemanticProviderError("claude_code_model_mismatch")
        parsed = envelope.get("structured_output")
        if not isinstance(parsed, dict):
            raise SemanticProviderError("claude_code_structured_output_missing")
        if list(validator.iter_errors(parsed)):
            raise SemanticProviderError(
                "semantic_response_schema_invalid",
                raw_output=self._canonical_json(parsed),
            )
        raw_output = self._canonical_json(parsed)
        usage = envelope.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        input_tokens = self._optional_int(usage_map.get("input_tokens"))
        output_tokens = self._optional_int(usage_map.get("output_tokens"))
        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
        duration_ms = self._optional_int(envelope.get("duration_ms"))
        latency_ms = duration_ms if duration_ms is not None else max(
            0,
            int(round((time.monotonic() - started) * 1_000)),
        )
        return SemanticProviderResponse(
            identity=self._identity,
            parsed_output=parsed,
            raw_output=raw_output,
            input_hash=self._sha256(user_content),
            output_hash=self._sha256(raw_output),
            request_id=self._optional_string(envelope.get("session_id")),
            response_model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _validator(schema: dict[str, object]) -> Draft202012Validator:
        if not isinstance(schema, dict):
            raise SemanticProviderError(
                "semantic_response_schema_definition_invalid"
            )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise SemanticProviderError(
                "semantic_response_schema_definition_invalid"
            ) from exc
        return Draft202012Validator(schema, format_checker=FormatChecker())

    @classmethod
    def _user_content(
        cls,
        bundle: SemanticInputBundle,
        response_schema: dict[str, object],
    ) -> str:
        return cls._canonical_json(
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
                "server_side_schema_guaranteed": True,
                "local_schema_validation_required": True,
            }
        )

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        return None

    @staticmethod
    def _optional_string(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _is_quota_error(value: str) -> bool:
        normalized = str(value).casefold()
        return any(marker in normalized for marker in _QUOTA_MARKERS)


__all__: Sequence[str] = ("ClaudeCodeSemanticProvider",)
