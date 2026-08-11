"""Immutable identity contract for provider-neutral semantic execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


EXECUTION_CONTRACT_VERSION = "semantic-execution-v1"
EXECUTOR_MODES = frozenset({"api", "coding_plan"})


class SemanticExecutionContractError(ValueError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(self.code)


@dataclass(frozen=True)
class ExecutorBinding:
    executor_mode: str
    provider: str
    model: str
    client_version: str

    def __post_init__(self) -> None:
        values = {
            "executor_mode": str(self.executor_mode).strip(),
            "provider": str(self.provider).strip(),
            "model": str(self.model).strip(),
            "client_version": str(self.client_version).strip(),
        }
        if values["executor_mode"] not in EXECUTOR_MODES:
            raise SemanticExecutionContractError("semantic_executor_mode_invalid")
        if any(not value for value in values.values()):
            raise SemanticExecutionContractError("semantic_executor_binding_incomplete")
        object.__setattr__(self, "executor_mode", values["executor_mode"])
        object.__setattr__(self, "provider", values["provider"])
        object.__setattr__(self, "model", values["model"])
        object.__setattr__(self, "client_version", values["client_version"])

    @property
    def binding_id(self) -> str:
        return "seb-" + _hash(self.to_mapping())[:24]

    def to_mapping(self) -> dict[str, str]:
        return {
            "contract_version": EXECUTION_CONTRACT_VERSION,
            "executor_mode": self.executor_mode,
            "provider": self.provider,
            "model": self.model,
            "client_version": self.client_version,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ExecutorBinding":
        return cls(
            executor_mode=str(value.get("executor_mode") or ""),
            provider=str(value.get("provider") or ""),
            model=str(value.get("model") or ""),
            client_version=str(value.get("client_version") or ""),
        )


def semantic_task_id(
    *,
    profile_hash: str,
    document_id: int,
    artifact_hash: str,
    input_hash: str,
) -> str:
    document = int(document_id)
    if document <= 0:
        raise SemanticExecutionContractError("semantic_task_document_id_invalid")
    identity = {
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "profile_hash": _required_hash(profile_hash, "profile_hash"),
        "document_id": document,
        "artifact_hash": _required_hash(artifact_hash, "artifact_hash"),
        "input_hash": _required_hash(input_hash, "input_hash"),
    }
    return "st-" + _hash(identity)[:24]


def execution_job_id(task_id: str, binding: ExecutorBinding) -> str:
    normalized_task = str(task_id).strip()
    if not normalized_task.startswith("st-"):
        raise SemanticExecutionContractError("semantic_task_id_invalid")
    return "sej-" + _hash(
        {
            "contract_version": EXECUTION_CONTRACT_VERSION,
            "semantic_task_id": normalized_task,
            "binding_id": binding.binding_id,
        }
    )[:24]


def verify_executor_identity(
    binding: ExecutorBinding,
    identity: Mapping[str, object] | object,
) -> None:
    if isinstance(identity, Mapping):
        actual = {
            "provider": str(identity.get("provider") or ""),
            "model": str(identity.get("model") or ""),
            "client_version": str(identity.get("client_version") or ""),
        }
    else:
        actual = {
            "provider": str(getattr(identity, "provider", "")),
            "model": str(getattr(identity, "model", "")),
            "client_version": str(getattr(identity, "client_version", "")),
        }
    expected = {
        "provider": binding.provider,
        "model": binding.model,
        "client_version": binding.client_version,
    }
    if actual != expected:
        raise SemanticExecutionContractError(
            "semantic_executor_identity_mismatch",
            detail=f"expected={_canonical(expected)} actual={_canonical(actual)}",
        )


def _required_hash(value: str, field: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise SemanticExecutionContractError(f"semantic_{field}_invalid")
    return normalized


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = (
    "EXECUTION_CONTRACT_VERSION",
    "EXECUTOR_MODES",
    "ExecutorBinding",
    "SemanticExecutionContractError",
    "execution_job_id",
    "semantic_task_id",
    "verify_executor_identity",
)
