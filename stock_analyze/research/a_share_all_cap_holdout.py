"""Immutable development artifacts and one-time all-cap holdout authorization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid

from .a_share_all_cap_contract import AllCapContract, load_all_cap_contract


_STORE_RELATIVE = Path("data/research/a_share_all_cap/v1")
_MANIFEST_NAMES = ("source", "universe", "feature")
_OUTCOME_STATUSES = frozenset({"pass", "fail", "insufficient_data"})
_RETURN_DATE_FIELDS = frozenset(
    {
        "trade_date",
        "signal_date",
        "entry_date",
        "exit_date",
        "label_end_date",
        "return_date",
        "oos_start",
        "oos_end",
        "evaluation_dates",
        "observed_return_dates",
    }
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("all_cap_artifact:json")


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _json_value(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """Return the stable SHA-256 used by all Task 4 JSON artifacts."""

    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def contract_sha256(contract: AllCapContract) -> str:
    """Hash every parsed contract field, including the complete frozen payload."""

    if not isinstance(contract, AllCapContract):
        raise ValueError("all_cap_artifact:contract")
    return canonical_hash(
        {
            "campaign_id": contract.campaign_id,
            "development_start": contract.development_start,
            "development_end": contract.development_end,
            "holdout_start": contract.holdout_start,
            "holdout_end": contract.holdout_end,
            "holdout_policy": contract.holdout_policy,
            "size_boundaries": contract.size_boundaries,
            "boundary_buffer_fraction": contract.boundary_buffer_fraction,
            "sleeves": [
                {
                    "name": sleeve.name,
                    "rank_min": sleeve.rank_min,
                    "rank_max": sleeve.rank_max,
                    "benchmark": sleeve.benchmark,
                    "capital_weight": sleeve.capital_weight,
                }
                for sleeve in contract.sleeves
            ],
            "raw": contract.raw,
        }
    )


def _store_root(repo_root: str | Path) -> Path:
    try:
        repo = Path(repo_root).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("all_cap_artifact:path") from exc
    root = repo / _STORE_RELATIVE
    current = repo
    for part in _STORE_RELATIVE.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("all_cap_artifact:symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("all_cap_artifact:symlink")
    return root.resolve(strict=True)


def _safe_existing_path(
    raw_path: str | Path,
    *,
    root: Path,
) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        candidate = root.parents[3] / path
    else:
        candidate = path
    absolute = candidate.absolute()
    try:
        resolved = absolute.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError("all_cap_artifact:path") from exc
    anchor = next(
        (
            ancestor
            for ancestor in (absolute, *absolute.parents)
            if ancestor.resolve(strict=False) == root
        ),
        None,
    )
    if anchor is None:
        raise ValueError("all_cap_artifact:path")
    current = anchor
    for part in absolute.relative_to(anchor).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("all_cap_artifact:symlink")
    if not resolved.is_file():
        raise ValueError("all_cap_artifact:path")
    return resolved


def _safe_output_dir(root: Path, relative: str) -> Path:
    destination = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("all_cap_artifact:symlink")
        current.mkdir(exist_ok=True)
    return destination


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("all_cap_artifact:json") from exc
    if not isinstance(payload, dict):
        raise ValueError("all_cap_artifact:json")
    return payload


def _write_json_exclusive(path: Path, payload: Mapping[str, Any], error: str) -> None:
    data = _canonical_bytes(payload) + b"\n"
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise ValueError(error) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _verified_manifest(path: Path, *, root: Path) -> dict[str, Any]:
    safe_path = _safe_existing_path(path, root=root)
    manifest = _read_json(safe_path)
    unsigned = dict(manifest)
    recorded = unsigned.pop("manifest_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_hash(unsigned):
        raise ValueError("all_cap_artifact:manifest_checksum")
    return {
        "path": safe_path.relative_to(root).as_posix(),
        "manifest_sha256": recorded,
        "payload": manifest,
    }


def _verified_manifests(
    manifest_paths: Mapping[str, str | Path],
    *,
    contract: AllCapContract,
    root: Path,
) -> dict[str, dict[str, str]]:
    if set(manifest_paths) != set(_MANIFEST_NAMES):
        raise ValueError("all_cap_artifact:manifest_binding")
    verified = {
        name: _verified_manifest(Path(manifest_paths[name]), root=root)
        for name in _MANIFEST_NAMES
    }
    source_hash = verified["source"]["manifest_sha256"]
    universe_hash = verified["universe"]["manifest_sha256"]
    feature = verified["feature"]["payload"]
    universe = verified["universe"]["payload"]
    expected_contract = contract_sha256(contract)
    if (
        universe.get("contract_sha256") != expected_contract
        or universe.get("source_manifest_sha256") != source_hash
        or feature.get("contract_sha256") != expected_contract
        or feature.get("source_manifest_sha256") != source_hash
        or feature.get("universe_manifest_sha256") != universe_hash
    ):
        raise ValueError("all_cap_artifact:manifest_binding")
    return {
        name: {
            "path": str(verified[name]["path"]),
            "manifest_sha256": str(verified[name]["manifest_sha256"]),
        }
        for name in _MANIFEST_NAMES
    }


def _date_key(value: object, *, error: str) -> str:
    text = str(value or "").replace("-", "")
    try:
        parsed = date(
            int(text[0:4]),
            int(text[4:6]),
            int(text[6:8]),
        )
    except (TypeError, ValueError):
        raise ValueError(error) from None
    if len(text) != 8 or parsed.strftime("%Y%m%d") != text:
        raise ValueError(error)
    return text


def _return_dates(value: Any, *, field: str | None = None) -> list[object]:
    if isinstance(value, Mapping):
        dates: list[object] = []
        for key, item in value.items():
            dates.extend(_return_dates(item, field=str(key)))
        return dates
    if isinstance(value, (list, tuple)):
        if field in _RETURN_DATE_FIELDS:
            return list(value)
        dates = []
        for item in value:
            dates.extend(_return_dates(item))
        return dates
    return [value] if field in _RETURN_DATE_FIELDS else []


def _validate_evaluation(
    evaluation: Mapping[str, Any],
    *,
    start: date,
    end: date,
    scope: str,
) -> dict[str, Any]:
    if not isinstance(evaluation, Mapping):
        raise ValueError(f"all_cap_{scope}:evaluation")
    payload = _json_value(evaluation)
    status = payload.get("status")
    gate = payload.get("gate")
    dates = payload.get("observed_return_dates")
    if (
        status not in _OUTCOME_STATUSES
        or not isinstance(gate, dict)
        or not isinstance(gate.get("passed"), bool)
        or not isinstance(dates, list)
        or any(not isinstance(value, str) for value in dates)
        or (status == "pass") != gate["passed"]
    ):
        raise ValueError(f"all_cap_{scope}:evaluation")
    lower = start.strftime("%Y%m%d")
    upper = end.strftime("%Y%m%d")
    normalized_dates = [
        _date_key(value, error=f"all_cap_{scope}:window")
        for value in _return_dates(payload)
    ]
    if any(value < lower or value > upper for value in normalized_dates):
        raise ValueError(f"all_cap_{scope}:window")
    payload["observed_return_dates"] = sorted(normalized_dates)
    payload["status"] = status
    return payload


def _artifact_payload(
    *,
    kind: str,
    contract: AllCapContract,
    manifests: Mapping[str, Mapping[str, str]],
    evaluation: Mapping[str, Any],
    development_sha256: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "campaign_id": contract.campaign_id,
        "status": evaluation["status"],
        "gate": evaluation["gate"],
        "contract_sha256": contract_sha256(contract),
        "manifests": manifests,
        "observed_return_dates": evaluation["observed_return_dates"],
        "result": evaluation.get("result", {}),
        "immutable": True,
    }
    if development_sha256 is not None:
        payload["development_artifact_sha256"] = development_sha256
    payload["artifact_sha256"] = canonical_hash(payload)
    return payload


def _persist_artifact(
    *,
    root: Path,
    directory: str,
    payload: Mapping[str, Any],
    error: str,
) -> dict[str, Any]:
    destination_dir = _safe_output_dir(root, directory)
    destination = destination_dir / f"{payload['artifact_sha256']}.json"
    _write_json_exclusive(destination, payload, error)
    return {**dict(payload), "artifact_path": str(destination)}


def run_development(
    *,
    repo_root: str | Path,
    contract: AllCapContract,
    manifest_paths: Mapping[str, str | Path],
    load_evaluation: Callable[[], Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and seal one development-only result."""

    root = _store_root(repo_root)
    development_dir = root / "development"
    if development_dir.is_symlink():
        raise ValueError("all_cap_artifact:symlink")
    sealed_path = development_dir / "sealed.json"
    if sealed_path.exists() or sealed_path.is_symlink():
        raise ValueError("all_cap_development:already_sealed")
    manifests = _verified_manifests(
        manifest_paths,
        contract=contract,
        root=root,
    )
    evaluation = _validate_evaluation(
        load_evaluation(),
        start=contract.development_start,
        end=contract.development_end,
        scope="development",
    )
    payload = _artifact_payload(
        kind="a_share_all_cap_development",
        contract=contract,
        manifests=manifests,
        evaluation=evaluation,
    )
    artifact = _persist_artifact(
        root=root,
        directory="development",
        payload=payload,
        error="all_cap_development:result_exists",
    )
    seal = {
        "schema_version": 1,
        "kind": "a_share_all_cap_development_seal",
        "campaign_id": contract.campaign_id,
        "artifact_sha256": artifact["artifact_sha256"],
        "artifact_path": Path(str(artifact["artifact_path"])).relative_to(
            root
        ).as_posix(),
        "immutable": True,
    }
    seal["seal_sha256"] = canonical_hash(seal)
    _write_json_exclusive(
        sealed_path,
        seal,
        "all_cap_development:already_sealed",
    )
    return artifact


