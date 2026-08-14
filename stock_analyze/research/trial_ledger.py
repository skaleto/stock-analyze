"""Immutable predeclaration ledger for classical model comparisons."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..utils import now_iso, write_text_atomic


CAMPAIGN_TRANSPARENT_BUDGET = 24
CAMPAIGN_INCREMENTAL_BUDGET = 8


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
        max_specs: int | None = None,
    ) -> dict[str, Any]:
        normalized = _normalized_specs(specs)
        if max_specs is not None and len(normalized) > int(max_specs):
            raise ValueError(f"trial_ledger_spec_budget:{int(max_specs)}")
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


@dataclass(frozen=True)
class CampaignLedger:
    """Immutable campaign declaration plus append-only bounded trial records."""

    root: Path

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def trials_path(self) -> Path:
        return self.root / "trials.jsonl"

    @staticmethod
    def _canonical(value: Any) -> Any:
        return json.loads(
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        )

    @staticmethod
    def _manifest_hash(payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _read_manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _read_trials(self) -> list[dict[str, Any]]:
        try:
            lines = self.trials_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("campaign_trial_corrupt")
            rows.append(value)
        return rows

    def read_trials(self) -> list[dict[str, Any]]:
        return self._read_trials()

    @staticmethod
    def _validate_declared_specs(payload: Mapping[str, Any]) -> None:
        transparent = list(payload.get("transparent_specs") or [])
        incremental = list(payload.get("incremental_specs") or [])
        if len(transparent) > CAMPAIGN_TRANSPARENT_BUDGET:
            raise ValueError("campaign_transparent_budget_exceeded")
        if len(incremental) > CAMPAIGN_INCREMENTAL_BUDGET:
            raise ValueError("campaign_incremental_budget_exceeded")
        all_specs = [*transparent, *incremental]
        hashes = [str(item.get("spec_hash") or "") for item in all_specs]
        identities = [
            (
                str(item.get("market") or ""),
                str(item.get("account_scope") or ""),
                str(item.get("spec_id") or ""),
            )
            for item in all_specs
        ]
        if not all(hashes) or len(hashes) != len(set(hashes)):
            raise ValueError("campaign_duplicate_or_missing_spec_hash")
        if not all(identity[2] for identity in identities) or len(identities) != len(set(identities)):
            raise ValueError("campaign_duplicate_or_missing_spec_id")

    def declare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._canonical(dict(payload))
        self._validate_declared_specs(normalized)
        manifest_hash = self._manifest_hash(normalized)
        current = self._read_manifest()
        if current:
            existing_payload = dict(current)
            for key in ("schema_version", "manifest_hash", "declared_at", "declaration_count"):
                existing_payload.pop(key, None)
            if (
                self._canonical(existing_payload) != normalized
                or str(current.get("manifest_hash") or "") != manifest_hash
            ):
                raise ValueError("campaign_manifest_mismatch")
            return current
        manifest = {
            **normalized,
            "schema_version": 1,
            "manifest_hash": manifest_hash,
            "declared_at": now_iso(),
            "declaration_count": 1,
        }
        write_text_atomic(
            self.manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        )
        return manifest

    def record_trial(
        self,
        *,
        manifest_hash: str,
        stage: str,
        trial: Mapping[str, Any],
    ) -> dict[str, Any]:
        manifest = self._read_manifest()
        if not manifest or str(manifest.get("manifest_hash") or "") != str(manifest_hash):
            raise ValueError("campaign_manifest_missing")
        normalized_stage = str(stage).strip().replace("-", "_")
        if normalized_stage not in {"transparent", "incremental_ml"}:
            raise ValueError(f"campaign_stage_invalid:{normalized_stage}")
        normalized_trial = self._canonical(dict(trial))
        trial_id = str(normalized_trial.get("trial_id") or "")
        if not trial_id:
            raise ValueError("campaign_trial_id_missing")
        rows = self._read_trials()
        for row in rows:
            if str(row.get("trial_id") or "") == trial_id:
                existing = dict(row)
                existing.pop("recorded_at", None)
                existing.pop("manifest_hash", None)
                existing.pop("stage", None)
                if existing != normalized_trial:
                    raise ValueError("campaign_trial_mismatch")
                return {**row, "idempotent": True}

        stage_rows = [row for row in rows if str(row.get("stage") or "") == normalized_stage]
        stage_budget = (
            CAMPAIGN_TRANSPARENT_BUDGET
            if normalized_stage == "transparent"
            else CAMPAIGN_INCREMENTAL_BUDGET
        )
        if len(rows) >= CAMPAIGN_TRANSPARENT_BUDGET + CAMPAIGN_INCREMENTAL_BUDGET or len(stage_rows) >= stage_budget:
            raise ValueError("campaign_budget_exhausted")

        manifest_key = "transparent_specs" if normalized_stage == "transparent" else "incremental_specs"
        allowed_hashes = {
            str(item.get("spec_hash") or "")
            for item in manifest.get(manifest_key) or []
        }
        if str(normalized_trial.get("spec_hash") or "") not in allowed_hashes:
            raise ValueError("campaign_unknown_spec")
        row = {
            **normalized_trial,
            "manifest_hash": str(manifest_hash),
            "stage": normalized_stage,
            "recorded_at": now_iso(),
        }
        serialized = "\n".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in [*rows, row]
        )
        write_text_atomic(self.trials_path, f"{serialized}\n")
        return {**row, "idempotent": False}


__all__ = [
    "CAMPAIGN_INCREMENTAL_BUDGET",
    "CAMPAIGN_TRANSPARENT_BUDGET",
    "CampaignLedger",
    "DEFAULT_CLASSICAL_TRIAL_SPECS",
    "TrialLedger",
]
