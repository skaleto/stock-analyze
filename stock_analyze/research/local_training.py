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


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_source_fingerprint(manifest: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"source_fingerprint", "created_at", "status", "bundle"}
    }
    payload["files"] = sorted(
        [dict(item) for item in payload.get("files") or []],
        key=lambda item: str(item.get("path") or ""),
    )
    return _payload_fingerprint(payload)


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
    files = manifest.get("files") or []
    if not isinstance(files, list) or not files:
        raise ValueError("transfer_bundle_files_empty")
    if str(manifest.get("source_fingerprint") or "") != (
        manifest_source_fingerprint(manifest)
    ):
        raise ValueError("transfer_bundle_source_fingerprint_mismatch")
    for item in files:
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
    source_entries: list[tuple[Path, dict[str, Any]]] = []
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"training_bundle_source_missing:{source}")
        relative = source.relative_to(repo)
        source_entries.append((source, {
            "path": str(relative),
            "sha256": _sha256(source),
            "size": source.stat().st_size,
        }))
    source_entries.sort(key=lambda item: item[1]["path"])
    source_files = [dict(item) for _, item in source_entries]
    manifest_base = {
        "schema_version": 1,
        "kind": "research_training_input",
        "market": str(market),
        "as_of": str(as_of),
        "snapshot_date": snapshot_date,
        "read_only_input": True,
        "files": source_files,
    }
    source_fingerprint = manifest_source_fingerprint(manifest_base)
    if manifest_path.exists():
        existing = verify_transfer_bundle(bundle)
        if (
            existing.get("kind") == "research_training_input"
            and existing.get("market") == str(market)
            and existing.get("as_of") == str(as_of)
            and existing.get("snapshot_date") == snapshot_date
            and existing.get("source_fingerprint") == source_fingerprint
        ):
            return existing
        raise ValueError("training_bundle_destination_stale")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError("transfer_bundle_destination_not_empty")
    files: list[dict[str, Any]] = []
    for source, source_file in source_entries:
        relative = Path(str(source_file["path"]))
        target = bundle / "payload" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append(dict(source_file))
    manifest = {
        **manifest_base,
        "source_fingerprint": source_fingerprint,
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
        "source_fingerprint": verified.get("source_fingerprint"),
        "installed": installed,
    }


def verify_installed_training_bundle(
    repo_root: str | Path,
    bundle: str | Path,
) -> dict[str, Any]:
    repo = Path(repo_root)
    verified = verify_transfer_bundle(bundle)
    if verified.get("kind") != "research_training_input":
        raise ValueError("training_bundle_kind")
    for item in verified.get("files") or []:
        relative = _safe_relative(str(item["path"]))
        installed = repo / relative
        if not installed.is_file():
            raise ValueError(f"training_bundle_installed_file_missing:{relative}")
        if _sha256(installed) != str(item.get("sha256") or ""):
            raise ValueError(f"training_bundle_installed_hash_mismatch:{relative}")
    return verified


def _verify_training_provenance_link(
    output_manifest: dict[str, Any],
    training_input_bundle: str | Path | None,
) -> dict[str, Any]:
    if training_input_bundle is None:
        raise ValueError("model_bundle_training_input_required")
    training_input = verify_transfer_bundle(training_input_bundle)
    if training_input.get("kind") != "research_training_input":
        raise ValueError("model_bundle_training_input_kind")
    if str(training_input.get("market") or "") != str(
        output_manifest.get("market") or ""
    ):
        raise ValueError("model_bundle_training_input_market_mismatch")
    if str(training_input.get("snapshot_date") or "") != str(
        output_manifest.get("training_snapshot_date") or ""
    ):
        raise ValueError("model_bundle_training_input_snapshot_mismatch")
    if str(training_input.get("source_fingerprint") or "") != str(
        output_manifest.get("training_input_fingerprint") or ""
    ):
        raise ValueError("model_bundle_training_input_fingerprint_mismatch")
    return training_input