def _development_unsigned(development: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(development)
    unsigned.pop("artifact_sha256", None)
    unsigned.pop("artifact_path", None)
    return unsigned


def _development_manifest_paths(
    development: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Path]:
    raw = development.get("manifests")
    if not isinstance(raw, Mapping) or set(raw) != set(_MANIFEST_NAMES):
        raise ValueError("all_cap_holdout:manifest_binding")
    paths: dict[str, Path] = {}
    for name in _MANIFEST_NAMES:
        record = raw.get(name)
        if not isinstance(record, Mapping):
            raise ValueError("all_cap_holdout:manifest_binding")
        relative = record.get("path")
        if not isinstance(relative, str):
            raise ValueError("all_cap_holdout:manifest_binding")
        paths[name] = root / relative
    return paths


def _validate_development(
    development: Mapping[str, Any],
    *,
    contract: AllCapContract,
    root: Path,
    expected_sha256: str | None,
) -> dict[str, dict[str, str]]:
    recorded = development.get("artifact_sha256")
    if (
        not isinstance(recorded, str)
        or recorded != canonical_hash(_development_unsigned(development))
        or (expected_sha256 is not None and recorded != expected_sha256)
    ):
        raise ValueError("all_cap_holdout:development_checksum")
    if development.get("contract_sha256") != contract_sha256(contract):
        raise ValueError("all_cap_holdout:contract_hash")
    try:
        verified = _verified_manifests(
            _development_manifest_paths(development, root=root),
            contract=contract,
            root=root,
        )
    except ValueError as exc:
        raise ValueError("all_cap_holdout:manifest_binding") from exc
    if verified != development.get("manifests"):
        raise ValueError("all_cap_holdout:manifest_binding")
    gate = development.get("gate")
    if (
        development.get("kind") != "a_share_all_cap_development"
        or development.get("status") != "pass"
        or not isinstance(gate, Mapping)
        or gate.get("passed") is not True
    ):
        raise ValueError("all_cap_holdout:development_gate")
    return verified


def open_holdout(
    development: Mapping[str, Any],
    contract: AllCapContract,
    repo_root: str | Path,
    *,
    expected_development_sha256: str | None = None,
) -> dict[str, Any]:
    """Authorize the holdout exactly once, before any holdout return is read."""

    root = _store_root(repo_root)
    holdout_dir = _safe_output_dir(root, "holdout")
    marker = holdout_dir / "opened.json"
    if marker.exists() or marker.is_symlink():
        raise ValueError("all_cap_holdout:already_opened")
    manifests = _validate_development(
        development,
        contract=contract,
        root=root,
        expected_sha256=expected_development_sha256,
    )
    payload = {
        "schema_version": 1,
        "kind": "a_share_all_cap_holdout_opened",
        "campaign_id": contract.campaign_id,
        "contract_sha256": contract_sha256(contract),
        "development_artifact_sha256": development["artifact_sha256"],
        "manifests": manifests,
        "holdout_start": contract.holdout_start.isoformat(),
        "holdout_end": contract.holdout_end.isoformat(),
        "immutable": True,
    }
    payload["marker_sha256"] = canonical_hash(payload)
    _write_json_exclusive(
        marker,
        payload,
        "all_cap_holdout:already_opened",
    )
    return payload


def run_holdout(
    *,
    repo_root: str | Path,
    contract: AllCapContract,
    development: Mapping[str, Any],
    load_evaluation: Callable[[], Mapping[str, Any]],
    expected_development_sha256: str | None = None,
) -> dict[str, Any]:
    """Open once, then and only then read and seal holdout evidence."""

    root = _store_root(repo_root)
    marker = open_holdout(
        development,
        contract,
        repo_root,
        expected_development_sha256=expected_development_sha256,
    )
    evaluation = _validate_evaluation(
        load_evaluation(),
        start=contract.holdout_start,
        end=contract.holdout_end,
        scope="holdout",
    )
    payload = _artifact_payload(
        kind="a_share_all_cap_holdout",
        contract=contract,
        manifests=marker["manifests"],
        evaluation=evaluation,
        development_sha256=str(development["artifact_sha256"]),
    )
    return _persist_artifact(
        root=root,
        directory="holdout/results",
        payload=payload,
        error="all_cap_holdout:result_exists",
    )


def _input_payload(path: Path, *, root: Path) -> dict[str, Any]:
    return _read_json(_safe_existing_path(path, root=root))


def _manifest_paths_from_input(
    payload: Mapping[str, Any],
) -> dict[str, Path]:
    raw = payload.get("manifests")
    if not isinstance(raw, Mapping) or set(raw) != set(_MANIFEST_NAMES):
        raise ValueError("all_cap_artifact:manifest_binding")
    if any(not isinstance(raw[name], str) for name in _MANIFEST_NAMES):
        raise ValueError("all_cap_artifact:manifest_binding")
    return {name: Path(raw[name]) for name in _MANIFEST_NAMES}


def run_development_command(
    *,
    repo_root: Path,
    contract_path: Path,
    evaluation_input_path: Path,
) -> dict[str, Any]:
    root = _store_root(repo_root)
    payload = _input_payload(evaluation_input_path, root=root)
    contract_file = contract_path
    if not contract_file.is_absolute():
        contract_file = Path(repo_root) / contract_file
    contract = load_all_cap_contract(contract_file)
    return run_development(
        repo_root=repo_root,
        contract=contract,
        manifest_paths=_manifest_paths_from_input(payload),
        load_evaluation=lambda: payload.get("evaluation"),
    )


def run_holdout_command(
    *,
    repo_root: Path,
    contract_path: Path,
    development_artifact_path: Path,
    development_sha256: str,
    evaluation_input_path: Path,
) -> dict[str, Any]:
    root = _store_root(repo_root)
    contract_file = contract_path
    if not contract_file.is_absolute():
        contract_file = Path(repo_root) / contract_file
    contract = load_all_cap_contract(contract_file)
    development = _read_json(
        _safe_existing_path(development_artifact_path, root=root)
    )

    def load_evaluation() -> Mapping[str, Any]:
        payload = _input_payload(evaluation_input_path, root=root)
        return payload.get("evaluation")

    return run_holdout(
        repo_root=repo_root,
        contract=contract,
        development=development,
        load_evaluation=load_evaluation,
        expected_development_sha256=development_sha256,
    )


__all__ = [
    "canonical_hash",
    "contract_sha256",
    "open_holdout",
    "run_development",
    "run_development_command",
    "run_holdout",
    "run_holdout_command",
]
