"""Run ledger + config-hash snapshot. CSV-backed to avoid a database dependency.

Each CLI invocation goes through `RunLedger.run(...)`, which appends one
`status=running` row immediately, and on exit appends a follow-up row with
`status=success` or `status=failed`. Readers should group by `run_id` and take
the row with the latest `finished_at`/`started_at`.
"""

from __future__ import annotations

import json
import os
import random
import string
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import canonical_json, config_hash
from .utils import append_csv, ensure_dirs, now_iso


RUNS_FILE = "runs.csv"
CONFIG_SNAPSHOT_DIR = "configs"
RUNS_COLUMNS = [
    "run_id",
    "command",
    "as_of",
    "started_at",
    "finished_at",
    "duration_ms",
    "status",
    "error_summary",
    "config_hash",
    "code_version",
]


def generate_run_id(command: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{command}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{suffix}"


def code_version(repo_root: str | Path | None = None) -> str:
    """Return the short HEAD SHA when inside a git working copy, else 'no_git'.

    Avoids spawning ``git`` by reading the porcelain files directly, so it
    works inside containers and worktrees without git installed.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    git_path = _find_git_path(root)
    if git_path is None:
        return "no_git"
    try:
        head_file = git_path / "HEAD"
        if not head_file.exists():
            return "no_git"
        head = head_file.read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            ref_path = git_path / ref
            if ref_path.exists():
                sha = ref_path.read_text(encoding="utf-8").strip()
            else:
                packed = git_path / "packed-refs"
                sha = None
                if packed.exists():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.endswith(ref):
                            sha = line.split(" ", 1)[0].strip()
                            break
                if not sha:
                    return "no_git"
        else:
            sha = head
        return sha[:7] if sha else "no_git"
    except Exception:  # noqa: BLE001
        return "no_git"


def _find_git_path(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        git = candidate / ".git"
        if git.is_dir():
            return git
        if git.is_file():
            try:
                contents = git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if contents.startswith("gitdir:"):
                resolved = (candidate / contents.split(":", 1)[1].strip()).resolve()
                if resolved.is_dir():
                    return resolved
    return None


class RunLedger:
    """File-backed append-only run ledger."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        ensure_dirs(self.data_dir, self.data_dir / CONFIG_SNAPSHOT_DIR)

    @property
    def runs_path(self) -> Path:
        return self.data_dir / RUNS_FILE

    def snapshot_config(self, config: dict[str, Any]) -> str:
        digest = config_hash(config)
        snapshot_path = self.data_dir / CONFIG_SNAPSHOT_DIR / f"{digest}.json"
        if not snapshot_path.exists():
            try:
                snapshot_path.write_text(canonical_json(config), encoding="utf-8")
            except OSError:
                pass
        return digest

    def _append(self, row: dict[str, Any]) -> None:
        try:
            append_csv(self.runs_path, [row], RUNS_COLUMNS)
        except Exception:  # noqa: BLE001
            # Ledger is observability-only; never break the host command.
            pass

    @contextmanager
    def run(self, command: str, as_of: str | None, config: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        run_id = generate_run_id(command)
        started_at = now_iso()
        digest = self.snapshot_config(config) if config else ""
        version = code_version(self.data_dir.parent if self.data_dir.name == "data" else None)
        context: dict[str, Any] = {
            "run_id": run_id,
            "command": command,
            "as_of": as_of or "",
            "started_at": started_at,
            "config_hash": digest,
            "code_version": version,
        }
        self._append({**context, "finished_at": "", "duration_ms": "", "status": "running", "error_summary": ""})
        start_clock = datetime.now()
        try:
            yield context
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((datetime.now() - start_clock).total_seconds() * 1000)
            error_summary = _summarize_error(exc)
            self._append(
                {
                    **context,
                    "finished_at": now_iso(),
                    "duration_ms": duration_ms,
                    "status": "failed",
                    "error_summary": error_summary,
                }
            )
            raise
        else:
            duration_ms = int((datetime.now() - start_clock).total_seconds() * 1000)
            self._append(
                {
                    **context,
                    "finished_at": now_iso(),
                    "duration_ms": duration_ms,
                    "status": "success",
                    "error_summary": "",
                }
            )


def _summarize_error(exc: BaseException) -> str:
    name = type(exc).__name__
    message = str(exc).splitlines()[0] if str(exc) else ""
    payload = f"{name}: {message}".strip(": ").strip()
    return payload[:300]


def read_runs(data_dir: str | Path) -> list[dict[str, Any]]:
    """Return the latest row per run_id from ``data/runs.csv``."""

    path = Path(data_dir) / RUNS_FILE
    if not path.exists():
        return []
    import csv as _csv

    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for row in _csv.DictReader(handle):
            run_id = row.get("run_id") or ""
            if not run_id:
                continue
            existing = rows.get(run_id)
            if existing is None or (row.get("finished_at") or "") >= (existing.get("finished_at") or ""):
                rows[run_id] = row
    return sorted(rows.values(), key=lambda item: item.get("started_at", ""), reverse=True)
