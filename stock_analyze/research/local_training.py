"""Checksummed transfer bundles for local CPU model training."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import write_text_atomic
from .storage import ResearchStore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(raw: str) -> Path:
    value = Path(str(raw))
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise ValueError("transfer_bundle_path_invalid")
    return value


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temporary = handle.name
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def verify_transfer_bundle(bundle: str | Path) -> dict[str, Any]:
    root = Path(bundle)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("transfer_bundle_manifest_missing") from exc
    if int(manifest.get("schema_version") or 0) != 1:
        raise ValueError("transfer_bundle_schema")
    for item in manifest.get("files") or []:
        relative = _safe_relative(str(item.get("path") or ""))
        path = root / "payload" / relative
        if not path.is_file():
            raise ValueError(f"transfer_bundle_file_missing:{relative}")
        if path.stat().st_size != int(item.get("size") or -1):
            raise ValueError(f"transfer_bundle_size_mismatch:{relative}")
        if _sha256(path) != str(item.get("sha256") or ""):
            raise ValueError(f"transfer_bundle_hash_mismatch:{relative}")
    return {
        **manifest,
        "status": "verified",
        "bundle": str(root),
    }


def export_training_bundle(
    repo_root: str | Path,
    *,
    market: str,
    as_of: str,
    destination: str | Path,
) -> dict[str, Any]:
    repo = Path(repo_root)
    bundle = Path(destination)
    manifest_path = bundle / "manifest.json"
    if manifest_path.exists():
        existing = verify_transfer_bundle(bundle)
        if (
            existing.get("kind") == "research_training_input"
            and existing.get("market") == str(market)
        ):
            return existing
        raise ValueError("transfer_bundle_destination_conflict")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError("transfer_bundle_destination_not_empty")
    store = ResearchStore(repo / "data" / "research")
    snapshot_date = store.latest_common_snapshot_date(market, as_of=as_of)
    sources = [
        store.feature_snapshot_path(market, snapshot_date),
        store.label_snapshot_path(market, snapshot_date),
    ]
    competition_name = (
        "competition_a_share.yaml"
        if market == "a_share" else "competition_cn_qdii_etf.yaml"
    )
    for optional in (
        repo / "configs" / competition_name,
        repo / "configs" / "intelligence_factors.json",
    ):
        if optional.exists():
            sources.append(optional)
    baseline_root = repo / "data" / "research" / "baseline_first" / str(market)
    sources.extend(sorted(baseline_root.glob("*/window_manifest.json")))
    model_root = repo / "data" / "research" / "models" / str(market)
    if model_root.exists():
        for account_root in sorted(path for path in model_root.iterdir() if path.is_dir()):
            for horizon_root in sorted(
                path for path in account_root.iterdir()
                if path.is_dir() and path.name.isdigit()
            ):
                legacy = sorted(
                    (horizon_root / "tournaments").glob(
                        "*/evaluation_manifest.json"
                    )
                )
                if legacy:
                    sources.append(legacy[-1])
    sources = list(dict.fromkeys(sources))
    files: list[dict[str, Any]] = []
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"training_bundle_source_missing:{source}")
        relative = source.relative_to(repo)
        target = bundle / "payload" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({
            "path": str(relative),
            "sha256": _sha256(target),
            "size": target.stat().st_size,
        })
    manifest = {
        "schema_version": 1,
        "kind": "research_training_input",
        "market": str(market),
        "as_of": str(as_of),
        "snapshot_date": snapshot_date,
        "read_only_input": True,
        "files": sorted(files, key=lambda item: item["path"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def install_training_bundle(
    repo_root: str | Path,
    bundle: str | Path,
) -> dict[str, Any]:
    repo = Path(repo_root)
    verified = verify_transfer_bundle(bundle)
    if verified.get("kind") != "research_training_input":
        raise ValueError("training_bundle_kind")
    installed: list[str] = []
    for item in verified.get("files") or []:
        relative = _safe_relative(str(item["path"]))
        source = Path(bundle) / "payload" / relative
        destination = repo / relative
        if relative.parts[0] == "configs" and destination.exists():
            if _sha256(destination) != str(item["sha256"]):
                raise ValueError(f"training_bundle_config_mismatch:{relative}")
            continue
        if relative.parts[:2] != ("data", "research"):
            if relative.parts[0] != "configs":
                raise ValueError(f"training_bundle_target_forbidden:{relative}")
        _copy_atomic(source, destination)
        installed.append(str(relative))
    return {
        "status": "installed",
        "market": verified.get("market"),
        "snapshot_date": verified.get("snapshot_date"),
        "installed": installed,
    }


def _allowed_model_state(model: dict[str, Any]) -> None:
    if str(model.get("status") or "research") not in {"research", "shadow", "rejected"}:
        raise ValueError("model_bundle_active_state_forbidden")
    role_status = dict(model.get("role_status") or {})
    if any(str(status) not in {"research", "shadow", "rejected", "inactive"} for status in role_status.values()):
        raise ValueError("model_bundle_active_role_forbidden")


def export_model_bundle(
    repo_root: str | Path,
    report_path: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    report_file = Path(report_path).resolve()
    try:
        report_relative = report_file.relative_to(repo)
    except ValueError as exc:
        raise ValueError("model_bundle_report_outside_repo") from exc
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model_bundle_report_invalid") from exc
    if report.get("formal_strategy_activated") is not False:
        raise ValueError("model_bundle_formal_activation_forbidden")
    market = str(report.get("market") or "")
    account_scope = str(report.get("account_scope") or "")
    horizon = int(report.get("horizon") or 0)
    model_root_relative = Path(
        f"data/research/models/{market}/{account_scope}/{horizon}"
    )
    tournament_relative = report_relative.parent
    if tournament_relative.parts[: len(model_root_relative.parts) + 1] != (
        *model_root_relative.parts,
        "tournaments",
    ):
        raise ValueError("model_bundle_report_path_invalid")
    registry_path = repo / model_root_relative / "registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model_bundle_registry_invalid") from exc
    versions = {
        str(item.get("model_version") or "")
        for item in report.get("candidates") or []
        if item.get("model_version")
    }
    if not versions:
        raise ValueError("model_bundle_candidates_missing")
    patch_models: dict[str, Any] = {}
    for version in sorted(versions):
        model = dict((registry.get("models") or {}).get(version) or {})
        if not model:
            raise ValueError(f"model_bundle_registry_model_missing:{version}")
        _allowed_model_state(model)
        artifact = Path(str(model.get("artifact") or "")).resolve()
        try:
            artifact_relative = artifact.relative_to(repo)
        except ValueError as exc:
            raise ValueError("model_bundle_artifact_outside_repo") from exc
        if artifact_relative.parts[: len(tournament_relative.parts)] != tournament_relative.parts:
            raise ValueError("model_bundle_artifact_outside_tournament")
        model.pop("artifact", None)
        model.pop("tournament_report", None)
        model["artifact_relative"] = str(artifact_relative)
        model["tournament_report_relative"] = str(report_relative)
        patch_models[version] = model

    bundle = Path(destination)
    manifest_path = bundle / "manifest.json"
    if manifest_path.exists():
        existing = verify_transfer_bundle(bundle)
        if existing.get("kind") == "research_model_output":
            return existing
        raise ValueError("transfer_bundle_destination_conflict")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError("transfer_bundle_destination_not_empty")
    full_evidence_specs = {
        str(item.get("spec_id") or "")
        for item in report.get("candidates") or []
        if str(item.get("status") or "") == "shadow"
    }
    large_evidence_names = {
        "final_predictions.parquet",
        "final_periods.parquet",
        "final_trades.parquet",
        "final_decisions.parquet",
    }

    def include_file(path: Path) -> bool:
        if path.name not in large_evidence_names:
            return True
        try:
            candidate_index = path.parts.index("candidates")
            spec_id = path.parts[candidate_index + 1]
        except (ValueError, IndexError):
            return False
        return spec_id in full_evidence_specs

    file_sources = sorted(
        path
        for path in (repo / tournament_relative).rglob("*")
        if path.is_file() and include_file(path)
    )
    control_relative = Path("_control/registry_patch.json")
    control_path = bundle / "payload" / control_relative
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control = {
        "market": market,
        "account_scope": account_scope,
        "horizon": horizon,
        "model_root": str(model_root_relative),
        "models": patch_models,
    }
    control_path.write_text(
        json.dumps(control, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    files: list[dict[str, Any]] = [{
        "path": str(control_relative),
        "sha256": _sha256(control_path),
        "size": control_path.stat().st_size,
    }]
    for source in file_sources:
        relative = source.relative_to(repo)
        target = bundle / "payload" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({
            "path": str(relative),
            "sha256": _sha256(target),
            "size": target.stat().st_size,
        })
    manifest = {
        "schema_version": 1,
        "kind": "research_model_output",
        "market": market,
        "account_scope": account_scope,
        "horizon": horizon,
        "as_of": str(report.get("as_of") or ""),
        "model_root": str(model_root_relative),
        "tournament_root": str(tournament_relative),
        "formal_strategy_activated": False,
        "rejected_candidate_evidence": "compact_without_row_level_parquet",
        "shadow_candidate_evidence": "complete",
        "files": sorted(files, key=lambda item: item["path"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def import_model_bundle(
    repo_root: str | Path,
    bundle: str | Path,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    verified = verify_transfer_bundle(bundle)
    if verified.get("kind") != "research_model_output":
        raise ValueError("model_bundle_kind")
    if verified.get("formal_strategy_activated") is not False:
        raise ValueError("model_bundle_formal_activation_forbidden")
    model_root_relative = _safe_relative(str(verified.get("model_root") or ""))
    tournament_relative = _safe_relative(str(verified.get("tournament_root") or ""))
    expected_prefix = (*model_root_relative.parts, "tournaments")
    if tournament_relative.parts[: len(expected_prefix)] != expected_prefix:
        raise ValueError("model_bundle_tournament_path_invalid")
    installed: list[str] = []
    for item in verified.get("files") or []:
        relative = _safe_relative(str(item["path"]))
        if relative.parts[0] == "_control":
            continue
        if relative.parts[: len(tournament_relative.parts)] != tournament_relative.parts:
            raise ValueError(f"model_bundle_target_forbidden:{relative}")
        _copy_atomic(Path(bundle) / "payload" / relative, repo / relative)
        installed.append(str(relative))
    control_path = Path(bundle) / "payload/_control/registry_patch.json"
    try:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model_bundle_registry_patch_invalid") from exc
    if str(control.get("model_root") or "") != str(model_root_relative):
        raise ValueError("model_bundle_registry_patch_mismatch")
    registry_path = repo / model_root_relative / "registry.json"
    try:
        state = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {"champion_model_version": None, "models": {}}
    models = state.setdefault("models", {})
    imported_versions: list[str] = []
    for version, raw_model in sorted((control.get("models") or {}).items()):
        model = dict(raw_model)
        _allowed_model_state(model)
        artifact_relative = _safe_relative(str(model.pop("artifact_relative", "")))
        report_relative = _safe_relative(str(model.pop("tournament_report_relative", "")))
        if artifact_relative.parts[: len(tournament_relative.parts)] != tournament_relative.parts:
            raise ValueError("model_bundle_artifact_target_forbidden")
        model["artifact"] = str(repo / artifact_relative)
        model["tournament_report"] = str(repo / report_relative)
        models[str(version)] = model
        imported_versions.append(str(version))
    write_text_atomic(
        registry_path,
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "status": "imported",
        "market": verified.get("market"),
        "account_scope": verified.get("account_scope"),
        "horizon": verified.get("horizon"),
        "installed": installed,
        "model_versions": imported_versions,
        "champion_model_version": state.get("champion_model_version"),
        "formal_strategy_activated": False,
    }


__all__ = [
    "export_model_bundle",
    "export_training_bundle",
    "import_model_bundle",
    "install_training_bundle",
    "verify_transfer_bundle",
]
