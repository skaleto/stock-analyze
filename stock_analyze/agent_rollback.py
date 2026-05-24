"""Rollback an agent overlay to a historical config hash.

Reads ``configs/agents/_history/<hash>.yaml`` and restores it to
``configs/agents/<agent>.yaml``. Appends a ``rollback`` row to
``data/<agent>/config_evolution.csv`` using the schema from
:mod:`evolution_writer`.

This module is what the ``agent-rollback`` CLI subcommand calls. The old
implementation lived in ``proposal_apply.rollback_agent`` which is being
deleted by ``enable-llm-direct-strategy-evolution``; this is the
freestanding replacement.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import competition
from .config import config_hash
from .evolution_writer import _append_evolution_row
from .utils import write_text_atomic


def rollback(
    agent_id: str,
    to_hash: str,
    repo_root: str | Path | None = None,
    reviewer: str = "operator",
) -> dict[str, Any]:
    """Restore an agent overlay from ``configs/agents/_history/<hash>.yaml``.

    The historical snapshot remains in ``_history/`` (we do not move or
    delete it; the same hash may be re-applied later). The current overlay
    file becomes the snapshot content. A ``rollback`` row is appended to
    ``data/<agent>/config_evolution.csv`` for audit.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    paths = competition.resolve_agent_paths(agent_id, repo_root=root)
    history_path = root / "configs" / "agents" / "_history" / f"{to_hash}.yaml"
    if not history_path.exists():
        raise FileNotFoundError(f"history_not_found:{to_hash}")

    current_hash = config_hash(competition.load(agent_id, repo_root=root))
    overlay_path = paths.config_path
    old_text = overlay_path.read_text(encoding="utf-8")
    restored_text = history_path.read_text(encoding="utf-8")

    try:
        write_text_atomic(
            overlay_path,
            restored_text if restored_text.endswith("\n") else restored_text + "\n",
            encoding="utf-8",
        )
        restored_hash = config_hash(competition.load(agent_id, repo_root=root))
    except Exception:
        write_text_atomic(overlay_path, old_text, encoding="utf-8")
        raise

    _append_evolution_row(
        paths.data_dir / "config_evolution.csv",
        {
            "event": "rollback",
            "event_at": datetime.now().isoformat(timespec="seconds"),
            "agent_id": agent_id,
            "month": "",
            "from_hash": current_hash,
            "to_hash": restored_hash,
            "diff_summary": f"rollback_to:{to_hash}",
            "reasoning_file": "",
            "diff_file": "",
            "reviewer": reviewer,
        },
    )
    return {
        "agent_id": agent_id,
        "status": "rolled_back",
        "from_hash": current_hash,
        "to_hash": restored_hash,
    }
