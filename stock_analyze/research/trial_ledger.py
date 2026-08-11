"""Immutable predeclaration ledger for classical model comparisons."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..utils import now_iso, write_text_atomic


DEFAULT_CLASSICAL_TRIAL_SPECS: tuple[dict[str, str], ...] = (
    {"spec_id": "ridge_ranker", "score_source": "linear_ranking_score", "family": "linear"},
    {"spec_id": "hgbr_ranker", "score_source": "boosting_ranking_score", "family": "tree"},
    {"spec_id": "ridge_hgbr_ensemble", "score_source": "score", "family": "ensemble"},
    {"spec_id": "momentum_20", "score_source": "momentum_20", "family": "classic_baseline"},
    {"spec_id": "low_volatility_20", "score_source": "negative_realized_volatility_20", "family": "classic_baseline"},
)


def _normalized_specs(specs: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows = [
        {str(key): str(value) for key, value in sorted(dict(spec).items())}
        for spec in specs
    ]
    if len(rows) != len({row.get("spec_id") for row in rows}):
        raise ValueError("trial_ledger_duplicate_spec")
    return sorted(rows, key=lambda row: row.get("spec_id", ""))


@dataclass(frozen=True)
class TrialLedger:
    path: Path

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> dict[str, Any]:
        write_text_atomic(
            self.path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return value

    def declare(
        self,
        *,
        family_id: str,
        specs: Iterable[Mapping[str, Any]],
        objective: str,
    ) -> dict[str, Any]:
        normalized = _normalized_specs(specs)
        payload = {
            "family_id": str(family_id),
            "objective": str(objective),
            "specs": normalized,
        }
        declaration_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        state = self._read()
        if state:
            existing = {
                "family_id": str(state.get("family_id") or ""),
                "objective": str(state.get("objective") or ""),
                "specs": _normalized_specs(state.get("specs") or []),
            }
            if existing != payload or str(state.get("declaration_id") or "") != declaration_id:
                raise ValueError("trial_ledger_declaration_mismatch")
            return state
        return self._write({
            **payload,
            "schema_version": 1,
            "declaration_id": declaration_id,
            "declared_at": now_iso(),
            "declaration_count": 1,
            "runs": [],
        })

    def finalize(
        self,
        *,
        run_id: str,
        declaration_id: str,
        results: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        state = self._read()
        if not state or str(state.get("declaration_id") or "") != str(declaration_id):
            raise ValueError("trial_ledger_declaration_missing")
        rows = list(state.get("runs") or [])
        if any(str(row.get("run_id") or "") == str(run_id) for row in rows):
            return state
        allowed = {str(spec.get("spec_id") or "") for spec in state.get("specs") or []}
        normalized_results = [dict(result) for result in results]
        if any(str(result.get("spec_id") or "") not in allowed for result in normalized_results):
            raise ValueError("trial_ledger_unknown_spec")
        rows.append({
            "run_id": str(run_id),
            "recorded_at": now_iso(),
            "results": normalized_results,
        })
        state["runs"] = rows
        return self._write(state)


__all__ = ["DEFAULT_CLASSICAL_TRIAL_SPECS", "TrialLedger"]
