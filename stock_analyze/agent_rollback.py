from __future__ import annotations

from pathlib import Path
from typing import Any

from .proposal_apply import rollback_agent


def rollback(agent_id: str, to_hash: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Restore an agent overlay from `configs/agents/_history/<hash>.yaml`."""

    return rollback_agent(agent_id, to_hash, repo_root=repo_root)

