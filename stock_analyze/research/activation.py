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
    deflated_sharpe_probability: float = 0.0
    probability_of_backtest_overfit: float = 1.0
    pbo_trial_count: int = 0
    seed_rank_ic_std: float = 1_000_000_000.0
    subperiod_stability: float = 0.0
    feature_selection_stability: float = 0.0
    unbiased_universe: bool = False
    effective_dates: int = 0
    effective_non_overlapping_periods: int = 0
    simulator_version: str = "unverified"
    all_accounts_positive_active: bool = False
    valid_trial_count: int = 0
    trial_evidence_status: str = "insufficient_evidence"
    execution_evidence_status: str = "unavailable"
    missing_liquidity_notional_ratio: float = 1.0
    impact_capped_notional_ratio: float = 1.0
    edge_calibration_available: bool = True
    attribution_status: str = "reconciled"
    trade_count: int = 1
    capital_utilization: float = 0.0
    diagnostic_net_excess_return: float = 0.0
    diagnostic_max_drawdown: float = 1.0
    diagnostic_annual_turnover: float = 1_000_000_000.0
    diagnostic_trade_count: int = 0
    diagnostic_capital_utilization: float = 0.0
    diagnostic_all_accounts_positive_active: bool = False
    diagnostic_simulator_version: str = "unverified"
    diagnostic_execution_evidence_status: str = "unavailable"
    diagnostic_missing_liquidity_notional_ratio: float = 1.0
    diagnostic_impact_capped_notional_ratio: float = 1.0
    diagnostic_attribution_status: str = "unavailable"
    forward_evidence_status: str = "insufficient_evidence"
    forward_cycles: int = 0
    forward_net_excess_return: float = 0.0
    forward_max_drawdown: float = 1.0
    forward_all_accounts_positive_active: bool = False
    lookthrough_required: bool = False
    lookthrough_evidence_status: str = "not_required"
    underlying_profile_coverage: float = 1.0
    underlying_company_weight_coverage: float = 1.0


@dataclass(frozen=True)
class GateReport:
    current_status: str
    target_status: str
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, object]


_VALID_TRANSITIONS = {("research", "shadow"), ("shadow", "active")}
_MODEL_ROLES = ("classifier", "ranker", "portfolio")
_TERMINAL_MODEL_STATUSES = {"rejected", "superseded", "quarantined", "retired"}


def select_registry_model(
    registry: dict,
    *,
    available_versions: set[str] | None = None,
    role: str | None = None,
) -> tuple[str, dict] | None:
    """Select champion first, then the most advanced and newest candidate."""

    models = registry.get("models") or {}
    allowed = available_versions if available_versions is not None else set(models)
    champion = str(
        ((registry.get("champion_model_versions") or {}).get(role) if role else None)
        or registry.get("champion_model_version")
        or ""
    )
    if champion in models and champion in allowed:
        champion_status = (
            (models[champion].get("role_status") or {}).get(
                role, models[champion].get("status", "research")
            )
            if role
            else models[champion].get("status", "research")
        )
        if champion_status == "active":
            return champion, models[champion]
    for status in ("active", "shadow", "research"):
        candidates = [
            (version, metadata)
            for version, metadata in models.items()
            if version in allowed
            and str(metadata.get("status") or "research") not in _TERMINAL_MODEL_STATUSES
            and (
                (metadata.get("role_status") or {}).get(role, metadata.get("status", "research"))
                if role
                else metadata.get("status", "research")
            ) == status
        ]
        if not candidates:
            continue
        registered = [item for item in candidates if item[1].get("registered_at")]
        return max(registered, key=lambda item: str(item[1]["registered_at"])) if registered else candidates[-1]
    return None


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
        "effective_dates": evidence.effective_dates >= 60,
        "effective_non_overlapping_periods": evidence.effective_non_overlapping_periods >= 20,
        "rank_ic": evidence.rank_ic > 0.02,
        "icir": evidence.icir >= 0.30,
        "brier_improvement": evidence.brier_improvement >= 0.05,
        "hit_rate_uplift": evidence.hit_rate_uplift >= 0.05,
        "auc": evidence.auc >= 0.54,
        "net_excess_return": evidence.net_excess_return >= 0.02,
        "max_drawdown": evidence.max_drawdown <= 0.20,
        "annual_turnover": evidence.annual_turnover <= 8.0,
        "capital_utilization": evidence.capital_utilization >= 0.85,
        "ablation_stability": evidence.ablation_stability >= 0.70,
        "simulator_version": evidence.simulator_version == "paper-parity-daily-v1",
        "all_accounts_positive_active": evidence.all_accounts_positive_active,
        "valid_trial_count": evidence.valid_trial_count >= 4,
        "trial_evidence_status": evidence.trial_evidence_status == "available",
        "execution_evidence_status": evidence.execution_evidence_status in {
            "available", "not_applicable"
        },
        "missing_liquidity_notional_ratio": (
            evidence.missing_liquidity_notional_ratio <= 0.05
        ),
        "impact_capped_notional_ratio": (
            evidence.impact_capped_notional_ratio <= 0.10
        ),
    }
    if target_status == "active":
        checks["shadow_cycles"] = evidence.shadow_cycles >= 12
        checks["forward_evidence_status"] = evidence.forward_evidence_status == "available"
        checks["forward_cycles"] = evidence.forward_cycles >= 12
        checks["forward_net_excess_return"] = evidence.forward_net_excess_return > 0.0
        checks["forward_max_drawdown"] = evidence.forward_max_drawdown <= 0.20
        checks["forward_all_accounts_positive_active"] = evidence.forward_all_accounts_positive_active
        if evidence.lookthrough_required:
            checks["lookthrough_evidence_status"] = (
                evidence.lookthrough_evidence_status == "available"
            )
            checks["underlying_profile_coverage"] = (
                evidence.underlying_profile_coverage >= 0.80
            )
            checks["underlying_company_weight_coverage"] = (
                evidence.underlying_company_weight_coverage >= 0.60
            )
    reasons = tuple(name for name, passed in checks.items() if not passed)
    return GateReport(
        current_status=current_status,
        target_status=target_status,
        passed=not reasons,
        reasons=reasons,
        metrics=asdict(evidence),
    )


