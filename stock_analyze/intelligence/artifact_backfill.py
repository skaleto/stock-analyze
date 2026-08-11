"""Adaptive, resource-guarded announcement PDF and parse backfill."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

import yaml

from ..utils import write_text_atomic


MIB = 1024 * 1024
GIB = 1024 * MIB
DEFERRED_EXIT = 75


@dataclass(frozen=True)
class Phase:
    download_limit: int
    parse_batches: int
    parse_batch_size: int


@dataclass(frozen=True)
class RuntimeHealth:
    memory_available_mib: float
    swap_used_mib: float
    load_1m: float
    disk_free_gib: float
    reconcile_active: bool
    semantic_active: bool
    critical_window: bool = False
    formal_pipeline_active: bool = False


PHASES = {
    "a": Phase(download_limit=180, parse_batches=75, parse_batch_size=1),
    "b": Phase(download_limit=240, parse_batches=100, parse_batch_size=1),
}
PARSE_DOCUMENT_TIMEOUT_SECONDS = 120


def guard_reason(health: RuntimeHealth) -> str | None:
    if health.critical_window:
        return "daily_critical_window"
    if health.formal_pipeline_active:
        return "formal_pipeline_active"
    if health.reconcile_active:
        return "reconcile_active"
    if health.semantic_active:
        return "semantic_active"
    if health.memory_available_mib < 512:
        return "memory_available_low"
    if health.disk_free_gib < 5:
        return "disk_free_low"
    if health.load_1m > 2.5:
        return "load_high"
    return None


def choose_phase(
    state: dict,
    *,
    now: datetime | None = None,
) -> str:
    current = str(state.get("phase") or "a")
    if current == "b" and int(state.get("consecutive_breaches") or 0) < 2:
        return "b"
    timestamp = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(
            str(state.get("phase_started_at") or "")
        )
    except ValueError:
        return "a"
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if timestamp - started < timedelta(hours=24):
        return "a"
    history = [
        item
        for item in list(state.get("history") or ())
        if item.get("status") == "success"
    ][-20:]
    if len(history) < 20:
        return "a"
    durations = sorted(float(item["duration_seconds"]) for item in history)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
    peak_rss = max(float(item.get("peak_rss_mib") or 0.0) for item in history)
    if p95 >= 1_080 or peak_rss >= 900:
        return "a"
    return "b"


def _in_daily_critical_window(now: datetime) -> bool:
    shanghai = now.astimezone(ZoneInfo("Asia/Shanghai"))
    minutes = shanghai.hour * 60 + shanghai.minute
    return shanghai.weekday() < 5 and (17 * 60 + 45) <= minutes < (21 * 60 + 30)


def current_health(
    repo_root: Path,
    *,
    now: datetime | None = None,
) -> RuntimeHealth:
    memory = _meminfo()
    total_swap = float(memory.get("SwapTotal", 0)) / 1024
    free_swap = float(memory.get("SwapFree", 0)) / 1024
    disk = shutil.disk_usage(repo_root)

    def service_active(unit: str) -> bool:
        return (
            subprocess.run(
                [
                    "systemctl",
                    "is-active",
                    "--quiet",
                    unit,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
            if shutil.which("systemctl")
            else False
        )

    reconcile_active = service_active(
        "stock-analyze-intelligence-reconcile.service"
    )
    semantic_active = service_active(
        "stock-analyze-intelligence-semantic.service"
    )
    formal_pipeline_active = any(
        service_active(unit)
        for unit in (
            "stock-analyze-market-data.service",
            "stock-analyze-research.service",
            "stock-analyze-model-iteration.service",
            "stock-analyze-claude-daily.service",
            "stock-analyze-codex-daily.service",
            "stock-analyze-claude-cn-qdii-etf-daily.service",
            "stock-analyze-codex-cn-qdii-etf-daily.service",
        )
    )
    timestamp = now or datetime.now(timezone.utc)
    return RuntimeHealth(
        memory_available_mib=float(memory.get("MemAvailable", 0)) / 1024,
        swap_used_mib=max(0.0, total_swap - free_swap),
        load_1m=float(os.getloadavg()[0]),
        disk_free_gib=float(disk.free) / GIB,
        reconcile_active=reconcile_active,
        semantic_active=semantic_active,
        critical_window=_in_daily_critical_window(timestamp),
        formal_pipeline_active=formal_pipeline_active,
    )


def run_backfill(
    repo_root: str | Path,
    *,
    python: str,
    state_path: str | Path,
    runtime_budget_seconds: int = 1_080,
) -> dict:
    root = Path(repo_root).resolve()
    state_file = Path(state_path)
    now = datetime.now(timezone.utc)
    state = _load_state(state_file, now=now)
    health_before = current_health(root, now=now)
    reason = guard_reason(health_before)
    if reason:
        result = {
            "status": "deferred",
            "reason": reason,
            "health": asdict(health_before),
            "generated_at": now.isoformat(),
        }
        _record_state(state_file, state, result, phase=str(state["phase"]))
        return result

    selected_phase = choose_phase(state, now=now)
    if selected_phase != str(state.get("phase") or "a"):
        state["phase"] = selected_phase
        state["phase_started_at"] = now.isoformat()
        state["consecutive_breaches"] = 0
    phase = PHASES[selected_phase]
    started = time.monotonic()
    commands = [
        [
            python,
            "-m",
            "stock_analyze.cli",
            "intelligence-enrich",
            "--repo-root",
            str(root),
            "--limit",
            str(phase.download_limit),
            "--stages",
            "enqueue",
            "download",
        ],
        *[
            [
                python,
                "-m",
                "stock_analyze.cli",
                "intelligence-enrich",
                "--repo-root",
                str(root),
                "--limit",
                str(phase.parse_batch_size),
                "--stages",
                "parse",
            ]
            for _ in range(phase.parse_batches)
        ],
    ]
    completed = 0
    errors: list[dict] = []
    deferred_document_ids: list[int] = []
    stop_reason = ""
    for command in commands:
        remaining = runtime_budget_seconds - (time.monotonic() - started)
        if remaining <= 30:
            stop_reason = "runtime_budget_reached"
            break
        is_parse = command[-1] == "parse"
        parse_document_id = (
            _next_parse_document(root) if is_parse else None
        )
        if is_parse and parse_document_id is None:
            stop_reason = "parse_queue_empty"
            break
        try:
            child = subprocess.run(
                command,
                cwd=root,
                check=False,
                text=True,
                capture_output=True,
                timeout=max(
                    1,
                    int(
                        min(
                            remaining,
                            PARSE_DOCUMENT_TIMEOUT_SECONDS
                            if is_parse
                            else remaining,
                        )
                    ),
                ),
            )
        except subprocess.TimeoutExpired:
            if parse_document_id is not None:
                if _defer_parse_document(
                    root,
                    parse_document_id,
                    reason="parse_timeout_deferred",
                ):
                    deferred_document_ids.append(parse_document_id)
                health_now = current_health(root)
                guard = guard_reason(health_now)
                if guard:
                    stop_reason = guard
                    break
                if (
                    health_now.swap_used_mib
                    - health_before.swap_used_mib
                    >= 256
                ):
                    stop_reason = "swap_growth_high"
                    break
                continue
            stop_reason = "runtime_budget_reached"
            break
        if child.stdout:
            print(child.stdout, end="")
        if child.stderr:
            print(child.stderr, end="", file=os.sys.stderr)
        if child.returncode != 0:
            errors.append(
                {
                    "returncode": child.returncode,
                    "command": command,
                }
            )
            stop_reason = "child_failed"
            break
        completed += 1
        health_now = current_health(root)
        guard = guard_reason(health_now)
        if guard:
            stop_reason = guard
            break
        if (
            health_now.swap_used_mib
            - health_before.swap_used_mib
            >= 256
        ):
            stop_reason = "swap_growth_high"
            break
    duration = time.monotonic() - started
    peak_rss_mib = max(
        float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        / 1024,
        _cgroup_peak_mib(),
    )
    breach = bool(errors) or peak_rss_mib >= 1_024
    state["consecutive_breaches"] = (
        int(state.get("consecutive_breaches") or 0) + 1
        if breach
        else 0
    )
    if (
        selected_phase == "b"
        and int(state["consecutive_breaches"]) >= 2
    ):
        state["phase"] = "a"
        state["phase_started_at"] = datetime.now(timezone.utc).isoformat()
        stop_reason = stop_reason or "phase_b_auto_fallback"
    result = {
        "status": "failed" if errors else "success",
        "phase": selected_phase,
        "commands_completed": completed,
        "commands_planned": len(commands),
        "duration_seconds": round(duration, 3),
        "peak_rss_mib": round(peak_rss_mib, 3),
        "stop_reason": stop_reason or None,
        "errors": errors,
        "parse_timeouts": len(deferred_document_ids),
        "deferred_document_ids": deferred_document_ids,
        "health_before": asdict(health_before),
        "health_after": asdict(current_health(root)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _record_state(state_file, state, result, phase=selected_phase)
    return result


def _next_parse_document(repo_root: Path) -> int | None:
    config_path = repo_root / "configs" / "intelligence_semantic.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        parser_version = str(
            dict(config.get("parser") or {}).get("version")
            or "announcement-layout-v1"
        )
    except (OSError, UnicodeError, ValueError, AttributeError):
        parser_version = "announcement-layout-v1"
    database = (
        repo_root
        / "data"
        / "shared"
        / "intelligence"
        / "intelligence.sqlite3"
    )
    with sqlite3.connect(database) as connection:
        lease_tables_ready = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type='table'
              AND name IN (
                'artifact_worker_jobs',
                'artifact_worker_items'
              )
            """
        ).fetchone()[0] == 2
        lease_filter = (
            """
              AND NOT EXISTS (
                SELECT 1
                FROM artifact_worker_items wi
                JOIN artifact_worker_jobs wj
                  ON wj.job_id=wi.job_id
                WHERE wi.document_id=a.document_id
                  AND wj.stage='parse'
                  AND wj.status IN ('leased', 'importing')
                  AND wj.lease_until>?
              )
            """
            if lease_tables_ready
            else ""
        )
        parameters = (
            (
                parser_version,
                datetime.now(timezone.utc).isoformat(),
            )
            if lease_tables_ready
            else (parser_version,)
        )
        row = connection.execute(
            f"""
            SELECT a.document_id
            FROM document_artifacts a
            JOIN documents d ON d.id=a.document_id
            WHERE a.artifact_type='pdf'
              AND a.status='downloaded'
              AND NOT EXISTS (
                SELECT 1
                FROM document_artifacts p
                WHERE p.document_id=a.document_id
                  AND p.artifact_type='parsed'
                  AND p.parser_version=?
              )
              {lease_filter}
            ORDER BY d.queue_priority DESC,
                     d.live_observed DESC,
                     a.updated_at,
                     a.document_id
            LIMIT 1
            """,
            parameters,
        ).fetchone()
    return int(row[0]) if row is not None else None


