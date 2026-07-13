"""Evidence gates for research, shadow, and active model lifecycle states."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..utils import write_text_atomic


@dataclass(frozen=True)
class ActivationEvidence:
    coverage: float
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
    metrics: dict[str, float | int]


_VALID_TRANSITIONS = {("research", "shadow"), ("shadow", "active")}


def evaluate_activation(
    evidence: ActivationEvidence,
    *,
    current_status: str,
    target_status: str,
) -> GateReport:
    if (current_status, target_status) not in _VALID_TRANSITIONS:
        raise ValueError("activation_transition")
    checks = {
        "coverage": evidence.coverage >= 0.70,
        "rank_ic": evidence.rank_ic >= 0.02,
        "icir": evidence.icir >= 0.30,
        "brier_improvement": evidence.brier_improvement >= 0.01,
        "hit_rate_uplift": evidence.hit_rate_uplift >= 0.03,
        "auc": evidence.auc >= 0.54,
        "net_excess_return": evidence.net_excess_return > 0.0,
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
