"""Read-only Dashboard projection for completed multi-agent research runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


MAX_DIGEST_CHARS = 4_000
MAX_MANIFESTS_TO_INSPECT = 200


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object, *, limit: int = 512) -> str:
    return str(value or "").strip()[:limit]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _empty_universe() -> dict[str, object]:
    return {
        "status": "unavailable",
        "asOf": None,
        "aShare": {"scopeCounts": {}},
        "funds": {"sourceCounts": {}, "overseasScopeCounts": {}},
    }


def _universe_summary(root: Path) -> dict[str, object]:
    payload = _read_json(root / "data" / "research" / "universe_catalogs" / "latest.json")
    if not payload:
        return _empty_universe()
    a_share = _mapping(_mapping(payload.get("a_share")).get("summary"))
    funds = _mapping(_mapping(payload.get("funds")).get("summary"))
    return {
        "status": "available",
        "asOf": _text(payload.get("as_of"), limit=16) or None,
        "aShare": {
            "scopeCounts": _mapping(a_share.get("scope_counts")),
            "uniqueInstruments": a_share.get("unique_instruments"),
        },
        "funds": {
            "sourceCounts": _mapping(funds.get("source_counts")),
            "overseasScopeCounts": _mapping(funds.get("overseas_scope_counts")),
            "classificationCounts": _mapping(funds.get("classification_counts")),
        },
    }


def _latest_run(root: Path) -> dict[str, object] | None:
    artifact_root = root / "reports" / "research" / "multi_agent"
    if not artifact_root.exists():
        return None
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    paths = sorted(artifact_root.rglob("manifest.json"), reverse=True)
    for path in paths[:MAX_MANIFESTS_TO_INSPECT]:
        manifest = _read_json(path)
        instrument = _mapping(manifest.get("instrument"))
        if (
            not manifest
            or not _text(manifest.get("run_id"))
            or not _text(manifest.get("market"))
            or not _text(instrument.get("code"))
            or manifest.get("execution_effect") != "none_research_only"
        ):
            continue
        created = _text(manifest.get("created_at"))
        candidates.append((created, path, manifest))
    if not candidates:
        return None
    _, manifest_path, manifest = max(candidates, key=lambda item: (item[0], item[1].as_posix()))
    output = manifest_path.parent
    instrument = _mapping(manifest.get("instrument"))
    try:
        digest = (output / "digest.md").read_text(encoding="utf-8").strip()[:MAX_DIGEST_CHARS]
    except OSError:
        digest = ""
    return {
        "runId": _text(manifest.get("run_id"), limit=160),
        "createdAt": _text(manifest.get("created_at"), limit=64) or None,
        "status": _text(manifest.get("status"), limit=64) or "unknown",
        "market": _text(manifest.get("market"), limit=64),
        "instrument": {
            "code": _text(instrument.get("code"), limit=64),
            "name": _text(instrument.get("name"), limit=256),
        },
        "model": _text(manifest.get("model"), limit=128) or None,
        "degradedRoles": [
            _text(item, limit=64)
            for item in (manifest.get("degraded_roles") or [])
            if _text(item, limit=64)
        ][:8],
        "digest": digest,
        "executionEffect": "none_research_only",
        "reportPath": (output / "full_report.md").relative_to(root).as_posix(),
    }


def build_dashboard_multi_agent_research_data(
    *,
    repo_root: str | Path | None = None,
) -> dict[str, object]:
    """Return bounded persisted artifacts without fetching providers or models."""
    root = Path(repo_root or ".").resolve()
    latest = _latest_run(root)
    return {
        "schemaVersion": "multi-agent-research-dashboard-v1",
        "status": "available" if latest is not None else "empty",
        "latestRun": latest,
        "universe": _universe_summary(root),
        "executionEffect": "none_research_only",
    }


__all__ = ["build_dashboard_multi_agent_research_data"]