def export_research_result_bundle(
    repo_root: str | Path,
    result_path: str | Path,
    training_input_bundle: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    """Export bounded reports and frozen windows without any model state."""

    repo = Path(repo_root).resolve()
    training_input = verify_transfer_bundle(training_input_bundle)
    if training_input.get("kind") != "research_training_input":
        raise ValueError("research_result_training_input_kind")
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research_result_invalid") from exc
    items = list(result.get("results") or [result])
    sources: dict[Path, Path] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("json_path", "report_path"):
            source = Path(str(item.get(key) or "")).resolve()
            try:
                relative = source.relative_to(repo)
            except ValueError as exc:
                raise ValueError("research_result_report_outside_repo") from exc
            if (
                relative.parts[:2] != ("reports", "research")
                or not relative.name.startswith("baseline_first_")
                or relative.suffix not in {".json", ".md"}
                or not source.is_file()
            ):
                raise ValueError("research_result_report_path_invalid")
            sources[relative] = source
        window = dict(item.get("window_manifest") or {})
        source = Path(str(window.get("source_path") or "")).resolve()
        target = _safe_relative(str(window.get("target_path") or ""))
        if (
            target.parts[:3] != ("data", "research", "baseline_first")
            or target.name != "window_manifest.json"
            or not source.is_file()
        ):
            raise ValueError("research_result_window_path_invalid")
        try:
            source.relative_to(repo)
        except ValueError as exc:
            raise ValueError("research_result_window_outside_repo") from exc
        sources[target] = source
    if not sources:
        raise ValueError("research_result_files_empty")
    planned_files = [
        {
            "path": str(relative),
            "sha256": _sha256(source),
            "size": source.stat().st_size,
        }
        for relative, source in sorted(sources.items(), key=lambda item: str(item[0]))
    ]
    manifest_base = {
        "schema_version": 1,
        "kind": "research_evaluation_output",
        "market": str(training_input.get("market") or ""),
        "as_of": str(result.get("snapshot_date") or result.get("as_of") or ""),
        "training_snapshot_date": str(training_input.get("snapshot_date") or ""),
        "training_input_fingerprint": str(
            training_input.get("source_fingerprint") or ""
        ),
        "formal_strategy_activated": False,
        "registry_mutated": False,
        "files": planned_files,
    }
    manifest = {
        **manifest_base,
        "source_fingerprint": manifest_source_fingerprint(manifest_base),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    bundle = Path(destination)
    manifest_path = bundle / "manifest.json"
    if manifest_path.exists():
        existing = verify_transfer_bundle(bundle)
        if existing.get("source_fingerprint") == manifest["source_fingerprint"]:
            return existing
        raise ValueError("research_result_destination_stale")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError("transfer_bundle_destination_not_empty")
    for relative, source in sources.items():
        _copy_atomic(source, bundle / "payload" / relative)
    write_text_atomic(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def import_research_result_bundle(
    repo_root: str | Path,
    bundle: str | Path,
    *,
    training_input_bundle: str | Path | None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    verified = verify_transfer_bundle(bundle)
    if verified.get("kind") != "research_evaluation_output":
        raise ValueError("research_result_bundle_kind")
    _verify_training_provenance_link(verified, training_input_bundle)
    installed: list[str] = []
    unchanged: list[str] = []
    for item in verified.get("files") or []:
        relative = _safe_relative(str(item.get("path") or ""))
        source = Path(bundle) / "payload" / relative
        destination = repo / relative
        is_report = (
            relative.parts[:2] == ("reports", "research")
            and relative.name.startswith("baseline_first_")
            and relative.suffix in {".json", ".md"}
        )
        is_window = (
            relative.parts[:3] == ("data", "research", "baseline_first")
            and relative.name == "window_manifest.json"
        )
        if not is_report and not is_window:
            raise ValueError(f"research_result_target_forbidden:{relative}")
        if is_window and destination.exists():
            try:
                existing = json.loads(destination.read_text(encoding="utf-8"))
                incoming = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("research_result_window_invalid") from exc
            if (
                existing.get("declaration_id") != incoming.get("declaration_id")
                or existing.get("payload") != incoming.get("payload")
            ):
                raise ValueError("research_result_window_conflict")
            unchanged.append(str(relative))
            continue
        _copy_atomic(source, destination)
        installed.append(str(relative))
    return {
        "status": "imported",
        "market": verified.get("market"),
        "installed": installed,
        "unchanged": unchanged,
        "registry_mutated": False,
        "formal_strategy_activated": False,
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
    training_input = dict(report.get("training_input") or {})
    training_input_fingerprint = str(
        training_input.get("source_fingerprint") or ""
    )
    training_snapshot_date = str(training_input.get("snapshot_date") or "")
    if (
        str(training_input.get("market") or "") != market
        or len(training_input_fingerprint) != 64
        or any(
            character not in "0123456789abcdef"
            for character in training_input_fingerprint
        )
        or len(training_snapshot_date) != 8
        or not training_snapshot_date.isdigit()
    ):
        raise ValueError("model_bundle_training_input_provenance_missing")
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
    control = {
        "market": market,
        "account_scope": account_scope,
        "horizon": horizon,
        "model_root": str(model_root_relative),
        "models": patch_models,
    }
    control_text = json.dumps(
        control,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    source_file_records = [
        {
            "path": str(source.relative_to(repo)),
            "sha256": _sha256(source),
            "size": source.stat().st_size,
        }
        for source in file_sources
    ]
    control_bytes = control_text.encode("utf-8")
    planned_files = [{
        "path": str(control_relative),
        "sha256": hashlib.sha256(control_bytes).hexdigest(),
        "size": len(control_bytes),
    }, *source_file_records]
    manifest_base = {
        "schema_version": 1,
        "kind": "research_model_output",
        "market": market,
        "account_scope": account_scope,
        "horizon": horizon,
        "as_of": str(report.get("as_of") or ""),
        "model_root": str(model_root_relative),
        "tournament_root": str(tournament_relative),
        "formal_strategy_activated": False,
        "training_input_fingerprint": training_input_fingerprint,
        "training_snapshot_date": training_snapshot_date,
        "rejected_candidate_evidence": "compact_without_row_level_parquet",
        "shadow_candidate_evidence": "complete",
        "files": planned_files,
    }
    source_fingerprint = manifest_source_fingerprint(manifest_base)
    bundle = Path(destination)
    manifest_path = bundle / "manifest.json"
    if manifest_path.exists():
        existing = verify_transfer_bundle(bundle)
        if (
            existing.get("kind") == "research_model_output"
            and existing.get("market") == market
            and existing.get("account_scope") == account_scope
            and int(existing.get("horizon") or 0) == horizon
            and existing.get("as_of") == str(report.get("as_of") or "")
            and existing.get("training_input_fingerprint")
            == training_input_fingerprint
            and existing.get("training_snapshot_date") == training_snapshot_date
            and existing.get("source_fingerprint") == source_fingerprint
        ):
            return existing
        raise ValueError("model_bundle_destination_stale")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError("transfer_bundle_destination_not_empty")
    control_path = bundle / "payload" / control_relative
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text(
        control_text,
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
        **manifest_base,
        "source_fingerprint": source_fingerprint,
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
    *,
    training_input_bundle: str | Path | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    verified = verify_transfer_bundle(bundle)
    if verified.get("kind") != "research_model_output":
        raise ValueError("model_bundle_kind")
    if verified.get("formal_strategy_activated") is not False:
        raise ValueError("model_bundle_formal_activation_forbidden")
    _verify_training_provenance_link(verified, training_input_bundle)
    model_root_relative = _safe_relative(str(verified.get("model_root") or ""))
    tournament_relative = _safe_relative(str(verified.get("tournament_root") or ""))
    expected_prefix = (*model_root_relative.parts, "tournaments")
    if tournament_relative.parts[: len(expected_prefix)] != expected_prefix:
        raise ValueError("model_bundle_tournament_path_invalid")
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
    champion_versions = {
        str(state.get("champion_model_version") or ""),
        *{
            str(value)
            for value in (state.get("champion_model_versions") or {}).values()
            if value
        },
    }
    incoming_models: dict[str, dict[str, Any]] = {}
    existing_versions: list[str] = []
    for version, raw_model in sorted((control.get("models") or {}).items()):
        normalized_version = str(version)
        model = dict(raw_model)
        _allowed_model_state(model)
        artifact_relative = _safe_relative(str(model.pop("artifact_relative", "")))
        report_relative = _safe_relative(str(model.pop("tournament_report_relative", "")))
        if artifact_relative.parts[: len(tournament_relative.parts)] != tournament_relative.parts:
            raise ValueError("model_bundle_artifact_target_forbidden")
        if normalized_version in champion_versions and normalized_version not in models:
            raise ValueError("model_bundle_champion_pointer_collision")
        model["artifact"] = str(repo / artifact_relative)
        model["tournament_report"] = str(repo / report_relative)
        if normalized_version in models:
            existing_versions.append(normalized_version)
            continue
        incoming_models[normalized_version] = model
    if existing_versions:
        if incoming_models:
            raise ValueError("model_bundle_partial_version_collision")
        return {
            "status": "already_present",
            "market": verified.get("market"),
            "account_scope": verified.get("account_scope"),
            "horizon": verified.get("horizon"),
            "installed": [],
            "model_versions": [],
            "existing_model_versions": existing_versions,
            "champion_model_version": state.get("champion_model_version"),
            "formal_strategy_activated": False,
        }
    installed: list[str] = []
    for item in verified.get("files") or []:
        relative = _safe_relative(str(item["path"]))
        if relative.parts[0] == "_control":
            continue
        if relative.parts[: len(tournament_relative.parts)] != tournament_relative.parts:
            raise ValueError(f"model_bundle_target_forbidden:{relative}")
        _copy_atomic(Path(bundle) / "payload" / relative, repo / relative)
        installed.append(str(relative))
    imported_versions: list[str] = []
    for version, model in incoming_models.items():
        models[version] = model
        imported_versions.append(version)
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
        "existing_model_versions": [],
        "champion_model_version": state.get("champion_model_version"),
        "formal_strategy_activated": False,
    }


__all__ = [
    "export_model_bundle",
    "export_research_result_bundle",
    "export_training_bundle",
    "import_research_result_bundle",
    "import_model_bundle",
    "install_training_bundle",
    "manifest_source_fingerprint",
    "verify_transfer_bundle",
    "verify_installed_training_bundle",
]
