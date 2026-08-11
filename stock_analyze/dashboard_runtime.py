"""Read-only, allowlisted systemd snapshots for the Dashboard."""

from __future__ import annotations

import os
import re
import subprocess
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable


RUNTIME_SERVICE_UNITS = (
    "stock-analyze-intelligence.service",
    "stock-analyze-market-data.service",
    "stock-analyze-research.service",
    "stock-analyze-model-iteration.service",
    "stock-analyze-daily-finalize.service",
    "stock-analyze-claude-daily.service",
    "stock-analyze-codex-daily.service",
    "stock-analyze-claude-cn-qdii-etf-daily.service",
    "stock-analyze-codex-cn-qdii-etf-daily.service",
    "stock-analyze-aggregate-dashboard.service",
    "stock-analyze-daily-summary.service",
    "stock-analyze-intelligence-artifact-backfill.service",
    "stock-analyze-intelligence-reconcile.service",
    "stock-analyze-intelligence-semantic.service",
    "stock-analyze-intelligence-quality.service",
    "stock-analyze-ifind-source-audit.service",
    "stock-analyze-weekly-trigger.service",
    "stock-analyze-claude-weekly.service",
    "stock-analyze-codex-weekly.service",
    "stock-analyze-claude-cn-qdii-etf-weekly.service",
    "stock-analyze-codex-cn-qdii-etf-weekly.service",
    "stock-analyze-qdii-research.service",
    "stock-analyze-model-training.service",
    "stock-analyze-monthly-review.service",
    "stock-analyze-weekly-summary.service",
    "stock-analyze-monthly-summary.service",
)

RUNTIME_TIMER_UNITS = (
    "stock-analyze-market-data.timer",
    "stock-analyze-weekly-trigger.timer",
    "stock-analyze-monthly-review.timer",
    "stock-analyze-claude-cn-qdii-etf-weekly.timer",
    "stock-analyze-codex-cn-qdii-etf-weekly.timer",
    "stock-analyze-qdii-research.timer",
    "stock-analyze-model-training.timer",
    "stock-analyze-daily-summary.timer",
    "stock-analyze-weekly-summary.timer",
    "stock-analyze-monthly-summary.timer",
    "stock-analyze-intelligence.timer",
    "stock-analyze-intelligence-reconcile.timer",
    "stock-analyze-intelligence-artifact-backfill.timer",
    "stock-analyze-intelligence-semantic.timer",
    "stock-analyze-intelligence-quality.timer",
    "stock-analyze-ifind-source-audit.timer",
)

SERVICE_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStatus",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
)

TIMER_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
)

_RUNTIME_CACHE: dict[str, Any] = {}
_RUNTIME_CACHE_LOCK = threading.Lock()


def _parse_show(output: str) -> dict[str, dict[str, str]]:
    if not output.strip():
        raise ValueError("systemctl_show_empty")

    parsed: dict[str, dict[str, str]] = {}
    for block in re.split(r"\r?\n[ \t]*\r?\n", output.strip()):
        row: dict[str, str] = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            if not separator or not key or key in row:
                raise ValueError("systemctl_show_malformed")
            row[key] = value
        unit = row.get("Id")
        if not unit or unit in parsed:
            raise ValueError("systemctl_show_invalid_id")
        parsed[unit] = row
    return parsed


def _show(
    units: tuple[str, ...],
    properties: tuple[str, ...],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, dict[str, str]]:
    command = [
        "systemctl",
        "show",
        "--no-pager",
        "--all",
        f"--property={','.join(properties)}",
        *units,
    ]
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "systemctl_show_failed")
    try:
        parsed = _parse_show(result.stdout)
    except ValueError as error:
        raise OSError(str(error)) from error

    expected_units = set(units)
    if set(parsed) != expected_units:
        raise OSError("systemctl_show_unit_mismatch")
    if any(not set(properties).issubset(row) for row in parsed.values()):
        raise OSError("systemctl_show_missing_properties")
    return parsed


def _project_service(row: dict[str, str]) -> dict[str, Any]:
    exit_status = row.get("ExecMainStatus")
    load_state = row.get("LoadState") or "loaded"
    return {
        "loadState": load_state,
        "reason": (
            None
            if load_state == "loaded"
            else f"unit_load_state_{load_state}"
        ),
        "activeState": row.get("ActiveState") or "unknown",
        "subState": row.get("SubState") or "unknown",
        "result": row.get("Result") or "unknown",
        "exitStatus": int(exit_status) if str(exit_status).isdigit() else None,
        "startedAt": row.get("ExecMainStartTimestamp") or None,
        "finishedAt": row.get("ExecMainExitTimestamp") or None,
    }


def _project_timer(row: dict[str, str]) -> dict[str, Any]:
    load_state = row.get("LoadState") or "loaded"
    return {
        "loadState": load_state,
        "reason": (
            None
            if load_state == "loaded"
            else f"unit_load_state_{load_state}"
        ),
        "activeState": row.get("ActiveState") or "unknown",
        "lastTriggerAt": row.get("LastTriggerUSec") or None,
        "nextTriggerAt": row.get("NextElapseUSecRealtime") or None,
    }


def _begin_generation(runtime_cache: dict[str, Any]) -> int:
    with _RUNTIME_CACHE_LOCK:
        generation = int(runtime_cache.get("_next_generation") or 0) + 1
        runtime_cache["_next_generation"] = generation
        return generation


def _last_successful(runtime_cache: dict[str, Any]) -> dict[str, Any]:
    with _RUNTIME_CACHE_LOCK:
        return deepcopy(runtime_cache.get("last_successful") or {})


def _store_successful(
    runtime_cache: dict[str, Any],
    payload: dict[str, Any],
    *,
    generation: int,
) -> None:
    with _RUNTIME_CACHE_LOCK:
        stored_generation = int(
            runtime_cache.get("_last_successful_generation") or 0
        )
        if generation >= stored_generation:
            runtime_cache["last_successful"] = deepcopy(payload)
            runtime_cache["_last_successful_generation"] = generation


def read_dashboard_runtime(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_cache = _RUNTIME_CACHE if cache is None else cache
    generation = _begin_generation(runtime_cache)
    generated_at = datetime.now().isoformat(timespec="seconds")

    try:
        services = _show(
            RUNTIME_SERVICE_UNITS,
            SERVICE_PROPERTIES,
            runner=runner,
        )
        timers = _show(
            RUNTIME_TIMER_UNITS,
            TIMER_PROPERTIES,
            runner=runner,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        previous = _last_successful(runtime_cache)
        return {
            "status": "unavailable",
            "generated_at": generated_at,
            "last_known_at": previous.get("generated_at"),
            "reason": "runtime_status_unavailable",
            "services": previous.get("services") or {},
            "timers": previous.get("timers") or {},
        }

    payload = {
        "status": "available",
        "generated_at": generated_at,
        "last_known_at": generated_at,
        "reason": None,
        "services": {
            unit: _project_service(services[unit])
            for unit in RUNTIME_SERVICE_UNITS
            if unit in services
        },
        "timers": {
            unit: _project_timer(timers[unit])
            for unit in RUNTIME_TIMER_UNITS
            if unit in timers
        },
    }
    _store_successful(runtime_cache, payload, generation=generation)
    return payload