def evaluate_role_activation(
    evidence: ActivationEvidence,
    *,
    current_status: str,
    target_status: str,
) -> dict[str, GateReport]:
    """Evaluate independent classifier, ranker, and portfolio contracts.

    Model artifacts contain all three roles, but their evidence and consumers
    differ. Keeping the reports separate prevents a weak probability head from
    disabling a useful ranking head (and vice versa).
    """

    if (current_status, target_status) not in _VALID_TRANSITIONS:
        raise ValueError("activation_transition")
    common = {
        "coverage": evidence.coverage >= 0.95,
        "point_in_time_audit": evidence.point_in_time_audit,
        "oos_predictions": evidence.oos_predictions >= 200,
        "effective_dates": evidence.effective_dates >= 60,
        "effective_non_overlapping_periods": evidence.effective_non_overlapping_periods >= 20,
    }
    role_checks = {
        "classifier": {
            **common,
            "brier_improvement": evidence.brier_improvement >= 0.05,
            "hit_rate_uplift": evidence.hit_rate_uplift >= 0.05,
            "auc": evidence.auc >= 0.54,
        },
        "ranker": {
            **common,
            "rank_ic": evidence.rank_ic > 0.02,
            "icir": evidence.icir >= 0.30,
            "diagnostic_trade_activity": evidence.diagnostic_trade_count > 0,
            "diagnostic_capital_utilization": (
                evidence.diagnostic_capital_utilization >= 0.85
            ),
            "diagnostic_net_excess_return": (
                evidence.diagnostic_net_excess_return >= 0.02
            ),
            "diagnostic_max_drawdown": evidence.diagnostic_max_drawdown <= 0.20,
            "diagnostic_annual_turnover": (
                evidence.diagnostic_annual_turnover <= 8.0
            ),
            "ablation_stability": evidence.ablation_stability >= 0.70,
            "deflated_sharpe_probability": evidence.deflated_sharpe_probability >= 0.95,
            "probability_of_backtest_overfit": evidence.probability_of_backtest_overfit <= 0.50,
            "pbo_trial_count": evidence.pbo_trial_count >= 4,
            "seed_rank_ic_std": evidence.seed_rank_ic_std <= 0.03,
            "subperiod_stability": evidence.subperiod_stability >= 0.60,
            "feature_selection_stability": evidence.feature_selection_stability >= 0.60,
            "unbiased_universe": evidence.unbiased_universe,
            "simulator_version": (
                evidence.diagnostic_simulator_version == "paper-parity-daily-v1"
            ),
            "all_accounts_positive_active": (
                evidence.diagnostic_all_accounts_positive_active
            ),
            "valid_trial_count": evidence.valid_trial_count >= 4,
            "trial_evidence_status": evidence.trial_evidence_status == "available",
            "execution_evidence_status": (
                evidence.diagnostic_execution_evidence_status
                in {"available", "not_applicable"}
            ),
            "missing_liquidity_notional_ratio": (
                evidence.diagnostic_missing_liquidity_notional_ratio <= 0.05
            ),
            "impact_capped_notional_ratio": (
                evidence.diagnostic_impact_capped_notional_ratio <= 0.10
            ),
            "attribution_status": (
                evidence.diagnostic_attribution_status == "reconciled"
            ),
        },
        "portfolio": {
            **common,
            "trade_activity": evidence.trade_count > 0,
            "capital_utilization": evidence.capital_utilization >= 0.85,
            "net_excess_return": evidence.net_excess_return >= 0.02,
            "max_drawdown": evidence.max_drawdown <= 0.20,
            "annual_turnover": evidence.annual_turnover <= 8.0,
            "deflated_sharpe_probability": evidence.deflated_sharpe_probability >= 0.95,
            "probability_of_backtest_overfit": evidence.probability_of_backtest_overfit <= 0.50,
            "pbo_trial_count": evidence.pbo_trial_count >= 4,
            "seed_rank_ic_std": evidence.seed_rank_ic_std <= 0.03,
            "subperiod_stability": evidence.subperiod_stability >= 0.60,
            "feature_selection_stability": evidence.feature_selection_stability >= 0.60,
            "unbiased_universe": evidence.unbiased_universe,
            "simulator_version": evidence.simulator_version == "paper-parity-daily-v1",
            "all_accounts_positive_active": evidence.all_accounts_positive_active,
            "valid_trial_count": evidence.valid_trial_count >= 4,
            "trial_evidence_status": evidence.trial_evidence_status == "available",
            "execution_evidence_status": evidence.execution_evidence_status in {
                "available", "not_applicable"
            },
            "missing_liquidity_notional_ratio": (
                evidence.missing_liquidity_notional_ratio <= 0.05
            ),
            "impact_capped_notional_ratio": (
                evidence.impact_capped_notional_ratio <= 0.10
            ),
            "edge_calibration_available": evidence.edge_calibration_available,
            "attribution_status": evidence.attribution_status == "reconciled",
        },
    }
    if target_status == "active":
        for checks in role_checks.values():
            checks["shadow_cycles"] = evidence.shadow_cycles >= 12
            checks["forward_evidence_status"] = evidence.forward_evidence_status == "available"
            checks["forward_cycles"] = evidence.forward_cycles >= 12
        for role in ("ranker", "portfolio"):
            role_checks[role]["forward_net_excess_return"] = evidence.forward_net_excess_return > 0.0
            role_checks[role]["forward_max_drawdown"] = evidence.forward_max_drawdown <= 0.20
        role_checks["portfolio"]["forward_all_accounts_positive_active"] = (
            evidence.forward_all_accounts_positive_active
        )
        if evidence.lookthrough_required:
            for role in ("ranker", "portfolio"):
                role_checks[role]["lookthrough_evidence_status"] = (
                    evidence.lookthrough_evidence_status == "available"
                )
                role_checks[role]["underlying_profile_coverage"] = (
                    evidence.underlying_profile_coverage >= 0.80
                )
                role_checks[role]["underlying_company_weight_coverage"] = (
                    evidence.underlying_company_weight_coverage >= 0.60
                )
    metrics = asdict(evidence)
    return {
        role: GateReport(
            current_status=current_status,
            target_status=target_status,
            passed=all(checks.values()),
            reasons=tuple(name for name, passed in checks.items() if not passed),
            metrics={**metrics, "model_role": role},
        )
        for role, checks in role_checks.items()
    }


