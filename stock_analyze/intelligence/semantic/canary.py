"""Comparable, non-importing canary execution for frozen semantic tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ...utils import write_text_atomic
from .exchange import run_job


class SemanticCanaryError(ValueError):
    pass


@dataclass(frozen=True)
class CanaryExecution:
    label: str
    job_path: str | Path
    provider: object | None = None
    executor_config: str | Path | None = None

    def __post_init__(self) -> None:
        if not str(self.label).strip():
            raise SemanticCanaryError("semantic_canary_label_required")
        if (self.provider is None) == (self.executor_config is None):
            raise SemanticCanaryError(
                "semantic_canary_executor_choice_invalid"
            )


def run_provider_canary(
    repo_root: str | Path,
    *,
    executions: Sequence[CanaryExecution],
    report_path: str | Path,
    runner: Callable[..., Mapping[str, object]] = run_job,
) -> dict[str, object]:
    """Run equivalent task sets without importing or changing qualification."""

    root = Path(repo_root).resolve()
    specs = tuple(executions)
    if not specs:
        raise SemanticCanaryError("semantic_canary_execution_required")
    labels = [str(spec.label).strip() for spec in specs]
    if len(set(labels)) != len(labels):
        raise SemanticCanaryError("semantic_canary_label_duplicate")

    prepared: list[tuple[CanaryExecution, Path, dict[str, object]]] = []
    task_sets: list[tuple[str, ...]] = []
    contract_hashes: set[str] = set()
    job_paths: set[Path] = set()
    for spec in specs:
        job_dir = Path(spec.job_path).resolve()
        if job_dir in job_paths:
            raise SemanticCanaryError("semantic_canary_job_reused")
        job_paths.add(job_dir)
        manifest = _read_mapping(job_dir / "job.json")
        if manifest.get("execution_contract_version") != "semantic-execution-v1":
            raise SemanticCanaryError("semantic_canary_contract_invalid")
        items = manifest.get("items")
        if not isinstance(items, list):
            raise SemanticCanaryError("semantic_canary_items_invalid")
        task_ids = tuple(
            sorted(
                str(item.get("semantic_task_id") or "")
                for item in items
                if isinstance(item, Mapping)
            )
        )
        if len(task_ids) != len(items) or any(not value for value in task_ids):
            raise SemanticCanaryError("semantic_canary_task_identity_invalid")
        task_sets.append(task_ids)
        contract_hashes.add(str(manifest.get("semantic_contract_hash") or ""))
        prepared.append((spec, job_dir, manifest))
    if len(set(task_sets)) != 1:
        raise SemanticCanaryError("semantic_canary_task_set_mismatch")
    if len(contract_hashes) != 1 or "" in contract_hashes:
        raise SemanticCanaryError("semantic_canary_contract_mismatch")

    execution_reports: list[dict[str, object]] = []
    for spec, job_dir, manifest in prepared:
        kwargs: dict[str, object]
        if spec.provider is not None:
            kwargs = {"provider": spec.provider}
        else:
            kwargs = {"executor_config": spec.executor_config}
        run_report = dict(runner(root, job_dir, **kwargs))
        outputs = _read_jsonl_if_present(job_dir / "output.jsonl")
        quarantined = _read_jsonl_if_present(job_dir / "quarantine.jsonl")
        errors = run_report.get("errors")
        error_rows = (
            [dict(value) for value in errors if isinstance(value, Mapping)]
            if isinstance(errors, list)
            else []
        )
        output_tasks = {
            str(row.get("semantic_task_id") or "")
            for row in outputs
        }
        expected_tasks = set(task_sets[0])
        if outputs and not output_tasks.issubset(expected_tasks):
            raise SemanticCanaryError("semantic_canary_output_task_mismatch")
        binding = manifest.get("executor_binding")
        if not isinstance(binding, Mapping):
            raise SemanticCanaryError("semantic_canary_binding_invalid")
        execution_reports.append(
            {
                "label": str(spec.label),
                "job_id": str(manifest.get("job_id") or ""),
                "binding_id": str(manifest.get("binding_id") or ""),
                "executor": dict(binding),
                "status": str(run_report.get("status") or "unknown"),
                "schema_valid": len(outputs),
                "grounding_valid": len(outputs),
                "accepted": len(outputs),
                "quarantined": len(quarantined),
                "failed": int(run_report.get("failed") or 0),
                "severe_errors": sum(
                    bool(row.get("terminal")) or not bool(row.get("retryable"))
                    for row in error_rows
                ),
                "errors": error_rows,
                "usage": dict(run_report.get("usage") or {}),
                "task_ids": sorted(output_tasks),
            }
        )

    tasks = task_sets[0]
    result: dict[str, object] = {
        "status": (
            "complete"
            if all(row["status"] == "complete" for row in execution_reports)
            else "partial"
        ),
        "canary_contract_version": "semantic-provider-canary-v1",
        "semantic_contract_hash": next(iter(contract_hashes)),
        "task_set_hash": _hash(list(tasks)),
        "task_count": len(tasks),
        "executions": execution_reports,
        "production_approved": False,
        "imported": False,
        "qualification_changed": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        target,
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def _read_mapping(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SemanticCanaryError("semantic_canary_job_unreadable") from exc
    if not isinstance(value, Mapping):
        raise SemanticCanaryError("semantic_canary_job_invalid")
    return {str(key): item for key, item in value.items()}


def _read_jsonl_if_present(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SemanticCanaryError("semantic_canary_output_unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise SemanticCanaryError("semantic_canary_output_invalid") from exc
        if not isinstance(value, Mapping):
            raise SemanticCanaryError("semantic_canary_output_invalid")
        rows.append({str(key): item for key, item in value.items()})
    return rows


def _hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CanaryExecution",
    "SemanticCanaryError",
    "run_provider_canary",
]
