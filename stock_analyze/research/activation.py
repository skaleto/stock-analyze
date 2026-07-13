"""Evidence gates for research, shadow, and active model lifecycle states."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from ..utils import write_text_atomic


@dataclass(frozen=True)
class ActivationEvidence:
    coverage: float
    point_in_time_audit: bool
    oos_predictions: int
    rank_ic: float
    icir: float
    brier_improvement: float
    hit_rate_uplift: float
    auc: float
    net_excess_return: float
    max_drawdown: float
    annual_turnover: float
    ablation_stability: float
    shadow_cycles: int


@dataclass(frozen=True)
class GateReport:
    current_status: str
    target_status: str
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float | int | bool]


_VALID_TRANSITIONS = {("research", "shadow"), ("shadow", "active")}


def select_registry_model(registry: dict, *, available_versions: set[str] | None = None) -> tuple[str, dict] | None:
    """Select champion first, then the most advanced and newest candidate."""

    models = registry.get("models") or {}
    allowed = available_versions if available_versions is not None else set(models)
    champion = str(registry.get("champion_model_version") or "")
    if champion in models and champion in allowed:
        return champion, models[champion]
    for status in ("active", "shadow", "research"):
        candidates = [
            (version, metadata)
            for version, metadata in models.items()
            if version in allowed and metadata.get("status", "research") == status
        ]
        if not candidates:
            continue
        registered = [item for item in candidates if item[1].get("registered_at")]
        return max(registered, key=lambda item: str(item[1]["registered_at"])) if registered else candidates[-1]
    fallback = [(version, metadata) for version, metadata in models.items() if version in allowed]
    return fallback[-1] if fallback else None


def evaluate_activation(
    evidence: ActivationEvidence,
    *,
    current_status: str,
    target_status: str,
) -> GateReport:
    if (current_status, target_status) not in _VALID_TRANSITIONS:
        raise ValueError("activation_transition")
    checks = {
        "coverage": evidence.coverage >= 0.95,
        "point_in_time_audit": evidence.point_in_time_audit,
        "oos_predictions": evidence.oos_predictions >= 200,
        "rank_ic": evidence.rank_ic > 0.02,
        "icir": evidence.icir >= 0.30,
        "brier_improvement": evidence.brier_improvement >= 0.05,
        "hit_rate_uplift": evidence.hit_rate_uplift >= 0.05,
        "auc": evidence.auc >= 0.54,
        "net_excess_return": evidence.net_excess_return >= 0.02,
        "max_drawdown": evidence.max_drawdown <= 0.20,
        "annual_turnover": evidence.annual_turnover <= 8.0,
        "ablation_stability": evidence.ablation_stability >= 0.70,
    }
    if target_status == "active":
        checks["shadow_cycles"] = evidence.shadow_cycles >= 4
    reasons = tuple(name for name, passed in checks.items() if not passed)
    return GateReport(
        current_status=current_status,
        target_status=target_status,
        passed=not reasons,
        reasons=reasons,
        metrics=asdict(evidence),
    )


def activation_evidence_from_metrics(metrics: dict, *, shadow_cycles: int = 0) -> ActivationEvidence:
    """Build a fail-closed gate input from auditable model metadata."""

    def number(name: str, default: float = 0.0) -> float:
        try:
            value = float(metrics.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    return ActivationEvidence(
        coverage=number("feature_coverage"),
        point_in_time_audit=metrics.get("point_in_time_audit") is True,
        oos_predictions=int(number("oos_predictions")),
        rank_ic=number("rank_ic"),
        icir=number("icir"),
        brier_improvement=number("brier_improvement"),
        hit_rate_uplift=number("hit_rate_uplift"),
        auc=number("auc"),
        net_excess_return=number("net_excess_return"),
        max_drawdown=number("max_drawdown", 1.0),
        annual_turnover=number("annual_turnover", 1_000_000_000.0),
        ablation_stability=number("ablation_stability"),
        shadow_cycles=int(shadow_cycles),
    )


def transition_status(current_status: str, target_status: str, report: GateReport) -> str:
    if report.current_status != current_status or report.target_status != target_status:
        raise ValueError("activation_report_mismatch")
    return target_status if report.passed else current_status


class ModelRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"champion_model_version": None, "models": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, state: dict) -> None:
        write_text_atomic(self.path, json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def initialize_champion(self, model_version: str) -> dict:
        state = self._read()
        state["champion_model_version"] = model_version
        state.setdefault("models", {}).setdefault(model_version, {"status": "active", "gate_history": []})
        self._write(state)
        return state

    def record_gate(self, model_version: str, report: GateReport) -> dict:
        state = self._read()
        model = state.setdefault("models", {}).setdefault(
            model_version,
            {"status": report.current_status, "gate_history": []},
        )
        model["status"] = transition_status(report.current_status, report.target_status, report)
        model.setdefault("gate_history", []).append(
            {
                "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "current_status": report.current_status,
                "target_status": report.target_status,
                "passed": report.passed,
                "reasons": list(report.reasons),
                "metrics": report.metrics,
            }
        )
        if report.passed and report.target_status == "active":
            state["champion_model_version"] = model_version
        self._write(state)
        return state


class ShadowCycleTracker:
    def __init__(self, path: str | Path, required_cycles: int = 4) -> None:
        self.path = Path(path)
        self.required_cycles = required_cycles

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "models": {}}

    def record(self, model_version: str, as_of: str, metrics: dict) -> dict:
        state = self._read()
        model = state.setdefault("models", {}).setdefault(model_version, {"cycles": []})
        day = date.fromisoformat(str(as_of)[:10])
        iso_year, iso_week, _ = day.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        cycles = model.setdefault("cycles", [])
        existing = next((cycle for cycle in cycles if cycle.get("week") == week), None)
        row = {"week": week, "as_of": day.isoformat(), "metrics": metrics}
        is_new_cycle = existing is None
        if is_new_cycle:
            cycles.append(row)
        else:
            existing.update(row)
        cycles.sort(key=lambda cycle: str(cycle.get("week") or ""))
        write_text_atomic(self.path, json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        count = len(cycles)
        return {
            "count": count,
            "remaining": max(0, self.required_cycles - count),
            "cycles": cycles,
            "is_new_cycle": is_new_cycle,
        }