def activation_evidence_from_metrics(metrics: dict, *, shadow_cycles: int = 0) -> ActivationEvidence:
    """Build a fail-closed gate input from auditable model metadata."""

    def number(name: str, default: float = 0.0) -> float:
        try:
            value = float(metrics.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    governance = metrics.get("governance") if isinstance(metrics.get("governance"), dict) else {}
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
        deflated_sharpe_probability=number(
            "deflated_sharpe_probability",
            float(governance.get("deflated_sharpe_probability", 0.0)),
        ),
        probability_of_backtest_overfit=number(
            "probability_of_backtest_overfit",
            float(governance.get("probability_of_backtest_overfit", 1.0)),
        ),
        pbo_trial_count=int(number(
            "pbo_trial_count",
            float(governance.get("pbo_trial_count", 0)),
        )),
        seed_rank_ic_std=number("seed_rank_ic_std", 1_000_000_000.0),
        subperiod_stability=number("subperiod_stability"),
        feature_selection_stability=number("feature_selection_stability"),
        unbiased_universe=metrics.get("unbiased_universe") is True,
        effective_dates=int(number("effective_dates")),
        effective_non_overlapping_periods=int(number("effective_non_overlapping_periods")),
        simulator_version=str(metrics.get("simulator_version") or "unverified"),
        all_accounts_positive_active=metrics.get("all_accounts_positive_active") is True,
        valid_trial_count=int(number(
            "valid_trial_count",
            float(governance.get("valid_trial_count", 0)),
        )),
        trial_evidence_status=str(
            metrics.get("trial_evidence_status")
            or governance.get("trial_evidence_status")
            or "insufficient_evidence"
        ),
        execution_evidence_status=str(
            metrics.get("execution_evidence_status") or "unavailable"
        ),
        missing_liquidity_notional_ratio=number(
            "missing_liquidity_notional_ratio", 1.0
        ),
        impact_capped_notional_ratio=number(
            "impact_capped_notional_ratio", 1.0
        ),
        edge_calibration_available=(
            metrics.get("edge_calibration_available", True) is not False
        ),
        attribution_status=str(
            metrics.get("attribution_status") or "reconciled"
        ),
        trade_count=int(number("trade_count")),
        capital_utilization=number("capital_utilization"),
        diagnostic_net_excess_return=number("diagnostic_net_excess_return"),
        diagnostic_max_drawdown=number("diagnostic_max_drawdown", 1.0),
        diagnostic_annual_turnover=number(
            "diagnostic_annual_turnover", 1_000_000_000.0
        ),
        diagnostic_trade_count=int(number("diagnostic_trade_count")),
        diagnostic_capital_utilization=number("diagnostic_capital_utilization"),
        diagnostic_all_accounts_positive_active=(
            metrics.get("diagnostic_all_accounts_positive_active") is True
        ),
        diagnostic_simulator_version=str(
            metrics.get("diagnostic_simulator_version") or "unverified"
        ),
        diagnostic_execution_evidence_status=str(
            metrics.get("diagnostic_execution_evidence_status") or "unavailable"
        ),
        diagnostic_missing_liquidity_notional_ratio=number(
            "diagnostic_missing_liquidity_notional_ratio", 1.0
        ),
        diagnostic_impact_capped_notional_ratio=number(
            "diagnostic_impact_capped_notional_ratio", 1.0
        ),
        diagnostic_attribution_status=str(
            metrics.get("diagnostic_attribution_status") or "unavailable"
        ),
        forward_evidence_status=str(
            metrics.get("forward_evidence_status") or "insufficient_evidence"
        ),
        forward_cycles=int(number("forward_cycles")),
        forward_net_excess_return=number("forward_net_excess_return"),
        forward_max_drawdown=number("forward_max_drawdown", 1.0),
        forward_all_accounts_positive_active=(
            metrics.get("forward_all_accounts_positive_active") is True
        ),
        lookthrough_required=metrics.get("lookthrough_required") is True,
        lookthrough_evidence_status=str(
            metrics.get("lookthrough_evidence_status")
            or (
                "insufficient_evidence"
                if metrics.get("lookthrough_required") is True
                else "not_required"
            )
        ),
        underlying_profile_coverage=number(
            "underlying_profile_coverage",
            0.0 if metrics.get("lookthrough_required") is True else 1.0,
        ),
        underlying_company_weight_coverage=number(
            "underlying_company_weight_coverage",
            0.0 if metrics.get("lookthrough_required") is True else 1.0,
        ),
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

    def initialize_champion(self, model_version: str, *, roles: tuple[str, ...] = _MODEL_ROLES) -> dict:
        state = self._read()
        state["champion_model_version"] = model_version
        champions = state.setdefault("champion_model_versions", {})
        model = state.setdefault("models", {}).setdefault(
            model_version,
            {"status": "active", "gate_history": []},
        )
        role_status = model.setdefault("role_status", {})
        for role in roles:
            if role not in _MODEL_ROLES:
                raise ValueError(f"model_role:{role}")
            champions[role] = model_version
            role_status[role] = "active"
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

    def record_role_gate(self, model_version: str, role: str, report: GateReport) -> dict:
        if role not in _MODEL_ROLES:
            raise ValueError(f"model_role:{role}")
        state = self._read()
        models = state.setdefault("models", {})
        model = models.setdefault(
            model_version,
            {"status": report.current_status, "gate_history": []},
        )
        role_status = model.setdefault("role_status", {})
        current = str(role_status.get(role, report.current_status))
        if current != report.current_status:
            raise ValueError(f"activation_role_status_mismatch:{role}")
        next_status = transition_status(current, report.target_status, report)
        role_status[role] = next_status
        model.setdefault("gate_history", []).append(
            {
                "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "model_role": role,
                "current_status": report.current_status,
                "target_status": report.target_status,
                "passed": report.passed,
                "reasons": list(report.reasons),
                "metrics": report.metrics,
            }
        )
        if report.passed and report.target_status == "active":
            champions = state.setdefault("champion_model_versions", {})
            previous = str(champions.get(role) or "")
            if previous and previous != model_version and previous in models:
                models[previous].setdefault("role_status", {})[role] = "superseded"
                state.setdefault("previous_champion_model_versions", {})[role] = previous
            champions[role] = model_version
            if role == "ranker":
                state["champion_model_version"] = model_version
        statuses = set(role_status.values())
        model["status"] = "active" if "active" in statuses else (
            "shadow" if "shadow" in statuses else report.current_status
        )
        self._write(state)
        return state

    def finalize_research_evaluation(
        self,
        model_version: str,
        *,
        required_roles: tuple[str, ...] = ("ranker", "portfolio"),
    ) -> dict:
        """Close one research evaluation as Shadow or Rejected."""

        state = self._read()
        model = (state.get("models") or {}).get(model_version)
        if model is None:
            raise ValueError("model_version_missing")
        for role in required_roles:
            if role not in _MODEL_ROLES:
                raise ValueError(f"model_role:{role}")
        role_status = model.get("role_status") or {}
        latest_by_role: dict[str, dict] = {}
        for gate in model.get("gate_history") or []:
            role = str(gate.get("model_role") or "")
            if role in required_roles:
                latest_by_role[role] = gate
        rejection_reasons: list[str] = []
        for role in required_roles:
            gate = latest_by_role.get(role)
            if gate is None:
                rejection_reasons.append(f"{role}:gate_missing")
                continue
            if role_status.get(role) != "shadow":
                reasons = gate.get("reasons") or ["gate_failed"]
                rejection_reasons.extend(f"{role}:{reason}" for reason in reasons)
        status = "shadow" if not rejection_reasons else "rejected"
        evaluated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        model["status"] = status
        model["rejection_reasons"] = rejection_reasons
        model["research_evaluation"] = {
            "evaluated_at": evaluated_at,
            "required_roles": list(required_roles),
            "status": status,
            "reasons": rejection_reasons,
        }
        self._write(state)
        return state

    def quarantine_roles(
        self,
        model_version: str,
        *,
        roles: tuple[str, ...],
        reason: str,
        event_id: str,
    ) -> dict:
        state = self._read()
        models = state.setdefault("models", {})
        if model_version not in models:
            raise ValueError("model_version_missing")
        events = state.setdefault("lifecycle_events", [])
        existing = next(
            (event for event in events if str(event.get("event_id") or "") == event_id),
            None,
        )
        if existing is not None:
            return state
        champions = state.setdefault("champion_model_versions", {})
        previous_map = state.setdefault("previous_champion_model_versions", {})
        quarantined = models[model_version].setdefault("role_status", {})
        rollbacks: dict[str, str | None] = {}
        for role in roles:
            if role not in _MODEL_ROLES:
                raise ValueError(f"model_role:{role}")
            quarantined[role] = "quarantined"
            if str(champions.get(role) or "") != model_version:
                rollbacks[role] = None
                continue
            previous = str(previous_map.get(role) or "")
            if previous in models:
                models[previous].setdefault("role_status", {})[role] = "active"
                champions[role] = previous
                rollbacks[role] = previous
            else:
                candidates = [
                    (version, metadata)
                    for version, metadata in models.items()
                    if version != model_version
                    and (metadata.get("role_status") or {}).get(role) == "superseded"
                ]
                if candidates:
                    fallback, metadata = max(
                        candidates,
                        key=lambda item: str(item[1].get("registered_at") or ""),
                    )
                    metadata.setdefault("role_status", {})[role] = "active"
                    champions[role] = fallback
                    rollbacks[role] = fallback
                else:
                    champions.pop(role, None)
                    rollbacks[role] = None
        role_states = set(quarantined.values())
        models[model_version]["status"] = (
            "active" if "active" in role_states else "quarantined"
        )
        ranker_champion = str(champions.get("ranker") or "")
        state["champion_model_version"] = ranker_champion or None
        events.append(
            {
                "event_id": event_id,
                "event_type": "quarantine",
                "model_version": model_version,
                "roles": list(roles),
                "reason": reason,
                "rollbacks": rollbacks,
                "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        self._write(state)
        return state


class ShadowCycleTracker:
    def __init__(self, path: str | Path, required_cycles: int = 12) -> None:
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