def _defer_parse_document(
    repo_root: Path,
    document_id: int,
    *,
    reason: str,
) -> bool:
    database = (
        repo_root
        / "data"
        / "shared"
        / "intelligence"
        / "intelligence.sqlite3"
    )
    with sqlite3.connect(database) as connection:
        cursor = connection.execute(
            """
            UPDATE document_artifacts
            SET error=?, updated_at=?
            WHERE document_id=?
              AND artifact_type='pdf'
              AND status='downloaded'
            """,
            (
                str(reason)[:256],
                datetime.now(timezone.utc).isoformat(),
                int(document_id),
            ),
        )
        connection.commit()
    return bool(cursor.rowcount)


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    path = Path("/proc/meminfo")
    if not path.is_file():
        return {
            "MemAvailable": 8 * 1024 * 1024,
            "SwapTotal": 0,
            "SwapFree": 0,
        }
    for line in path.read_text(encoding="ascii").splitlines():
        key, _, value = line.partition(":")
        raw = value.strip().split()[0]
        if raw.isdigit():
            values[key] = int(raw)
    return values


def _cgroup_peak_mib() -> float:
    try:
        cgroup_line = next(
            line
            for line in Path("/proc/self/cgroup")
            .read_text(encoding="ascii")
            .splitlines()
            if line.startswith("0::")
        )
        relative = cgroup_line.split("::", 1)[1].lstrip("/")
        peak_path = Path("/sys/fs/cgroup") / relative / "memory.peak"
        raw = peak_path.read_text(encoding="ascii").strip()
        return float(raw) / MIB
    except (OSError, StopIteration, ValueError):
        return 0.0


