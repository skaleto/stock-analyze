"""Read-only, allowlisted systemd snapshots for the Dashboard."""

from __future__ import annotations

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
    "stock-analyze-claude-daily.service",
    "stock-analyze-codex-daily.service",
    "stock-analyze-claude-cn-qdii-etf-daily.service",
    "stock-analyze-codex-cn-qdii-etf-daily.service",
    "stock-analyze-aggregate-dashboard.service",
    "stock-analyze-daily-summary.service",
    "stock-analyze-intelligence-artifact-backfill.service",
    "stock-analyze-intelligence-reconcile.service",
    "stock-analyze-intelligence-semantic.service",
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
    "stock-analyze-ifind-source-audit.timer",
)

SERVICE_PROPERTIES = (
    "Id",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStatus",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
)

TIMER_PROPERTIES = (
    "Id",
    "ActiveState",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
)

_RUNTIME_CACHE: dict[str, Any] = {}
_RUNTIME_CACHE_LOCK = threading.Lock()


def _parse_show(output: str) -> dict[str, dict[str, str]]:
    parsed: dict[str, dict[str, str]] = {}
    for block in output.strip().split("\n\n"):
        row: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                row[key] = value
        unit = row.get("Id")
        if unit:
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
        f"--property={','.join(properties)}",
        *units,
    ]
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "systemctl_show_failed")
    return _parse_show(result.stdout)


def _project_service(row: dict[str, str]) -> dict[str, Any]:
    exit_status = row.get("ExecMainStatus")
    return {
        "activeState": row.get("ActiveState") or "unknown",
        "subState": row.get("SubState") or "unknown",
        "result": row.get("Result") or "unknown",
        "exitStatus": int(exit_status) if str(exit_status).isdigit() else None,
        "startedAt": row.get("ExecMainStartTimestamp") or None,
        "finishedAt": row.get("ExecMainExitTimestamp") or None,
    }


def _project_timer(row: dict[str, str]) -> dict[str, Any]:
    return {
        "activeState": row.get("ActiveState") or "unknown",
        "lastTriggerAt": row.get("LastTriggerUSec") or None,
        "nextTriggerAt": row.get("NextElapseUSecRealtime") or None,
    }


def _last_successful(
    runtime_cache: dict[str, Any],
    *,
    use_lock: bool,
) -> dict[str, Any]:
    if use_lock:
        with _RUNTIME_CACHE_LOCK:
            return deepcopy(runtime_cache.get("last_successful") or {})
    return deepcopy(runtime_cache.get("last_successful") or {})


def _store_successful(
    runtime_cache: dict[str, Any],
    payload: dict[str, Any],
    *,
    use_lock: bool,
) -> None:
    if use_lock:
        with _RUNTIME_CACHE_LOCK:
            runtime_cache["last_successful"] = deepcopy(payload)
        return
    runtime_cache["last_successful"] = deepcopy(payload)


def read_dashboard_runtime(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    runtime_cache = _RUNTIME_CACHE if cache is None else cache
    use_lock = cache is None

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
        previous = _last_successful(runtime_cache, use_lock=use_lock)
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
    _store_successful(runtime_cache, payload, use_lock=use_lock)
    return payload
