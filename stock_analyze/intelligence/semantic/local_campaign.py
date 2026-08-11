"""Recoverable local Claude Code campaign for one frozen semantic job."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ...utils import write_text_atomic
from .claude_code_provider import ClaudeCodeSemanticProvider
from .exchange import run_job


class RunJob(Protocol):
    def __call__(self, repo_root, job_path, *, provider): ...


def run_campaign(
    repo_root: str | Path,
    job_path: str | Path,
    *,
    provider: object,
    duration_seconds: float,
    quota_wait_seconds: float = 900,
    run_job_fn: RunJob = run_job,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    job_dir = Path(job_path).resolve()
    deadline_duration = max(1.0, float(duration_seconds))
    wait_seconds = max(1.0, float(quota_wait_seconds))
    started = monotonic_clock()
    deadline = started + deadline_duration
    attempts = 0
    quota_waits = 0
    attempt_reports: list[dict[str, object]] = []
    final_report: dict[str, object] = {}
    status = "running"
    state_path = job_dir / "campaign_state.json"
    stop_path = job_dir / "STOP"

    while monotonic_clock() < deadline:
        if stop_path.exists():
            status = "stopped"
            break
        attempts += 1
        report = dict(run_job_fn(root, job_dir, provider=provider))
        final_report = report
        attempt_reports.append(_bounded_attempt_report(report))
        if str(report.get("status")) == "complete":
            status = "awaiting_human_audit"
            break
        errors = _errors(report)
        if errors and all(_is_quota_error(error) for error in errors):
            quota_waits += 1
            status = "waiting_for_quota"
            _write_state(
                state_path,
                _state(
                    status=status,
                    attempts=attempts,
                    quota_waits=quota_waits,
                    report=report,
                    attempt_reports=attempt_reports,
                ),
            )
            remaining = max(0.0, deadline - monotonic_clock())
            if remaining <= 0:
                break
            sleep(min(wait_seconds, remaining))
            continue
        status = "quality_gate_failed"
        break
    else:
        status = "deadline_reached"

    quality_gate = _quality_gate(final_report)
    if status == "awaiting_human_audit" and not quality_gate["passed"]:
        status = "quality_gate_failed"
    result = _state(
        status=status,
        attempts=attempts,
        quota_waits=quota_waits,
        report=final_report,
        attempt_reports=attempt_reports,
        quality_gate=quality_gate,
    )
    _write_state(state_path, result)
    return result


def _quality_gate(report: Mapping[str, object]) -> dict[str, object]:
    compilation = report.get("mention_compilation")
    compile_map = compilation if isinstance(compilation, Mapping) else {}
    expected = _non_negative_int(report.get("expected"))
    failed = _non_negative_int(report.get("failed"))
    rejected = _non_negative_int(compile_map.get("rejected"))
    dropped = _non_negative_int(compile_map.get("dropped_items"))
    checks = {
        "job_complete": str(report.get("status")) == "complete",
        "batch_at_most_10": expected is not None and expected <= 10,
        "no_execution_failures": failed == 0,
        "no_compiler_rejections": rejected == 0,
        "no_compiler_drops": dropped == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "human_semantic_audit_required": True,
        "automatic_import_allowed": False,
    }


def _state(
    *,
    status: str,
    attempts: int,
    quota_waits: int,
    report: Mapping[str, object],
    attempt_reports: list[dict[str, object]],
    quality_gate: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "contract_version": "semantic-local-campaign-v1",
        "status": status,
        "attempts": attempts,
        "quota_waits": quota_waits,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "latest_run": dict(report),
        "attempt_reports": attempt_reports,
        "quality_gate": dict(quality_gate or {}),
    }


def _bounded_attempt_report(report: Mapping[str, object]) -> dict[str, object]:
    return {
        key: report.get(key)
        for key in (
            "status",
            "expected",
            "completed",
            "reused",
            "failed",
            "mention_compilation",
            "usage",
            "errors",
            "started_at",
            "finished_at",
        )
    }


def _errors(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = report.get("errors")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _is_quota_error(error: Mapping[str, object]) -> bool:
    return (
        str(error.get("error")) == "claude_code_quota_limited"
        and error.get("retryable") is True
    )


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _write_state(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one quality-gated local Claude semantic job."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--job", required=True)
    parser.add_argument("--hours", type=float, default=10.0)
    parser.add_argument("--quota-wait-seconds", type=float, default=900.0)
    parser.add_argument("--model", default="claude-fable-5")
    parser.add_argument("--effort", default="high")
    parser.add_argument(
        "--claude-path",
        default="/Users/bytedance/.local/bin/claude",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(args.repo_root).resolve()
    job_dir = Path(args.job).resolve()
    prompt_path = job_dir / "prompt.md"
    if not prompt_path.is_file():
        raise SystemExit("semantic_job_prompt_missing")
    provider = ClaudeCodeSemanticProvider(
        system_prompt=prompt_path.read_text(encoding="utf-8"),
        claude_path=args.claude_path,
        model=args.model,
        effort=args.effort,
        cwd=root,
    )
    result = run_campaign(
        root,
        job_dir,
        provider=provider,
        duration_seconds=max(1.0, args.hours * 3_600),
        quota_wait_seconds=args.quota_wait_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "awaiting_human_audit" else 1


if __name__ == "__main__":
    raise SystemExit(main())