def _load_state(path: Path, *, now: datetime) -> dict:
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return {
        "schema_version": 1,
        "phase": "a",
        "phase_started_at": now.isoformat(),
        "consecutive_breaches": 0,
        "history": [],
    }


def _record_state(
    path: Path,
    state: dict,
    result: dict,
    *,
    phase: str,
) -> None:
    entry = {
        "phase": phase,
        "status": result["status"],
        "duration_seconds": float(result.get("duration_seconds") or 0.0),
        "peak_rss_mib": float(result.get("peak_rss_mib") or 0.0),
        "generated_at": result["generated_at"],
        "reason": result.get("reason") or result.get("stop_reason"),
    }
    history = list(state.get("history") or ())
    history.append(entry)
    state["history"] = history[-200:]
    state["updated_at"] = result["generated_at"]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        path,
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("/opt/stock-analyze/app"),
    )
    parser.add_argument(
        "--python",
        default="/opt/stock-analyze/venv/bin/python",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path(
            "/opt/stock-analyze/app/data/shared/intelligence/"
            "artifact_backfill_state.json"
        ),
    )
    parser.add_argument(
        "--runtime-budget-seconds",
        type=int,
        default=1_080,
    )
    args = parser.parse_args(argv)
    result = run_backfill(
        args.repo_root,
        python=args.python,
        state_path=args.state_path,
        runtime_budget_seconds=max(60, args.runtime_budget_seconds),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "deferred":
        return DEFERRED_EXIT
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PHASES",
    "RuntimeHealth",
    "choose_phase",
    "guard_reason",
    "main",
    "run_backfill",
]
