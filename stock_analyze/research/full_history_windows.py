"""Immutable windows for the full-history model rebuild campaign."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


_ALLOWED_ESTIMATORS = {
    "elastic_net",
    "lightgbm_lambdarank",
    "catboost_ranker",
    "temporal_context_net",
    "additive",
}


@dataclass(frozen=True)
class FoldBoundary:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str


@dataclass(frozen=True)
class ScopeContract:
    market: str
    horizon: int
    max_features: int


@dataclass(frozen=True)
class FullHistoryContract:
    protocol: str
    source_start: str
    development_end: str
    historical_test_start: str
    outer_folds: tuple[FoldBoundary, ...]
    inner_splits: int
    scopes: Mapping[str, ScopeContract]
    minimum_feature_coverage: float
    minimum_feature_stability: float
    maximum_variants_per_family: int
    candidates: Mapping[str, Mapping[str, tuple[Mapping[str, Any], ...]]]


@dataclass(frozen=True)
class PurgedFold:
    train_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]
    train_label_end_dates: tuple[str, ...]
    validation_start: str
    validation_end: str
    embargo_sessions: int
    inner_folds: tuple["PurgedFold", ...] = ()


def _date_key(value: Any) -> str:
    key = str(value).replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"full_history_date:{value}")
    return key


def _load_payload(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        import yaml

        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError("full_history_config_payload")
    return payload


def load_full_history_config(
    source: str | Path | Mapping[str, Any],
) -> FullHistoryContract:
    payload = _load_payload(source)
    source_start = _date_key(payload.get("source_start"))
    development_end = _date_key(payload.get("development_end"))
    historical_test_start = _date_key(payload.get("historical_test_start"))
    if historical_test_start <= development_end:
        raise ValueError("full_history_test_overlap")

    fold_payloads = payload.get("outer_folds") or []
    if len(fold_payloads) < 2:
        raise ValueError("full_history_outer_folds")
    folds: list[FoldBoundary] = []
    prior_validation_end = ""
    for raw in fold_payloads:
        boundary = FoldBoundary(
            train_start=_date_key(raw.get("train_start")),
            train_end=_date_key(raw.get("train_end")),
            validation_start=_date_key(raw.get("validation_start")),
            validation_end=_date_key(raw.get("validation_end")),
        )
        if not (
            source_start <= boundary.train_start <= boundary.train_end
            < boundary.validation_start <= boundary.validation_end
            <= development_end
        ):
            raise ValueError("full_history_fold_boundary")
        if prior_validation_end and boundary.validation_start <= prior_validation_end:
            raise ValueError("full_history_fold_order")
        prior_validation_end = boundary.validation_end
        folds.append(boundary)

    scopes_payload = payload.get("scopes") or {}
    if not scopes_payload:
        raise ValueError("full_history_scopes")
    scopes = {
        str(name): ScopeContract(
            market=str(raw.get("market") or ""),
            horizon=int(raw.get("horizon") or 0),
            max_features=int(raw.get("max_features") or 0),
        )
        for name, raw in scopes_payload.items()
    }
    if any(not item.market or item.horizon <= 0 or item.max_features <= 0 for item in scopes.values()):
        raise ValueError("full_history_scope_contract")

    maximum_variants = int(payload.get("maximum_variants_per_family") or 0)
    if maximum_variants <= 0:
        raise ValueError("full_history_candidate_variants")
    normalized_candidates: dict[str, dict[str, tuple[Mapping[str, Any], ...]]] = {}
    for market, families in (payload.get("candidates") or {}).items():
        normalized_candidates[str(market)] = {}
        for estimator, variants in (families or {}).items():
            if estimator not in _ALLOWED_ESTIMATORS:
                raise ValueError(f"full_history_candidate_estimator:{estimator}")
            declared = tuple(dict(item) for item in (variants or []))
            if not declared or len(declared) > maximum_variants:
                raise ValueError(f"full_history_candidate_variants:{market}:{estimator}")
            normalized_candidates[str(market)][str(estimator)] = declared

    return FullHistoryContract(
        protocol=str(payload.get("protocol") or ""),
        source_start=source_start,
        development_end=development_end,
        historical_test_start=historical_test_start,
        outer_folds=tuple(folds),
        inner_splits=int(payload.get("inner_splits") or 0),
        scopes=scopes,
        minimum_feature_coverage=float(payload.get("minimum_feature_coverage")),
        minimum_feature_stability=float(payload.get("minimum_feature_stability")),
        maximum_variants_per_family=maximum_variants,
        candidates=normalized_candidates,
    )


def _inner_folds(
    train_pairs: Sequence[tuple[str, str]],
    *,
    count: int,
    embargo_sessions: int,
) -> tuple[PurgedFold, ...]:
    dates = sorted({date for date, _ in train_pairs})
    if count <= 0 or not dates:
        return ()
    folds: list[PurgedFold] = []
    for number in range(1, count + 1):
        index = max(1, min(len(dates) - 1, (len(dates) * number) // (count + 1)))
        validation_start = dates[index]
        next_index = max(index + 1, min(len(dates), (len(dates) * (number + 1)) // (count + 1)))
        validation_dates = tuple(dates[index:next_index] or [dates[index]])
        eligible = [(day, end) for day, end in train_pairs if day < validation_start and end < validation_start]
        folds.append(
            PurgedFold(
                train_dates=tuple(day for day, _ in eligible),
                validation_dates=validation_dates,
                train_label_end_dates=tuple(end for _, end in eligible),
                validation_start=validation_start,
                validation_end=validation_dates[-1],
                embargo_sessions=embargo_sessions,
            )
        )
    return tuple(folds)


def build_full_history_windows(
    trade_dates: Sequence[Any],
    label_end_dates: Sequence[Any],
    *,
    contract: FullHistoryContract,
    scope: str,
) -> tuple[PurgedFold, ...]:
    if len(trade_dates) != len(label_end_dates):
        raise ValueError("full_history_window_length")
    scope_contract = contract.scopes.get(str(scope))
    if scope_contract is None:
        raise ValueError(f"full_history_scope_unknown:{scope}")
    pairs = sorted((_date_key(day), _date_key(end)) for day, end in zip(trade_dates, label_end_dates))
    result: list[PurgedFold] = []
    for boundary in contract.outer_folds:
        train_pairs = [
            pair for pair in pairs
            if boundary.train_start <= pair[0] <= boundary.train_end
            and pair[1] < boundary.validation_start
        ]
        validation_dates = tuple(sorted({
            day for day, _ in pairs
            if boundary.validation_start <= day <= boundary.validation_end
        }))
        result.append(
            PurgedFold(
                train_dates=tuple(day for day, _ in train_pairs),
                validation_dates=validation_dates,
                train_label_end_dates=tuple(end for _, end in train_pairs),
                validation_start=boundary.validation_start,
                validation_end=boundary.validation_end,
                embargo_sessions=scope_contract.horizon,
                inner_folds=_inner_folds(
                    train_pairs,
                    count=contract.inner_splits,
                    embargo_sessions=scope_contract.horizon,
                ),
            )
        )
    return tuple(result)


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def seal_full_history_manifest(path: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    destination = Path(path)
    normalized = dict(payload)
    declaration_id = hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()[:16]
    sealed = {
        "schema_version": 1,
        "declaration_id": declaration_id,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "historical_test_open_count": 0,
        "payload": normalized,
    }
    if destination.exists():
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing.get("declaration_id") != declaration_id:
            raise ValueError("full_history_manifest_conflict")
        return existing
    _write_json_atomic(destination, sealed)
    return sealed


def open_historical_test_once(path: str | Path, declaration_id: str) -> dict[str, Any]:
    destination = Path(path)
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    if manifest.get("declaration_id") != str(declaration_id):
        raise ValueError("full_history_manifest_declaration")
    count = int(manifest.get("historical_test_open_count") or 0)
    if count == 0:
        manifest["historical_test_open_count"] = 1
        manifest["historical_test_opened_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(destination, manifest)
    elif count != 1:
        raise ValueError("full_history_manifest_open_count")
    return manifest


__all__ = [
    "FoldBoundary",
    "FullHistoryContract",
    "PurgedFold",
    "ScopeContract",
    "build_full_history_windows",
    "load_full_history_config",
    "open_historical_test_once",
    "seal_full_history_manifest",
]
