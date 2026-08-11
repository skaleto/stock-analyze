"""Sealed account-level evaluation windows for bounded model tournaments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..utils import write_text_atomic


@dataclass(frozen=True)
class EvaluationFold:
    train_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]
    train_label_end: str
    validation_start: str


@dataclass(frozen=True)
class AccountEvaluationWindows:
    account_scope: str
    horizon: int
    embargo_days: int
    folds: tuple[EvaluationFold, ...]


def build_account_windows(
    frame: pd.DataFrame,
    *,
    account_scope: str,
    horizon: int,
    n_splits: int = 4,
    embargo_days: int | None = None,
) -> AccountEvaluationWindows:
    required = {"trade_date", "label_end_date"}
    if required.difference(frame.columns):
        raise ValueError("evaluation_window_missing_dates")
    scope_column = (
        "research_scope"
        if "research_scope" in frame.columns
        else "account_id" if "account_id" in frame.columns else ""
    )
    if not scope_column:
        raise ValueError("evaluation_window_scope_missing")
    normalized_scope = str(account_scope).strip()
    observed = {
        str(value).strip()
        for value in frame[scope_column].dropna().astype(str).unique()
        if str(value).strip()
    }
    if observed != {normalized_scope}:
        raise ValueError("evaluation_window_scope_mismatch")
    normalized = frame.copy()
    normalized["trade_date"] = normalized["trade_date"].astype(str)
    normalized["label_end_date"] = normalized["label_end_date"].astype(str)
    dates = np.asarray(sorted(normalized["trade_date"].unique()))
    split_count = max(1, int(n_splits))
    validation_size = max(1, len(dates) // (split_count + 2))
    first_validation = len(dates) - validation_size * split_count
    embargo = max(0, int(horizon if embargo_days is None else embargo_days))
    folds: list[EvaluationFold] = []
    for split_number in range(split_count):
        start = first_validation + split_number * validation_size
        stop = len(dates) if split_number == split_count - 1 else start + validation_size
        validation_dates = tuple(str(value) for value in dates[start:stop])
        candidate_train_dates = tuple(
            str(value) for value in dates[: max(0, start - embargo)]
        )
        if not candidate_train_dates or not validation_dates:
            continue
        validation_start = validation_dates[0]
        eligible = normalized.loc[
            normalized["trade_date"].isin(candidate_train_dates)
            & normalized["label_end_date"].lt(validation_start)
        ]
        train_dates = tuple(sorted(eligible["trade_date"].unique()))
        if not train_dates:
            continue
        folds.append(EvaluationFold(
            train_dates=train_dates,
            validation_dates=validation_dates,
            train_label_end=str(eligible["label_end_date"].max()),
            validation_start=validation_start,
        ))
    if len(folds) != split_count:
        raise ValueError("evaluation_window_insufficient_history")
    return AccountEvaluationWindows(
        account_scope=normalized_scope,
        horizon=int(horizon),
        embargo_days=embargo,
        folds=tuple(folds),
    )


def _manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(dict(payload), sort_keys=True))
    required = {"market", "account_scope", "horizon", "spec_hashes", "data_fingerprint"}
    if required.difference(normalized):
        raise ValueError("evaluation_manifest_missing_fields")
    return normalized


def seal_evaluation_manifest(
    path: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    destination = Path(path)
    normalized = _manifest_payload(payload)
    declaration_id = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    try:
        existing = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = None
    if isinstance(existing, dict):
        existing_payload = existing.get("payload")
        if existing_payload != normalized or existing.get("declaration_id") != declaration_id:
            raise ValueError("sealed_manifest_mismatch")
        return existing
    state = {
        "schema_version": 1,
        "declaration_id": declaration_id,
        "payload": normalized,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "final_gate_open_count": 0,
        "final_gate_opened_at": None,
    }
    write_text_atomic(
        destination,
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return state


def open_final_gate(path: str | Path, declaration_id: str) -> dict[str, Any]:
    destination = Path(path)
    try:
        state = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("sealed_manifest_missing") from exc
    if str(state.get("declaration_id") or "") != str(declaration_id):
        raise ValueError("sealed_manifest_declaration_mismatch")
    if int(state.get("final_gate_open_count") or 0) >= 1:
        raise ValueError("final_gate_already_opened")
    state["final_gate_open_count"] = 1
    state["final_gate_opened_at"] = datetime.now(timezone.utc).isoformat()
    write_text_atomic(
        destination,
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return state


__all__ = [
    "AccountEvaluationWindows",
    "EvaluationFold",
    "build_account_windows",
    "open_final_gate",
    "seal_evaluation_manifest",
]
